"""Bounded thread discovery uses the native index without changing results."""

import sqlite3

import pytest

import server
from ccc_server import codex


@pytest.fixture
def thread_db(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    monkeypatch.setattr(codex, "_codex_state_db_candidates", lambda: [db])
    monkeypatch.setattr(codex, "_CODEX_THREADS_CACHE", {})
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: set())
    return db


def _schema(con, suffix=""):
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER, "
                "updated_at_ms INTEGER, created_at INTEGER, created_at_ms INTEGER, "
                "archived INTEGER) " + suffix)
    con.execute("CREATE INDEX recent ON threads(updated_at_ms DESC, id DESC)")


def test_projection_discovers_columns_after_schema_change(thread_db):
    with sqlite3.connect(thread_db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER, created_at INTEGER)")
        con.execute("INSERT INTO threads VALUES ('thread', 1, 1)")
    assert codex._codex_fetch_threads_in_home(columns=("id", "model")) == [{"id": "thread"}]
    with sqlite3.connect(thread_db) as con:
        con.execute("ALTER TABLE threads ADD COLUMN model TEXT")
        con.execute("UPDATE threads SET model='test-model'")
    assert codex._codex_fetch_threads_in_home(columns=("id", "model")) == [
        {"id": "thread", "model": "test-model"}]


def test_projection_supports_sqlite_without_table_valued_pragma(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        _schema(con)
        con.execute("INSERT INTO threads VALUES ('thread', 1, 1000, 0, 0, 0)")
    read = codex._sqlite_fetch_rows

    def older_sqlite(con, sql, params, columns):
        if "pragma_table_info" in sql:
            raise sqlite3.OperationalError("no such table: pragma_table_info")
        return read(con, sql, params, columns)

    monkeypatch.setattr(codex, "_sqlite_fetch_rows", older_sqlite)
    assert codex._codex_fetch_threads_in_home(limit=1, columns=("id",)) == [{"id": "thread"}]


def test_exact_id_projection_chunks_inside_one_unsorted_snapshot(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT)")
        con.executemany("INSERT INTO threads VALUES (?, ?)", [
            ("wanted-a", "First"), ("wanted-b", "Second"), ("outside", "Outside"),
        ])
    real_connect = sqlite3.connect
    calls = []
    statements = []

    class LowLimitConnection(sqlite3.Connection):
        def getlimit(self, category):
            assert category == sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER
            return 2

    if not hasattr(sqlite3, "SQLITE_LIMIT_VARIABLE_NUMBER"):
        monkeypatch.setattr(sqlite3, "SQLITE_LIMIT_VARIABLE_NUMBER", 9, raising=False)

    def connect(*args, **kwargs):
        calls.append(args)
        con = real_connect(*args, factory=LowLimitConnection, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(codex.sqlite3, "connect", connect)
    rows = codex._codex_fetch_threads_in_home(
        columns=("id", "title"), exact_ids=("missing", "wanted-a", "wanted-b"),
    )
    assert {row["id"]: row["title"] for row in rows} == {
        "wanted-a": "First", "wanted-b": "Second",
    }
    assert len(calls) == 1
    assert statements.count("BEGIN") == 1
    reads = [sql for sql in statements if "FROM threads WHERE id IN" in sql]
    assert len(reads) == 2
    assert all("ORDER BY" not in sql for sql in reads)


def test_indexed_projection_work_is_bounded_and_preserves_ties(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        _schema(con)
        # Deliberately make insertion order differ from the index's id order.
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", [
            (f"thread-{i:05}", i // 3, i // 3 * 1000, 0, 0, 0)
            for i in range(6000)
        ])
        expected = [r[0] for r in con.execute(
            "SELECT id FROM threads ORDER BY COALESCE(updated_at_ms, "
            "updated_at * 1000, created_at_ms, created_at * 1000) DESC LIMIT 20")]

    real_connect = sqlite3.connect
    steps = []

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.set_progress_handler(lambda: steps.append(1) or 0, 100)
        return con

    monkeypatch.setattr(codex.sqlite3, "connect", connect)
    rows = codex._codex_fetch_threads_in_home(limit=20, columns=("id", "updated_at_ms"))
    assert [r["id"] for r in rows] == expected
    # A full-table sort costs tens of thousands of VM operations. The guard
    # and indexed top-N query must stay bounded independently of 6000 rows.
    assert len(steps) < 30
    assert set(rows[0]) == {"id", "updated_at_ms"}


@pytest.mark.parametrize("without_rowid", [False, True])
def test_null_fallback_and_where_params_remain_correct(thread_db, without_rowid):
    with sqlite3.connect(thread_db) as con:
        _schema(con, "WITHOUT ROWID" if without_rowid else "")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", [
            ("millis", 1, 2000, 0, 0, 0),
            ("seconds", 3, None, 0, 0, 0),
            ("created", None, None, 4, None, 0),
            ("excluded", 100, 100000, 0, 0, 1),
        ])
    rows = codex._codex_fetch_threads_in_home(
        "archived = ?", (0,), limit=3, columns=("id",))
    assert [r["id"] for r in rows] == ["created", "seconds", "millis"]


