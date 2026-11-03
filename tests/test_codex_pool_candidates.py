"""Keep candidate discovery independent of wide thread metadata."""

import sqlite3

import pytest

import server
from ccc_server import codex


@pytest.mark.parametrize("millisecond_schema", [False, True])
def test_pool_projection_preserves_candidates_without_reading_prompts(
    tmp_path, monkeypatch, millisecond_schema,
):
    db = tmp_path / "state.sqlite"
    now = 2_000_000_000
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, created_at INTEGER, "
                    "updated_at INTEGER, first_user_message TEXT, source TEXT)")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?)", [
            ("fresh", now - 1000, now - 5, "large prompt", "exec"),
            ("stale", now - 1000, now - 301, "older prompt", "exec"),
            ("created-only", now - 10, None, "new prompt", "exec"),
            ("deleted", now - 100, now - 1, "deleted prompt", "exec"),
        ])
        if millisecond_schema:
            con.execute("ALTER TABLE threads ADD COLUMN updated_at_ms INTEGER")
            con.execute("ALTER TABLE threads ADD COLUMN created_at_ms INTEGER")
            # Milliseconds take precedence even when the seconds look stale.
            con.execute("UPDATE threads SET updated_at_ms=? WHERE id='stale'",
                        (int((now - 2) * 1000),))

    monkeypatch.setattr(codex, "_codex_state_db_candidates", lambda: [db])
    monkeypatch.setattr(codex, "_CODEX_THREADS_CACHE", {})
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: {"deleted"})
    monkeypatch.setattr(server, "_codex_pool_alive", lambda now=None: True)
    monkeypatch.setattr(server, "_codex_pool_candidates_cache",
                        {"ts": 0.0, "sids": frozenset()})

    real_connect = sqlite3.connect
    reads = []

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)

        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == "threads":
                reads.append(column)
            return sqlite3.SQLITE_OK

        con.set_authorizer(authorize)
        return con

    monkeypatch.setattr(codex.sqlite3, "connect", connect)
    expected = {"fresh", "created-only"}
    if millisecond_schema:
        expected.add("stale")
    assert server._codex_pool_candidate_sids(now) == expected
    assert "first_user_message" not in reads
    assert "source" not in reads
    reads.clear()
    assert server._codex_pool_candidate_sids(now + 1) == expected
    assert reads == []

    # A narrow cache entry must never replace the full metadata projection.
    full = server._codex_fetch_threads(limit=100)
    assert all("first_user_message" in row for row in full)
    assert "first_user_message" in reads
    assert "deleted" not in {row["id"] for row in full}
