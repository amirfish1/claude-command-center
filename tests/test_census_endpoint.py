"""Coverage for /api/sessions/census (build_session_census).

The census joins liveness + identity + lineage server-side so the `ccc` CLI
answers "what sessions exist and what are they doing" in one call. These
tests pin the contract: rows for every live session, garbage timestamps
clamped to None (never a bogus age), rowless sessions flagged
has_conversation_row=False, and parent/children inversion.
"""

import importlib
import json
import pathlib
import tempfile
import time
import unittest

server = importlib.import_module("server")

_STATES = {"ended", "waiting", "working", "idle"}


def _live_entry(**over):
    base = {
        "is_live": True,
        "sidecar_status": "waiting",
        "sidecar_ts": time.time(),
        "sidecar_in_flight": False,
        "pending_tool": None,
        "question_waiting": False,
        "needs_approval": False,
    }
    base.update(over)
    return base


class TestBuildSessionCensus(unittest.TestCase):
    def setUp(self):
        self._orig_activity = server.build_live_sessions_activity
        self._orig_identity = server._census_identity_map

    def tearDown(self):
        server.build_live_sessions_activity = self._orig_activity
        server._census_identity_map = self._orig_identity

    def _stub(self, activity, identity=None):
        server.build_live_sessions_activity = lambda: activity
        server._census_identity_map = lambda: (identity or {})

    def test_row_per_live_session_sorted_recent_first(self):
        now = time.time()
        self._stub({
            "sid-old": _live_entry(sidecar_ts=now - 500),
            "sid-new": _live_entry(sidecar_ts=now - 5),
        })
        out = server.build_session_census()
        self.assertTrue(out["ok"])
        self.assertEqual([r["session_id"] for r in out["sessions"]], ["sid-new", "sid-old"])
        for row in out["sessions"]:
            self.assertIn(row["state"], _STATES)
            self.assertIsNotNone(row["last_event_age_s"])

    def test_garbage_timestamp_clamps_to_null_and_sorts_last(self):
        now = time.time()
        self._stub({
            "sid-good": _live_entry(sidecar_ts=now - 5),
            "sid-zero": _live_entry(sidecar_ts=0),
            "sid-future": _live_entry(sidecar_ts=now + 99999),
        })
        rows = server.build_session_census()["sessions"]
        by_sid = {r["session_id"]: r for r in rows}
        self.assertIsNone(by_sid["sid-zero"]["last_event_age_s"])
        self.assertIsNone(by_sid["sid-future"]["last_event_age_s"])
        self.assertEqual(rows[0]["session_id"], "sid-good")

    def test_rowless_session_flagged_and_identity_joined(self):
        now = time.time()
        self._stub(
            {"sid-rowless": _live_entry(sidecar_ts=now - 3)},
            {"sid-rowless": {
                "name": "wt worker", "engine": "grok", "model": "grok-4.1",
                "effort": "med", "repo_path": "/x/BYM", "parent_session_id": None,
                "has_conversation_row": False,
            }},
        )
        row = server.build_session_census()["sessions"][0]
        self.assertFalse(row["has_conversation_row"])
        self.assertEqual(row["engine"], "grok")
        self.assertEqual(row["model"], "grok-4.1")
        self.assertEqual(row["effort"], "med")
        self.assertEqual(row["name"], "wt worker")

    def test_no_identity_means_nulls_not_crash(self):
        self._stub({"sid-mystery": _live_entry()})
        row = server.build_session_census()["sessions"][0]
        self.assertIsNone(row["engine"])
        self.assertIsNone(row["name"])
        self.assertFalse(row["has_conversation_row"])

    def test_children_inverted_from_parent_links(self):
        now = time.time()
        self._stub(
            {
                "parent-1": _live_entry(sidecar_ts=now - 2),
                "child-1": _live_entry(sidecar_ts=now - 1),
            },
            {
                "parent-1": {"name": "p", "engine": "grok", "repo_path": None,
                             "parent_session_id": None, "has_conversation_row": True},
                "child-1": {"name": "c", "engine": "grok", "repo_path": None,
                            "parent_session_id": "parent-1", "has_conversation_row": True},
            },
        )
        rows = {r["session_id"]: r for r in server.build_session_census()["sessions"]}
        self.assertEqual(rows["parent-1"]["children"], ["child-1"])
        self.assertEqual(rows["child-1"]["parent_session_id"], "parent-1")
        self.assertEqual(rows["child-1"]["children"], [])

    def test_flags_pass_through(self):
        self._stub({"sid-q": _live_entry(question_waiting=True, needs_approval=True,
                                         pending_tool="AskUserQuestion")})
        row = server.build_session_census()["sessions"][0]
        self.assertTrue(row["question_waiting"])
        self.assertTrue(row["needs_approval"])
        self.assertEqual(row["pending_tool"], "AskUserQuestion")

    def test_since_merges_recently_ended_archive_sessions(self):
        now = time.time()
        self._stub(
            {"sid-live": _live_entry(sidecar_ts=now - 5)},
            {
                "sid-live": {"name": "live", "engine": "claude",
                             "has_conversation_row": True, "mtime": now - 5},
                "sid-ended-recent": {"name": "recent", "engine": "kimi",
                                     "has_conversation_row": True, "mtime": now - 3600},
                "sid-ended-old": {"name": "old", "engine": "claude",
                                  "has_conversation_row": True, "mtime": now - 90000},
                "sid-registry-only": {"name": "spawn", "engine": "codex",
                                      "has_conversation_row": False, "mtime": now - 60},
            },
        )
        out = server.build_session_census(since_s=10 * 3600)
        by_sid = {r["session_id"]: r for r in out["sessions"]}
        self.assertIn("sid-live", by_sid)
        self.assertEqual(by_sid["sid-ended-recent"]["state"], "ended")
        self.assertAlmostEqual(by_sid["sid-ended-recent"]["last_event_age_s"], 3600, delta=5)
        self.assertEqual(by_sid["sid-ended-recent"]["engine"], "kimi")
        # Outside the window and registry-only rows never appear.
        self.assertNotIn("sid-ended-old", by_sid)
        self.assertNotIn("sid-registry-only", by_sid)
        # Default call (no since) stays live-only.
        out_live = server.build_session_census()
        self.assertEqual([r["session_id"] for r in out_live["sessions"]], ["sid-live"])


