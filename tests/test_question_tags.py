import unittest

from ccc_server import question_tags as qt


def ev(role, ts, text, uuid):
    return {"role": role, "ts": ts, "text": text, "uuid": uuid, "sid": "s1"}


def ticket(ref="OPS-1", question="[Q7:publish] OK to publish?", **fields):
    item = {"ref": ref, "title": "Publish", "status": "in_progress", "needs_input": True,
            "block_question": question, "blocked_at": "2026-01-01T00:00:01Z",
            "claimed_session_id": "worker", "history": []}
    item.update(fields)
    return item


def question(n=7, answered=False):
    return {"n": n, "tag": f"[Q{n}:publish]", "answered": answered,
            "answered_by": "owner" if answered else ""}


class TicketLinkTest(unittest.TestCase):
    def test_blocked_ticket_keeps_its_question_open_after_a_chat_mention(self):
        q = question(answered=True)
        qt.apply_tickets([q], [ticket()])
        self.assertFalse(q["answered"])
        self.assertEqual(q["ticket"], {"ref": "OPS-1", "title": "Publish", "open": True,
                                       "session_id": "worker"})

    def test_unblocked_ticket_answers_its_past_question(self):
        q = question()
        past = ticket(needs_input=False, block_question="", history=[
            {"event": "block", "at": "2026-01-01T00:00:01Z", "question": "[Q7:publish] OK?"}])
        qt.apply_tickets([q], [past])
        self.assertTrue(q["answered"])
        self.assertEqual(q["answered_by"], "ticket")
        self.assertFalse(q["ticket"]["open"])

    def test_closed_ticket_is_not_waiting_even_with_a_stale_flag(self):
        q = question()
        qt.apply_tickets([q], [ticket(status="closed")])
        self.assertNotIn("ticket", q)
        self.assertFalse(q["answered"])

    def test_a_question_only_mentioned_by_a_ticket_stays_unlinked(self):
        q = question()
        qt.apply_tickets([q], [ticket(question="[Q9:other] Same call as [Q7:publish]?")])
        self.assertNotIn("ticket", q)

    def test_untagged_questions_keep_the_transcript_verdict(self):
        q = question(n=8, answered=True)
        qt.apply_tickets([q], [ticket()])
        self.assertTrue(q["answered"])
        self.assertEqual(q["answered_by"], "owner")

    def test_waiting_ticket_wins_when_a_question_spans_tickets(self):
        past = ticket("OPS-2", needs_input=False, block_question="", history=[
            {"event": "block", "at": "2026-02-01T00:00:00Z", "question": "[Q7:publish] OK?"}])
        for items in ([past, ticket()], [ticket(), past]):
            self.assertEqual(qt.ticket_links(list(items))[7]["ref"], "OPS-1")

    def test_parked_session_is_the_one_to_talk_to(self):
        link = qt.ticket_links([ticket(status="blocked", claimed_session_id=None,
                                       parked={"session_id": "parked"})])[7]
        self.assertEqual(link["session_id"], "parked")


