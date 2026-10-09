"""Mazkir: ccc-state MCP handshake/tools, fleet heuristics, Ask pipeline seams."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccc_server import mazkir  # noqa: E402

CENSUS = {"ok": True, "now": 1000.0, "sessions": [
    {"session_id": "s-working-idle", "state": "working", "engine": "claude", "repo_path": "/r/a",
     "last_event_age_s": 900, "pending_tool": None},
    {"session_id": "s-working-ok", "state": "working", "engine": "codex", "repo_path": "/r/b",
     "last_event_age_s": 20},
    {"session_id": "s-helper", "state": "working", "engine": "claude",
     "repo_path": "/Users/x/.claude/command-center/scratch", "last_event_age_s": 5000},
    {"session_id": "s-question", "state": "idle", "engine": "claude", "repo_path": "/r/c",
     "last_event_age_s": 30, "question_waiting": True},
    {"session_id": "s-flagged", "state": "idle", "engine": "kimi", "repo_path": "/r/d",
     "last_event_age_s": 10, "stuck": True, "stuck_age_s": 1200},
]}
LIVE = {"sessions": {"s-working-ok": {"is_live": True, "sidecar_status": "ok"},
                     "s-question": {"is_live": False, "question_waiting": True}}}
WINDOW = {"ok": True, "scope": "30m", "totals": {"total_tokens": 1000}, "session_count": 4,
          "sessions": [{"session_id": "s-burn", "session_name": "hot", "engine": "claude", "total_tokens": 900},
                       {"session_id": "a", "total_tokens": 100}, {"session_id": "b", "total_tokens": 120},
                       {"session_id": "c", "total_tokens": 90}]}


def fake_fetch(path):
    if path.startswith("/api/sessions/census"):
        return CENSUS
    if path.startswith("/api/sessions/live-activity"):
        return LIVE
    if path.startswith("/api/throughput/window"):
        return WINDOW
    if path.startswith("/api/queue/status"):
        return {"ok": True, "projects": [{"project": "ops", "depth": 2}]}
    raise OSError(f"unexpected {path}")


class DiagnosticsTest(unittest.TestCase):
    def test_stuck_waiting_burning(self):
        d = mazkir.fleet_diagnostics(CENSUS, LIVE, WINDOW)
        stuck = {s["session_id"]: s["reasons"] for s in d["stuck"]}
        self.assertIn("s-working-idle", stuck)
        self.assertIn("s-flagged", stuck)
        self.assertNotIn("s-working-ok", stuck)
        self.assertNotIn("s-helper", stuck)  # scratch helpers are never stuck
        self.assertEqual([w["session_id"] for w in d["waiting"]], ["s-question"])
        self.assertEqual([b["session_id"] for b in d["burning"]], ["s-burn"])
        self.assertGreater(d["burning"][0]["x_median"], 3)

    def test_list_sessions_filters(self):
        out = mazkir.tool_list_sessions(CENSUS, state="working", engine="codex")
        self.assertEqual([s["session_id"] for s in out["sessions"]], ["s-working-ok"])
        self.assertEqual(out["by_state"], {"working": 3, "idle": 2})


class McpProtocolTest(unittest.TestCase):
    def setUp(self):
        self.state = mazkir.CccState("http://x", fetch=fake_fetch)

    def rpc(self, method, params=None, rid=1):
        return mazkir.handle_request(self.state, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})

    def test_initialize_and_list(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "ccc-state")
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(mazkir.handle_request(self.state, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        with mock.patch.object(mazkir, "checkin_enabled", return_value=True):
            names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["list_sessions", "live_activity", "throughput_window", "queue_status",
                                 "session_detail", "fleet_diagnostics", "inject_diagnostics", "daily_checkin", "daily_brief",
                                 "propose_spawn_session", "propose_inject",
                                 "propose_wt_add", "propose_wt_comment"])

    def test_tools_call_and_errors(self):
        r = self.rpc("tools/call", {"name": "queue_status", "arguments": {}})
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["projects"][0]["project"], "ops")
        r = self.rpc("tools/call", {"name": "session_detail", "arguments": {"session_id": "s-flagged"}})
        self.assertTrue(json.loads(r["result"]["content"][0]["text"])["known"])
        r = self.rpc("tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(self.rpc("bogus")["error"]["code"], -32601)

    def test_fleet_diagnostics_degrades_when_live_activity_fails(self):
        def flaky(path):
            if "live-activity" in path:
                raise OSError("timed out")
            return fake_fetch(path)
        st = mazkir.CccState("http://x", fetch=flaky)
        out = st.call("fleet_diagnostics", {})
        self.assertIn("live-activity unavailable: OSError", out["notes"])
        self.assertTrue(any(s["session_id"] == "s-working-idle" for s in out["stuck"]))


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _index_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, cwd TEXT, project_dir TEXT, first_ts TEXT,
                               last_ts TEXT, message_count INTEGER, slug TEXT, harness TEXT, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, type TEXT, content TEXT, ts_unix REAL);
        INSERT INTO sessions VALUES ('kimi-1', '/Users/x/Apps/ccc', '_kimi', '2026-09-02T02:50:29Z',
                                     '2026-09-02T03:16:00Z', 206, 'main', NULL, NULL);
        INSERT INTO messages VALUES (1, 'kimi-1', 'user', 'Handoff: ccc spawn + ccc models', 1.0);
    """)
    conn.commit()
    conn.close()


