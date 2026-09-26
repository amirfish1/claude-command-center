"""Claude re-authentication routed to a paired peer, end to end.

Node A (preview flag on) drives the login on node B (flag off) through the
federation route envelope. Node B runs a real tmux session around a
stand-in `claude` CLI, so the only thing faked is Anthropic's side.
"""

import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from two_node_harness import TwoNodeFleet

GOOD_CODE = "peercode1234567890#statexyz"


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class TestClaudeAuthTwoNode(unittest.TestCase):
    fleet: TwoNodeFleet = None

    @classmethod
    def setUpClass(cls):
        cls.fleet = TwoNodeFleet()
        state = cls.fleet.base / "b-logged-in"
        script = cls.fleet.base / "fake-claude"
        script.write_text(f"""#!/bin/sh
if [ "$1 $2" = "auth login" ]; then
  echo "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=statexyz"
  printf "Paste code here if prompted > "
  read code
  if [ "$code" = "{GOOD_CODE}" ]; then touch "{state}"; echo "Login successful."; else echo "OAuth error: Invalid code"; fi
elif [ "$1 $2" = "auth status" ]; then
  if [ -f "{state}" ]; then echo '{{"loggedIn": true, "email": "peer@example.com"}}'; else echo '{{"loggedIn": false}}'; fi
elif [ "$1" = "-p" ]; then
  echo ok
fi
""")
        script.chmod(0o755)
        cls.fleet.node_a.start(extra_env={"CCC_FF_CLAUDE_REAUTH": "1"})
        cls.fleet.node_b.start(extra_env={
            "CCC_CLAUDE_BIN": str(script),
            "CCC_CLAUDE_AUTH_TMUX_SESSION": "ccc-claude-auth-e2e-" + uuid.uuid4().hex[:6],
        })
        cls.fleet.node_a.wait_ready()
        cls.fleet.node_b.wait_ready()
        cls.fleet.pair()
        cls.b_id = cls.fleet.node_b.get("/api/federation/v1/hello")["node_id"]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.fleet.node_b.post("/api/claude-auth/cancel", {"via_route": True})
        finally:
            cls.fleet.cleanup()

    def test_routed_login_flow(self):
        a, b = self.fleet.node_a, self.fleet.node_b
        # B's own browser surface is gated by B's (off) preview flag...
        status, payload = b.post("/api/claude-auth/start", {}, expect_error=True)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "feature_disabled")

        before = a.get(f"/api/claude-auth/status?node_id={self.b_id}")
        self.assertFalse(before["status"]["logged_in"], before)

        # ...but a routed call from paired A runs there.
        _, started = a.post("/api/claude-auth/start", {"node_id": self.b_id})
        self.assertTrue(started.get("ok"), started)
        self.assertTrue(started["url"].startswith("https://claude.com/cai/oauth/authorize?"))
        self.assertEqual(started.get("routed_to"), self.b_id)

        _, done = a.post("/api/claude-auth/submit", {
            "node_id": self.b_id, "attempt_id": started["attempt_id"], "code": GOOD_CODE})
        self.assertTrue(done.get("ok"), done)
        self.assertEqual(done["email"], "peer@example.com")
        self.assertTrue(done["smoke"]["ok"])

        after = a.get(f"/api/claude-auth/status?node_id={self.b_id}")
        self.assertTrue(after["status"]["logged_in"], after)
        self.assertEqual(after["attempt"]["state"], "succeeded")

        _, nudged = a.post("/api/claude-auth/nudge", {
            "node_id": self.b_id, "session_ids": ["00000000-0000-0000-0000-000000000000"]})
        self.assertTrue(nudged.get("ok"), nudged)
        self.assertEqual(len(nudged["results"]), 1)

        log = (self.fleet.node_b.state_dir / "logs" / "activity.log")
        if log.exists():
            self.assertNotIn(GOOD_CODE.split("#")[0], log.read_text(errors="replace"))


if __name__ == "__main__":
    unittest.main()
