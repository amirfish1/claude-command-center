"""Regression coverage for Devin CLI message queue drain."""

import importlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock


class DevinQueueTests(unittest.TestCase):
    def test_devin_prompt_history_count_reads_sqlite_row(self):
        """Delivery proof can count a row from the CLI's sqlite.Row connection."""
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "sessions.db")
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "CREATE TABLE prompt_history "
                    "(session_id TEXT, content TEXT, timestamp INTEGER)",
                )
                con.execute(
                    "INSERT INTO prompt_history VALUES (?, ?, ?)",
                    ("proof-test", "hello devin", 100),
                )
                con.commit()
            finally:
                con.close()
            with mock.patch.dict(os.environ, {"CCC_DEVIN_DB": db_path}):
                self.assertEqual(
                    server._devin_cli_prompt_history_count(
                        "proof-test", "hello devin", 99,
                    ),
                    1,
                )

    def test_devin_cli_inject_not_routed_to_control_plane(self):
        """Devin CLI follow-ups must stay in the dashboard process.

        Routing them through the control-plane ``claude`` engine splits the
        durable resume queue between the worker and the dashboard watcher. The
        dashboard watcher drains the queue by calling ``resume_session_devin``
        locally, so the initial inject must also be handled locally.
        """
        server = importlib.import_module("server")
        sid = "devincli-routing-test"
        with mock.patch.object(server, "_is_codex_session", return_value=False), \
             mock.patch.object(server, "_is_kimi_session", return_value=False), \
             mock.patch.object(server, "_is_devin_cli_session", return_value=True), \
             mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(server, "session_live_status", return_value={
                 "live": False, "status": None, "kind": None,
                 "tty": None, "terminal_app": None,
             }), \
             mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
             mock.patch.object(server, "_save_pending_inputs") as save, \
             mock.patch.object(server, "resume_session_devin") as resume, \
             mock.patch.object(server, "_control_plane_engine_call") as cp:
            result = server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "devin-resume-queued")
        pump.assert_called_once_with(sid)
        resume.assert_not_called()
        cp.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["follow up"])

    def test_ask_routes_devin_cli_to_devin_resume(self):
        """A synchronous ask must not launch a Claude resume for a Devin ID."""
        server = importlib.import_module("server")
        expected = {"ok": True, "text": "Devin reply", "source": "devin-resume"}
        with mock.patch.object(server, "_detect_session_engine", return_value="devin"), \
             mock.patch.object(server, "ask_engine_session_and_wait", return_value=expected) as ask, \
             mock.patch.object(server, "resume_session_headless") as claude_resume:
            result = server.ask_session_and_wait("devincli-routing-test", "follow up")

        self.assertEqual(result, expected)
        ask.assert_called_once_with(
            "devincli-routing-test", "follow up", 30000, "devin",
        )
        claude_resume.assert_not_called()

    def test_devin_resume_watchdog_requeues_startup_failure(self):
        """A devin resume that dies at startup requeues the follow-up (OPS-807).

        `devin --resume -p` can exit non-zero seconds after spawn (its own
        sessions DB busy-timeout, e.g. "Error: session/list failed: database
        is locked") — after the send path already reported success. The
        watchdog must park the text back on the durable queue for retry
        instead of silently dropping it.
        """
        import subprocess
        server = importlib.import_module("server")
        sid = "devincli-watchdog-test"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                proc = subprocess.Popen(
                    ["/bin/sh", "-c",
                     "echo 'Error: session/list failed: database is locked'; exit 1"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            with mock.patch.object(server, "_pending_resume_queue", {}) as queue, \
                 mock.patch.object(server, "_pending_resume_retry_after", {}), \
                 mock.patch.object(server, "_save_pending_inputs"):
                server._start_devin_resume_watchdog(proc, sid, "follow up", log_path)
                deadline = time.time() + 5
                while time.time() < deadline and not queue.get(sid):
                    time.sleep(0.05)
        self.assertEqual(queue.get(sid), ["follow up"])

    def test_devin_resume_watchdog_drops_gone_session(self):
        """'No session found' at startup is permanent: drop, never requeue.

        OPS-922: after Devin's sessions.db lost rows, the startup watchdog
        requeued undeliverable follow-ups forever — a fresh `devin --resume`
        spawn every ~60s per dead session (~70 spawns in 15 min).
        """
        import subprocess
        server = importlib.import_module("server")
        sid = "devincli-watchdog-gone-test"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                proc = subprocess.Popen(
                    ["/bin/sh", "-c",
                     "echo \"Error: No session found matching 'watchdog-gone-test'\"; exit 1"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            with mock.patch.object(server, "_pending_resume_queue", {sid: ["follow up"]}) as queue, \
                 mock.patch.object(server, "_pending_resume_retry_after", {}) as retry_after, \
                 mock.patch.object(server, "_save_pending_inputs"):
                server._start_devin_resume_watchdog(proc, sid, "follow up", log_path)
                deadline = time.time() + 5
                while time.time() < deadline and queue.get(sid):
                    time.sleep(0.05)
        self.assertFalse(queue.get(sid))
        self.assertNotIn(sid, retry_after)

    def test_devin_resume_watchdog_ignores_success_and_running(self):
        """No requeue when the resume succeeds fast or outlives the window."""
        import subprocess
        server = importlib.import_module("server")
        sid = "devincli-watchdog-ok"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                ok_proc = subprocess.Popen(
                    ["/bin/sh", "-c", "exit 0"], stdout=fh, stderr=subprocess.STDOUT,
                )
            with mock.patch.object(server, "_pending_resume_queue", {}) as queue, \
                 mock.patch.object(server, "_pending_resume_retry_after", {}), \
                 mock.patch.object(server, "_save_pending_inputs"):
                server._start_devin_resume_watchdog(ok_proc, sid, "follow up", log_path)
                ok_proc.wait(timeout=10)
                time.sleep(0.3)
                self.assertFalse(queue.get(sid))
                # A process still running past the window must not requeue.
                with open(log_path, "w") as fh2:
                    running = subprocess.Popen(
                        ["/bin/sh", "-c", "sleep 2"], stdout=fh2, stderr=subprocess.STDOUT,
                    )
                with mock.patch.object(
                    server, "_DEVIN_RESUME_WATCHDOG_WINDOW_S", 0.2
                ):
                    server._start_devin_resume_watchdog(running, sid, "late", log_path)
                    time.sleep(0.6)
                self.assertFalse(queue.get(sid))
                running.wait(timeout=10)

    def test_devin_resume_queue_respects_running_spawn(self):
        """The resume-queue watcher must wait while a Devin resume is running."""
        server = importlib.import_module("server")
        sid = "devincli-busy-test"
        entry = {
            "engine": "devin",
            "resumed_sid": sid,
            "pid": 12345,
            "proc": mock.Mock(poll=mock.Mock(return_value=None)),
        }
        with mock.patch.object(server, "_spawned_sessions", [entry]):
            self.assertTrue(server._resume_queue_engine_busy(sid))

    def test_devin_resume_queue_drains_when_spawn_exits(self):
        """The resume-queue watcher may drain once the running Devin resume exits."""
        server = importlib.import_module("server")
        sid = "devincli-idle-test"
        entry = {
            "engine": "devin",
            "resumed_sid": sid,
            "pid": 12345,
            "proc": mock.Mock(poll=mock.Mock(return_value=0)),
        }
        with mock.patch.object(server, "_spawned_sessions", [entry]):
            self.assertFalse(server._resume_queue_engine_busy(sid))

    def test_devin_spawn_session_id_resolved_from_cli_db(self):
        """A Devin spawn without a session id should resolve from the CLI DB."""
        server = importlib.import_module("server")

        # Build a throwaway Devin CLI sessions DB with one matching session.
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        self.addCleanup(lambda: os.unlink(db_path) if os.path.exists(db_path) else None)

        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                working_directory TEXT,
                created_at REAL,
                last_activity_at REAL
            );
            CREATE TABLE prompt_history (
                session_id TEXT,
                content TEXT,
                timestamp REAL,
                is_shell INTEGER
            );
            """
        )
        spawn_ts = 1700000000.0
        spawned_at = datetime.fromtimestamp(spawn_ts).strftime("%Y%m%dT%H%M%S")
        con.execute(
            "INSERT INTO sessions (id, working_directory, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?)",
            ("ferret-test", "/tmp/bym", spawn_ts, spawn_ts),
        )
        con.execute(
            "INSERT INTO prompt_history (session_id, content, timestamp, is_shell) "
            "VALUES (?, ?, ?, ?)",
            ("ferret-test", "fix the client portal bug", spawn_ts, 0),
        )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )

        entry = {
            "engine": "devin",
            "cwd": "/tmp/bym",
            "repo_path": "/tmp/bym",
            "command_summary": "fix the client portal bug",
            "spawned_at": spawned_at,
        }
        self.assertEqual(
            server._devin_cli_session_id_for_spawn_entry(entry),
            "devincli-ferret-test",
        )
        self.assertEqual(
            server._spawn_session_id_from_entry(dict(entry)),
            "devincli-ferret-test",
        )

    def test_devin_prompt_match_ignores_whitespace(self):
        """Spawn summaries flatten newlines; the CLI DB keeps them."""
        server = importlib.import_module("server")
        summary = (
            "Failed to load resource: the server responded with a status of 403 () "
            "/icons/pwa-192x192.png:1 Failed to load resource"
        )
        db_prompt = (
            "Failed to load resource: the server responded with a status of 403 ()\n"
            "/icons/pwa-192x192.png:1 Failed to load resource"
        )
        self.assertTrue(server._devin_cli_first_prompts_match(summary, db_prompt))

    def test_devin_spawn_session_id_resolved_from_lock_pid(self):
        """Devin writes the child pid into session_locks/<id>.lock.

        CCC tracks the spawn pid. Matching that pid to a lock file is the
        reliable correlation when prompt/cwd matching misses.
        """
        server = importlib.import_module("server")
        import ccc_server.devin as devin_mod

        lock_dir = tempfile.mkdtemp(prefix="devin-locks-")
        self.addCleanup(lambda: __import__("shutil").rmtree(lock_dir, ignore_errors=True))
        lock_path = os.path.join(lock_dir, "palm-burn.lock")
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("51894\n")

        with mock.patch.object(devin_mod, "DEVIN_CLI_LOCKS_DIR", __import__("pathlib").Path(lock_dir)):
            entry = {
                "engine": "devin",
                "pid": 51894,
                "cwd": "/tmp/bym",
                "prompt": "unrelated because lock pid should win",
            }
            self.assertEqual(
                server._devin_cli_session_id_for_spawn_entry(entry),
                "devincli-palm-burn",
            )

    def test_devin_cli_session_live_requires_live_pid(self):
        """A leftover lock file must not count as live if the pid is dead."""
        server = importlib.import_module("server")
        import ccc_server.devin as devin_mod

        lock_dir = tempfile.mkdtemp(prefix="devin-locks-")
        self.addCleanup(lambda: __import__("shutil").rmtree(lock_dir, ignore_errors=True))
        with open(os.path.join(lock_dir, "stale-lock.lock"), "w", encoding="utf-8") as fh:
            fh.write("999999999\n")
        with open(os.path.join(lock_dir, "live-lock.lock"), "w", encoding="utf-8") as fh:
            fh.write("%s\n" % os.getpid())

        with mock.patch.object(devin_mod, "DEVIN_CLI_LOCKS_DIR", __import__("pathlib").Path(lock_dir)):
            self.assertFalse(server._devin_cli_session_live("stale-lock"))
            self.assertTrue(server._devin_cli_session_live("live-lock"))
            self.assertFalse(server._devin_cli_session_live("missing"))

    def test_devin_list_attaches_spawn_pid(self):
        """Durable Devin CLI rows must carry spawn_pid so the UI placeholder swaps."""
        server = importlib.import_module("server")
        import ccc_server.devin as devin_mod

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        self.addCleanup(lambda: os.unlink(db_path) if os.path.exists(db_path) else None)
        now = time.time()
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                working_directory TEXT,
                backend_type TEXT,
                model TEXT,
                agent_mode TEXT,
                created_at REAL,
                last_activity_at REAL,
                title TEXT,
                main_chain_id TEXT
            );
            CREATE TABLE prompt_history (
                session_id TEXT,
                content TEXT,
                timestamp REAL,
                is_shell INTEGER
            );
            """
        )
        con.execute(
            "INSERT INTO sessions (id, working_directory, backend_type, model, "
            "agent_mode, created_at, last_activity_at, title, main_chain_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("mighty-outfit", "/tmp/ccc", "", "", "", now, now, "Autocompact", None),
        )
        con.execute(
            "INSERT INTO prompt_history (session_id, content, timestamp, is_shell) "
            "VALUES (?, ?, ?, ?)",
            ("mighty-outfit", "can we add autocompact settings", now, 0),
        )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )

        entry = {
            "engine": "devin",
            "pid": 67663,
            "cwd": "/tmp/ccc",
            "repo_path": "/tmp/ccc",
            "command_summary": "can we add autocompact settings",
            "spawned_at": datetime.fromtimestamp(now).strftime("%Y%m%dT%H%M%S"),
            "proc": mock.Mock(poll=mock.Mock(return_value=None)),
        }
        with mock.patch.object(server, "_spawned_sessions", [entry]), \
             mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_CACHE", {}):
            rows = server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        match = [r for r in rows if r.get("id") == "devincli-mighty-outfit"]
        self.assertTrue(match)
        self.assertEqual(match[0].get("spawn_pid"), 67663)

    def test_devin_list_attaches_acp_spawn_id(self):
        """ACP spawns have no process pid — the durable row must still carry
        the synthetic spawn id as spawn_pid so the sidebar placeholder
        (keyed by that same id) can swap onto the real row."""
        server = importlib.import_module("server")
        import ccc_server.devin as devin_mod

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        self.addCleanup(lambda: os.unlink(db_path) if os.path.exists(db_path) else None)
        now = time.time()
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                working_directory TEXT,
                backend_type TEXT,
                model TEXT,
                agent_mode TEXT,
                created_at REAL,
                last_activity_at REAL,
                title TEXT,
                main_chain_id TEXT
            );
            CREATE TABLE prompt_history (
                session_id TEXT,
                content TEXT,
                timestamp REAL,
                is_shell INTEGER
            );
            """
        )
        con.execute(
            "INSERT INTO sessions (id, working_directory, backend_type, model, "
            "agent_mode, created_at, last_activity_at, title, main_chain_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("acp-raw-1", "/tmp/ccc", "", "", "", now, now, "ACP session", None),
        )
        con.execute(
            "INSERT INTO prompt_history (session_id, content, timestamp, is_shell) "
            "VALUES (?, ?, ?, ?)",
            ("acp-raw-1", "spawn me over acp", now, 0),
        )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )

        # Shape produced by spawn_session_devin's ACP branch: no proc, the
        # spawn_id doubles as the pid, session_id known in-band.
        entry = {
            "engine": "devin",
            "pid": "devin-acp-acp-raw-1",
            "spawn_id": "devin-acp-acp-raw-1",
            "session_id": "devincli-acp-raw-1",
            "cwd": "/tmp/ccc",
            "repo_path": "/tmp/ccc",
            "command_summary": "spawn me over acp",
            "spawned_at": datetime.fromtimestamp(now).strftime("%Y%m%dT%H%M%S"),
            "via": "devin-acp",
            # Skip the one-shot exit-cleanup write so the test stays
            # side-effect free; liveness is irrelevant to spawn_pid.
            "_cleanup_done": True,
        }
        with mock.patch.object(server, "_spawned_sessions", [entry]), \
             mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_CACHE", {}):
            rows = server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        match = [r for r in rows if r.get("id") == "devincli-acp-raw-1"]
        self.assertTrue(match)
        self.assertEqual(match[0].get("spawn_pid"), "devin-acp-acp-raw-1")

    def test_devin_spawn_session_id_resolved_from_message_nodes(self):
        """One-shot `devin -p` writes message_nodes, not prompt_history."""
        server = importlib.import_module("server")

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        self.addCleanup(lambda: os.unlink(db_path) if os.path.exists(db_path) else None)

        spawn_ts = 1700000000.0
        spawned_at = datetime.fromtimestamp(spawn_ts).strftime("%Y%m%dT%H%M%S")
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                working_directory TEXT,
                created_at REAL,
                last_activity_at REAL
            );
            CREATE TABLE prompt_history (
                session_id TEXT,
                content TEXT,
                timestamp REAL,
                is_shell INTEGER
            );
            CREATE TABLE message_nodes (
                row_id INTEGER PRIMARY KEY,
                session_id TEXT,
                node_id INTEGER,
                chat_message TEXT,
                created_at REAL
            );
            """
        )
        con.execute(
            "INSERT INTO sessions (id, working_directory, created_at, last_activity_at) "
            "VALUES (?, ?, ?, ?)",
            ("mighty-outfit", "/tmp/ccc", spawn_ts * 1000, spawn_ts * 1000),
        )
        con.execute(
            "INSERT INTO message_nodes "
            "(session_id, node_id, chat_message, created_at) VALUES (?, ?, ?, ?)",
            (
                "mighty-outfit",
                1,
                json.dumps({
                    "role": "user",
                    "content": "can we add here: autocompact settings",
                    "metadata": {"is_user_input": True},
                }),
                spawn_ts,
            ),
        )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )

        entry = {
            "engine": "devin",
            "cwd": "/tmp/ccc",
            "repo_path": "/tmp/ccc",
            "command_summary": "can we add here: autocompact settings",
            "spawned_at": spawned_at,
        }
        self.assertEqual(
            server._devin_cli_session_id_for_spawn_entry(entry),
            "devincli-mighty-outfit",
        )


