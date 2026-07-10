"""Two-node fleet inventory integration: acceptance scenarios 1 and 2's
observation half — one Fleet view over both nodes with independently
correct dirty / unpublished / default-branch dimensions and honest
staleness when a peer dies.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation
from two_node_harness import TwoNodeFleet, git


class TestFleetTwoNode(unittest.TestCase):
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

    def _repo_entry(self, payload, node_id):
        for repo in payload["repos"]:
            if repo["identity"] == self.identity:
                return repo["nodes"].get(node_id)
        return None

    def _inventory(self, fetch=False):
        return self.fleet.node_a.get(
            f"/api/fleet/inventory?fetch={'1' if fetch else '0'}")

    # ------------------------------------------------------------------

    def test_01_both_nodes_in_one_view_with_observation_times(self):
        inv = self._inventory()
        self.assertTrue(inv["ok"])
        node_ids = {n["node_id"] for n in inv["nodes"]}
        self.assertIn(self.fleet.node_a.node_id, node_ids)
        self.assertIn(self.fleet.node_b.node_id, node_ids)
        for node_id in (self.fleet.node_a.node_id, self.fleet.node_b.node_id):
            entry = self._repo_entry(inv, node_id)
            self.assertIsNotNone(entry, f"no entry for {node_id}")
            self.assertTrue(entry["ok"], entry)
            self.assertIn("observed_at", entry)
            self.assertEqual(len(entry["worktrees"]), 1)
            self.assertEqual(entry["default_branch"]["branch"], "main")
            # Independent dimensions all present
            self.assertIn("prs", entry)
            self.assertIn("deployment", entry)
            self.assertIn("sessions", entry)
            # Temp repos: PR dimension explicitly skipped (local origin),
            # never silently empty.
            self.assertEqual(entry["prs"].get("skipped"), "no remote host")

    def test_02_dirty_state_is_per_node(self):
        dirty = self.fleet.repo_b / "uncommitted.txt"
        dirty.write_text("wip on node B\n")
        try:
            inv = self._inventory(fetch=True)
            a = self._repo_entry(inv, self.fleet.node_a.node_id)
            b = self._repo_entry(inv, self.fleet.node_b.node_id)
            self.assertFalse(a["worktrees"][0]["dirty"])
            self.assertTrue(b["worktrees"][0]["dirty"])
            self.assertEqual(b["worktrees"][0]["dirty_files"], 1)
            self.assertEqual(b["worktrees"][0]["untracked"], 1)
        finally:
            dirty.unlink()

    def test_03_remote_unpublished_commit_visible_from_node_a(self):
        sha = self.fleet.commit_on(self.fleet.repo_b, "b-only.txt",
                                   "made on node B\n", "feat: node B work",
                                   push=False)
        inv = self._inventory(fetch=True)
        a = self._repo_entry(inv, self.fleet.node_a.node_id)
        b = self._repo_entry(inv, self.fleet.node_b.node_id)
        self.assertEqual(a["worktrees"][0]["unpublished_commits"], 0)
        self.assertEqual(b["worktrees"][0]["unpublished_commits"], 1)
        self.assertEqual(b["worktrees"][0]["head_sha"], sha)
        # Origin's default branch does NOT include it yet — separate facts.
        self.assertNotEqual(a["default_branch"]["sha"], sha)
        type(self).b_sha = sha

    def test_04_after_push_and_fetch_node_a_sees_new_origin_head(self):
        git(self.fleet.repo_b, "push", "-q", "origin", "main")
        inv = self._inventory(fetch=True)
        a = self._repo_entry(inv, self.fleet.node_a.node_id)
        b = self._repo_entry(inv, self.fleet.node_b.node_id)
        self.assertEqual(b["worktrees"][0]["unpublished_commits"], 0)
        # A's fetched view of origin/main now shows B's commit while A's own
        # checkout is still on the old commit — the "pull needed" fact.
        self.assertEqual(a["default_branch"]["sha"], self.b_sha)
        self.assertNotEqual(a["worktrees"][0]["head_sha"], self.b_sha)

    def test_99_dead_peer_is_stale_not_silent(self):
        self._inventory()  # warm the peer cache
        self.fleet.node_b.stop()
        time.sleep(0.3)
        inv = self._inventory(fetch=True)
        b_node = next(n for n in inv["nodes"]
                      if n["node_id"] == self.fleet.node_b.node_id)
        self.assertFalse(b_node["ok"])
        self.assertIn(b_node.get("error"), ("peer_offline", "timeout"))
        b_entry = self._repo_entry(inv, self.fleet.node_b.node_id)
        if b_entry is not None:  # served from cache — must be labeled stale
            self.assertTrue(b_entry.get("stale"))


if __name__ == "__main__":
    unittest.main()
