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
