"""Unattended usage-limit auto-resume must not start a model turn.

CCC-863 used to inject ``continue`` (or spawn a continuation session) when a
usage/rate-limit wall lifted. Combined with Codex re-queueing an accepted but
unconfirmed turn, that became an overnight token incinerator. Auto-send is
killed; these tests drive the real scan and resume entry points.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


class UsageLimitAutoResumeDisabledTests(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-usage-limit-")
        self.server.USAGE_LIMIT_RESUME_FILE = Path(self.tmp_dir) / "usage_limit_resumes.json"
        self.server.PENDING_INPUTS_FILE = Path(self.tmp_dir) / "pending-inputs.json"
        with self.server._usage_limit_resume_lock:
            self.server._usage_limit_resume_cache["data"] = None
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def _due_entry(self, sid, *, now, context_tokens=0):
        return {
            "engine": "codex",
            "detected_at": now - 60,
            "resume_at": now - 1,
            "resume_at_estimated": True,
            "source_text_snippet": "You've hit your usage limit. try again at 8:33 PM.",
            "context_tokens": context_tokens,
            "model": "gpt-5.6-terra",
            "effort": "high",
            "cwd": self.tmp_dir,
            "display_name": "Drain a queue",
        }

    def _scan_with_due_stop(self, *, context_tokens=0, extra_patches=None):
        now = time.time()
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.server._save_usage_limit_resume_entry(sid, self._due_entry(sid, now=now, context_tokens=context_tokens))
        inject = mock.Mock(return_value={"ok": True})
        spawn = mock.Mock(return_value={"ok": True, "session_id": "new-sid"})
        patches = [
            mock.patch.object(self.server, "_usage_limit_kimi_candidates", return_value=[]),
            mock.patch.object(self.server, "_usage_limit_codex_candidates", return_value=[]),
            mock.patch.object(self.server, "_usage_limit_claude_candidates", return_value=[]),
            mock.patch.object(self.server, "_usage_limit_session_path", return_value=None),
            mock.patch.object(self.server, "_inject_text_into_session", inject),
            mock.patch.object(self.server, "_usage_limit_spawn_continuation", spawn),
            mock.patch.object(self.server, "_log_activity"),
        ]
        extra_patches = extra_patches or []
        patches.extend(extra_patches)
        for p in patches:
            p.start()
        try:
            self.server._usage_limit_scan_once(now=now)
        finally:
            for p in reversed(patches):
                p.stop()
        return sid, inject, spawn

    def test_due_usage_limit_scan_does_not_inject_or_spawn(self):
        """A tracked, already-due stop must not start a new model turn."""
        sid, inject, spawn = self._scan_with_due_stop()
        inject.assert_not_called()
        spawn.assert_not_called()
        entry = self.server._load_usage_limit_resumes().get(sid) or {}
        self.assertTrue(
            entry.get("fired") or entry.get("dismissed"),
            "due stop must become inert so a later scan cannot send either",
        )

    def test_due_large_context_stop_does_not_spawn_continuation(self):
        """The old 120k path spawned a new session unattended; that must not fire."""
        _sid, inject, spawn = self._scan_with_due_stop(context_tokens=200_000)
        inject.assert_not_called()
        spawn.assert_not_called()

    def test_durable_due_entry_after_fresh_load_still_does_not_send(self):
        """Overnight restart: a due unfired file entry must not inject/spawn."""
        now = time.time()
        sid = "ffffffff-1111-2222-3333-444444444444"
        payload = {sid: self._due_entry(sid, now=now)}
        self.server.USAGE_LIMIT_RESUME_FILE.write_text(json.dumps(payload, indent=2))
        with self.server._usage_limit_resume_lock:
            self.server._usage_limit_resume_cache["data"] = None
        inject = mock.Mock(return_value={"ok": True})
        spawn = mock.Mock(return_value={"ok": True, "session_id": "new-sid"})
        with mock.patch.object(self.server, "_usage_limit_kimi_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_codex_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_claude_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_session_path", return_value=None), \
             mock.patch.object(self.server, "_inject_text_into_session", inject), \
             mock.patch.object(self.server, "_usage_limit_spawn_continuation", spawn), \
             mock.patch.object(self.server, "_log_activity"):
            self.server._usage_limit_scan_once(now=now)
        inject.assert_not_called()
        spawn.assert_not_called()


class CodexUnconfirmedResumeDoesNotRequeueTests(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-codex-requeue-")
        self.server.PENDING_INPUTS_FILE = Path(self.tmp_dir) / "pending-inputs.json"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def _resume(self, text, *, accepted=True, confirmed=False, extra=None):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        app_result = {
            "ok": True,
            "accepted": accepted,
            "confirmed": confirmed,
            "via": "codex-app-turn",
            "warning": "turn accepted but no app-server events observed",
        }
        if extra:
            app_result.update(extra)
        with mock.patch.object(
            self.server,
            "_control_plane_engine_call",
            return_value=None,
        ), mock.patch.object(
            self.server,
            "_resolve_codex_bin",
            return_value={"available": True, "bin": "/usr/bin/codex-test"},
        ), mock.patch.object(
            self.server, "_codex_thread_row", return_value={"cwd": self.tmp_dir},
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=self.tmp_dir,
        ), mock.patch.object(
            self.server,
            "_codex_resume_or_steer_via_app_server",
            return_value=app_result,
        ), mock.patch.object(self.server, "_schedule_codex_queue_pump") as schedule:
            result = self.server.resume_session_codex(sid, text)
        return sid, result, schedule

    def test_accepted_unconfirmed_continue_does_not_requeue(self):
        sid, result, schedule = self._resume("continue", accepted=True, confirmed=False)
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("accepted"))
        self.assertNotEqual(result.get("queued"), True)
        with self.server._pending_resume_lock:
            self.assertNotIn("continue", self.server._pending_resume_queue.get(sid) or [])
        schedule.assert_not_called()

    def test_accepted_unconfirmed_user_text_does_not_requeue_duplicate(self):
        """Any already-accepted turn must not enqueue a second copy of that text."""
        sid, result, _schedule = self._resume(
            "must become visible", accepted=True, confirmed=False,
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("accepted"))
        self.assertNotEqual(result.get("queued"), True)
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue.get(sid), None)

    def test_not_accepted_user_follow_up_still_queues(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(
            self.server, "_control_plane_engine_call", return_value=None,
        ), mock.patch.object(
            self.server,
            "_resolve_codex_bin",
            return_value={"available": True, "bin": "/usr/bin/codex-test"},
        ), mock.patch.object(
            self.server, "_codex_thread_row", return_value={"cwd": self.tmp_dir},
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=self.tmp_dir,
        ), mock.patch.object(
            self.server,
            "_codex_resume_or_steer_via_app_server",
            return_value={
                "ok": False,
                "fallback": "queue",
                "error": "Codex thread is still active",
            },
        ), mock.patch.object(self.server, "_schedule_codex_queue_pump"):
            result = self.server.resume_session_codex(sid, "please keep going")
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("queued"))
        with self.server._pending_resume_lock:
            self.assertEqual(
                self.server._pending_resume_queue.get(sid),
                ["please keep going"],
            )

    def test_durable_queued_auto_continue_is_inert_on_load_and_pump(self):
        """A restart that reloads pending-inputs.json must not send auto-continue."""
        sid = "01a01beb-c020-7733-877b-c12173b133a4"
        self.server.PENDING_INPUTS_FILE.write_text(json.dumps({
            "resume_queue": {sid: ["continue"], "keep-me": ["please keep going"]},
            "terminal_queue": {},
        }, indent=2))
        self.server._load_pending_inputs()
        with self.server._pending_resume_lock:
            loaded = dict(self.server._pending_resume_queue)
        self.assertNotIn("continue", loaded.get(sid) or [])
        self.assertEqual(loaded.get("keep-me"), ["please keep going"])

        resume = mock.Mock(return_value={"ok": True, "accepted": True, "confirmed": True})
        with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
             mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=False), \
             mock.patch.object(self.server, "resume_session_codex", resume):
            self.server._pump_codex_resume_queue(sid)
        for call in resume.call_args_list:
            args = call.args if call.args else ()
            kwargs = call.kwargs
            sent = args[1] if len(args) > 1 else kwargs.get("text")
            self.assertNotEqual(sent, "continue")


if __name__ == "__main__":
    unittest.main()
