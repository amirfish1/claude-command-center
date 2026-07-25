"""Two-node orchestration integration: spawn/inject/ask/report/group chat
across machines through global session references — no caller-authored SSH.

Uses the fake Claude CLI (tests/fake_claude.py, selected via CCC_CLAUDE_BIN)
so the full spawn → FIFO inject → ask-resume → report pipeline runs for real
without an actual agent. Covers acceptance scenarios 5 and 6.
"""

import json
import os
import stat
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation
from two_node_harness import TwoNodeFleet

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


def _fabricate_session(home: Path, cwd: str, sid: str):
    slug = federation.encode_project_slug(cwd)
    project_dir = home / ".claude" / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{sid}.jsonl"
    path.write_text(json.dumps({
        "type": "user", "cwd": cwd, "sessionId": sid,
        "message": {"role": "user", "content": "parent session seed"},
    }) + "\n")
    return path


class TestOrchestrationTwoNode(unittest.TestCase):
    fleet: TwoNodeFleet = None

    @classmethod
    def setUpClass(cls):
        os.chmod(FAKE_CLAUDE, os.stat(FAKE_CLAUDE).st_mode | stat.S_IEXEC)
        cls.fleet = TwoNodeFleet()
        fake_env = {"CCC_CLAUDE_BIN": str(FAKE_CLAUDE)}
        cls.fleet.node_a.start(extra_env=fake_env)
        cls.fleet.node_b.start(extra_env=fake_env)
        cls.fleet.node_a.wait_ready()
        cls.fleet.node_b.wait_ready()
        cls.fleet.pair()
        cls.fleet.make_origin_and_clones()
        cls.repo_identity = federation.repo_identity(str(cls.fleet.repo_a))["identity"]
        for node, repo in ((cls.fleet.node_a, cls.fleet.repo_a),
                           (cls.fleet.node_b, cls.fleet.repo_b)):
            node.post("/api/federation/repo-map", {
                "identity": cls.repo_identity, "local_path": str(repo)})
        # A dormant "parent" session on A that remote children report to.
        cls.parent_sid = str(uuid.uuid4())
        _fabricate_session(cls.fleet.node_a.home, str(cls.fleet.repo_a), cls.parent_sid)

    @classmethod
    def tearDownClass(cls):
        cls.fleet.cleanup()

    @property
    def node_a(self):
        return self.fleet.node_a

    @property
    def node_b(self):
        return self.fleet.node_b

    def _b_transcript(self, sid):
        return (self.node_b.home / ".claude" / "projects" /
                federation.encode_project_slug(str(self.fleet.repo_b)) /
                f"{sid}.jsonl")

    # ------------------------------------------------------------------

    def test_01_spawn_on_peer_with_global_return_routing(self):
        status, result = self.node_a.post("/api/sessions/spawn", {
            "prompt": "Do the demo task.",
            "engine": "claude",
            "node": self.node_b.node_id,
            "repo_path": str(self.fleet.repo_a),  # A-local path, translated
            "report_to": self.parent_sid,          # bare id, globalized
            "name": "cross-node-child",
        })
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["node_id"], self.node_b.node_id)
        sid = result.get("session_id")
        self.assertTrue(sid, result)
        self.assertEqual(result["ref"],
                         f"{self.node_b.node_id}:{sid}")
        type(self).child_sid = sid

        # The child runs on B, in B's clone of the repo (path translated via
        # the stable identity — A's path never traveled).
        self.assertEqual(result.get("cwd") or result.get("repo_path"),
                         str(self.fleet.repo_b))
        deadline = time.time() + 10
        transcript = self._b_transcript(sid)
        while time.time() < deadline and not transcript.exists():
            time.sleep(0.2)
        self.assertTrue(transcript.exists(), self.node_b.log_tail())
        body = transcript.read_text()
        # Return address footer carries the GLOBAL parent ref, so the child's
        # completion report survives the machine boundary.
        self.assertIn(f"{self.node_a.node_id}:{self.parent_sid}", body)

    def test_02_inject_via_global_ref(self):
        ref = f"{self.node_b.node_id}:{self.child_sid}"
        status, result = self.node_a.post("/api/inject-input", {
            "session_id": ref, "text": "please ping back",
        })
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("routed_to"), self.node_b.node_id)
        deadline = time.time() + 10
        transcript = self._b_transcript(self.child_sid)
        while time.time() < deadline:
            if "FAKE-PONG" in transcript.read_text():
                break
            time.sleep(0.2)
        self.assertIn("please ping back", transcript.read_text())
        self.assertIn("FAKE-PONG", transcript.read_text())

    def test_03_ask_via_global_ref_returns_reply(self):
        # A dormant session on B (fresh fabricated one) — ask resumes it via
        # the fake CLI and relays the reply across nodes.
        sid = str(uuid.uuid4())
        _fabricate_session(self.node_b.home, str(self.fleet.repo_b), sid)
        ref = f"{self.node_b.node_id}:{sid}"
        status, result = self.node_a.post("/api/ask", {
            "session_id": ref, "text": "ping across the fleet",
            "timeout_ms": 30000,
        })
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"), result)
        self.assertIn("FAKE-PONG", result.get("text") or "")
        self.assertEqual(result.get("routed_to"), self.node_b.node_id)

    def test_04_remote_child_reports_to_local_parent(self):
        # Simulate exactly what the child's footer instructs: a curl to ITS
        # OWN CCC (node B) targeting the global parent ref on node A.
        report = ("STATUS: SUCCEEDED\nSUMMARY: demo task done\n"
                  "FILES: none\n")
        status, result = self.node_b.post("/api/inject-input", {
            "session_id": f"{self.node_a.node_id}:{self.parent_sid}",
            "text": report,
            "announced_from": "cross-node-child",
        })
        self.assertEqual(status, 200, result)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("routed_to"), self.node_a.node_id)
        parent_transcript = (self.node_a.home / ".claude" / "projects" /
                             federation.encode_project_slug(str(self.fleet.repo_a)) /
                             f"{self.parent_sid}.jsonl")
        deadline = time.time() + 10
        while time.time() < deadline:
            if "STATUS: SUCCEEDED" in parent_transcript.read_text():
                break
            time.sleep(0.2)
        self.assertIn("STATUS: SUCCEEDED", parent_transcript.read_text())

    def test_05_unpaired_target_node_is_typed_error(self):
        status, result = self.node_a.post("/api/inject-input", {
            "session_id": f"{str(uuid.uuid4())}:{self.child_sid}",
            "text": "hello?",
        })
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "unpaired_peer")

    def test_06_cross_machine_group_chat(self):
        # Chat hosted on A; one local participant (parent) + one remote
        # participant (the spawned child on B, as a global ref).
        remote_ref = f"{self.node_b.node_id}:{self.child_sid}"
        status, created = self.node_a.post("/api/coordinate", {
            "topic": "cross machine standup",
            "session_ids": [self.parent_sid, remote_ref],
            "include_human": True,
        })
        self.assertTrue(created.get("ok"), created)
        chat_id = created["uuid"]
        by_sid = {r["session_id"]: r for r in created["results"]}
        self.assertTrue(by_sid[remote_ref]["ok"], by_sid)
        type(self).chat_id = chat_id

        # The remote participant received a check-in that references the chat
        # by uuid + host node (its own CCC proxies), never a foreign path.
        child_transcript = self._b_transcript(self.child_sid).read_text()
        self.assertIn(chat_id, child_transcript)
        self.assertIn(self.node_a.node_id, child_transcript)

        # Remote side reads the chat through ITS OWN CCC (proxy to host A).
        status, read = self.node_b.request(
            "GET",
            f"/api/group-chat/read?id={chat_id}&host_node={self.node_a.node_id}")
        self.assertEqual(status, 200, read)
        self.assertTrue(read.get("ok"), read)
        self.assertIn("cross machine standup", read.get("content", ""))
        self.assertEqual(read.get("host_node") or self.node_a.node_id,
                         self.node_a.node_id)

        # Remote side posts through the same proxy; the post lands on A.
        status, posted = self.node_b.post("/api/group-chat/post", {
            "id": chat_id,
            "host_node": self.node_a.node_id,
            "session_id": self.child_sid[:8],
            "name": "remote-child",
            "text": "hello from node B",
        })
        self.assertTrue(posted.get("ok"), posted)
        chat_files = list(self.node_a.group_chats_dir.glob("*.md"))
        self.assertEqual(len(chat_files), 1)
        self.assertIn("hello from node B", chat_files[0].read_text())

        # A targeted nudge from the host reaches the remote participant.
        status, nudged = self.node_a.post("/api/group-chat/nudge", {
            "id": chat_id, "target_sid": remote_ref,
        })
        self.assertTrue(nudged.get("ok"), nudged)
        results = nudged.get("results") or []
        self.assertTrue(any(r.get("ok") for r in results), nudged)

    def test_07_chat_ownership_moves_between_nodes(self):
        # Host the chat on B instead: identity (uuid) survives, the old
        # host's copy becomes a transparent proxy stub.
        status, moved = self.node_a.post("/api/group-chats/move-host", {
            "id": self.chat_id, "node_id": self.node_b.node_id})
        self.assertTrue(moved.get("ok"), moved)
        self.assertEqual(moved["host_node"], self.node_b.node_id)

        # B now physically hosts it.
        b_sidecars = [json.loads(p.read_text())
                      for p in self.node_b.group_chats_dir.glob("*.json")]
        hosted = [s for s in b_sidecars if s.get("uuid") == self.chat_id]
        self.assertEqual(len(hosted), 1)
        self.assertEqual(hosted[0]["host_node"], self.node_b.node_id)

        # Reading through A (no explicit host) transparently proxies to B.
        status, read = self.node_a.request(
            "GET", f"/api/group-chat/read?id={self.chat_id}")
        self.assertEqual(status, 200, read)
        self.assertTrue(read.get("ok"), read)
        self.assertIn("hello from node B", read.get("content", ""))

        # Posting through A lands on B's copy, not the stub.
        status, posted = self.node_a.post("/api/group-chat/post", {
            "id": self.chat_id, "text": "posted after the move",
            "name": "human-a"})
        self.assertTrue(posted.get("ok"), posted)
        b_md = next(p for p in self.node_b.group_chats_dir.glob("*.md")
                    if self.chat_id in
                    json.loads(p.with_suffix(".json").read_text()).get("uuid", ""))
        self.assertIn("posted after the move", b_md.read_text())
        a_md = list(self.node_a.group_chats_dir.glob("*.md"))[0]
        self.assertNotIn("posted after the move", a_md.read_text())

    def test_99_chat_host_offline_is_truthful(self):
        self.node_a.stop()
        time.sleep(0.3)
        status, read = self.node_b.request(
            "GET",
            f"/api/group-chat/read?id={self.chat_id}&host_node={self.node_a.node_id}")
        payload = read if isinstance(read, dict) else {}
        self.assertFalse(payload.get("ok", True), payload)
        self.assertIn(payload.get("error"), ("peer_offline", "timeout"))


if __name__ == "__main__":
    unittest.main()
