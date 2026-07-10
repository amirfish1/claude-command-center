"""Contract tests for federation.py — identities, peers, envelopes, manifests.

These are the stable cross-machine contracts everything else builds on:
canonical repo identity, global session references, the paired-peer registry,
route idempotency, and the session-transfer manifest schema.
"""

import json
import subprocess
import threading
import unittest
import tempfile
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation


class _IsolatedHome(unittest.TestCase):
    """Every test gets a scratch HOME so federation state never touches the
    developer's real ~/.claude/command-center."""

    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp = tempfile.TemporaryDirectory(prefix="ccc-fed-test-")
        os.environ["HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self._tmp.cleanup()


class TestRepoIdentity(unittest.TestCase):
    def test_parse_remote_url_matrix(self):
        cases = {
            "https://github.com/owner/repo.git": "github.com/owner/repo",
            "https://github.com/owner/repo": "github.com/owner/repo",
            "git@github.com:owner/repo.git": "github.com/owner/repo",
            "ssh://git@github.com/owner/repo.git": "github.com/owner/repo",
            "ssh://git@example.test:2222/owner/repo.git": "example.test/owner/repo",
            "git://example.test/owner/repo": "example.test/owner/repo",
            "https://GitLab.example.TEST/group/sub/repo.git": "gitlab.example.test/group/sub/repo",
            "git@bitbucket.example:team/proj.git": "bitbucket.example/team/proj",
        }
        for url, expected in cases.items():
            self.assertEqual(federation.parse_remote_url(url), expected, url)

    def test_parse_remote_url_rejects_garbage(self):
        for url in ("", "   ", "/Users/nobody/some/local/path", "file:///x/y",
                    "not a url at all", "https://hostonly"):
            self.assertIsNone(federation.parse_remote_url(url), url)

    def test_repo_identity_remote_and_local_fallback(self):
        with tempfile.TemporaryDirectory(prefix="ccc-fed-repo-") as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@test",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@test"}
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True,
                               capture_output=True, env=env)
            git("init", "-q")
            (repo / "a.txt").write_text("hello\n")
            git("add", "a.txt")
            git("commit", "-q", "-m", "root")

            # No remote → local identity from root commit
            ident = federation.repo_identity(str(repo))
            self.assertIsNotNone(ident)
            self.assertEqual(ident["kind"], "local")
            self.assertTrue(ident["identity"].startswith("local:myrepo:"))
            self.assertEqual(len(ident["identity"].split(":")[-1]), 12)

            # With a remote → canonical identity wins
            git("remote", "add", "origin", "https://git.example.test/acme/myrepo.git")
            ident2 = federation.repo_identity(str(repo))
            self.assertEqual(ident2["kind"], "remote")
            self.assertEqual(ident2["identity"], "git.example.test/acme/myrepo")

            # Two clones of the same history share the local identity
            clone = Path(tmp) / "clone"
            subprocess.run(["git", "clone", "-q", str(repo), str(clone)],
                           check=True, capture_output=True, env=env)
            subprocess.run(["git", "-C", str(clone), "remote", "remove", "origin"],
                           check=True, capture_output=True, env=env)
            ident3 = federation.repo_identity(str(clone))
            self.assertEqual(ident3["identity"].split(":")[-1],
                             ident["identity"].split(":")[-1])

    def test_repo_identity_non_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(federation.repo_identity(tmp))
        self.assertIsNone(federation.repo_identity("/nonexistent/path/xyz"))


class TestSessionRefs(unittest.TestCase):
    def test_round_trip(self):
        nid = "12345678-1234-1234-1234-123456789abc"
        sid = "abcdefab-5678-5678-5678-abcdefabcdef"
        ref = federation.format_session_ref(nid, sid)
        self.assertEqual(federation.parse_session_ref(ref), (nid, sid))

    def test_bare_session_id_means_local(self):
        self.assertEqual(
            federation.parse_session_ref("abcdefab-5678-5678-5678-abcdefabcdef"),
            (None, "abcdefab-5678-5678-5678-abcdefabcdef"))

    def test_non_uuid_prefix_is_not_a_node(self):
        # A native id that happens to contain a colon stays intact.
        self.assertEqual(federation.parse_session_ref("weird:id"), (None, "weird:id"))
        self.assertEqual(federation.parse_session_ref(""), (None, ""))


