"""Two-node federation integration tests: pairing, auth boundary, routing.

Boots two isolated CCC servers (separate HOMEs, loopback transport — see
tests/two_node_harness.py) and exercises the peer protocol end to end.
Ordered test methods share one fleet; the node-kill test runs last.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from two_node_harness import TwoNodeFleet


class TestTwoNodeFederation(unittest.TestCase):
    fleet: TwoNodeFleet = None

    @classmethod
    def setUpClass(cls):
        cls.fleet = TwoNodeFleet()
        cls.fleet.start()

    @classmethod
    def tearDownClass(cls):
        cls.fleet.cleanup()

    # ---- identity ----------------------------------------------------------

    def test_01_hello_identities_distinct(self):
        a = self.fleet.node_a.get("/api/federation/v1/hello")
        b = self.fleet.node_b.get("/api/federation/v1/hello")
        self.assertTrue(a["node_id"])
        self.assertTrue(b["node_id"])
        self.assertNotEqual(a["node_id"], b["node_id"])
        self.assertIn("handoff", a["caps"]["features"])
        self.assertEqual(a["caps"]["handoff_engines"], ["claude"])

    def test_02_unpaired_peer_rejected(self):
        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/route",
            {"action": "group_chat_create", "args": {"topic": "x"}, "hops": 2,
             "req_id": "nope"},
            headers={"X-CCC-Peer": "not-a-node", "X-CCC-Peer-Token": "bogus"},
            expect_error=True,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload.get("error"), "unpaired_peer")

    def test_03_pairing_round_trip(self):
        peer = self.fleet.pair()
        self.assertEqual(peer["node_id"], self.fleet.node_b.node_id)
        self.assertTrue(peer["has_secret"])
        self.assertNotIn("secret", peer)

        # A sees B
        peers_a = self.fleet.node_a.get("/api/federation/peers")
        self.assertEqual(len(peers_a["peers"]), 1)
        self.assertEqual(peers_a["peers"][0]["node_id"], self.fleet.node_b.node_id)
        self.assertNotIn("secret", peers_a["peers"][0])

        # B reciprocally sees A with a loopback transport back
        peers_b = self.fleet.node_b.get("/api/federation/peers")
        self.assertEqual(len(peers_b["peers"]), 1)
        entry = peers_b["peers"][0]
        self.assertEqual(entry["node_id"], self.fleet.node_a.node_id)
        self.assertEqual(entry["transport"]["type"], "loopback")
        self.assertEqual(entry["transport"]["port"], self.fleet.node_a.port)
        self.assertEqual(entry["paired_by"], "inbound")

    def test_04_test_connection_health(self):
        status, payload = self.fleet.node_a.post(
            "/api/federation/peers/test", {"node_id": self.fleet.node_b.node_id})
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["health"]["node_id"], self.fleet.node_b.node_id)
        self.assertGreaterEqual(payload["latency_ms"], 0)

    def test_05_route_executes_on_owner(self):
        # A routes a group-chat creation to B: the chat must exist on B's
        # disk, not A's.
        status, payload = self.fleet.node_a.post("/api/federation/peers/test", {
            "node_id": self.fleet.node_b.node_id})
        self.assertTrue(payload["ok"])
        # Send the route envelope from A's side manually via B's route
        # endpoint using A's stored secret — i.e. exactly what PeerClient does.
        peers_b = self.fleet.node_b.get("/api/federation/peers")
        # (assert reciprocity intact before routing)
        self.assertEqual(peers_b["peers"][0]["node_id"], self.fleet.node_a.node_id)

        env = {
            "proto": 1,
            "req_id": "route-create-1",
            "hops": 2,
            "action": "group_chat_create",
            "args": {"topic": "federation route test", "session_ids": [],
                     "include_human": True},
        }
        status, payload = self._route_a_to_b(env)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        result = payload["result"]
        self.assertTrue(result.get("ok"), result)
        chat_uuid = result.get("uuid")
        self.assertTrue(chat_uuid)

        # Chat file lives under B's HOME, not A's
        b_chats = list(self.fleet.node_b.group_chats_dir.glob("*.json"))
        self.assertEqual(len(b_chats), 1)
        self.assertFalse(self.fleet.node_a.group_chats_dir.exists())

        # Read it back through the route layer
        env_read = {
            "proto": 1, "req_id": "route-read-1", "hops": 2,
            "action": "group_chat_read", "args": {"id": chat_uuid},
        }
        status, payload = self._route_a_to_b(env_read)
        self.assertEqual(status, 200)
        self.assertIn("federation route test", payload["result"].get("content", ""))
        type(self)._chat_uuid = chat_uuid

    def test_06_duplicate_req_id_is_idempotent(self):
        env = {
            "proto": 1,
            "req_id": "route-create-1",  # same as test_05
            "hops": 2,
            "action": "group_chat_create",
            "args": {"topic": "federation route test", "session_ids": []},
        }
        status, payload = self._route_a_to_b(env)
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("duplicate"), payload)
        # No second chat materialized on B
        b_chats = list(self.fleet.node_b.group_chats_dir.glob("*.json"))
        self.assertEqual(len(b_chats), 1)

    def test_07_hop_limit_enforced(self):
        env = {
            "proto": 1, "req_id": "route-hops-0", "hops": 0,
            "action": "group_chat_read", "args": {"id": "whatever"},
        }
        status, payload = self._route_a_to_b(env, expect_error=True)
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "routing_loop")

    def test_08_unknown_action_is_unsupported(self):
        env = {
            "proto": 1, "req_id": "route-bad-action", "hops": 2,
            "action": "rm_dash_rf", "args": {},
        }
        status, payload = self._route_a_to_b(env, expect_error=True)
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("error"), "unsupported_capability")

    def test_09_repo_inventory_validates_paths_on_owner(self):
        self.fleet.make_origin_and_clones()
        # B answers for its own clone
        secret = self._secret_of_a_for_b()
        status, payload = self.fleet.node_b.request(
            "GET",
            f"/api/federation/v1/repo-inventory?repo_path={self.fleet.repo_b}",
            headers=self._auth_headers(),
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["repo_identity"].startswith("local:") or
                        "/" in payload["repo_identity"])
        self.assertEqual(len(payload["worktrees"]), 1)
        self.assertIn("observed_at", payload)
        # Path traversal / non-repo path is rejected by the owning node
        status, payload = self.fleet.node_b.request(
            "GET",
            "/api/federation/v1/repo-inventory?repo_path=/etc",
            headers=self._auth_headers(),
        )
        self.assertGreaterEqual(status, 400)

    def test_10_sessions_inventory_carries_node_refs(self):
        payload = None
        status, payload = self.fleet.node_b.request(
            "GET", "/api/federation/v1/sessions", headers=self._auth_headers())
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["node_id"], self.fleet.node_b.node_id)
        self.assertIn("observed_at", payload)
        for row in payload["sessions"]:
            self.assertTrue(row["ref"].startswith(self.fleet.node_b.node_id + ":"))

    def test_11_cross_origin_post_still_rejected(self):
        status, payload = self.fleet.node_b.post(
            "/api/federation/v1/route", {},
            headers={"Origin": "https://evil.example"},
            expect_error=True,
        )
        self.assertEqual(status, 403)

    def test_99_peer_offline_is_explicit(self):
        self.fleet.node_b.stop()
        time.sleep(0.3)
        status, payload = self.fleet.node_a.post(
            "/api/federation/peers/test",
            {"node_id": self.fleet.node_b.node_id},
            expect_error=True,
        )
        self.assertEqual(status, 502)
        self.assertIn(payload.get("error"), ("peer_offline", "timeout"))

    # ---- helpers -------------------------------------------------------------

    def _secret_of_a_for_b(self):
        import json
        peers_file = self.fleet.node_a.state_dir / "peers.json"
        peers = json.loads(peers_file.read_text())
        for p in peers:
            if p["node_id"] == self.fleet.node_b.node_id:
                return p["secret"]
        raise AssertionError("A has no stored secret for B")

    def _auth_headers(self):
        return {
            "X-CCC-Peer": self.fleet.node_a.node_id,
            "X-CCC-Peer-Token": self._secret_of_a_for_b(),
        }

    def _route_a_to_b(self, envelope, expect_error=False):
        return self.fleet.node_b.post(
            "/api/federation/v1/route", envelope,
            headers=self._auth_headers(), expect_error=expect_error)


if __name__ == "__main__":
    unittest.main()