def test_without_rowid_and_nonnull_timestamps_use_legacy_query(thread_db):
    with sqlite3.connect(thread_db) as con:
        _schema(con, "WITHOUT ROWID")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", [
            ("older", 1, 1000, 0, 0, 0),
            ("newer", 2, 2000, 0, 0, 0),
        ])
    rows = codex._codex_fetch_threads_in_home(limit=1, columns=("id",))
    assert [r["id"] for r in rows] == ["newer"]


def test_sidebar_title_omission_keeps_order_and_revalidates_cache(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        _schema(con)
        con.execute("ALTER TABLE threads ADD COLUMN title TEXT")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("known", 3, 3000, 0, 0, 0, "Long title " * 300),
            ("missing", 2, 2000, 0, 0, 0, "Title for missing row"),
            ("outside", 1, 1000, 0, 0, 0, "Outside the limit"),
        ])
    columns = ("id", "title")
    fetch = codex._codex_fetch_threads_in_home
    assert fetch(limit=2, columns=columns, omit_titles_for={"known"}) == [
        {"id": "known", "title": None},
        {"id": "missing", "title": "Title for missing row"},
    ]
    assert fetch(limit=2, columns=columns)[0]["title"] == "Long title " * 300
    # A different archive snapshot needs the title of the formerly loaded row.
    assert fetch(limit=2, columns=columns, omit_titles_for={"missing"}) == [
        {"id": "known", "title": "Long title " * 300},
        {"id": "missing", "title": None},
    ]
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: {"known"})
    assert fetch(limit=2, columns=columns, omit_titles_for={"missing"}) == [
        {"id": "missing", "title": None},
    ]


def test_sidebar_title_omission_falls_back_without_json_each(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        _schema(con, "WITHOUT ROWID")
        con.execute("ALTER TABLE threads ADD COLUMN title TEXT")
        con.execute("INSERT INTO threads VALUES ('known', 1, 1000, 0, 0, 0, 'Original')")
    read = codex._sqlite_fetch_rows

    def older_sqlite(con, sql, params, columns):
        if "json_each" in sql:
            raise sqlite3.OperationalError("no such table: json_each")
        return read(con, sql, params, columns)

    monkeypatch.setattr(codex, "_sqlite_fetch_rows", older_sqlite)
    assert codex._codex_fetch_threads_in_home(
        limit=1, columns=("id", "title"), omit_titles_for={"known"},
    ) == [{"id": "known", "title": "Original"}]


def test_null_guard_and_top_n_share_a_read_snapshot(thread_db, monkeypatch):
    with sqlite3.connect(thread_db) as con:
        con.execute("PRAGMA journal_mode=WAL")
        _schema(con)
        con.execute("INSERT INTO threads VALUES ('old', 1, 1000, 0, 0, 0)")

    real_connect = sqlite3.connect

    class ConcurrentInsert(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if "SELECT id FROM threads ORDER BY" in sql:
                with real_connect(thread_db) as writer:
                    writer.execute("INSERT INTO threads VALUES ('new', 9, NULL, 0, 0, 0)")
            return super().execute(sql, parameters)

    monkeypatch.setattr(codex.sqlite3, "connect", lambda *a, **kw: real_connect(
        *a, **kw, factory=ConcurrentInsert))
    rows = codex._codex_fetch_threads_in_home(limit=1, columns=("id",))
    assert [r["id"] for r in rows] == ["old"]
    with real_connect(thread_db) as con:
        assert con.execute("SELECT count(*) FROM threads").fetchone()[0] == 2
