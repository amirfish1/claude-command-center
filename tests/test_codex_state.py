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


class _FakeStat:
    def __init__(self, mtime):
        self.st_mtime = mtime
        self.st_size = 128
        self.st_mtime_ns = int(mtime * 1_000_000_000)


class TestCodexWorkingSince(unittest.TestCase):
    """_codex_state_fields emits working_since (epoch the current turn began)
    alongside codex_state == 'working' — the data behind the sidebar WIP
    timer. It must prefer the app-server's stamped turn start and fall back
    to the rollout's own task_started/user_message markers, and it must never
    appear on non-working states."""

    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def setUp(self):
        srv = self.server
        self._saved_state = dict(srv._CODEX_APP_SERVER_THREAD_STATE)
        self._pool = srv._codex_pool_alive
        self._live_ids = srv._live_engine_session_ids
        self._resolve = srv._resolve_codex_rollout_path
        srv._codex_pool_alive = lambda now=None: True
        srv._live_engine_session_ids = lambda: frozenset()
        srv._resolve_codex_rollout_path = lambda sid: None

    def tearDown(self):
        srv = self.server
        srv._CODEX_APP_SERVER_THREAD_STATE.clear()
        srv._CODEX_APP_SERVER_THREAD_STATE.update(self._saved_state)
        srv._codex_pool_alive = self._pool
        srv._live_engine_session_ids = self._live_ids
        srv._resolve_codex_rollout_path = self._resolve

    def test_app_state_turn_started_wins(self):
        srv = self.server
        sid = "wip-app-state-sid"
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid,
            "active_turn_id": "turn-1",
            "active_writer": "ccc",
            "turn_started_at": 500.0,
            "status": "active",
            "last_activity_at": 990.0,
        }
        try:
            fields = srv._codex_state_fields(
                sid, now=1000.0, note_writer_transition=False,
                rollout_tail={"turn_started_ts": 800.0, "last_user_ts": 810.0},
            )
        finally:
            srv._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)
        self.assertEqual(fields["codex_state"], "working")
        self.assertEqual(fields["working_since"], 500.0)

    def test_rollout_task_started_fallback(self):
        srv = self.server
        # mtime 50s old: past the external-writer window, inside the 24h
        # recency gate — exercises the tail-derived row_state path.
        fields = srv._codex_state_fields(
            "wip-tail-sid", now=1000.0, note_writer_transition=False,
            rollout_path="/tmp/ccc-wip-test-rollout.jsonl",
            rollout_stat=_FakeStat(950.0),
            rollout_tail={"pending_tool": "shell", "turn_started_ts": 930.0},
        )
        self.assertEqual(fields["codex_state"], "working")
        self.assertEqual(fields["working_since"], 930.0)

    def test_no_working_since_when_idle(self):
        srv = self.server
        fields = srv._codex_state_fields(
            "wip-idle-sid", now=1000.0, note_writer_transition=False,
            rollout_path="/tmp/ccc-wip-test-rollout.jsonl",
            rollout_stat=_FakeStat(950.0),
            rollout_tail={"last_event_type": "result", "turn_started_ts": 0},
        )
        self.assertEqual(fields["codex_state"], "idle")
        self.assertNotIn("working_since", fields)


class TestCodexPoolAlive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def test_pool_alive_true_when_app_server_running(self):
        srv = self.server
        srv._codex_pool_alive_cache["ts"] = 0.0
        orig = srv._raw_engine_process_commands
        srv._raw_engine_process_commands = lambda _engine: [
            ("1", "/opt/homebrew/bin/codex app-server --listen stdio://")
        ]
        try:
            self.assertTrue(srv._codex_pool_alive(now=1000.0))
        finally:
            srv._raw_engine_process_commands = orig
            srv._codex_pool_alive_cache["ts"] = 0.0

    def test_pool_alive_false_when_no_app_server(self):
        srv = self.server
        srv._codex_pool_alive_cache["ts"] = 0.0
        orig = srv._raw_engine_process_commands
        srv._raw_engine_process_commands = lambda _engine: [
            ("1", "codex --resume abc123")
        ]
        try:
            self.assertFalse(srv._codex_pool_alive(now=1000.0))
        finally:
            srv._raw_engine_process_commands = orig
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