class TestNodeIdentity(_IsolatedHome):
    def test_identity_created_once_and_stable(self):
        first = federation.node_identity()
        self.assertTrue(first["node_id"])
        self.assertTrue(first["display_name"])
        second = federation.node_identity()
        self.assertEqual(first["node_id"], second["node_id"])

    def test_rename(self):
        federation.node_identity()
        out = federation.set_node_display_name("  studio-mac  ")
        self.assertEqual(out["display_name"], "studio-mac")
        self.assertEqual(federation.node_identity()["display_name"], "studio-mac")
        with self.assertRaises(ValueError):
            federation.set_node_display_name("   ")

    def test_capability_manifest(self):
        caps = federation.capability_manifest("9.9.9", ["claude", "codex"])
        self.assertEqual(caps["proto"], federation.FEDERATION_PROTO_VERSION)
        self.assertEqual(caps["version"], "9.9.9")
        self.assertIn("handoff", caps["features"])
        self.assertEqual(caps["handoff_engines"], ["claude"])


class TestPeerRegistry(_IsolatedHome):
    PEER = {
        "node_id": "11111111-2222-3333-4444-555555555555",
        "name": "test-vm",
        "transport": {"type": "loopback", "port": 18099},
        "secret": "obvious-fake-secret-XXXX",
    }

    def test_round_trip(self):
        self.assertEqual(federation.load_peers(), [])
        federation.upsert_peer(dict(self.PEER))
        peers = federation.load_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["name"], "test-vm")
        self.assertTrue(peers[0]["added_at"])

        # Upsert merges rather than duplicating
        federation.upsert_peer({**self.PEER, "name": "renamed-vm"})
        peers = federation.load_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["name"], "renamed-vm")

        got = federation.get_peer(self.PEER["node_id"])
        self.assertIsNotNone(got)
        federation.update_peer(self.PEER["node_id"], last_seen="2026-07-10")
        self.assertEqual(federation.get_peer(self.PEER["node_id"])["last_seen"], "2026-07-10")

        self.assertTrue(federation.remove_peer(self.PEER["node_id"]))
        self.assertFalse(federation.remove_peer(self.PEER["node_id"]))
        self.assertEqual(federation.load_peers(), [])

    def test_transport_validation(self):
        with self.assertRaises(ValueError):
            federation.upsert_peer({"node_id": "x-1", "transport": {"type": "carrier-pigeon"}})
        with self.assertRaises(ValueError):
            federation.upsert_peer({"transport": {"type": "ssh"}})

    def test_peer_auth(self):
        federation.upsert_peer(dict(self.PEER))
        nid = self.PEER["node_id"]
        self.assertIsNotNone(federation.validate_peer_auth(nid, "obvious-fake-secret-XXXX"))
        self.assertIsNone(federation.validate_peer_auth(nid, "wrong"))
        self.assertIsNone(federation.validate_peer_auth(nid, ""))
        self.assertIsNone(federation.validate_peer_auth("unknown-node", "obvious-fake-secret-XXXX"))

    def test_secret_generation_is_unique(self):
        a, b = federation.generate_pairing_secret(), federation.generate_pairing_secret()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 32)


class TestRepoMap(_IsolatedHome):
    def test_map_resolve_unmap(self):
        with tempfile.TemporaryDirectory() as tmp:
            federation.map_repo("example.test/acme/app", tmp)
            resolved = federation.resolve_repo_path("example.test/acme/app")
            self.assertEqual(Path(resolved).resolve(), Path(tmp).resolve())
            federation.unmap_repo("example.test/acme/app")
            self.assertIsNone(federation.resolve_repo_path("example.test/acme/app"))

    def test_map_requires_identity(self):
        with self.assertRaises(ValueError):
            federation.map_repo("", "/tmp")


