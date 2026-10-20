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


class OtherEnginesTest(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self._old = {k: os.environ.get(k) for k in ("HOME",)}
        os.environ["HOME"] = self.home
        qt._files.clear()
        qt._loaded = True  # skip on-disk cache
        qt._save_cache = lambda: None

    def tearDown(self):
        import os
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        qt._files.clear()
        self.tmp.cleanup()

    def _write(self, rel, lines):
        import json, os
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(l) for l in lines))
        return p

    def test_codex_and_kimi_files(self):
        sid = "01a148e0-3755-7dd1-96e3-2ebe46065826"
        self._write(f".codex/sessions/2026/10/17/rollout-2026-10-17T08-00-16-{sid}.jsonl", [
            {"type": "response_item", "timestamp": "2026-10-17T08:00:00Z", "payload": {
                "type": "message", "role": "user", "id": "i",
                "content": [{"type": "input_text", "text": "## Owner question tags (all engines)\n[Q33:gap-month]"}]}},
            {"type": "response_item", "timestamp": "2026-10-17T08:00:01Z", "payload": {
                "type": "message", "role": "assistant", "id": "a",
                "content": [{"type": "output_text", "text": "## [Q40:codex-q] why?"}]}},
            {"type": "event_msg", "timestamp": "2026-10-17T08:00:01Z", "payload": {"type": "agent_message", "message": "[Q40:codex-q] dup"}},
        ])
        self._write(".kimi-code/sessions/wd_x_1/session_abc/agents/main/wire.jsonl", [
            {"type": "agent.message.appended", "time": 1789703147046, "message": {"message": {
                "role": "assistant", "message_id": "m1", "content": [{"type": "text", "text": "[Q41:kimi-q] ok?"}]}}},
            {"type": "context.append_message", "time": 1789703148046, "message": {
                "role": "user", "content": [{"type": "text", "text": "[Q41:kimi-q] yes"}]}},
        ])
        qt.refresh(force=True)
        qs = {q["n"]: q for q in qt.build_index()["questions"]}
        self.assertEqual(sorted(qs), [40, 41])
        self.assertEqual(qs[40]["first"]["sid"], sid)
        self.assertEqual(len(qs[40]["mentions"]), 0)
        self.assertEqual(qs[41]["first"]["sid"], "session_abc")
        self.assertTrue(qs[41]["answered"])

    def test_db_stores_are_incremental(self):
        import json, os, sqlite3
        hp = os.path.join(self.home, ".hermes", "state.db")
        os.makedirs(os.path.dirname(hp))
        con = sqlite3.connect(hp)
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('hs','assistant','[Q50:herm] ask',1790000000.0)")
        con.commit()
        dp = os.path.join(self.home, ".local/share/devin/cli/sessions.db")
        os.makedirs(os.path.dirname(dp))
        d = sqlite3.connect(dp)
        d.execute("CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, chat_message TEXT, created_at INTEGER)")
        d.execute("INSERT INTO message_nodes (session_id, chat_message, created_at) VALUES ('ds', ?, 1790000001)",
                  (json.dumps({"role": "assistant", "content": "[Q51:dev] ask", "message_id": "x"}),))
        d.execute("INSERT INTO message_nodes (session_id, chat_message, created_at) VALUES ('ds', ?, 1790000002)",
                  (json.dumps({"role": "user", "content": "[Q51:dev] injected", "metadata": {}}),))
        d.commit()
        qt.refresh(force=True)
        qs = {q["n"]: q for q in qt.build_index()["questions"]}
        self.assertEqual(sorted(qs), [50, 51])
        self.assertFalse(qs[51]["answered"])  # non-input user rows are ignored
        # A second pass with nothing new must not re-read; a new row is picked up once.
        con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('hs','user','[Q50:herm] yes',1790000005.0)")
        con.commit()
        qt.refresh(force=True)
        qs = {q["n"]: q for q in qt.build_index()["questions"]}
        self.assertTrue(qs[50]["answered"])
        self.assertEqual(len(qt._files["hermes:" + hp]["ev"]), 2)


if __name__ == "__main__":
    unittest.main()
