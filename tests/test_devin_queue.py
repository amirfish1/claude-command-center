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


if __name__ == "__main__":
    unittest.main()
