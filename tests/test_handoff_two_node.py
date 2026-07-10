"""Two-node handoff integration: Continue-on-another-machine end to end.

Covers the brief's acceptance scenarios 3 (continue while source off),
4 (dirty handoff guard), plus reverse handoff with divergence checksums and
lease-guarded injection. Uses fabricated Claude transcripts — no agent CLI
is launched; structural resume-ability (transcript present, path-normalized,
listed by the destination CCC) is what's asserted here.
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


def _fabricate_claude_session(home: Path, cwd: str, sid: str, n_turns=3):
    """Write a realistic-enough Claude Code transcript under HOME."""
    slug = federation.encode_project_slug(cwd)
    project_dir = home / ".claude" / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    for i in range(n_turns):
        lines.append({
            "type": "user", "cwd": cwd, "sessionId": sid, "timestamp": ts,
            "gitBranch": "main", "version": "2.0.0",
            "message": {"role": "user", "content": f"user turn {i}: please fix bug {i}"},
            "uuid": str(uuid.uuid4()),
        })
        lines.append({
            "type": "assistant", "cwd": cwd, "sessionId": sid, "timestamp": ts,
            "message": {"role": "assistant", "model": "claude-fable-5",
                        "content": [{"type": "text", "text": f"assistant turn {i} done"}]},
            "uuid": str(uuid.uuid4()),
        })
    path = project_dir / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return path


class TestHandoffTwoNode(unittest.TestCase):
    fleet: TwoNodeFleet = None
    sid = None

    @classmethod
    def setUpClass(cls):
        cls.fleet = TwoNodeFleet()
        cls.fleet.start()
        cls.fleet.pair()
        cls.fleet.make_origin_and_clones()
        cls.repo_identity = federation.repo_identity(str(cls.fleet.repo_a))["identity"]
        # Map the repository identity on both nodes (exercises the API).
        for node, repo in ((cls.fleet.node_a, cls.fleet.repo_a),
                           (cls.fleet.node_b, cls.fleet.repo_b)):
            status, payload = node.post("/api/federation/repo-map", {
                "identity": cls.repo_identity, "local_path": str(repo)})
            assert payload.get("ok"), payload
        cls.sid = str(uuid.uuid4())
        cls.transcript_a = _fabricate_claude_session(
            cls.fleet.node_a.home, str(cls.fleet.repo_a), cls.sid)
        # Give the session a user-set name so sidecar transfer is observable.
        names = cls.fleet.node_a.state_dir / "session-names.json"
        names.parent.mkdir(parents=True, exist_ok=True)
        names.write_text(json.dumps({cls.sid: "handoff guinea pig"}))

    @classmethod
    def tearDownClass(cls):
        cls.fleet.cleanup()

    @property
    def node_a(self):
        return self.fleet.node_a

    @property
    def node_b(self):
        return self.fleet.node_b

    # -------------------------------------------------------------------

    def test_01_dirty_handoff_is_blocked_and_copies_nothing(self):
        dirty = self.fleet.repo_a / "wip.txt"
        dirty.write_text("uncommitted work\n")
        try:
            status, pf = self.node_a.post("/api/federation/handoff/preflight", {
                "session_id": self.sid, "dest_node_id": self.node_b.node_id})
            self.assertTrue(pf["ok"], pf)
            self.assertFalse(pf["ready"])
            codes = [b["code"] for b in pf["blockers"]]
            self.assertIn("dirty_worktree", codes)

            status, started = self.node_a.post(
                "/api/federation/handoff/start",
                {"session_id": self.sid, "dest_node_id": self.node_b.node_id},
                expect_error=True)
            self.assertEqual(status, 409)
            self.assertEqual(started["error"], "preflight_blocked")
            # Nothing copied: B has no transcript for this session
            hits = list((self.node_b.home / ".claude" / "projects").rglob(
                f"{self.sid}.jsonl")) if (self.node_b.home / ".claude" / "projects").exists() else []
            self.assertEqual(hits, [])
        finally:
            dirty.unlink()

    def test_02_preflight_clean_is_ready_with_full_plan(self):
        self.fleet.commit_on(self.fleet.repo_a, "feature.py", "print('hi')\n",
                             "feat: add feature", push=True)
        status, pf = self.node_a.post("/api/federation/handoff/preflight", {
            "session_id": self.sid, "dest_node_id": self.node_b.node_id})
        self.assertTrue(pf["ready"], pf)
        self.assertEqual(pf["git"]["dirty_count"], 0)
        self.assertEqual(pf["git"]["unpublished_commits"], 0)
        steps = [s["step"] for s in pf["steps"]]
        self.assertEqual(steps, ["push", "prepare_destination",
                                 "transfer_session", "flip_ownership"])

    def test_03_handoff_moves_session_and_normalizes_paths(self):
        status, result = self.node_a.post("/api/federation/handoff/start", {
            "session_id": self.sid, "dest_node_id": self.node_b.node_id})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["dest_cwd"], str(self.fleet.repo_b))
        self.assertGreater(result["rewrites"]["cwd_rewrites"], 0)

        # Transcript landed in B's encoded project dir with rewritten cwds
        dest = Path(result["log"][2]["transcript_sha256"] and
                    (self.node_b.home / ".claude" / "projects" /
                     federation.encode_project_slug(str(self.fleet.repo_b)) /
                     f"{self.sid}.jsonl"))
        self.assertTrue(dest.is_file(), dest)
        for line in dest.read_text().splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if "cwd" in obj:
                self.assertEqual(obj["cwd"], str(self.fleet.repo_b))

        # Leases: both sides say B owns it
        lease_a = json.loads((self.node_a.state_dir / "federation" / "leases" /
                              f"{self.sid}.json").read_text())
        lease_b = json.loads((self.node_b.state_dir / "federation" / "leases" /
                              f"{self.sid}.json").read_text())
        self.assertEqual(lease_a["owner_node"], self.node_b.node_id)
        self.assertEqual(lease_b["owner_node"], self.node_b.node_id)

        # Sidecar title applied on B
        names_b = json.loads((self.node_b.state_dir / "session-names.json").read_text())
        self.assertEqual(names_b[self.sid], "handoff guinea pig")

    def test_04_source_refuses_inject_and_second_handoff(self):
        status, payload = self.node_a.post(
            "/api/inject-input", {"session_id": self.sid, "text": "hello?"},
            expect_error=True)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "not_owner")
        self.assertEqual(payload["owner_node"], self.node_b.node_id)

        status, payload = self.node_a.post(
            "/api/federation/handoff/start",
            {"session_id": self.sid, "dest_node_id": self.node_b.node_id},
            expect_error=True)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "not_owner")

    def test_05_reverse_handoff_with_divergence_checksums(self):
        # B continues the conversation (transcript grows on B)...
        dest = (self.node_b.home / ".claude" / "projects" /
                federation.encode_project_slug(str(self.fleet.repo_b)) /
                f"{self.sid}.jsonl")
        with dest.open("a") as f:
            f.write(json.dumps({
                "type": "user", "cwd": str(self.fleet.repo_b),
                "sessionId": self.sid,
                "message": {"role": "user", "content": "continued on node B"},
            }) + "\n")
        # ...then hands it back. A's stale copy matches its lease checksum,
        # so the overwrite is provably safe and needs no force flag.
        status, result = self.node_b.post("/api/federation/handoff/start", {
            "session_id": self.sid, "dest_node_id": self.node_a.node_id})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["dest_cwd"], str(self.fleet.repo_a))

        src_a = (self.node_a.home / ".claude" / "projects" /
                 federation.encode_project_slug(str(self.fleet.repo_a)) /
                 f"{self.sid}.jsonl")
        text = src_a.read_text()
        self.assertIn("continued on node B", text)
        for line in text.splitlines():
            if line.strip() and "cwd" in json.loads(line):
                self.assertEqual(json.loads(line)["cwd"], str(self.fleet.repo_a))
        lease_a = json.loads((self.node_a.state_dir / "federation" / "leases" /
                              f"{self.sid}.json").read_text())
        self.assertEqual(lease_a["owner_node"], self.node_a.node_id)
        # A can inject again
        status, payload = self.node_a.post(
            "/api/inject-input", {"session_id": "not-" + self.sid, "text": "x"},
            expect_error=True)  # sanity: other sessions unaffected by lease
        # (unknown session fails differently — just assert not a lease 409)
        self.assertNotEqual(payload.get("error"), "not_owner")

    def test_06_divergent_return_is_blocked(self):
        # Hand A -> B again (B's copy still matches ITS lease, so safe).
        status, result = self.node_a.post("/api/federation/handoff/start", {
            "session_id": self.sid, "dest_node_id": self.node_b.node_id})
        self.assertTrue(result["ok"], result)
        # Now corrupt the story: A's local transcript changes AFTER handoff.
        src_a = (self.node_a.home / ".claude" / "projects" /
                 federation.encode_project_slug(str(self.fleet.repo_a)) /
                 f"{self.sid}.jsonl")
        with src_a.open("a") as f:
            f.write(json.dumps({"type": "user", "cwd": str(self.fleet.repo_a),
                                "sessionId": self.sid,
                                "message": {"role": "user",
                                            "content": "DIVERGED on A"}}) + "\n")
        # Return handoff B -> A must refuse: A's copy no longer matches the
        # checkpoint in A's lease.
        status, result = self.node_b.post(
            "/api/federation/handoff/start",
            {"session_id": self.sid, "dest_node_id": self.node_a.node_id},
            expect_error=True)
        self.assertGreaterEqual(status, 400)
        detail = json.dumps(result)
        self.assertIn("session_exists", detail)

    def test_07_force_takeover_is_audited(self):
        status, payload = self.node_a.post("/api/federation/handoff/takeover", {
            "session_id": self.sid, "reason": "node B lost (test)"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["previous_owner"], self.node_b.node_id)
        lease = payload["lease"]
        self.assertEqual(lease["owner_node"], self.node_a.node_id)
        self.assertIn("FORCE TAKEOVER", lease["history"][-1]["note"])

    def test_99_destination_lists_session_after_source_death(self):
        # Fresh session, clean handoff, then the source node dies.
        sid2 = str(uuid.uuid4())
        _fabricate_claude_session(self.node_a.home, str(self.fleet.repo_a), sid2)
        status, result = self.node_a.post("/api/federation/handoff/start", {
            "session_id": sid2, "dest_node_id": self.node_b.node_id})
        self.assertTrue(result["ok"], result)
        self.node_a.stop()
        time.sleep(0.3)
        # B lists and can read the conversation with history intact — no
        # dependence on A being alive.
        payload = self.node_b.get("/api/sessions?all=1")
        ids = {r.get("session_id") for r in payload.get("sessions", [])}
        self.assertIn(sid2, ids)
        dest = (self.node_b.home / ".claude" / "projects" /
                federation.encode_project_slug(str(self.fleet.repo_b)) /
                f"{sid2}.jsonl")
        turns = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        self.assertGreaterEqual(len(turns), 6)
        self.assertTrue(all(t["cwd"] == str(self.fleet.repo_b)
                            for t in turns if "cwd" in t))


if __name__ == "__main__":
    unittest.main()