class TestCensusIdentityMap(unittest.TestCase):
    def setUp(self):
        self._orig_registry = server._load_spawn_registry
        self._orig_cache = dict(server._ARCHIVE_RESPONSE_CACHE)
        self._orig_loaded = server._ARCHIVE_RESPONSE_CACHE_LOADED
        server._ARCHIVE_RESPONSE_CACHE.clear()
        server._ARCHIVE_RESPONSE_CACHE_LOADED = True
        server._CENSUS_IDENTITY_CACHE["token"] = None
        server._CENSUS_IDENTITY_CACHE["map"] = {}

    def tearDown(self):
        server._load_spawn_registry = self._orig_registry
        server._ARCHIVE_RESPONSE_CACHE.clear()
        server._ARCHIVE_RESPONSE_CACHE.update(self._orig_cache)
        server._ARCHIVE_RESPONSE_CACHE_LOADED = self._orig_loaded
        server._CENSUS_IDENTITY_CACHE["token"] = None
        server._CENSUS_IDENTITY_CACHE["map"] = {}

    def test_registry_identity_and_archive_override(self):
        server._load_spawn_registry = lambda: [{
            "session_id": "sid-1", "engine": "codex", "name": "spawned",
            "model": "gpt-5.6-sol", "cwd": "/x/from-registry",
            "parent_session_id": "parent-9",
        }]
        server._ARCHIVE_RESPONSE_CACHE["k"] = {
            "cached_at": time.time(),
            "conversations": [{
                "session_id": "sid-2", "display_name": "Archived\nName",
                "engine": "claude", "model": "claude-fable-5",
                "reasoning_effort": "high", "session_cwd": "/x/from-archive",
            }],
            "signature": None,
        }
        out = server._census_identity_map()
        self.assertEqual(out["sid-1"]["engine"], "codex")
        self.assertEqual(out["sid-1"]["model"], "gpt-5.6-sol")
        self.assertFalse(out["sid-1"]["has_conversation_row"])
        self.assertEqual(out["sid-1"]["parent_session_id"], "parent-9")
        self.assertEqual(out["sid-2"]["name"], "Archived Name")
        self.assertEqual(out["sid-2"]["model"], "claude-fable-5")
        self.assertEqual(out["sid-2"]["effort"], "high")
        self.assertEqual(out["sid-2"]["repo_path"], "/x/from-archive")
        self.assertTrue(out["sid-2"]["has_conversation_row"])

    def test_memoized_until_token_changes(self):
        calls = []
        server._load_spawn_registry = lambda: calls.append(1) or []
        first = server._census_identity_map()
        second = server._census_identity_map()
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_cold_archive_cache_kicks_background_refresh(self):
        kicked = []
        orig_refresh = server._archive_refresh_response_cache_async
        server._archive_refresh_response_cache_async = (
            lambda key, options: kicked.append((key, options)) or True
        )
        try:
            server._load_spawn_registry = lambda: []
            server._census_identity_map()
        finally:
            server._archive_refresh_response_cache_async = orig_refresh
        self.assertEqual(len(kicked), 1)

    def test_warm_archive_cache_does_not_kick(self):
        kicked = []
        orig_refresh = server._archive_refresh_response_cache_async
        server._archive_refresh_response_cache_async = (
            lambda key, options: kicked.append((key, options)) or True
        )
        try:
            server._load_spawn_registry = lambda: []
            server._ARCHIVE_RESPONSE_CACHE["k"] = {
                "cached_at": time.time(), "conversations": [], "signature": None,
            }
            server._CENSUS_IDENTITY_CACHE["token"] = None
            server._census_identity_map()
        finally:
            server._archive_refresh_response_cache_async = orig_refresh
        self.assertEqual(kicked, [])


class TestCensusHelperProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_projects = server._SYS_PROJECTS_DIR
        server._SYS_PROJECTS_DIR = self._tmp.name
        server._CENSUS_PROBE_CACHE.clear()
        proj = pathlib.Path(self._tmp.name) / "-scratch"
        proj.mkdir()
        self.proj = proj

    def tearDown(self):
        server._SYS_PROJECTS_DIR = self._orig_projects
        server._CENSUS_PROBE_CACHE.clear()
        self._tmp.cleanup()

    def _write_transcript(self, sid, prompt):
        ev = {"type": "user", "message": {"role": "user", "content": prompt}}
        (self.proj / f"{sid}.jsonl").write_text(json.dumps(ev) + "\n")

    def test_title_bot_classified_as_helper(self):
        self._write_transcript(
            "sid-bot",
            "Produce a concise 4-8 word title summarizing what the user is trying to do below. …",
        )
        probe = server._census_probe_transcript("sid-bot")
        self.assertTrue(probe["is_helper"])
        self.assertIn("4-8 word title", probe["first_message"])

    def test_real_session_gets_identity_not_helper(self):
        self._write_transcript("sid-real", "Fix the failing payment webhook")
        probe = server._census_probe_transcript("sid-real")
        self.assertFalse(probe["is_helper"])
        self.assertEqual(probe["first_message"], "Fix the failing payment webhook")

    def test_no_transcript_returns_none(self):
        self.assertIsNone(server._census_probe_transcript("sid-missing"))

    def test_census_marks_helper_rows(self):
        self._write_transcript(
            "sid-bot",
            "Produce a concise 4-8 word title summarizing what the user is trying to do below. …",
        )
        orig_activity = server.build_live_sessions_activity
        orig_identity = server._census_identity_map
        server.build_live_sessions_activity = lambda: {
            "sid-bot": {"is_live": True, "sidecar_status": "idle", "sidecar_ts": time.time()},
        }
        server._census_identity_map = lambda: {}
        try:
            row = server.build_session_census()["sessions"][0]
        finally:
            server.build_live_sessions_activity = orig_activity
            server._census_identity_map = orig_identity
        self.assertTrue(row["helper"])
        self.assertEqual(row["engine"], "claude")
        self.assertIn("[helper]", row["name"])