class TestRouteEnvelope(_IsolatedHome):
    def test_envelope_shape(self):
        env = federation.make_route_envelope("inject", {"session_id": "abc"})
        self.assertEqual(env["proto"], federation.FEDERATION_PROTO_VERSION)
        self.assertEqual(env["action"], "inject")
        self.assertEqual(env["hops"], federation.MAX_ROUTE_HOPS)
        self.assertTrue(env["req_id"])

    def test_idempotency_dedupe(self):
        rid = "req-00000000-1"
        self.assertIsNone(federation.check_and_record_request(rid))
        dup = federation.check_and_record_request(rid)
        self.assertIsNotNone(dup)
        federation.record_request_result(rid, {"ok": True, "echo": 1})
        dup2 = federation.check_and_record_request(rid)
        self.assertEqual(dup2["result"], {"ok": True, "echo": 1})

    def test_dedupe_survives_reload_and_caps(self):
        federation.check_and_record_request("req-persist")
        # New in-memory state (fresh module has no memory anyway — file-backed)
        self.assertIsNotNone(federation.check_and_record_request("req-persist"))
        # Cap enforcement doesn't explode
        for i in range(30):
            federation.check_and_record_request(f"req-cap-{i}")


class TestTransferManifest(unittest.TestCase):
    def _valid(self):
        return {
            "manifest_version": federation.TRANSFER_MANIFEST_VERSION,
            "transfer_id": "t-1",
            "engine": "claude",
            "session_id": "abcdefab-1111-2222-3333-444444444444",
            "source_node": "n-a",
            "dest_node": "n-b",
            "repo_identity": "example.test/acme/app",
            "source_cwd": "/home/alice/app",
            "dest_cwd": "/home/bob/app",
            "files": [
                {"name": "transcript.jsonl", "role": "transcript",
                 "bytes": 10, "sha256": "0" * 64},
            ],
        }

    def test_valid_manifest(self):
        self.assertEqual(federation.validate_transfer_manifest(self._valid()), [])

    def test_missing_fields(self):
        m = self._valid()
        del m["repo_identity"]
        problems = federation.validate_transfer_manifest(m)
        self.assertTrue(any("repo_identity" in p for p in problems))

    def test_bad_version(self):
        m = self._valid()
        m["manifest_version"] = 99
        self.assertTrue(federation.validate_transfer_manifest(m))

    def test_path_escape_rejected(self):
        m = self._valid()
        m["files"][0]["name"] = "../../etc/passwd"
        problems = federation.validate_transfer_manifest(m)
        self.assertTrue(any("escapes" in p for p in problems))
        m["files"][0]["name"] = "/abs/path"
        self.assertTrue(federation.validate_transfer_manifest(m))

    def test_not_a_dict(self):
        self.assertTrue(federation.validate_transfer_manifest(None))
        self.assertTrue(federation.validate_transfer_manifest([]))


class _StubPeerHandler(BaseHTTPRequestHandler):
    """Minimal peer: validates pairing headers on POST, echoes envelope."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/api/federation/v1/hello":
            self._send(200, {"ok": True, "node_id": "stub-node", "name": "stub"})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("X-CCC-Peer-Token") != "stub-secret":
            self._send(403, {"error": "unpaired_peer"})
            return
        self._send(200, {"ok": True, "echo": body,
                         "peer": self.headers.get("X-CCC-Peer")})

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestLoopbackTransport(_IsolatedHome):
    def setUp(self):
        super().setUp()
        self.server = HTTPServer(("127.0.0.1", 0), _StubPeerHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _client(self, secret="stub-secret", port=None):
        peer = {
            "node_id": "stub-node",
            "transport": {"type": "loopback", "port": port or self.port},
            "secret": secret,
        }
        return federation.PeerClient(peer, self_node_id="local-node-id")

    def test_request_round_trip_with_auth(self):
        out = self._client().request("POST", "/api/federation/v1/route",
                                     {"action": "ping"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["echo"], {"action": "ping"})
        self.assertEqual(out["peer"], "local-node-id")

    def test_unpaired_peer_maps_to_typed_error(self):
        with self.assertRaises(federation.PeerError) as ctx:
            self._client(secret="wrong").request("POST", "/api/federation/v1/route", {})
        self.assertEqual(ctx.exception.kind, "unpaired_peer")

    def test_peer_offline(self):
        with self.assertRaises(federation.PeerError) as ctx:
            # A port from the ephemeral range with nothing listening.
            self._client(port=1).request("GET", "/api/federation/v1/health")
        self.assertEqual(ctx.exception.kind, "peer_offline")

    def test_get_hello(self):
        out = self._client().request("GET", "/api/federation/v1/hello")
        self.assertEqual(out["node_id"], "stub-node")


if __name__ == "__main__":
    unittest.main()
