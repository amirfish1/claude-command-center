"""Security-boundary integration tests — acceptance scenario 12.

The federation must work WITHOUT: changing the default loopback bind,
exposing an unauthenticated remote command API, accepting unpaired peers,
or letting an imported bundle escape approved roots.
"""

import base64
import hashlib
import json
import socket
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation
from two_node_harness import TwoNodeFleet


def _lan_ip():
    """A non-loopback IP of this machine, or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET, no packets actually sent
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


class TestSecurityBoundary(unittest.TestCase):
    fleet: TwoNodeFleet = None

    @classmethod
    def setUpClass(cls):
        cls.fleet = TwoNodeFleet()
        cls.fleet.start()
        cls.fleet.pair()
        cls.fleet.make_origin_and_clones()
        cls.identity = federation.repo_identity(str(cls.fleet.repo_a))["identity"]
        for node, repo in ((cls.fleet.node_a, cls.fleet.repo_a),
                           (cls.fleet.node_b, cls.fleet.repo_b)):
            node.post("/api/federation/repo-map", {
                "identity": cls.identity, "local_path": str(repo)})

    @classmethod
    def tearDownClass(cls):
        cls.fleet.cleanup()

    def _auth_headers(self):
        peers = json.loads(
            (self.fleet.node_a.state_dir / "peers.json").read_text())
        secret = next(p["secret"] for p in peers
                      if p["node_id"] == self.fleet.node_b.node_id)
        return {"X-CCC-Peer": self.fleet.node_a.node_id,
                "X-CCC-Peer-Token": secret}

    # ------------------------------------------------------------------

    def test_default_bind_is_loopback_only(self):
        lan = _lan_ip()
        if not lan:
            self.skipTest("no non-loopback interface available")
        for node in (self.fleet.node_a, self.fleet.node_b):
            s = socket.socket()
            s.settimeout(2)
            with self.assertRaises(OSError,
                                   msg=f"{node.name} reachable on {lan}!"):
                s.connect((lan, node.port))
            s.close()

    def test_every_peer_endpoint_rejects_unpaired_callers(self):
        bogus = {"X-CCC-Peer": str(uuid.uuid4()),
                 "X-CCC-Peer-Token": "obvious-fake-token-XXXX"}
        for method, path, body in (
            ("GET", "/api/federation/v1/health", None),
            ("GET", "/api/federation/v1/sessions", None),
            ("GET", "/api/federation/v1/repo-inventory?repo_path=/tmp", None),
            ("GET", "/api/federation/v1/fleet-inventory", None),
            ("POST", "/api/federation/v1/route",
             {"action": "inject", "args": {}, "hops": 2, "req_id": "x"}),
            ("POST", "/api/federation/v1/handoff/prepare",
             {"repo_identity": "x", "commit": "y"}),
            ("POST", "/api/federation/v1/handoff/import",
             {"manifest": {}, "files": {}}),
            ("POST", "/api/federation/v1/unpair", {}),
        ):
            status, payload = self.fleet.node_b.request(
                method, path, body=body, headers=bogus)
            self.assertEqual(status, 403, f"{method} {path}: {payload}")
            self.assertEqual(payload.get("error"), "unpaired_peer",
                             f"{method} {path}: {payload}")

    def test_import_cannot_escape_approved_roots(self):
        # Even a PAIRED peer cannot land a bundle outside the repo mapping /
        # projects tree: dest_cwd must be an existing checkout of the SAME
        # repo identity, file names must stay inside the bundle, and the
        # session id must be filename-safe.
        transcript = b'{"type":"user","cwd":"/x","sessionId":"s"}\n'
        sha = hashlib.sha256(transcript).hexdigest()
        sid = str(uuid.uuid4())

        def manifest(dest_cwd, name="transcript.jsonl", session_id=sid):
            return {
                "manifest_version": 1,
                "transfer_id": str(uuid.uuid4()),
                "engine": "claude",
                "session_id": session_id,
                "source_node": self.fleet.node_a.node_id,
                "dest_node": self.fleet.node_b.node_id,
                "repo_identity": self.identity,
                "source_cwd": "/somewhere",
                "dest_cwd": dest_cwd,
                "files": [{"name": name, "role": "transcript",
                           "bytes": len(transcript), "sha256": sha}],
            }

        files = {"transcript.jsonl": base64.b64encode(transcript).decode()}

        # dest_cwd outside any approved repo checkout → rejected.
        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/handoff/import",
            {"manifest": manifest("/etc"), "files": files},
            headers=self._auth_headers(), expect_error=True)
        self.assertEqual(status, 409, payload)

        # dest_cwd pointing at a DIFFERENT repo than the manifest claims.
        other = self.fleet.node_b.home / "repos" / "other-repo"
        other.mkdir(parents=True, exist_ok=True)
        import subprocess
        subprocess.run(["git", "init", "-q", str(other)], check=True,
                       capture_output=True)
        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/handoff/import",
            {"manifest": manifest(str(other)), "files": files},
            headers=self._auth_headers(), expect_error=True)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload.get("error"), "stale_mapping")

        # Path-traversal file names and session ids → rejected outright.
        bad = manifest(str(self.fleet.repo_b), name="../../evil.jsonl")
        bad["files"][0]["name"] = "../../evil.jsonl"
        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/handoff/import",
            {"manifest": bad,
             "files": {"../../evil.jsonl": files["transcript.jsonl"]}},
            headers=self._auth_headers(), expect_error=True)
        self.assertEqual(status, 400, payload)

        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/handoff/import",
            {"manifest": manifest(str(self.fleet.repo_b),
                                  session_id="../../../evil"),
             "files": files},
            headers=self._auth_headers(), expect_error=True)
        self.assertEqual(status, 400, payload)

        # Nothing escaped anywhere.
        self.assertFalse(list(self.fleet.node_b.home.rglob("evil*")))

    def test_cross_origin_posts_rejected_everywhere(self):
        for path, body in (
            ("/api/federation/v1/route", {}),
            ("/api/federation/peers/pair", {}),
            ("/api/fleet/plan", {}),
            ("/api/fleet/execute", {}),
        ):
            status, payload = self.fleet.node_b.post(
                path, body, headers={"Origin": "https://evil.example"},
                expect_error=True)
            self.assertEqual(status, 403, f"{path}: {payload}")

    def test_secrets_never_served(self):
        payload = self.fleet.node_a.get("/api/federation/peers")
        blob = json.dumps(payload)
        peers = json.loads(
            (self.fleet.node_a.state_dir / "peers.json").read_text())
        for p in peers:
            self.assertNotIn(p["secret"], blob)


if __name__ == "__main__":
    unittest.main()
