"""Two-node fleet executor integration — acceptance scenarios 2 (reviewed
push + pull), 7 (safe merged cleanup), 8 (unmerged preservation), 10
(attribution honesty), 11 (restart and retry without repeated mutations).
"""

import json
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation
from two_node_harness import TwoNodeFleet, git


def _wait_job(node, plan_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = node.get(f"/api/fleet/job?id={plan_id}")
        if payload["job"].get("status") == "finished":
            return payload["job"]
        time.sleep(0.4)
    raise AssertionError(f"job {plan_id} did not finish: {payload}")


class TestFleetExecutorTwoNode(unittest.TestCase):
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

    def _plan(self):
        status, payload = self.fleet.node_a.post("/api/fleet/plan", {})
        self.assertTrue(payload["ok"], payload)
        return payload["plan"]

    def _actions_of(self, plan, kind, node_id=None):
        return [a for a in plan["actions"] if a["kind"] == kind
                and (node_id is None or a["node_id"] == node_id)]

    def _origin_main_sha(self):
        return git(self.fleet.origin, "rev-parse", "main").stdout.strip()

    # ------------------------------------------------------------------

    def test_01_reviewed_push_then_pull_scenario_2(self):
        sha = self.fleet.commit_on(self.fleet.repo_b, "b-work.txt",
                                   "node B work\n", "feat: b work", push=False)
        plan = self._plan()
        pushes = self._actions_of(plan, "push", self.fleet.node_b.node_id)
        self.assertEqual(len(pushes), 1, plan["actions"])
        self.assertEqual(pushes[0]["status"], "proposed")
        self.assertIn("unreachable from origin", pushes[0]["reason"])

        status, payload = self.fleet.node_a.post("/api/fleet/execute", {
            "plan_id": plan["plan_id"], "selected": [pushes[0]["id"]]})
        self.assertTrue(payload["ok"], payload)
        job = _wait_job(self.fleet.node_a, plan["plan_id"])
        push_action = next(a for a in job["actions"] if a["kind"] == "push")
        self.assertEqual(push_action["status"], "done", push_action)
        # Code traveled via git only: origin now has B's commit.
        self.assertEqual(self._origin_main_sha(), sha)

        # Second plan now offers the pull on node A (its clone is behind).
        plan2 = self._plan()
        pulls = self._actions_of(plan2, "pull_ff", self.fleet.node_a.node_id)
        self.assertEqual(len(pulls), 1, [a["kind"] for a in plan2["actions"]])
        status, payload = self.fleet.node_a.post("/api/fleet/execute", {
            "plan_id": plan2["plan_id"], "selected": [pulls[0]["id"]]})
        self.assertTrue(payload["ok"], payload)
        _wait_job(self.fleet.node_a, plan2["plan_id"])
        self.assertEqual(git(self.fleet.repo_a, "rev-parse", "HEAD").stdout.strip(),
                         sha)
        type(self).pushed_sha = sha

    def test_02_merged_worktree_cleanup_scenario_7(self):
        # A worktree whose branch gets merged into origin/main.
        wt = Path(str(self.fleet.repo_a) + "-wt-featx")
        git(self.fleet.repo_a, "worktree", "add", str(wt), "-b", "feat/x")
        (wt / "featx.txt").write_text("feature x\n")
        git(wt, "add", "featx.txt")
        git(wt, "commit", "-q", "-m", "feat: x")
        git(wt, "push", "-q", "-u", "origin", "feat/x")
        git(self.fleet.repo_a, "merge", "-q", "--no-edit", "feat/x")
        git(self.fleet.repo_a, "push", "-q", "origin", "main")

        plan = self._plan()
        removals = self._actions_of(plan, "remove_worktree",
                                    self.fleet.node_a.node_id)
        self.assertEqual(len(removals), 1, [a["kind"] for a in plan["actions"]])
        removal = removals[0]
        self.assertEqual(removal["target"], str(wt))
        self.assertTrue(removal["destructive"])
        self.assertIn("provably reachable", removal["reason"])
        self.assertIn("merge-base --is-ancestor",
                      removal["evidence"]["proof"])

        status, payload = self.fleet.node_a.post("/api/fleet/execute", {
            "plan_id": plan["plan_id"], "selected": [removal["id"]]})
        self.assertTrue(payload["ok"], payload)
        job = _wait_job(self.fleet.node_a, plan["plan_id"])
        act = next(a for a in job["actions"] if a["kind"] == "remove_worktree")
        self.assertEqual(act["status"], "done", act)
        self.assertFalse(wt.exists())
        # Branch deletion is a separate explicit action — branch survives.
        rc = git(self.fleet.repo_a, "rev-parse", "--verify", "feat/x",
                 check=False).returncode
        self.assertEqual(rc, 0)

    def test_03_unmerged_worktree_preserved_scenario_8(self):
        wt = Path(str(self.fleet.repo_a) + "-wt-featy")
        git(self.fleet.repo_a, "worktree", "add", str(wt), "-b", "feat/y")
        (wt / "unfinished.txt").write_text("unmerged work\n")
        git(wt, "add", "unfinished.txt")
        git(wt, "commit", "-q", "-m", "wip: y")  # never pushed, never merged

        plan = self._plan()
        removals = [a for a in self._actions_of(plan, "remove_worktree")
                    if a["target"] == str(wt)]
        self.assertEqual(removals, [], "unmerged worktree must never be removable")
        finishes = [a for a in self._actions_of(plan, "finish_worktree")
                    if a["target"] == str(wt)]
        self.assertEqual(len(finishes), 1)
        self.assertEqual(finishes[0]["status"], "blocked")

        # Selecting the blocked action is refused outright.
        status, payload = self.fleet.node_a.post(
            "/api/fleet/execute",
            {"plan_id": plan["plan_id"], "selected": [finishes[0]["id"]]},
            expect_error=True)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "action_blocked")

        # Even a FORGED direct removal step is stopped by fresh revalidation.
        status, payload = self.fleet.node_a.post("/api/fleet/step", {
            "action": {"kind": "remove_worktree",
                       "repo_identity": self.identity,
                       "node_id": self.fleet.node_a.node_id,
                       "target": str(wt)}})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "revalidation_failed")
        self.assertTrue(wt.exists())
        type(self).wt_unmerged = wt

    def test_04_dirty_worktree_never_deleted(self):
        wt = self.wt_unmerged
        (wt / "dirty.txt").write_text("dirty\n")
        status, payload = self.fleet.node_a.post("/api/fleet/step", {
            "action": {"kind": "remove_worktree",
                       "repo_identity": self.identity,
                       "node_id": self.fleet.node_a.node_id,
                       "target": str(wt)}})
        self.assertFalse(payload["ok"])
        self.assertIn("dirty", payload["detail"])
        self.assertTrue(wt.exists())
        (wt / "dirty.txt").unlink()

    def test_05_attribution_honesty_scenario_10(self):
        repo = str(self.fleet.repo_a)
        target = self.fleet.repo_a / "attributed.py"
        target.write_text("# edited by a session\n")

        # A session whose transcript shows an Edit tool_use on the file
        # (strong evidence) and whose cwd is the repo.
        sid_strong = str(uuid.uuid4())
        slug = federation.encode_project_slug(repo)
        pdir = self.fleet.node_a.home / ".claude" / "projects" / slug
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / f"{sid_strong}.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "cwd": repo, "sessionId": sid_strong,
                        "message": {"role": "user", "content": "edit the file"}}),
            json.dumps({"type": "assistant", "cwd": repo, "sessionId": sid_strong,
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Edit",
                             "input": {"file_path": str(target)}}]}}),
        ]) + "\n")
        # A second session in the same repo with only cwd-level evidence.
        sid_weak = str(uuid.uuid4())
        (pdir / f"{sid_weak}.jsonl").write_text(json.dumps({
            "type": "user", "cwd": repo, "sessionId": sid_weak,
            "message": {"role": "user", "content": "unrelated work"}}) + "\n")

        # The session list behind attribution is cache-backed (2s serve TTL)
        # — poll briefly until the freshly-written transcripts are visible.
        deadline = time.time() + 12
        result = None
        while time.time() < deadline:
            status, result = self.fleet.node_a.post("/api/fleet/attribute", {
                "repo_path": repo, "path": str(target)})
            if result.get("ok") and not result.get("unknown"):
                break
            time.sleep(1.0)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["unknown"], result)
        by_sid = {c["session_id"]: c for c in result["candidates"]}
        self.assertIn(sid_strong, by_sid)
        self.assertEqual(by_sid[sid_strong]["confidence"], "high")
        kinds = {e["kind"] for e in by_sid[sid_strong]["evidence"]}
        self.assertIn("transcript_tool_path", kinds)
        # Shared/ambiguous work lists MULTIPLE candidates, ranked.
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["session_id"], sid_strong)
        weak = by_sid.get(sid_weak)
        self.assertIsNotNone(weak)
        self.assertIn(weak["confidence"], ("medium", "low"))

        # Missing evidence is labeled unknown — never a fabricated owner.
        # A file in a worktree NO session works in, old enough that even
        # timestamp correlation cannot fire.
        orphan = self.wt_unmerged / "nobody-touched.py"
        orphan.write_text("# untouched\n")
        import os
        old = time.time() - 86400
        os.utime(orphan, (old, old))
        status, result = self.fleet.node_a.post("/api/fleet/attribute", {
            "repo_path": repo, "path": str(orphan)})
        self.assertTrue(result["unknown"], result)
        self.assertEqual(result["candidates"], [])
        target.unlink()
        orphan.unlink()

    def test_06_restart_and_retry_scenario_11(self):
        # A push that ALREADY completed externally but whose step is still
        # marked running (crash after the external mutation) must not be
        # repeated after restart — the executor detects the end state.
        git(self.fleet.repo_b, "pull", "-q", "--ff-only", "origin", "main")
        sha = self.fleet.commit_on(self.fleet.repo_b, "b-work-2.txt",
                                   "more node B work\n", "feat: b work 2",
                                   push=True)  # external effect already done
        origin_before = self._origin_main_sha()
        self.assertEqual(origin_before, sha)

        plan = self._plan()  # fresh plan (no push needed anymore, but we
        # simulate a stale interrupted job that still wants one)
        job_path = (self.fleet.node_a.state_dir / "fleet-jobs" /
                    f"{plan['plan_id']}.json")
        job = json.loads(job_path.read_text())
        job["status"] = "confirmed"
        job["actions"] = [{
            "id": f"push:{self.identity}:x:main",
            "kind": "push",
            "repo_identity": self.identity,
            "node_id": self.fleet.node_b.node_id,
            "node_name": "node-b",
            "target": "main",
            "status": "running",  # crashed mid-step
            "evidence": {}, "blockers": [], "requires": [],
        }]
        job_path.write_text(json.dumps(job))

        # Restart node A (the coordinator), then resume the interrupted job.
        self.fleet.node_a.stop()
        self.fleet.node_a.start()
        self.fleet.node_a.wait_ready()

        status, payload = self.fleet.node_a.post("/api/fleet/job/resume", {
            "plan_id": plan["plan_id"]})
        self.assertTrue(payload["ok"], payload)
        job = _wait_job(self.fleet.node_a, plan["plan_id"])
        act = job["actions"][0]
        self.assertEqual(act["status"], "done", act)
        self.assertTrue(act["result"].get("already"),
                        "resume must detect the already-satisfied end state")
        # No repeated external mutation: origin head unchanged.
        self.assertEqual(self._origin_main_sha(), origin_before)


if __name__ == "__main__":
    unittest.main()
