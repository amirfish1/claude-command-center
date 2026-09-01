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
        server._CENSUS_HELPER_PROBE_CACHE.clear()
        proj = pathlib.Path(self._tmp.name) / "-scratch"
        proj.mkdir()
        self.proj = proj

    def tearDown(self):
        server._SYS_PROJECTS_DIR = self._orig_projects
        server._CENSUS_HELPER_PROBE_CACHE.clear()
        self._tmp.cleanup()

    def _write_transcript(self, sid, prompt):
        ev = {"type": "user", "message": {"role": "user", "content": prompt}}
        (self.proj / f"{sid}.jsonl").write_text(json.dumps(ev) + "\n")

    def test_title_bot_classified_as_helper(self):
        self._write_transcript(
            "sid-bot",
            "Produce a concise 4-8 word title summarizing what the user is trying to do below. …",
        )
        is_helper, msg = server._census_probe_helper_session("sid-bot")
        self.assertTrue(is_helper)
        self.assertIn("4-8 word title", msg)
        # Cached: second call returns the same object without re-reading.
        (self.proj / "sid-bot.jsonl").unlink()
        self.assertEqual(server._census_probe_helper_session("sid-bot"), (True, msg))

    def test_real_session_not_helper(self):
        self._write_transcript("sid-real", "Fix the failing payment webhook")
        is_helper, _ = server._census_probe_helper_session("sid-real")
        self.assertFalse(is_helper)

    def test_no_transcript_not_helper(self):
        is_helper, msg = server._census_probe_helper_session("sid-missing")
        self.assertFalse(is_helper)
        self.assertIsNone(msg)

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


if __name__ == "__main__":
    unittest.main()
