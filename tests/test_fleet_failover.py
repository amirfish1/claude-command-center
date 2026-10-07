"""Fleet limit view (M01): one banner per engine limit wall.

The overnight L06 lane gave each stopped session its own card — seven
lanes on one wall meant seven cards. free_failover_fleet() re-groups the
same watcher state so the UI can show ONE banner, and
free_failover_fleet_action() fans a single approved click out to the
per-session primitives (the per-minute armed stagger still applies).

The perf test at the bottom is the budget: the fleet endpoint must build
its answer from the two cached stores alone — no transcript scan, no
archive build, no subprocess — because the banner polls every 5s.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


_FRESH_MODULES = ("server", "morning", "morning_store")
_ORIGINAL_MODULES = {m: sys.modules[m] for m in _FRESH_MODULES if m in sys.modules}


def _fresh_server():
    for mod in _FRESH_MODULES:
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def tearDownModule():
    for mod in _FRESH_MODULES:
        sys.modules.pop(mod, None)
    sys.modules.update(_ORIGINAL_MODULES)
    original = _ORIGINAL_MODULES.get("server")
    if original is not None:
        importlib.reload(original)


class _FleetBase(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-fleet-failover-")
        self.server.USAGE_LIMIT_RESUME_FILE = (
            Path(self.tmp_dir) / "usage_limit_resumes.json"
        )
        self.server.COMMAND_CENTER_STATE_DIR = Path(self.tmp_dir) / "ccstate"
        self.server.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with self.server._usage_limit_resume_lock:
            self.server._usage_limit_resume_cache["data"] = None
        self.server._free_failover_cache_clear()
        self.server._free_ready_cache_clear()
        # Detection helpers attach row fields via _archive_all_rows_cached;
        # a cold build would scan the real corpus. Never needed here.
        self._archive_patch = mock.patch.object(
            self.server, "_archive_all_rows_cached", return_value=([], {}))
        self._archive_patch.start()

    def tearDown(self):
        self._archive_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.server._free_failover_cache_clear()
        self.server._free_ready_cache_clear()

    def _track(self, sid, *, engine="claude", resume_in=3600, now=None):
        now = time.time() if now is None else now
        self.server._save_usage_limit_resume_entry(sid, {
            "engine": engine,
            "detected_at": now - 60,
            "resume_at": now + resume_in,
            "resume_at_estimated": False,
            "source_text_snippet": "limit",
            "model": "opus",
            "cwd": self.tmp_dir,
            "display_name": "Lane " + sid[:6],
        })


class FleetGroupTests(_FleetBase):
    def test_empty_when_nothing_tracked(self):
        fleet = self.server.free_failover_fleet()
        self.assertTrue(fleet["ok"])
        self.assertEqual(fleet["groups"], [])

    def test_sessions_grouped_by_engine(self):
        self._track("aaaaaaaa-0000-0000-0000-000000000001")
        self._track("bbbbbbbb-0000-0000-0000-000000000002")
        self._track("cccccccc-0000-0000-0000-000000000003", engine="codex")
        fleet = self.server.free_failover_fleet()
        by_key = {g["key"]: g for g in fleet["groups"]}
        self.assertEqual(set(by_key), {"claude", "codex"})
        self.assertEqual(by_key["claude"]["limited_count"], 2)
        self.assertEqual(by_key["codex"]["limited_count"], 1)
        claude_sids = {
            m["session_id"] for m in by_key["claude"]["sessions"]}
        self.assertEqual(len(claude_sids), 2)

    def test_dismissed_sessions_leave_the_group(self):
        sid = "dddddddd-0000-0000-0000-000000000001"
        self._track(sid)
        self.server.free_failover_dismiss(sid)
        fleet = self.server.free_failover_fleet()
        self.assertEqual(fleet["groups"], [])

    def test_armed_sessions_stay_visible_as_armed(self):
        sid = "eeeeeeee-0000-0000-0000-000000000001"
        self._track(sid)
        self.assertTrue(self.server.free_failover_arm(sid)["ok"])
        fleet = self.server.free_failover_fleet()
        self.assertEqual(len(fleet["groups"]), 1)
        group = fleet["groups"][0]
        self.assertEqual(group["armed_count"], 1)
        self.assertEqual(group["limited_count"], 0)
        self.assertEqual(group["sessions"][0]["state"], "armed")

    def test_free_sessions_fold_into_the_group(self):
        sid = "ffffffff-0000-0000-0000-000000000001"
        self._track(sid)
        self.server._free_failover_save(sid, {
            "state": "free", "engine": "claude",
            "free_since": time.time() - 60, "free_model": "free-x",
        })
        fleet = self.server.free_failover_fleet()
        self.assertEqual(len(fleet["groups"]), 1)
        group = fleet["groups"][0]
        self.assertEqual(group["free_count"], 1)
        self.assertEqual(group["sessions"][0]["state"], "free")

    def test_claude_continue_gate_needs_free_ready(self):
        self._track("11111111-0000-0000-0000-000000000001")
        # free_ready False: no router env, no CCC_FREE_ROUTER_* fallback.
        for key in ("CCC_FREE_ROUTER_BASE_URL", "CCC_FREE_ROUTER_TOKEN",
                    "CCC_FREE_ROUTER_MODEL"):
            os.environ.pop(key, None)
        self.server._free_ready_cache_clear()
        with mock.patch.object(
                self.server, "_free_spawn_env", return_value={}):
            fleet = self.server.free_failover_fleet()
        self.assertFalse(fleet["free_ready"])
        self.assertFalse(fleet["groups"][0]["can_continue_free"])
        self.assertTrue(fleet["groups"][0]["can_auto_resume"])

    def test_devin_continue_gate_skips_router_requirement(self):
        self._track("devincli-22222222-0000-0000-0000-000000000001",
                    engine="devin")
        for key in ("CCC_FREE_ROUTER_BASE_URL", "CCC_FREE_ROUTER_TOKEN",
                    "CCC_FREE_ROUTER_MODEL"):
            os.environ.pop(key, None)
        self.server._free_ready_cache_clear()
        with mock.patch.object(
                self.server, "_free_spawn_env", return_value={}), \
             mock.patch.object(
                self.server, "_devin_free_model_candidates",
                return_value=["swe-2-medium"]):
            fleet = self.server.free_failover_fleet()
        group = fleet["groups"][0]
        self.assertEqual(group["engine"], "devin")
        self.assertTrue(group["can_continue_free"])

    def test_codex_group_offers_resume_not_free_continue(self):
        self._track("33333333-0000-0000-0000-000000000001", engine="codex")
        fleet = self.server.free_failover_fleet()
        group = fleet["groups"][0]
        self.assertFalse(group["can_continue_free"])
        self.assertTrue(group["can_auto_resume"])


class FleetActionTests(_FleetBase):
    def test_unknown_action_rejected(self):
        res = self.server.free_failover_fleet_action("explode", ["x"])
        self.assertFalse(res["ok"])
        self.assertIn("unknown", res["error"])

    def test_missing_session_ids_rejected(self):
        res = self.server.free_failover_fleet_action("arm", [])
        self.assertFalse(res["ok"])
        self.assertIn("session_ids", res["error"])

    def test_arm_fans_out_to_every_selected_session(self):
        sids = [f"sid{i:08d}-0000-0000-0000-000000000000" for i in range(3)]
        for sid in sids:
            self._track(sid)
        with mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("arm", sids)
        self.assertTrue(res["ok"])
        self.assertEqual(res["succeeded"], 3)
        self.assertEqual(res["failed"], 0)
        store = self.server._load_free_failovers()
        for sid in sids:
            self.assertTrue(store[sid]["auto_resume"])

    def test_disarm_clears_the_arming(self):
        sid = "44444444-0000-0000-0000-000000000001"
        self._track(sid)
        self.server.free_failover_arm(sid)
        with mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("disarm", [sid])
        self.assertTrue(res["ok"])
        self.assertFalse(
            self.server._load_free_failovers()[sid]["auto_resume"])

    def test_dismiss_marks_offer_dismissed(self):
        sids = ["55555555-0000-0000-0000-000000000001",
                "66666666-0000-0000-0000-000000000002"]
        for sid in sids:
            self._track(sid)
        with mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("dismiss", sids)
        self.assertTrue(res["ok"])
        store = self.server._load_free_failovers()
        for sid in sids:
            self.assertTrue(store[sid]["offer_dismissed_at"])
        # And the fleet view now has nothing to show.
        self.assertEqual(self.server.free_failover_fleet()["groups"], [])

    def test_continue_passes_always_through_and_reports_failures(self):
        sids = ["77777777-0000-0000-0000-000000000001",
                "88888888-0000-0000-0000-000000000002"]
        calls = []

        def fake_continue(sid, always=False, auto=False):
            calls.append((sid, always))
            if sid.endswith("2"):
                return {"ok": False, "error": "busy"}
            return {"ok": True, "session_id": sid}

        with mock.patch.object(
                self.server, "free_failover_continue",
                side_effect=fake_continue), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action(
                "continue", sids, always=True)
        self.assertEqual(sorted(c for c, _ in calls), sorted(sids))
        self.assertTrue(all(a for _, a in calls))
        self.assertFalse(res["ok"])
        self.assertEqual(res["succeeded"], 1)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["results"][sids[1]]["error"], "busy")

    def test_continue_results_cover_every_sid_even_in_parallel(self):
        # >1 sid takes the ThreadPoolExecutor path; a raising session must
        # still get an entry instead of dropping out of the results map.
        sids = [f"sid{i:08d}-0000-0000-0000-000000000000" for i in range(6)]

        def flaky(sid, always=False, auto=False):
            if sid == sids[3]:
                raise RuntimeError("boom")
            return {"ok": True, "session_id": sid}

        with mock.patch.object(
                self.server, "free_failover_continue",
                side_effect=flaky), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("continue", sids)
        self.assertEqual(set(res["results"]), set(sids))
        self.assertEqual(res["succeeded"], 5)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["results"][sids[3]]["error"], "boom")

    def test_switch_back_fans_out(self):
        sids = ["99999999-0000-0000-0000-000000000001",
                "99999999-0000-0000-0000-000000000002"]
        calls = []
        with mock.patch.object(
                self.server, "free_failover_switch_back",
                side_effect=lambda sid: calls.append(sid)
                or {"ok": True}), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("switch_back", sids)
        self.assertTrue(res["ok"])
        self.assertEqual(sorted(calls), sorted(sids))

    def test_duplicate_and_blank_ids_dedupe(self):
        sid = "aaaaaaaa-0000-0000-0000-000000000001"
        self._track(sid)
        with mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action(
                "dismiss", [sid, sid, "", "  "])
        self.assertTrue(res["ok"])
        self.assertEqual(res["succeeded"], 1)

    def test_string_session_ids_treated_as_one(self):
        sid = "bbbbbbbb-0000-0000-0000-000000000001"
        self._track(sid)
        with mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_fleet_action("dismiss", sid)
        self.assertTrue(res["ok"])
        self.assertEqual(res["succeeded"], 1)


class FleetPerfBudgetTests(_FleetBase):
    """The banner polls every 5s: its payload must come from the cached
    stores the usage-limit watcher already maintains. Any candidate scan,
    archive build, conversation rebuild or subprocess on this path is the
    classic 'CCC is slow' regression — guard the call counts."""

    def test_fleet_get_does_no_scan_work(self):
        sids = [f"cccccccc-0000-0000-0000-{i:012d}" for i in range(5)]
        for sid in sids:
            self._track(sid)
        hits = {"candidates": [], "archive": [], "conversations": []}
        patches = []
        for engine in ("kimi", "codex", "claude"):
            patches.append(mock.patch.object(
                self.server, f"_usage_limit_{engine}_candidates",
                side_effect=lambda *a, _e=engine, **k:
                    hits["candidates"].append(_e) or []))
        patches.append(mock.patch.object(
            self.server, "_archive_all_rows_cached",
            side_effect=lambda *a, **k:
                hits["archive"].append(1) or ([], {})))
        patches.append(mock.patch.object(
            self.server, "find_all_conversations",
            side_effect=lambda *a, **k:
                hits["conversations"].append(1) or []))
        spawned = []

        def no_spawn(*a, **k):
            spawned.append(a)
            raise AssertionError("fleet GET spawned a subprocess")

        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        import subprocess as _sp
        with mock.patch.object(_sp, "Popen", no_spawn), \
             mock.patch.object(_sp, "run", no_spawn), \
             mock.patch.object(_sp, "check_output", no_spawn):
            for _ in range(3):
                fleet = self.server.free_failover_fleet()
        self.assertEqual(len(fleet["groups"]), 1)
        self.assertEqual(fleet["groups"][0]["limited_count"], 5)
        self.assertEqual(hits["candidates"], [],
                         "fleet GET re-ran the watcher's candidate scans")
        self.assertEqual(hits["archive"], [],
                         "fleet GET rebuilt the session archive per poll")
        self.assertEqual(hits["conversations"], [],
                         "fleet GET rebuilt the conversation list per poll")
        self.assertEqual(spawned, [], "fleet GET forked a subprocess")


if __name__ == "__main__":
    unittest.main()
