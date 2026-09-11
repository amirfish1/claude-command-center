"""Unattended "continue" pokes into a Codex session must be opt-in.

CCC-863's usage-limit auto-resume (already fully disabled, see
tests/test_usage_limit_auto_resume.py and commit 1112c68e) used to call
_inject_text_into_session(sid, "continue", ...) unconditionally for any
tracked session -- opt-out by default. A 5-day-old leaked process running
pre-fix code exploited exactly that default, injecting "continue" into a
live Codex WatchTower-worker session 118 times.

These tests prove the shared Codex resume-queue chokepoint (_queue_codex_resume,
called from every resume_session_codex path that would otherwise queue text
for later delivery) now refuses to enqueue the literal unattended-continue
marker unless the target session has an explicit, durable opt-in flag --
default is NOT opted in. Real user text (anything other than the bare
"continue" marker) is never gated.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


class QueueCodexResumeOptInTests(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-auto-resume-optin-")
        self.server.PENDING_INPUTS_FILE = Path(self.tmp_dir) / "pending-inputs.json"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._auto_resume_opt_in_lock:
            self.server._auto_resume_opt_in.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._auto_resume_opt_in_lock:
            self.server._auto_resume_opt_in.clear()

    def test_continue_not_enqueued_without_opt_in(self):
        sid = "no-opt-in-session"
        with mock.patch.object(self.server, "_schedule_codex_queue_pump"):
            result = self.server._queue_codex_resume(sid, "continue")
        self.assertFalse(result.get("ok"))
        with self.server._pending_resume_lock:
            self.assertNotIn("continue", self.server._pending_resume_queue.get(sid) or [])

    def test_continue_enqueued_once_session_opted_in(self):
        sid = "opted-in-session"
        self.server._set_auto_resume_opt_in(sid, True)
        with mock.patch.object(self.server, "_schedule_codex_queue_pump"):
            result = self.server._queue_codex_resume(sid, "continue")
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("queued"))
        with self.server._pending_resume_lock:
            self.assertIn("continue", self.server._pending_resume_queue.get(sid) or [])

    def test_real_user_text_is_never_gated_by_opt_in(self):
        """Only the bare unattended-continue marker is gated -- a genuine
        multi-word follow-up must queue exactly like before, opted in or not."""
        sid = "no-opt-in-session-real-text"
        with mock.patch.object(self.server, "_schedule_codex_queue_pump"):
            result = self.server._queue_codex_resume(sid, "please keep going")
        self.assertTrue(result.get("ok"))
        with self.server._pending_resume_lock:
            self.assertEqual(
                self.server._pending_resume_queue.get(sid), ["please keep going"],
            )

    def test_opt_in_flag_survives_a_reload(self):
        """The flag is durable: a fresh process load must still see it."""
        sid = "durable-opt-in-session"
        self.server._set_auto_resume_opt_in(sid, True)
        reloaded = _fresh_server()
        reloaded.PENDING_INPUTS_FILE = self.server.PENDING_INPUTS_FILE
        reloaded._load_pending_inputs()
        self.assertTrue(reloaded._is_auto_resume_opted_in(sid))

    def test_stale_loader_snapshot_does_not_erase_new_in_memory_opt_in(self):
        """A watcher read begun before spawn cannot erase the new permission."""
        sid = "new-opt-in-after-stale-read"
        self.server.PENDING_INPUTS_FILE.write_text(
            '{"resume_queue": {}, "terminal_queue": {}, "auto_resume_opt_in": {}}'
        )
        with self.server._auto_resume_opt_in_lock:
            self.server._auto_resume_opt_in[sid] = True

        self.server._load_pending_inputs()

        self.assertTrue(self.server._is_auto_resume_opted_in(sid))


class SpawnAutoResumeOptInWiringTests(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-auto-resume-optin-spawn-")
        self.server.PENDING_INPUTS_FILE = Path(self.tmp_dir) / "pending-inputs.json"
        with self.server._auto_resume_opt_in_lock:
            self.server._auto_resume_opt_in.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._auto_resume_opt_in_lock:
            self.server._auto_resume_opt_in.clear()

    def test_spawn_payload_auto_resume_true_sets_flag_on_new_session(self):
        payload = {"auto_resume": True}
        result = {"ok": True, "session_id": "freshly-spawned-sid"}
        self.server._apply_spawn_auto_resume_opt_in(payload, result)
        self.assertTrue(self.server._is_auto_resume_opted_in("freshly-spawned-sid"))

    def test_spawn_without_auto_resume_field_leaves_flag_unset(self):
        payload = {}
        result = {"ok": True, "session_id": "plain-spawned-sid"}
        self.server._apply_spawn_auto_resume_opt_in(payload, result)
        self.assertFalse(self.server._is_auto_resume_opted_in("plain-spawned-sid"))

    def test_failed_spawn_never_sets_flag_even_if_requested(self):
        payload = {"auto_resume": True}
        result = {"ok": False, "error": "boom"}
        self.server._apply_spawn_auto_resume_opt_in(payload, result)
        self.assertFalse(self.server._is_auto_resume_opted_in(""))
        with self.server._auto_resume_opt_in_lock:
            self.assertEqual(self.server._auto_resume_opt_in, {})


if __name__ == "__main__":
    unittest.main()
