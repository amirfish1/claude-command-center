"""Idea-ticket autopilot and handoff (ccc_server/idea_tickets.py).

Done tickets close with the session's own summary and archive; agent-owned
tails are handed off (the session is told it owns the ticket) and the
watchdog follows them to a close, a block on the owner, or a nudge. Pure
module tests against a fake ``_core`` and a recorded ``_wt``.
"""

import importlib
import json
import pathlib
import tempfile
import types
import unittest

it = importlib.import_module("ccc_server.idea_tickets")

NOW = 2_000_000_000.0


class _Sync:
    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _footer(**over):
    comp = {
        "version": 1, "valid": True,
        "scope": {"closure_met": True},
        "decision_dependency": {"score": 0, "evidence": "none"},
        "caveats": [], "remaining_actions": [], "ready_to_close": False,
    }
    comp.update(over.pop("completion", {}))
    ss = {"did": "Fixed the popup.", "next_step_user": "None.", "completion": comp}
    ss.update(over)
    return ss


class AttentionTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(it.attention(_footer(completion={"ready_to_close": True})), "done")
        self.assertEqual(it.attention(_footer(next_step_user="Reply A or B")), "needs_you")
        self.assertEqual(it.attention(_footer(next_step_user="None — nothing pending")), "done")
        self.assertEqual(it.attention(_footer(completion={
            "decision_dependency": {"score": 0.5}})), "needs_you")
        self.assertEqual(it.attention(_footer(completion={"remaining_actions": [
            {"text": "Owner: user — approve the send"}]})), "needs_you")
        # Waiting only on a routine release does not keep the row open.
        self.assertEqual(it.attention(_footer(completion={"remaining_actions": [
            {"text": "External: scheduled evening release deploys it"}]})), "done")
        self.assertEqual(it.attention(_footer(completion={
            "scope": {"closure_met": False}, "remaining_actions": [
                {"text": "agent: verify production after deploy"}]})), "handoff")
        self.assertEqual(it.attention(_footer(completion={"valid": False})), "")
        self.assertEqual(it.attention({}), "")


class AutopilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.calls, self.injects, self.archived = [], [], []
        self.find = {}
        self._orig = (it._core, it._wt, it.threading.Thread)
        it._state_cache.update({"key": None, "data": None})
        it._inflight.clear()
        it._watch.update({"at": 0.0, "running": False})
        self.core = types.SimpleNamespace(
            COMMAND_CENTER_STATE_DIR=root,
            SPAWN_MARKERS_DIR=root / "markers",
            _feature_flag=lambda name: name == "idea_tickets",
            _log_activity=lambda *a: None,
            _restamp_archive_serve_cache_after_mutation=lambda: None,
            _set_conversations_archived=lambda sids, want, source="": self.archived.append(
                (list(sids), want, source)),
            _inject_text_into_session=lambda sid, text, **kw: (
                self.injects.append((sid, text, kw.get("source"))) or {"ok": True}),
            _find_session_jsonl=lambda sid: None,
            _spawn_registry_entry_for_session=lambda sid: None,
        )
        it._core = self.core
        it._wt = self._fake_wt
        it.threading.Thread = _Sync
        (root / it.STATE_FILE_NAME).write_text(json.dumps({
            "sessions": {"a1": {"ref": "IDEAS-1", "kind": "started"},
                         "a2": {"ref": "IDEAS-1", "kind": "child"}},
            "tickets": {"IDEAS-1": {"queue": "IDEAS", "sessions": ["a1", "a2"], "closed": False}},
            "pending": {},
        }))

    def tearDown(self):
        it._core, it._wt, it.threading.Thread = self._orig
        importlib.reload(it)
        self.tmp.cleanup()

    def _fake_wt(self, args, cwd=None, timeout=30):
        self.calls.append(list(args))
        if args[0] == "find":
            return 0, json.dumps(self.find.get(args[1], {"status": "open"}))
        if args[0] == "add":
            return 0, "FILED: SMALL-BUGS-7  title\n"
        return 0, ""

    def _rows(self, ss, *, age=600, **lead):
        row = {"session_id": "a1", "mtime": NOW - age, "state": "ended",
               "last_event_type": "result", "session_state": ss}
        row.update(lead)
        child = {"session_id": "a2", "mtime": NOW - age - 50, "state": "ended",
                 "last_event_type": "result"}
        return [row, child]

    def _ticket(self):
        it._state_cache["key"] = None
        return it.load_state()["tickets"]["IDEAS-1"]

    def test_done_ticket_closes_with_summary_and_archives_group(self):
        it.autopilot_rows(self._rows(_footer(completion={"ready_to_close": True})), now=NOW)
        close = [c for c in self.calls if c[0] == "close"][0]
        self.assertEqual(close[1], "IDEAS-1")
        self.assertIn("Fixed the popup.", close[close.index("--summary") + 1])
        self.assertEqual(self.archived, [(["a1"], True, "autopilot-done")])
        self.assertTrue(self._ticket()["closed"])
        self.assertEqual(self.injects, [])

    def test_agent_tail_is_handed_off_once(self):
        ss = _footer(completion={"scope": {"closure_met": False}, "remaining_actions": [
            {"text": "agent: verify after deploy"}]})
        it.autopilot_rows(self._rows(ss), now=NOW)
        self.assertEqual(len(self.injects), 1)
        sid, text, source = self.injects[0]
        self.assertEqual((sid, source), ("a1", "handoff"))
        self.assertIn("wt close IDEAS-1", text)
        self.assertIn("wt block IDEAS-1", text)
        h = self._ticket()["handoff"]
        self.assertEqual((h["state"], h["session"], h["auto"]), ("running", "a1", True))
        self.assertEqual(it.idea_ticket_rows_map()["a1"]["handoff"]["state"], "running")
        it.autopilot_rows(self._rows(ss), now=NOW)  # already handed off
        self.assertEqual(len(self.injects), 1)

    def test_owner_rows_and_guards_do_nothing(self):
        done = _footer(completion={"ready_to_close": True})
        for rows in (
            self._rows(_footer(next_step_user="Pick A or B")),  # needs the owner
            self._rows(done, age=30),                           # not quiet yet
            self._rows(done, age=3 * 86400),                    # old backlog
            self._rows(done, state="working"),                  # busy
            self._rows(done, last_event_type="user"),           # turn pending
            self._rows(done, recently_unarchived=True),         # owner undid it
            self._rows(done, archived=True),
        ):
            it.autopilot_rows(rows, now=NOW)
        self.assertEqual([c for c in self.calls if c[0] == "close"], [])
        self.assertEqual(self.injects, [])

    def test_marker_respects_manual_unarchive(self):
        done = _footer(completion={"ready_to_close": True})
        it.autopilot_rows(self._rows(done), now=NOW)
        it._mutate(lambda d: d["tickets"]["IDEAS-1"].update(closed=False))
        it.autopilot_rows(self._rows(done), now=NOW + 500)  # same turn: left alone
        self.assertEqual(len([c for c in self.calls if c[0] == "close"]), 1)

    def test_handoff_files_small_bug_for_untracked_session(self):
        self.core._spawn_registry_entry_for_session = lambda sid: None
        it._opening_text = lambda sid, jsonl=None: "Fix the thing"
        res = it.handoff("loose")
        self.assertEqual(res, {"ok": True, "ref": "SMALL-BUGS-7"})
        add = [c for c in self.calls if c[0] == "add"][0]
        self.assertEqual(add[add.index("-q") + 1], "SMALL-BUGS")
        self.assertEqual(self.injects[0][0], "loose")

    def _handed_off(self, **h):
        it._mutate(lambda d: d["tickets"]["IDEAS-1"].update(handoff=dict(
            {"state": "running", "session": "a1", "at": NOW - 3600, "nudges": 0,
             "nudged_at": 0}, **h)))
        return self._ticket()["handoff"]

    def test_watchdog_closed_ticket_archives(self):
        h = self._handed_off()
        self.find["IDEAS-1"] = {"status": "closed"}
        it._watch_one("IDEAS-1", h, {"seen": True}, now=NOW)
        self.assertTrue(self._ticket()["closed"])
        self.assertEqual(self._ticket()["handoff"]["state"], "closed")
        self.assertEqual(self.archived, [(["a1"], True, "handoff-closed")])

    def test_watchdog_surfaces_block_question(self):
        h = self._handed_off()
        self.find["IDEAS-1"] = {"status": "in_progress", "needs_input": True,
                                "block_question": "Ship to prod now?"}
        it._watch_one("IDEAS-1", h, {"seen": True}, now=NOW)
        h = self._ticket()["handoff"]
        self.assertEqual((h["state"], h["question"]), ("blocked", "Ship to prod now?"))
        self.assertEqual(it.idea_ticket_rows_map()["a1"]["handoff"]["question"], "Ship to prod now?")

    def test_watchdog_nudges_then_blocks_a_stalled_session(self):
        info = {"seen": True, "busy": False, "mtime": NOW - 3600, "session_state": {}}
        h = self._handed_off()
        it._watch_one("IDEAS-1", h, info, now=NOW)
        self.assertEqual(self.injects[-1][2], "handoff-nudge")
        self.assertEqual(self._ticket()["handoff"]["nudges"], 1)
        it._watch_one("IDEAS-1", self._ticket()["handoff"], info, now=NOW + 60)
        self.assertEqual(len(self.injects), 1)  # nudged recently: wait
        h = self._handed_off(nudges=it.HANDOFF_MAX_NUDGES)
        it._watch_one("IDEAS-1", h, info, now=NOW)
        block = [c for c in self.calls if c[0] == "block"][0]
        self.assertIn("--force", block)
        self.assertEqual(self._ticket()["handoff"]["state"], "blocked")

    def test_watchdog_closes_when_footer_says_done(self):
        h = self._handed_off()
        info = {"seen": True, "busy": False, "mtime": NOW - 1800,
                "session_state": _footer(completion={"ready_to_close": True})}
        it._watch_one("IDEAS-1", h, info, now=NOW)
        self.assertTrue(self._ticket()["closed"])
        self.assertEqual(self.archived[0][2], "handoff-done")

    def test_busy_session_is_not_nudged(self):
        h = self._handed_off()
        it._watch_one("IDEAS-1", h, {"seen": True, "busy": True, "mtime": 0}, now=NOW)
        self.assertEqual(self.injects, [])

    def test_answer_records_and_resumes(self):
        self._handed_off(state="blocked", question="Ship?")
        res = it.answer("a1", "Yes, ship it")
        self.assertTrue(res["ok"])
        self.assertIn(["answer", "IDEAS-1", "Yes, ship it"], self.calls)
        self.assertIn("Yes, ship it", self.injects[-1][1])
        self.assertEqual(self._ticket()["handoff"]["state"], "running")

    def test_takeback_stops_watching(self):
        self._handed_off()
        it.takeback("a1")
        self.assertEqual(self._ticket()["handoff"]["state"], "taken_back")
        self.assertNotIn("handoff", {k: v for k, v in it.idea_ticket_rows_map()["a1"].items()
                                     if k == "handoff" and v["state"] in it._ACTIVE_HANDOFF})

    def test_watchdog_tick_is_throttled(self):
        self._handed_off()
        rows = self._rows(_footer())
        it.autopilot_rows(rows, now=NOW)
        finds = len([c for c in self.calls if c[0] == "find"])
        it.autopilot_rows(rows, now=NOW + 5)
        self.assertEqual(len([c for c in self.calls if c[0] == "find"]), finds)
        self.assertEqual(finds, 1)


if __name__ == "__main__":
    unittest.main()
