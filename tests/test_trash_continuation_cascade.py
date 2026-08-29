"""Continuation-ancestor discovery for the trash cascade.

A "Continue in a new session" / auto-resume session records its origin in
its transcript's first user prompt as "Origin session id: <sid>". Trashing a
successor session should cascade over that continuation-origin ancestor
chain too (not just spawn descendants), so the UI's nested continuation
group trashes as one unit. This covers `_find_continuation_ancestors` in
isolation — no server spawn, no real ~/.claude/projects scan.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import server


def _write_transcript(path, origin_session_id=None):
    """Write a minimal transcript whose first user event optionally embeds
    an "Origin session id: <sid>" continuation marker."""
    text = (
        f"Origin session id: {origin_session_id}\n\ncontinue please"
        if origin_session_id
        else "hello, a normal first prompt"
    )
    event = {
        "type": "user",
        "message": {"role": "user", "content": text},
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


class TestFindContinuationAncestors(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self._orig_find_session_jsonl = server._find_session_jsonl
        self._orig_find_session_jsonl_any_project = (
            server._find_session_jsonl_any_project
        )
        self._paths = {}
        server._find_session_jsonl = self._fake_find_session_jsonl
        server._find_session_jsonl_any_project = self._fake_find_session_jsonl
        self.addCleanup(self._restore)

    def _restore(self):
        server._find_session_jsonl = self._orig_find_session_jsonl
        server._find_session_jsonl_any_project = (
            self._orig_find_session_jsonl_any_project
        )

    def _fake_find_session_jsonl(self, session_id):
        return self._paths.get(session_id)

    def _register(self, sid, origin_session_id=None):
        path = self.tmp_dir / f"{sid}.jsonl"
        _write_transcript(path, origin_session_id)
        self._paths[sid] = path

    def test_single_hop_continuation(self):
        self._register("origin-session-01")
        self._register("successor-session-01", origin_session_id="origin-session-01")

        self.assertEqual(
            server._find_continuation_ancestors("successor-session-01"),
            ["origin-session-01"],
        )

    def test_multi_hop_chain_returns_all_ancestors(self):
        self._register("origin-session-01")
        self._register("mid-session-01", origin_session_id="origin-session-01")
        self._register("successor-session-01", origin_session_id="mid-session-01")

        result = server._find_continuation_ancestors("successor-session-01")
        self.assertEqual(result, ["mid-session-01", "origin-session-01"])

    def test_no_continuation_marker_returns_empty(self):
        self._register("standalone-session-01")

        self.assertEqual(
            server._find_continuation_ancestors("standalone-session-01"), []
        )

    def test_missing_transcript_returns_empty(self):
        # No entry registered at all for this sid.
        self.assertEqual(server._find_continuation_ancestors("ghost-session-01"), [])

    def test_cycle_terminates(self):
        self._register("session-a-loop-01", origin_session_id="session-b-loop-01")
        self._register("session-b-loop-01", origin_session_id="session-a-loop-01")

        result = server._find_continuation_ancestors("session-a-loop-01")
        # Must terminate (not hang / infinite loop) and never include the
        # starting sid itself.
        self.assertNotIn("session-a-loop-01", result)
        self.assertLessEqual(len(result), 2)

    def test_missing_sid_returns_empty(self):
        self.assertEqual(server._find_continuation_ancestors(""), [])
        self.assertEqual(server._find_continuation_ancestors(None), [])


if __name__ == "__main__":
    unittest.main()
