"""Regression coverage for CCC-1146: KAP-routed kimi sessions must surface
the wire-tail contract (pending tool + stale-mid-turn fields) the ACP branch
already reports.

A kap-routed session's live state comes from the daemon's busy flag, which a
wedged serving process can leave stuck true forever. Without the wire-tail
cross-check the pane shows a bare "Working…" with no tool name and never
reaches the stuck card; with it, a wire silent past CCC_STALE_TOOL_SEC sets
stale_tool_call and the client-side stuck card takes over (it wins over the
busy indicator), while a healthy turn names its dangling tool.
"""

import importlib
import time
import unittest
from unittest import mock

import server  # noqa: F401  (populates sys.modules so ccc_server's core proxy resolves)
from ccc_server import kap, session_graph
from ccc_server import kimi_store


SID = "session_kap-ccc-1146"
SESSION_DIR = "/tmp/kimi-kap-ccc-1146"


def _run(status_payload, tail_meta):
    """session_live_status with the KAP daemon and wire-tail lookups mocked."""
    with mock.patch.object(session_graph._core, "_is_kimi_session", return_value=True), \
         mock.patch.object(session_graph._core, "_acp_wire_path", return_value=None), \
         mock.patch.object(kap, "kap_routes", return_value=True), \
         mock.patch.object(kap, "kap_session_status", return_value=status_payload), \
         mock.patch.object(kap, "kap_runtime_binding_cached", return_value=None), \
         mock.patch.object(session_graph._core, "_kimi_session_index",
                           return_value={SID: {"session_dir": SESSION_DIR}}), \
         mock.patch.object(session_graph._core, "_kimi_wire_tail_meta",
                           return_value=tail_meta), \
         mock.patch.object(session_graph._core, "_kimi_stale_tool_fields",
                           side_effect=lambda tail, acp_active=False: kimi_store._kimi_stale_tool_fields(
                               tail, acp_active=acp_active, threshold_s=900)):
        return session_graph.session_live_status(SID, "/repo")


class KapLiveStatusWireTailTests(unittest.TestCase):
    def test_busy_turn_names_its_pending_tool(self):
        result = _run(
            {"busy": True, "model": "kimi-code/k3"},
            {"last_event_type": "assistant", "pending_tool": "Agent",
             "mid_turn": True, "wire_mtime": 1000.0},
        )
        self.assertTrue(result["live"])
        self.assertEqual(result["kind"], "kap")
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["pending_tool"], "Agent")
        self.assertEqual(result["pending_tool_ts"], 1000.0)
        self.assertEqual(result["last_event_type"], "assistant")

    def test_wedged_busy_turn_stamps_the_stale_contract(self):
        # Daemon still says busy but the wire has been silent past the
        # threshold — the pane must be able to flip from "Working…" to the
        # stuck card instead of spinning forever (CCC-1146).
        old = time.time() - 2000  # well past the 900s stale threshold
        result = _run(
            {"busy": True, "model": "kimi-code/k3"},
            {"last_event_type": "assistant", "pending_tool": "Agent",
             "mid_turn": True, "wire_mtime": old},
        )
        self.assertEqual(result["status"], "running")  # daemon's word stands
        self.assertTrue(result["stale_tool_call"])
        self.assertGreaterEqual(result["stale_tool_age_s"], 900)
        self.assertEqual(result["pending_tool"], "Agent")

    def test_idle_daemon_with_clean_wire_reports_no_stale(self):
        result = _run(
            {"busy": False, "model": "kimi-code/k3"},
            {"last_event_type": "result", "pending_tool": None,
             "mid_turn": False, "wire_mtime": 0.0},
        )
        self.assertEqual(result["status"], "idle")
        self.assertFalse(result["stale_tool_call"])
        self.assertNotIn("pending_tool", result)

    def test_wire_tail_failure_still_returns_kap_status(self):
        # A broken wire lookup must degrade to the pre-existing KAP answer,
        # never break the status poll.
        with mock.patch.object(session_graph._core, "_is_kimi_session", return_value=True), \
             mock.patch.object(session_graph._core, "_acp_wire_path", return_value=None), \
             mock.patch.object(kap, "kap_routes", return_value=True), \
             mock.patch.object(kap, "kap_session_status", return_value={"busy": True}), \
             mock.patch.object(kap, "kap_runtime_binding_cached", return_value=None), \
             mock.patch.object(session_graph._core, "_kimi_session_index",
                               return_value={SID: {"session_dir": SESSION_DIR}}), \
             mock.patch.object(session_graph._core, "_kimi_wire_tail_meta",
                               side_effect=RuntimeError("wire gone")):
            result = session_graph.session_live_status(SID, "/repo")
        self.assertEqual(result["kind"], "kap")
        self.assertEqual(result["status"], "running")


if __name__ == "__main__":
    unittest.main()
