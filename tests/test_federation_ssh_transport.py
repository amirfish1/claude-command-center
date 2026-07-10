"""SSH peer-transport unit tests.

The SSH transport = (a) a tiny HTTP client executed ON the peer that talks
to the peer CCC's own loopback, and (b) envelope construction + stale-port
retry in PeerClient. (a) is executed for real here — the exact script that
ships over SSH runs locally against a stub loopback server. (b) is tested
with a stubbed multiplexer; only the ssh binary itself is out of scope.
"""

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation


class _Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        out = json.dumps({
            "ok": True,
            "echo": body,
            "peer_header": self.headers.get("X-CCC-Peer"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestRemoteHttpClientScript(unittest.TestCase):
    """Run the exact script the SSH transport executes on the peer."""

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Stub)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _run(self, envelope):
        proc = subprocess.run(
            [sys.executable, "-c", federation._REMOTE_HTTP_CLIENT],
            input=json.dumps(envelope), capture_output=True, text=True,
            timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_post_round_trip_with_headers(self):
        out = self._run({
            "method": "POST",
            "path": "/api/federation/v1/route",
            "headers": {"X-CCC-Peer": "node-a", "X-CCC-Peer-Token": "s"},
            "body": {"action": "ping"},
            "port": self.port,
            "timeout": 10,
        })
        self.assertEqual(out["status"], 200)
        self.assertEqual(out["body"]["echo"], {"action": "ping"})
        self.assertEqual(out["body"]["peer_header"], "node-a")

    def test_unreachable_port_reports_status_zero(self):
        out = self._run({"method": "GET", "path": "/x", "headers": {},
                         "body": None, "port": 1, "timeout": 3})
        self.assertEqual(out["status"], 0)
        self.assertTrue(out.get("error"))


class _FakeMux:
    """Records envelopes; returns scripted results per call."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run_capture(self, args, timeout=60, input=None):
        self.calls.append(json.loads(input))
        result = self.results.pop(0)

        class P:
            returncode = 0
            stdout = json.dumps(result)
            stderr = ""
        return P()


class TestSshEnvelopeAndRetry(unittest.TestCase):
    def _client(self, mux):
        peer = {"node_id": "n-b",
                "transport": {"type": "ssh", "host": "user@host.example",
                              "port": 4321},
                "secret": "obvious-fake-XXXX"}
        client = federation.PeerClient(peer, self_node_id="n-a")
        client._ssh_mux = lambda: mux
        return client

    def test_envelope_carries_auth_and_pinned_port(self):
        mux = _FakeMux([{"status": 200, "body": {"ok": True}}])
        out = self._client(mux).request("GET", "/api/federation/v1/health")
        self.assertTrue(out["ok"])
        env = mux.calls[0]
        self.assertEqual(env["method"], "GET")
        self.assertEqual(env["port"], 4321)
        self.assertEqual(env["headers"]["X-CCC-Peer"], "n-a")
        self.assertEqual(env["headers"]["X-CCC-Peer-Token"], "obvious-fake-XXXX")

    def test_stale_pinned_port_retries_via_port_txt(self):
        mux = _FakeMux([
            {"status": 0, "error": "connection refused"},   # pinned port dead
            {"status": 200, "body": {"ok": True, "via": "port.txt"}},
        ])
        out = self._client(mux).request("GET", "/api/federation/v1/health")
        self.assertTrue(out["ok"])
        self.assertEqual(mux.calls[0]["port"], 4321)
        self.assertIsNone(mux.calls[1]["port"])  # rediscover on the peer

    def test_both_attempts_dead_is_peer_offline(self):
        mux = _FakeMux([
            {"status": 0, "error": "refused"},
            {"status": 0, "error": "refused"},
        ])
        with self.assertRaises(federation.PeerError) as ctx:
            self._client(mux).request("GET", "/api/federation/v1/health")
        self.assertEqual(ctx.exception.kind, "peer_offline")


if __name__ == "__main__":
    unittest.main()