class TestCensusUnansweredInput(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        server._CENSUS_UNANSWERED_CACHE.clear()

    def tearDown(self):
        server._CENSUS_UNANSWERED_CACHE.clear()
        self._tmp.cleanup()

    def _write(self, name, events):
        p = pathlib.Path(self._tmp.name) / name
        p.write_text("".join(json.dumps(e) + "\n" for e in events))
        return str(p)

    def test_claude_user_last_is_unanswered(self):
        path = self._write("s.jsonl", [
            {"type": "assistant", "message": {"role": "assistant", "content": "done"}},
            {"type": "user", "message": {"role": "user", "content": "are you there?"}},
        ])
        self.assertTrue(server._census_unanswered_input("s1", path))

    def test_claude_assistant_last_is_answered(self):
        path = self._write("s.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "hello"}},
        ])
        self.assertFalse(server._census_unanswered_input("s2", path))

    def test_claude_tool_result_tail_not_unanswered(self):
        path = self._write("s.jsonl", [
            {"type": "assistant", "message": {"role": "assistant", "content": "working"}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        ])
        self.assertFalse(server._census_unanswered_input("s3", path))

    def test_grok_user_chunk_last_is_unanswered(self):
        grok_dir = pathlib.Path(self._tmp.name) / ".grok"
        grok_dir.mkdir()
        gpath = grok_dir / "updates.jsonl"
        gpath.write_text(
            json.dumps({"params": {"update": {"sessionUpdate": "agent_message_chunk"}}}) + "\n"
            + json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n"
        )
        self.assertTrue(server._census_unanswered_input("s4", str(gpath)))

    def test_grok_turn_completed_last_is_answered(self):
        grok_dir = pathlib.Path(self._tmp.name) / ".grok"
        grok_dir.mkdir()
        gpath = grok_dir / "updates.jsonl"
        gpath.write_text(
            json.dumps({"params": {"update": {"sessionUpdate": "user_message_chunk"}}}) + "\n"
            + json.dumps({"params": {"update": {"sessionUpdate": "turn_completed"}}}) + "\n"
        )
        self.assertFalse(server._census_unanswered_input("s5", str(gpath)))

    def test_missing_file_is_false(self):
        self.assertFalse(server._census_unanswered_input(
            "s6", str(pathlib.Path(self._tmp.name) / "nope.jsonl")))

    def test_ended_row_carries_unanswered_flag(self):
        path = self._write("ended.jsonl", [
            {"type": "assistant", "message": {"role": "assistant", "content": "bye"}},
            {"type": "user", "message": {"role": "user", "content": "hello?"}},
        ])
        now = time.time()
        orig_activity = server.build_live_sessions_activity
        orig_identity = server._census_identity_map
        server.build_live_sessions_activity = lambda: {}
        server._census_identity_map = lambda: {
            "sid-dead": {"name": "dead", "engine": "grok",
                         "has_conversation_row": True, "mtime": now - 60,
                         "jsonl_path": path},
        }
        try:
            rows = server.build_session_census(since_s=3600)["sessions"]
        finally:
            server.build_live_sessions_activity = orig_activity
            server._census_identity_map = orig_identity
        self.assertTrue(rows[0]["unanswered_input"])


class TestAcpLiveDiscovery(unittest.TestCase):
    """ACP-attached sessions (kimi/grok) own no per-session OS process, so
    registry/resume-arg/sidecar discovery never nominates them. The ACP
    registry union in _discover_live_session_ids is what makes them visible
    — gated to attached, non-closed, recently-active sessions."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_registry = server._load_session_registry
        self._orig_engine = server._live_engine_session_ids
        self._orig_sidecar_dir = server.SIDECAR_STATE_DIR
        self._orig_harnesses = server._ACP_HARNESSES
        self._orig_enabled = server._acp_harness_enabled
        self._orig_load = server._acp_load_state
        self._orig_state = server._ACP_SESSION_STATE
        server._load_session_registry = lambda: {}
        server._live_engine_session_ids = lambda: frozenset()
        server.SIDECAR_STATE_DIR = pathlib.Path(self._tmp.name)  # empty dir
        server._ACP_HARNESSES = {"kimi": {"label": "Kimi"}, "grok": {"label": "Grok"}}
        server._acp_harness_enabled = lambda harness: harness != "grok"
        server._acp_load_state = lambda harness: None

    def tearDown(self):
        server._load_session_registry = self._orig_registry
        server._live_engine_session_ids = self._orig_engine
        server.SIDECAR_STATE_DIR = self._orig_sidecar_dir
        server._ACP_HARNESSES = self._orig_harnesses
        server._acp_harness_enabled = self._orig_enabled
        server._acp_load_state = self._orig_load
        server._ACP_SESSION_STATE = self._orig_state
        self._tmp.cleanup()

    def _set_acp(self, sessions):
        server._ACP_SESSION_STATE = {"kimi": sessions, "grok": {"grok-1": {
            "attached": True, "status": "idle", "updated_at": time.time()}}}

    def test_fresh_attached_session_nominated(self):
        now = time.time()
        self._set_acp({"session_k1": {"attached": True, "status": "idle", "updated_at": now - 5}})
        self.assertIn("session_k1", server._discover_live_session_ids())

    def test_grok_included_via_acp_even_without_tui_process(self):
        # The disabled-harness stub excludes grok here; enable it to prove
        # the harness-driven (no `grok --resume` process) path nominates too.
        server._acp_harness_enabled = lambda harness: True
        self._set_acp({})
        self.assertIn("grok-1", server._discover_live_session_ids())

    def test_disabled_harness_closed_unattached_and_stale_excluded(self):
        now = time.time()
        self._set_acp({
            "session_closed": {"attached": True, "status": "closed", "updated_at": now - 5},
            "session_detached": {"attached": False, "status": "idle", "updated_at": now - 5},
            "session_stale": {"attached": True, "status": "idle",
                              "updated_at": now - server._SIDECAR_LIVE_WINDOW - 60},
            "session_nots": {"attached": True, "status": "idle", "updated_at": 0},
        })
        sids = server._discover_live_session_ids()
        for sid in ("session_closed", "session_detached", "session_stale", "session_nots", "grok-1"):
            self.assertNotIn(sid, sids)


class TestAcpLiveActivityFields(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state = server._ACP_SESSION_STATE
        self._orig_load = server._acp_load_state
        self._orig_kidx = server._kimi_session_index
        self._orig_ktail = server._kimi_wire_tail_meta
        self._orig_gsrc = server._grok_conversation_source
        server._acp_load_state = lambda harness: None
        server._ACP_SESSION_STATE = {}

    def tearDown(self):
        server._ACP_SESSION_STATE = self._orig_state
        server._acp_load_state = self._orig_load
        server._kimi_session_index = self._orig_kidx
        server._kimi_wire_tail_meta = self._orig_ktail
        server._grok_conversation_source = self._orig_gsrc
        self._tmp.cleanup()

    def test_kimi_wire_tail_drives_state_and_age(self):
        now = time.time()
        server._kimi_session_index = lambda: {"session_k1": {"session_dir": "/x"}}
        server._kimi_wire_tail_meta = lambda d: {
            "last_event_type": "assistant", "pending_tool": "Bash",
            "mid_turn": True, "wire_mtime": now - 3}
        out = server._acp_live_activity_fields("kimi", "session_k1")
        self.assertEqual(out["sidecar_ts"], now - 3)
        self.assertEqual(out["pending_tool"], "Bash")
        self.assertEqual(out["last_event_type"], "assistant")
        self.assertFalse(out["needs_approval"])

    def test_kimi_mid_turn_without_tool_reads_thinking(self):
        server._kimi_session_index = lambda: {"session_k1": {"session_dir": "/x"}}
        server._kimi_wire_tail_meta = lambda d: {
            "last_event_type": "assistant", "pending_tool": None,
            "mid_turn": True, "wire_mtime": time.time()}
        self.assertEqual(
            server._acp_live_activity_fields("kimi", "session_k1")["pending_tool"],
            "Thinking")

    def test_pending_permissions_flag_needs_approval(self):
        server._kimi_session_index = lambda: {}
        server._ACP_SESSION_STATE = {"kimi": {"session_k1": {
            "attached": True, "pending_permissions": [{"request_id": "r1"}],
            "updated_at": time.time() - 9}}}
        out = server._acp_live_activity_fields("kimi", "session_k1")
        self.assertTrue(out["needs_approval"])
        # No wire signal → falls back to the registry's updated_at.
        self.assertAlmostEqual(out["sidecar_ts"], time.time() - 9, delta=2)

    def test_grok_tail_mid_turn_then_finished(self):
        path = pathlib.Path(self._tmp.name) / "grok.jsonl"
        server._grok_conversation_source = lambda sid: path
        server._GROK_WIRE_TAIL_CACHE.clear()
        path.write_text(
            json.dumps({"type": "user_text"}) + "\n" + json.dumps({"type": "assistant"}) + "\n")
        out = server._acp_live_activity_fields("grok", "g1")
        self.assertEqual(out["pending_tool"], "Thinking")
        self.assertEqual(out["last_event_type"], "assistant")
        with path.open("a") as f:
            f.write(json.dumps({"type": "result"}) + "\n")
        out = server._acp_live_activity_fields("grok", "g1")
        self.assertIsNone(out["pending_tool"])
        self.assertEqual(out["last_event_type"], "result")


class TestCensusCodexPoolIdentity(unittest.TestCase):
    def setUp(self):
        self._orig_registry = server._load_spawn_registry
        self._orig_threads = server._codex_fetch_threads
        self._orig_cache = dict(server._ARCHIVE_RESPONSE_CACHE)
        self._orig_loaded = server._ARCHIVE_RESPONSE_CACHE_LOADED
        server._ARCHIVE_RESPONSE_CACHE.clear()
        server._ARCHIVE_RESPONSE_CACHE_LOADED = True
        server._CENSUS_IDENTITY_CACHE["token"] = None
        server._CENSUS_IDENTITY_CACHE["map"] = {}

    def tearDown(self):
        server._load_spawn_registry = self._orig_registry
        server._codex_fetch_threads = self._orig_threads
        server._ARCHIVE_RESPONSE_CACHE.clear()
        server._ARCHIVE_RESPONSE_CACHE.update(self._orig_cache)
        server._ARCHIVE_RESPONSE_CACHE_LOADED = self._orig_loaded
        server._CENSUS_IDENTITY_CACHE["token"] = None
        server._CENSUS_IDENTITY_CACHE["map"] = {}

    def test_pool_thread_identity_filled(self):
        server._load_spawn_registry = lambda: []
        server._codex_fetch_threads = lambda limit=None: [{
            "id": "pool-1", "title": "  Fix\n the flaky test  ",
            "model": "gpt-5.6-terra", "reasoning_effort": "xhigh",
            "cwd": "/x/BYM", "updated_at": 1788286000,
        }]
        out = server._census_identity_map()
        row = out["pool-1"]
        self.assertEqual(row["engine"], "codex")
        self.assertEqual(row["name"], "Fix the flaky test")
        self.assertEqual(row["model"], "gpt-5.6-terra")
        self.assertEqual(row["effort"], "xhigh")
        self.assertEqual(row["repo_path"], "/x/BYM")
        # Live-row identity only — never leaks into the ended-merge.
        self.assertFalse(row["has_conversation_row"])

    def test_pool_fill_never_overrides_known_identity(self):
        server._load_spawn_registry = lambda: [{
            "session_id": "pool-1", "engine": "codex", "name": "spawned-name"}]
        server._codex_fetch_threads = lambda limit=None: [{
            "id": "pool-1", "title": "sqlite-name", "cwd": "/x/BYM"}]
        self.assertEqual(server._census_identity_map()["pool-1"]["name"], "spawned-name")


if __name__ == "__main__":
    unittest.main()