class BuildIndexTest(unittest.TestCase):
    def test_bundled_guidance_is_neither_a_question_nor_an_answer(self):
        from ccc_server.question_defaults import guidance
        instruction = "Agent context:\n" + guidance() + "\nExample: [Q42:install-scope]"
        ask = ev("assistant", "2026-01-01T00:00:01Z",
                 "[Q42:install-scope] Which scope?", "ask")
        for role in ("user", "assistant"):
            injected = ev(role, "2026-01-01T00:00:02Z", instruction, "instructions")
            self.assertIsNone(qt._event(role, instruction, "instructions", injected["ts"], "s1"))
            questions = qt.build_index({"a": {"ev": [ask, injected]}})["questions"]
            self.assertEqual(len(questions), 1)
            self.assertFalse(questions[0]["answered"])
            self.assertEqual(questions[0]["mentions"], [])
            self.assertEqual(qt.build_index({"a": {"ev": [injected]}})["questions"], [])

    def test_cached_compaction_summary_is_not_an_owner_answer(self):
        ask = ev("assistant", "2026-01-01T00:00:01Z", "[Q7:publish] OK to publish?", "ask")
        summary = ev("user", "2026-01-01T00:00:02Z",
                     "  This session is being continued from a previous conversation that ran out of context.\n"
                     "Summary: The owner was asked [Q7:publish] OK to publish?", "summary")
        files = {"a": {"ev": [ask, summary]}}
        self.assertFalse(qt.build_index(files)["questions"][0]["answered"])
        files["a"]["ev"].append(ev("user", "2026-01-01T00:00:03Z", "[Q7] no", "answer"))
        q = qt.build_index(files)["questions"][0]
        self.assertTrue(q["answered"])
        self.assertEqual(q["answered_by"], "owner")

    def test_scan_skips_synthetic_metadata_and_summary_prefix(self):
        import json, tempfile
        from pathlib import Path
        lines = [
            {"type": "assistant", "uuid": "ask", "message": {"content": "[Q7:publish] OK to publish?"}},
            {"type": "user", "uuid": "meta", "isMeta": True, "message": {"content": "[Q7] yes"}},
            {"type": "user", "uuid": "summary", "isCompactSummary": True, "message": {"content": "[Q7] yes"}},
            {"type": "assistant", "uuid": "synthetic-ask", "isMeta": True, "message": {"content": "[Q8:other] OK?"}},
            {"type": "user", "uuid": "prefix", "message": {"content":
                "This session is being continued from a previous conversation.\n[Q7] yes"}},
            {"type": "user", "uuid": "answer", "message": {"content": "[Q7] no"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "session.jsonl"
            target.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
            events = qt.scan_file(str(target))
        self.assertEqual([event["uuid"] for event in events], ["ask", "answer"])

    def test_quoting_question_in_unrelated_session_does_not_answer_it(self):
        ask = ev("assistant", "2026-01-01T00:00:01Z", "[Q7:publish] OK to publish?", "ask")
        quote = dict(ev("user", "2026-01-01T00:00:02Z", "The original was [Q7:publish] OK to publish?", "quote"), sid="other")
        marker = dict(ev("assistant", "2026-01-01T00:00:03Z", "[Q7] answered", "marker"), sid="other")
        q = qt.build_index({"a": {"ev": [ask, quote, marker]}})["questions"][0]
        self.assertFalse(q["answered"])
        self.assertEqual(q["first"]["uuid"], "ask")

    def test_continuation_reply_answers_after_assistant_reminder(self):
        events = [ev("assistant", "2026-01-01T00:00:01Z", "[Q7:publish] OK to publish?", "ask")]
        # The display cap must not discard the continuation's answer provenance.
        for n in range(55):
            events.append(dict(ev("assistant", f"2026-01-01T00:01:{n:02}Z", "[Q7:publish] yes/no?", f"r{n}"), sid=f"continuation-{n}"))
        events.append(dict(ev("user", "2026-01-01T00:02:00Z", "[Q7] no", "answer"), sid="continuation-54"))
        q = qt.build_index({"a": {"ev": events}})["questions"][0]
        self.assertTrue(q["answered"])
        self.assertEqual(q["answered_by"], "owner")
        self.assertEqual(len(q["mentions"]), 50)

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


class IncrementalScanTest(unittest.TestCase):
    def test_resume_offset_skips_old_lines_and_partial_tail(self):
        import json, os, tempfile
        def line(uuid, text):
            return json.dumps({"type": "assistant", "uuid": uuid, "message": {"content": [{"type": "text", "text": text}]}}) + "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(line("a", "[Q1:x] one"))
            f.write(line("b", "[Q2:y] two")[:-5])  # half-written tail
        try:
            ev, off = qt.scan_file_from(f.name, 0)
            self.assertEqual([e["uuid"] for e in ev], ["a"])
            with open(f.name, "a") as g:
                g.write(line("b", "[Q2:y] two")[-5:])
                g.write(line("c", "[Q3:z] three"))
            ev2, _ = qt.scan_file_from(f.name, off)
            self.assertEqual([e["uuid"] for e in ev2], ["b", "c"])
        finally:
            os.unlink(f.name)


class ConcurrencyAndMemoTest(unittest.TestCase):
    def setUp(self):
        self._saved_files = dict(qt._files)
        self._saved_loaded = qt._loaded
        self._saved_last_scan = qt._last_scan
        qt._files.clear()
        qt._loaded = True
        qt._last_scan = 1e18

    def tearDown(self):
        qt._files.clear()
        qt._files.update(self._saved_files)
        qt._loaded = self._saved_loaded
        qt._last_scan = self._saved_last_scan

    def test_concurrent_files_mutation_never_raises_runtime_error(self):
        import threading
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                key = f"file-{i % 32}"
                qt._files[key] = {"ev": [ev("assistant", "2026-01-01T00:00:01Z", f"[Q{(i % 5) + 1}:slug] ask?", f"u{i}")]}
                if i % 3 == 0:
                    qt._files.pop(f"file-{(i + 7) % 32}", None)
                i += 1

        t = threading.Thread(target=writer)
        t.start()
        try:
            for _ in range(300):
                try:
                    res = qt.build_index()
                    self.assertIn("questions", res)
                except Exception as exc:
                    errors.append(exc)
                    break
        finally:
            stop.set()
            t.join()
        self.assertEqual(errors, [])

    def test_memoized_index_reused_and_isolated_from_ticket_mutations(self):
        from unittest import mock
        qt._files["a"] = {"ev": [ev("assistant", "2026-01-01T00:00:01Z", "[Q7:publish] OK to publish?", "ask")]}
        first = qt.build_index()
        self.assertEqual(len(first["questions"]), 1)
        self.assertFalse(first["questions"][0]["answered"])

        # Subsequent call with unchanged _files uses the memo without re-running regex scans.
        with mock.patch.object(qt, "_TAG") as tag_mock:
            second = qt.build_index()
            tag_mock.finditer.assert_not_called()
        self.assertEqual(second["questions"][0]["n"], 7)

        # payload() applying tickets must not leak ticket/answered state into the memoized index.
        unblocked = ticket(needs_input=False, block_question="", history=[
            {"event": "block", "at": "2026-01-01T00:00:01Z", "question": "[Q7:publish] OK?"}])
        with_tickets = qt.payload(tickets=[unblocked])
        self.assertTrue(with_tickets["questions"][0]["answered"])
        self.assertEqual(with_tickets["questions"][0]["answered_by"], "ticket")
        self.assertIn("ticket", with_tickets["questions"][0])

        without_tickets = qt.build_index()
        self.assertFalse(without_tickets["questions"][0]["answered"])
        self.assertEqual(without_tickets["questions"][0]["answered_by"], "")
        self.assertNotIn("ticket", without_tickets["questions"][0])

        # Appending an answer event invalidates the memo automatically.
        qt._files["a"]["ev"].append(ev("user", "2026-01-01T00:00:02Z", "[Q7] yes", "ans"))
        updated = qt.build_index()
        self.assertTrue(updated["questions"][0]["answered"])
        self.assertEqual(updated["questions"][0]["answered_by"], "owner")

