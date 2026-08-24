"""Regression coverage for the app-server "busy vs wedged" liveness fix.

Production was seen replacing its single shared Codex app-server process
every few seconds under load (see activity.log WEDGED storm). Root cause:
the periodic liveness probe (`thread/list`) shares the same request/response
channel as real turn traffic, and Codex app-server services requests on one
stdio connection largely in order -- so a slow reply to the probe while a
real request is in flight means "busy", not "wedged". Tearing down a
perfectly healthy, working app-server killed the in-flight session and
forced a respawn, which added its own CPU/process overhead and made the
underlying system load (and thus future probe latency) worse -- a
self-reinforcing loop.

_CODEX_APP_SERVER_INFLIGHT tracks real (non-probe) requests currently
awaiting a reply; _ensure_codex_app_server now checks it before concluding
"wedged" and tearing the transport down.
"""

import importlib
import threading
import time
import unittest
from unittest import mock


class BusyNotWedgedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_slow_probe_reply_with_inflight_request_is_not_replaced(self):
        server = self.server
        transport = mock.Mock()
        transport.alive.return_value = True
        transport.started_at = 1000.0
        transport.proc = mock.Mock(pid=555)
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
        transport.close.assert_not_called()
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("BUSY", kinds)
        self.assertNotIn("WEDGED", kinds)
        busy_call = next(c for c in log_activity.call_args_list if c.args[1] == "BUSY")
        self.assertIn("pid=555", busy_call.args[2])
        self.assertIn("1 request", busy_call.args[2])

    def test_slow_probe_reply_with_no_inflight_request_still_replaces(self):
        server = self.server
        transport = mock.Mock()
        transport.alive.return_value = True
        transport.started_at = 1000.0
        transport.proc = mock.Mock(pid=556)
        transport.consecutive_liveness_misses = 1
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
        self.assertNotIn("BUSY", kinds)

    def test_successful_init_stamps_last_live_check_to_skip_immediate_reprobe(self):
        server = self.server
        proc = mock.Mock()
        proc.poll.return_value = None
        with mock.patch.object(server, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(server, "_codex_shared_state_conflict", return_value=None), \
             mock.patch.object(server, "_resolve_codex_bin",
                                return_value={"available": True, "bin": "/usr/bin/codex"}), \
             mock.patch.object(server, "_codex_app_server_reap_stray_children"), \
             mock.patch.object(server.subprocess, "Popen", return_value=proc), \
             mock.patch.object(server.threading, "Thread"), \
             mock.patch.object(server, "_codex_app_server_request_to_transport",
                                return_value={"result": {}}), \
             mock.patch.object(server.time, "time", return_value=5000.0), \
             mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", None), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0):
            result = server._ensure_codex_app_server()
            self.assertIsNotNone(result)
            self.assertEqual(server._CODEX_APP_SERVER_LAST_LIVE_CHECK, 5000.0)


class InflightCounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        server = self.server
        with server._CODEX_APP_SERVER_INFLIGHT_LOCK:
            server._CODEX_APP_SERVER_INFLIGHT = 0

    def test_probe_calls_do_not_count_as_inflight(self):
        server = self.server
        transport = mock.Mock()
        transport.send_json.side_effect = BrokenPipeError("closed")
        server._codex_app_server_request_to_transport(
            transport, "thread/list", {}, timeout=1, count_as_inflight=False,
        )
        self.assertEqual(server._CODEX_APP_SERVER_INFLIGHT, 0)

    def test_real_request_is_counted_while_awaiting_a_reply_and_cleared_after(self):
        server = self.server
        transport = mock.Mock()
        transport.send_json = mock.Mock()
        seen_midflight = threading.Event()

        def _worker():
            server._codex_app_server_request_to_transport(
                transport, "turn/start", {}, timeout=2,
            )

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        deadline = time.time() + 2
        while time.time() < deadline:
            if server._CODEX_APP_SERVER_INFLIGHT > 0:
                seen_midflight.set()
                break
            time.sleep(0.01)
        t.join(timeout=3)
        self.assertTrue(seen_midflight.is_set(), "expected inflight counter to go above 0 while awaiting reply")
        self.assertEqual(server._CODEX_APP_SERVER_INFLIGHT, 0)


if __name__ == "__main__":
    unittest.main()
