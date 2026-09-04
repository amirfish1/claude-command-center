"""Decision Inbox + token governor (ccc_server/decision_inbox.py).

Pure-core tests: board parsing, WatchTower filtering, idle-session
candidacy, governor detectors on a synthetic transcript, the analyst JSON
parse, the dedupe/cap contract of a run, and option follow-through with
injected spawn/inject/pause/kill hooks. No subprocess, no server.py.
"""

import importlib
import json
import pathlib
import tempfile
import time
import unittest

di = importlib.import_module("ccc_server.decision_inbox")

NOW = 1_788_450_000.0  # 2026-09-03T15:40:00Z
H = 3600.0


def _cfg(**over):
    cfg = dict(di.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


BOARD = """# Strategy Board

| Category | Task / Project | Status | ETA | Today's Focus / Next Steps |
| :--- | :--- | :--- | :--- | :--- |
| Blockers | **"DROR" investor** | ⚠️ Blocked | - | Real-estate T&C ping: deadline? |
| Blockers | **Upwork browser thread** | ✅ Done | - | resolved |
| Scheduled | **Webinar** | 🔵 Open | Tomorrow 8-9am | - |
| Scheduled | **Show HN** | 🟡 Active | Tue 28 7am | plan |
| Scheduled | **Launch post** | 🔵 Open | 2026-05-28 | - |
| Scheduled | **Future thing** | 🔵 Open | 2099-01-01 | - |
| Growth | **Organic posting** | 🔵 Open | - | IG, FB |
"""


class BoardParsing(unittest.TestCase):
    def test_blocked_and_past_eta_rows_surface_done_and_future_do_not(self):
        cands = di.parse_strategy_board(BOARD, file_mtime=NOW - 10 * 86400, now=NOW)
        ids = {c["source_id"]: c for c in cands}
        self.assertIn("board:dror-investor", ids)
        self.assertEqual(ids["board:dror-investor"]["reason"], "marked Blocked")
        self.assertIn("board:webinar", ids)          # "Tomorrow" written 10d ago
        self.assertIn("board:launch-post", ids)      # ISO date in the past
        self.assertNotIn("board:future-thing", ids)  # ISO date in the future
        self.assertNotIn("board:upwork-browser-thread", ids)  # Done
        self.assertNotIn("board:show-hn", ids)       # Active, not Open
        self.assertNotIn("board:organic-posting", ids)  # Open, no ETA

    def test_relative_eta_is_not_past_when_board_is_fresh(self):
        cands = di.parse_strategy_board(BOARD, file_mtime=NOW - H, now=NOW)
        ids = {c["source_id"] for c in cands}
        self.assertNotIn("board:webinar", ids)
        self.assertIn("board:dror-investor", ids)

    def test_ignore_patterns_drop_rows(self):
        cands = di.parse_strategy_board(BOARD, file_mtime=NOW - 10 * 86400, now=NOW,
                                        cfg=_cfg(ignore_patterns=["dror"]))
        self.assertNotIn("board:dror-investor", {c["source_id"] for c in cands})

    def test_eta_is_past_shapes(self):
        self.assertEqual(di.eta_is_past("-", file_mtime=NOW, now=NOW), (False, ""))
        self.assertTrue(di.eta_is_past("Tue 2026-05-28", file_mtime=NOW, now=NOW)[0])
        self.assertFalse(di.eta_is_past("This week", file_mtime=NOW - 2 * 86400, now=NOW)[0])
        self.assertTrue(di.eta_is_past("This week", file_mtime=NOW - 9 * 86400, now=NOW)[0])


class WatchTowerQueues(unittest.TestCase):
    STATUS = [
        {"queue": "OLD", "depth": 4, "oldest_open_age_s": 9 * 86400, "oldest_open_age": "9d",
         "since_progress": "9d", "stuck": True},
        {"queue": "FRESH", "depth": 2, "oldest_open_age_s": 3600, "stuck": True},
        {"queue": "EMPTY", "depth": 0, "oldest_open_age_s": 0, "stuck": False},
        {"queue": "DRAINING", "depth": 7, "oldest_open_age_s": 20 * 86400, "stuck": False},
        {"queue": "OLDER", "depth": 1, "oldest_open_age_s": 30 * 86400, "oldest_open_age": "30d",
         "since_progress": "30d", "stuck": True},
    ]

    def test_stuck_queues_filtered_and_sorted_oldest_first(self):
        rows = di.wt_stuck_queues(self.STATUS, min_age_s=3 * 86400)
        self.assertEqual([r["queue"] for r in rows], ["OLDER", "OLD"])

    def test_wt_candidates_one_status_call_plus_one_ls_per_queue(self):
        calls = []

        def runner(args):
            calls.append(list(args))
            if args[0] == "status":
                return self.STATUS
            return [{"ref": f"{args[2]}-1", "title": "first ticket"}]

        cands = di.wt_candidates(_cfg(wt_age_days=3), runner=runner, limit=5)
        self.assertEqual([c["source_id"] for c in cands], ["wt:OLDER", "wt:OLD"])
        self.assertEqual(cands[0]["tickets"], ["OLDER-1 first ticket"])
        self.assertEqual(len([c for c in calls if c[0] == "status"]), 1)
        self.assertEqual(len([c for c in calls if c[0] == "ls"]), 2)

    def test_missing_wt_yields_nothing(self):
        self.assertEqual(di.wt_candidates(_cfg(), runner=lambda a: None), [])


def _row(sid, **over):
    row = {"session_id": sid, "mtime": NOW - 3 * H, "display_name": "Lane " + sid,
           "session_cwd": "/repo", "jsonl_path": f"/x/{sid}.jsonl",
           "pending_tool": None, "goal": "", "goal_status": "", "session_state": None,
           "live_context_percent": 0, "latest_input_tokens": 0, "context_limit": 200_000}
    row.update(over)
    return row


class IdleSessions(unittest.TestCase):
    def test_only_live_idle_unfinished_sessions_surface(self):
        rows = [
            _row("a", pending_tool="Bash"),                       # live, idle, pending -> yes
            _row("b", pending_tool="Bash"),                       # not live -> no
            _row("c", goal="ship it", goal_status="in progress", mtime=NOW - 0.5 * H),  # too recent
            _row("d", goal="ship it", goal_status="done"),        # finished
            _row("e", session_state={"state": "working"}),        # yes
            _row("f"),                                            # nothing pending
        ]
        cands = di.idle_session_candidates(rows, {"a", "c", "d", "e", "f"}, now=NOW, cfg=_cfg(idle_hours=2))
        self.assertEqual(sorted(c["session_id"] for c in cands), ["a", "e"])
        self.assertEqual(cands[0]["kind"], "session")
        self.assertIn("idle 3.0h", cands[0]["reason"])


def _ev(t, ts, blocks):
    return json.dumps({"type": t, "timestamp": di._iso(ts), "message": {"content": blocks}})


def _tool_use(name):
    return {"type": "tool_use", "name": name, "input": {}}


def _tool_err(text):
    return {"type": "tool_result", "tool_use_id": "x", "is_error": True, "content": text}


class Governor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, lines):
        p = self.dir / name
        p.write_text("\n".join(lines) + "\n")
        return str(p)

    def test_repeated_identical_errors_detected(self):
        lines = [_ev("assistant", NOW - 600 + i, [_tool_use("Bash")]) for i in range(4)]
        lines += [_ev("user", NOW - 590 + i, [_tool_err("npm ERR! missing script: build")]) for i in range(4)]
        lines.append(_ev("user", NOW - 100, [_tool_err("some other error")]))
        sig = di.analyze_transcript_tail(self._write("a.jsonl", lines), now=NOW)
        self.assertEqual(max(sig["error_counts"].values()), 4)
        rows = [_row("a", jsonl_path=str(self.dir / "a.jsonl"), mtime=NOW - 60)]
        findings = di.governor_findings(rows, {"a"}, now=NOW, cfg=_cfg())
        self.assertEqual([f["kind"] for f in findings], ["repeated_errors"])
        self.assertIn("4x", findings[0]["detail"])

    def test_no_edits_while_working_detected_only_after_earlier_edits(self):
        base = [_ev("assistant", NOW - 3 * H, [_tool_use("Edit")])]  # edited long ago
        churn = [_ev("assistant", NOW - 40 * 60 + i * 60, [_tool_use("Read"), _tool_use("Grep")]) for i in range(6)]
        churn.append(_ev("assistant", NOW - 60, [{"type": "text", "text": "still looking"}]))
        path = self._write("b.jsonl", base + churn)
        rows = [_row("b", jsonl_path=path, mtime=NOW - 60)]
        findings = di.governor_findings(rows, {"b"}, now=NOW, cfg=_cfg())
        self.assertEqual([f["kind"] for f in findings], ["no_edits"])
        # Same churn, but the session never edited anything: research, not stuck.
        path2 = self._write("c.jsonl", churn)
        rows = [_row("c", jsonl_path=path2, mtime=NOW - 60)]
        self.assertEqual(di.governor_findings(rows, {"c"}, now=NOW, cfg=_cfg()), [])

    def test_context_high_from_cached_row_without_tail_parse(self):
        calls = []
        rows = [_row("d", live_context_percent=91, mtime=NOW - 10 * H),
                _row("e", latest_input_tokens=180_000, context_limit=200_000, mtime=NOW - 10 * H),
                _row("f", latest_input_tokens=50_000, mtime=NOW - 10 * H)]
        findings = di.governor_findings(rows, {"d", "e", "f"}, now=NOW, cfg=_cfg(),
                                        analyze=lambda p: calls.append(p) or {})
        self.assertEqual(sorted(f["session_id"] for f in findings), ["d", "e"])
        self.assertEqual(calls, [], "idle-for-hours rows must not get a tail parse")

    def test_not_live_sessions_are_never_analyzed(self):
        calls = []
        rows = [_row("z", live_context_percent=99, mtime=NOW - 60)]
        findings = di.governor_findings(rows, set(), now=NOW, cfg=_cfg(),
                                        analyze=lambda p: calls.append(p) or {})
        self.assertEqual(findings, [])
        self.assertEqual(calls, [])

    def test_governor_card_has_nudge_pause_kill(self):
        f = {"session_id": "s1", "name": "Lane X", "kind": "repeated_errors", "detail": "same error 3x",
             "value": 3, "cwd": "/repo"}
        card = di.governor_card(f, now=NOW, run_id="run_t")
        self.assertEqual([o["action"]["kind"] for o in card["options"]], ["inject", "pause", "kill"])
        self.assertEqual(sum(o["recommended"] for o in card["options"]), 1)
        self.assertEqual(card["source_id"], "governor:s1:repeated_errors")


