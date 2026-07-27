"""Regression coverage for liveness-miss hysteresis on the Codex app-server.

Even after the busy-vs-wedged fix (see test_codex_app_server_busy_vs_wedged.py),
production kept seeing an occasional WEDGED-and-replace on a fully idle
transport (no in-flight request) roughly once a minute -- consistent with this
machine's known memory pressure causing an isolated multi-second scheduling
stall rather than the process actually being dead. Replacing on a single slow
reply is itself expensive (subprocess spawn, ps scan, handshake) and adds to
that same pressure, so _ensure_codex_app_server now requires
_CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD consecutive misses on one transport
before concluding it's actually wedged.
"""

import importlib
import unittest
from unittest import mock


class LivenessHysteresisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _transport(self, pid, misses=0):
        transport = mock.Mock()
        transport.alive.return_value = True
        transport.started_at = 1000.0
        transport.proc = mock.Mock(pid=pid)
        transport.consecutive_liveness_misses = misses
        return transport

    def test_first_miss_on_idle_transport_is_deferred_not_replaced(self):
        server = self.server
        transport = self._transport(pid=901, misses=0)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INFLIGHT", 0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            result = server._ensure_codex_app_server(allow_stdio=False)
        self.assertIs(result, transport)
        transport.close.assert_not_called()
        self.assertEqual(transport.consecutive_liveness_misses, 1)
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("SLOW", kinds)
        self.assertNotIn("WEDGED", kinds)

    def test_reaching_the_threshold_replaces_and_logs_wedged(self):
        server = self.server
        transport = self._transport(pid=902, misses=1)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INFLIGHT", 0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(server, "_resolve_codex_bin", return_value={"available": False}), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            server._ensure_codex_app_server(allow_stdio=False)
        transport.close.assert_called_once()
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("WEDGED", kinds)
        self.assertNotIn("SLOW", kinds)
        wedged_call = next(c for c in log_activity.call_args_list if c.args[1] == "WEDGED")
        self.assertIn("2 consecutive misses", wedged_call.args[2])

    def test_a_successful_probe_resets_the_miss_streak(self):
        server = self.server
        transport = self._transport(pid=903, misses=1)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=True), \
             mock.patch.object(server.time, "time", return_value=1200.0):
            result = server._ensure_codex_app_server(allow_stdio=False)
        self.assertIs(result, transport)
        self.assertEqual(transport.consecutive_liveness_misses, 0)

    def test_busy_inflight_request_does_not_count_as_a_liveness_miss(self):
        server = self.server
        transport = self._transport(pid=904, misses=0)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INFLIGHT", 1), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            result = server._ensure_codex_app_server(allow_stdio=False)
        self.assertIs(result, transport)
        self.assertEqual(transport.consecutive_liveness_misses, 0)
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("BUSY", kinds)


if __name__ == "__main__":
    unittest.main()
