"""Regression coverage for Devin CLI message queue drain."""

import importlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock


class DevinQueueTests(unittest.TestCase):
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
        self.assertTrue(result.get("queued"))
        pump.assert_called_once_with(sid)
        save.assert_called_once()
        resume.assert_not_called()
        cp.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["follow up"])

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
        clock = self._FakeClock()
        probes = []

        def fake_resolve(entry):
            probes.append(round(clock.now - 100.0, 6))
            return None

        with mock.patch.object(server, "time", clock), \
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
        clock = self._FakeClock()
        probes = []

        def slow_resolve(entry):
            probes.append(round(clock.now - 100.0, 6))
            clock.now += 0.03
            return "devincli-found" if len(probes) == 3 else None

        with mock.patch.object(server, "time", clock), \
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
        save.assert_called_once()

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
            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(server, "_save_pending_inputs") as save, \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=1):
                server._start_devin_delivery_proof_watchdog(proc, sid, text, time.time())
                deadline = time.time() + 2
                while time.time() < deadline and queue.get(sid):
                    time.sleep(0.05)
        self.assertIsNone(queue.get(sid))
        self.assertNotIn(sid, retry_after)
        save.assert_called_once()
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
            with mock.patch.object(server, "_pending_resume_queue", queue), \
                 mock.patch.object(server, "_pending_resume_retry_after", retry_after), \
                 mock.patch.object(server, "_save_pending_inputs") as save, \
                 mock.patch.object(server, "_devin_cli_prompt_history_count", return_value=0), \
                 mock.patch.object(server, "_DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S", 0.05):
                server._start_devin_delivery_proof_watchdog(proc, sid, text, time.time())
                deadline = time.time() + 2
                while time.time() < deadline and sid not in retry_after:
                    time.sleep(0.05)
        self.assertEqual(queue.get(sid), [text])
        self.assertIn(sid, retry_after)
        save.assert_called_once()
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
        resume.assert_called_once_with(sid, text)
        # Message stays queued until the proof-of-delivery watchdog removes it.
        self.assertEqual(queue.get(sid), [text])
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
