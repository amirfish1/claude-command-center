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

Written in stdlib `unittest` (no pytest) so it runs under CI's
`python -m unittest discover` — CCC keeps the runtime and its CI stdlib-only.
"""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

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


def _reset_history_conn() -> None:
    """Drop any cached connection so the next open() picks up the patched path."""
    with server._history_conn_lock:
        if server._history_conn is not None:
            try:
                server._history_conn.close()
            except Exception:
                pass
        server._history_conn = None


class TestHistorySearchConcurrency(unittest.TestCase):
    """Point server.py's history index at a fresh temp DB and reset the cached
    connection so each test opens its own, then hammer it from many threads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "index.db"
        _build_index(db)
        self._orig_path = server._HISTORY_INDEX_PATH
        server._HISTORY_INDEX_PATH = db
        _reset_history_conn()

    def tearDown(self):
        _reset_history_conn()
        server._HISTORY_INDEX_PATH = self._orig_path
        self._tmp.cleanup()

    def test_concurrent_searches_do_not_raise(self):
        """Many threads searching the shared connection at once must all succeed.

        Pre-fix this raised sqlite3.InterfaceError ('bad parameter or other API
        misuse') in at least one worker thread under load."""
        errors = []
        results = []
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

        self.assertFalse(errors, f"{len(errors)} worker(s) raised; first: {errors[0]!r}" if errors else "")
        self.assertTrue(results and all(r > 0 for r in results))

    def test_concurrent_mixed_search_and_fetch(self):
        """Interleave search_conversation_history and get_history_message — both
        touch the shared connection and must coexist without SQLITE_MISUSE."""
        errors = []
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

        self.assertFalse(errors, f"{len(errors)} worker(s) raised; first: {errors[0]!r}" if errors else "")


if __name__ == "__main__":
    unittest.main()
