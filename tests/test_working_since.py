"""working_since — the epoch the current turn began — must appear on
live-activity entries only while a session is genuinely working, so the
sidebar WIP timer never labels a parked (question/approval) or idle
session as working."""
import importlib
import sys
import unittest


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


class TestLiveActivityWorkingSince(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def _entry_with_sidecar(self, sidecar_fields):
        srv = self.server
        saved = {
            "live": srv._archive_session_is_live,
            "engine": srv._detect_session_engine,
            "sidecar": srv._add_sidecar_fields,
        }
        srv._archive_session_is_live = lambda sid: True
        srv._detect_session_engine = lambda sid: "claude"
        srv._add_sidecar_fields = (
            lambda entry: entry.update(sidecar_fields)
        )
        try:
            return srv._live_activity_entry_for_session("wip-claude-sid")
        finally:
            srv._archive_session_is_live = saved["live"]
            srv._detect_session_engine = saved["engine"]
            srv._add_sidecar_fields = saved["sidecar"]

    def test_in_flight_tool_sets_working_since(self):
        entry = self._entry_with_sidecar({
            "sidecar_in_flight": True,
            "sidecar_ts": 700.0,
            "sidecar_tool": "Bash",
            "question_waiting": False,
            "needs_approval": False,
        })
        self.assertEqual(entry["working_since"], 700.0)

    def test_question_waiting_suppresses_timer(self):
        # AskUserQuestion registers as an in-flight tool — without the
        # question_waiting gate the chip would tick while the session is
        # parked on the human.
        entry = self._entry_with_sidecar({
            "sidecar_in_flight": True,
            "sidecar_ts": 700.0,
            "sidecar_tool": "AskUserQuestion",
            "question_waiting": True,
            "needs_approval": False,
        })
        self.assertIsNone(entry.get("working_since"))

    def test_idle_session_has_no_timer(self):
        entry = self._entry_with_sidecar({
            "sidecar_in_flight": False,
            "sidecar_ts": 700.0,
            "sidecar_tool": "Read",
            "question_waiting": False,
            "needs_approval": False,
        })
        self.assertIsNone(entry.get("working_since"))

    def test_working_since_in_live_activity_field_keys(self):
        # The /api/sessions/live-activity projection whitelists keys —
        # forgetting it silently drops the field before the client sees it.
        self.assertIn("working_since", self.server._LIVE_ACTIVITY_FIELD_KEYS)


class TestAcpWorkingSince(unittest.TestCase):
    """ACP harnesses (kimi/grok/devin) get working_since from the observed
    turn start — the same stamp that feeds turn_age_s."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def setUp(self):
        self.server._ACP_TURN_TRACK.clear()

    def tearDown(self):
        self.server._ACP_TURN_TRACK.clear()

    def test_mid_turn_emits_start_epoch(self):
        srv = self.server
        saved_load = srv._acp_load_state
        saved_kimi_idx = srv._kimi_session_index
        saved_tail = srv._kimi_wire_tail_meta
        srv._acp_load_state = lambda h: None
        srv._kimi_session_index = lambda: {"s1": {"session_dir": "/x"}}
        srv._kimi_wire_tail_meta = lambda d: {"mid_turn": True, "wire_mtime": 900.0}
        try:
            out = srv._acp_live_activity_fields("kimi", "s1")
        finally:
            srv._acp_load_state = saved_load
            srv._kimi_session_index = saved_kimi_idx
            srv._kimi_wire_tail_meta = saved_tail
        self.assertIsNotNone(out.get("working_since"))
        self.assertAlmostEqual(
            out["turn_age_s"],
            srv.time.time() - out["working_since"],
            delta=1.0,
        )

    def test_approval_parked_turn_suppresses_timer(self):
        srv = self.server
        saved_load = srv._acp_load_state
        saved_state = srv._ACP_SESSION_STATE
        srv._acp_load_state = lambda h: None
        srv._ACP_SESSION_STATE = {"kimi": {"s2": {"pending_permissions": [{"id": "p1"}]}}}
        saved_tail = srv._kimi_wire_tail_meta
        saved_idx = srv._kimi_session_index
        srv._kimi_session_index = lambda: {"s2": {"session_dir": "/x"}}
        srv._kimi_wire_tail_meta = lambda d: {"mid_turn": True, "wire_mtime": 900.0}
        try:
            out = srv._acp_live_activity_fields("kimi", "s2")
        finally:
            srv._acp_load_state = saved_load
            srv._ACP_SESSION_STATE = saved_state
            srv._kimi_session_index = saved_idx
            srv._kimi_wire_tail_meta = saved_tail
        self.assertTrue(out["needs_approval"])
        self.assertIsNone(out.get("working_since"))


if __name__ == "__main__":
    unittest.main()
