"""A cold headless resume must carry the autocompact window.

Spawns set CLAUDE_CODE_AUTO_COMPACT_WINDOW, but a cold `claude -p --resume`
is a new process and used to get no window at all, silently falling back to
Claude Code's own default. Resume runs inside the worker process, so the test
also pins that the user-facing default is used, not the WatchTower-worker one.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_FRESH_MODULES = ("server", "morning", "morning_store")
_ORIGINAL_MODULES = {m: sys.modules[m] for m in _FRESH_MODULES if m in sys.modules}


def tearDownModule():
    for mod in _FRESH_MODULES:
        sys.modules.pop(mod, None)
    sys.modules.update(_ORIGINAL_MODULES)
    original = _ORIGINAL_MODULES.get("server")
    if original is not None:
        importlib.reload(original)


class ResumeAutoCompactTests(unittest.TestCase):
    def setUp(self):
        for mod in _FRESH_MODULES:
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-resume-compact-")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _cold_resume_env(self, defaults):
        from ccc_server import engines, free_runtime

        captured = {}

        def capture_spawn(*args, **kwargs):
            captured.update(kwargs["env"])
            raise OSError("stop at the process boundary")

        s = self.server
        with mock.patch.dict(os.environ, {"CCC_WORKER_PROCESS": "1"}), \
             mock.patch.object(s, "_load_spawn_defaults", return_value=defaults), \
             mock.patch.object(s, "_claude_subagent_parent_session_id", return_value=None), \
             mock.patch.object(s, "_control_plane_engine_call", return_value=None), \
             mock.patch.object(s, "_free_failover_session_env", return_value=None), \
             mock.patch.object(free_runtime, "session_runtime", return_value=""), \
             mock.patch.object(s, "_resolve_cwd_context", return_value={"cwd": self.tmp_dir, "repo_path": self.tmp_dir}), \
             mock.patch.object(s, "_ensure_session_jsonl_for_cwd", return_value={"ok": True}), \
             mock.patch.object(s, "repo_log_dir", return_value=Path(self.tmp_dir)), \
             mock.patch.object(s, "_resolve_claude_bin", return_value={"available": True, "bin": "claude"}), \
             mock.patch.object(s, "_claude_session_state_args", return_value=[]), \
             mock.patch.object(s, "_claude_peer_inbound_args", return_value=[]), \
             mock.patch.object(s, "_get_session_override", return_value=None), \
             mock.patch.object(s, "_resume_ledger_append"), \
             mock.patch.object(s, "_make_stdin_fifo", return_value=(None, None)), \
             mock.patch.object(engines.subprocess, "Popen", side_effect=capture_spawn):
            result = s.resume_session_headless(
                "dddddddd-1111-2222-3333-444444444444", "continue", cwd=self.tmp_dir)
        self.assertFalse(result["ok"])
        return captured

    def test_cold_resume_sets_user_autocompact_window(self):
        env = self._cold_resume_env({"auto_compact_k": 400, "worker_auto_compact_k": 150})
        self.assertEqual(env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "400000")
        self.assertNotIn("CCC_WORKER_PROCESS", env)

    def test_changed_setting_applies_on_next_cold_resume(self):
        first = self._cold_resume_env({"auto_compact_k": 250})
        second = self._cold_resume_env({"auto_compact_k": 600})
        self.assertEqual(first.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "250000")
        self.assertEqual(second.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), "600000")


if __name__ == "__main__":
    unittest.main()
