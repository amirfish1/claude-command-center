"""Unit tests for the deterministic fleet recommendation rules — every gate
explained, deployment separated from Git state (acceptance scenario 9),
cleanup only with proof, preservation otherwise.

Pure function tests over synthetic inventories: no servers, no git, no gh.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server

NODE_A = {"node_id": "aaaa1111-0000-0000-0000-000000000001", "name": "laptop",
          "self": True, "ok": True, "stale": False}
NODE_B = {"node_id": "bbbb2222-0000-0000-0000-000000000002", "name": "vm",
          "self": False, "ok": True, "stale": False}
IDENT = "example.test/acme/demo-app"


def _wt(path="/repos/demo-app", branch="main", dirty=False, dirty_files=0,
        unpublished=0, head="c" * 40, merged=None, primary=True):
    return {
        "path": path, "branch": branch, "detached": False, "locked": False,
        "dirty": dirty, "dirty_files": dirty_files, "staged": 0,
        "untracked": dirty_files, "unpublished_commits": unpublished,
        "head_sha": head, "merged_into_default": merged,
        "is_primary_clone": primary,
    }


def _entry(worktrees=None, default_sha="c" * 40, prs=None, deploy=None,
           sessions=None, ok=True, stale=False, **extra):
    entry = {
        "ok": ok,
        "stale": stale,
        "observed_at": 1000.0,
        "repo_path": "/repos/demo-app",
        "repo_identity": IDENT,
        "repo_identity_kind": "remote",
        "default_branch": {"branch": "main", "sha": default_sha},
        "worktrees": worktrees if worktrees is not None else [_wt()],
        "prs": prs or {"open": [], "observed_at": 1000.0},
        "deployment": deploy or {"skipped": "no deployment provider configured"},
        "sessions": sessions or [],
    }
    entry.update(extra)
    return entry


def _inventory(a_entry=None, b_entry=None):
    nodes = [NODE_A, NODE_B]
    repo_nodes = {}
    if a_entry is not None:
        repo_nodes[NODE_A["node_id"]] = a_entry
    if b_entry is not None:
        repo_nodes[NODE_B["node_id"]] = b_entry
    return {"ok": True, "nodes": nodes,
            "repos": [{"identity": IDENT, "nodes": repo_nodes}]}


def _recs(inv):
    return server._fleet_recommendations(inv)


def _kinds(recs):
    return [r["kind"] for r in recs]


class TestDeploymentSeparation(unittest.TestCase):
    """Scenario 9: pushed/merged commit + failed deploy = 'Git complete,
    deployment failed' — recommend deploy investigation, never a push."""

    def test_failed_deploy_yields_investigation_not_push(self):
        inv = _inventory(a_entry=_entry(
            worktrees=[_wt(dirty=False, unpublished=0)],
            deploy={"provider": "vercel", "state": "ERROR",
                    "commit_sha": "c" * 7, "observed_at": 1000.0,
                    "url": "https://demo.example"}))
        recs = _recs(inv)
        kinds = _kinds(recs)
        self.assertIn("investigate_deploy", kinds)
        self.assertNotIn("push", kinds)
        deploy_rec = next(r for r in recs if r["kind"] == "investigate_deploy")
        self.assertIn("Git state is complete", deploy_rec["reason"])
        self.assertIn("do NOT push again", deploy_rec["command_intent"])

    def test_green_deploy_is_silent(self):
        inv = _inventory(a_entry=_entry(
            deploy={"provider": "vercel", "state": "READY",
                    "commit_sha": "c" * 7}))
        self.assertNotIn("investigate_deploy", _kinds(_recs(inv)))


class TestPushPullRules(unittest.TestCase):
    def test_unpublished_commits_recommend_push(self):
        inv = _inventory(b_entry=_entry(
            worktrees=[_wt(unpublished=2, head="d" * 40)]))
        recs = _recs(inv)
        push = next(r for r in recs if r["kind"] == "push")
        self.assertEqual(push["node_id"], NODE_B["node_id"])
        self.assertTrue(push["ready"])
        self.assertIn("only this node has them", push["reason"])

    def test_push_blocked_by_dirty_tree(self):
        inv = _inventory(b_entry=_entry(
            worktrees=[_wt(unpublished=2, dirty=True, dirty_files=3)]))
        recs = _recs(inv)
        push = next(r for r in recs if r["kind"] == "push")
        self.assertFalse(push["ready"])
        self.assertEqual(push["blockers"][0]["code"], "dirty_worktree")
        # And an ask_commit accompanies it, ordered FIRST.
        kinds = _kinds(recs)
        self.assertLess(kinds.index("ask_commit"), kinds.index("push"))

    def test_clone_behind_origin_recommends_ff_pull(self):
        inv = _inventory(a_entry=_entry(
            worktrees=[_wt(head="e" * 40)], default_sha="f" * 40))
        recs = _recs(inv)
        pull = next(r for r in recs if r["kind"] == "pull_ff")
        self.assertTrue(pull["ready"])
        self.assertEqual(pull["evidence"]["origin_head"], "f" * 40)

    def test_dirty_clone_pull_is_blocked(self):
        inv = _inventory(a_entry=_entry(
            worktrees=[_wt(head="e" * 40, dirty=True, dirty_files=1)],
            default_sha="f" * 40))
        pull = next(r for r in _recs(inv) if r["kind"] == "pull_ff")
        self.assertFalse(pull["ready"])