class RunMazkirTest(unittest.TestCase):
    def test_pipeline_with_injected_seams(self):
        tmp = tempfile.mkdtemp()
        dbp = os.path.join(tmp, "index.db")
        _index_db(dbp)
        cands = [{"session_id": "c-100001", "harness": "claude", "cwd": "/r/a", "title": "Ask tab build",
                  "best_snippet": "«Ask» «tab»", "first_ts": "2026-08-30T00:00:00Z", "last_ts": "2026-08-31T00:00:00Z", "hits": 5}]
        prefetch = lambda argv, **kw: _Proc(stdout=json.dumps(cands))
        seen = {}

        def runner(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return _Proc(stdout=json.dumps({"result": "Built in [[session:c-100001]], continued in Kimi [[session:kimi-1]]. "
                                                      "[[action:spawn-continue:c-100001]]",
                                            "num_turns": 2, "total_cost_usd": 0.01}))

        with mock.patch.object(mazkir, "INDEX_BIN", "/x/claude-index"):
            body, status = mazkir.run_mazkir("Where did I work on the Ask tab?", [{"q": "hi", "a": "yo"}], "7d",
                                             runner=runner, base="http://x", claude_bin="/x/claude",
                                             fetch=fake_fetch, prefetch_runner=prefetch, db_path=dbp,
                                             peer_fan_out=lambda a, b: [])
        self.assertEqual(status, 200)
        self.assertEqual(body["agent"], "mazkir")
        self.assertEqual(body["cited"], ["c-100001", "kimi-1"])
        self.assertEqual([s["id"] for s in body["sources"]], ["c-100001", "kimi-1"])
        self.assertEqual(body["sources"][1]["title"], "[kimi] Handoff: ccc spawn + ccc models")
        self.assertEqual(body["actions"], [{"kind": "spawn-continue", "session_id": "c-100001"}])
        self.assertEqual(body["turns"], 2)
        argv = seen["argv"]
        self.assertEqual(argv[:2], ["/x/claude", "-p"])
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("mcp__ccc-state", argv)
        self.assertIn("Bash", argv[argv.index("--disallowedTools"):])
        cfg = json.loads(argv[argv.index("--mcp-config") + 1])
        self.assertEqual(set(cfg["mcpServers"]), {"claude-index", "ccc-state"})
        prompt = seen["kw"]["input"]
        self.assertIn("[[session:c-100001]]", prompt)
        self.assertIn("QUESTION: Where did I work on the Ask tab?", prompt)
        self.assertIn("Q: hi", prompt)
        self.assertNotIn("CLAUDECODE", seen["kw"]["env"])

    def test_engine_failures_map_to_status(self):
        prefetch = lambda argv, **kw: _Proc(stdout="[]")
        body, status = mazkir.run_mazkir("q", runner=lambda a, **k: _Proc(returncode=1, stderr="boom"),
                                         base="http://x", claude_bin="/x/claude", fetch=fake_fetch,
                                         prefetch_runner=prefetch, db_path="/nonexistent.db")
        self.assertEqual((status, body["code"]), (502, "ask_engine_error"))
        body, status = mazkir.run_mazkir("", base="http://x", fetch=fake_fetch)
        self.assertEqual(status, 400)
        with mock.patch.object(mazkir, "_find_claude_bin", lambda: None):
            body, status = mazkir.run_mazkir("q", base="http://x", fetch=fake_fetch, prefetch_runner=prefetch)
        self.assertEqual(status, 503)

    def test_signed_out_claude_is_a_clear_401(self):
        prefetch = lambda argv, **kw: _Proc(stdout="[]")
        out = json.dumps({"result": "Not logged in · Please run /login", "is_error": True})
        body, status = mazkir.run_mazkir("q", runner=lambda a, **k: _Proc(stdout=out), base="http://x",
                                         claude_bin="/x/claude", fetch=fake_fetch,
                                         prefetch_runner=prefetch, db_path="/nonexistent.db")
        self.assertEqual((status, body["code"]), (401, "ask_engine_unauthenticated"))
        self.assertIn("/login", body["error"])
        self.assertFalse(body["ok"])
        expired = json.dumps({"result": "Failed to authenticate: OAuth session expired and could not be refreshed",
                              "is_error": True})
        body, status = mazkir.run_mazkir("q", runner=lambda a, **k: _Proc(stdout=expired), base="http://x",
                                         claude_bin="/x/claude", fetch=fake_fetch,
                                         prefetch_runner=prefetch, db_path="/nonexistent.db")
        self.assertEqual(status, 401)

    def test_warm_up_reports_optional_features(self):
        with mock.patch.object(mazkir, "checkin_enabled", return_value=False), \
                mock.patch.object(mazkir, "INDEX_BIN", None), \
                mock.patch.object(mazkir, "_find_claude_bin", lambda: None), \
                mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": "", "CCC_ASK_WARM": "1"}):
            out = mazkir.warm_up("http://x")
        self.assertEqual((out["daily_checkin"], out["history_search"]), (False, False))
        self.assertEqual(out["code"], "ask_engine_unavailable")

    def test_uncited_unknown_ids_are_dropped(self):
        sources, cited, actions = mazkir.assemble_sources("see [[session:nope-1]]", [], "/nonexistent.db")
        self.assertEqual((sources, cited, actions), ([], [], []))



class OutsideUserDefaultsTest(unittest.TestCase):
    """No maintainer paths/names; optional pieces switch off cleanly."""

    def test_no_personal_defaults_in_source(self):
        src = Path(mazkir.__file__).read_text(encoding="utf-8")
        for needle in ("/Users/", "MyOfficeMgr", "Amir", "Becky", "BYM"):
            self.assertNotIn(needle, src)

    def test_index_bin_from_env_then_path_else_off(self):
        with mock.patch.dict(os.environ, {"CLAUDE_INDEX_BIN": sys.executable}):
            self.assertEqual(mazkir._resolve_index_bin(), sys.executable)
        with mock.patch.dict(os.environ, {"CLAUDE_INDEX_BIN": "/nonexistent/claude-index"}):
            self.assertIsNone(mazkir._resolve_index_bin())
        with mock.patch.dict(os.environ, {"CLAUDE_INDEX_BIN": ""}), \
                mock.patch.object(mazkir.shutil, "which", return_value=None):
            self.assertIsNone(mazkir._resolve_index_bin())

    def test_without_index_no_index_mcp_and_prompt_says_so(self):
        cfg = json.loads(mazkir.mcp_config("http://x", index_bin=None))
        self.assertEqual(list(cfg["mcpServers"]), ["ccc-state"])
        argv = mazkir.mazkir_argv("/x/claude", "http://x", None, index_bin=None)
        allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
        self.assertEqual(allowed, ["mcp__ccc-state"])
        self.assertIn("claude-index is not installed", argv[argv.index("--system-prompt") + 1])
        self.assertEqual(mazkir.prefetch_sessions("q", None, index_bin=None), [])
        with_index = mazkir.mazkir_argv("/x/claude", "http://x", None, index_bin="/x/claude-index")
        self.assertIn("mcp__claude-index", with_index)
        self.assertIn("claude-index", json.loads(mazkir.mcp_config("http://x", "/x/claude-index"))["mcpServers"])

    def test_checkin_only_offered_when_configured(self):
        missing = str(Path(tempfile.mkdtemp()) / "none.md")
        with mock.patch.dict(os.environ, {"CCC_DAILY_CHECKIN_FILE": ""}), \
                mock.patch.object(mazkir, "CHECKIN_PATH", missing):
            self.assertFalse(mazkir.checkin_enabled())
            self.assertNotIn("daily_checkin", [t["name"] for t in mazkir.available_tools()])
            self.assertNotIn("daily_checkin", mazkir.system_prompt("/x/claude-index"))
            st = mazkir.CccState("http://x", fetch=fake_fetch)
            r = mazkir.handle_request(st, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "daily_checkin", "arguments": {}}})
            self.assertEqual(r["error"]["code"], -32602)
        with mock.patch.dict(os.environ, {"CCC_DAILY_CHECKIN_FILE": missing}):
            self.assertTrue(mazkir.checkin_enabled())
            self.assertIn("daily_checkin", mazkir.system_prompt("/x/claude-index"))

    def test_builtin_prefetch_maps_ccc_search_rows(self):
        recent = {"results": [{"session_id": "sess-aaaaaa", "cwd": "/r/a", "ts_unix": 1700000000,
                               "snippet": "fixed the <mark>ask</mark> tab"}]}

        def history(*a, **k):
            raise OSError("index locked")
        seams = {"search_recent": lambda *a, **k: recent, "search_history": history,
                 "enrich": lambda hits: hits}
        out = mazkir.builtin_prefetch("ask tab fix", "7d", **seams)
        self.assertEqual(mazkir.builtin_prefetch("ask tab fix", "7d", exclude_session_ids={"sess-aaaaaa"},
                                                 **seams), [])
        self.assertEqual(out[0]["session_id"], "sess-aaaaaa")
        self.assertEqual(out[0]["best_snippet"], "fixed the ask tab")
        self.assertRegex(out[0]["last_ts"], r"^\d{4}-\d{2}-\d{2}$")




