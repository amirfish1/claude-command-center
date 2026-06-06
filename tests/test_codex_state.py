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


class TestCodexPoolLiveness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def test_recently_active_pool_codex_counts_live(self):
        srv = self.server
        sid = "test-pool-sid"
        saved = {
            "is_codex": srv._is_codex_session,
            "is_cursor": srv._is_cursor_session,
            "is_gemini": srv._is_gemini_session,
            "is_antigravity": srv._is_antigravity_session,
            "fields": srv._codex_state_fields,
            "ids": srv._live_engine_session_ids,
        }
        srv._is_codex_session = lambda s: s == sid
        srv._is_cursor_session = lambda s: False
        srv._is_gemini_session = lambda s: False
        srv._is_antigravity_session = lambda s: False
        srv._codex_state_fields = lambda s, now=None: {"codex_state": "working", "codex_fresh": True}
        srv._live_engine_session_ids = lambda: frozenset()
        try:
            self.assertTrue(srv._archive_session_is_live(sid))
        finally:
            srv._is_codex_session = saved["is_codex"]
            srv._is_cursor_session = saved["is_cursor"]
            srv._is_gemini_session = saved["is_gemini"]
            srv._is_antigravity_session = saved["is_antigravity"]
            srv._codex_state_fields = saved["fields"]
            srv._live_engine_session_ids = saved["ids"]


if __name__ == "__main__":
    unittest.main()
