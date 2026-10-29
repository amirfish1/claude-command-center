"""CCC-245: a terminal session's new turn reads 'active' from its first tool.

Only PostToolUse writes sidecar status=active, so after Stop wrote 'waiting'
the first tool of the next turn used to keep the row idle until it finished.
"""
import unittest
from unittest import mock

import server as srv


class SidecarResumedTurnTests(unittest.TestCase):
    def _fields(self, sc, inflight):
        entry = {"session_id": "ccc245-sid", "is_live": True}
        with mock.patch.object(srv, "_read_sidecar_state", lambda sid: sc), \
                mock.patch.object(srv, "_read_in_flight_state", lambda sid: inflight), \
                mock.patch.object(srv, "_live_in_flight_or_none", lambda sid, st: st), \
                mock.patch.object(srv, "_read_notification_state", lambda sid: None), \
                mock.patch.object(srv, "_read_compacting_state", lambda sid: None), \
                mock.patch.object(srv, "_session_recently_interrupted", lambda sid: False):
            srv._add_sidecar_fields(entry)
        return entry

    def test_tool_started_after_stop_reads_active(self):
        entry = self._fields(
            {"status": "waiting", "timestamp": 100.0},
            {"tool": "Bash", "file": "npm test", "started_at": 150.0},
        )
        self.assertEqual(entry["sidecar_status"], "active")
        self.assertTrue(entry["sidecar_in_flight"])

    def test_stale_marker_from_before_stop_stays_waiting(self):
        # An interrupted tool never ran PostToolUse; its marker predates Stop.
        entry = self._fields(
            {"status": "waiting", "timestamp": 200.0},
            {"tool": "Bash", "file": "npm test", "started_at": 150.0},
        )
        self.assertEqual(entry["sidecar_status"], "waiting")

    def test_no_tool_in_flight_keeps_waiting(self):
        entry = self._fields({"status": "waiting", "timestamp": 200.0}, None)
        self.assertEqual(entry["sidecar_status"], "waiting")
        self.assertFalse(entry["sidecar_in_flight"])


if __name__ == "__main__":
    unittest.main()
