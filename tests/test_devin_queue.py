"""Regression coverage for Devin CLI message queue drain."""

import importlib
import os
import sqlite3
import tempfile
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
             mock.patch.object(server, "resume_session_devin", return_value={
                 "ok": True, "resumed": True, "via": "devin-resume",
             }) as resume, \
             mock.patch.object(server, "_control_plane_engine_call") as cp:
            result = server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "devin-resume")
        resume.assert_called_once_with(sid, "follow up")
        cp.assert_not_called()

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
             mock.patch.object(devin_mod, "_DEVIN_CLI_LIST_CACHE", {"key": None, "rows": None}):
            rows = server.find_devin_cli_conversations("/tmp/ccc", include_old=True)
        match = [r for r in rows if r.get("id") == "devincli-mighty-outfit"]
        self.assertTrue(match)
        self.assertEqual(match[0].get("spawn_pid"), 67663)


if __name__ == "__main__":
    unittest.main()