class AnalystParse(unittest.TestCase):
    def test_parses_fenced_json_and_normalises_options(self):
        raw = '```json\n{"title": "Stuck", "context": "why", "options": [' \
              '{"label": "A", "cost": "x", "recommended": true, "action": {"kind": "spawn", "prompt": "do A"}},' \
              '{"label": "B", "recommended": true, "action": {"kind": "weird"}},' \
              '{"label": "C"}, {"label": "D"}]}\n```'
        body = di.parse_analyst_json(raw)
        self.assertEqual(body["title"], "Stuck")
        self.assertEqual(len(body["options"]), 3)
        self.assertEqual(body["options"][1]["action"]["kind"], "human")
        self.assertEqual(sum(o["recommended"] for o in body["options"]), 1)
        self.assertTrue(body["options"][0]["recommended"])

    def test_rejects_prose(self):
        with self.assertRaises(ValueError):
            di.parse_analyst_json("I could not decide.")

    def test_prompt_mentions_facts_and_schema(self):
        p = di.analyst_prompt({"kind": "board", "source_id": "board:x", "title": "Neta referral",
                               "reason": "marked Blocked"})
        self.assertIn("Neta referral", p)
        self.assertIn('"recommended"', p)


class RunContract(unittest.TestCase):
    def _run(self, cards, **kw):
        kw.setdefault("cfg", _cfg(max_cards_per_run=5, strategy_board="", dedupe_days=7))
        kw.setdefault("now", NOW)
        kw.setdefault("rows", [])
        kw.setdefault("live_ids", set())
        kw.setdefault("wt_runner", lambda a: None)
        kw.setdefault("analyst", lambda c: {"title": "T " + c["source_id"], "context": "c",
                                            "options": [{"label": "go", "detail": "", "cost": "1",
                                                         "recommended": True,
                                                         "action": {"kind": "spawn", "prompt": "brief"}}]})
        kw.setdefault("persist", False)
        return di.run_once(cards=cards, **kw)

    def test_cap_five_per_run_and_dedupe_by_source_id(self):
        board = "| Cat | Task | Status | ETA | Notes |\n|---|---|---|---|---|\n" + \
            "".join(f"| x | **Item {i}** | ⚠️ Blocked | - | - |\n" for i in range(8))
        cards = {}
        rec = self._run(cards, board_text=board, board_mtime=NOW)
        self.assertEqual(len(rec["created"]), 5)
        self.assertEqual(rec["skipped_cap"], 3)
        self.assertEqual(len(cards), 5)
        # Second run: the five open cards block their source ids; the three
        # that hit the cap now get their turn. Nothing duplicates.
        rec2 = self._run(cards, board_text=board, board_mtime=NOW)
        self.assertEqual(len(rec2["created"]), 3)
        self.assertEqual(rec2["skipped_dedupe"], 5)
        self.assertEqual(len({c["source_id"] for c in cards.values()}), 8)

    def test_decided_recently_blocks_but_old_decisions_do_not(self):
        board = "| Cat | Task | Status | ETA | Notes |\n|---|---|---|---|---|\n| x | **Item** | ⚠️ Blocked | - | - |\n"
        cards = {"c1": {"id": "c1", "source_id": "board:item", "status": "decided",
                        "updated_at": di._iso(NOW - 2 * 86400)}}
        rec = self._run(cards, board_text=board, board_mtime=NOW)
        self.assertEqual(rec["created"], [])
        cards["c1"]["updated_at"] = di._iso(NOW - 20 * 86400)
        rec = self._run(cards, board_text=board, board_mtime=NOW)
        self.assertEqual(len(rec["created"]), 1)

    def test_governor_cards_come_first_and_count_toward_cap(self):
        rows = [_row(f"g{i}", live_context_percent=95, mtime=NOW - 10 * H) for i in range(3)]
        board = "| Cat | Task | Status | ETA | Notes |\n|---|---|---|---|---|\n" + \
            "".join(f"| x | **Item {i}** | ⚠️ Blocked | - | - |\n" for i in range(4))
        cards = {}
        rec = self._run(cards, rows=rows, live_ids={"g0", "g1", "g2"}, board_text=board, board_mtime=NOW,
                        cfg=_cfg(max_cards_per_run=4, strategy_board=""))
        kinds = [cards[c["id"]]["kind"] for c in rec["created"]]
        self.assertEqual(kinds, ["governor", "governor", "governor", "board"])
        self.assertEqual(rec["sources"]["governor"], 3)

    def test_analyst_failure_falls_back_to_generic_card(self):
        board = "| Cat | Task | Status | ETA | Notes |\n|---|---|---|---|---|\n| x | **Item** | ⚠️ Blocked | - | - |\n"
        cards = {}

        def boom(c):
            raise RuntimeError("claude not found")

        rec = self._run(cards, board_text=board, board_mtime=NOW, analyst=boom)
        card = cards[rec["created"][0]["id"]]
        self.assertEqual(card["analyst"]["error"], "claude not found")
        self.assertEqual(card["options"][0]["action"]["kind"], "spawn")
        self.assertTrue(card["options"][0]["recommended"])

    def test_session_candidate_inject_keeps_session_id_board_inject_becomes_spawn(self):
        rows = [_row("s9", pending_tool="Bash", mtime=NOW - 5 * H)]
        board = "| Cat | Task | Status | ETA | Notes |\n|---|---|---|---|---|\n| x | **Item** | ⚠️ Blocked | - | - |\n"
        analyst = lambda c: {"title": "t", "context": "c", "options": [  # noqa: E731
            {"label": "steer", "detail": "", "cost": "", "recommended": True,
             "action": {"kind": "inject", "prompt": "continue"}}]}
        cards = {}
        self._run(cards, rows=rows, live_ids={"s9"}, board_text=board, board_mtime=NOW, analyst=analyst)
        by_src = {c["source_id"]: c for c in cards.values()}
        self.assertEqual(by_src["session:s9"]["options"][0]["action"]["session_id"], "s9")
        self.assertEqual(by_src["session:s9"]["options"][0]["action"]["cwd"], "/repo")
        self.assertEqual(by_src["board:item"]["options"][0]["action"]["kind"], "spawn")


