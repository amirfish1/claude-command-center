"""Regression test for the shared-connection concurrency bug in the history
search path.

`_open_history_index()` caches one read-only sqlite3.Connection for the whole
process, and the server runs behind ThreadingHTTPServer (a thread per request).
A single sqlite3.Connection cannot be used concurrently from multiple threads:
overlapping .execute() on one shared handle raises SQLITE_MISUSE, surfaced as
`sqlite3.InterfaceError: bad parameter or other API misuse`.

These tests build a real FTS5 index matching the production schema and hammer
`search_conversation_history` / `get_history_message` from many threads at once.
Before the fix (no `_history_query_lock`), this reliably raised InterfaceError
inside one of the worker threads. After the fix, all calls return clean results.
"""
import sqlite3
import threading
from pathlib import Path

import pytest

import server


def _build_index(db_path: Path, n_docs: int = 400) -> None:
    """Create a minimal index.db matching the columns server.py reads:
    a `messages` table joined to a `messages_fts` FTS5 table on rowid."""
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            uuid TEXT, session_id TEXT, type TEXT, role TEXT,
            cwd TEXT, project_dir TEXT, git_branch TEXT,
            timestamp TEXT, ts_unix REAL, model TEXT,
            source_file TEXT, source_line INTEGER, content TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        """
    )
    rows = []
    for i in range(n_docs):
        content = f"alpha beta gamma session {i} widget refactor deadline"
        rows.append(
            (
                i + 1, f"uuid-{i}", f"sess-{i % 7}", "user", "user",
                "/Users/x/dev/proj", "proj", "main",
                "2026-06-02T10:00:00Z", 1780000000.0 + i, "claude-opus-4-8",
                "transcript.jsonl", i, content,
            )
        )
    con.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.executemany(
        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
        [(r[0], r[13]) for r in rows],
    )
    con.commit()
    con.close()


@pytest.fixture
def history_index(tmp_path, monkeypatch):
    """Point server.py's history index at a fresh temp DB and reset the
    cached connection so each test opens its own."""
    db = tmp_path / "index.db"
    _build_index(db)
    monkeypatch.setattr(server, "_HISTORY_INDEX_PATH", db)
    # Drop any connection cached by a prior test/run so the patched path takes.
    with server._history_conn_lock:
        if server._history_conn is not None:
            try:
                server._history_conn.close()
            except Exception:
                pass
        server._history_conn = None
    yield db
    with server._history_conn_lock:
        if server._history_conn is not None:
            try:
                server._history_conn.close()
            except Exception:
                pass
        server._history_conn = None


def test_concurrent_searches_do_not_raise(history_index):
    """Many threads searching the shared connection at once must all succeed.

    Pre-fix this raised sqlite3.InterfaceError ('bad parameter or other API
    misuse') in at least one worker thread under load."""
    errors: list[BaseException] = []
    results: list[int] = []
    barrier = threading.Barrier(24)

    def worker():
        barrier.wait()  # maximise overlap on the shared connection
        try:
            for _ in range(15):
                out = server.search_conversation_history("widget refactor", limit=20)
                assert "error" not in out, out.get("error")
                results.append(len(out["results"]))
        except BaseException as e:  # noqa: BLE001 — capture across threads
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"{len(errors)} worker(s) raised; first: {errors[0]!r}"
    assert results and all(r > 0 for r in results)


def test_concurrent_mixed_search_and_fetch(history_index):
    """Interleave search_conversation_history and get_history_message — both
    touch the shared connection and must coexist without SQLITE_MISUSE."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(20)

    def searcher():
        barrier.wait()
        try:
            for _ in range(20):
                out = server.search_conversation_history("alpha beta", limit=10)
                assert "error" not in out, out.get("error")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def fetcher():
        barrier.wait()
        try:
            for i in range(20):
                server.get_history_message(f"uuid-{i}")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=searcher) for _ in range(10)]
    threads += [threading.Thread(target=fetcher) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"{len(errors)} worker(s) raised; first: {errors[0]!r}"