class DevinListPerfTests(unittest.TestCase):
    """The Devin CLI session list must not rescan every session's history.

    sessions.db stores full turn content inline (observed: 3.7 GB across
    ~33k message_nodes rows). Whole-table json_extract scans made every list
    rebuild take 40-87 s, and the rebuild fires on every DB write, so a new
    session could not appear in the sidebar for a minute. Per-session fields
    are memoized by (last_activity_at, max row_id, row count); only changed
    sessions are re-queried, with bounded head/tail walks.
    """

    SCHEMA = """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            working_directory TEXT,
            backend_type TEXT,
            model TEXT,
            agent_mode TEXT,
            created_at REAL,
            last_activity_at REAL,
            title TEXT,
            main_chain_id TEXT
        );
        CREATE TABLE prompt_history (
            session_id TEXT,
            content TEXT,
            timestamp REAL,
            is_shell INTEGER
        );
        CREATE TABLE message_nodes (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            parent_node_id INTEGER,
            chat_message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(session_id, node_id)
        );
        CREATE TABLE tool_call_state (
            session_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_call_json TEXT,
            tool_call_update_json TEXT,
            PRIMARY KEY (session_id, tool_call_id)
        );
    """

    def _msg(self, role, content, **meta):
        return json.dumps({"role": role, "content": content, "metadata": meta})

    def _setup(self):
        server = importlib.import_module("server")
        import ccc_server.devin as devin_mod

        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "sessions.db")
        memo_path = os.path.join(tmpdir, "row_memo.json")
        now = int(time.time() * 1000)
        con = sqlite3.connect(db_path)
        con.executescript(self.SCHEMA)
        for sid in ("alpha-one", "beta-two"):
            con.execute(
                "INSERT INTO sessions VALUES (?, ?, '', '', '', ?, ?, NULL, NULL)",
                (sid, "/tmp/ccc", now, now),
            )
        con.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("alpha-one", 1, self._msg("user", "fix payouts sort", is_user_input=True), now),
        )
        con.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "alpha-one", 2,
                self._msg(
                    "assistant", "Done: ran git commit",
                    generation_model="claude-opus-5",
                    metrics={"input_tokens": 1000, "cache_read_tokens": 500},
                ),
                now,
            ),
        )
        con.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("beta-two", 1, self._msg("user", "hello there", is_user_input=True), now),
        )
        con.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("beta-two", 2, self._msg("assistant", "hi", generation_model="m-b"), now),
        )
        con.execute(
            "INSERT INTO tool_call_state VALUES (?, ?, ?, ?)",
            (
                "alpha-one", "tc1",
                json.dumps({
                    "_meta": {"cognition.ai/inferenceToolName": "run_subagent"},
                    "rawInput": {"title": "scan repo", "profile": "explore"},
                }),
                json.dumps({"status": "completed"}),
            ),
        )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )
        # Point the second (Devin desktop app) home at a path that doesn't
        # exist rather than leaving it unset: unset falls through to the
        # real ~/.local/share/devin/cli-next/sessions.db on a dev machine
        # that has the desktop app installed, silently merging real
        # sessions into what must be an isolated fixture.
        prev_next_db = os.environ.get("CCC_DEVIN_NEXT_DB")
        os.environ["CCC_DEVIN_NEXT_DB"] = os.path.join(tmpdir, "cli-next-sessions.db")
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_NEXT_DB", prev_next_db)
                if prev_next_db is not None
                else os.environ.pop("CCC_DEVIN_NEXT_DB", None)
            )
        )
        patches = [
            mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_CACHE", {}),
            mock.patch.object(devin_mod, "_DEVIN_CLI_ROW_MEMO", {}),
            mock.patch.object(devin_mod, "_DEVIN_CLI_ROW_MEMO_LOADED", False),
            mock.patch.object(devin_mod, "_devin_cli_row_memo_path", lambda: devin_mod.Path(memo_path)),
            mock.patch.object(devin_mod, "_DEVIN_CLI_ROW_MEMO_BG", {"pending": {}, "thread": None}),
            mock.patch.object(server, "_spawned_sessions", []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # Cleanups run LIFO: drain the background finisher before the
        # patches above unwind, so it never touches the real memo.
        self.addCleanup(devin_mod._devin_cli_row_memo_background_join, 10)
        return server, devin_mod, db_path, now

    def _bump_mtime(self, db_path, secs):
        st = os.stat(db_path)
        os.utime(db_path, (st.st_atime + secs, st.st_mtime + secs))

    def test_devin_tool_dump_title_falls_back_to_first_message(self):
        """The CLI auto-titler can store a serialized tool call
        (``functions.Bash:0{"command": ...}``) as sessions.title instead of a
        summary (CCC-1164). The row must treat it as untitled and fall back
        to the first user prompt."""
        server, devin_mod, db_path, now = self._setup()
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE sessions SET title = ? WHERE id = ?",
            (
                'functions.Bash:0{"command": "cat ~/.watchtower/learnings/CCC.md"}',
                "alpha-one",
            ),
        )
        con.commit()
        con.close()
        rows = {r["id"]: r for r in server.find_devin_cli_conversations(
            "/tmp/ccc", include_old=True)}
        a = rows["devincli-alpha-one"]
        self.assertEqual(a["display_name"], "fix payouts sort")
        self.assertIsNone(a["ai_title"])

    def test_devin_title_is_tool_dump_matches_only_serialized_calls(self):
        import ccc_server.devin as devin_mod
        self.assertTrue(devin_mod._devin_title_is_tool_dump(
            'functions.Bash:0{"command": "cat x"}'))
        self.assertTrue(devin_mod._devin_title_is_tool_dump(
            'Functions.edit:12{"file_path": "/x"}'))
        self.assertFalse(devin_mod._devin_title_is_tool_dump("Autocompact"))
        self.assertFalse(devin_mod._devin_title_is_tool_dump("functions Bash notes"))
        self.assertFalse(devin_mod._devin_title_is_tool_dump(""))
        self.assertFalse(devin_mod._devin_title_is_tool_dump(None))

    def test_devin_list_memoizes_per_session_fields(self):
        server, devin_mod, db_path, now = self._setup()
        orig = devin_mod._devin_cli_row_fields_for_session
        calls = []

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy), \
             mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_TTL_SEC", 0):
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])
            a = rows["devincli-alpha-one"]
            b = rows["devincli-beta-two"]
            self.assertEqual(a["first_message"], "fix payouts sort")
            self.assertEqual(a["model"], "claude-opus-5")
            self.assertEqual(a["last_assistant_text"], "Done: ran git commit")
            self.assertEqual(a["latest_input_tokens"], 1500)
            self.assertTrue(a["has_commit"])
            self.assertFalse(a["has_push"])
            self.assertEqual(a["subagent_count"], 1)
            self.assertEqual(a["subagent_recent"][0]["status"], "done")
            self.assertEqual(b["model"], "m-b")
            self.assertFalse(b["has_commit"])
            self.assertEqual(b["subagent_count"], 0)

            # Append a turn to beta only. Only beta must be re-queried.
            con = sqlite3.connect(db_path)
            con.execute(
                "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("beta-two", 3, self._msg("assistant", "pushed via git push", generation_model="m-b2"), now + 1),
            )
            con.commit()
            con.close()
            self._bump_mtime(db_path, 10)
            calls.clear()
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(calls, ["beta-two"])
            b = rows["devincli-beta-two"]
            self.assertTrue(b["has_push"])
            self.assertEqual(b["model"], "m-b2")
            self.assertEqual(b["last_assistant_text"], "pushed via git push")
            self.assertEqual(b["first_message"], "hello there")
            self.assertTrue(rows["devincli-alpha-one"]["has_commit"])

            # A restart (empty in-memory memo) reloads the persisted memo and
            # re-queries nothing when the DB is unchanged.
            devin_mod._DEVIN_CLI_ROW_MEMO.clear()
            devin_mod._DEVIN_CLI_ROW_MEMO_LOADED = False
            devin_mod._DEVIN_CLI_LIST_CACHE.clear()
            calls.clear()
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(calls, [])
            self.assertTrue(rows["devincli-beta-two"]["has_push"])
            self.assertEqual(rows["devincli-alpha-one"]["model"], "claude-opus-5")


    def test_devin_list_ttl_window_serves_without_restat(self):
        """Within the TTL window a variant is served from cache even when the
        DB changed (a live CLI touches WAL/SHM every few seconds); explicit
        invalidation (key=None) bypasses the TTL and forces a rebuild."""
        server, devin_mod, db_path, now = self._setup()
        orig = devin_mod._devin_cli_row_fields_for_session
        calls = []

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_TTL_SEC", 3600), \
             mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy):
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])

            # A DB write inside the TTL window is still served from cache.
            con = sqlite3.connect(db_path)
            con.execute(
                "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("beta-two", 3, self._msg("assistant", "pushed via git push", generation_model="m-b2"), now + 1),
            )
            con.commit()
            con.close()
            self._bump_mtime(db_path, 10)
            calls.clear()
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(calls, [])
            self.assertEqual(rows["devincli-beta-two"]["model"], "m-b")

            # A second variant (limit=1) is a cold build that coexists with
            # the first instead of evicting it: it re-queries only the changed
            # session, and the base variant afterwards still serves its own
            # cached (stale) rows rather than rebuilding.
            rows_limited = server.find_devin_cli_conversations(
                "/tmp/ccc", include_old=True, limit=1
            )
            self.assertEqual(len(rows_limited), 1)
            self.assertEqual(len(devin_mod._DEVIN_CLI_LIST_CACHE), 2)
            self.assertEqual(calls, ["beta-two"])
            calls.clear()
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(calls, [])
            self.assertEqual(rows["devincli-beta-two"]["model"], "m-b")

            # Invalidation (key=None) bypasses the TTL and rebuilds, picking
            # up the appended turn from the (already warm) row memo.
            for entry in devin_mod._DEVIN_CLI_LIST_CACHE.values():
                entry["key"] = None
                entry["ts"] = 0.0
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(calls, [])
            self.assertEqual(rows["devincli-beta-two"]["model"], "m-b2")

    def test_devin_list_cold_memo_defers_to_background(self):
        """With no budget every miss is deferred: the first list call returns
        placeholder fields immediately, one background thread fills the memo
        and drops the list cache, and the next call has the real fields. Each
        session is computed exactly once overall."""
        server, devin_mod, db_path, now = self._setup()
        orig = devin_mod._devin_cli_row_fields_for_session
        calls = []

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_DEVIN_CLI_COLD_BUILD_BUDGET_S", 0), \
             mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy):
            t0 = time.perf_counter()
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertLess(time.perf_counter() - t0, 5.0)
            self.assertEqual(sorted(rows), ["devincli-alpha-one", "devincli-beta-two"])
            a = rows["devincli-alpha-one"]
            self.assertEqual(a["first_message"], "")
            self.assertEqual(a["model"], "")
            self.assertEqual(a["last_assistant_text"], "")
            self.assertEqual(a["latest_input_tokens"], 0)
            self.assertFalse(a["has_commit"])
            self.assertEqual(a["subagent_count"], 0)
            self.assertTrue(a["display_name"].startswith("Devin session "))
            # Placeholders are never stored as complete memo entries.
            with devin_mod._DEVIN_CLI_ROW_MEMO_LOCK:
                self.assertFalse(
                    any(e.get("deferred") for e in devin_mod._DEVIN_CLI_ROW_MEMO.values())
                )

            self.assertTrue(devin_mod._devin_cli_row_memo_background_join(10))
            self.assertIsNone(devin_mod._DEVIN_CLI_ROW_MEMO_BG["thread"])
            self.assertEqual(devin_mod._DEVIN_CLI_ROW_MEMO_BG["pending"], {})
            # Background completion invalidates every cached list variant.
            self.assertEqual(len(devin_mod._DEVIN_CLI_LIST_CACHE), 1)
            self.assertIsNone(
                next(iter(devin_mod._DEVIN_CLI_LIST_CACHE.values()))["key"]
            )
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])

            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            a = rows["devincli-alpha-one"]
            self.assertEqual(a["first_message"], "fix payouts sort")
            self.assertEqual(a["model"], "claude-opus-5")
            self.assertEqual(a["last_assistant_text"], "Done: ran git commit")
            self.assertEqual(a["latest_input_tokens"], 1500)
            self.assertTrue(a["has_commit"])
            self.assertEqual(a["subagent_count"], 1)
            self.assertEqual(rows["devincli-beta-two"]["model"], "m-b")
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])
            self.assertIsNotNone(
                next(iter(devin_mod._DEVIN_CLI_LIST_CACHE.values()))["key"]
            )
            # The background stamp was persisted.
            with open(devin_mod._devin_cli_row_memo_path(), encoding="utf-8") as f:
                saved = json.load(f)["rows"]
            self.assertEqual(saved["alpha-one"]["model"], "claude-opus-5")

    def test_devin_list_default_budget_defers_nothing(self):
        """The tiny fixture fits inside the default budget: every field is
        computed on the request path and no background thread is started."""
        server, devin_mod, db_path, now = self._setup()
        orig = devin_mod._devin_cli_row_fields_for_session
        calls = []

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy):
            rows = {r["id"]: r for r in server.find_devin_cli_conversations("/tmp/ccc", include_old=True)}
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])
            self.assertEqual(rows["devincli-alpha-one"]["model"], "claude-opus-5")
            self.assertEqual(rows["devincli-beta-two"]["first_message"], "hello there")
            self.assertIsNone(devin_mod._DEVIN_CLI_ROW_MEMO_BG["thread"])
            self.assertEqual(devin_mod._DEVIN_CLI_ROW_MEMO_BG["pending"], {})
            self.assertIsNotNone(
                next(iter(devin_mod._DEVIN_CLI_LIST_CACHE.values()))["key"]
            )

    def test_devin_list_cold_budget_prioritises_recent_sessions(self):
        """Misses are computed most recently active first, so a budget that
        runs out leaves the oldest sessions for the background."""
        server, devin_mod, db_path, now = self._setup()
        con = sqlite3.connect(db_path)
        con.execute("UPDATE sessions SET last_activity_at = ? WHERE id = 'beta-two'", (now + 5000,))
        con.commit()
        con.close()
        calls = []
        orig = devin_mod._devin_cli_row_fields_for_session

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy):
            server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        self.assertEqual(calls, ["beta-two", "alpha-one"])

    def test_devin_overlay_fills_snapshot_gap(self):
        """A just-spawned Devin CLI row missing from the archive snapshot is
        overlaid on the /list path; rows already present or old are not."""
        server, devin_mod, db_path, now = self._setup()
        con = sqlite3.connect(db_path)
        # Devin stores session timestamps in epoch seconds; two hours old is
        # well outside the overlay window and there is no lock file for it.
        old_s = int(time.time()) - 2 * 3600
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, '', '', '', ?, ?, NULL, NULL)",
            ("gamma-old", "/tmp/ccc", old_s, old_s),
        )
        con.commit()
        con.close()
        rows = server._archive_overlay_devin_cli_sessions([])
        ids = sorted(r["id"] for r in rows)
        self.assertEqual(ids, ["devincli-alpha-one", "devincli-beta-two"])
        self.assertTrue(all(r["source"] == "devin-cli" for r in rows))
        rows = server._archive_overlay_devin_cli_sessions([{"id": "devincli-alpha-one"}])
        self.assertEqual([r["id"] for r in rows], ["devincli-beta-two"])

    def test_devin_list_ttl_hit_does_not_wait_on_rebuild_lock(self):
        """A warm TTL hit must not queue behind a 30-60s Devin list rebuild.

        Sidebar trash waits on a free HTTP/1.1 slot; if every /list poll
        parks on `_DEVIN_CLI_LIST_REBUILD_LOCK`, the trash POST sits in the
        browser queue for tens of seconds even though the handler is cheap.
        """
        server, devin_mod, db_path, now = self._setup()
        with mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_TTL_SEC", 3600):
            warmed = server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
            self.assertEqual(len(warmed), 2)
            held = threading.Event()
            release = threading.Event()

            def holder():
                with devin_mod._DEVIN_CLI_LIST_REBUILD_LOCK:
                    held.set()
                    release.wait(timeout=5)

            worker = threading.Thread(target=holder)
            worker.start()
            self.assertTrue(held.wait(timeout=1))
            try:
                t0 = time.perf_counter()
                rows = server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
                elapsed = time.perf_counter() - t0
                self.assertEqual(len(rows), 2)
                self.assertLess(elapsed, 0.5)
            finally:
                release.set()
                worker.join(timeout=2)

    def test_devin_overlay_serves_expired_cache_without_rebuild(self):
        """After the TTL window the overlay must not kick a 30-60s rebuild."""
        server, devin_mod, db_path, now = self._setup()
        orig = devin_mod._devin_cli_row_fields_for_session
        calls = []

        def spy(con, raw_id, prev):
            calls.append(raw_id)
            return orig(con, raw_id, prev)

        with mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_TTL_SEC", 3600), \
             mock.patch.object(devin_mod, "_devin_cli_row_fields_for_session", spy):
            # Warm the overlay's own cache variant (repo_path=None).
            server._archive_overlay_devin_cli_sessions([])
            self.assertEqual(sorted(calls), ["alpha-one", "beta-two"])
            for entry in devin_mod._DEVIN_CLI_LIST_CACHE.values():
                entry["ts"] = 0.0
            calls.clear()
            t0 = time.perf_counter()
            rows = server._archive_overlay_devin_cli_sessions([])
            elapsed = time.perf_counter() - t0
            self.assertEqual(sorted(r["id"] for r in rows), [
                "devincli-alpha-one", "devincli-beta-two",
            ])
            self.assertEqual(calls, [])
            self.assertLess(elapsed, 0.5)

    def test_devin_overlay_skips_rebuild_when_list_lock_is_held(self):
        """The /list Devin overlay must not block on an in-flight rebuild."""
        server, devin_mod, db_path, now = self._setup()
        held = threading.Event()
        release = threading.Event()

        def holder():
            with devin_mod._DEVIN_CLI_LIST_REBUILD_LOCK:
                held.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=holder)
        worker.start()
        self.assertTrue(held.wait(timeout=1))
        try:
            t0 = time.perf_counter()
            rows = server._archive_overlay_devin_cli_sessions([])
            elapsed = time.perf_counter() - t0
            self.assertEqual(rows, [])
            self.assertLess(elapsed, 0.5)
        finally:
            release.set()
            worker.join(timeout=2)

