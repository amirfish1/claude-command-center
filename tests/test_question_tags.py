import unittest

from ccc_server import question_tags as qt


def ev(role, ts, text, uuid):
    return {"role": role, "ts": ts, "text": text, "uuid": uuid, "sid": "s1"}


class BuildIndexTest(unittest.TestCase):
    def test_first_mention_is_explanation_and_owner_reply_answers(self):
        files = {"a": {"ev": [
            ev("assistant", "2026-01-01T00:00:02Z", "[Q7:x] reminder", "u2"),
            ev("assistant", "2026-01-01T00:00:01Z", "## [Q7:x] full explanation", "u1"),
            ev("assistant", "2026-01-01T00:00:03Z", "## [Q8:y] other", "u3"),
            ev("user", "2026-01-01T00:00:04Z", "re Q7 [Q7:x] yes", "u4"),
        ]}}
        qs = {q["n"]: q for q in qt.build_index(files)["questions"]}
        self.assertEqual(qs[7]["first"]["uuid"], "u1")
        self.assertEqual(len(qs[7]["mentions"]), 1)
        self.assertTrue(qs[7]["answered"])
        self.assertFalse(qs[8]["answered"])

    def test_agent_answered_marker(self):
        files = {"a": {"ev": [
            ev("assistant", "2026-01-01T00:00:01Z", "[Q9:z] ask", "u1"),
            ev("assistant", "2026-01-01T00:00:02Z", "[Q9] answered", "u2"),
        ]}}
        self.assertTrue(qt.build_index(files)["questions"][0]["answered"])

    def test_scan_file_ignores_tool_use_and_untagged(self):
        import json, tempfile, os
        lines = [
            {"type": "assistant", "uuid": "a", "message": {"content": [{"type": "tool_use", "input": {"c": "[Q1:x]"}}]}},
            {"type": "assistant", "uuid": "b", "message": {"content": [{"type": "text", "text": "[Q1:x] hi"}]}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(l) for l in lines))
        try:
            out = qt.scan_file(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual([e["uuid"] for e in out], ["b"])


if __name__ == "__main__":
    unittest.main()
