"""Model reply suggestions stay bounded, text-only and failure-tolerant."""

import importlib
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock


class QuickActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        importlib.import_module("server")
        cls.actions = importlib.import_module("ccc_server.quick_actions")

    def setUp(self):
        self.actions._quick_action_cache.clear()
        self.cli = mock.patch.object(self.actions._core, "_resolve_claude_bin",
                                     return_value={"available": True, "bin": "claude"})
        self.cli.start()
        self.addCleanup(self.cli.stop)
        self.payload = {"next_step": "Say yes to a dashboard and worker restart, or run it yourself.",
                        "context": "The running processes have not picked up the new code."}

    def test_model_chooses_label_and_scoped_reply_and_caches_it(self):
        action = {"label": "Restart", "reply": "Yes, restart the dashboard and worker."}
        with mock.patch.object(self.actions.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(action))) as run:
            self.assertEqual(self.actions.suggest_session_quick_action(self.payload), {"ok": True, "action": action})
            self.assertEqual(self.actions.suggest_session_quick_action(self.payload)["action"], action)
        self.assertEqual(run.call_count, 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn('"mcpServers":{}', argv[argv.index("--strict-mcp-config") + 1])
        self.assertIn(self.payload["next_step"], argv[-1])
        self.assertIn("Never choose between alternatives", argv[-1])

    def test_abstention_is_cached_but_changed_request_is_recomputed(self):
        with mock.patch.object(self.actions.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="null")) as run:
            for _ in range(2):
                self.assertEqual(self.actions.suggest_session_quick_action(self.payload), {"ok": True, "action": None})
            self.actions.suggest_session_quick_action(dict(self.payload, next_step="Choose one."))
        self.assertEqual(run.call_count, 2)

    def test_provider_json_fences_and_disable_switch(self):
        with mock.patch.object(self.actions.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout='```json\n{"label":"Restart","reply":"Yes, restart both."}\n```')):
            self.assertEqual(self.actions.suggest_session_quick_action(self.payload)["action"]["label"], "Restart")
        with mock.patch.dict(self.actions.os.environ, {"CCC_QUICK_ACTIONS": "0"}), \
                mock.patch.object(self.actions.subprocess, "run") as run:
            self.assertEqual(self.actions.suggest_session_quick_action(self.payload), {"ok": True, "action": None})
            run.assert_not_called()

    def test_invalid_model_output_is_not_an_action(self):
        for output in ["not json", '"Restart"', '{"label":"Restart all","reply":"yes"}',
                       '{"label":"<script>","reply":"yes"}', '{"label":"Restart","reply":""}',
                       '{"label":"Restart","reply":"line\\nbreak"}']:
            with self.subTest(output=output), mock.patch.object(self.actions.subprocess, "run",
                    return_value=SimpleNamespace(returncode=0, stdout=output)):
                self.assertFalse(self.actions.suggest_session_quick_action(self.payload)["ok"])

    def test_failure_and_busy_slots_do_not_spawn_unbounded_work(self):
        with mock.patch.object(self.actions.subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 30)):
            self.assertFalse(self.actions.suggest_session_quick_action(self.payload)["ok"])
        self.actions._quick_action_slots.acquire()
        self.actions._quick_action_slots.acquire()
        try:
            with mock.patch.object(self.actions.subprocess, "run") as run:
                self.assertFalse(self.actions.suggest_session_quick_action(self.payload)["ok"])
                run.assert_not_called()
        finally:
            self.actions._quick_action_slots.release()
            self.actions._quick_action_slots.release()

    def test_oversized_or_invalid_requests_never_reach_model(self):
        for payload in [[], {}, {"next_step": 1}, {"next_step": "x" * 2001},
                        dict(self.payload, context="x" * 4001)]:
            with mock.patch.object(self.actions.subprocess, "run") as run:
                self.assertFalse(self.actions.suggest_session_quick_action(payload)["ok"])
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
