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
        # Second repository: the Fleet view is multi-repo by design.
        _origin2, extra_a, extra_b = cls.fleet.make_extra_repo("second-app")
        cls.identity2 = federation.repo_identity(str(extra_a))["identity"]
        for node, repo in ((cls.fleet.node_a, extra_a),
                           (cls.fleet.node_b, extra_b)):
            node.post("/api/federation/repo-map", {
                "identity": cls.identity2, "local_path": str(repo)})

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
        # Several repositories, one view — each with entries for both nodes.
        identities = {r["identity"] for r in inv["repos"]}
        self.assertIn(self.identity, identities)
        self.assertIn(self.identity2, identities)
        second = next(r for r in inv["repos"] if r["identity"] == self.identity2)
        self.assertEqual(set(second["nodes"].keys()),
                         {self.fleet.node_a.node_id, self.fleet.node_b.node_id})
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

    def test_05_fast_pass_skips_the_slow_dimensions_across_both_nodes(self):
        """The Fleet page paints from a git-only pass first.

        prs/deploy/sessions are the three dimensions that cost network
        round-trips or a full transcript parse; dropping them is what turns a
        multi-minute scan into a few seconds. The skip must reach peers too
        (not just the local node), and a skipped dimension must be *marked*
        skipped rather than reported as an empty result — otherwise the page
        would render "0 open PRs" for a repo it never asked GitHub about.
        """
        inv = self.fleet.node_a.get(
            "/api/fleet/inventory?fetch=0&prs=0&deploy=0&sessions=0")
        self.assertTrue(inv["ok"])
        checked = 0
        for repo in inv["repos"]:
            for node_id, entry in repo["nodes"].items():
                if not entry.get("ok"):
                    continue
                checked += 1
                # Git facts still present — this pass is what paints the matrix.
                self.assertIn("worktrees", entry, node_id)
                self.assertIn("default_branch", entry, node_id)
                # Slow dimensions explicitly marked, not silently empty.
                self.assertEqual(entry["prs"].get("skipped"), "excluded", node_id)
                self.assertEqual(
                    entry["deployment"].get("skipped"), "excluded", node_id)
                self.assertTrue(entry.get("sessions_skipped"), node_id)
                self.assertEqual(entry.get("sessions"), [], node_id)
        self.assertGreaterEqual(checked, 2, "expected entries on both nodes")

    def test_06_full_pass_still_returns_every_dimension(self):
        """The fast pass must not leak into the default (enriching) pass.

        Both shapes are cached per-peer, so a cache keyed only by node would
        let a fast-pass response satisfy the full request forever.
        """
        inv = self.fleet.node_a.get("/api/fleet/inventory?fetch=0")
        self.assertTrue(inv["ok"])
        for repo in inv["repos"]:
            for node_id, entry in repo["nodes"].items():
                if not entry.get("ok"):
                    continue
                self.assertNotEqual(
                    entry["prs"].get("skipped"), "excluded", node_id)
                self.assertFalse(entry.get("sessions_skipped"), node_id)
                self.assertIsInstance(entry.get("sessions"), list, node_id)

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
