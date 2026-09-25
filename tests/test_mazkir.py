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
                                 "session_detail", "fleet_diagnostics", "daily_checkin", "daily_brief",
                                 "hunch_why", "propose_spawn_session", "propose_inject",
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

        body, status = mazkir.run_mazkir("Where did I work on the Ask tab?", [{"q": "hi", "a": "yo"}], "7d",
                                         runner=runner, base="http://x", claude_bin="/x/claude",
                                         fetch=fake_fetch, prefetch_runner=prefetch, db_path=dbp)
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
        from ccc_server import core as _core
        recent = {"results": [{"session_id": "sess-aaaaaa", "cwd": "/r/a", "ts_unix": 1700000000,
                               "snippet": "fixed the <mark>ask</mark> tab"}]}
        with mock.patch.object(_core, "search_recent_sessions", return_value=recent, create=True), \
                mock.patch.object(_core, "search_conversation_history", side_effect=OSError, create=True), \
                mock.patch("ccc_server.ask.enrich_ask_hits", side_effect=lambda h: h):
            out = mazkir.builtin_prefetch("ask tab fix", "7d")
            self.assertEqual(mazkir.builtin_prefetch("ask tab fix", "7d", exclude_session_ids={"sess-aaaaaa"}), [])
        self.assertEqual(out[0]["session_id"], "sess-aaaaaa")
        self.assertEqual(out[0]["best_snippet"], "fixed the ask tab")
        self.assertRegex(out[0]["last_ts"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()


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
