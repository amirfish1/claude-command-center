"""The WatchTower liveness probe must not race an aggregate endpoint.

CCC used to probe `/api/status` under a 0.8s budget. That endpoint walks every
queue on disk: measured 3.0-3.7s against 19 real queues, so the probe timed out
every single time and a perfectly healthy WatchTower reported `degraded`
forever — one permanent amber on the System status chip that no restart could
clear. Widening the timeout only moves the cliff to the next queue someone adds.

A path the router does not know answers in under a millisecond, and a 404 from
WatchTower proves socket + process + router are alive, which is all `api_ok`
claims. Process identity is checked separately (`command_verified`).
"""

import unittest
import urllib.error
from unittest import mock

import server


class WatchtowerLivenessProbeTests(unittest.TestCase):
    def setUp(self):
        server._watchtower_forget_api_probe()
        self.addCleanup(server._watchtower_forget_api_probe)

    def test_probe_does_not_hit_the_expensive_status_aggregate(self):
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with mock.patch.object(server.urllib.request, "urlopen", fake_urlopen):
            server._watchtower_api_probe("http://127.0.0.1:8787")

        self.assertEqual(len(seen), 1)
        self.assertNotIn("/api/status", seen[0])

    def test_a_404_from_a_live_router_counts_as_up(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with mock.patch.object(server.urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(server._watchtower_api_probe("http://127.0.0.1:8787"))

    def test_a_server_error_is_not_liveness(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 500, "Boom", {}, None)

        with mock.patch.object(server.urllib.request, "urlopen", fake_urlopen):
            self.assertFalse(server._watchtower_api_probe("http://127.0.0.1:8787"))

    def test_a_refused_connection_is_not_liveness(self):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError("nope"))

        with mock.patch.object(server.urllib.request, "urlopen", fake_urlopen):
            self.assertFalse(server._watchtower_api_probe("http://127.0.0.1:8787"))

    def test_a_slow_endpoint_cannot_starve_the_probe_budget(self):
        """Whatever the daemon is busy with, the probe is bounded."""
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["timeout"] = timeout
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with mock.patch.object(server.urllib.request, "urlopen", fake_urlopen):
            server._watchtower_api_probe("http://127.0.0.1:8787")

        self.assertEqual(captured["timeout"], server._WT_API_PROBE_TIMEOUT)
        self.assertLessEqual(server._WT_API_PROBE_TIMEOUT, 3.0)


if __name__ == "__main__":
    unittest.main()
