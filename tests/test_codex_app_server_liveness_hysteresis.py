"""Regression coverage for liveness-miss hysteresis on the Codex app-server.

Even after the busy-vs-wedged fix (see test_codex_app_server_busy_vs_wedged.py),
production kept seeing an occasional WEDGED-and-replace on a fully idle
transport (no in-flight request) roughly once a minute. Direct measurement
ruled out "codex app-server is slow": a freshly-spawned instance answered a
thread/list probe in well under half a second across repeated 25s-idle-gap
trials, with zero misses. So a miss here is not evidence the app-server is
actually slow -- it means CCC's own process didn't observe a reply in time,
most likely because the reader thread was starved of the GIL by something
else running concurrently in this same process. The exact culprit is still
unconfirmed; _codex_app_server_dump_stacks_on_liveness_miss captures an
all-thread stack snapshot at the moment of each miss so it can be identified
from evidence. In the meantime, _ensure_codex_app_server requires
_CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD consecutive misses on one transport
before concluding it's actually wedged, since replacing on a single missed
reply is itself expensive (subprocess spawn, ps scan, handshake).
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
        self.assertIn("MISS", kinds)
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
        self.assertNotIn("MISS", kinds)
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

    def test_a_miss_triggers_a_stack_dump_for_diagnosis(self):
        server = self.server
        transport = self._transport(pid=905, misses=0)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INFLIGHT", 0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_codex_app_server_dump_stacks_on_liveness_miss") as dump_stacks, \
             mock.patch.object(server, "_log_activity"):
            server._ensure_codex_app_server(allow_stdio=False)
        dump_stacks.assert_called_once_with("miss")

    def test_reaching_the_threshold_dumps_stacks_with_wedge_reason(self):
        server = self.server
        transport = self._transport(pid=906, misses=1)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INFLIGHT", 0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(server, "_resolve_codex_bin", return_value={"available": False}), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_codex_app_server_dump_stacks_on_liveness_miss") as dump_stacks, \
             mock.patch.object(server, "_log_activity"):
            server._ensure_codex_app_server(allow_stdio=False)
        dump_stacks.assert_called_once_with("wedge-threshold")


class DumpStacksOnLivenessMissTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_noop_without_sigusr2_or_dump_file(self):
        server = self.server
        with mock.patch.object(server, "_PYTHON_STACK_DUMP_FILE", None), \
             mock.patch.object(server.os, "kill") as kill:
            server._codex_app_server_dump_stacks_on_liveness_miss("miss")
        kill.assert_not_called()

    def test_writes_a_marker_and_raises_sigusr2_against_self(self):
        server = self.server
        dump_file = mock.Mock()
        with mock.patch.object(server, "_PYTHON_STACK_DUMP_FILE", dump_file), \
             mock.patch.object(server.os, "kill") as kill, \
             mock.patch.object(server.os, "getpid", return_value=4242):
            server._codex_app_server_dump_stacks_on_liveness_miss("wedge-threshold")
        dump_file.write.assert_called_once()
        self.assertIn("wedge-threshold", dump_file.write.call_args.args[0])
        self.assertIn("pid=4242", dump_file.write.call_args.args[0])
        dump_file.flush.assert_called_once()
        kill.assert_called_once_with(4242, server.signal.SIGUSR2)

    def test_swallows_oserror_from_kill(self):
        server = self.server
        dump_file = mock.Mock()
        with mock.patch.object(server, "_PYTHON_STACK_DUMP_FILE", dump_file), \
             mock.patch.object(server.os, "kill", side_effect=OSError("nope")):
            server._codex_app_server_dump_stacks_on_liveness_miss("miss")  # must not raise


if __name__ == "__main__":
    unittest.main()
