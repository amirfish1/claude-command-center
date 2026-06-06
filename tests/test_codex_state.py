import importlib
import sys
import unittest


class TestCodexRowState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def test_empty_tail_returns_none(self):
        self.assertIsNone(self.server._codex_row_state({}, 100.0, 100.0, True, False))

    def test_offline_when_pool_dead_and_no_live_proc(self):
        tail = {"last_event_type": "assistant"}
        self.assertEqual(
            self.server._codex_row_state(tail, 100.0, 100.0, False, False),
            "offline",
        )

    def test_live_proc_overrides_dead_pool(self):
        tail = {"pending_tool": "shell"}
        self.assertEqual(
            self.server._codex_row_state(tail, 100.0, 100.0, False, True),
            "working",
        )

    def test_working_when_mid_turn_and_fresh(self):
        tail = {"pending_tool": "shell"}
        self.assertEqual(
            self.server._codex_row_state(tail, 1000.0, 1010.0, True, False),
            "working",
        )

    def test_working_via_assistant_tail(self):
        tail = {"last_event_type": "user"}
        self.assertEqual(
            self.server._codex_row_state(tail, 1000.0, 1010.0, True, False),
            "working",
        )

    def test_stuck_when_mid_turn_and_past_stale_threshold(self):
        tail = {"pending_tool": "shell"}
        # age = 1000s > default 900s stale threshold
        self.assertEqual(
            self.server._codex_row_state(tail, 0.0, 1000.0, True, False),
            "stuck",
        )

    def test_idle_when_turn_complete(self):
        tail = {"last_event_type": "result"}
        self.assertEqual(
            self.server._codex_row_state(tail, 1000.0, 1010.0, True, False),
            "idle",
        )


class TestCodexPoolAlive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def test_pool_alive_true_when_app_server_running(self):
        srv = self.server
        srv._codex_pool_alive_cache["ts"] = 0.0
        orig = srv.find_live_codex_processes
        srv.find_live_codex_processes = lambda: [
            {"pid": 1, "command": "/opt/homebrew/bin/codex app-server --listen stdio://"}
        ]
        try:
            self.assertTrue(srv._codex_pool_alive(now=1000.0))
        finally:
            srv.find_live_codex_processes = orig
            srv._codex_pool_alive_cache["ts"] = 0.0

    def test_pool_alive_false_when_no_app_server(self):
        srv = self.server
        srv._codex_pool_alive_cache["ts"] = 0.0
        orig = srv.find_live_codex_processes
        srv.find_live_codex_processes = lambda: [
            {"pid": 1, "command": "codex --resume abc123"}
        ]
        try:
            self.assertFalse(srv._codex_pool_alive(now=1000.0))
        finally:
            srv.find_live_codex_processes = orig
            srv._codex_pool_alive_cache["ts"] = 0.0


if __name__ == "__main__":
    unittest.main()