CHECKIN_MD = """# Daily check-in agenda

## 1. Immediate (today)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1.1 | Demo prep | today (ready) | replay param |
| 1.2 | Old thing | done | shipped |

## 2. Product

| # | Item | Status | Notes |
|---|------|--------|-------|
| 2.1 | Becky honesty | open | 1-in-3 hit rate |
| 2.2 | Dropped idea | dropped | no |

## Discussion log

- 2026-09-04: agenda created.
"""


class DailyCheckinTest(unittest.TestCase):
    def test_parse_open_items_and_log(self):
        out = mazkir.parse_checkin(CHECKIN_MD)
        self.assertEqual(out["open_count"], 2)
        self.assertEqual([s["title"] for s in out["sections"]], ["1. Immediate (today)", "2. Product"])
        self.assertEqual(out["sections"][0]["items"], [
            {"id": "1.1", "item": "Demo prep", "status": "today", "notes": "replay param"}])
        self.assertEqual(out["discussion_log"], ["2026-09-04: agenda created."])
        everything = mazkir.parse_checkin(CHECKIN_MD, include_closed=True)
        self.assertEqual(sum(len(s["items"]) for s in everything["sections"]), 4)
        self.assertEqual(everything["open_count"], 2)

    def test_tool_reads_file_and_degrades(self):
        out = mazkir.tool_daily_checkin("/x/agenda.md", reader=lambda p: CHECKIN_MD)
        self.assertTrue(out["available"])
        self.assertEqual(out["open_count"], 2)

        def missing(p):
            raise FileNotFoundError(p)
        out = mazkir.tool_daily_checkin("/x/missing.md", reader=missing)
        self.assertFalse(out["available"])
        self.assertEqual(out["sections"], [])

    def test_mcp_dispatch(self):
        st = mazkir.CccState("http://x", fetch=fake_fetch)
        with mock.patch.object(mazkir, "checkin_enabled", return_value=True):
            r = mazkir.handle_request(st, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "daily_checkin", "arguments": {}}})
        body = json.loads(r["result"]["content"][0]["text"])
        self.assertIn("open_count", body)
        self.assertIn("path", body)