class DevinSpawnIdentityTests(unittest.TestCase):
    """Two Devin spawns with the same prompt must resolve to two sessions.

    The prompt-match fallback used to scan newest-first inside a symmetric
    900s window, so a second spawn with an identical prompt in the same cwd
    resolved to the FIRST spawn's session. The match must only consider rows
    created at or after the spawn stamp, prefer the one created soonest
    after it, and skip ids already claimed by another spawn entry.
    """

    CWD = "/tmp/ccc-devin-identity"
    PROMPT = "Reply with exactly: ccc-e2e-probe. Nothing else."

    def _setup(self, sessions):
        """Build a throwaway Devin CLI DB with ``sessions`` = [(id, created)].

        Every session shares ``CWD`` and ``PROMPT``. Isolates the resolver
        from this machine's real spawn registry and lock directory.
        """
        server = importlib.import_module("server")
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        self.addCleanup(lambda: os.unlink(db_path) if os.path.exists(db_path) else None)
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                working_directory TEXT,
                created_at INTEGER,
                last_activity_at INTEGER
            );
            CREATE TABLE prompt_history (
                session_id TEXT,
                content TEXT,
                timestamp INTEGER,
                is_shell INTEGER
            );
            """
        )
        for raw_id, created in sessions:
            con.execute(
                "INSERT INTO sessions (id, working_directory, created_at, last_activity_at) "
                "VALUES (?, ?, ?, ?)",
                (raw_id, self.CWD, int(created), int(created)),
            )
            con.execute(
                "INSERT INTO prompt_history (session_id, content, timestamp, is_shell) "
                "VALUES (?, ?, ?, ?)",
                (raw_id, self.PROMPT, int(created), 0),
            )
        con.commit()
        con.close()

        prev_db = os.environ.get("CCC_DEVIN_DB")
        os.environ["CCC_DEVIN_DB"] = db_path
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("CCC_DEVIN_DB", prev_db)
                if prev_db is not None
                else os.environ.pop("CCC_DEVIN_DB", None)
            )
        )
        for patch in (
            mock.patch.object(server, "_load_spawn_registry", return_value=[]),
            mock.patch.object(server, "_devin_cli_raw_id_for_pid", return_value=None),
            mock.patch.object(server, "_update_spawn_session_id_in_registry"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        return server

    def _entry(self, spawn_ts, pid):
        return {
            "engine": "devin",
            "pid": pid,
            "cwd": self.CWD,
            "repo_path": self.CWD,
            "command_summary": self.PROMPT,
            "spawned_at": datetime.fromtimestamp(spawn_ts).strftime("%Y%m%dT%H%M%S"),
        }

    def test_identical_prompts_resolve_to_distinct_sessions(self):
        base = 1700000000
        server = self._setup([("early-otter", base + 1), ("later-otter", base + 6)])
        first = self._entry(base, pid=1001)
        second = self._entry(base + 5, pid=1002)
        with mock.patch.object(server, "_spawned_sessions", [first, second]):
            # The later spawn cannot own the session created before it,
            # whichever entry happens to be resolved first.
            self.assertEqual(
                server._devin_cli_session_id_for_spawn_entry(second),
                "devincli-later-otter",
            )
            self.assertEqual(
                server._devin_cli_session_id_for_spawn_entry(first),
                "devincli-early-otter",
            )
            # And once claims are recorded via the shared resolver, the
            # pairing is stable on re-resolution.
            self.assertEqual(server._spawn_session_id_from_entry(first), "devincli-early-otter")
            self.assertEqual(server._spawn_session_id_from_entry(second), "devincli-later-otter")
            self.assertEqual(first["session_id"], "devincli-early-otter")
            self.assertEqual(second["session_id"], "devincli-later-otter")

    def test_same_second_spawns_skip_claimed_session(self):
        """Two spawns in the same second: the second must not reuse the id
        the first already claimed, even though both rows post-date both."""
        base = 1700000000
        server = self._setup([("first-fox", base + 1), ("second-fox", base + 2)])
        first = self._entry(base, pid=2001)
        second = self._entry(base, pid=2002)
        with mock.patch.object(server, "_spawned_sessions", [first, second]):
            self.assertEqual(server._spawn_session_id_from_entry(first), "devincli-first-fox")
            self.assertEqual(server._spawn_session_id_from_entry(second), "devincli-second-fox")

    def test_session_created_before_spawn_is_never_matched(self):
        base = 1700000000
        server = self._setup([("stale-heron", base - 30)])
        entry = self._entry(base, pid=3001)
        with mock.patch.object(server, "_spawned_sessions", [entry]):
            self.assertIsNone(server._devin_cli_session_id_for_spawn_entry(entry))

    def test_session_created_in_spawn_second_still_matches(self):
        """The spawn stamp is floored to the second; a row created in that
        same second (created_at == spawn_ts) is a legitimate match."""
        base = 1700000000
        server = self._setup([("prompt-kestrel", base)])
        entry = self._entry(base, pid=4001)
        with mock.patch.object(server, "_spawned_sessions", [entry]):
            self.assertEqual(
                server._devin_cli_session_id_for_spawn_entry(entry),
                "devincli-prompt-kestrel",
            )

    class _FakeClock:
        """Deterministic stand-in for ``server.time``: ``sleep`` advances a
        virtual monotonic clock instead of blocking, so the cadence test does
        not depend on scheduler jitter (a loaded box turns ``sleep(0.1)`` into
        0.17s and makes any real-time assertion flaky)."""

        def __init__(self):
            self.now = 100.0
            self.sleeps = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(round(seconds, 6))
            self.now += seconds

        def __getattr__(self, name):
            return getattr(time, name)

    def test_wait_for_spawn_session_id_polls_on_tenth_second_tick(self):
        server = importlib.import_module("server")
        import ccc_server.engines as _engines
        clock = self._FakeClock()
        probes = []

        def fake_resolve(entry):
            probes.append(round(clock.now - 100.0, 6))
            return None

        with mock.patch.object(server, "time", clock), \
             mock.patch.object(_engines, "time", clock), \
             mock.patch.object(server, "_spawn_session_id_from_entry", side_effect=fake_resolve):
            self.assertIsNone(server._wait_for_spawn_session_id({"engine": "devin"}, timeout_s=0.45))
        # 0.1s cadence inside a 0.45s budget: probes at 0, .1, .2, .3, .4 and
        # a final one exactly at the deadline, never past it.
        self.assertEqual(probes, [0.0, 0.1, 0.2, 0.3, 0.4, 0.45])
        self.assertEqual(clock.sleeps, [0.1, 0.1, 0.1, 0.1, 0.05])

    def test_wait_for_spawn_session_id_tick_absorbs_resolver_cost(self):
        """A slow resolver (Devin: lock scan + ps fork + DB scan, ~30ms) must
        not stretch the cadence; the sleep shrinks so probes stay 0.1s apart."""
        server = importlib.import_module("server")
        import ccc_server.engines as _engines
        clock = self._FakeClock()
        probes = []

        def slow_resolve(entry):
            probes.append(round(clock.now - 100.0, 6))
            clock.now += 0.03
            return "devincli-found" if len(probes) == 3 else None

        with mock.patch.object(server, "time", clock), \
             mock.patch.object(_engines, "time", clock), \
             mock.patch.object(server, "_spawn_session_id_from_entry", side_effect=slow_resolve):
            sid = server._wait_for_spawn_session_id({"engine": "devin"}, timeout_s=0.75)
        self.assertEqual(sid, "devincli-found")
        self.assertEqual(probes, [0.0, 0.1, 0.2])
        self.assertEqual(clock.sleeps, [0.07, 0.07])


    def test_devin_inject_always_enqueues(self):
        """Devin CLI follow-ups are persisted to the durable queue first."""
        server = importlib.import_module("server")
        sid = "devincli-queue-test"
        with mock.patch.object(server, "_is_devin_cli_session", return_value=True), \
             mock.patch.object(server, "_is_codex_session", return_value=False), \
             mock.patch.object(server, "_is_kimi_session", return_value=False), \
             mock.patch.object(server, "_is_gemini_session", return_value=False), \
             mock.patch.object(server, "_is_cursor_session", return_value=False), \
             mock.patch.object(server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(server, "_is_hermes_session", return_value=False), \
             mock.patch.object(server, "_is_opencode_session", return_value=False), \
             mock.patch.object(server, "_is_aider_session", return_value=False), \
             mock.patch.object(server, "session_live_status", return_value={
                 "live": False, "status": None, "kind": None,
                 "tty": None, "terminal_app": None,
             }), \
             mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
             mock.patch.object(server, "_save_pending_inputs") as save:
            result = server._inject_text_into_session(sid, "follow up")
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("queued"))
        self.assertEqual(result.get("via"), "devin-resume-queued")
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["follow up"])
        pump.assert_called_once_with(sid)

    def test_devin_pump_lock_prevents_concurrent_pumps(self):
        """Only one Devin pump may run per session at a time."""
        server = importlib.import_module("server")
        sid = "devincli-pump-lock-test"
        lock = server._devin_queue_pump_lock(sid)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            result = server._pump_devin_resume_queue(sid)
        finally:
            lock.release()
        self.assertEqual(result, {"ok": True, "waiting": "already-pumping"})

    def test_devin_delivery_proof_watchdog_removes_on_proof(self):
        """A matching prompt_history row proves delivery and drains the queue."""
        server = importlib.import_module("server")
        import subprocess
        sid = "devincli-proof-test"
        text = "hello devin"
        queue = {sid: [text]}
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", "sleep 2"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            retry_after = {}
            def apply_operation(session_id, operations):
                operation = operations[0]
                removed = None
                if operation.get("action") == "pop_head_if_matching":
                    items = queue.get(session_id) or []
                    if items and items[0] == operation.get("match"):
                        removed = items.pop(0)
                        if not items:
                            queue.pop(session_id, None)
                return {"ok": True, "value": [removed]}

            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(
                     server, "_apply_pending_input_operations",
                     side_effect=apply_operation,
                 ) as apply, \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=1):
                server._start_devin_delivery_proof_watchdog(proc, sid, text, time.time())
                deadline = time.time() + 2
                while time.time() < deadline and queue.get(sid):
                    time.sleep(0.05)
        self.assertIsNone(queue.get(sid))
        self.assertNotIn(sid, retry_after)
        apply.assert_called_once()
        proc.wait(timeout=5)

    def test_devin_delivery_proof_watchdog_requeues_on_failure(self):
        """No prompt_history row before the process exits means requeue."""
        server = importlib.import_module("server")
        import subprocess
        sid = "devincli-fail-proof-test"
        text = "hello devin"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", "exit 1"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            queue = {sid: [text]}
            retry_after = {}
            def apply_operation(session_id, operations):
                operation = operations[0]
                items = queue.setdefault(session_id, [])
                value = operation.get("value")
                changed = not (items and items[0] == value)
                if changed:
                    items.insert(0, value)
                return {"ok": True, "value": [changed]}

            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(
                     server, "_apply_pending_input_operations",
                     side_effect=apply_operation,
                 ) as apply, \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=0), \
                 mock.patch.object(server, "_DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S", 0.05):
                server._start_devin_delivery_proof_watchdog(proc, sid, text, time.time())
                deadline = time.time() + 2
                while time.time() < deadline and sid not in retry_after:
                    time.sleep(0.05)
        self.assertEqual(queue.get(sid), [text])
        self.assertIn(sid, retry_after)
        apply.assert_called_once()
        proc.wait(timeout=5)

    def test_devin_delivery_proof_watchdog_drops_gone_session(self):
        """A resume that fails with 'No session found' is permanent — drop it.

        OPS-922: after Devin's sessions.db lost rows, the watchdog requeued
        undeliverable follow-ups forever (a spawn loop every few minutes per
        dead session). The watchdog must pop the queued message instead.
        """
        server = importlib.import_module("server")
        import subprocess
        sid = "devincli-gone-session-test"
        text = "follow up"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                fh.write("Error: No session found matching 'gone-session-test'\n")
                fh.flush()
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", "exit 1"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            queue = {sid: [text]}
            retry_after = {}
            calls = []

            def apply_operation(session_id, operations):
                operation = operations[0]
                calls.append(operation)
                removed = None
                if operation.get("action") == "pop_head_if_matching":
                    items = queue.get(session_id) or []
                    if items and items[0] == operation.get("match"):
                        removed = items.pop(0)
                        if not items:
                            queue.pop(session_id, None)
                elif operation.get("action") == "insert_front":
                    queue.setdefault(session_id, []).insert(0, operation.get("value"))
                return {"ok": True, "value": [removed]}

            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(
                     server, "_apply_pending_input_operations",
                     side_effect=apply_operation,
                 ), \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=0), \
                 mock.patch.object(server, "_DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S", 0.05):
                server._start_devin_delivery_proof_watchdog(
                    proc, sid, text, time.time(), log_path=log_path,
                )
                deadline = time.time() + 3
                while time.time() < deadline and queue.get(sid):
                    time.sleep(0.05)
        self.assertIsNone(queue.get(sid))
        self.assertNotIn(sid, retry_after)
        self.assertEqual(
            [c.get("action") for c in calls], ["pop_head_if_matching"],
        )
        proc.wait(timeout=5)

    def test_devin_delivery_proof_watchdog_requeues_on_plain_startup_failure(self):
        """A startup failure WITHOUT the gone-session signature still requeues."""
        server = importlib.import_module("server")
        import subprocess
        sid = "devincli-transient-fail-test"
        text = "follow up"
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "resume.log")
            with open(log_path, "w") as fh:
                fh.write("Error: database is locked\n")
                fh.flush()
                proc = subprocess.Popen(
                    ["/bin/sh", "-c", "exit 1"],
                    stdout=fh, stderr=subprocess.STDOUT,
                )
            queue = {sid: [text]}
            retry_after = {}

            def apply_operation(session_id, operations):
                operation = operations[0]
                items = queue.setdefault(session_id, [])
                value = operation.get("value")
                changed = not (items and items[0] == value)
                if changed:
                    items.insert(0, value)
                return {"ok": True, "value": [changed]}

            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(
                     server, "_apply_pending_input_operations",
                     side_effect=apply_operation,
                 ), \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=0), \
                 mock.patch.object(server, "_DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S", 0.05):
                server._start_devin_delivery_proof_watchdog(
                    proc, sid, text, time.time(), log_path=log_path,
                )
                deadline = time.time() + 3
                while time.time() < deadline and sid not in retry_after:
                    time.sleep(0.05)
        self.assertEqual(queue.get(sid), [text])
        self.assertIn(sid, retry_after)
        proc.wait(timeout=5)

    def test_devin_pump_starts_resume_and_leaves_queue_for_proof(self):
        """A successful resume returns 'started' and keeps the message queued."""
        server = importlib.import_module("server")
        sid = "devincli-pump-start-test"
        text = "do the thing"
        queue = {sid: [text]}
        with mock.patch.object(server, "_pending_resume_queue", queue), \
             mock.patch.object(server, "_pending_resume_retry_after", {}), \
             mock.patch.object(server, "_save_pending_inputs") as save, \
             mock.patch.object(server, "_resume_queue_engine_busy", return_value=False), \
             mock.patch.object(server, "resume_session_devin", return_value={
                 "ok": True, "pid": 12345, "via": "devin-resume",
             }) as resume:
            result = server._pump_devin_resume_queue(sid)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("started"))
        resume.assert_called_once_with(sid, text, _delivery_slot="resume")
        # Message stays queued until the proof-of-delivery watchdog removes it.
        self.assertEqual(queue.get(sid), [text])
        save.assert_not_called()


class DevinCliNextHomeTests(unittest.TestCase):
    """The Devin desktop app ("Devin - Next") writes its own sessions.db at
    a sibling path (``cli-next/`` instead of ``cli/``) with an identical
    schema. Discovery must merge both homes, not just the original CLI's.
    """

    SCHEMA = """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            working_directory TEXT,
            backend_type TEXT,
            model TEXT,
            agent_mode TEXT,
            created_at REAL,
            last_activity_at REAL,
            title TEXT,
            main_chain_id TEXT
        );
        CREATE TABLE prompt_history (
            session_id TEXT,
            content TEXT,
            timestamp REAL,
            is_shell INTEGER
        );
    """

    def _make_db(self, path, raw_id, cwd, when):
        con = sqlite3.connect(path)
        con.executescript(self.SCHEMA)
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, '', '', '', ?, ?, NULL, NULL)",
            (raw_id, cwd, when, when),
        )
        con.commit()
        con.close()

    def setUp(self):
        self.server = importlib.import_module("server")
        self.devin_mod = importlib.import_module("ccc_server.devin")
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

        self.primary_db = os.path.join(self.tmpdir, "primary-sessions.db")
        self.next_db = os.path.join(self.tmpdir, "next-sessions.db")
        now = time.time()
        self._make_db(self.primary_db, "orig-cli-session", "/tmp/ccc", now)
        self._make_db(self.next_db, "desktop-app-session", "/tmp/ccc", now - 1)

        env_patch = mock.patch.dict(
            os.environ,
            {"CCC_DEVIN_DB": self.primary_db, "CCC_DEVIN_NEXT_DB": self.next_db},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

        for p in (
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_LIST_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ID_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO_LOADED", False),
            mock.patch.object(
                self.devin_mod,
                "_devin_cli_row_memo_path",
                lambda: self.devin_mod.Path(os.path.join(self.tmpdir, "row_memo.json")),
            ),
            mock.patch.object(
                self.devin_mod, "_DEVIN_CLI_ROW_MEMO_BG", {"pending": {}, "thread": None}
            ),
            mock.patch.object(self.server, "_spawned_sessions", []),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.devin_mod._devin_cli_row_memo_background_join, 10)

    def test_find_devin_cli_conversations_merges_both_homes(self):
        """Sessions from the original cli/ store and the desktop app's
        cli-next/ store both appear in one discovery call."""
        rows = self.server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        ids = {r["id"] for r in rows}
        self.assertIn("devincli-orig-cli-session", ids)
        self.assertIn("devincli-desktop-app-session", ids)
        self.assertEqual(len(rows), 2)

    def test_devin_cli_session_ids_union_across_homes(self):
        """The cached raw-id set spans every home, used for O(1) home lookup."""
        ids = self.devin_mod._devin_cli_session_ids()
        self.assertIn("orig-cli-session", ids)
        self.assertIn("desktop-app-session", ids)

    def test_db_path_for_raw_id_resolves_correct_home(self):
        """A raw id that only exists in cli-next/ resolves to that DB path,
        via cached id-set membership (no fresh DB hit)."""
        self.devin_mod._devin_cli_session_ids()  # warm both homes' id caches
        resolved = self.devin_mod._devin_cli_db_path_for_raw_id("desktop-app-session")
        self.assertEqual(str(resolved), self.next_db)
        resolved_primary = self.devin_mod._devin_cli_db_path_for_raw_id("orig-cli-session")
        self.assertEqual(str(resolved_primary), self.primary_db)

    def test_missing_next_home_still_serves_primary(self):
        """If cli-next/ doesn't exist at all (no desktop app installed),
        discovery still returns the primary home's sessions."""
        os.unlink(self.next_db)
        with mock.patch.object(self.devin_mod, "_DEVIN_CLI_LIST_CACHE", {}), \
             mock.patch.object(self.devin_mod, "_DEVIN_CLI_ID_CACHE", {}):
            rows = self.server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {"devincli-orig-cli-session"})


