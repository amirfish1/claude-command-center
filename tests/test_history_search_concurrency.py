"""Regression test for the shared-connection concurrency bug in the history
search path.

`_open_history_index()` caches one read-only sqlite3.Connection for the whole
process, and the server runs behind ThreadingHTTPServer (a thread per request).
A single sqlite3.Connection cannot be used concurrently from multiple threads:
overlapping .execute() on one shared handle raises SQLITE_MISUSE, surfaced as
`sqlite3.InterfaceError: bad parameter or other API misuse`.

`get_history_message` (the click-through panel) still goes through that shared
connection and is exercised here for real. `search_conversation_history` moved
to ccc_server.session_fts under MEMO-FIX-19, which opens one sqlite3.Connection
per thread (see session_fts._get_connection) rather than sharing a single
handle — the original SQLITE_MISUSE class this file was written to catch no
longer applies to the search path. It's still worth hammering concurrently
(many threads opening/syncing the same on-disk FTS db must not raise or
deadlock), just via a session_fts-backed corpus instead of the old
claude-index `messages`/`messages_fts` schema.

Written in stdlib `unittest` (no pytest) so it runs under CI's
`python -m unittest discover` — CCC keeps the runtime and its CI stdlib-only.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server


_THREAD_TIMEOUT_SECONDS = 15
_PROCESS_TIMEOUT_SECONDS = 30
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _join_threads(threads, timeout=_THREAD_TIMEOUT_SECONDS, clock=time.monotonic):
    """Join a worker group within one shared deadline.

    A regression in the code under test must fail this test instead of leaving
    unittest discovery blocked forever on the first stuck worker.
    """
    deadline = clock() + timeout
    for thread in threads:
        thread.join(timeout=max(0, deadline - clock()))
    alive = [thread.name for thread in threads if thread.is_alive()]
    if alive:
        raise AssertionError(
            f"{len(alive)} worker thread(s) did not finish within {timeout:g}s: "
            + ", ".join(alive)
        )


class TestThreadHarness(unittest.TestCase):
    def test_join_threads_fails_within_one_global_deadline(self):
        class FakeClock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = FakeClock()

        class StuckThread:
            def __init__(self, name):
                self.name = name
                self.join_timeouts = []

            def join(self, timeout):
                self.join_timeouts.append(timeout)
                clock.now += timeout

            def is_alive(self):
                return True

        threads = [
            StuckThread(f"stuck-{i}")
            for i in range(3)
        ]

        self.assertIn("clock", _join_threads.__code__.co_varnames)
        with self.assertRaisesRegex(AssertionError, "3 worker thread"):
            _join_threads(threads, timeout=0.05, clock=clock)

        self.assertAlmostEqual(clock.now, 0.05)
        self.assertAlmostEqual(threads[0].join_timeouts[0], 0.05)
        self.assertEqual(threads[1].join_timeouts, [0])
        self.assertEqual(threads[2].join_timeouts, [0])

    def test_isolated_worker_is_killed_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = globals().get("_run_isolated_history_case")
            self.assertTrue(
                callable(runner),
                "_run_isolated_history_case is missing",
            )
            started = time.monotonic()
            with self.assertRaisesRegex(AssertionError, "timed out"):
                runner("__hang__", Path(tmp) / "index.db", timeout=0.1)
            self.assertLess(time.monotonic() - started, 5)


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


def _build_session_fts_corpus(projects_dir: Path, n_docs: int = 400) -> None:
    """Claude-Code-shaped transcripts for session_fts to index, mirroring the
    content _build_index used to put in the old claude-index schema."""
    repo_dir = projects_dir / "proj"
    repo_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n_docs):
        sid = f"aaaaaaaa-0000-0000-0000-{i:012d}"
        content = f"alpha beta gamma session {i} widget refactor deadline"
        line = json.dumps({
            "type": "user",
            "cwd": str(repo_dir),
            "message": {"role": "user", "content": content},
        })
        (repo_dir / f"{sid}.jsonl").write_text(line + "\n", encoding="utf-8")


def _set_session_fts_env(tmp_dir: Path) -> None:
    os.environ["CCC_SESSION_FTS_DB"] = str(tmp_dir / "session_fts.sqlite")
    os.environ["CCC_PROJECTS_ROOT"] = str(tmp_dir / "projects")
    os.environ["CCC_CODEX_SESSIONS_ROOT"] = str(tmp_dir / "codex-empty")
    os.environ["CCC_KIMI_SESSIONS_ROOT"] = str(tmp_dir / "kimi-empty")
    os.environ["CCC_GEMINI_TMP_ROOT"] = str(tmp_dir / "gemini-empty")
    os.environ["CCC_CURSOR_PROJECTS_ROOT"] = str(tmp_dir / "cursor-empty")
    os.environ["CCC_SESSION_FTS_DAYS"] = "0"
    os.environ["CCC_SESSION_FTS_ALLOW_SCRATCH"] = "1"
    os.environ["CCC_SESSION_FTS_EMBED"] = "0"


def _prewarm_session_fts(query: str) -> None:
    """Run search_conversation_history once and drain any background
    cold-start indexing before the concurrent phase starts, so the threaded
    workload below exercises concurrent READS (the thing under test), not a
    race against the one-time initial index build (covered separately by
    test_session_fts.py::test_search_sessions_cold_start_offloads_to_background)."""
    from ccc_server import session_fts

    server.search_conversation_history(query, limit=1)
    deadline = time.monotonic() + _THREAD_TIMEOUT_SECONDS
    while session_fts.is_indexing() and time.monotonic() < deadline:
        time.sleep(0.05)


def _reset_history_conn() -> None:
    """Drop any cached connection so the next open() picks up the patched path."""
    with server._history_conn_lock:
        if server._history_conn is not None:
            try:
                server._history_conn.close()
            except Exception:
                pass
        server._history_conn = None


def _raise_worker_errors(errors):
    if errors:
        raise AssertionError(
            f"{len(errors)} worker(s) raised; first: {errors[0]!r}"
        )


def _run_concurrent_searches():
    errors = []
    results = []
    barrier = threading.Barrier(24)

    def worker():
        try:
            barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            for _ in range(15):
                out = server.search_conversation_history(
                    "widget refactor",
                    limit=20,
                )
                if "error" in out:
                    raise AssertionError(out.get("error"))
                results.append(len(out["results"]))
        except BaseException as exc:  # noqa: BLE001 — capture across threads
            errors.append(exc)

    threads = [
        threading.Thread(
            target=worker,
            daemon=True,
            name=f"history-search-{i}",
        )
        for i in range(24)
    ]
    for thread in threads:
        thread.start()
    _join_threads(threads)
    _raise_worker_errors(errors)
    if not results or not all(result > 0 for result in results):
        raise AssertionError("concurrent searches returned empty results")


def _run_concurrent_mixed_search_and_fetch():
    errors = []
    barrier = threading.Barrier(20)

    def searcher():
        try:
            barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            for _ in range(20):
                out = server.search_conversation_history(
                    "alpha beta",
                    limit=10,
                )
                if "error" in out:
                    raise AssertionError(out.get("error"))
        except BaseException as exc:  # noqa: BLE001 — capture across threads
            errors.append(exc)

    def fetcher():
        try:
            barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
            for i in range(20):
                server.get_history_message(f"uuid-{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(
            target=searcher,
            daemon=True,
            name=f"history-search-{i}",
        )
        for i in range(10)
    ]
    threads += [
        threading.Thread(
            target=fetcher,
            daemon=True,
            name=f"history-fetch-{i}",
        )
        for i in range(10)
    ]
    for thread in threads:
        thread.start()
    _join_threads(threads)
    _raise_worker_errors(errors)


def _run_history_case(case, tmp_dir):
    if case == "__hang__":
        threading.Event().wait()
        return

    tmp_dir = Path(tmp_dir)
    server._HISTORY_INDEX_PATH = tmp_dir / "index.db"
    _reset_history_conn()
    _set_session_fts_env(tmp_dir)
    # Isolate the Hermes channel too: otherwise the searches scan this host's
    # real ~/.hermes DBs and blow the thread deadline on a busy machine.
    server.HERMES_STATE_DB = tmp_dir / "hermes" / "state.db"
    server.HERMES_PROFILES_DIR = tmp_dir / "hermes" / "profiles"
    _prewarm_session_fts("widget refactor")
    succeeded = False
    try:
        if case == "search":
            _run_concurrent_searches()
        elif case == "mixed":
            _run_concurrent_mixed_search_and_fetch()
        else:
            raise AssertionError(f"unknown history concurrency case: {case}")
        succeeded = True
    finally:
        if succeeded:
            _reset_history_conn()
        else:
            # The parent process owns the timeout boundary. Avoid potentially
            # blocking on close if a daemon is still inside SQLite; this child
            # exits immediately and takes its private connection + locks with it.
            server._history_conn = None


def _run_isolated_history_case(
    case,
    tmp_dir,
    timeout=_PROCESS_TIMEOUT_SECONDS,
):
    command = [
        sys.executable,
        "-m",
        "tests.test_history_search_concurrency",
        "--history-worker",
        case,
        str(tmp_dir),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"history concurrency worker timed out after {timeout:g}s: {case}"
        ) from exc
    if result.returncode != 0:
        detail = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        raise AssertionError(
            f"isolated history concurrency worker failed ({case})"
            + (f":\n{detail}" if detail else "")
        )


class TestHistorySearchConcurrency(unittest.TestCase):
    """Run each threaded SQLite workload in a killable child process."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_dir = Path(self._tmp.name)
        _build_index(self._tmp_dir / "index.db")
        _build_session_fts_corpus(self._tmp_dir / "projects")

    def tearDown(self):
        self._tmp.cleanup()

    def test_concurrent_searches_do_not_raise(self):
        """Many threads calling search_conversation_history (session_fts,
        thread-local connections) must all succeed concurrently."""
        _run_isolated_history_case("search", self._tmp_dir)

    def test_concurrent_mixed_search_and_fetch(self):
        """search_conversation_history (session_fts) and get_history_message
        (the shared claude-index connection under _history_query_lock) must
        coexist without SQLITE_MISUSE or hanging.

        Pre-fix, sharing one sqlite3.Connection across threads for the search
        path reliably raised sqlite3.InterfaceError ('bad parameter or other
        API misuse') under load; get_history_message still uses that shared
        connection today, so this remains a real regression guard for it."""
        _run_isolated_history_case("mixed", self._tmp_dir)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--history-worker":
        _run_history_case(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