class SourceHygieneTest(unittest.TestCase):
    def test_clean_title_strips_preamble_and_names_continuations(self):
        self.assertEqual(mazkir.clean_title(
            "Heads-up: this may already be shipped: feat(x): thing (ccc abc123), 2 days ago. "
            "Verify before rebuilding. Add resume-from-session"), "Add resume-from-session")
        self.assertEqual(mazkir.clean_title(
            "Continue session 3dc24eb9 ('Second brain: entity pages'), which was idle"),
            "Continue: Second brain: entity pages")
        self.assertEqual(mazkir.clean_title("continuing 5e8dbbee-b2e0-49bf-aa59-a83b34e9a210 ok we got cards"),
                         "ok we got cards")
        long = mazkir.clean_title("word " * 40)
        self.assertLessEqual(len(long), mazkir.TITLE_MAX)
        self.assertTrue(long.endswith("…"))

    def test_prepare_candidates_hides_evals_merges_loops_prefers_ccc_titles(self):
        cands = [
            {"session_id": "a1", "title": "Drain the MEMORY queue and keep it empty."},
            {"session_id": "e1", "title": "Should I raise Meta budget? (Evaluation run r09. READ-ONLY)"},
            {"session_id": "a2", "title": "Drain the MEMORY queue and keep it empty."},
            {"session_id": "b1", "title": "You are the COORDINATOR of the sprint"},
        ]
        out, stats = mazkir.prepare_candidates(cands, "what are we working on", {"b1": "Thursday Blast coordinator"})
        self.assertEqual([c["session_id"] for c in out], ["a1", "b1"])
        self.assertEqual(out[0]["runs"], 2)
        self.assertEqual(out[1]["title"], "Thursday Blast coordinator")
        self.assertEqual(stats, {"evals_hidden": 1, "runs_merged": 1})
        self.assertIn("runs=2", mazkir._fmt_candidate(1, out[0]))
        kept, _ = mazkir.prepare_candidates(cands, "how did the eval runs go", {})
        self.assertIn("e1", [c["session_id"] for c in kept])

    def test_titles_cut_inside_a_preamble_use_the_first_message(self):
        cut = "Heads-up: this may already be shipped: feat(x): y (wt 1), confidence 0.9. Verify before rebuilding. You ar"
        out, _ = mazkir.prepare_candidates(
            [{"session_id": "s1", "title": cut}], "q",
            full_title=lambda ids: {i: cut[:-6] + " You are the BYM UX worker" for i in ids})
        self.assertEqual(out[0]["title"], "You are the BYM UX worker")

    def test_ccc_titles_follow_sidebar_priority(self):
        meta = {"/p/-a/s1.jsonl": {"custom_title": "AUG-4#13: expiry", "ai_title": "ai one"},
                "/p/-a/s2.jsonl": {"custom_title": None, "ai_title": "Questions about Twilio"},
                "/p/-a/s1/subagents/agent-x.jsonl": {"custom_title": "subagent"},
                "/p/-a/s4.jsonl": {"custom_title": "cust"}}
        got = mazkir.ccc_titles(["s1", "s2", "s3", "s4", "s5"], {"s4": "My rename"}, meta, {"s3": "auto three"})
        self.assertEqual(got, {"s1": "AUG-4#13: expiry", "s2": "Questions about Twilio",
                               "s3": "auto three", "s4": "My rename"})

    def test_build_trace_names_servers_and_args(self):
        trace = mazkir.build_trace("claude-index · sessions search", 8, 5, 900,
                                   {"evals_hidden": 2, "runs_merged": 1},
                                   [{"name": "mcp__ccc-state__daily_brief", "input": {}},
                                    {"name": "mcp__claude-index__search_sessions", "input": {"query": "bym ads"}}])
        self.assertEqual(trace[0]["detail"], "8 candidates, 5 kept (2 eval runs hidden, 1 repeat run merged) · 0.9s")
        self.assertEqual(trace[2], {"tool": "ccc-state · daily_brief", "detail": ""})
        self.assertEqual(trace[3], {"tool": "claude-index · search_sessions", "detail": '"bym ads"'})

    def test_sources_flag_which_were_cited(self):
        cands = [{"session_id": "aaaaaa1", "title": "One"}, {"session_id": "bbbbbb2", "title": "Two"}]
        sources, cited, _ = mazkir.assemble_sources("see [[session:bbbbbb2]]", cands, "/nonexistent.db")
        self.assertEqual([(s["id"], s["cited"]) for s in sources], [("bbbbbb2", True), ("aaaaaa1", False)])