class FollowThrough(unittest.TestCase):
    def _cards(self):
        return {"c1": {"id": "c1", "source_id": "board:x", "status": "open", "title": "Fix X",
                       "options": [
                           {"label": "spawn", "action": {"kind": "spawn", "prompt": "do it", "cwd": "/repo"}},
                           {"label": "human", "action": {"kind": "human"}},
                           {"label": "steer", "action": {"kind": "inject", "session_id": "s1", "prompt": "go"}},
                       ]}}

    def test_decide_spawn_calls_hook_and_closes_card(self):
        seen = {}

        def spawn(prompt, **kw):
            seen.update(prompt=prompt, **kw)
            return {"ok": True, "session_id": "new1"}

        cards = self._cards()
        res = di.decide("c1", 0, cards=cards, now=NOW, persist=False, spawn=spawn)
        self.assertTrue(res["ok"])
        self.assertEqual(seen["prompt"], "do it")
        self.assertEqual(seen["cwd"], "/repo")
        self.assertEqual(seen["name"], "Decision: Fix X")
        self.assertEqual(cards["c1"]["status"], "decided")
        self.assertEqual(cards["c1"]["decided"]["result"]["session_id"], "new1")
        # A decided card cannot be decided twice.
        self.assertFalse(di.decide("c1", 1, cards=cards, now=NOW, persist=False)["ok"])

    def test_decide_human_needs_no_hook(self):
        cards = self._cards()
        res = di.decide("c1", 1, cards=cards, now=NOW, persist=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["effect"], "noted")

    def test_decide_inject_and_failed_action_keeps_card_open(self):
        cards = self._cards()
        res = di.decide("c1", 2, cards=cards, now=NOW, persist=False,
                        inject=lambda sid, text: {"ok": False, "error": "no route"})
        self.assertFalse(res["ok"])
        self.assertEqual(cards["c1"]["status"], "open")
        res = di.decide("c1", 2, cards=cards, now=NOW, persist=False,
                        inject=lambda sid, text: {"ok": True, "via": "uds"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["effect"], "injected")

    def test_dismiss_and_unknown_ids(self):
        cards = self._cards()
        self.assertTrue(di.dismiss("c1", cards=cards, now=NOW, persist=False)["ok"])
        self.assertEqual(cards["c1"]["status"], "dismissed")
        self.assertFalse(di.dismiss("nope", cards=cards, persist=False)["ok"])
        self.assertFalse(di.decide("nope", 0, cards=cards, persist=False)["ok"])

    def test_governor_act_routes(self):
        log = []
        di.governor_act("s1", "nudge", reason="same error 3x", inject=lambda sid, t: log.append(("inject", sid, t)) or {"ok": True})
        di.governor_act("s1", "pause", pause=lambda sid: log.append(("pause", sid)) or {"ok": True})
        di.governor_act("s1", "kill", kill=lambda sid: log.append(("kill", sid)) or {"ok": True})
        self.assertEqual([e[0] for e in log], ["inject", "pause", "kill"])
        self.assertIn("same error 3x", log[0][2])
        self.assertFalse(di.governor_act("s1", "dance")["ok"])


class Persistence(unittest.TestCase):
    def test_cards_roundtrip_and_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "cards.json"
            di.save_cards({"c1": {"id": "c1"}}, path=p)
            self.assertEqual(di.load_cards(path=p), {"c1": {"id": "c1"}})
            self.assertEqual(di.load_cards(path=pathlib.Path(tmp) / "missing.json"), {})
            runs = pathlib.Path(tmp) / "runs.jsonl"
            di._append_run({"run_id": "r1"}, path=runs)
            di._append_run({"run_id": "r2"}, path=runs)
            self.assertEqual(di.last_run(path=runs)["run_id"], "r2")

    def test_config_defaults_and_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "decision-inbox.json"
            self.assertEqual(di.load_config(path=p)["max_cards_per_run"], 5)
            p.write_text(json.dumps({"max_cards_per_run": 2, "strategy_board": "~/board.md"}))
            cfg = di.load_config(path=p)
            self.assertEqual(cfg["max_cards_per_run"], 2)
            self.assertTrue(cfg["strategy_board"].endswith("/board.md"))
            self.assertFalse(cfg["strategy_board"].startswith("~"))


if __name__ == "__main__":
    unittest.main()