class TestCleanupSafety(unittest.TestCase):
    def test_merged_clean_worktree_removable_with_proof(self):
        inv = _inventory(a_entry=_entry(worktrees=[
            _wt(),  # primary clone
            _wt(path="/repos/demo-app-wt-feat", branch="feat/x",
                merged=True, primary=False),
        ]))
        recs = _recs(inv)
        removal = next(r for r in recs if r["kind"] == "remove_worktree")
        self.assertTrue(removal["destructive"])
        self.assertTrue(removal["ready"])
        self.assertIn("merge-base --is-ancestor", removal["evidence"]["proof"])

    def test_unmerged_worktree_never_removable(self):
        inv = _inventory(a_entry=_entry(worktrees=[
            _wt(),
            _wt(path="/repos/demo-app-wt-feat", branch="feat/x",
                merged=False, primary=False),
        ]))
        recs = _recs(inv)
        self.assertNotIn("remove_worktree", _kinds(recs))
        finish = next(r for r in recs if r["kind"] == "finish_worktree")
        self.assertFalse(finish["ready"])
        self.assertIn("preserved", finish["reason"])

    def test_dirty_merged_worktree_still_preserved(self):
        inv = _inventory(a_entry=_entry(worktrees=[
            _wt(),
            _wt(path="/repos/demo-app-wt-feat", branch="feat/x", merged=True,
                dirty=True, dirty_files=2, primary=False),
        ]))
        recs = _recs(inv)
        self.assertNotIn("remove_worktree", _kinds(recs))
        self.assertIn("finish_worktree", _kinds(recs))

    def test_unknown_mergedness_not_removable(self):
        inv = _inventory(a_entry=_entry(worktrees=[
            _wt(),
            _wt(path="/repos/demo-app-wt-feat", branch="feat/x",
                merged=None, primary=False),
        ]))
        self.assertNotIn("remove_worktree", _kinds(_recs(inv)))


class TestPRGates(unittest.TestCase):
    def _pr(self, draft=False, failing=(), pending=(), mergeable="MERGEABLE",
            review="APPROVED"):
        return {"number": 7, "title": "t", "branch": "feat/x",
                "head_sha": "a" * 40, "draft": draft,
                "url": "https://example.test/pr/7",
                "mergeable": mergeable, "merge_state": "CLEAN",
                "review_decision": review,
                "checks_failing": list(failing), "checks_pending": list(pending),
                "checks_total": 3}

    def test_green_draft_ready_to_mark(self):
        inv = _inventory(a_entry=_entry(prs={"open": [self._pr(draft=True)]}))
        rec = next(r for r in _recs(inv) if r["kind"] == "mark_ready")
        self.assertTrue(rec["ready"])

    def test_red_draft_blocked_with_named_checks(self):
        inv = _inventory(a_entry=_entry(prs={
            "open": [self._pr(draft=True, failing=["ci/unit"])]}))
        rec = next(r for r in _recs(inv) if r["kind"] == "mark_ready")
        self.assertFalse(rec["ready"])
        self.assertIn("ci/unit", rec["blockers"][0]["detail"])

    def test_merge_never_ready_with_failing_checks(self):
        inv = _inventory(a_entry=_entry(prs={
            "open": [self._pr(failing=["ci/e2e"])]}))
        rec = next(r for r in _recs(inv) if r["kind"] == "merge_pr")
        self.assertFalse(rec["ready"])
        codes = [b["code"] for b in rec["blockers"]]
        self.assertIn("checks_failing", codes)
        # Failing checks also get their own investigation recommendation.
        self.assertIn("investigate_checks", _kinds(_recs(inv)))

    def test_merge_gates_conflict_and_review(self):
        inv = _inventory(a_entry=_entry(prs={
            "open": [self._pr(mergeable="CONFLICTING",
                              review="CHANGES_REQUESTED")]}))
        rec = next(r for r in _recs(inv) if r["kind"] == "merge_pr")
        codes = [b["code"] for b in rec["blockers"]]
        self.assertIn("merge_conflict", codes)
        self.assertIn("changes_requested", codes)

    def test_clean_pr_merge_ready(self):
        inv = _inventory(a_entry=_entry(prs={"open": [self._pr()]}))
        rec = next(r for r in _recs(inv) if r["kind"] == "merge_pr")
        self.assertTrue(rec["ready"], rec["blockers"])


class TestHonestSources(unittest.TestCase):
    def test_stale_node_flagged(self):
        inv = _inventory(a_entry=_entry(stale=True))
        recs = _recs(inv)
        stale = [r for r in recs if r["kind"] == "investigate_source"
                 and r["target"] == "stale"]
        self.assertEqual(len(stale), 1)
        self.assertIn("outdated", stale[0]["reason"])

    def test_failed_entry_flagged(self):
        inv = _inventory(a_entry={"ok": False, "error": "stale_mapping",
                                  "detail": "mapped path missing"})
        recs = _recs(inv)
        self.assertEqual(_kinds(recs), ["investigate_source"])

    def test_dirty_without_sessions_is_honest_unknown(self):
        inv = _inventory(a_entry=_entry(
            worktrees=[_wt(dirty=True, dirty_files=4)], sessions=[]))
        ask = next(r for r in _recs(inv) if r["kind"] == "ask_commit")
        self.assertIn("unknown — no session evidence",
                      ask["evidence"]["attribution"])


if __name__ == "__main__":
    unittest.main()