class FocusedSessionAndInjectTest(unittest.TestCase):
    """CCC-1214: Mazkir knows the on-screen session and can explain a failed inject."""

    def test_focused_session_lands_in_prompt(self):
        sid = "8ec0c98c-1127-4fad-a43a-eba5fc437fb9"
        prompt = mazkir.build_prompt("why won't this session take my message?", [], [], "fleet: x", None,
                                     focused={"session_id": sid, "title": "Fix  the\nlane map"})
        self.assertIn(f'ON SCREEN: the user is viewing session {sid} ("Fix the lane map")', prompt)
        self.assertIn("the ON SCREEN session", mazkir.system_prompt())
        self.assertIn("inject_diagnostics", mazkir.system_prompt())

    def test_git_requests_route_to_a_spawn_proposal(self):
        # FEAT-NEXT-148: "push bym" got "I can't push" instead of a Confirm card.
        prompt = mazkir.system_prompt()
        self.assertIn('Git work ("push bym"', prompt)
        self.assertIn("Never ask it to force-push or skip hooks", prompt)

    def test_bad_or_missing_focus_is_dropped(self):
        for focused in (None, {}, {"session_id": ""}, {"session_id": "x y; rm"}, "abc"):
            self.assertEqual(mazkir.focused_line(focused), "")
            self.assertNotIn("ON SCREEN", mazkir.build_prompt("q", [], [], "fleet", None, focused=focused))

    def test_inject_diagnostics_surfaces_rejections_and_receipts(self):
        sid = "s-question"
        events = [
            {"ts": "t1", "category": "inject", "verb": "INJECT", "detail": f"session={sid} ok"},
            {"ts": "t2", "category": "inject", "verb": "INJECT_REJECT",
             "detail": f"session={sid} code=repo_not_allowed error=x"},
            {"ts": "t3", "category": "inject", "verb": "Q_HELD", "detail": f"session={sid} reason=headless_turn"},
        ]
        receipts = {"outstanding": {"inject_id": "i1", "text_preview": "hello", "source": "ui",
                                    "sent_ts": 1.0, "age_s": 95.0}}

        def fetch(path):
            if path.startswith("/api/session/"):
                return receipts
            if path.startswith("/api/activity-log"):
                self.assertIn(f"session_id={sid}", path)
                return {"ok": True, "events": events}
            return fake_fetch(path)
        out = mazkir.CccState("http://x", fetch=fetch).call("inject_diagnostics", {"session_id": sid})
        text = "\n".join(out["findings"])
        self.assertIn("undelivered inject 95s old", text)
        self.assertIn("force-restart", text)
        self.assertIn("INJECT_REJECT (repo_not_allowed)", text)
        self.assertIn("Q_HELD (headless_turn)", text)
        self.assertIn("no live process", text)
        self.assertEqual(len(out["recent_inject_events"]), 3)
        self.assertIn("inject_diagnostics", [t["name"] for t in mazkir.available_tools()])

    def test_inject_diagnostics_clean_and_invalid_ids(self):
        def fetch(path):
            if path.startswith("/api/session/"):
                return {"outstanding": None}
            if path.startswith("/api/activity-log"):
                return {"events": [{"ts": "t", "category": "inject", "verb": "INJECT", "detail": "ok"}]}
            return fake_fetch(path)
        st = mazkir.CccState("http://x", fetch=fetch)
        out = st.call("inject_diagnostics", {"session_id": "s-working-ok"})
        self.assertEqual(len(out["findings"]), 1)
        self.assertIn("no delivery problem recorded", out["findings"][0])
        self.assertIn("error", st.call("inject_diagnostics", {"session_id": "../etc"}))

    def test_ask_forwards_focus_to_mazkir(self):
        from ccc_server import ask
        seen = {}

        def fake_run(question, history, range_key, focused=None):
            seen["focused"] = focused
            return {"ok": True}, 200
        with mock.patch.object(mazkir, "run_mazkir", fake_run), \
                mock.patch.dict(os.environ, {"CCC_ASK_MODE": "mazkir"}):
            ask.handle_assistant_ask({"question": "q", "focused": {"session_id": "abc12345"}})
        self.assertEqual(seen["focused"], {"session_id": "abc12345"})



class TurnUsageTests(unittest.TestCase):
    """Ask shows each answer's cost in cache-adjusted tokens (FEAT-NEXT-150)."""

    def setUp(self):
        import server  # noqa: F401 -- usage_stats resolves helpers through it

    def test_cache_adjusted_weights_reads_and_1h_writes(self):
        res = mazkir.parse_result(json.dumps({
            "result": "ok",
            "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1379,
                      "cache_read_input_tokens": 35259, "output_tokens": 10,
                      "cache_creation": {"ephemeral_1h_input_tokens": 1379,
                                         "ephemeral_5m_input_tokens": 0}},
            "modelUsage": {"claude-sonnet-5-5": {}},
        }))
        u = mazkir.turn_usage(res)
        self.assertEqual(u["model"], "claude-sonnet-5-5")
        self.assertEqual(u["cache_read_input_tokens"], 35259)
        # 2 fresh + 1379 x 2.0 (1h write) + 35259 x 0.1 (read) + 10 out
        self.assertEqual(u["cache_adjusted_tokens"], 6296)

    def test_missing_usage_is_none(self):
        self.assertIsNone(mazkir.turn_usage(mazkir.parse_result("plain text")))


if __name__ == "__main__":
    unittest.main()