class DevinCompactorSummaryTests(unittest.TestCase):
    """CCC-1174: Devin's compactor writes a periodic "Request and Intent /
    Current state / ..." context handoff as an ordinary assistant row.
    Those rows must be identified (metadata, never text matching) and
    emitted as a distinct collapsed block kind — and must not masquerade
    as the session's last reply in the sidebar/detail surfaces."""

    SCHEMA = """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            working_directory TEXT,
            backend_type TEXT,
            model TEXT,
            agent_mode TEXT,
            created_at REAL,
            last_activity_at REAL,
            title TEXT,
            main_chain_id TEXT
        );
        CREATE TABLE prompt_history (
            session_id TEXT,
            content TEXT,
            timestamp REAL,
            is_shell INTEGER
        );
        CREATE TABLE message_nodes (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            node_id INTEGER NOT NULL,
            parent_node_id INTEGER,
            chat_message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(session_id, node_id)
        );
        CREATE TABLE tool_call_state (
            session_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_call_json TEXT,
            tool_call_update_json TEXT,
            PRIMARY KEY (session_id, tool_call_id)
        );
    """

    SUMMARY_BODY = (
        "## 1. Request and Intent\n\n### Enduring objective\n\n"
        "The user wants the queue drained.\n\n"
        "## 2. Current state\n\nTwo tickets closed."
    )

    def _msg(self, role, content, **meta):
        return json.dumps({"role": role, "content": content, "metadata": meta})

    def setUp(self):
        self.server = importlib.import_module("server")
        self.devin_mod = importlib.import_module("ccc_server.devin")
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "sessions.db")
        now = int(time.time() * 1000)
        con = sqlite3.connect(self.db_path)
        con.executescript(self.SCHEMA)
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, '', '', '', ?, ?, NULL, NULL)",
            ("sum-test", "/tmp/ccc", now, now),
        )
        rows = [
            ("sum-test", 1, self._msg("user", "drain the queue", is_user_input=True)),
            ("sum-test", 2, self._msg(
                "assistant", "closed two tickets",
                generation_model="swarm-1",
                metrics={"input_tokens": 900, "cache_read_tokens": 100},
            )),
            ("sum-test", 3, self._msg(
                "assistant", self.SUMMARY_BODY,
                generation_model="compactor",
                metrics={"input_tokens": 60000, "output_tokens": 5000},
            )),
        ]
        con.executemany(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(s, n, m, now + n) for s, n, m in rows],
        )
        con.commit()
        con.close()

        env_patch = mock.patch.dict(
            os.environ,
            {
                "CCC_DEVIN_DB": self.db_path,
                "CCC_DEVIN_NEXT_DB": os.path.join(self.tmpdir, "next-sessions.db"),
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for p in (
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_PARSE_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_LIST_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ID_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO_LOADED", False),
            mock.patch.object(
                self.devin_mod,
                "_devin_cli_row_memo_path",
                lambda: self.devin_mod.Path(os.path.join(self.tmpdir, "row_memo.json")),
            ),
            mock.patch.object(
                self.devin_mod, "_DEVIN_CLI_ROW_MEMO_BG", {"pending": {}, "thread": None}
            ),
            mock.patch.object(self.server, "_spawned_sessions", []),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.devin_mod._devin_cli_row_memo_background_join, 10)

    def test_is_context_summary_uses_metadata_not_text(self):
        m = self.devin_mod
        self.assertTrue(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": "x",
             "metadata": {"generation_model": "compactor"}}))
        self.assertTrue(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": "x",
             "metadata": {"extensions": {"devin-rs/summary": {"source": "file_compactor"}}}}))
        # A real reply that merely QUOTES the summary heading is not one.
        self.assertFalse(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": self.SUMMARY_BODY,
             "metadata": {"generation_model": "swarm-1"}}))
        self.assertFalse(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": self.SUMMARY_BODY}))
        self.assertFalse(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": "x", "metadata": "junk"}))
        self.assertFalse(m._devin_cli_is_context_summary(
            {"role": "assistant", "content": "x", "metadata": None}))

    def test_parse_marks_summary_blocks_devin_summary(self):
        parsed = self.server._parse_devin_cli_conversation("devincli-sum-test")
        events = parsed["events"]
        kinds = [
            b["kind"]
            for e in events if e.get("type") == "assistant"
            for b in e.get("blocks", [])
        ]
        self.assertEqual(kinds, ["text", "devin_summary"])
        self.assertEqual(events[0]["type"], "user_text")
        summary_ev = events[-1]
        self.assertEqual(summary_ev["blocks"][0]["text"], self.SUMMARY_BODY)

    def test_sidebar_tail_walk_skips_summary(self):
        rows = {r["id"]: r for r in self.server.find_devin_cli_conversations(
            "/tmp/ccc", include_old=True)}
        row = rows["devincli-sum-test"]
        self.assertEqual(row["last_assistant_text"], "closed two tickets")
        self.assertEqual(row["model"], "swarm-1")

    def test_session_detail_skips_summary_for_last_text(self):
        detail, status = self.devin_mod._devin_cli_session_detail("devincli-sum-test")
        self.assertEqual(status, 200)
        self.assertEqual(detail["last_assistant_text"], "closed two tickets")

    def test_usage_model_is_not_compactor(self):
        usage = self.devin_mod._extract_devin_cli_usage("devincli-sum-test")
        self.assertEqual(usage["model"], "swarm-1")
        # The compactor's tokens are still real spend — totals include them.
        self.assertEqual(usage["total_output_tokens"], 5000)


class DevinCliFusionLaneTests(unittest.TestCase):
    """Fusion sessions keep the lead and the sidekick in one message_nodes
    forest. The parser must emit tool calls and tag each assistant turn
    with the actor (lead vs sidekick) resolved from the per-message
    response_dimensions model label against the sessions.model slug."""

    SCHEMA = DevinCompactorSummaryTests.SCHEMA

    def _msg(self, role, content, message_id=None, **kw):
        msg = {"role": role, "content": content}
        if message_id:
            msg["message_id"] = message_id
        msg.update(kw)
        return json.dumps(msg)

    def _dims(self, model_label):
        return [{"uid": "model", "kind": {"Metric": {"value": model_label}}}]

    def setUp(self):
        self.server = importlib.import_module("server")
        self.devin_mod = importlib.import_module("ccc_server.devin")
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "sessions.db")
        now = int(time.time() * 1000)
        con = sqlite3.connect(self.db_path)
        con.executescript(self.SCHEMA)
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, '', ?, '', ?, ?, NULL, NULL)",
            ("fus-test", "/tmp/ccc",
             "fusion-claude-fable-5-1-medium-sidekick-swe-2-medium", now, now),
        )
        lead_meta = {"response_dimensions": self._dims("Claude Fable 5.1 Medium"),
                     "metrics": {"input_tokens": 10, "output_tokens": 5,
                                 "cache_read_tokens": 100}}
        kick_meta = {"response_dimensions": self._dims("SWE-2 Medium")}
        rows = [
            (1, None, self._msg("user", "sync the repos",
                                message_id="u1",
                                metadata={"is_user_input": True})),
            (2, 1, self._msg(
                "assistant", "Handing off.",
                message_id="a1",
                tool_calls=[{
                    "id": "toolu_sk1", "name": "sidekick", "kind": "function",
                    "arguments": {"message": "update auto_pull_repos.sh"}},
                ], metadata=lead_meta)),
            (3, 2, self._msg("tool", "Sidekick finished the handoff.",
                             message_id="t1", tool_call_id="toolu_sk1",
                             metadata={"extensions": {
                                 "chisel/tool_result_meta": {"success": True}}})),
            # Sidekick tree: separate root (parent_node_id NULL).
            (4, None, self._msg("system", "You are the Sidekick subagent of Devin,",
                                message_id="sk-sys")),
            (5, 4, self._msg(
                "assistant", "",
                message_id="sk-a1",
                tool_calls=[{
                    "id": "call_exec1", "name": "exec", "kind": "function",
                    "arguments": {"command": "git status --short"},
                }, {
                    "id": "call_ed1", "name": "edit", "kind": "function",
                    "arguments": {"file_path": "/tmp/x.sh",
                                  "old_string": "a", "new_string": "b"},
                }], metadata=kick_meta)),
            (6, 5, self._msg("tool", "M x.sh",
                             message_id="sk-t1", tool_call_id="call_exec1",
                             metadata={"extensions": {
                                 "chisel/tool_result_meta": {"success": True}}})),
            (7, 5, self._msg("tool", "boom: no such file",
                             message_id="sk-t2", tool_call_id="call_ed1",
                             metadata={"extensions": {
                                 "chisel/tool_result_meta": {"success": False}}})),
            (8, 5, self._msg("assistant", "Sidekick done.",
                             message_id="sk-a2", metadata=kick_meta)),
            (9, 3, self._msg(
                "system",
                "<subagent_completion_notification>\n[Background subagent "
                "with agent_id=sidekick completed]\n\nCommitted as abc123.",
                message_id="note1")),
            (10, 3, self._msg("assistant", "All synced.",
                              message_id="a2", metadata=lead_meta)),
            # The sidekick's handoff brief lands as user input in its tree.
            (11, 4, self._msg(
                "user",
                "This is your first handoff from the lead. Work it to "
                "completion.\n\n<lead_handoff>\nTwo final items.\n\n"
                "## A. Update the status script",
                message_id="sk-u1",
                metadata={"is_user_input": True})),
        ]
        con.executemany(
            "INSERT INTO message_nodes "
            "(session_id, node_id, parent_node_id, chat_message, created_at) "
            "VALUES ('fus-test', ?, ?, ?, ?)",
            [(n, p, m, now + n) for n, p, m in rows],
        )
        con.execute(
            "INSERT INTO tool_call_state VALUES ('fus-test', 'call_exec1', ?, NULL)",
            (json.dumps({"toolCallId": "call_exec1",
                         "rawInput": {"command": "git status --short"}}),),
        )
        con.commit()
        con.close()

        env_patch = mock.patch.dict(
            os.environ,
            {
                "CCC_DEVIN_DB": self.db_path,
                "CCC_DEVIN_NEXT_DB": os.path.join(self.tmpdir, "next-sessions.db"),
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for p in (
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_PARSE_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_LIST_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ID_CACHE", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO", {}),
            mock.patch.object(self.devin_mod, "_DEVIN_CLI_ROW_MEMO_LOADED", False),
            mock.patch.object(
                self.devin_mod,
                "_devin_cli_row_memo_path",
                lambda: self.devin_mod.Path(os.path.join(self.tmpdir, "row_memo.json")),
            ),
            mock.patch.object(
                self.devin_mod, "_DEVIN_CLI_ROW_MEMO_BG", {"pending": {}, "thread": None}
            ),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.devin_mod._devin_cli_row_memo_background_join, 10)

    def test_fusion_parse_tags_actors_and_tools(self):
        parsed = self.server._parse_devin_cli_conversation("devincli-fus-test")
        events = parsed["events"]
        types = [e["type"] for e in events]
        self.assertEqual(
            types,
            ["user_text", "assistant", "tool_result", "assistant",
             "tool_result", "tool_result", "assistant", "assistant",
             "assistant", "user_text"],
        )
        by_mid = {e.get("message_id"): e for e in events if e.get("type") == "assistant"}
        lead = by_mid["a1"]
        self.assertEqual(lead["actor"], "lead")
        self.assertEqual(lead["model"], "Claude Fable 5.1 Medium")
        self.assertEqual(lead["tokens_in"], 110)
        self.assertEqual(lead["tokens_cached"], 100)
        sk_call = lead["blocks"][-1]
        self.assertEqual(sk_call["kind"], "tool_use")
        self.assertEqual(sk_call["name"], "sidekick")
        self.assertIn("auto_pull_repos", sk_call["detail"])
        self.assertTrue(sk_call["has_input"])

        kick = by_mid["sk-a1"]
        self.assertEqual(kick["actor"], "sidekick")
        self.assertEqual(kick["model"], "SWE-2 Medium")
        kinds = [b["kind"] for b in kick["blocks"]]
        self.assertEqual(kinds, ["tool_use", "tool_use"])
        names = [b["name"] for b in kick["blocks"]]
        self.assertEqual(names, ["exec", "edit"])
        self.assertEqual(kick["blocks"][0]["detail"], "git status --short")
        self.assertEqual(kick["blocks"][1]["edit_input"]["new_string"], "b")

        report = by_mid["note1"]
        self.assertEqual(report["actor"], "sidekick")
        self.assertTrue(report["subagent_report"])
        self.assertIn("Committed as abc123.", report["blocks"][0]["text"])

        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual(
            [(r["tool_use_id"], r["is_error"]) for r in results],
            [("toolu_sk1", False), ("call_exec1", False), ("call_ed1", True)],
        )

        handoff = events[-1]
        self.assertEqual(handoff["type"], "user_text")
        self.assertTrue(handoff["handoff"])
        self.assertEqual(handoff["handoff_title"], "Two final items.")
        self.assertEqual(handoff["actor"], "sidekick")

    def test_non_fusion_session_has_no_actor(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO sessions VALUES ('plain', '/tmp/ccc', '', "
            "'swe-2-medium', '', 0, 0, NULL, NULL)")
        con.execute(
            "INSERT INTO message_nodes "
            "(session_id, node_id, chat_message, created_at) "
            "VALUES ('plain', 1, ?, 0)",
            (self._msg("assistant", "hi", message_id="p1",
                       metadata={"response_dimensions": self._dims("SWE-2 Medium")}),),
        )
        con.commit()
        con.close()
        parsed = self.server._parse_devin_cli_conversation("devincli-plain")
        ev = parsed["events"][0]
        self.assertNotIn("actor", ev)
        self.assertNotIn("model", ev)

    def test_tool_input_endpoint_reads_tool_call_state(self):
        payload = self.server._conversation_tool_input(
            "devincli-fus-test", "1", "call_exec1")
        self.assertIn("git status --short", payload)


if __name__ == "__main__":
    unittest.main()
