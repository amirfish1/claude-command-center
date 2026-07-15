"""Lightweight smoke tests that don't depend on optional plugins.

Anything Morning-specific lives in `tests/test_morning.py` which is
gitignored alongside the Morning plugin itself; CI never sees it.
"""
import importlib
import inspect
import ast
import fcntl
import json
import os
import pathlib
import shutil
import sqlite3
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unittest
from datetime import datetime, timezone
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestServerImports(unittest.TestCase):
    def test_codex_agent_task_labels_humanize_cleartext_paths(self):
        server = importlib.import_module("server")
        self.assertEqual(
            server._codex_agent_task_label({"agent_path": "/root/ccc_588_review"}),
            "CCC-588 review",
        )
        self.assertEqual(
            server._codex_agent_task_label({"agent_path": "/root/trash_fix_review"}),
            "Trash fix review",
        )
        source = json.dumps({
            "subagent": {"thread_spawn": {"agent_path": "/root/api_audit"}},
        })
        self.assertEqual(server._codex_agent_task_label({"source": source}), "Api audit")
        self.assertEqual(server._codex_agent_task_label({"source": "vscode"}), "")

    def test_codex_display_name_prefers_task_label_over_generated_nickname(self):
        server = importlib.import_module("server")
        row = {"agent_path": "/root/ccc_588_review", "agent_nickname": "Erdos"}
        self.assertEqual(server._codex_display_name(row), "CCC-588 review")
        self.assertEqual(
            server._codex_display_name(row, first_message="Review pagination"),
            "Review pagination",
        )
        self.assertEqual(
            server._codex_display_name(row, title="Queue pagination review"),
            "Queue pagination review",
        )
        self.assertEqual(server._codex_display_name(row, override="My reviewer"), "My reviewer")
        self.assertEqual(server._codex_display_name({"agent_nickname": "Erdos"}), "Erdos")

    def test_server_imports_without_morning(self):
        """server.py must import cleanly even when the optional Morning
        plugin (morning.py, morning_store.py, etc.) isn't on disk. The
        plugin is gitignored — CI clones see no morning files at all."""
        # Ensure no stale module cached from a prior test run.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "__version__"))
        self.assertIsInstance(server.__version__, str)
        self.assertRegex(server.__version__, r"^\d+\.\d+\.\d+")

    def test_inject_routes_claude_subagent_reference_to_parent_session(self):
        """Recall can surface bare Claude ``agent-*`` child session IDs."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        parent_sid = "11111111-2222-3333-4444-555555555555"
        agent_sid = "agent-a473bdecd59d4f637"

        with tempfile.TemporaryDirectory() as tmp:
            projects_root = pathlib.Path(tmp) / "projects"
            child_path = projects_root / "-example-project" / parent_sid / "subagents" / f"{agent_sid}.jsonl"
            child_path.parent.mkdir(parents=True)
            child_path.write_text(json.dumps({"cwd": tmp}) + "\n", encoding="utf-8")
            original_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = projects_root
            server._session_cwd_cache.pop(agent_sid, None)
            try:
                with mock.patch.object(server, "session_live_status", return_value={"live": False}), \
                     mock.patch.object(server, "resume_session_headless", return_value={"ok": True}) as resume:
                    result = server._inject_text_into_session(agent_sid, "follow up")
                self.assertTrue(result["ok"])
                resume.assert_called_once_with(parent_sid, "follow up")
            finally:
                server._session_cwd_cache.pop(agent_sid, None)
                server.PROJECTS_ROOT = original_root

    def test_native_usage_snapshots_feed_weekly_usage(self):
        """Native plan-usage snapshots persist compact history and replace the
        legacy scraper cache for fresh weekly usage reads."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as tmp:
            snapshots_file = pathlib.Path(tmp) / "usage-snapshots.jsonl"
            legacy_file = pathlib.Path(tmp) / "claude-usage-pct.json"
            old_snapshot_file = server._USAGE_SNAPSHOTS_FILE
            old_legacy_file = server._WEEKLY_PCT_FILE
            try:
                server._USAGE_SNAPSHOTS_FILE = snapshots_file
                server._WEEKLY_PCT_FILE = legacy_file
                snapshot = server._native_usage_snapshot_from_plan_usage(
                    {
                        "ok": True,
                        "usage": {
                            "five_hour": {
                                "utilization": 12.5,
                                "resets_at": "2026-07-02T20:00:00Z",
                            },
                            "seven_day": {
                                "utilization": 42.0,
                                "resets_at": "2026-07-09T17:00:00Z",
                            },
                            "seven_day_sonnet": {
                                "utilization": 8.0,
                                "resets_at": "2026-07-09T17:00:00Z",
                            },
                            "limits": [{
                                "kind": "weekly_scoped",
                                "group": "weekly",
                                "percent": 44.0,
                                "resets_at": "2026-07-09T17:00:00Z",
                                "scope": {
                                    "model": {
                                        "display_name": "Fable",
                                    },
                                },
                            }],
                        },
                    },
                    now_epoch=1_783_014_000,
                )
                server._append_native_usage_snapshot(snapshot, now_epoch=1_783_014_000)

                live = server._live_weekly_usage(now_epoch=1_783_014_300)
                self.assertEqual(live["weekly_pct"], 42.0)
                self.assertEqual(live["session_pct"], 12.5)
                self.assertEqual(live["sonnet_pct"], 8.0)
                self.assertEqual(live["fable_pct"], 44.0)
                self.assertEqual(live["source"], "native")

                payload = server.usage_snapshots_payload(hours=24, now_epoch=1_783_014_300)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["snapshots"][0]["source"], "native")
                self.assertIn("/api/usage/snapshots", pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8"))
            finally:
                server._USAGE_SNAPSHOTS_FILE = old_snapshot_file
                server._WEEKLY_PCT_FILE = old_legacy_file

    def test_watchtower_worker_titles_replace_raw_codex_drain_prompt(self):
        """Live WT worker rows should show the active ticket, not the drain prompt."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        sid = "019f23e3-ba0e-7ec1-949d-d72d3f590ad2"
        rows = [{
            "session_id": sid,
            "source": "codex",
            "display_name": (
                "Drain the THROUGHPUT WatchTower queue and keep it empty. "
                "Work in the git repo."
            ),
            "name_overridden": False,
        }]
        items = [{
            "project": "THROUGHPUT",
            "ref": "THROUGHPUT-16",
            "status": "in_progress",
            "claimed_by": "throughput-eb3f49da",
            "claimed_session_id": sid,
            "title": "Publish usage state for external consumers",
            "note": "fallback",
            "claimed_at": "2026-07-02T18:04:36Z",
        }]

        with mock.patch.object(server, "_wt_read_workers", return_value=[{
            "worker_id": "throughput-eb3f49da",
            "queue": "THROUGHPUT",
            "session_id": sid,
            "alive": True,
        }]), mock.patch.object(server._q, "list_items", return_value=items):
            server._apply_watchtower_worker_display_names(rows)

        self.assertEqual(
            rows[0]["display_name"],
            "🧵 THROUGHPUT#16: Publish usage state for external consumers",
        )

    def test_watchtower_worker_titles_unclaimed_worker_gets_queue_placeholder(self):
        """A live WT worker that hasn't claimed a ticket yet (still
        bootstrapping, or between tickets) should get a clear placeholder
        name instead of falling through to whatever generic ai_title/
        first_message fallback the row would otherwise show (WT-104)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        sid = "a27da132-bd85-4584-960b-743de30f0100"
        rows = [{
            "session_id": sid,
            "source": "interactive",
            "display_name": None,
            "name_overridden": False,
        }]

        with mock.patch.object(server, "_wt_read_workers", return_value=[{
            "worker_id": "ccc-bdd1ca3a",
            "queue": "CCC",
            "session_id": sid,
            "alive": True,
        }]), mock.patch.object(server._q, "list_items", return_value=[]):
            server._apply_watchtower_worker_display_names(rows)

        self.assertEqual(rows[0]["display_name"], "CCC worker")

    def test_watchtower_worker_titles_survive_worker_record_prune(self):
        """Closed WT tickets with claimed_session_id can still title old sessions."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        sid = "019f23e3-ba0e-7ec1-949d-d72d3f590ad2"
        rows = [{
            "session_id": sid,
            "source": "codex",
            "display_name": "Drain the THROUGHPUT WatchTower queue and keep it empty.",
            "name_overridden": False,
        }]
        items = [{
            "project": "THROUGHPUT",
            "ref": "THROUGHPUT-16",
            "status": "closed",
            "claimed_by": "throughput-eb3f49da",
            "claimed_session_id": sid,
            "title": "Publish usage state for external consumers",
            "closed_at": "2026-07-02T18:14:36Z",
            "resolution": {"summary": "Published usage state API"},
        }]

        with mock.patch.object(server, "_wt_read_workers", return_value=[]), \
                mock.patch.object(server._q, "list_items", return_value=items):
            server._apply_watchtower_worker_display_names(rows)

        self.assertEqual(
            rows[0]["display_name"],
            "🧵 THROUGHPUT#16: Published usage state API",
        )

    def test_find_all_conversations_applies_watchtower_overlay(self):
        """CCC-505: the cross-repo archive path (find_all_conversations, used
        by /api/conversations/all) must run the same WT display-name overlay
        as the repo-scoped path (find_all_sessions) — otherwise a dormant
        session's clipped custom title never gets its unclipped "rest"
        attached and looks permanently truncated in the All-sessions view."""
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        fn_start = server_py.index("def find_all_conversations(")
        fn_end = server_py.index("\ndef ", fn_start + 1)
        self.assertIn(
            "_apply_watchtower_worker_display_names(out)",
            server_py[fn_start:fn_end],
            "find_all_conversations must apply the WT display-name overlay before returning",
        )

    def test_usage_pace_uses_ccc_calibration_and_week_override(self):
        """CCC owns weekly calibration/pace state without relying on legacy
        cache files, and the throughput UI exposes the projection."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as tmp:
            usage_dir = pathlib.Path(tmp) / "usage"
            old_cal = server._CCC_WEEKLY_CAL_FILE
            old_legacy_cal = server._WEEKLY_CAL_FILE
            old_override = server._WEEK_START_OVERRIDE_FILE
            old_reset_events = server._RESET_EVENTS_FILE
            old_memo = dict(server._weekly_cal_memo)
            try:
                server._CCC_WEEKLY_CAL_FILE = usage_dir / "calibration.json"
                server._WEEKLY_CAL_FILE = usage_dir / "legacy-calibration.json"
                server._WEEK_START_OVERRIDE_FILE = usage_dir / "week-start-override.json"
                server._RESET_EVENTS_FILE = usage_dir / "reset-events.jsonl"
                server._weekly_cal_memo.clear()
                server._weekly_cal_memo.update({"path": None, "mtime": None, "value": None})

                week_start = datetime(2026, 7, 1, 7, tzinfo=timezone.utc)
                self.assertTrue(server._save_weekly_calibration(week_start, 1000, 25.0))
                cal = server._weekly_pct_calibration()
                self.assertEqual(cal["week_start"], week_start.isoformat())
                self.assertEqual(cal["tokens"], 1000)
                self.assertAlmostEqual(cal["pct_per_token"], 0.025)

                reset_at = "2026-07-09T07:00:00+00:00"
                override_start = "2026-07-02T11:00:00+00:00"
                server._WEEK_START_OVERRIDE_FILE.write_text(json.dumps({
                    "week_start": override_start,
                    "applies_to_resets_week": server._usage_resets_week_key(reset_at),
                    "set_at": 1_783_000_000,
                }), encoding="utf-8")
                resolved = server._usage_week_start(reset_at)
                self.assertEqual(
                    resolved.timestamp(),
                    datetime.fromisoformat(override_start).timestamp(),
                )

                pace = server.usage_pace_payload(
                    live={"weekly_pct": 25.0, "weekly_resets_at": reset_at},
                    now_epoch=week_start.timestamp() + 24 * 3600,
                )
                self.assertTrue(pace["ok"])
                self.assertEqual(pace["weekly_pct"], 25.0)
                self.assertEqual(
                    datetime.fromisoformat(pace["week_start"]).timestamp(),
                    datetime.fromisoformat(override_start).timestamp(),
                )
                self.assertGreater(pace["total_h"], 0)
                self.assertIn("projected_pct", pace)

                server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
                throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")
                self.assertIn("/api/usage/pace", server_py)
                self.assertIn("projected", throughput_html)
            finally:
                server._CCC_WEEKLY_CAL_FILE = old_cal
                server._WEEKLY_CAL_FILE = old_legacy_cal
                server._WEEK_START_OVERRIDE_FILE = old_override
                server._RESET_EVENTS_FILE = old_reset_events
                server._weekly_cal_memo.clear()
                server._weekly_cal_memo.update(old_memo)

    def test_usage_reset_events_detect_log_and_override_week_start(self):
        """Reset detection produces durable events and teaches week-start
        resolution about unscheduled/manual weekly resets."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as tmp:
            usage_dir = pathlib.Path(tmp) / "usage"
            old_events = server._RESET_EVENTS_FILE
            old_override = server._WEEK_START_OVERRIDE_FILE
            try:
                server._RESET_EVENTS_FILE = usage_dir / "reset-events.jsonl"
                server._WEEK_START_OVERRIDE_FILE = usage_dir / "week-start-override.json"

                prev = {
                    "ts": "2026-07-02T16:55:00Z",
                    "source": "native",
                    "five_hour": {"utilization": 40.0, "resets_at": "2026-07-02T20:00:00Z"},
                    "seven_day": {"utilization": 33.0, "resets_at": "2026-07-09T07:00:00Z"},
                }
                curr = {
                    "ts": "2026-07-02T17:00:00Z",
                    "source": "native",
                    "five_hour": {"utilization": 2.0, "resets_at": "2026-07-02T20:00:00Z"},
                    "seven_day": {"utilization": 32.0, "resets_at": "2026-07-10T07:00:00Z"},
                }

                events = server._detect_usage_reset_events(prev, curr, now_epoch=1_783_011_600)
                kinds = {(e["window"], e["kind"]) for e in events}
                self.assertIn(("five_hour", "unscheduled"), kinds)
                self.assertIn(("seven_day", "scheduled"), kinds)

                for event in events:
                    self.assertTrue(server._append_usage_reset_event(event))
                manual = server.record_usage_reset_event(
                    "seven_day",
                    reset_at="2026-07-02T17:05:00Z",
                    source="user",
                )
                self.assertTrue(manual["ok"])

                payload = server.usage_reset_events_payload(days=2, now_epoch=1_783_012_000)
                self.assertTrue(payload["ok"])
                self.assertGreaterEqual(len(payload["events"]), 3)
                resolved = server._usage_week_start("2026-07-09T07:00:00Z")
                self.assertTrue(server._WEEK_START_OVERRIDE_FILE.exists())
                self.assertEqual(
                    resolved.timestamp(),
                    datetime.fromisoformat("2026-07-02T17:05:00+00:00").timestamp(),
                )

                server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
                throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")
                self.assertIn("/api/usage/reset-events", server_py)
                self.assertIn("Record limit reset", throughput_html)
                self.assertIn("reset-marker", throughput_html)
            finally:
                server._RESET_EVENTS_FILE = old_events
                server._WEEK_START_OVERRIDE_FILE = old_override

    def test_codex_usage_reads_rollout_rate_limits_and_persists_snapshot(self):
        """Codex rate-limit usage comes from recent rollout token_count events
        and is stored additively in native usage snapshots."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / ".codex" / "sessions"
            rollout_dir = root / "2026" / "07" / "02"
            rollout_dir.mkdir(parents=True)
            rollout = rollout_dir / "rollout-test.jsonl"
            rollout.write_text("\n".join([
                json.dumps({
                    "timestamp": "2026-07-02T16:00:00Z",
                    "payload": {
                        "type": "token_count",
                        "rate_limits": {
                            "plan_type": "pro",
                            "primary": {
                                "used_percent": 21,
                                "resets_at": 1_783_020_000,
                                "window_minutes": 300,
                            },
                            "secondary": {
                                "used_percent": 34,
                                "resets_at": 1_783_620_000,
                                "window_minutes": 10080,
                            },
                        },
                    },
                }),
            ]) + "\n", encoding="utf-8")

            old_root = server.CODEX_SESSIONS_ROOT
            old_snapshot_file = server._USAGE_SNAPSHOTS_FILE
            try:
                server.CODEX_SESSIONS_ROOT = root
                server._codex_usage_file_cache.clear()
                server._USAGE_SNAPSHOTS_FILE = pathlib.Path(tmp) / "usage-snapshots.jsonl"

                codex = server._read_codex_usage(now_epoch=1_783_011_600)
                self.assertEqual(codex["plan_type"], "pro")
                self.assertEqual(codex["session"]["pct"], 21.0)
                self.assertEqual(codex["weekly"]["pct"], 34.0)
                self.assertEqual(codex["weekly"]["window_minutes"], 10080)

                snap = server._native_usage_snapshot_from_plan_usage(
                    {"ok": True, "usage": {"five_hour": {}, "seven_day": {}, "seven_day_sonnet": {}}},
                    codex=codex,
                    now_epoch=1_783_011_600,
                )
                self.assertEqual(snap["codex"]["weekly"]["pct"], 34.0)
                self.assertTrue(server._append_native_usage_snapshot(snap, now_epoch=1_783_011_600))
                current = server.usage_current_payload(now_epoch=1_783_011_600)
                self.assertTrue(current["ok"])
                self.assertIn("claude", current)
                self.assertEqual(current["codex"]["weekly"]["pct"], 34.0)
                self.assertIn("last_reset_events", current)
                pace = server.codex_usage_pace_payload(codex=codex, now_epoch=1_783_011_600)
                self.assertTrue(pace["ok"])
                self.assertEqual(pace["weekly_pct"], 34.0)
                prev = {"ts": "2026-07-02T15:55:00Z", "codex": {
                    "session": {"pct": 30.0, "resets_at": "2026-07-02T20:00:00Z"},
                    "weekly": {"pct": 40.0, "resets_at": "2026-07-09T07:00:00Z"},
                }}
                curr = {"ts": "2026-07-02T16:00:00Z", "codex": {
                    "session": {"pct": 2.0, "resets_at": "2026-07-02T20:00:00Z"},
                    "weekly": {"pct": 39.0, "resets_at": "2026-07-10T07:00:00Z"},
                }}
                events = server._detect_usage_reset_events(prev, curr, now_epoch=1_783_008_000)
                self.assertIn(("codex_five_hour", "unscheduled"), {(e["window"], e["kind"]) for e in events})
                self.assertIn(("codex_weekly", "scheduled"), {(e["window"], e["kind"]) for e in events})
                self.assertIn("codex", pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8"))
                self.assertIn("'<div class=\"pu-header\">Codex</div>'", pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8"))
                self.assertIn("/api/usage/current", pathlib.Path(PROJECT_ROOT, "README.md").read_text(encoding="utf-8"))
            finally:
                server.CODEX_SESSIONS_ROOT = old_root
                server._USAGE_SNAPSHOTS_FILE = old_snapshot_file
                server._codex_usage_file_cache.clear()

    def test_codex_usage_pace_rejects_stale_persisted_snapshot(self):
        """The weekly meter must not present an old Codex rollout as current."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as tmp:
            old_snapshot_file = server._USAGE_SNAPSHOTS_FILE
            try:
                server._USAGE_SNAPSHOTS_FILE = pathlib.Path(tmp) / "usage-snapshots.jsonl"
                stale_at = 1_783_011_600
                server._USAGE_SNAPSHOTS_FILE.write_text(json.dumps({
                    "ts": server._usage_snapshot_iso(stale_at),
                    "codex": {
                        "snapshot_ts": server._usage_snapshot_iso(stale_at),
                        "weekly": {
                            "pct": 34.0,
                            "resets_at": "2026-07-09T07:00:00Z",
                            "window_minutes": 10080,
                        },
                    },
                }) + "\n", encoding="utf-8")

                pace = server.codex_usage_pace_payload(
                    now_epoch=stale_at + server._USAGE_NATIVE_FRESH_SECS + 1,
                )
                self.assertFalse(pace["ok"])
                self.assertTrue(pace["stale"])
            finally:
                server._USAGE_SNAPSHOTS_FILE = old_snapshot_file

    def test_open_session_in_claude_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-UUID session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_claude_desktop"))
        # Empty
        r = server.open_session_in_claude_desktop("")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # Not a UUID
        r = server.open_session_in_claude_desktop("not-a-uuid")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_open_session_in_codex_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-Codex session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_codex_desktop"))
        with mock.patch.object(server.subprocess, "Popen") as popen:
            r = server.open_session_in_codex_desktop("")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            with mock.patch.object(server, "_is_codex_session", return_value=False):
                r = server.open_session_in_codex_desktop("not-codex")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            popen.assert_not_called()

    def test_repo_ship_flow_is_wired(self):
        """The "Push all" ship flow exposes its server helpers and the static
        UI carries the control + endpoints. Import-level only — no git runs."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        for name in ("start_repo_ship", "repo_ship_status",
                     "continue_repo_ship", "_ship_candidate_sessions", "_run_ship_flow"):
            self.assertTrue(hasattr(server, name), name)
        # The Tier-A nudge must steer sessions toward path-scoped commits and
        # away from the index-sweeping forms that clobber sibling sessions.
        self.assertIn("--only", server.TIER_A_COMMIT_NUDGE)
        self.assertIn("git add -A", server.TIER_A_COMMIT_NUDGE)
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="ship-push-all"', app_js)
        self.assertIn("/api/repo/ship", app_js)
        self.assertIn("_startShipPushAll", app_js)
        self.assertIn("function _isShipRepoPath", app_js)
        self.assertIn("(section === 'inprogress' || section === 'archived') && _isShipRepoPath(repoPath)", app_js)
        self.assertIn("const archivedRepoPath = cards[0].folder_path || '';", app_js)
        self.assertIn("_folderGroupHeaderHtml('archived', folder, cards.length, hue, orphan, collapseKey, '', archivedRepoPath)", app_js)
        self.assertIn("if (!_isShipRepoPath(repo)) return;", app_js)
        self.assertIn("/api/repo/ship/continue", app_js)
        self.assertIn("ship-waiting-summary", app_js)
        self.assertIn("Continue now", app_js)
        self.assertIn("waiting_on", app_js)
        self.assertIn(".conv-folder-ship", app_css)
        self.assertIn(".conv-archived-section .conv-folder-ship-btn", app_css)
        self.assertIn(".ship-waiting-summary", app_css)
        # Editor/cache cruft is junk (gitignore material), not "app/deploy
        # review" — otherwise Push all parks on it every time. The cache/ prefix
        # is anchored so a legit src/cache/ deeper in the tree isn't swept.
        self.assertTrue(server._ship_is_junk("apps/x/.obsidian/app.json"))
        self.assertTrue(server._ship_is_junk("cache/projects.json"))
        self.assertFalse(server._ship_is_junk("apps/x/src/cache/util.ts"))
        # Resolving the last handoff action must carry through to a push, so the
        # integrate step is a standalone helper shared by the flow + the action
        # handler (the "I clicked skip and nothing happened" fix).
        self.assertTrue(hasattr(server, "_ship_integrate"))
        # A diverged branch is auto-reconciled in an ISOLATED throwaway worktree
        # (cherry-pick local commits onto origin → push → ff the shared clone),
        # falling back to the manual hand-off only on a real conflict. The
        # reconcile helper exists and _ship_integrate dispatches to it on the
        # diverged branch. String-level only — no real git/worktree runs here.
        self.assertTrue(hasattr(server, "_ship_reconcile_diverged"))
        self.assertIn("_ship_reconcile_diverged",
                      inspect.getsource(server._ship_integrate))
        # Loose repo-root scratch (a Puppeteer snapshot.js + its snapshot.png
        # output) is dev one-off noise, not app/deploy code — it must NOT park
        # Push all as "review". Anything under a source dir still does.
        self.assertNotEqual(server._ship_classify_remaining("snapshot.png"), "review")
        self.assertNotEqual(server._ship_classify_remaining("snapshot.js"), "review")
        self.assertEqual(server._ship_classify_remaining("snapshot.png"), "infra")
        self.assertEqual(server._ship_classify_remaining("snapshot.js"), "infra")
        self.assertEqual(server._ship_classify_remaining("apps/x/page.tsx"), "review")

    def test_repo_ship_continue_marks_waiting_job(self):
        """Push all can be told to stop waiting for commit replies."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        repo = "/tmp/ccc-ship-continue-test"
        with server._ship_jobs_lock:
            old_jobs = dict(server._ship_jobs)
            server._ship_jobs.clear()
            server._ship_jobs[repo] = {
                "phase": "waiting_commits",
                "running": True,
                "log": [],
                "waiting_on": [
                    {"sid": "abc12345", "name": "Session A", "status": "pending"},
                ],
            }
        try:
            result = server.continue_repo_ship(repo)
            self.assertTrue(result["ok"])
            self.assertTrue(result["job"]["continue_requested"])
            self.assertTrue(any(
                "Continue requested" in line.get("text", "")
                for line in result["job"]["log"]
            ))
        finally:
            with server._ship_jobs_lock:
                server._ship_jobs.clear()
                server._ship_jobs.update(old_jobs)

    def test_antigravity_turn_token_chips_show_cached_input(self):
        """Per-turn chips should expose cache reads, not only raw in/out counts."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function _formatAntigravityTokenChips(tIn, tOut, tThinking, tCached)", app_js)
        self.assertIn("if (tCached) parts.push(_formatTokensAntigravity(tCached) + ' cached')", app_js)
        self.assertIn("const chipCached = Number(ev.tokens_cached || (ev.token_usage && (ev.token_usage.cache_read_input_tokens || ev.token_usage.cached_input_tokens)) || 0);", app_js)
        self.assertIn("_formatAntigravityTokenChips(ev.tokens_in, ev.tokens_out, ev.tokens_thinking, chipCached)", app_js)
        self.assertIn("Cached input:    ' + chipCached.toLocaleString() + ' tokens", app_js)

    def test_system_health_gui_app_contract(self):
        """GUI app-server engines are visible but never reapable. Their only
        server-side control is a fixed graceful AppleScript quit command."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        rows = [
            {
                "pid": 101, "ppid": 1, "rss_mb": 200.0, "cpu": 2.5,
                "etime_min": 30.0, "tty": "??",
                "cmd": "/Applications/Cursor.app/Contents/MacOS/Cursor",
            },
            {
                "pid": 102, "ppid": 101, "rss_mb": 50.0, "cpu": 1.0,
                "etime_min": 20.0, "tty": "??",
                "cmd": "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper.app/Contents/MacOS/Cursor Helper",
            },
            {
                "pid": 201, "ppid": 1, "rss_mb": 100.0, "cpu": 3.0,
                "etime_min": 10.0, "tty": "??", "cmd": "/usr/local/bin/codex",
            },
        ]

        apps = server._sys_gui_apps(rows)

        self.assertEqual([app["id"] for app in apps], ["cursor"])
        self.assertEqual(apps[0]["name"], "Cursor.app")
        self.assertFalse(apps[0]["reapable"])
        self.assertEqual(apps[0]["control"], "quit-app")
        self.assertEqual(apps[0]["nprocs"], 2)
        self.assertEqual(apps[0]["rss_mb"], 250)
        self.assertEqual(apps[0]["pids"], [101, 102])

        fake_proc = subprocess.CompletedProcess(
            ["/usr/bin/osascript"], 0, stdout="", stderr=""
        )
        with mock.patch.object(server.platform, "system", return_value="Darwin"), \
             mock.patch.object(server.subprocess, "run", return_value=fake_proc) as run:
            res = server.system_health_quit_app("cursor")

        self.assertTrue(res["ok"])
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], [server._SYS_OSASCRIPT, "-e", 'tell application "Cursor" to quit'])
        self.assertFalse(kwargs.get("shell", False))

    def test_system_health_ui_wires_gui_app_quit(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/system-health/quit-app", app_js)
        self.assertIn("data-quit-app", app_js)
        self.assertIn("function _confirmQuitApp", app_js)
        self.assertIn("Sessions stay on disk", app_js)
        self.assertIn("Hard kill is not offered", app_js)

    def test_total_recall_search_ui_wires_sidebar_augmentation(self):
        """Conversation search calls the Recall session endpoint and labels
        Recall-backed sidebar hits without turning the field into doc search."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("const params = new URLSearchParams({ q, limit: '50', since: '90d' });", app_js)
        self.assertFalse(
            "params.set('cwd', cwd);" in app_js,
            "conversation search must not silently scope history search to the active repo",
        )
        self.assertIn("/api/search-recall-sessions", app_js)
        self.assertIn("c._historySource === 'recall'", app_js)
        self.assertIn("_historyBadgeLabel = _historyIsRecall ? 'TR'", app_js)
        self.assertIn("const recallParams = new URLSearchParams({ q, limit: '50' });", app_js)
        self.assertIn("fetch('/api/search-recall-sessions?' + recallParams.toString())", app_js)
        self.assertIn("const recallDone = recallReq.then((recallData) => {", app_js)
        self.assertIn("_mergeHistoryResults(qLower, (recallData && recallData.results) || []);", app_js)
        self.assertIn("const historyDone = historyReq.then((data) => {", app_js)
        self.assertIn("_mergeHistoryResults(qLower, (data && data.results) || []);", app_js)
        self.assertIn("return Promise.all([historyDone, recallDone, repoDone]).then(() => {", app_js)
        self.assertIn("Total Recall", app_js)
        self.assertIn("is-recall", app_js)
        self.assertIn(".conv-history-badge.is-recall", app_css)

    def test_ttl_memo_serves_stale_value_while_refreshing(self):
        """Slow liveness probes must not convoy request threads behind one lock."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        calls = 0
        refresh_entered = threading.Event()
        release_refresh = threading.Event()

        @server._ttl_memo(0.01)
        def slow_probe():
            nonlocal calls
            calls += 1
            if calls == 1:
                return "warm"
            refresh_entered.set()
            release_refresh.wait(timeout=2)
            return "fresh"

        self.assertEqual(slow_probe(), "warm")
        time.sleep(0.03)

        refresh_thread = threading.Thread(target=slow_probe)
        refresh_thread.start()
        self.assertTrue(refresh_entered.wait(timeout=1))

        result = {}
        waiter = threading.Thread(target=lambda: result.setdefault("value", slow_probe()))
        waiter.start()
        waiter.join(timeout=0.1)

        try:
            self.assertFalse(waiter.is_alive(), "stale memo caller blocked behind refresh")
            self.assertEqual(result.get("value"), "warm")
        finally:
            release_refresh.set()
            refresh_thread.join(timeout=1)
            waiter.join(timeout=1)

    def test_pending_input_saves_do_not_run_inside_queue_locks(self):
        """The queue watcher must not self-deadlock while persisting queues."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        source = inspect.getsource(server)
        tree = compile(source, server.__file__, "exec", flags=ast.PyCF_ONLY_AST)
        lock_names = {"_pending_resume_lock", "_pending_terminal_input_lock"}
        violations = []

        def calls_save(node):
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id == "_save_pending_inputs":
                        return True
            return False

        class SaveUnderQueueLockVisitor(ast.NodeVisitor):
            def visit_With(self, node):
                locked = []
                for item in node.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Name) and expr.id in lock_names:
                        locked.append(expr.id)
                if locked and calls_save(node):
                    violations.append((node.lineno, ", ".join(locked)))
                self.generic_visit(node)

        SaveUnderQueueLockVisitor().visit(tree)
        self.assertEqual([], violations)

    def test_conversation_search_remembers_last_ten_queries(self):
        """Search should offer the last ten committed conversation queries."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn('list="convSearchHistoryList"', index_html)
        self.assertIn('<datalist id="convSearchHistoryList"></datalist>', index_html)
        self.assertIn("const CONV_SEARCH_HISTORY_KEY = 'ccc-conv-search-history';", app_js)
        self.assertIn("function readConversationSearchHistory()", app_js)
        self.assertIn("function renderConversationSearchHistoryOptions()", app_js)
        self.assertIn("function rememberConversationSearchQuery(query)", app_js)
        self.assertIn("return deduped.slice(0, 10);", app_js)
        self.assertIn("option.value = item;", app_js)
        self.assertIn("renderConversationSearchHistoryOptions();", app_js)
        self.assertIn("rememberConversationSearchQuery($convSearch.value);", app_js)
        self.assertIn("$convSearch.addEventListener('change', () => rememberConversationSearchQuery($convSearch.value));", app_js)

    def test_conversation_search_shows_clear_searching_state(self):
        """Async conversation search should loudly say when it is searching."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('id="convSearchStatus"', index_html)
        self.assertIn("SEARCHING...", index_html)
        self.assertIn("const $convSearchStatus = document.getElementById('convSearchStatus');", app_js)
        self.assertIn("function setConversationSearchLoading(isLoading, query)", app_js)
        self.assertIn("$convSearchStatus.hidden = !isLoading;", app_js)
        self.assertIn("$convSearchStatus.textContent = isLoading ? 'SEARCHING...' : '';", app_js)
        self.assertIn("setConversationSearchLoading(true, q);", app_js)
        self.assertIn("setConversationSearchLoading(false, q);", app_js)
        self.assertIn(".conv-search-status", app_css)
        self.assertIn("font-weight: 800;", app_css)
        self.assertIn("@keyframes convSearchPulse", app_css)

    def test_sidebar_search_shares_new_session_row(self):
        """The conversation search box should live beside New session, not in
        a separate list-header row."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        panel_start = index_html.index('<div class="new-session-panel">')
        panel_end = index_html.index('<div id="gcManageModal"', panel_start)
        panel_html = index_html[panel_start:panel_end]
        self.assertIn('<div class="new-session-primary-row">', panel_html)
        self.assertLess(panel_html.index('id="sidebarNewBtn"'), panel_html.index('id="convSearch"'))
        self.assertIn('placeholder="Search..."', panel_html)
        list_start = index_html.index('<div class="conv-list-panel" id="convListPanel">')
        list_end = index_html.index('<div class="today-tray"', list_start)
        self.assertNotIn('id="convSearch"', index_html[list_start:list_end])
        self.assertIn('.new-session-primary-row,', app_css)
        self.assertIn('.new-session-primary-row .search-wrap {', app_css)

    def test_new_session_panel_controls_fit_single_toolbar_row(self):
        """New-session, search, group-chat, live, and manage controls stay in one row."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        panel_start = index_html.index('<div class="new-session-panel">')
        panel_end = index_html.index('<div id="gcManageModal"', panel_start)
        panel_html = index_html[panel_start:panel_end]
        panel_css = app_css[
            app_css.index(".new-session-panel {"):
            app_css.index("/* Sidebar footer", app_css.index(".new-session-panel {"))
        ]

        self.assertIn("flex-direction: row;", panel_css)
        self.assertIn("flex-wrap: nowrap;", panel_css)
        self.assertIn(".new-session-secondary-row .new-session-icon-btn {", panel_css)
        self.assertIn("width: 34px;", panel_css)
        self.assertIn("min-width: 48px;", panel_css)
        self.assertIn('id="sidebarGroupChatLiveBtn"', panel_html)
        self.assertIn('aria-label="Group chat live view"', panel_html)
        self.assertIn('<span aria-hidden="true">&#9654;</span>', panel_html)
        self.assertNotIn('>Live</button>', panel_html)

    def test_new_session_folder_shortcuts_are_labeled(self):
        """The folder dropdown and recent chips both set the same spawn CWD.

        Keep the UI copy explicit so the chips read as shortcuts for the
        folder field, not a competing project picker (CCC-190).
        """
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('placeholder="Pick or type a folder path"', index_html)
        self.assertIn('aria-label="Recent folder shortcuts"', index_html)
        self.assertIn("spawn-cwd-chip-label", app_js)
        self.assertIn("Recent folders", app_js)
        self.assertIn("id=\"nsRepoSuggestions\"", app_js)
        self.assertIn("function renderNewSessionRepoSuggestions", app_js)
        self.assertIn("for (const opt of (spawnCwdOptions || []))", app_js)
        self.assertIn("Show all folder suggestions", index_html)
        self.assertIn(".spawn-cwd-chip-label", app_css)
        self.assertIn(".ns-repo-suggestions", app_css)

    def test_new_session_stage_demotes_center_card_and_expands_composer(self):
        """New-session mode should make the bottom composer primary and keep
        center content as quiet onboarding, not a competing start form."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("class=\"ns-stage ns-stage-quiet\"", app_js)
        self.assertIn("class=\"ns-stage-title\">New session</div>", app_js)
        self.assertIn("class=\"ns-new-project-details\"", app_js)
        self.assertIn("Create a fresh folder", app_js)
        self.assertNotIn("class=\"ns-hero-title\">🚀 Start a new session</div>", app_js)
        self.assertIn("_activeInputBar.classList.toggle('is-new-session-launch', isNewSession);", app_js)
        self.assertIn(".conv-input-bar.is-new-session-launch textarea", app_css)
        self.assertIn("min-height: 96px;", app_css)

    def test_new_session_default_object_assignment_is_wired(self):
        """Every new session should be assigned to a generic durable object."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        spawn_start = app_js.index("async function spawnFromInlineInput(body) {")
        spawn_end = app_js.index("\n  // ── Appearance picker", spawn_start)
        spawn_block = app_js[spawn_start:spawn_end]
        draft_start = app_js.index("async function playFlowDraftSession(id) {")
        draft_end = app_js.index("\n  function flowDraftParentLabel", draft_start)
        draft_block = app_js[draft_start:draft_end]
        reconcile_start = app_js.index("function reconcilePendingNewSessionObjectAssignments() {")
        reconcile_end = app_js.index("\n  function _objectsGet()", reconcile_start)
        reconcile_block = app_js[reconcile_start:reconcile_end]

        self.assertIn('id="newSessionObjectContext"', index_html)
        self.assertIn("const NEW_SESSION_DEFAULT_OBJECT_ID = 'new-session-inbox';", app_js)
        self.assertIn("const NEW_SESSION_DEFAULT_OBJECT_TITLE = 'Inbox';", app_js)
        self.assertIn("function ensureNewSessionDefaultObject()", app_js)
        self.assertIn("function assignSpawnedSessionToDefaultObject(data)", app_js)
        self.assertIn("function reconcilePendingNewSessionObjectAssignments()", app_js)
        self.assertIn("const placeholder = adoptPendingSpawnPid(tempPid, data.spawn_id || data.pid, data.log, data.session_id);", spawn_block)
        self.assertIn("assignSpawnedSessionToDefaultObject(data);", spawn_block)
        self.assertNotIn("assignSpawnedSessionToDefaultObject(data);", draft_block)
        self.assertIn("_objectsApiPost('assign', { session_node_id: flowNodeKey('session', sid), object_id: objectId })", app_js)
        self.assertIn("if (!row || row.pending_spawn) continue;", reconcile_block)
        self.assertIn("if (!sid || /^spawning-/.test(String(sid))) continue;", reconcile_block)

    def test_new_session_object_picker_is_inline_and_folder_scoped(self):
        """New-session object choice should be selectable inline and remembered per folder."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const NEW_SESSION_OBJECT_BY_CWD_KEY = 'ccc-new-session-object-by-cwd';", app_js)
        self.assertIn("function newSessionObjectScopeKey()", app_js)
        self.assertIn("function getNewSessionSelectedObject()", app_js)
        self.assertIn("function setNewSessionSelectedObjectId(objectId)", app_js)
        self.assertIn("function createNewSessionObjectFromTitle(title)", app_js)
        self.assertIn("function renderNewSessionObjectMenu(query)", app_js)
        self.assertIn("function wireNewSessionObjectPicker()", app_js)
        self.assertIn("function focusNewSessionComposer()", app_js)
        self.assertIn('id="newSessionObjectPicker"', app_js)
        self.assertIn('data-role="new-session-object-create"', app_js)
        self.assertIn("focusNewSessionComposer();", app_js)
        self.assertIn("assignSpawnedSessionToDefaultObject(data);", app_js)
        self.assertIn("const obj = getNewSessionSelectedObject();", app_js)
        self.assertIn(".nso-combo", app_css)
        self.assertIn(".nso-menu", app_css)
        self.assertIn(".nso-option", app_css)
        self.assertIn("overflow: visible;", app_css)

    def test_inprogress_header_has_object_shortcut(self):
        """The row-list header should expose + object before by-objects mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-role="ip-add-object"', app_js)
        self.assertIn("+ object", app_js)
        self.assertIn("const _addObjectBtnHtml = _hasFolderChips", app_js)
        self.assertIn("localStorage.setItem('ccc-inprogress-grouping', 'objects')", app_js)
        self.assertNotIn(">+ project<", app_js)
        self.assertNotIn('data-role="ip-new-project"', app_js)
        self.assertNotIn(".conv-new-project", app_css)

    def test_inprogress_add_object_creates_inline_rename_draft(self):
        """+ object should render a draft row and edit it inline, not prompt first."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        start = app_js.index('// "+ object" (CCC-92): create an empty Flow object inline.')
        end = app_js.index('$convList.querySelectorAll(\'[data-role="elevate-to-object"]\')', start)
        handler = app_js[start:end]

        self.assertIn("function createDraftFlowCustomObject()", app_js)
        self.assertIn("const id = createDraftFlowCustomObject();", handler)
        self.assertIn("flowCustomObjects.unshift(obj);", app_js)
        self.assertIn("rankNewObjectFirst(flowNodeKey('object', id));", app_js)
        self.assertIn("localStorage.setItem('ccc-inprogress-collapsed', '0')", handler)
        self.assertIn("const title = $convList.querySelector('[data-role=\"object-title\"][data-object-id=\"' + id + '\"]');", handler)
        self.assertIn("startInlineObjectRename(title);", handler)
        self.assertNotIn("promptModal('Object name'", handler)
        self.assertNotIn("await promptModal", handler)

    def test_sidebar_tabs_start_with_active_and_all(self):
        """The high-traffic Active and All tabs should be first."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        tab_block = app_js[app_js.index("const _tabDefs = ["):app_js.index("const _tabBarHtml", app_js.index("const _tabDefs = ["))]

        active_pos = tab_block.index("['inprogress', 'Active'")
        self.assertIn("['archived', 'All'", tab_block)
        all_pos = tab_block.index("['archived', 'All'")
        issues_pos = tab_block.index("['issues', 'Issues'")
        queues_pos = tab_block.index("['queues', 'Queues'")
        self.assertLess(active_pos, all_pos)
        self.assertLess(all_pos, issues_pos)
        self.assertLess(issues_pos, queues_pos)

    def test_sidebar_all_tab_contains_active_and_archived_sessions(self):
        """The All tab should replay every session, not only archived rows."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _allTabConvs = ", app_js)
        # All shows active and archived rows in its main flow. Only rows with
        # the explicit trashed state belong in the bottom Trash bucket; pin and
        # lane placement never change lifecycle membership.
        all_start = app_js.index("const _allTabConvs = ")
        all_block = app_js[all_start:app_js.index("const _arcHasFolderChips", all_start)]
        self.assertIn("_sessionConvs.concat(_openAskConvs, _readyToMergeConvs, _mainArchivedConvs)", all_block)
        self.assertIn("const _trashConvs = _archivedConvs.filter(c => !!c.trashed);", app_js)
        self.assertIn("const _mainArchivedConvs = _archivedConvs.filter(c => !c.trashed);", app_js)
        self.assertIn("const _arcHasFolderChips = _allTabMainConvs.concat(_trashConvs).some(c => c.folder_label_chip);", app_js)
        self.assertIn("for (const c of _allTabMainConvs)", app_js)
        self.assertIn('data-role="trash-section"', app_js)
        self.assertIn('data-role="trash-toggle"', app_js)
        archived_markup = app_js[app_js.index("_archivedHtml ="):app_js.index("// Tabs", app_js.index("_archivedHtml ="))]
        self.assertNotIn('data-role="archived-toggle"', archived_markup)
        self.assertNotIn("conv-archived-arrow", archived_markup)
        self.assertNotIn("conv-archived-label", archived_markup)
        self.assertIn('data-role="archived-tools"', archived_markup)
        self.assertIn('<div class="conv-archived-list">', archived_markup)
        self.assertIn("_sidebarTab === 'archived' ? (_forceOpen(_archivedHtml, 'conv-archived-section') || _tabEmpty('sessions'))", app_js)

    def test_archived_sessions_have_visible_restore_action(self):
        """Archived session rows should have an explicit restore path back to Active."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('conv-archive-btn is-restore', app_js)
        self.assertIn('title="Move to Active" aria-label="Move to Active"', app_js)
        self.assertIn('data-archived="false"', app_js)
        self.assertIn('data-role="trash" title="Move to Trash"', app_js)
        self.assertNotIn("archivedRestoreRestHtml", app_js)
        self.assertIn("(c.archived ? ' is-archived-row' : '')", app_js)
        self.assertIn("payload.archived = archived;", app_js)
        self.assertIn("const nextArchived = btn.dataset.archived === 'true';", app_js)
        self.assertIn("archivePayloadForRow(c || { repo_path: repoPath }, sessionId, nextArchived)", app_js)
        self.assertIn("Restored to Active", app_js)
        self.assertIn(".conv-item .conv-archive-btn.is-restore", app_css)
        self.assertIn(".conv-item.is-archived-row .conv-row-end { min-width: 76px; }", app_css)

    def test_sidebar_tab_is_initialized_before_rows_use_it(self):
        """Archive rendering must not hit the sidebar-tab temporal dead zone."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        sidebar_tab = app_js.index("const _sidebarTab = (() => {")
        archive_action = app_js.index("const lifecycleContext = opts.lifecycleContext")
        self.assertLess(sidebar_tab, archive_action)

    def test_all_tab_archive_action_uses_explicit_button_intent(self):
        """Archive transitions must not be inferred from stale cache or DOM state."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const nextArchived = btn.dataset.archived === 'true';", app_js)
        self.assertIn('data-role="archive" data-archived="true"', app_js)
        self.assertIn('data-role="archive" data-archived="false"', app_js)
        self.assertNotIn("const currentlyArchived =", app_js)

    def test_sidebar_all_tab_splits_hermes_workers_from_messages(self):
        """When Hermes rows exist, All should expose Coding, Workers, and
        Messages lanes so plain WhatsApp/router conversations do not bury
        agentic Hermes work."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _isHermesWorkerRow = (c) => _isHermesAllRow(c)", app_js)
        self.assertIn("Number(c.hermes_tool_calls || 0) > 0 || !!String(c.hermes_profile || '').trim()", app_js)
        self.assertIn("const _isHermesMessageRow = (c) => _isHermesAllRow(c) && !_isHermesWorkerRow(c);", app_js)
        self.assertIn("_uxqHealthCache && _uxqHealthCache.worker_session_ids", app_js)
        self.assertIn("c._worker_id || (sid && _wtWorkerSessionIds.has(sid)) || _looksLikeWtWorkerTitle(c)", app_js)
        self.assertIn("const _allTabLaneOverride = (c) => {", app_js)
        self.assertIn("const _allTabLaneFor = (c) => _allTabLaneOverride(c) || _allTabNaturalLane(c);", app_js)
        self.assertIn("const _allTabCodingConvs = _allTabConvs.filter(c => _allTabLaneFor(c) === 'coding');", app_js)
        self.assertIn("const _allTabWorkerConvs = _allTabConvs.filter(c => _allTabLaneFor(c) === 'workers');", app_js)
        self.assertIn("const _allTabHasHermesSplit = _allTabHasLaneOverride || _allTabWorkerConvs.length > 0 || _allTabHermesMessageConvs.length > 0;", app_js)
        self.assertIn("data-role=\"all-hermes-tabs\"", app_js)
        self.assertIn("data-all-hermes-tab=\"coding\"", app_js)
        self.assertIn("data-all-hermes-tab=\"workers\"", app_js)
        self.assertIn("data-all-hermes-tab=\"messages\"", app_js)
        self.assertIn("localStorage.setItem('ccc-all-hermes-tab', value)", app_js)
        self.assertIn("/all-lane", app_js)
        self.assertIn(".conv-all-hermes-tabs", app_css)
        self.assertIn(".conv-all-hermes-tab.is-drop-target", app_css)

    def test_ready_to_merge_only_uses_known_repo_rows(self):
        """Cross-repo Ready to merge should not surface PRs from unknown repos."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function rowBelongsToKnownRepo(row)", app_js)
        self.assertIn("return known.some(root => repo === root || repo.startsWith(root + '/'));", app_js)
        self.assertIn("if (!rowBelongsToKnownRepo(r)) continue;", app_js)

    def test_cross_repo_feed_paths_keep_only_owned_github_repos(self):
        """All-repo issue/PR feeds should ignore external GitHub repos."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            mine = root / "mine"
            theirs = root / "theirs"
            local = root / "local-only"
            for repo in (mine, theirs, local):
                repo.mkdir()
                (repo / ".git").mkdir()
            owner_by_path = {
                str(mine.resolve()): "me",
                str(theirs.resolve()): "other-org",
                str(local.resolve()): "",
            }
            known = [{"path": str(mine)}, {"path": str(theirs)}, {"path": str(local)}]
            with mock.patch.object(server, "load_known_repos", return_value=known), \
                 mock.patch.object(server, "_github_owner_login_candidates", return_value={"me"}, create=True), \
                 mock.patch.object(
                     server,
                     "_github_repo_owner_for_path",
                     side_effect=lambda p: owner_by_path.get(str(pathlib.Path(p).resolve()), ""),
                     create=True,
                 ):
                self.assertEqual(server._cross_repo_feed_repo_paths(), [str(mine.resolve())])

    def test_by_objects_sort_prioritizes_objects_before_repos(self):
        """Manual rank must not let repos jump above objects in by-objects mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        object_tier_pos = app_js.index("const aObj = a[0].indexOf('object:') === 0 ? 0 : 1;")
        rank_pos = app_js.index("const aRank = Number.isFinite(_objOrder[a[0]]) ? _objOrder[a[0]] : Infinity;")

        self.assertLess(object_tier_pos, rank_pos)
        self.assertIn("Custom objects above repo-derived groups; saved rank only orders within each tier.", app_js)
        self.assertIn("function rankNewObjectFirst(nodeId)", app_js)
        self.assertIn("rankNewObjectFirst(node);", app_js)

    def test_by_objects_can_archive_custom_object_groups(self):
        """Archived custom objects leave the active by-objects conversation view."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function archiveFlowCustomObject(id)", app_js)
        self.assertIn("obj.archived = true", app_js)
        self.assertIn("isArchivedFlowObjectId(oid)", app_js)
        self.assertIn("if (isArchivedFlowObjectId(oid)) return { archived: true };", app_js)
        self.assertIn("if (grp && grp.archived) continue;", app_js)
        self.assertIn('data-role="archive-object"', app_js)
        self.assertIn(".conv-folder-object-archive-btn", app_css)

    def test_by_objects_can_elevate_session_to_own_object(self):
        """By-objects rows should have a one-click way to make a session its
        own custom object, named from the session row."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function elevateConversationToOwnObject(convId)", app_js)
        self.assertIn('data-role="elevate-to-object"', app_js)
        self.assertIn("opts.elevateToObject", app_js)
        self.assertIn("flowCustomObjects.unshift({ id, title, created_at: now, updated_at: now });", app_js)
        self.assertIn("flowNodeParents[flowNodeKey('session', sid)] = node;", app_js)
        self.assertIn("rankNewObjectFirst(node);", app_js)
        self.assertIn("Elevated to object", app_js)
        self.assertIn(".conv-elevate-object-btn", app_css)

    def test_sidebar_selected_rows_drag_together_to_objects(self):
        """Dragging a selected sidebar row should carry every selected row."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function selectedConversationDragIds(leadId)", app_js)
        self.assertIn("function toggleConversationRowSelection(item)", app_js)
        self.assertIn("toggleConversationRowSelection(item);", app_js)
        self.assertIn("dragSourceIds = ids;", app_js)
        self.assertIn("ev.dataTransfer.setData('text/plain', ids.join(','))", app_js)
        self.assertIn("function readConvIdsFromDrop(ev)", app_js)
        self.assertIn("for (const convId of convIds)", app_js)
        self.assertIn("clearSelectedConversationRows();", app_js)
        self.assertIn("function startSidebarDragAutoScroll(ev)", app_js)
        self.assertIn("$convList.addEventListener('dragover', updateSidebarDragAutoScroll);", app_js)

    def test_by_object_group_body_accepts_session_drops(self):
        """Dropping below an object header should still add sessions to it."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-object-drop-zone="', app_js)
        self.assertIn("function reparentConversationIdsToObject(target, convIds)", app_js)
        self.assertIn("$convList.querySelectorAll('[data-object-drop-zone]').forEach(zone =>", app_js)
        self.assertIn("if (ev.target.closest('[data-object-drop]')) return;", app_js)
        self.assertIn("if (reparentConversationIdsToObject(target, convIds))", app_js)
        self.assertIn(".conv-folder-group.is-drop-target", app_css)

    def test_by_object_session_rows_can_be_reordered_within_group(self):
        """Dropping a session row on another row inside an object should rank
        that object's sessions on screen without using global archive order."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const OBJECT_SESSION_ORDER_KEY = 'ccc-object-session-order';", app_js)
        self.assertIn("function sortedObjectCardsForRender(parentNode, cards)", app_js)
        self.assertIn("function reorderObjectSessionRows(targetRow, convIds, placement)", app_js)
        self.assertIn("const orderedCards = sortedObjectCardsForRender(nodeId, cards);", app_js)
        self.assertIn("_renderObjGroup(nodeId, group.title, orderedCards, depth, ordinal)", app_js)
        self.assertIn("const objectDropGroup = el.closest('[data-object-drop-zone]');", app_js)
        self.assertIn("if (reorderObjectSessionRows(el, readConvIdsFromDrop(ev), before ? 'before' : 'after'))", app_js)

    def test_nested_object_group_suppresses_empty_hint(self):
        """A parent object with child objects is not empty even without sessions."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const hasChildObjects = !!((_childrenOf.get(nodeId) || []).length);", app_js)
        self.assertIn("!hasChildObjects", app_js)
        self.assertIn("Empty — drag a session here, or use +.", app_js)

    def test_assign_object_picker_renders_object_hierarchy(self):
        """The object assignment dialog should show nested objects as a tree."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function flowAssignableObjectTree()", app_js)
        self.assertIn("depth: depth,", app_js)
        self.assertIn("childrenByParent.get(parentNode)", app_js)
        self.assertIn("visit(root, 0);", app_js)
        self.assertIn("o.matchesSearch || o.hasMatchingDescendant", app_js)
        self.assertIn("style=\"--object-depth:' + Number(o.depth || 0) + ';\"", app_js)
        self.assertIn("flow-object-assign-title", app_js)
        self.assertIn("flow-object-assign-path", app_js)
        self.assertIn(".flow-object-assign-row {", app_css)
        self.assertIn("padding-left: calc(12px + (var(--object-depth, 0) * 18px));", app_css)
        self.assertIn(".flow-object-assign-title::before", app_css)

    def test_search_hides_empty_custom_object_groups(self):
        """Searching conversations should not show every empty object group."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _ipSearchActive = !!(document.getElementById('convSearch')?.value || '').trim();", app_js)
        self.assertIn("const _objectHasVisibleDrafts = (node) =>", app_js)
        self.assertIn("if (_ipSearchActive && !_byObject.has(node) && !_objectHasVisibleDrafts(node)) continue;", app_js)

    def test_object_group_rows_align_under_object_titles(self):
        """Rows inside object groups should start under the object title column."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-folder-group[data-object-drop-zone] > .conv-item.is-grouped-row {\n"
                      "    padding-left: 29px;\n"
                      "  }", app_css)

    def test_custom_object_rename_uses_pencil_save_cancel(self):
        """Custom object header names should rename through explicit controls."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-role="object-title"', app_js)
        self.assertIn('data-role="object-rename"', app_js)
        self.assertIn('class="conv-folder-object-rename-icon"', app_js)
        self.assertNotIn('aria-label="Rename object">&#9998;', app_js)
        self.assertIn('data-role="object-playpause"', app_js)
        self.assertIn("function setFlowObjectStatus(id, status)", app_js)
        self.assertIn("function startInlineObjectRename(chip)", app_js)
        self.assertIn("saveBtn.setAttribute('data-role', 'object-rename-save')", app_js)
        self.assertIn("cancelBtn.setAttribute('data-role', 'object-rename-cancel')", app_js)
        self.assertIn("persistFlowCustomObjects();", app_js)
        self.assertIn("obj.title = newTitle;", app_js)
        self.assertIn("const $objectRename = $convList.querySelectorAll('[data-role=\"object-rename\"]');", app_js)
        self.assertIn("$convList.querySelectorAll('[data-role=\"object-playpause\"]').forEach(btn =>", app_js)
        self.assertIn("startInlineObjectRename(title);", app_js)
        self.assertNotIn("startInlineObjectRename(objectTitle);", app_js)
        self.assertNotIn("input.addEventListener('blur', () => finish(true));", app_js)
        self.assertIn("&#128465;", app_js)
        self.assertIn(".conv-folder-object-title-input", app_css)
        self.assertIn(".conv-folder-object-rename-btn", app_css)
        self.assertIn(".conv-folder-object-rename-icon", app_css)
        self.assertIn(".conv-folder-object-rename-btn:hover,\n  .conv-folder-object-rename-btn:focus-visible {\n    color: var(--text);\n    background: transparent;", app_css)
        self.assertIn(".conv-folder-object-playpause-btn", app_css)
        self.assertIn(".conv-folder-object-title-actions", app_css)
        self.assertIn(".conv-folder-group-header[data-object-drop] .conv-folder-group-arrow", app_css)

    def test_object_rename_input_keeps_space_key(self):
        """Space in object rename input should not bubble to the header toggle."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        fn_start = app_js.index("function startInlineObjectRename")
        fn_body = app_js[fn_start:app_js.index("function rankNewObjectFirst", fn_start)]
        start = app_js.index("input.addEventListener('keydown', ev => {", fn_start)
        block = app_js[start:app_js.index("});", start) + 3]
        self.assertIn("ev.stopPropagation();", block)
        self.assertIn("if (ev.key === 'Enter')", block)
        self.assertIn("else if (ev.key === 'Escape')", block)
        self.assertIn("restoreObjectTitleChip(finalTitle);", fn_body)
        self.assertNotIn("renderArchiveList(document.getElementById('convSearch')?.value || '');", fn_body)

    def test_coo_tracking_checkboxes_are_coo_mode_only(self):
        """The per-row COO tracking checkbox should stay hidden unless the
        user has opened/enabled COO mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        coo_button_js = pathlib.Path(PROJECT_ROOT, "static", "coo-button.js").read_text(encoding="utf-8")

        self.assertIn("const COO_MODE_KEY = 'ccc-coo-mode';", app_js)
        self.assertIn("function isCooModeOn()", app_js)
        self.assertIn("const cooTrackHtml = isCooModeOn()", app_js)
        self.assertIn('localStorage.setItem("ccc-coo-mode", "1")', coo_button_js)
        self.assertIn('window.dispatchEvent(new Event("ccc-coo-mode-changed"))', coo_button_js)
        self.assertIn("window.addEventListener('ccc-coo-mode-changed'", app_js)

    def test_by_object_headers_have_named_collapse_control(self):
        """Object groups should expose a visible, accessible collapse/expand
        control on each object header."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-role="folder-group-collapse"', app_js)
        self.assertIn('aria-label="Collapse or expand group"', app_js)
        self.assertIn('title="Collapse or expand group"', app_js)
        self.assertIn("ev.target.closest('[data-role=\"folder-group-collapse\"]')", app_js)
        self.assertIn(".conv-folder-group-arrow {", app_css)

    def test_object_title_click_expands_collapsed_group(self):
        """Clicking a collapsed object title should expand the object group."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const hitObjectTitle = ev.target.closest('[data-role=\"object-title\"]');", app_js)
        self.assertIn("if (opensInspector && hitObjectTitle && group && group.classList.contains('collapsed'))", app_js)
        self.assertIn("toggleFolderGroup(ev);\n          return;", app_js)

    def test_top_level_object_headers_are_uppercase(self):
        """Top-level custom object titles should render in uppercase."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-object-depth="', app_js)
        self.assertIn('.conv-folder-group[data-object-drop-zone^="object:"][data-object-depth="0"] > .conv-folder-group-header .conv-folder-object-title-text {\n'
                      '    text-transform: uppercase;\n'
                      '  }', app_css)

    def test_object_header_chevrons_are_larger(self):
        """Object collapse chevrons should be easier to see and tap."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("height: 24px;", app_css)
        self.assertIn("font-size: 18px;", app_css)
        self.assertIn(".conv-folder-group-header[data-object-drop] .conv-folder-group-arrow {\n"
                      "    grid-column: 6;\n"
                      "    justify-self: end;\n"
                      "    width: auto;\n"
                      "    min-width: 26px;\n"
                      "  }", app_css)

    def test_by_object_group_titles_use_larger_readable_type(self):
        """Object group headers should not use the tiny project-chip type."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-folder-group-header[data-object-drop] .conv-folder-group-chip {\n"
                      "    grid-column: 2;\n"
                      "    max-width: 220px;\n"
                      "    overflow: hidden;\n"
                      "    text-overflow: ellipsis;\n"
                      "    font-size: 15px;\n"
                      "    line-height: 1.28;", app_css)
        self.assertIn(".conv-folder-object-title-input {\n"
                      "    width: min(220px, 100%);\n"
                      "    min-width: 80px;\n"
                      "    padding: 1px 7px;\n"
                      "    border-radius: 3px;\n"
                      "    border: 1px solid var(--accent, #58a6ff);\n"
                      "    background: var(--bg);\n"
                      "    color: var(--text);\n"
                      "    font: inherit;\n"
                      "    font-size: 15px;", app_css)

    def test_by_object_group_titles_use_available_space(self):
        """Object headers should not ellipsize while header space is available."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-folder-group-header[data-object-drop]:has(.conv-object-meta) .conv-folder-group-chip {\n"
                      "    max-width: none;\n"
                      "  }", app_css)

    def test_by_object_nested_titles_use_sibling_ordinals(self):
        """Nested object numbering should reflect sibling order under a parent."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function numberedObjectTitleHtml(title, ordinal = 0)", app_js)
        self.assertIn("const prefix = ordinal > 0", app_js)
        self.assertIn("numberedObjectTitleHtml(folder, objectOrdinal)", app_js)
        self.assertIn("_renderObjGroup(nodeId, group.title, orderedCards, depth, ordinal)", app_js)
        self.assertIn("_emitObjTree(k, depth + 1, i + 1, opts)", app_js)
        self.assertIn("_objRoots.map((n, i) => _emitObjTree(n, 0, i + 1)).join('')", app_js)
        self.assertNotIn("raw.split('/').map", app_js)
        self.assertIn(".conv-folder-object-title-text", app_css)
        self.assertIn(".conv-folder-object-number", app_css)
        self.assertNotIn(".conv-project-tree .conv-folder-object-number { display: none; }", app_css)

    def test_chip_color_toggle_has_hierarchy_level_mode(self):
        """Chip colors should cycle through per-item, per-level, and muted modes."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("else if (m === 'level') document.body.classList.add('chips-level');", index_html)
        self.assertIn("document.body.classList.contains('chips-level') ? 'level' : 'color'", app_js)
        self.assertIn("const next = cur === 'color' ? 'level' : (cur === 'level' ? 'muted' : 'color');", app_js)
        self.assertIn("localStorage.setItem('ccc-chips-mode', next)", app_js)
        self.assertIn("body.chips-level .conv-folder-group[data-object-drop-zone] > .conv-folder-group-header", app_css)
        self.assertIn("const levelHue = [190, 48, 145, 28, 210][depth % 5];", app_js)
        self.assertIn("--level-chip-hue:", app_js)
        self.assertIn("--chip-hue: var(--level-chip-hue, 190) !important;", app_css)
        self.assertNotIn("--chip-hue: calc(205 + (var(--obj-depth, 0) * 46))", app_css)

    def test_by_objects_header_has_expand_collapse_all(self):
        """By-objects expand/collapse all should hide sessions but keep sub-objects visible."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _ipObjectsExpandAll = _shouldGroupByObjects", app_js)
        self.assertIn('data-role="objects-expand-all"', app_js)
        self.assertIn('data-objects-collapse="0"', app_js)
        self.assertIn('data-objects-collapse="1"', app_js)
        self.assertIn("function _objectSessionsStorageKey(key)", app_js)
        self.assertIn("function _areObjectSessionsCollapsed(key)", app_js)
        self.assertIn("function setInProgressObjectGroupsCollapsed(collapsed)", app_js)
        self.assertIn("$convList.querySelectorAll('[data-role=\"inprogress-section\"] .conv-folder-group').forEach(group =>", app_js)
        self.assertIn("localStorage.setItem(key, '0')", app_js)
        self.assertIn("localStorage.setItem(_objectSessionsStorageKey(nodeId || key), collapsed ? '1' : '0')", app_js)
        self.assertIn("group.classList.toggle('sessions-collapsed', !!collapsed)", app_js)
        self.assertIn("if (ev.target.closest('[data-role=\"objects-expand-all\"]')) return;", app_js)
        self.assertIn("$objectsExpandAll.addEventListener('click'", app_js)
        self.assertIn(".conv-objects-expand-all", app_css)
        self.assertIn("#convList .conv-folder-group.sessions-collapsed > .conv-item,", app_css)
        self.assertNotIn("#convList .conv-folder-group.sessions-collapsed > .conv-folder-group", app_css)

    def test_object_triangle_collapse_rerenders_subtree(self):
        """Object header chevrons should collapse nested object subtrees immediately."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const objNodeForToggle = hdr.getAttribute('data-object-drop') || '';", app_js)
        self.assertIn("function setObjectSubtreeDomHidden(group, hidden)", app_js)
        self.assertIn("setObjectSubtreeDomHidden(group, wasCollapsed);", app_js)
        self.assertIn("setObjectSubtreeDomHidden(group, false);", app_js)
        self.assertIn(".conv-folder-group.is-subtree-hidden { display: none; }", app_css)
        self.assertIn("if (objNodeForToggle) {", app_js)
        self.assertIn("localStorage.removeItem(_objectSessionsStorageKey(objNodeForToggle));", app_js)
        self.assertIn("renderArchiveList(document.getElementById('convSearch')?.value || '');", app_js)
        self.assertIn("if (_isFolderGroupCollapsed('inprogress', nodeId)) return html;", app_js)

    def test_window_resize_restores_conversation_reading_position(self):
        """A window resize reflows the transcript (word-wrap changes with pane
        width), which used to leave scrollTop untouched and visually jump the
        reader. It should restore against the last-known top-visible message
        instead (CCC-440)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("view._lastTopAnchor = _topVisibleAnchor(view);", app_js)
        self.assertIn("if (view._pinnedToBottom) scrollConversationToEnd(view);", app_js)
        self.assertIn("else if (view._lastTopAnchor) _restoreAnchor(view, view._lastTopAnchor);", app_js)

    def test_object_task_draft_survives_sidebar_refresh(self):
        """Typing in an object task should persist before periodic list refresh
        replaces the row DOM."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("let flowDraftFocusSelection = null;", app_js)
        self.assertIn("const _focusDraftIdBefore = (_activeDraftInputBefore && _activeDraftInputBefore.classList.contains('conv-draft-input'))", app_js)
        self.assertIn("saveFlowDraftInput(_focusDraftIdBefore, _activeDraftInputBefore.value);", app_js)
        self.assertIn("flowDraftFocusId = _focusDraftIdBefore;", app_js)
        self.assertIn("inp.addEventListener('input'", app_js)
        self.assertIn("flowDraftFocusId = id;", app_js)
        self.assertIn("_df.setSelectionRange(flowDraftFocusSelection.start, flowDraftFocusSelection.end);", app_js)

    def test_sidebar_has_no_manual_done_section(self):
        """The Active sidebar tab should not render the old manual Done bucket."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertNotIn("conv-done-section", app_js)
        self.assertNotIn("data-role=\"done-section\"", app_js)
        self.assertNotIn("ccc-done-sessions", app_js)
        self.assertNotIn("ccc-done-collapsed", app_js)
        self.assertNotIn(".conv-done-section", app_css)

    def test_kanban_tool_groups_stay_expanded(self):
        """Kanban comments/blocks are conversation context, not routine tools."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function toolCallCarriesConversationContext(toolCall)", app_js)
        self.assertIn("key === 'ask_user_question' || key.startsWith('kanban_')", app_js)
        self.assertIn("if (!on && toolGroupCarriesConversationContext(g)) return;", app_js)
        self.assertIn("if (toolGroupCarriesConversationContext(_currentToolGroup))", app_js)

    def test_active_sidebar_inprogress_section_is_headerless(self):
        """The Active tab should not repeat an In Progress header/count."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _ipToolbarHtml = _ipTools", app_js)
        self.assertIn('data-role="inprogress-toolbar"', app_js)
        self.assertIn("'<div class=\"conv-inprogress-list\">' + _ipToolbarHtml + _activeRowsHtml + '</div>'", app_js)
        self.assertNotIn('<span class="conv-inprogress-label">In progress</span>', app_js)
        self.assertNotIn('<span class="conv-inprogress-count"', app_js)
        self.assertIn("#convList .conv-inprogress-section { margin-top: 0; border-top: 0; padding-top: 0; }", app_css)
        self.assertIn("#convList .conv-inprogress-toolbar {", app_css)
        self.assertIn("#convList .conv-inprogress-list > .conv-item {\n"
                      "    padding-left: 8px;", app_css)

    def test_object_header_plus_creates_child_object(self):
        """By-object + should create a child object, not a draft task."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const objectAddObject = (section === 'inprogress' && archiveObjectId)", app_js)
        self.assertIn('class="conv-folder-object-add-object-btn"', app_js)
        self.assertIn("data-parent-node=\"' + escapeHtml(flowNodeKey('object', archiveObjectId)) + '\"", app_js)
        self.assertIn("objectRename + objectAddObject + objectPlayPause + objectArchive", app_js)
        self.assertIn("function createChildFlowCustomObject(parentNodeId)", app_js)
        self.assertIn("flowNodeParents[flowNodeKey('object', id)] = parentNodeId;", app_js)
        self.assertIn("startInlineObjectRename(title);", app_js)
        self.assertIn('data-flow-action="add-child-object"', app_js)
        self.assertNotIn('data-flow-action="add-draft-session"', app_js[app_js.index("const objectAddObject ="):app_js.index("let objectPlayPause =", app_js.index("const objectAddObject ="))])
        self.assertNotIn('class="conv-folder-object-add-task-btn"', app_js)
        self.assertNotIn('class="conv-object-add-task"', app_js)
        self.assertNotIn(">+ task</button>", app_js)
        self.assertIn("flowDraftSessions.push(draft);", app_js)
        self.assertNotIn("flowDraftSessions.unshift(draft);", app_js)
        self.assertIn(".conv-folder-object-add-object-btn", app_css)
        self.assertNotIn(".conv-folder-object-add-task-btn", app_css)
        self.assertNotIn(".conv-object-add-task", app_css)

    def test_by_objects_draft_rows_are_draggable_between_objects(self):
        """Draft task rows should move through the same object drop path."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("let _draggedFlowNode = '';", app_js)
        self.assertIn("function flowNodeDragPayload(nodeId)", app_js)
        self.assertIn("function readFlowNodeFromObjectDrop(ev)", app_js)
        self.assertIn("function setDraftNodeParent(draggedNode, target)", app_js)
        self.assertIn("' draggable=\"' + rowDraggableAttr() + '\" data-flow-draft-node=\"' + escapeAttr(flowNodeKey('draft-session', d.id)) + '\"'", app_js)
        self.assertIn("dataTransfer.setData('text/plain', flowNodeDragPayload(nodeId));", app_js)
        self.assertIn("$convList.querySelectorAll('[data-flow-draft-node]').forEach(row =>", app_js)
        self.assertIn("flowNodeParents[draggedNode] = target;", app_js)
        self.assertIn("if (draggedNode.indexOf('draft-session:') === 0)", app_js)
        self.assertIn(".conv-draft-row.dragging", app_js)
        self.assertIn(".conv-project-tree .conv-draft-row[draggable=\"true\"]", app_css)

    def test_by_objects_repo_groups_can_nest_under_objects(self):
        """Dragging a repo group onto an object should make it a child group."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("dragged.indexOf('object:') !== 0 && dragged.indexOf('repo:') !== 0", app_js)
        self.assertIn("const _treeParentOf = (nodeId) => {", app_js)
        self.assertIn("const p = _treeParentOf(nodeId);", app_js)
        self.assertIn("object groups and repo groups can nest", app_js)
        self.assertIn("return (p && p.indexOf('object:') === 0 && _byObject.has(p)) ? p : null;", app_js)

    def test_by_objects_project_tree_has_own_scroll_region(self):
        """The project tree should scroll separately from current sessions."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _projectTreeScrollHtml = (_projectTreeHtml || _looseHtml)", app_js)
        self.assertIn('data-role="project-tree-scroll"', app_js)
        self.assertIn("const _projectTreeHeaderHtml = _projectTreeHtml", app_js)
        self.assertIn('data-role="project-tree-header"', app_js)
        self.assertIn("const _currentSessionsScrollHtml = (_currentSessionsBodyHtml)", app_js)
        self.assertIn('data-role="current-sessions-scroll"', app_js)
        self.assertIn("_activeRowsHtml = _currentSessionsHeaderHtml + _currentSessionsScrollHtml + _objectsSplitHandleHtml + _projectTreeHeaderHtml + _projectTreeScrollHtml + _evergreenSplitHandleHtml + _evergreenAgentsHeaderHtml + _evergreenAgentsScrollHtml;", app_js)
        scroll_html_start = app_js.index("const _projectTreeScrollHtml = (_projectTreeHtml || _looseHtml)")
        scroll_html_end = app_js.index("const _objectsSplitHandleHtml", scroll_html_start)
        self.assertNotIn("Project tree</div>", app_js[scroll_html_start:scroll_html_end])
        self.assertIn("const _objectsSplitActive = _sidebarTab === 'inprogress' && _shouldGroupByObjects;", app_js)
        self.assertIn("$convList.classList.toggle('objects-scroll-split', _objectsSplitActive);", app_js)
        self.assertIn("#convList.objects-scroll-split {", app_css)
        outer_css = app_css[app_css.index("#convList.objects-scroll-split {"):app_css.index("#convList.objects-scroll-split .conv-inprogress-section", app_css.index("#convList.objects-scroll-split {"))]
        self.assertIn("overflow-y: hidden !important;", outer_css)
        self.assertIn(".conv-current-sessions-scroll {", app_css)
        current_css = app_css[app_css.index(".conv-current-sessions-scroll {"):app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))]
        self.assertIn("overflow-y: auto;", current_css)
        self.assertIn("max-height: var(--current-sessions-panel-h, clamp(", current_css)
        self.assertIn("overscroll-behavior: contain;", current_css)
        self.assertIn(".conv-project-tree-scroll {", app_css)
        scroll_css = app_css[app_css.index(".conv-project-tree-scroll {"):app_css.index(".conv-project-tree {", app_css.index(".conv-project-tree-scroll {"))]
        self.assertIn("overflow-y: auto;", scroll_css)
        self.assertIn("max-height: clamp(", scroll_css)
        self.assertIn("overscroll-behavior: contain;", scroll_css)
        self.assertIn("#convList.objects-scroll-split .conv-section-fill {", app_css)
        self.assertIn(".conv-project-tree-header {", app_css)
        header_css = app_css[app_css.index(".conv-project-tree-header {"):app_css.index(".conv-project-tree-scroll {", app_css.index(".conv-project-tree-header {"))]
        self.assertIn("flex: 0 0 auto;", header_css)
        self.assertIn("margin: 6px 2px 0 0;", header_css)
        self.assertIn("border-radius: 8px 8px 0 0;", header_css)
        self.assertIn("background:", header_css)
        self.assertIn("cursor: pointer;", header_css)

    def test_by_objects_current_sessions_splitter_is_resizable(self):
        """A horizontal splitter should resize Current sessions vs Project tree."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _CURRENT_SESSIONS_PANEL_H_KEY = 'ccc-current-sessions-panel-h';", app_js)
        self.assertIn("function applyCurrentSessionsPanelHeight()", app_js)
        self.assertIn("const _objectsSplitHandleHtml = (_currentSessionsScrollHtml && _projectTreeScrollHtml)", app_js)
        self.assertIn('data-role="objects-splitter"', app_js)
        self.assertIn("_activeRowsHtml = _currentSessionsHeaderHtml + _currentSessionsScrollHtml + _objectsSplitHandleHtml + _projectTreeHeaderHtml + _projectTreeScrollHtml + _evergreenSplitHandleHtml + _evergreenAgentsHeaderHtml + _evergreenAgentsScrollHtml;", app_js)
        self.assertIn("function beginObjectsSplitterResize(ev)", app_js)
        self.assertIn("ev.target.closest('[data-role=\"objects-splitter\"]')", app_js)
        self.assertIn("list.style.setProperty('--current-sessions-panel-h', nextHeight + 'px');", app_js)
        self.assertIn("localStorage.setItem(_CURRENT_SESSIONS_PANEL_H_KEY, String(nextHeight));", app_js)
        self.assertIn("$convList.addEventListener('pointerdown', beginObjectsSplitterResize);", app_js)

        current_css = app_css[app_css.index(".conv-current-sessions-scroll {"):app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))]
        self.assertIn("flex: 0 0 var(--current-sessions-panel-h, clamp(", current_css)
        self.assertIn("max-height: var(--current-sessions-panel-h, clamp(", current_css)
        self.assertIn(".conv-objects-splitter {", app_css)
        splitter_css = app_css[app_css.index(".conv-objects-splitter {"):app_css.index(".conv-objects-splitter::before", app_css.index(".conv-objects-splitter {"))]
        self.assertIn("cursor: row-resize;", splitter_css)
        self.assertIn(".conv-objects-splitter.is-dragging", app_css)
        self.assertIn("body.objects-splitter-resizing", app_css)

    def test_evergreen_agents_object_has_own_bottom_section(self):
        """The Triggered Workers section is its own bottom region in By objects,
        driven PURELY by WatchTower server data (drain-on queues + live workers)
        with NO Flow-object / localStorage dependency."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        # The legacy Evergreen flow-object detection is retained ONLY to keep a
        # legacy "Evergreen Agents" object out of the Project tree — it no longer
        # feeds the bottom section.
        self.assertIn("const _isEvergreenAgentsObjectTitle = (title) =>", app_js)
        self.assertIn("replace(/[^a-z0-9]+/g, '').startsWith('evergreen')", app_js)
        self.assertIn("const _evergreenObjectNodes = new Set();", app_js)
        self.assertIn("if (_evergreenObjectNodes.has(nodeId)) continue;", app_js)
        # The section is built from the cached server endpoints only: drain-on
        # queues from /api/ux-fixes/health and live workers from /api/wt/workers.
        self.assertIn("_uxqHealthCache.queues.filter(q => q && (q.auto_drain === true || Number(q.workers) > 0))", app_js)
        self.assertIn("const _twWorkers = (_wtWorkersCache && Array.isArray(_wtWorkersCache.workers))", app_js)
        self.assertIn("const _twWorkersByQueue = new Map();", app_js)
        # Workers resolve to the EXISTING session row via their cloud session_id.
        self.assertIn("const card = sid ? _twCardById.get(sid) : null;", app_js)
        self.assertIn("return _renderRow(enriched, { lifecycleContext: 'active', suppressFolderChip: !_ipRowChipsOn, elevateToObject: true, evergreenAgent: true, evergreenSingleLine: true });", app_js)
        self.assertIn("return _twFallbackRow(w);", app_js)
        self.assertIn("_twQueueHeaderHtml(q, workers.length)", app_js)
        self.assertIn("const _evergreenAgentsHtml = _evergreenAgentsBody", app_js)
        self.assertIn("+ _evergreenAgentsBody + '</div>'", app_js)
        self.assertIn('class="conv-evergreen-queue-header', app_js)
        # An unattended auto-drain queue is waiting for a worker, rather than
        # genuinely stuck. The sidebar must match the Queue health strip's
        # less alarming terminology for this zero-worker state.
        self.assertIn("const waiting = stuck && workers === 0;", app_js)
        self.assertIn("stateLabel = 'Waiting';", app_js)
        self.assertIn("Waiting means this auto-drain queue has claimable open tickets, but no WatchTower worker is currently assigned.", app_js)
        # The section must NOT read Flow-object state for its data.
        self.assertNotIn("const _renderEvergreenQueueGroup", app_js)
        self.assertNotIn("_evergreenRoots", app_js)
        self.assertNotIn("_emitObjTree(n, 0, i + 1, { includeEvergreen: true })", app_js)
        # Server-driven warm of BOTH caches with a single sig-gated re-render.
        self.assertIn("async function _fetchWtWorkers()", app_js)
        self.assertIn("await Promise.all([_fetchUxqHealth(), _fetchWtWorkers()]);", app_js)
        # Section header renamed to Triggered Workers.
        self.assertIn("+ 'Triggered Workers'", app_js)
        self.assertIn('data-role="evergreen-agents-header"', app_js)
        self.assertIn('data-role="evergreen-agents-scroll"', app_js)
        self.assertIn(
            "_activeRowsHtml = _currentSessionsHeaderHtml + _currentSessionsScrollHtml + _objectsSplitHandleHtml + _projectTreeHeaderHtml + _projectTreeScrollHtml + _evergreenSplitHandleHtml + _evergreenAgentsHeaderHtml + _evergreenAgentsScrollHtml;",
            app_js,
        )

        self.assertIn(".conv-evergreen-agents-header {", app_css)
        self.assertIn(".conv-evergreen-agents-scroll {", app_css)
        evergreen_css = app_css[
            app_css.index(".conv-evergreen-agents-scroll {"):
            app_css.index(".conv-project-tree {", app_css.index(".conv-evergreen-agents-scroll {"))
        ]
        self.assertIn("flex: 0 0 auto;", evergreen_css)
        self.assertIn("overflow-y: auto;", evergreen_css)

    def test_evergreen_agent_rows_keep_status_context_visible(self):
        """Evergreen agents need their queue count, goal, WIP/idle state, and
        last-run timestamp visible at rest."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _isEvergreenAgentGroup = _evergreenObjectNodes.has(nodeId);", app_js)
        self.assertIn("evergreenAgent: _isEvergreenAgentGroup", app_js)
        self.assertIn("const evergreenRowClass = opts.evergreenAgent ? ' is-evergreen-agent-row' : '';", app_js)
        self.assertIn("let evergreenStateHtml = '';", app_js)
        self.assertIn("const _evergreenStateLabel = _isAgentRunning ? 'WIP'", app_js)
        self.assertIn("const evergreenMetaRowHtml = (opts.evergreenAgent && !_egSingleLine)", app_js)
        self.assertIn("+ evergreenMetaRowHtml", app_js)
        # Single-line variant (TRIGGERED WORKERS rows): same badges, inline in the
        # main row instead of a second meta line. Scoped flag keeps project-tree
        # evergreen rows two-line.
        self.assertIn("const _egSingleLine = !!(opts.evergreenAgent && opts.evergreenSingleLine);", app_js)
        self.assertIn("const evergreenInlineBadgesHtml = _egSingleLine", app_js)
        self.assertIn("+ evergreenInlineBadgesHtml", app_js)
        self.assertIn(".conv-evergreen-agents-tree .conv-item.is-evergreen-single-line .conv-main-row > .conv-evergreen-inline-badges {", app_css)
        self.assertIn("const _hasMetaContent = !opts.evergreenAgent &&", app_js)
        self.assertIn("const hoverMetaRowHtml = _hasMetaContent", app_js)
        self.assertNotIn("+ evergreenGoalHtml\n            + uxFixesQueueProgressHtml\n            + evergreenStateHtml", app_js)
        self.assertIn(".conv-evergreen-agents-tree .conv-item.is-evergreen-agent-row .conv-title-row {", app_css)
        self.assertIn(".conv-evergreen-meta-row {", app_css)
        self.assertIn(".conv-evergreen-meta-row > .conv-ux-fix-progress", app_css)
        self.assertIn(".conv-evergreen-meta-row > .conv-goal", app_css)
        self.assertIn(".conv-evergreen-meta-row > .conv-evergreen-state", app_css)
        self.assertIn(".conv-evergreen-agents-tree .conv-item.is-evergreen-agent-row .conv-main-row > .conv-row-end", app_css)
        title_css = app_css[
            app_css.index(".conv-evergreen-agents-tree .conv-item.is-evergreen-agent-row .conv-title {"):
            app_css.index(".conv-evergreen-meta-row {", app_css.index(".conv-evergreen-agents-tree .conv-item.is-evergreen-agent-row .conv-title {"))
        ]
        self.assertIn("font-size: 13.5px;", title_css)
        self.assertIn("font-weight: 650;", title_css)
        self.assertIn("color: var(--text);", title_css)
        self.assertIn("letter-spacing: 0;", title_css)
        self.assertIn(':root:not([data-theme="light"]) .conv-evergreen-agents-tree .conv-item.is-evergreen-agent-row .conv-title', app_css)
        self.assertIn("color: #f0f4fb;", app_css)

    def test_by_objects_current_and_evergreen_sections_have_subtle_bands(self):
        """Current sessions and evergreen agents should read as separate regions."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        current_css = app_css[
            app_css.index(".conv-current-sessions-scroll {"):
            app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))
        ]
        evergreen_css = app_css[
            app_css.index(".conv-evergreen-agents-scroll {"):
            app_css.index(".conv-project-tree {", app_css.index(".conv-evergreen-agents-scroll {"))
        ]

        self.assertIn("background: rgba(139, 148, 158, 0.07);", current_css)
        self.assertIn("border: 1px solid color-mix(in srgb, var(--border) 48%, transparent);", current_css)
        self.assertIn("background: rgba(139, 148, 158, 0.06);", evergreen_css)
        self.assertIn("border: 1px solid color-mix(in srgb, var(--border) 48%, transparent);", evergreen_css)
        self.assertIn("border-radius: 0 0 8px 8px;", evergreen_css)

    def test_by_objects_current_sessions_exclude_evergreen_agents(self):
        """Evergreen agents belong only in their own bottom region at rest."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _evergreenSessionIds = new Set();", app_js)
        # When a worker resolves to a loaded session card, its id is collected so
        # that session stays out of the normal Current sessions list (it lives in
        # its queue group, not twice).
        self.assertIn("if (cid) _evergreenSessionIds.add(cid);", app_js)
        self.assertIn("const _currentSessionSource = _ipSearchActive", app_js)
        self.assertIn("? (_visibleSessionConvs || []).slice()", app_js)
        self.assertTrue(
            "if (_evergreenSessionIds.has(c.session_id || c.id || '')) return false;" in app_js,
            "Current Sessions should exclude rows rendered in Triggered Workers.",
        )
        self.assertIn("const _currentSessions = _ipSearchActive\n        ? _currentSessionSource\n        : _currentSessionSource", app_js)

    def test_current_sessions_respect_inprogress_window_filter(self):
        """Current sessions should use the same 1d/7d/All window as by-objects."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _currentSessionsWindowS = _ipWindowDays ? (_ipWindowDays * 24 * 3600) : null;", app_js)
        self.assertIn("const _currentSessionsWindowLabel = _ipWindow === 'all' ? 'all' : (_ipWindow === '7d' ? 'last 7d' : 'last 1d');", app_js)
        self.assertIn("if (!_currentSessionsWindowS) return true;", app_js)
        self.assertIn("return _sessionTs(c) >= _nowS - _currentSessionsWindowS;", app_js)
        self.assertIn("'<span class=\"conv-objects-section-sub\">' + _currentSessionsWindowLabel + '</span>'", app_js)
        self.assertNotIn("const _LIVE_WINDOW_S = 5 * 3600;", app_js)
        self.assertNotIn("last 5h", app_js)

    def test_by_objects_split_mode_only_applies_to_active_tab(self):
        """All/Issues/Merge must keep normal sidebar scrolling even when the
        saved Active grouping is by objects."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _ipGrouping = _hasFolderChips ? 'objects' : 'time';", app_js)
        inprogress_toolbar_js = app_js[
            app_js.index("const _ipWindowToggle = _hasFolderChips"):
            app_js.index("const _ipObjectsExpandAll = _shouldGroupByObjects", app_js.index("const _ipWindowToggle = _hasFolderChips"))
        ]
        self.assertIn("const _ipGroupingToggle = '';", inprogress_toolbar_js)
        self.assertNotIn('data-role="grouping-toggle"', inprogress_toolbar_js)
        self.assertIn('data-role="archived-grouping-toggle"', app_js)
        self.assertIn("const _objectsSplitActive = _sidebarTab === 'inprogress' && _shouldGroupByObjects;", app_js)
        self.assertIn("$convList.classList.toggle('objects-scroll-split', _objectsSplitActive);", app_js)
        self.assertIn("if (_objectsSplitActive) { applyCurrentSessionsPanelHeight(); applyEvergreenPanelHeight(); }", app_js)
        self.assertIn("const _projectTreeScrollBefore = _objectsSplitActive", app_js)
        self.assertIn("const _projectTreeScrollAfter = _projectTreeScrollBefore", app_js)
        self.assertNotIn("$convList.classList.toggle('objects-scroll-split', !!_shouldGroupByObjects);", app_js)
        self.assertNotIn("if (_shouldGroupByObjects) applyCurrentSessionsPanelHeight();", app_js)

    def test_by_objects_splitter_can_hide_current_sessions(self):
        """Dragging the splitter to the top should be allowed to hide Current sessions."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _CURRENT_SESSIONS_ROW_H = 20;", app_js)
        self.assertIn("const _CURRENT_SESSIONS_LABEL_H = 18;", app_js)
        self.assertIn("const _CURRENT_SESSIONS_MIN_VISIBLE_ROWS = 8;", app_js)
        self.assertIn(
            "const _CURRENT_SESSIONS_DEFAULT_PANEL_H = _CURRENT_SESSIONS_LABEL_H + (_CURRENT_SESSIONS_ROW_H * _CURRENT_SESSIONS_MIN_VISIBLE_ROWS);",
            app_js,
        )
        clamp_start = app_js.index("function _clampObjectsSplitterHeight")
        clamp_body = app_js[clamp_start:app_js.index("function isSidebarDragInProgress", clamp_start)]
        self.assertIn("const minHeight = 0;", clamp_body)
        self.assertNotIn("const minHeight = _CURRENT_SESSIONS_MIN_PANEL_H;", clamp_body)
        self.assertNotIn("const minHeight = 78;", clamp_body)
        apply_start = app_js.index("function applyCurrentSessionsPanelHeight()")
        apply_body = app_js[apply_start:app_js.index("function _clampObjectsSplitterHeight", apply_start)]
        self.assertIn("const height = _clampObjectsSplitterHeight(list, storedHeight);", apply_body)
        self.assertIn("storedHeight == null", apply_body)
        self.assertIn("return Number.isFinite(n) && n >= 0 ? n : null;", app_js)
        self.assertIn("const storedStartHeight = _storedCurrentSessionsPanelHeight();", app_js)

    def test_current_sessions_skip_body_title_tooltip(self):
        """Current session titles should not duplicate themselves in a body tooltip."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("if (el.closest('.conv-current-sessions-scroll')) {", app_js)
        self.assertIn("hideTip();\n        return;", app_js)

    def test_by_objects_split_scrolls_survive_refresh_rebuilds(self):
        """Polling rebuilds should not snap either by-objects split pane to top."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        current_snapshot_pos = app_js.index("const _currentSessionsScrollBefore = _objectsSplitActive")
        snapshot_pos = app_js.index("const _projectTreeScrollBefore = _objectsSplitActive")
        inner_html_pos = app_js.index("$convList.innerHTML = _convListHtml;", snapshot_pos)
        current_restore_pos = app_js.index("const _currentSessionsScrollAfter = _currentSessionsScrollBefore", inner_html_pos)
        restore_pos = app_js.index("const _projectTreeScrollAfter = _projectTreeScrollBefore", inner_html_pos)
        self.assertLess(current_snapshot_pos, inner_html_pos)
        self.assertLess(snapshot_pos, inner_html_pos)
        self.assertLess(inner_html_pos, current_restore_pos)
        self.assertLess(inner_html_pos, restore_pos)
        self.assertIn("$convList.querySelector('[data-role=\"current-sessions-scroll\"]')", app_js[current_snapshot_pos:inner_html_pos])
        self.assertIn("$convList.querySelector('[data-role=\"project-tree-scroll\"]')", app_js[snapshot_pos:inner_html_pos])
        self.assertIn("const _currentSessionsScrollTop = _currentSessionsScrollBefore ? _currentSessionsScrollBefore.scrollTop : 0;", app_js)
        self.assertIn("const _projectTreeScrollTop = _projectTreeScrollBefore ? _projectTreeScrollBefore.scrollTop : 0;", app_js)
        self.assertIn("_restoreSplitScrollTop(_currentSessionsScrollAfter, _currentSessionsScrollTop);", app_js)
        self.assertIn("_restoreSplitScrollTop(_projectTreeScrollAfter, _projectTreeScrollTop);", app_js)

    def test_by_objects_search_results_show_row_previews(self):
        """Search results in by-objects mode should keep snippets/previews
        visible instead of inheriting the compact Current sessions row diet."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _currentSessionsScrollClass = 'conv-current-sessions-scroll'", app_js)
        self.assertIn("+ (_ipSearchActive ? ' is-search-results' : '')", app_js)
        self.assertIn("'<div class=\"' + _currentSessionsScrollClass + '\" data-role=\"current-sessions-scroll\">'", app_js)
        current_css = app_css[app_css.index(".conv-current-sessions-scroll {"):app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))]
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-extras { display: contents; }", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-extras > .conv-last,", current_css)
        self.assertIn(".conv-current-sessions-scroll.is-search-results .conv-item {", current_css)
        search_css = current_css[current_css.index(".conv-current-sessions-scroll.is-search-results .conv-item {"):]
        self.assertIn("padding: 8px 10px;", search_css)
        self.assertIn("min-height: 0;", search_css)

    def test_by_objects_current_sessions_hover_reveals_preview_row(self):
        """Current sessions should stay compact at rest but reveal row
        previews/metadata on hover or keyboard focus."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        current_css = app_css[app_css.index(".conv-current-sessions-scroll {"):app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))]
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-extras { display: contents; }", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-meta-row {", current_css)
        self.assertIn("display: flex;", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-meta-row .conv-meta-inline,", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-meta-row .conv-folder-chip,", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-outcome { display: none; }", current_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item.is-brief-open .conv-outcome { display: block; }", current_css)
        self.assertIn(".conv-brief-chevron {", current_css)
        self.assertNotIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item[data-hb]", current_css)
        self.assertNotIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item:hover:not(.active) > .conv-outcome", current_css)
        self.assertNotIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item:focus-within:not(.active) > .conv-outcome", current_css)
        self.assertNotIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item:hover > :not(.conv-title-row),\n  .conv-current-sessions-scroll:not(.is-search-results) .conv-item:focus-within > :not(.conv-title-row),\n  .conv-current-sessions-scroll:not(.is-search-results) .conv-item.active > :not(.conv-title-row) { display: none; }", current_css)

    def test_by_objects_current_sessions_nest_parented_sessions(self):
        """Current sessions should show parented child sessions directly under
        their visible parent, indented."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("parent_session_id: c.parent_session_id || ''", app_js)
        self.assertIn("hermes_child_session_ids: Array.isArray(c.hermes_child_session_ids) ? c.hermes_child_session_ids : []", app_js)
        self.assertIn("const _currentSessionParentId = (c) =>", app_js)
        self.assertIn("const _currentSessionsTreeRows = (rows) => {", app_js)
        self.assertIn("childrenByParent.get(pid) || childrenByParent.set(pid, []).get(pid)", app_js)
        self.assertIn("const _currentSessionRows = _ipSearchActive", app_js)
        self.assertIn("const _curShown = _currentSessionRows;", app_js)
        self.assertIn("html: cl.rows.map(item => _renderRow(item.card, { lifecycleContext: 'active', suppressFolderChip: false, quietTitleChrome: true, currentChildDepth: item.depth })).join(''),", app_js)
        self.assertIn("? _currentSessionsByObjectGroupsHtml(_curShown)", app_js)
        self.assertIn(": _currentSessionsFlatRowsWithSeparators(_curShown, _gcItems);", app_js)
        self.assertIn("const currentChildRowClass = currentChildDepth > 0 ? ' is-current-child-row' : '';", app_js)
        self.assertIn("const currentChildStyle = currentChildDepth > 0", app_js)
        self.assertIn(".conv-current-sessions-scroll .conv-item.is-current-child-row {", app_css)
        current_css = app_css[app_css.index(".conv-current-sessions-scroll {"):app_css.index("/* ============================================================", app_css.index(".conv-current-sessions-scroll {"))]
        self.assertIn("--current-child-indent: calc(var(--current-child-depth, 1) * 14px);", current_css)
        self.assertIn("--conv-icon-left: calc(10px + var(--current-child-indent));", current_css)

    def test_current_sessions_hide_ended_spawned_child_rows(self):
        """Ended spawned children should not flood the Current sessions band."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertTrue(
            "const _currentSessionIsEndedSpawnChild = (c) => {" in app_js,
            "Current Sessions should define the ended spawned-child filter.",
        )
        self.assertTrue(
            "if (source === 'hermes' || engine === 'hermes') return false;" in app_js,
            "Hermes continuation rows should be exempt by actual source/engine only.",
        )
        self.assertTrue(
            "if (c.is_live || c.pending_spawn || c.sidecar_in_flight || c.needs_approval || c.question_waiting) return false;" in app_js,
            "Only genuinely live or waiting spawned children should stay in Current Sessions.",
        )
        self.assertTrue(
            "if (_currentSessionIsEndedSpawnChild(c)) return false;" in app_js,
            "Current Sessions should suppress ended spawned children outside search.",
        )
        self.assertTrue(
            "? (_visibleSessionConvs || []).slice()" in app_js,
            "Search should keep the full visible source list.",
        )

    def test_by_objects_current_sessions_can_group_by_object(self):
        """The Current sessions band should have its own by-object toggle."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _currentSessionsMode = (() => {", app_js)
        self.assertIn("localStorage.getItem('ccc-current-sessions-mode') === 'objects'", app_js)
        self.assertIn('data-role="current-sessions-mode-toggle"', app_js)
        self.assertIn('data-current-sessions-mode="objects">By objects</span>', app_js)
        self.assertIn("function _currentSessionsByObjectGroupsHtml(items) {", app_js)
        self.assertIn("const _currentSessionsRowsHtml = _currentSessionsByObjects", app_js)
        self.assertIn("localStorage.setItem('ccc-current-sessions-mode', mode)", app_js)
        self.assertIn("$currentSessionsModeToggle.addEventListener('click'", app_js)
        self.assertIn(".conv-current-object-heading", app_css)

    def test_by_objects_current_session_object_groups_accept_session_drops(self):
        """Current-session object groups should reuse the object drop path."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('data-current-object-group="\' + escapeAttr(group.key) + \'" data-object-drop-zone="\' + escapeAttr(group.key) + \'"', app_js)
        self.assertIn("$convList.querySelectorAll('[data-object-drop-zone]').forEach(zone =>", app_js)
        self.assertIn("if (reparentConversationIdsToObject(target, convIds))", app_js)
        self.assertIn(".conv-current-object-group.is-drop-target", app_css)

    def test_sidebar_left_model_icon_uses_reserved_title_gutter(self):
        """Left-side model icons should center across expanded rows while the
        title and preview rows reserve the same left gutter."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        row_start = app_js.index("+ '<div class=\"conv-main-row\">'")
        row_end = app_js.index("// Right-edge slot", row_start)
        self.assertLess(app_js.index("+ sessionIconHtml", row_start, row_end), app_js.index("+ '<div class=\"conv-title '", row_start, row_end))
        self.assertNotIn("+   sessionIconHtml", app_js[app_js.index("+ '<span class=\"conv-row-end\">'", row_start):app_js.index("+ '</span>'", app_js.index("+ '<span class=\"conv-row-end\">'", row_start))])
        main_css = app_css[app_css.index(".conv-item .conv-main-row {"):app_css.index(".conv-summary-toggle", app_css.index(".conv-item .conv-main-row {"))]
        self.assertIn("padding-left: var(--conv-content-left);", main_css)
        self.assertIn("box-sizing: border-box;", main_css)
        self.assertIn(".conv-project-tree .conv-item .conv-main-row { padding-left: 0; }", app_css)
        icon_css = app_css[app_css.index(".conv-item .conv-session-icon {"):app_css.index(".conv-item:hover .conv-session-icon", app_css.index(".conv-item .conv-session-icon {"))]
        self.assertIn("position: absolute;", icon_css)
        self.assertIn("left: var(--conv-icon-left);", icon_css)
        self.assertIn("top: 50%;", icon_css)
        self.assertIn("transform: translateY(-50%);", icon_css)
        self.assertIn("flex: 0 0 var(--conv-dot-col);", icon_css)
        hover_meta_css = app_css[app_css.index(".conv-item .conv-hover-meta-row {"):app_css.index(".conv-item .conv-hover-meta-row > *", app_css.index(".conv-item .conv-hover-meta-row {"))]
        self.assertIn("margin: 4px 48px 0 var(--conv-content-left);", hover_meta_css)
        self.assertIn("max-width: calc(100% - var(--conv-content-left) - 70px);", hover_meta_css)
        self.assertNotIn("@keyframes ccc-icon-pulse", app_css)
        self.assertNotIn(".conv-session-icon.is-live", app_css)
        self.assertIn("@keyframes ccc-activity-dot-pulse", app_css)
        self.assertNotIn(".session-cost-orbit", app_css)
        self.assertIn(".session-tier-cost", app_css)

    def test_by_objects_draft_rows_align_with_sessions_and_show_play(self):
        """Draft rows should start where real sessions start, with an always
        visible play action immediately after the draft text."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function flowDraftTitleCh(value)", app_js)
        self.assertIn("const _draftTitleCh = flowDraftTitleCh(d.title || '');", app_js)
        self.assertIn("' style=\"--draft-title-ch:' + _draftTitleCh + 'ch\"'", app_js)
        self.assertIn("inp.style.setProperty('--draft-title-ch', flowDraftTitleCh(inp.value) + 'ch');", app_js)
        header_css = app_css[app_css.index(".conv-project-tree .conv-folder-group-header {"):app_css.index(".conv-project-tree .conv-folder-group-arrow", app_css.index(".conv-project-tree .conv-folder-group-header {"))]
        session_css = app_css[app_css.index(".conv-project-tree .conv-item {"):app_css.index(".conv-project-tree .conv-item .conv-title-row", app_css.index(".conv-project-tree .conv-item {"))]
        grouped_session_css = app_css[
            app_css.index(".conv-folder-group[data-object-drop-zone] > .conv-item.is-grouped-row {"):
            app_css.index(".conv-folder-group:not(.collapsed) .conv-item.is-grouped-row::before", app_css.index(".conv-folder-group[data-object-drop-zone] > .conv-item.is-grouped-row {"))
        ]
        draft_css = app_css[app_css.index(".conv-project-tree .conv-draft-row {"):app_css.index(".conv-item .conv-ux-fix-progress", app_css.index(".conv-project-tree .conv-draft-row {"))]
        self.assertIn("padding: 0 8px;", header_css)
        self.assertIn("padding: 0 8px;", session_css)
        self.assertIn("padding-left: 29px;", grouped_session_css)
        self.assertIn("display: flex;", draft_css)
        self.assertIn("align-items: center;", draft_css)
        self.assertIn("margin: 0 0 0 18px;", draft_css)
        self.assertIn("padding: 0 8px;", draft_css)
        self.assertIn(".conv-project-tree .conv-folder-group[data-object-drop-zone] > .conv-draft-row", draft_css)
        self.assertIn("padding-left: 29px;", draft_css)
        self.assertIn(".conv-project-tree .conv-draft-row .conv-draft-play {\n    display: inline-flex;", draft_css)
        self.assertIn("width: min(calc(var(--draft-title-ch, 16) * 1ch + 18px), 100%);", draft_css)
        self.assertNotIn(".conv-project-tree .conv-draft-row .conv-draft-play,\n  .conv-project-tree .conv-draft-row .conv-draft-delete { display: none; }", draft_css)

    def test_project_tree_session_rename_input_stays_visible(self):
        """Project-tree session rows reuse inline rename; their input must not
        be hidden by the tree's names-only row rule."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn(
            ".conv-project-tree .conv-item .conv-main-row > :not(.conv-title):not(.conv-title-input) { display: none; }",
            app_css,
        )
        self.assertIn(".conv-project-tree .conv-item .conv-title-input {", app_css)
        self.assertNotIn(".conv-project-tree .conv-item .conv-main-row > :not(.conv-title) { display: none; }", app_css)

    def test_object_header_actions_are_hover_revealed(self):
        """Object header actions should stay quiet until hover/focus."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-folder-object-actions {\n    grid-column: 5;", app_css)
        self.assertIn("opacity: 0;", app_css)
        self.assertIn("pointer-events: none;", app_css)
        self.assertIn(".conv-folder-group-header:hover .conv-folder-object-actions", app_css)
        self.assertIn(".conv-folder-group-header:focus-within .conv-folder-object-actions", app_css)
        self.assertIn("pointer-events: auto;", app_css)

    def test_sidebar_outcome_line_reads_as_bright_done_brief(self):
        """A session DID summary should read as a bright, aligned brief and
        skip the next-step line in the sidebar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-item .conv-outcome {\n"
                      "    font-size: 14px; margin: 6px 0 0 var(--conv-content-left); line-height: 1.42;\n"
                      "    display: block;", app_css)
        self.assertIn("overflow-wrap: anywhere;", app_css)
        self.assertIn(".conv-item .conv-outcome-did {\n    color: var(--text); opacity: 0.96;", app_css)
        self.assertNotIn(".conv-item .conv-outcome-did {\n"
                         "    color: var(--text-muted); opacity: 0.86;\n"
                         "    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;", app_css)
        self.assertIn('content: "DONE";', app_css)
        self.assertIn("font-size: 10px;", app_css)
        self.assertIn("border: 1px solid rgba(63,185,80,0.36);", app_css)
        self.assertIn("if (!isBacklogRow && !isGithubPrRow && _ss && _ss.did)", app_js)
        self.assertNotIn("conv-outcome-next", app_js[app_js.index("// Outcome line (GOAL-1)"):app_js.index("const summaryDetailHtml", app_js.index("// Outcome line (GOAL-1)"))])

    def test_code_blocks_keep_readable_contrast_in_light_theme(self):
        """Fenced transcript code blocks should not inherit dark text from
        bright conversation themes."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("--cb-bg: #0b1016;", app_css)
        self.assertIn("--cb-text: #d6deeb;", app_css)
        self.assertIn("--cb-muted: #9aa7b5;", app_css)
        cb_css = app_css[app_css.index(".cb-wrap {"):app_css.index(".mermaid-block {", app_css.index(".cb-wrap {"))]
        self.assertIn("background: var(--cb-bg);", cb_css)
        self.assertIn("color: var(--cb-muted);", cb_css)
        self.assertIn("color: var(--cb-text);", cb_css)
        self.assertIn('pre.cb code { background: transparent; padding: 0; font: inherit; color: inherit; }', app_css)

    def test_sticky_header_uses_conversation_palette_in_bright_mode(self):
        """The sticky Last intent panel should not keep dark hard-coded chrome
        over a bright conversation background."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(".conv-pane.has-conv-bg .conv-sticky-header .csh-row {", app_css)
        sticky_css = app_css[
            app_css.index(".conv-pane.has-conv-bg .conv-sticky-header .csh-row {"):
            app_css.index(".conv-pane.has-conv-bg .conv-sticky-header .csh-col-ask {")
        ]
        self.assertIn("background: var(--conv-surface-2);", sticky_css)
        self.assertIn("border-color: var(--conv-border);", sticky_css)
        self.assertIn("color: var(--conv-text);", sticky_css)
        self.assertIn("color: var(--conv-text);", app_css[
            app_css.index(".conv-pane.has-conv-bg .conv-sticky-header .csh-col-ask .ask-first,"):
            app_css.index(".conv-pane.has-conv-bg .conv-sticky-header .csh-col-ask .ask-rest {")
        ])

    def test_sidebar_rows_have_summary_details_toggle(self):
        """Session rows with session_state should expose an expand/collapse
        affordance for the detailed summary block."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function sessionSummaryStorageKey(sid)", app_js)
        self.assertIn("data-role=\"session-summary-toggle\"", app_js)
        self.assertIn("data-role=\"session-summary-detail\"", app_js)
        self.assertIn("localStorage.setItem(sessionSummaryStorageKey(sid), nextOpen ? '1' : '0')", app_js)
        self.assertIn(".conv-summary-toggle", app_css)
        self.assertIn(".conv-session-summary-detail", app_css)

    def test_sidebar_summary_toggle_lives_in_row_actions(self):
        """Summary details should not render as a stray left-edge chevron."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const summaryActionBtn = _hasSummaryDetails", app_js)
        self.assertIn("wakeBtn + summaryActionBtn + mergeBtn", app_js)
        self.assertNotIn("+ summaryToggleHtml\n            + cooTrackHtml", app_js)
        self.assertIn(".conv-row-actions .conv-summary-toggle", app_css)
        self.assertIn("width: 20px;", app_css[app_css.index(".conv-row-actions .conv-summary-toggle"):])

    def test_details_off_hides_git_state_chips_and_expands_search_snippets(self):
        """Compact Details-off rows should hide non-live git/PR chips and
        search rows should show a larger snippet preview."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function compactRowsOn()", app_js)
        self.assertIn("const _rowsCompactOn = compactRowsOn();", app_js)
        self.assertIn("const _showGitStateSignals = !_rowsCompactOn;", app_js)
        self.assertIn("} else if (_showGitStateSignals && isWorktree && c.worktree_dirty)", app_js)
        self.assertIn("} else if (_showGitStateSignals && c.tail_pr_number)", app_js)
        self.assertIn("conv-history-snippet is-search-result", app_js)
        self.assertIn(".compact-rows .conv-item .conv-signal.uncommitted", app_css)
        self.assertIn(".compact-rows .conv-item .conv-signal.pr-open", app_css)
        self.assertIn(".compact-rows .conv-item .conv-signal.pr-merged", app_css)
        self.assertIn(".compact-rows .conv-item .conv-signal.pr-closed", app_css)
        self.assertIn(".conv-history-snippet.is-search-result", app_css)
        self.assertIn("max-height: 9.8em;", app_css)

    def test_sidebar_row_metadata_reveals_on_active_row(self):
        """Repo/source/branch metadata should not crowd resting rows."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const folderChipHtml = (c.folder_label_chip && !opts.suppressFolderChip)", app_js)
        # CCC-467 follow-up: the transcript-size badge ("3MB") was dropped from
        # the meta row — it wrapped onto a second line and is redundant with the
        # size in the pane titlebar. rowSizeHtml is now a constant empty string.
        self.assertIn("const rowSizeHtml = '';", app_js)
        self.assertNotIn("+ '<span>' + formatSize(c.size) + '</span>'", app_js)
        self.assertIn("const _hmObjectChip = opts.elevateToObject ? '' : objectChipHtml;", app_js)
        self.assertIn("const _hasMetaContent = !opts.evergreenAgent && (_hmObjectChip || _hmFolderChip || sessionIdChipHtml || goalChipHtml || pinnedHtml || rowSizeHtml || branchSlotHtml || _hasBrief);", app_js)
        self.assertIn("const hoverMetaRowHtml = _hasMetaContent", app_js)
        self.assertIn("'<div class=\"conv-hover-meta-row\">'", app_js)
        self.assertIn("+ _briefChevronHtml", app_js)
        self.assertIn("+ _hmObjectChip\n          + _hmFolderChip", app_js)
        self.assertIn("+   hoverMetaRowHtml", app_js)
        row_start = app_js.index("+ '<div class=\"conv-main-row\">'")
        row_end = app_js.index("// Right-edge slot", row_start)
        self.assertNotIn("+ goalChipHtml", app_js[row_start:row_end])
        self.assertIn("+ (opts.evergreenAgent ? '' : (pctBadgeHtml || ''))", app_js[row_start:row_end])
        self.assertIn(".conv-item .conv-meta-inline,\n  .conv-item .conv-branch-slot,\n  .conv-item .conv-object-chip,\n  .conv-item .conv-folder-chip,\n  .conv-item .conv-repo-pin", app_css)
        self.assertIn(".conv-item .conv-hover-meta-row {\n    display: none;", app_css)
        hover_meta_css = app_css[
            app_css.index(".conv-item .conv-hover-meta-row {"):
            app_css.index(".conv-item .conv-hover-meta-row > *", app_css.index(".conv-item .conv-hover-meta-row {"))
        ]
        self.assertIn("font-size: 11px;", hover_meta_css)
        self.assertIn("color: color-mix(in srgb, var(--text) 74%, var(--text-muted));", hover_meta_css)
        self.assertIn("opacity: 1;", hover_meta_css)
        hover_chip_css = app_css[
            app_css.index(".conv-item .conv-hover-meta-row .conv-folder-chip {"):
            app_css.index(".conv-item .conv-hover-meta-row .conv-meta-inline", app_css.index(".conv-item .conv-hover-meta-row .conv-folder-chip {"))
        ]
        self.assertIn("font-size: 11px;", hover_chip_css)
        self.assertIn("height: 20px;", hover_chip_css)
        self.assertIn("background: hsla(var(--chip-hue), 60%, 50%, 0.18);", hover_chip_css)
        self.assertIn("border-color: hsla(var(--chip-hue), 55%, 50%, 0.35);", hover_chip_css)
        self.assertIn("color: hsl(var(--chip-hue), 55%, 65%);", hover_chip_css)
        self.assertNotIn("background: rgba(154, 173, 194, 0.14);", hover_chip_css)
        hover_badge_css = app_css[
            app_css.index(".conv-item .conv-hover-meta-row .branch-badge,"):
            app_css.index(".conv-item .conv-hover-meta-row .conv-goal .conv-goal-icon", app_css.index(".conv-item .conv-hover-meta-row .branch-badge,"))
        ]
        self.assertIn("font-size: 11px;", hover_badge_css)
        self.assertIn(".conv-item.active .conv-hover-meta-row { display: flex; }", app_css)
        self.assertIn(".conv-item.active .conv-hover-meta-row .conv-meta-inline,", app_css)
        self.assertNotIn(".conv-item.active .conv-hover-meta-row .conv-meta-inline .source-badge", app_css)
        self.assertIn(".compact-rows .conv-item.active .conv-hover-meta-row { display: flex; }", app_css)
        self.assertIn(".conv-item.active .conv-hover-meta-row .conv-goal { opacity: 1; }", app_css)

    def test_sidebar_hover_metadata_has_copyable_session_id_chip(self):
        """Active-row metadata should include a compact copyable session id
        chip with an inline copied confirmation."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function sidebarSessionIdChipHtml(c)", app_js)
        self.assertIn("data-copy-row-session-id", app_js)
        self.assertIn("data-session-id-short", app_js)
        self.assertIn("const sessionIdChipHtml = sidebarSessionIdChipHtml(c);", app_js)
        self.assertIn("_hmObjectChip || _hmFolderChip || sessionIdChipHtml", app_js)
        self.assertIn("+ sessionIdChipHtml", app_js)
        self.assertIn("function handleSidebarSessionIdCopyClick(ev)", app_js)
        self.assertIn("document.addEventListener('click', handleSidebarSessionIdCopyClick, true);", app_js)
        self.assertIn("Copied session ID", app_js)
        self.assertIn(".conv-sidebar-session-id-chip", app_css)
        self.assertIn(".conv-sidebar-session-id-chip.copied", app_css)

    def test_sidebar_hover_metadata_shows_object_membership_chip(self):
        """Hovered current-session rows should name their Flow object."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function flowObjectForConversation(c)", app_js)
        self.assertIn("function flowObjectChipHtml(c)", app_js)
        self.assertIn("const objectChipHtml = flowObjectChipHtml(c);", app_js)
        self.assertIn("_hmObjectChip || _hmFolderChip || sessionIdChipHtml", app_js)
        self.assertIn("+ _hmObjectChip\n          + _hmFolderChip", app_js)
        self.assertIn("Object &middot; ", app_js)
        self.assertIn(".conv-item .conv-hover-meta-row .conv-object-chip", app_css)
        self.assertIn(".conv-current-sessions-scroll:not(.is-search-results) .conv-item .conv-hover-meta-row .conv-object-chip,", app_css)
        self.assertIn(".conv-item.active .conv-hover-meta-row .conv-object-chip,", app_css)

    def test_sidebar_add_to_object_lives_in_rail_not_per_row_chip(self):
        """CCC-467: the per-row "+" add-to-object chip is gone; assigning an
        object now happens from the RHS status rail for the selected session,
        via #statusRailAddObjectBtn. The underlying picker + assignment logic
        is unchanged."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")

        # Per-row empty "+" chip is dropped.
        self.assertNotIn('class="conv-object-chip is-empty"', app_js)
        # Assign affordance moved to the rail head, wired to the same picker.
        self.assertIn('id="statusRailAddObjectBtn"', index_html)
        self.assertIn("const $statusRailAddObjectBtn = document.getElementById('statusRailAddObjectBtn');", app_js)
        self.assertIn("_flowOpenObjectAssignPicker(sid, title);", app_js)
        # Picker + assignment logic still present.
        self.assertIn("function _flowOpenObjectAssignPicker(sessionId, sessionTitle)", app_js)
        self.assertIn("flowNodeParents[flowNodeKey('session', sessionId)] = flowNodeKey('object', objectId);", app_js)

    def test_sidebar_titles_strip_leading_pasted_image_paths(self):
        """Current-session rows should show the human task, not the leading
        pasted-image file path that can be captured in display_name."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function capitalizeSessionTitleStart(title)", app_js)
        self.assertIn("function sidebarRowDisplayTitle(rawTitle)", app_js)
        self.assertIn("replace(LEADING_CCC_PASTED_IMAGE_PATH_RE, '')", app_js)
        self.assertIn("pasted[ -]images", app_js)
        self.assertIn("return capitalizeSessionTitleStart(cleanedTitle);", app_js)
        self.assertIn("let title = sidebarRowDisplayTitle(rawTitle);", app_js)
        self.assertNotIn("let title = rawTitle.replace(/-/g, ' ');", app_js)

    def test_current_session_rows_use_plain_title_chrome(self):
        """The by-objects current-session strip should not add extra title
        marker glyphs in front of compact row names."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const quietTitleChrome = !!opts.quietTitleChrome;", app_js)
        self.assertIn("if (titleSource === 'ai' && !quietTitleChrome) title = '✨ ' + title;", app_js)
        self.assertIn("if (c.name_overridden && !quietTitleChrome) titleClass = 'user-renamed';", app_js)
        self.assertIn("html: cl.rows.map(item => _renderRow(item.card, { lifecycleContext: 'active', suppressFolderChip: false, quietTitleChrome: true, currentChildDepth: item.depth })).join(''),", app_js)
        self.assertIn("? _currentSessionsByObjectGroupsHtml(_curShown)", app_js)
        self.assertIn(": _currentSessionsFlatRowsWithSeparators(_curShown, _gcItems);", app_js)

    def test_repo_pin_marker_is_not_duplicate_pin_glyph(self):
        """Repo override rows should use a distinct repo chip, not a second pin."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(">repo</button>", app_js)
        self.assertNotIn('class="conv-repo-pin" data-role="unpin-repo" title="Pinned to this repo. Click to reset to the session’s real repo.">&#128204;</button>', app_js)
        self.assertIn(".conv-repo-pin {\n    display: inline-flex;", app_css)

    def test_toolbar_single_row_and_rail_close_in_flow(self):
        """The conversation toolbar should not wrap, and the rail close control
        should not absolutely overlay conversation/rail content."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("display: flex; align-items: center; gap: 10px; flex-wrap: nowrap;", app_css)
        self.assertIn("overflow: hidden;", app_css)
        # CCC-450 moved the quick-close x into the topbar as an in-flow flex
        # button; the invariant is still "no absolute overlay".
        self.assertIn(".status-rail-close {\n    flex: 0 0 auto;", app_css)
        self.assertNotIn(".status-rail-close {\n    position: absolute;", app_css)

    def test_coo_status_pill_names_its_source(self):
        """The COO activity badge should explain what creates the status."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _displayLabel = 'COO · ' + _label;", app_js)
        self.assertIn("COO status from Command Center's COO tracker", app_js)
        self.assertIn("aria-label=\"' + escapeAttr(_cooStatusTip) + '\"", app_js)

    def test_stale_sidecar_does_not_count_as_live(self):
        """A Claude liveness sidecar only counts while fresh. The hooks never
        delete these markers on session end, so a stale marker must NOT keep a
        long-dead session flagged live (regression: sessions idle for days were
        still reported is_live)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        sid = "11111111-2222-3333-4444-555555555555"  # not a real engine session
        with tempfile.TemporaryDirectory() as d:
            dpath = pathlib.Path(d)
            engine_patches = [
                mock.patch.object(server, name, return_value=False)
                for name in ("_is_codex_session", "_is_cursor_session",
                             "_is_gemini_session", "_is_antigravity_session",
                             "_is_kilo_session")
            ]
            with mock.patch.object(server, "SIDECAR_STATE_DIR", dpath), \
                 mock.patch.object(server, "_live_engine_session_ids", return_value=set()):
                for p in engine_patches:
                    p.start()
                try:
                    marker = dpath / f"{sid}.json"
                    marker.write_text("{}")
                    # Fresh marker → live.
                    self.assertTrue(server._archive_session_is_live(sid))
                    # Stale marker (older than the window) → not live. Clear the
                    # per-session liveness memo (CCC_SESSION_LIVE_TTL) first — it
                    # would otherwise still serve the fresh-marker True from above.
                    old = time.time() - (server._SIDECAR_LIVE_WINDOW + 600)
                    os.utime(marker, (old, old))
                    server._session_live_cache.clear()
                    self.assertFalse(server._archive_session_is_live(sid))
                finally:
                    for p in engine_patches:
                        p.stop()

    def test_ship_index_attribution_is_wired_and_degrades(self):
        """The conversation-index attribution layer is defined, the verdict +
        ship-flow consult it, and a missing/erroring index degrades silently to
        git-only (None) — never raises. No real index is touched: we monkeypatch
        search_conversation_history to mimic the index-missing/error contract."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Helper exists and is referenced by both consumers (source-level — we
        # don't run the daemon flow, just prove the wiring).
        self.assertTrue(hasattr(server, "_ship_index_attribution"))
        self.assertIn("_ship_index_attribution", inspect.getsource(server._ship_review_verdict))
        self.assertIn("_ship_index_attribution", inspect.getsource(server._run_ship_flow))
        # Index missing → {"error": ...} contract → None, no raise.
        with mock.patch.object(server, "search_conversation_history",
                               return_value={"error": "no index", "results": []}):
            self.assertIsNone(server._ship_index_attribution("/tmp/repo", "static/app.js"))
        # The reader raising → still None (never load-bearing).
        with mock.patch.object(server, "search_conversation_history",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(server._ship_index_attribution("/tmp/repo", "static/app.js"))

    def test_claude_append_prompt_discourages_blocking_recursive_grep(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        args = server._claude_session_state_args()

        self.assertEqual(args[0], "--append-system-prompt")
        self.assertIn("Do not run `grep -r`", args[1])
        self.assertIn(".claude/logs/*.stdin", args[1])
        self.assertIn("<session-state>", args[1])

    def test_spawn_defaults_drive_omitted_spawn_engine_and_model(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.SPAWN_DEFAULTS_FILE
            server.SPAWN_DEFAULTS_FILE = pathlib.Path(td) / "spawn-defaults.json"
            try:
                with mock.patch.object(server, "_antigravity_cli_configured_model", return_value=""):
                    saved = server._save_spawn_defaults({
                        "engine": "codex",
                        "reasoning_effort": "high",
                        "models": {
                            "claude": "sonnet-4-6",
                            "codex": "gpt-5-codex",
                            "cursor": "composer-2.5",
                            "antigravity": "",
                        },
                    })
                    self.assertTrue(saved["ok"])
                    self.assertEqual(saved["reasoning_effort"], "high")

                    engine, model = server._spawn_request_engine_and_model({})
                    self.assertEqual(engine, "codex")
                    self.assertEqual(model, "gpt-5-codex")
                    self.assertEqual(server._spawn_request_reasoning_effort({}, engine), "high")
                    self.assertEqual(
                        server._spawn_request_reasoning_effort({"reasoning_effort": "low"}, engine),
                        "low",
                    )
                    self.assertEqual(
                        server._spawn_request_reasoning_effort({"reasoning_effort": ""}, engine),
                        "",
                    )

                    engine, model = server._spawn_request_engine_and_model({"engine": "claude"})
                    self.assertEqual(engine, "claude")
                    self.assertEqual(model, "sonnet-4-6")

                    engine, model = server._spawn_request_engine_and_model({
                        "engine": "claude",
                        "model": "opus-4-7",
                    })
                    self.assertEqual(engine, "claude")
                    self.assertEqual(model, "opus-4-7")

                    engine, model = server._spawn_request_engine_and_model({"engine": "cursor"})
                    self.assertEqual(engine, "cursor")
                    self.assertEqual(model, "composer-2.5")

                    engine, model = server._spawn_request_engine_and_model({"engine": "gemini"})
                    self.assertEqual(engine, "antigravity")
                    self.assertIsNone(model)

                    engine, model = server._spawn_request_engine_and_model({"engine": "bogus"})
                    self.assertIsNone(engine)
                    self.assertIsNone(model)
            finally:
                server.SPAWN_DEFAULTS_FILE = old_file

    def test_codex_spawn_default_prefers_best_model(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.SPAWN_DEFAULTS_FILE
            server.SPAWN_DEFAULTS_FILE = pathlib.Path(td) / "spawn-defaults.json"
            try:
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(server._spawn_fallback_model_for_engine("codex"), "gpt-5.5")
                    defaults = server._load_spawn_defaults()
                    self.assertEqual(defaults["models"]["codex"], "gpt-5.5")

                    server.SPAWN_DEFAULTS_FILE.write_text(json.dumps({
                        "engine": "codex",
                        "models": {"codex": "gpt-5.4"},
                    }), encoding="utf-8")
                    defaults = server._load_spawn_defaults()
                    self.assertEqual(defaults["models"]["codex"], "gpt-5.4")

                with mock.patch.dict(os.environ, {"CCC_CODEX_MODEL": "gpt-5.4"}, clear=True):
                    self.assertEqual(server._spawn_fallback_model_for_engine("codex"), "gpt-5.4")
            finally:
                server.SPAWN_DEFAULTS_FILE = old_file

    def test_engine_model_catalog_merges_local_codex_sources(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            codex_home = root / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                'model = "gpt-5.6-luna"\n',
                encoding="utf-8",
            )
            (codex_home / "models_cache.json").write_text(json.dumps({
                "models": [
                    {
                        "slug": "gpt-5.4-mini",
                        "display_name": "GPT-5.4 Mini",
                        "visibility": "list",
                        "priority": 23,
                        "supported_reasoning_levels": [{"effort": "low"}],
                    },
                    {
                        "slug": "o3",
                        "display_name": "O3",
                        "visibility": "list",
                    },
                    {
                        "slug": "codex-hidden-test",
                        "display_name": "Hidden",
                        "visibility": "hide",
                    },
                ],
            }), encoding="utf-8")

            old_file = server.SPAWN_DEFAULTS_FILE
            old_cache = dict(server._MODEL_CATALOG_CACHE)
            server.SPAWN_DEFAULTS_FILE = root / "spawn-defaults.json"
            try:
                with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False), \
                     mock.patch.object(server, "_harness_model_list_result", return_value={"available": False, "records": []}), \
                     mock.patch.object(server, "_antigravity_cli_configured_model", return_value=""):
                    payload = server._build_engine_model_catalog(force_refresh=True)
            finally:
                server.SPAWN_DEFAULTS_FILE = old_file
                server._MODEL_CATALOG_CACHE.clear()
                server._MODEL_CATALOG_CACHE.update(old_cache)

        codex_ids = payload["engines"]["codex"]
        self.assertEqual(codex_ids, [
            "gpt-5.5",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex-spark",
        ])
        self.assertNotIn("o3", codex_ids)
        self.assertNotIn("codex-hidden-test", codex_ids)
        self.assertEqual(payload["enforced"], [])
        self.assertFalse(payload["catalog"]["codex"]["supports_custom"])
        labels = [m["label"] for m in payload["catalog"]["codex"]["models"]]
        self.assertEqual(labels[:4], ["5.5", "5.6 Sol", "5.6 Terra", "5.6 Luna"])
        mini = next(m for m in payload["catalog"]["codex"]["models"] if m["id"] == "gpt-5.4-mini")
        self.assertIn("codex-cache", mini["sources"])
        self.assertEqual(mini["reasoning_efforts"], ["low"])

    def test_observed_model_records_keep_each_cached_transcript_engine(self):
        """Transcript-derived models must not leak into Claude's picker."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        old_cache = dict(server._conv_meta_cache)
        try:
            server._conv_meta_cache.clear()
            server._conv_meta_cache.update({
                "codex-session": {"engine": "codex", "model": "gpt-5.6-sol"},
                "cursor-session": {"engine": "cursor", "model": "composer-2.5"},
            })
            with mock.patch.object(server, "_load_session_overrides", return_value={}), \
                 mock.patch.object(server, "_load_spawn_registry", return_value=[]), \
                 mock.patch.object(server, "_codex_thread_registry_entries", return_value={}), \
                 mock.patch.object(server, "_spawned_sessions", []):
                records = server._observed_model_records()
        finally:
            server._conv_meta_cache.clear()
            server._conv_meta_cache.update(old_cache)

        self.assertIn(
            {"engine": "codex", "id": "gpt-5.6-sol", "label": "gpt-5.6-sol", "source": "transcript-cache"},
            records,
        )
        self.assertIn(
            {"engine": "cursor", "id": "composer-2.5", "label": "composer-2.5", "source": "transcript-cache"},
            records,
        )

    def test_engine_model_catalog_filters_foreign_observed_models(self):
        """A stale engine tag must not put a foreign model in another picker."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        old_cache = dict(server._MODEL_CATALOG_CACHE)
        observed = [
            {"engine": "claude", "id": "gpt-5.6-sol", "label": "gpt-5.6-sol", "source": "session-override"},
            {"engine": "cursor", "id": "composer-2.5", "label": "composer-2.5", "source": "transcript-cache"},
        ]
        try:
            with mock.patch.object(server, "_observed_model_records", return_value=observed), \
                 mock.patch.object(server, "_harness_model_list_result", return_value={"available": False, "records": []}), \
                 mock.patch.object(server, "_codex_models_cache_records", return_value=[]), \
                 mock.patch.object(server, "_codex_configured_model", return_value=""), \
                 mock.patch.object(server, "_antigravity_cli_configured_model", return_value=""):
                payload = server._build_engine_model_catalog(force_refresh=True)
        finally:
            server._MODEL_CATALOG_CACHE.clear()
            server._MODEL_CATALOG_CACHE.update(old_cache)

        self.assertNotIn("gpt-5.6-sol", payload["engines"]["claude"])
        self.assertIn("composer-2.5", payload["engines"]["cursor"])

    def test_codex_model_catalog_marks_missing_cli_models_unavailable(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        fake_debug_models = {
            "available": True,
            "records": [
                {"id": "gpt-5.5", "label": "GPT-5.5", "source": "codex-cli"},
                {"id": "gpt-5.4", "label": "GPT-5.4", "source": "codex-cli"},
                {"id": "gpt-5.4-mini", "label": "GPT-5.4-Mini", "source": "codex-cli"},
                {"id": "gpt-5.3-codex-spark", "label": "GPT-5.3-Codex-Spark", "source": "codex-cli"},
            ],
            "command": ["codex", "debug", "models"],
        }

        old_cache = dict(server._MODEL_CATALOG_CACHE)
        try:
            with mock.patch.object(server, "_harness_model_list_result", return_value=fake_debug_models), \
                 mock.patch.object(server, "_codex_models_cache_records", return_value=[]), \
                 mock.patch.object(server, "_codex_configured_model", return_value=""), \
                 mock.patch.object(server, "_antigravity_cli_configured_model", return_value=""):
                payload = server._build_engine_model_catalog(force_refresh=True)
                codex_models = payload["catalog"]["codex"]["models"]
                luna = next(m for m in codex_models if m["id"] == "gpt-5.6-luna")
                self.assertFalse(luna["available"])
                self.assertIn("codex update", luna["availability_reason"])
                self.assertTrue(next(m for m in codex_models if m["id"] == "gpt-5.5")["available"])
                model, error = server._validate_codex_model("gpt-5.6-luna", require_available=True)
                self.assertEqual(model, "gpt-5.6-luna")
                self.assertIn("unavailable", error)
        finally:
            server._MODEL_CATALOG_CACHE.clear()
            server._MODEL_CATALOG_CACHE.update(old_cache)

    def test_codex_model_validation_enforces_picker_models(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        self.assertEqual(server._validate_codex_model("gpt-5.6-luna"), ("gpt-5.6-luna", None))
        self.assertEqual(server._validate_codex_model("gpt-5.5-codex"), ("gpt-5.5", None))
        model, error = server._validate_codex_model("gpt-5.6-preview")
        self.assertEqual(model, "gpt-5.6-preview")
        self.assertIn("unsupported codex model", error)

    def test_static_model_picker_uses_server_catalog_and_codex_allowlist(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()

        self.assertIn("fetch('/api/engines/models'", app_js)
        self.assertIn("function _modelAllowedForEngine", app_js)
        self.assertIn("gpt-5.6-sol", app_js)
        self.assertIn("gpt-5.6-terra", app_js)
        self.assertIn("gpt-5.6-luna", app_js)
        self.assertIn("opt.disabled", app_js)
        self.assertIn("_modelUnavailableReason", app_js)
        self.assertIn("if (_engineSupportsCustomModel(engine))", app_js)
        self.assertIn("ENGINE_SUPPORTS_CUSTOM_MODEL[engine] = info.supports_custom", app_js)

    def test_morning_disabled_when_plugin_absent(self):
        """If morning.py isn't importable, MORNING_ENABLED must be False
        no matter what CCC_ENABLE_MORNING says."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        # Force-flag morning as missing by setting an env that doesn't
        # affect the actual import attempt — we just verify the gate
        # short-circuits when _MORNING_IMPORTABLE is False.
        server = importlib.import_module("server")
        if not server._MORNING_IMPORTABLE:
            self.assertFalse(server.MORNING_ENABLED,
                             "MORNING_ENABLED must be False when plugin missing")

    def test_page_annotation_is_bounded_and_persisted(self):
        """Browser annotations should store local context without requiring
        screenshot support or touching the real user state directory."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                # html_excerpt / nearby_text / selected_text / document_rect
                # used to be persisted alongside each annotation. They were
                # dropped because the screenshot + selector + note are enough
                # for Claude to act, and the raw outerHTML / surrounding
                # paragraphs added kilobytes of noise to every wire payload.
                result = server.create_annotation({
                    "note": "Check this button state",
                    "url": "http://127.0.0.1:8090/",
                    "title": "Claude Command Center",
                    "rect": {"x": 10, "y": 20, "width": 120, "height": 32},
                    "element": {
                        "tag": "button",
                        "selector": "#annotationStartBtn",
                        "text": "Annotate",
                    },
                    "html_excerpt": "<button>" + ("x" * 9000) + "</button>",
                    "nearby_text": "surrounding context",
                    "capture_screen": False,
                })
                self.assertTrue(result["ok"])
                saved = server.list_annotations(limit=10)
                self.assertEqual(saved["count"], 1)
                ann = saved["annotations"][0]
                self.assertEqual(ann["note"], "Check this button state")
                self.assertEqual(ann["element"]["selector"], "#annotationStartBtn")
                self.assertNotIn("html_excerpt", ann)
                self.assertNotIn("nearby_text", ann)
                self.assertNotIn("selected_text", ann)
                self.assertNotIn("document_rect", ann)
                self.assertNotIn("screenshot_path", ann)
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_screen_annotation_saves_local_screenshot(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                result = server.create_annotation({
                    "note": "Look at this screen region",
                    "source": "screen-capture",
                    "screenshot_b64": "dGVzdC1pbWFnZS1ieXRlcw==",
                })
                self.assertTrue(result["ok"])
                ann = result["annotation"]
                self.assertEqual(ann["source"], "screen-capture")
                shot = pathlib.Path(ann["screenshot_path"])
                self.assertTrue(shot.is_file())
                self.assertEqual(shot.read_bytes(), b"test-image-bytes")
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_annotation_screenshot_warning_is_persisted(self):
        """A failed screenshot should carry the actual capture diagnostic into
        the saved annotation so queue workers do not infer a permission issue."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                with mock.patch.object(
                    server,
                    "_capture_annotation_screenshot_native",
                    return_value=(None, "window capture failed: front window was unavailable"),
                ):
                    result = server.create_annotation({
                        "note": "Screenshot failed",
                        "capture_screen": True,
                    })
                self.assertTrue(result["ok"])
                self.assertEqual(
                    result["screenshot_warning"],
                    "window capture failed: front window was unavailable",
                )
                ann = result["annotation"]
                self.assertEqual(
                    ann["screenshot_warning"],
                    "window capture failed: front window was unavailable",
                )
                saved = server.list_annotations(limit=10)["annotations"][0]
                self.assertEqual(saved["screenshot_warning"], ann["screenshot_warning"])
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_breadcrumb_has_popout_button_wired_to_existing_helper(self):
        """The conversation breadcrumb gains a pop-out button that reuses the
        existing drag-to-out-of-window helper. The button is delegated so it
        survives every updatePaneHeader innerHTML rewrite, and it is hidden
        when the page is itself the popout (CONV_POPOUT_MODE)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="ccc-breadcrumb-popout"', app_js)
        self.assertIn("CONV_POPOUT_MODE ? ''", app_js)
        self.assertIn("openConversationPopout(convId, null, null)", app_js)
        self.assertIn(".ccc-breadcrumb-popout", app_css)

    def test_global_breadcrumb_streaming_status_stays_compact(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const headLabel = streaming ? (stale ? 'stream-json ⚠' : 'stream-json')", app_js)
        self.assertIn("#cccBreadcrumb .ccc-proc-checked { display: none; }", app_css)
        self.assertNotIn("(stale ? 'headless ⚠' : 'headless') + (streaming ? ' · stream-json' : '')", app_js)

    def test_terminal_repo_comes_from_open_conversation(self):
        """The repo picker (dropdown + modal) is gone: the conversation
        list has exactly one mode (all repos), and anything needing a
        concrete repo derives it from the OPEN conversation. The
        terminal panel must resolve its repo via activeConvRepoPath
        and refresh when the open conversation changes — no picker."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        # The picker machinery must not come back.
        for gone in ("cccOpenRepoPicker", "repoPickerModal", "sbRepoPicker",
                     "archiveFolderFilter", "selectedRepoPath", "requireSelectedRepo"):
            self.assertNotIn(gone, app_js, gone)
            self.assertNotIn(gone, index_html, gone)
        # App.js exposes the open-conversation repo for inline scripts.
        self.assertIn("activeConvRepoPath,", app_js)
        # Terminal panel keys its repo off the open conversation and
        # re-resolves cwd when the conversation switches.
        self.assertIn("window.activeConvRepoPath", index_html)
        self.assertIn("ccc-repo-changed", index_html)
        self.assertIn("ccc-repo-changed", app_js)

    def test_annotation_text_strips_lone_surrogates(self):
        """An unpaired UTF-16 surrogate code point (U+D800..U+DFFF)
        coming from the browser's clipboard / selection APIs used to
        sail through _annotation_text and then break the downstream
        Anthropic API call with "no low surrogate in string". The
        sanitizer must drop lone surrogates AND leave real astral
        chars (paired surrogates collapsed into a single Python code
        point) untouched."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Lone high surrogate in the middle of normal text. Build it at
        # runtime so the source file itself never contains a literal
        # backslash-u surrogate escape that can poison a Claude transcript
        # when another agent reads this test.
        lone_high = chr(0xD83D)
        dirty = "fix the bug " + lone_high + " in row 42"
        cleaned = server._annotation_text(dirty)
        self.assertNotIn(lone_high, cleaned)
        # Result must round-trip through json + utf-8 — that's the
        # failure surface the Anthropic API rejects.
        json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        # A real astral character (😀 = U+1F600, one Python code point)
        # must survive — only LONE surrogates are stripped.
        kept = server._annotation_text("hi 😀 there")
        self.assertIn("😀", kept)
        # _inject_text_into_session uses the same strip so a missed
        # entry point still can't leak a surrogate to the API.
        self.assertIn("_strip_lone_surrogates", inspect.getsource(server._inject_text_into_session))

    def test_annotation_notes_render_screenshots(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("ann-note-shot", app_js)
        self.assertIn("/api/local-image?path=", app_js)
        self.assertIn("data-ann-open-session", app_js)
        self.assertIn("function annOpenNewSessionWithContext", app_js)
        self.assertIn("enterNewSessionMode(text)", app_js)
        self.assertIn("data-ann-ux-queue", app_js)
        self.assertIn("function annOpenUxFixesQueue", app_js)
        self.assertIn("/api/annotations/ux-fixes-queue", app_js)
        self.assertIn("Add to UX fixes queue", app_js)
        self.assertIn("Session ID: ", app_js)
        self.assertIn("persistAnnotation", app_js)
        self.assertIn("annCaptureRegionB64", app_js)
        self.assertIn("annBeginTabCaptureRequest", app_js)
        self.assertIn("annCaptureDomRegionB64", app_js)
        self.assertIn("data-ann-enable-shot", app_js)
        self.assertIn("CCC will try to attach a screenshot automatically.", app_js)
        self.assertIn("savedAnnotation.screenshot_warning = (data && data.screenshot_warning) || '';", app_js)
        self.assertIn("Screenshot warning: ", app_js)
        self.assertNotIn("Screenshots use macOS Screen Recording — grant it to Claude Command Center", app_js)
        self.assertNotIn("grant Screen Recording to the CCC server", app_js)
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("screenshot_warning", server_py)
        self.assertIn("capture failed; check Screen Recording permission, window focus, or region bounds", server_py)
        self.assertNotIn("grant Screen Recording to the CCC server process", server_py)
        self.assertNotIn("other tool", app_js.lower())

    def test_annotation_page_screenshot_starts_before_editor_opens(self):
        """Page annotation screenshots should represent the selected page
        before the note/Queue dialog exists."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function annBeginPreEditorScreenshotCapture()", app_js)
        self.assertIn("function annResolvePreEditorScreenshot()", app_js)
        pointer_start = app_js.index("function annPointerUp(e)")
        pointer_body = app_js[pointer_start:app_js.index("function annHandleKeydown", pointer_start)]
        self.assertIn("annotationState.preEditorScreenshotPromise = annBeginPreEditorScreenshotCapture();", pointer_body)
        self.assertLess(
            pointer_body.index("annotationState.preEditorScreenshotPromise = annBeginPreEditorScreenshotCapture();"),
            pointer_body.index("annShowEditor();"),
        )
        self.assertLess(
            pointer_body.index("await annotationState.preEditorScreenshotPromise;"),
            pointer_body.index("annShowEditor();"),
        )
        persist_start = app_js.index("const persistAnnotation = async (busyLabel) => {")
        persist_body = app_js[persist_start:app_js.index("const save = async () => {", persist_start)]
        self.assertIn("const screenshotB64 = await annResolvePreEditorScreenshot();", persist_body)
        self.assertNotIn("annCaptureDomRegionB64(contextRect, captureElement)", persist_body)
        self.assertNotIn("annotationState.overlay.classList.add('ann-capturing')", persist_body)

    def test_annotation_empty_pre_editor_capture_uses_capability_gated_native_fallback(self):
        """A failed DOM/tab pre-capture falls back only when native screenshots exist."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        build_start = app_js.index("function annBuildPayload(note, screenshotB64)")
        build_body = app_js[build_start:app_js.index("function annStop()", build_start)]
        self.assertIn("const canNativeScreenshot = caps.screenshots !== false;", build_body)
        self.assertIn("capture_screen: !screenshotB64 && canNativeScreenshot,", build_body)
        self.assertNotIn("capture_screen: !(annotationState && annotationState.preEditorCaptureAttempted)", build_body)
        self.assertNotIn("preEditorCaptureAttempted", build_body)

    def test_annotation_controls_split_page_and_native_capture_capabilities(self):
        """The page Annotate button stays available when native screenshots do not."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn(".ccc-no-screenshots #annotationScreenBtn", app_css)
        self.assertIn(".ccc-no-annotate [data-flow-action=\"annotate\"]", app_css)
        self.assertNotIn(".ccc-no-annotate #annotationStartBtn", app_css)

    def test_annotation_editor_previews_pre_dialog_screenshot(self):
        """The annotation editor should show the screenshot that was captured
        before the dialog appeared."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("ann-editor-shot-preview", app_js)
        self.assertIn("function annRenderPreEditorScreenshotPreview", app_js)
        self.assertIn("annotationState.preEditorScreenshotB64 = b64 || '';", app_js)
        self.assertIn("shotPreviewImg.src = 'data:image/png;base64,' + screenshotB64;", app_js)
        self.assertIn(".ann-editor-shot-preview", app_css)
        self.assertIn(".ann-editor-shot-preview img", app_css)

    def test_annotation_ux_preview_accepts_pasted_images(self):
        """The editable UX-queue preview should share composer image paste."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        preview_start = app_js.index("function annShowUxFixesPreview(ann, onSubmit)")
        preview_body = app_js[preview_start:app_js.index("async function annOpenUxFixesQueue", preview_start)]
        self.assertIn("try { if (typeof attachImagePaste === 'function') attachImagePaste(textArea); } catch (_) {}", preview_body)
        self.assertIn("function _pastedImageHost(el)", app_js)
        self.assertIn(".ann-editor, .ann-screen-dialog, .ann-ux-preview-card", app_js)

    def test_ux_fixes_queue_progress_badge_is_rendered_from_queue_api(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("/api/ux-fixes/list", app_js)
        self.assertIn("claimed_by", app_js)
        self.assertIn("conv-ux-fix-progress", app_js)
        self.assertIn("function _uxFixesWorkerProjectForRow(c)", app_js)
        self.assertIn("activeByProject.set(project,", app_js)
        self.assertIn("const projectHint = _uxFixesWorkerProjectForRow(c);", app_js)
        self.assertIn("uxFixesQueueMeta.activeByProject || new Map()", app_js)
        self.assertIn("uxFixesQueueMeta.lastFixByProject || new Map()", app_js)
        self.assertIn(".conv-item .conv-ux-fix-progress", app_css)

    def test_compute_queues_health_marks_configured_empty_queues(self):
        """Configured queues with zero tickets are intentional and should be
        distinguishable from stale historical queues in the CCC Queue tab."""
        server = importlib.import_module("server")

        with mock.patch.object(server, "_wt_read_config", return_value={
            "BYM-GH-FINIE": {
                "backend": "github",
                "repo_path": "/Users/amirfish/Apps/BYM+Finie",
                "auto_drain": False,
            },
        }), mock.patch.object(server._q, "list_items", return_value=[]):
            rows = {r["queue"]: r for r in server.compute_queues_health([], [])}

        row = rows["BYM-GH-FINIE"]
        self.assertTrue(row["configured"])
        self.assertEqual(row["total"], 0)
        self.assertIsNone(row["last_activity_seconds"])
        self.assertEqual(row["repo_path"], "/Users/amirfish/Apps/BYM+Finie")

    def test_compute_queues_health_respects_watchtower_claimable_flag(self):
        """GitHub-backed WT queues list all open issues, but only runnable
        issues should count as claimable/stuck work in CCC."""
        server = importlib.import_module("server")

        with mock.patch.object(server, "_wt_read_config", return_value={
            "BYM-GH-FINIE": {
                "backend": "github",
                "repo_path": "/Users/amirfish/Apps/BYM+Finie",
                "auto_drain": True,
            },
        }), mock.patch.object(server._q, "list_items", return_value=[{
            "project": "BYM-GH-FINIE",
            "ref": "BYM-GH-FINIE-402",
            "status": "open",
            "type": "bug",
            "claimable": False,
            "watchtower_runnable": False,
            "created_at": "2026-07-01T12:00:00Z",
            "updated_at": "2026-07-01T12:00:00Z",
        }]):
            rows = {r["queue"]: r for r in server.compute_queues_health([
                {
                    "project": "BYM-GH-FINIE",
                    "depth": 1,
                    "oldest_open_age_seconds": 60,
                    "stuck": True,
                }
            ], [])}

        row = rows["BYM-GH-FINIE"]
        self.assertEqual(row["depth"], 1)
        self.assertEqual(row["claimable"], 0)
        self.assertFalse(row["stuck"])
        self.assertEqual(row["state"], "backlog")

    def test_queue_panel_has_run_action_for_unrunnable_github_issues(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("watchtower_runnable", app_js)
        self.assertIn("fq-run", app_js)
        self.assertIn("/api/ux-fixes/run", app_js)
        self.assertIn('if path == "/api/ux-fixes/run":', server_py)
        self.assertIn('getattr(_q, "mark_runnable", None)', server_py)

    def test_dashboard_pollers_skip_overlapping_async_ticks(self):
        """Periodic dashboard pollers must not start a second async fetch while
        the previous tick is still in flight."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("const _pollerInflight = {};", app_js)
        self.assertIn("if (_pollerInflight[name]) return _pollerInflight[name];", app_js)
        self.assertIn("delete _pollerInflight[name];", app_js)

    def test_queue_health_strip_keeps_configured_never_active_queues(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        strip = app_js[
            app_js.index("async function _renderQueueHealthStrip"):
            app_js.index("const proj = scopeProject !== undefined ? _uxqProjectKey(scopeProject) : _uxqWorkerProject();", app_js.index("async function _renderQueueHealthStrip"))
        ]
        self.assertIn("const configured = !!q.configured;", strip)
        self.assertIn("if (!configured && (la == null || la > _ACTIVE_WINDOW_S)) return;", strip)

    def test_project_worker_progress_prefers_latest_project_fix(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        fn_start = app_js.index("function _uxFixesQueueProgressForRow(c)")
        fn_end = app_js.index("function _uxFixesQueueProgressHtml(c)", fn_start)
        fn_body = app_js[fn_start:fn_end]
        project_done_pos = fn_body.index("const projectLastFix = uxFixesQueueMeta.lastFixByProject || new Map();")
        session_done_pos = fn_body.index("const lastFix = uxFixesQueueMeta.lastFixBySession || new Map();")
        self.assertLess(project_done_pos, session_done_pos)

    def test_queue_panel_falls_back_from_empty_repo_project_scope(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function _uxqResolvePanelProject(items, requestedProject)", app_js)
        self.assertIn("const fallback = _uxqDominantOpenProject(items);", app_js)
        self.assertIn("const proj = _uxqResolvePanelProject(items, requestedProject);", app_js)
        self.assertIn("_uxqLastResolvedProject = proj;", app_js)
        self.assertIn("const proj = _uxqLastResolvedProject || _uxqWorkerProject();", app_js)

    def test_queue_scope_normalizes_cc_alias_to_ccc(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        key_start = app_js.index("function _uxqProjectKey(value)")
        key_body = app_js[key_start:app_js.index("// True when item project", key_start)]
        self.assertIn("const _UXQ_PROJECT_ALIASES = { CC: 'CCC' };", app_js)
        self.assertIn("return _UXQ_PROJECT_ALIASES[key] || key;", key_body)

    def test_queue_scope_key_survives_missing_open_row(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        key_start = app_js.index("function _uxqScopeKey()")
        key_body = app_js[key_start:app_js.index("function _uxqLoadScopeMap()", key_start)]
        self.assertIn("const rowKey = r && (r.id || r.session_id);", key_body)
        self.assertIn("if (rowKey) return rowKey;", key_body)
        self.assertIn("if (typeof currentConversation !== 'undefined' && currentConversation) return currentConversation;", key_body)
        self.assertIn("return '__queue_global__';", key_body)

    def test_queue_scope_switch_repaints_from_completed_caches(self):
        """Changing a client-side scope must not wait for multi-MB refetches."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        items_fetch = app_js[
            app_js.index("async function _fetchUxqItems"):
            app_js.index("// Per-project queue-health snapshot", app_js.index("async function _fetchUxqItems"))
        ]
        health_fetch = app_js[
            app_js.index("async function _fetchUxqHealth"):
            app_js.index("// Live WatchTower workers", app_js.index("async function _fetchUxqHealth"))
        ]
        scope_handler = app_js[
            app_js.index("// Queue scope picker:"):
            app_js.index("const $queueAdd", app_js.index("// Queue scope picker:"))
        ]

        self.assertIn("async function _fetchUxqItems(allowStale)", items_fetch)
        self.assertIn("allowStale && _uxqItemsCache.ts", items_fetch)
        self.assertIn("async function _fetchUxqHealth(allowStale)", health_fetch)
        self.assertIn("allowStale && _uxqHealthCache.ts", health_fetch)
        self.assertIn("_renderQueuePanel({ allowStale: true });", scope_handler)
        self.assertNotIn("_uxqItemsCache.ts = 0;", scope_handler)
        self.assertNotIn("_uxqHealthCache.ts = 0;", scope_handler)

    def test_ux_fixes_worker_ids_with_numeric_suffix_are_plausible(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        fn_start = app_js.index("function _uxFixesPlausibleSessionId(value)")
        fn_end = app_js.index("function _uxFixesRowIdentityKeys(c)", fn_start)
        fn_body = app_js[fn_start:fn_end]
        self.assertIn("^[A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*-\\d+$", fn_body)
        self.assertNotIn("^[A-Za-z][A-Za-z0-9]*(-[A-Za-z0-9]+)*-\\d+$", fn_body)

    def test_sidebar_refresh_defers_while_dragging(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function deferSidebarRenderIfDragging", app_js)
        self.assertIn(".flow-node.dragging", app_js)
        self.assertIn(".flow-board.is-zooming", app_js)
        self.assertIn("beginSidebarDrag();", app_js)
        self.assertIn("function markFlowZoomInteraction", app_js)
        self.assertIn("markFlowZoomInteraction(targetEl);", app_js)
        self.assertIn("markFlowZoomInteraction(ev.currentTarget);", app_js)
        self.assertIn("if (deferSidebarRenderIfDragging()) return;", app_js)

    def test_flow_new_session_drafts_wait_for_play(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("ccc-flow-draft-sessions", app_js)
        self.assertIn("function createFlowDraftSession", app_js)
        self.assertIn("function playFlowDraftSession", app_js)
        self.assertIn("function flowRepoPathForNode", app_js)
        self.assertIn("ccc-flow-collapsed-nodes", app_js)
        self.assertIn("function toggleFlowNodeCollapsed", app_js)
        self.assertIn("function setFlowAllNodesCollapsed", app_js)
        self.assertIn("function ensureFlowDefaultRepoCollapsed", app_js)
        self.assertIn("function carryFlowPendingSpawnNode", app_js)
        self.assertIn("function flowParentForCollapse", app_js)
        self.assertIn("function flowHasAncestorNode", app_js)
        self.assertIn("function flowSessionSignal", app_js)
        self.assertIn("function archiveFlowSession", app_js)
        self.assertIn("let flowSelectedNodes", app_js)
        self.assertIn("function startFlowRangeSelection", app_js)
        self.assertIn("FLOW_CANVAS_PAD_RATIO = 0.30", app_js)
        self.assertIn("FLOW_CANVAS_PAD_MIN_PX = 260", app_js)
        self.assertIn("function flowCanvasPadding", app_js)
        self.assertIn("data-flow-pad-x", app_js)
        self.assertIn("flowCanvasPaddingFromCanvas", app_js)
        self.assertIn("canvas.addEventListener('pointerdown'", app_js)
        self.assertIn("flowSelectedNodeIds", app_js)
        self.assertIn("const isGroupDrag = dragItems.length > 1;", app_js)
        self.assertIn("data-flow-action=\"play-draft-session\"", app_js)
        self.assertIn("data-flow-action=\"archive-session\"", app_js)
        self.assertIn("data-flow-action=\"toggle-collapse\"", app_js)
        self.assertIn("data-flow-action=\"collapse-all\"", app_js)
        self.assertIn("flowRecencyButtonHtml('1d', '1d'", app_js)
        self.assertIn("flowHasCollapsedAncestor(nodeId, repoId)", app_js)
        self.assertIn("function flowIsVisibleSession", app_js)
        self.assertIn("if (col === 'backlog') return false;", app_js)
        self.assertIn("if (col === 'archived' && !flowIncludeArchived && !pinnedInFlow) return false;", app_js)
        self.assertIn("flow_parent_node_id", app_js)
        self.assertIn("return ts ? relativeTime(ts) : '';", app_js)
        self.assertIn("if (value === 'flow') return FLOW_POPOUT_MODE ? 'flow' : 'list';", app_js)
        self.assertIn("if (value === 'board' || value === 'kanban') return 'board';", app_js)
        self.assertIn("return localStorage.getItem('ccc-kanban-view') === 'true' ? 'board' : 'list';", app_js)
        self.assertIn("New session draft connected here", app_js)
        self.assertIn("if (isFlowView()) createFlowDraftSession();", app_js)
        self.assertIn(".flow-node-archive", app_css)
        self.assertIn(".flow-selection-box", app_css)
        self.assertIn(".flow-node.selected", app_css)
        self.assertIn("--flow-grid-size", app_css)
        self.assertIn("background-position:", app_css)

    def test_flow_work_item_inspector_wired(self):
        """Repo/object Flow nodes open a Markdown-backed work-item inspector,
        and work-item cards use automatic accents plus parsed Flow fields."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("FLOW_STATE_DIR", server_py)
        self.assertIn("/api/flow/node", server_py)
        self.assertIn("/api/flow/node/refresh", server_py)
        self.assertIn("/api/flow/index", server_py)
        self.assertIn("function openFlowNodeInspector", app_js)
        self.assertIn("flowInspectorPayloadFromNode", app_js)
        self.assertIn("flowInspectorRefresh", app_js)
        self.assertIn("data-flow-inspector-action=\"refresh\"", app_js)
        self.assertIn("flow-node-work-item", app_js)
        self.assertIn("flowAccentStyle", app_js)
        self.assertIn("flowWorkItemCardHtml", app_js)
        self.assertIn("accentSeed: flowColorSeedForNode(nodeId, obj.id)", app_js)
        self.assertIn("function _isAbsoluteLocalPath", app_js)
        self.assertIn("[row.repo_path, row.folder_path, row.spawn_cwd, row.session_cwd, row.cwd]", app_js)
        self.assertIn("absolute || row.repo_path || row.spawn_cwd || row.session_cwd || row.cwd || row.folder_path", app_js)
        self.assertIn("function flowIndexedRepoEntries", app_js)
        self.assertIn("indexedRepoEntries.forEach(entry =>", app_js)
        self.assertIn("ensureRepoGroup(entry.repo_path).metaEntry = entry;", app_js)
        self.assertIn(".flow-node-work-item", app_css)
        self.assertIn(".flow-inspector", app_css)
        self.assertIn("--flow-accent", app_css)

    def test_by_objects_draft_task_opens_flow_details(self):
        """Draft task rows in By objects open the same details pane as Flow
        nodes, including clicks that land on the inline task input."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function flowDraftInspectorPayload(draftId)", app_js)
        self.assertIn("kind: 'draft-session'", app_js)
        self.assertIn("draft_id: draft.id", app_js)
        self.assertIn("title: draft.title || flowDraftPrompt(draft) || 'Draft session'", app_js)
        self.assertIn("content: flowDraftPrompt(draft)", app_js)
        self.assertIn("if (!draft.prompt || draft.prompt === prevTitle)", app_js)
        self.assertIn("localDraft.prompt = sd.prompt;", app_js)
        self.assertIn("function saveFlowDraftInspector(root, opts)", app_js)
        self.assertIn("data-flow-inspector-action=\"draft-save\"", app_js)
        self.assertIn("data-flow-inspector-action=\"draft-play\"", app_js)
        self.assertIn("draft.prompt = editor.value || '';", app_js)
        self.assertIn("$convList.querySelectorAll('.conv-draft-row[data-draft-id]').forEach(row =>", app_js)
        self.assertIn("openFlowDraftInspector(row.getAttribute('data-draft-id') || '');", app_js)
        self.assertIn("openFlowDraftInspector(inp.getAttribute('data-draft-id') || '');", app_js)
        self.assertIn("if (ev.target.closest('button')) return;", app_js)
        self.assertIn(".conv-project-tree .conv-draft-row[data-draft-id]", app_css)
        self.assertIn("cursor: pointer;", app_css)

    def test_by_objects_uses_server_draft_parent_fallback(self):
        """API-created draft sessions with only parent_node_id should still
        appear under their object in By objects."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function flowDraftParentNode(draft)", app_js)
        self.assertIn("const serverDraftParent = sd.parent_node_id || '';", app_js)
        self.assertIn("if (serverDraftParent && !flowNodeParents[serverDraftNode])", app_js)
        self.assertIn("(flowDraftSessions || []).some(d => d && flowDraftParentNode(d) === node)", app_js)
        self.assertIn("? (flowDraftSessions || []).filter(d => d && flowDraftParentNode(d) === nodeId)", app_js)

    def test_object_reconcile_only_deletes_tombstoned_drafts(self):
        """Open tabs must not delete server-created drafts they have not seen."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const FLOW_DRAFT_DELETED_KEY = 'ccc-flow-draft-deleted-ids';", app_js)
        self.assertIn("function rememberDeletedFlowDraftSession(id)", app_js)
        self.assertIn("rememberDeletedFlowDraftSession(id);", app_js)
        self.assertIn("const deletedDraftIds = loadDeletedFlowDraftSessionIds();", app_js)
        self.assertIn("deletedDraftIds.has(sd.id)", app_js)
        self.assertNotIn("!localDraftIds.has(sd.id)", app_js)
        self.assertIn("clearDeletedFlowDraftSessionIds(clearedDraftIds);", app_js)
        self.assertIn("mergeServerDraftSessions(server.drafts || [], deletedDraftIds)", app_js)

    def test_flow_object_refresh_reads_parent_map_sessions(self):
        """Refreshing an object inspector should rebuild auto sections from
        Flow's source-of-truth parent map, not only from rendered DOM nodes."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function flowInspectorNodeDescendantIds(rootNodeId)", app_js)
        self.assertIn("const descendantIds = flowInspectorNodeDescendantIds(nodeId);", app_js)
        self.assertIn("descendantIds.has(flowNodeKey('session', row.session_id || row.id || ''))", app_js)
        self.assertIn("descendantIds.has(flowNodeKey('draft-session', draft.id || ''))", app_js)
        self.assertIn("descendantIds.has(flowNodeKey('object', obj.id || ''))", app_js)

    def test_flow_state_helpers_create_save_refresh_markdown(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as td:
            old_dir = server.FLOW_STATE_DIR
            old_index = server.FLOW_INDEX_FILE
            server.FLOW_STATE_DIR = pathlib.Path(td) / "flow"
            server.FLOW_INDEX_FILE = server.FLOW_STATE_DIR / "index.json"
            try:
                payload = {"kind": "object", "object_id": "obj-test", "title": "Release work"}
                result, status = server._flow_load_node_payload(payload, create=True)
                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                self.assertIn("## Flow fields", result["content"])
                self.assertIn("ccc:auto:start status-table", result["content"])
                edited = result["content"].replace(
                    "Write the current state here.",
                    "Manual summary survives refresh.",
                )
                saved, status = server._flow_save_node_payload({
                    **payload,
                    "content": edited,
                    "mtime": result["mtime"],
                })
                self.assertEqual(status, 200)
                refreshed, status = server._flow_refresh_node_payload({
                    **payload,
                    "items": [{
                        "title": "Fix layout",
                        "status": "working",
                        "session": "abc12345",
                        "updated": "just now",
                        "notes": "main",
                    }],
                })
                self.assertEqual(status, 200)
                self.assertIn("Manual summary survives refresh.", refreshed["content"])
                self.assertIn("Fix layout", refreshed["content"])
                self.assertIn("abc12345", refreshed["content"])
                index = server._flow_index_payload()
                self.assertEqual(index["count"], 1)
                self.assertEqual(index["entries"][0]["fields"]["status"], "Active")
            finally:
                server.FLOW_STATE_DIR = old_dir
                server.FLOW_INDEX_FILE = old_index

    def test_mobile_breakpoint_covers_phones_landscape(self):
        """Mobile single-column layout (conv list full-width, conv pane
        slides in as overlay, back button shows) must trigger on phones
        in BOTH portrait and landscape. iPhone Pro Max landscape is
        932px so the breakpoint must be ≥ 932; 950px gives a small
        safety margin. JS _mobileMQ and the relevant CSS @media blocks
        must use the same threshold so isMobile() and the slide-in
        overlay agree."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("matchMedia('(max-width: 1200px)')", app_js)
        # CSS for the back-button visibility + main-overlay must match.
        self.assertIn("@media (max-width: 1200px)", app_css)
        self.assertIn('id="mobileBackBtn"', index_html)
        self.assertNotIn('data-role="pane-mobile-back"', index_html)
        self.assertNotIn("mobile-show-main .conv-split[data-orientation=\"\"] .conv-pane > .conv-pane-header", app_css)
        self.assertNotIn("_captureRailEl(document.getElementById('mobileBackBtn'))", app_js)

    def test_mobile_back_button_stays_in_stable_toolbar(self):
        """Dynamic task-tab rendering must never own the only mobile exit."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(
            '<div class="toolbar" id="convToolbar">\n'
            '      <button class="mobile-back-btn" id="mobileBackBtn"',
            index_html,
        )
        self.assertNotIn("syncMobileBackIntoTabStrip", app_js)
        self.assertNotIn("insertBefore($mobileBackBtn", app_js)
        self.assertNotIn(".conv-tab-strip.has-mobile-back", app_css)
        self.assertIn("#convToolbar .font-size-controls { display: none !important; }", app_css)
        self.assertIn("order: -100;", app_css)

    def test_mobile_reload_fab_is_not_rendered(self):
        """Mobile should not render the old floating page-reload button."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertNotIn('id="mobileReloadBtn"', index_html)
        self.assertNotIn("mobileReloadBtn", app_js)
        self.assertNotIn(".mobile-reload-btn", app_css)
        self.assertIn('id="kanbanReloadBtn"', index_html)

    def test_flow_group_chat_nodes_and_drop(self):
        """Group chats render as a third node kind on the flow board
        (alongside repo and object), have a "+ Group chat" toolbar
        button that triggers createEmptyGroupChat, click opens the
        existing group-chat reader, and dropping a session node onto
        a group-chat node calls addSessionToGroupChat — same outcome
        as dragging a conv-list row onto a chat row in the sidebar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-flow-action="add-group-chat"', app_js)
        self.assertIn("createEmptyGroupChat()", app_js)
        self.assertIn("flow-node-group-chat", app_js)
        # Records carry gc-path / gc-id / gc-mode for the drop handler.
        self.assertIn("groupChatPath", app_js)
        self.assertIn("data-gc-path", app_js)
        # Drop handler: session → group-chat node calls
        # addSessionToGroupChat instead of the parent-link operation.
        self.assertIn("targetIsGroupChat", app_js)
        self.assertIn("addSessionToGroupChat(gcPath, sid, displayName, gcId)", app_js)
        # Click on a group-chat node opens the reader.
        self.assertIn("openGroupChatReader(gcPath, topic, gcMode", app_js)
        # CSS for the distinct accent.
        self.assertIn(".flow-node-group-chat", app_css)

    def test_flow_popout_reader_toggle(self):
        """Flow popout has a button to show/hide a conversation reader
        on the right side. Toggling writes ccc-flow-popout-reader to
        localStorage; the body class flow-popout-reader splits the
        viewport into flow-left + conv-pane-right via CSS."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-flow-action="toggle-reader"', app_js)
        self.assertIn("flowPopoutReaderEnabled", app_js)
        self.assertIn("ccc-flow-popout-reader", app_js)
        self.assertIn("body.flow-popout.flow-popout-reader", app_css)

    def test_sidebar_search_hides_group_chat_rows(self):
        """Sidebar search is for sessions/issues. Active and archived
        group-chat rows are navigation chrome, so they should not appear
        in In progress or Archived while a query is active."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("const _hideGroupChatsForSearch = !!_qActive;", app_js)
        self.assertIn(
            "const _visibleGroupChats = _hideGroupChatsForSearch ? [] : (_gcActiveChats || []).filter(chat => {",
            app_js,
        )
        self.assertIn("const _gcItems = _visibleGroupChats.map(chat => {", app_js)
        self.assertIn("const _gcCountForSection = _hideGroupChatsForSearch ? 0", app_js)
        self.assertIn("const _archivedGroupChatsForRender = _hideGroupChatsForSearch", app_js)
        self.assertIn("const hasGc = !q && _gcActiveChats && _gcActiveChats.length > 0;", app_js)

    def test_group_chats_respect_inprogress_window_filter(self):
        """Group chats should disappear from Active rows under 1d/7d just like
        sessions, while still contributing to the hidden-count footer."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const _groupChatWindowTs = (chat) => {", app_js)
        self.assertIn("const _visibleGroupChats = _hideGroupChatsForSearch ? [] : (_gcActiveChats || []).filter(chat => {", app_js)
        self.assertIn("if (!_ipWindowCutoff) return true;", app_js)
        self.assertIn("return _groupChatWindowTs(chat) >= _ipWindowCutoff;", app_js)
        self.assertIn("const _gcItems = _visibleGroupChats.map(chat => {", app_js)
        self.assertIn("const _hiddenGroupChatCount = _hideGroupChatsForSearch", app_js)
        self.assertIn("+ _hiddenGroupChatCount;", app_js)
        self.assertNotIn("const _gcItems = _hideGroupChatsForSearch ? [] : (_gcActiveChats || []).map(chat => {", app_js)

    def test_flow_popout_button_and_mode_wired(self):
        """Flow toolbar gets a pop-out button (skipped inside the popout
        itself). Click → openFlowPopout → window.open with ccc_popout=flow.
        Body class + popout-only mode routing send the popped-out tab
        straight into the Flow view without polluting main-window storage."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Boot flag + body class.
        self.assertIn("FLOW_POPOUT_MODE", app_js)
        self.assertIn("'ccc_popout') === 'flow'", app_js)
        self.assertIn("document.body.classList.add('flow-popout')", app_js)
        self.assertIn("if (FLOW_POPOUT_MODE) return 'flow';", app_js)
        self.assertNotIn("localStorage.setItem('ccc-session-view', 'flow')", app_js)
        self.assertIn("localStorage.setItem('ccc-session-view', sidebarViewMode === 'flow' ? 'list' : sidebarViewMode)", app_js)
        # Helper + URL shape.
        self.assertIn("function openFlowPopout", app_js)
        self.assertIn("let _flowPopoutWindow = null;", app_js)
        self.assertIn("function focusFlowPopoutWindow", app_js)
        self.assertIn("if (focusFlowPopoutWindow()) return true;", app_js)
        self.assertIn("_flowPopoutWindow = popup;", app_js)
        self.assertIn("u.searchParams.set('ccc_popout', 'flow')", app_js)
        self.assertIn("u.searchParams.set('title', 'Flow')", app_js)
        # Toolbar button + click wiring.
        self.assertIn('data-flow-action="popout"', app_js)
        self.assertIn('openFlowPopout(null)', app_js)
        # CSS gates the popout layout.
        self.assertIn("body.flow-popout", app_css)
        self.assertIn(".conv-list-panel > *:not(#flowBoard)", app_css)

    def test_tool_results_attach_to_matching_tool_call(self):
        """Tool result previews should render under the command/tool whose
        tool_use id matches, with a visible result/error label."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function toolCallForResult", app_js)
        self.assertIn("tc.dataset.toolUseId || '') === toolUseId", app_js)
        self.assertIn("data-tool-use-id=\"' + escapeAttr(toolUseId) + '\"", app_js)
        self.assertIn("const last = toolCallForResult(_currentToolGroup, ev.tool_use_id || '');", app_js)
        self.assertIn("out.dataset.resultLabel = ", app_js)
        self.assertIn("toolResultOutputLabel(last, ", app_js)
        self.assertIn("Command result", app_js)
        self.assertIn("⚠ Command failed", app_js)
        self.assertIn(".tool-result-output::before", app_css)
        self.assertIn("content: attr(data-result-label);", app_css)

    def test_tool_calls_render_complete_input_disclosure(self):
        """Persisted tool payloads should load lazily behind an Input toggle."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function renderToolInputDisclosure(block, conversationId, line)", app_js)
        self.assertIn("async function loadToolInputDisclosure(details)", app_js)
        self.assertIn("<span>Input</span>", app_js)
        self.assertIn("/tool-input?line=", app_js)
        self.assertIn("tool_use_id=", app_js)
        self.assertIn("const inputDisclosure = renderToolInputDisclosure(b,", app_js)
        self.assertIn("+ inputDisclosure", app_js)
        self.assertNotIn("escapeHtml(block.input)", app_js)

    def test_recoverable_tool_failures_use_a_subdued_warning_treatment(self):
        """A failed tool call should not look like a CCC or session failure."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("⚠ Command failed", app_js)
        self.assertIn(".tool-result-output.is-error { border-left-color: var(--orange); color: var(--orange); }", app_css)
        self.assertIn(".tool-result-output.is-error::before { color: var(--orange); }", app_css)
        self.assertIn("const shouldRenderResult = !!text || _isErr;", app_js)
        self.assertIn("No error details returned.", app_js)

    def test_organize_is_incremental_with_overlap_resolve(self):
        """Per user request: Organize must keep repos/objects where they
        are, only moving them when absolutely needed to avoid overlap,
        and the total pixel displacement should be minimized. Strategy:
        anchor each chain at its root's current position, then
        greedy-resolve overlaps by pushing the less-displaced chain by
        the minimum right/down amount. Untouched chains seed from the
        legacy bin-pack cursor."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # New R10 rule documented in the algorithm comment block.
        self.assertIn("R10. INCREMENTAL", app_js)
        # Per-cluster anchoring — every cluster (root AND nested) starts
        # at its own parent's current offsetLeft/offsetTop, not a
        # chain-relative derived offset.
        self.assertIn("INCREMENTAL ORGANIZE, per-cluster", app_js)
        self.assertIn("parentNode.offsetLeft", app_js)
        self.assertIn("parentNode.offsetTop", app_js)
        self.assertIn("clusterPlacements", app_js)
        # Unplaced nested clusters seed BELOW the ancestor (not right of
        # it) per the 2026-06-05 layout rule: nested objects/repos stack
        # vertically under their parent, not horizontally.
        self.assertIn("ancPlace.y + ancPlace.h + CLUSTER_MARGIN", app_js)
        # Overlap-resolve picks the worst overlap each iteration and
        # pushes the cluster with smaller displacement.
        self.assertIn("worstArea", app_js)
        self.assertIn("totalPushPx", app_js)
        self.assertIn("aDisp <= bDisp", app_js)

    def test_flow_record_mode_and_organize_plus_wired(self):
        """Flow has a Record mode that stores before/after layout examples
        and an Organize+ button that replays the best matching example."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("FLOW_ORGANIZE_PLUS_KEY = 'ccc-flow-organize-plus-examples'", app_js)
        self.assertIn("function flowSnapshotNodePositions", app_js)
        self.assertIn("function toggleFlowOrganizeRecord", app_js)
        self.assertIn("function applyFlowOrganizePlus", app_js)
        self.assertIn('data-flow-action="record-organize"', app_js)
        self.assertIn('data-flow-action="organize-plus"', app_js)
        self.assertIn("flowOrganizePlusExamples.unshift(example)", app_js)
        self.assertIn("organizeFlowSessions(targetEl, { silent: true });", app_js)
        self.assertIn("updateFlowOrganizeRecordState(targetEl);", app_js)
        self.assertIn(".flow-toolbar-btn.is-recording", app_css)

    def test_inline_rename_force_renders_even_when_search_focused(self):
        """Inline session rename commit() must force the sidebar render —
        the rename input itself is a text input, and after Enter/blur
        focus is either on it or has moved to the search box (also
        text). Either case trips shouldPauseSidebarRender, which would
        suppress the post-commit render and leave the title stuck in
        edit mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # The rename commit's renderSidebar call must pass force:true.
        self.assertIn(
            "renderSidebar(filterConversations($convSearch.value), { force: true });",
            app_js,
        )
        # And the surrounding function should still be the inline-rename
        # commit (so we can be sure the right call site got forced).
        self.assertIn("function startInlineRename", app_js)
        # Defensive: the inline-rename comment mentions the pause-guard
        # rationale so a future refactor can't silently drop the force.
        self.assertIn("trips shouldPauseSidebarRender", app_js)

    def test_flow_edges_are_selectable_deletable_draggable(self):
        """Flow edges (the lines connecting child nodes to their parent)
        are now selectable with a click, deletable with Backspace, and
        draggable from one parent to another. Each edge renders as a
        <g class="flow-edge"> containing a wide transparent hit path
        plus a thin visible line."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Edge selection state + DOM contract.
        self.assertIn("_selectedFlowEdgeChildId", app_js)
        self.assertIn("function selectFlowEdge", app_js)
        self.assertIn("function clearFlowEdgeSelection", app_js)
        self.assertIn("'flow-edge'", app_js)
        self.assertIn("'flow-edge-hit'", app_js)
        self.assertIn("'flow-edge-line'", app_js)
        # Backspace handler + delete helper.
        self.assertIn("function deleteFlowEdge", app_js)
        self.assertIn("ev.key === 'Backspace'", app_js)
        # Drag-to-reparent.
        self.assertIn("function startEdgeReparentDrag", app_js)
        self.assertIn("function reparentFlowNode", app_js)
        self.assertIn("is-drop-target", app_js)
        # CSS for the hit-area, selected state, drag ghost, drop target.
        self.assertIn(".flow-edge-hit", app_css)
        self.assertIn(".flow-edge.is-selected", app_css)
        self.assertIn(".flow-edge-line.is-dragging", app_css)
        self.assertIn(".flow-node.is-drop-target", app_css)

    def test_mermaid_code_blocks_render_as_svg(self):
        """```mermaid fenced blocks render as SVG instead of raw code.
        renderCodeBlock emits a .mermaid-block carrier whose .mermaid-source
        pre is the offline fallback; a lazy lib loader replaces the
        block with rendered SVG on first appearance. Hooked into the
        existing conv-view MutationObserver so every render path picks
        it up for free."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Detection inside renderCodeBlock
        self.assertIn("(lang || '').toLowerCase() === 'mermaid'", app_js)
        self.assertIn('"mermaid-block"', app_js.replace("'", '"'))
        # Lazy loader + render helper
        self.assertIn("_loadMermaid", app_js)
        self.assertIn("_renderMermaidBlocks", app_js)
        self.assertIn("cdn.jsdelivr.net/npm/mermaid", app_js)
        # Hooked into the existing observer that already does RTL tagging.
        self.assertIn("_renderMermaidBlocks(n)", app_js)
        # CSS for the carrier + SVG container.
        self.assertIn(".mermaid-block", app_css)
        self.assertIn(".mermaid-svg", app_css)

    def test_tts_rate_knob_is_live_and_persisted(self):
        """User wanted a live knob that adjusts TTS speed while it's
        playing, plus a persisted default. The rate is no longer
        baked-in to 1.25 — it's read from localStorage at init,
        controlled by a range input next to the TTS button, and the
        change cancels + re-speaks from the current word boundary so
        the new rate kicks in within ~180ms instead of waiting for
        the next turn."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        # Markup
        self.assertIn('id="convTtsRateControl"', index_html)
        self.assertIn('id="convTtsRateDown"', index_html)
        self.assertIn('id="convTtsRateUp"', index_html)
        # State + persistence
        self.assertIn("let _ttsRate", app_js)
        self.assertIn("ccc-tts-rate", app_js)
        self.assertNotIn("const _TTS_RATE =", app_js)
        # Live restart wiring — click listener + restart helper.
        self.assertIn("_restartTtsAtCurrentPosition", app_js)
        self.assertIn("addEventListener('click'", app_js)
        # CSS
        self.assertIn(".tts-rate-control", app_css)

    def test_tts_last_message_skips_queued_user_echoes(self):
        """The main Speak button should not read optimistic queued user sends
        instead of the latest agent reply."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        last_msg = app_js[
            app_js.index("function lastMessageTtsData(paneId)"):
            app_js.index("  // TTS playback rate", app_js.index("function lastMessageTtsData(paneId)"))
        ]

        self.assertIn("function isPendingSendEchoElement(el)", app_js)
        self.assertIn("el.classList.contains('send-queued')", app_js)
        self.assertIn("el.classList.contains('send-delivered')", app_js)
        self.assertIn("el.classList.contains('not-acknowledged')", app_js)
        self.assertIn("if (isPendingSendEchoElement(el)) continue;", last_msg)

    def test_first_existing_dir_picks_first_real_path(self):
        """Codex / claude rows used to surface a tail-extracted worktree
        cwd that had since been deleted, so Launch built
        `cd '/.../no-such-worktree' && resume` and dropped the user in
        their home dir. _first_existing_dir prefers the first cwd
        candidate that still exists on disk."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
        self.assertTrue(hasattr(srv, "_first_existing_dir"))
        with tempfile.TemporaryDirectory() as td:
            real = pathlib.Path(td, "real-repo")
            real.mkdir()
            missing = pathlib.Path(td, "deleted-worktree-sGH1nB")
            # missing intentionally never created
            self.assertEqual(srv._first_existing_dir(str(missing), str(real)), str(missing.parent / "real-repo"))
            # All missing → None.
            other_missing = pathlib.Path(td, "also-missing")
            self.assertIsNone(srv._first_existing_dir(str(missing), str(other_missing)))
            # Empty / None args skip cleanly.
            self.assertEqual(srv._first_existing_dir("", None, str(real)), str(real))

    def test_launch_falls_back_to_repo_when_cwd_missing(self):
        """buildResumeCommand used to emit `cd '/.../no-such-worktree' &&
        resume` for non-.claude/worktrees paths that don't exist on
        disk — `cd` fails, `&&` blocks the resume. Falls back to the
        session's repoPath, and drops the `cd` entirely if no fallback
        is known."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # Fallback to currentSession.repoPath is the documented escape.
        self.assertIn("currentSession.repoPath", app_js)
        # No-cd path returns the bare resumeCmd.
        self.assertIn("return resumeCmd;", app_js)

    def test_macapp_cmd_backtick_cycles_windows(self):
        """Cmd+` should switch between CCC windows (main ↔ flow popout ↔
        conv popout). Default Cmd+` works for AppKit apps with multiple
        windows, but WKWebView swallows the keystroke before AppKit
        sees it — so we surface explicit Window-menu items for forward
        and shift-reverse cycling, bound at the menu-bar level."""
        macapp = pathlib.Path(PROJECT_ROOT, "scripts", "macapp", "main.swift").read_text(encoding="utf-8")
        self.assertIn('cycleWindowsForward', macapp)
        self.assertIn('cycleWindowsReverse', macapp)
        self.assertIn('"Cycle Through Windows"', macapp)
        # Bound to Cmd+` and Cmd+Shift+` in the Window menu.
        self.assertIn('keyEquivalent: "`"', macapp)

    def test_macapp_first_launch_is_native_and_observable(self):
        """DMG first launch must not depend on Terminal automation.

        The app owns the bundled installer process, observes an early exit,
        and gives the user recovery actions backed by the actual process log.
        """
        macapp = pathlib.Path(
            PROJECT_ROOT, "scripts", "macapp", "main.swift"
        ).read_text(encoding="utf-8")
        self.assertNotIn("NSAppleScript", macapp)
        self.assertNotIn('tell application "Terminal"', macapp)
        self.assertNotIn("ccc-install-", macapp)
        self.assertIn(
            'proc.arguments = [installScript, "--from=dmg"]', macapp
        )
        self.assertIn('env["CCC_INSTALL_MODE"] = "app"', macapp)
        self.assertIn("process.terminationStatus", macapp)
        self.assertIn('alert.addButton(withTitle: "Retry")', macapp)
        self.assertIn('alert.addButton(withTitle: "Open Log")', macapp)

    def test_macapp_does_not_quit_when_last_window_closes(self):
        """Closing a conversation pop-out (or the main window momentarily)
        must NOT terminate the app — that kills the server we spawned
        and yanks every other open window. Mirrors Safari / Mail
        behavior: Cmd+Q is the explicit quit path; closing windows
        leaves the app running. Dock-click re-opens main."""
        macapp = pathlib.Path(PROJECT_ROOT, "scripts", "macapp", "main.swift").read_text(encoding="utf-8")
        # Must explicitly return false, not true. A bare "return true" in
        # this delegate method is the bug we just fixed.
        self.assertIn(
            "func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {",
            macapp,
        )
        self.assertNotRegex(
            macapp,
            r"applicationShouldTerminateAfterLastWindowClosed\(_ sender: NSApplication\) -> Bool \{\s*return true",
        )
        # Dock-click reopen must rebuild a window when the last one closed.
        self.assertIn(
            "func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool",
            macapp,
        )

    def test_macapp_main_window_can_shrink_into_mobile_layout(self):
        """CCC.app must allow the main dashboard to resize below the web
        UI's 1200px narrow breakpoint. Otherwise the native shell blocks
        the existing single-column layout before CSS/JS can activate it."""
        macapp = pathlib.Path(PROJECT_ROOT, "scripts", "macapp", "main.swift").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("matchMedia('(max-width: 1200px)')", app_js)
        self.assertIn("let CCC_MAIN_MIN_WIDTH: CGFloat = 420", macapp)
        self.assertIn("let CCC_MAIN_MIN_HEIGHT: CGFloat = 600", macapp)
        self.assertIn(
            "win.minSize = NSSize(width: CCC_MAIN_MIN_WIDTH, height: CCC_MAIN_MIN_HEIGHT)",
            macapp,
        )
        self.assertNotIn("win.minSize = NSSize(width: 900, height: 600)", macapp)

    def test_sending_sidebar_render_bypasses_textarea_pause_guard(self):
        """Hitting Send leaves focus in the conv input textarea, which
        normally pauses sidebar renders (so background pollers can't
        yank the list around mid-type). But the user's own send IS a
        user-initiated event and must paint the "Sending…" pill in the
        sidebar row immediately. markSessionSending/clearSessionSending
        therefore pass {force: true} to renderSidebar, and renderSidebar
        skips the periodic-pause guard when force is set."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("renderSidebar(filterConversations($convSearch.value), { force: true });", app_js)
        self.assertIn("function renderSidebar(convs, opts)", app_js)
        self.assertIn("if (!(opts && opts.force) && shouldPauseSidebarRender()) { _sidebarRenderPendingWhilePaused = true; return; }", app_js)

    def test_composer_uses_js_autosize_not_native_field_sizing(self):
        """The composer must avoid native field-sizing in WKWebView.

        WebKit can claim support but paint the native textarea internals as a
        gray rounded bar over the placeholder. Keep autosize in JS instead.
        """
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertNotIn("field-sizing: content", app_css)
        self.assertNotIn("_hasFieldSizing", app_js)
        self.assertIn("function _autosizeConvInput()", app_js)
        self.assertIn("$convInput.style.height = 'auto';", app_js)
        self.assertIn("$convInput.style.height = Math.min($convInput.scrollHeight, max) + 'px';", app_js)
        input_css = app_css[
            app_css.index(".conv-input-bar input,"):
            app_css.index("/* Keep composer sizing in JS.", app_css.index(".conv-input-bar input,"))
        ]
        self.assertIn("-webkit-appearance: none;", input_css)
        self.assertIn("appearance: none;", input_css)
        self.assertNotIn("background: transparent !important;", input_css)
        self.assertIn("background: var(--bg) !important;", input_css)
        self.assertIn("background-color: var(--bg) !important;", input_css)
        self.assertIn("background-image: none !important;", input_css)
        self.assertIn("box-shadow: none;", input_css)
        self.assertNotIn("$convInput.style.height = ($convInput.scrollHeight) + 'px';", app_js)

    def test_image_paste_value_hook_preserves_composer_autosize_hook(self):
        """Paste cleanup should not replace the composer auto-shrink setter.

        The composer installs an element-level value setter so programmatic
        clears shrink a previously tall prompt. The paste-image hook must wrap
        that existing setter instead of going straight back to the prototype.
        """
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const ownDesc = Object.getOwnPropertyDescriptor(el, 'value');", app_js)
        self.assertIn("const protoDesc = Object.getOwnPropertyDescriptor(proto, 'value');", app_js)
        self.assertIn("const desc = ownDesc || protoDesc;", app_js)

    def test_composer_file_drop_uses_managed_attachments(self):
        """Finder/browser drops must persist files before inserting a path token."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")

        self.assertIn("function attachFileDrop(el)", app_js)
        self.assertIn("ev.dataTransfer.files", app_js)
        self.assertIn("uploadManagedAttachment(file)", app_js)
        self.assertIn("/api/upload-attachment", app_js)
        self.assertIn('path == "/api/upload-attachment"', server_py)
        self.assertIn("100 * 1024 * 1024", server_py)
        self.assertIn("COMMAND_CENTER_ATTACHMENTS_DIR", server_py)

    def test_composer_textarea_hides_native_scrollbar_chrome(self):
        """The composer textarea should not show WebKit scrollbar thumbs.

        In the Mac app, a horizontal textarea scrollbar inherits
        --border from the conversation palette and appears as a large gray
        bar across the input box.
        """
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        textarea_css = app_css[
            app_css.index(".conv-input-bar textarea {\n    /* Single-row by default;"):
            app_css.index("/* Native-app typing on touch devices.", app_css.index(".conv-input-bar textarea {\n    /* Single-row by default;"))
        ]
        self.assertIn("overflow-x: hidden;", textarea_css)
        self.assertIn("scrollbar-width: none;", textarea_css)
        self.assertIn(".conv-input-bar textarea::-webkit-scrollbar", app_css)
        self.assertIn("display: none;", app_css[
            app_css.index(".conv-input-bar textarea::-webkit-scrollbar"):
            app_css.index("/* Native-app typing on touch devices.", app_css.index(".conv-input-bar textarea::-webkit-scrollbar"))
        ])

    def test_empty_composer_arrow_up_recalls_last_command(self):
        """ArrowUp in an empty composer should recall the last sent command."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const COMPOSER_HISTORY_KEY = 'ccc-composer-history';", app_js)
        self.assertIn("function rememberComposerCommand(text)", app_js)
        self.assertIn("function recallLastComposerCommand(input, ev)", app_js)
        self.assertIn("if (ev.key !== 'ArrowUp') return false;", app_js)
        self.assertIn("if ((input.value || '').trim()) return false;", app_js)
        self.assertIn("rememberComposerCommand(text);", app_js)
        self.assertIn("if (recallLastComposerCommand($convInput, e)) return;", app_js)
        self.assertIn("if (recallLastComposerCommand(input, ev)) return;", app_js)

    def test_conv_pct_badge_is_clickable_compact_shortcut(self):
        """The context-% badge on each conv row is a one-click shortcut to
        /compact. Click -> confirm -> run the engine-aware compact helper. The
        row-click handler must EXCLUDE the badge so clicking it doesn't
        also open the conversation."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="conv-pct-compact"', app_js)
        self.assertIn("conv-pct-badge is-actionable", app_js)
        # Badge must be in the row-click exclusion list so the row
        # itself doesn't open underneath the /compact confirm.
        self.assertIn("CONVERSATION_ROW_ACTION_SELECTOR", app_js)
        self.assertIn("'[data-role=\"conv-pct-compact\"]'", app_js)
        self.assertIn("conversationRowTapIsBlocked(ev && ev.target, opts)", app_js)
        # Confirm + POST shape — compact is a command operation, not a
        # generic text inject. Both Claude and Codex now route through
        # /api/session/compact (postCompactSession); Codex compaction runs via
        # the app-server thread/compact/start RPC, not a literal text inject.
        self.assertIn("window.confirm(msg)", app_js)
        self.assertIn("postRunCompactForSession(sid, source)", app_js)
        self.assertIn("postCompactSession", app_js)
        self.assertIn("/api/session/compact", app_js)
        self.assertIn(".conv-pct-badge.is-actionable", app_css)

    def test_conversation_row_quality_badge_precedes_context_percent(self):
        """Rows with Token Optimizer quality data should show Q/C before the
        context percent badge."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function _convRowQualityBadge(c)", app_js)
        self.assertIn("quality_score: c.quality_score", app_js)
        self.assertIn("const qcBadgeHtml = pctBadgeHtml ? _convRowQualityBadge(c) : '';", app_js)
        row_quality = app_js[
            app_js.index("function _convRowQualityBadge(c)"):
            app_js.index("function _formatTokenOptimizerQuality", app_js.index("function _convRowQualityBadge(c)"))
        ]
        self.assertIn("const label = (grade ? grade + ' ' : '') + rounded;", row_quality)
        self.assertNotIn("const label = 'Q ' +", row_quality)
        self.assertLess(app_js.index("+ (qcBadgeHtml || '')"), app_js.index("+ (pctBadgeHtml || '')"))
        self.assertIn(".conv-qc-badge", app_css)
        self.assertIn(".conv-qc-badge.is-good", app_css)
        self.assertIn(".conv-qc-badge.is-warn", app_css)
        self.assertIn(".conv-qc-badge.is-bad", app_css)

    def test_codex_slash_commands_are_wired_as_codex_commands(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("const CODEX_SLASH_FALLBACK_COMMANDS = [", app_js)
        self.assertIn("{ name: '/compact', description: 'Summarize the visible conversation to free tokens' }", app_js)
        self.assertIn("return source === 'codex' ? CODEX_SLASH_FALLBACK_COMMANDS : SLASH_FALLBACK_COMMANDS;", app_js)
        self.assertIn("compactCommand && isCompactionCapableSource(currentSession.source)", app_js)
        self.assertNotIn("Codex sessions do not use Claude slash commands", app_js)
        self.assertIn("const failurePrefix = compactCommand ? '/compact failed'", app_js)

    def test_slash_command_picker_selects_on_press(self):
        """Mouse/touch selection must commit on press, before focus refreshes
        or document-level click handlers can interfere with the popup."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function selectSlashCommandMenuItemFromEvent(ev)", app_js)
        self.assertIn("target && target.closest ? target : (target && target.parentElement)", app_js)
        self.assertIn("el.closest('.slash-command-item')", app_js)
        self.assertIn("_slashMenuEl.addEventListener('pointerdown'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('mousedown'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('touchstart'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('click'", app_js)
        self.assertIn("return commitSlashCommandSelection(_slashMenuInput);", app_js)
        self.assertIn("function syncSlashCommandMenuSelection()", app_js)
        self.assertIn("btn.classList.toggle('selected', selected);", app_js)
        self.assertIn("syncSlashCommandMenuSelection();", app_js)
        self.assertNotIn("renderSlashCommandMenu(input, _slashMenuItems, q);", app_js)

    def test_mobile_conversation_rows_open_on_pointer_tap_without_scroll_jank(self):
        """Touch rows should open from the real pointer tap, not a later
        synthetic click, and scrolling the list should not run per-row
        touchmove/touchstart handlers."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function wireMobileConversationRowTaps()", app_js)
        self.assertIn("const _anyCoarsePointerMQ = window.matchMedia('(any-pointer: coarse)');", app_js)
        self.assertIn("function isTouchLikePointerEvent(ev)", app_js)
        self.assertIn("function shouldUseTouchRowMode(ev)", app_js)
        self.assertIn("function rowDraggableAttr() { return shouldUseTouchRowMode() ? 'false' : 'true'; }", app_js)
        self.assertIn("if (ev.pointerType === 'mouse' || !shouldUseTouchRowMode(ev)) return;", app_js)
        self.assertIn("$convList.addEventListener('pointerup', finishMobileConversationRowTap);", app_js)
        self.assertIn("activateConversationRowFromTap(row, ev, { allowTitle: true, source: 'pointer' });", app_js)
        self.assertIn("function shouldSuppressSyntheticRowClick(id)", app_js)
        self.assertIn("if (shouldSuppressSyntheticRowClick(item.dataset.id)) { ev.preventDefault(); return; }", app_js)
        self.assertIn("function noteConversationListScrollActivity()", app_js)
        self.assertIn("$convList.addEventListener('scroll', noteConversationListScrollActivity, { passive: true, capture: true });", app_js)
        self.assertIn("$convList.addEventListener('touchmove', noteConversationListScrollActivity, { passive: true, capture: true });", app_js)
        self.assertIn("if (isConversationListScrollActive()) return true;", app_js)
        self.assertIn("if (!(opts && opts.force) && shouldPauseSidebarRender()) { _sidebarRenderPendingWhilePaused = true; return; }", app_js)
        self.assertIn("const _skipFlipForTouchScroll = shouldUseTouchRowMode();", app_js)
        self.assertIn("&& !_skipFlipForTouchScroll", app_js)
        self.assertNotIn("el.classList.add('mobile-active-tap')", app_js)
        self.assertNotIn("el.classList.remove('mobile-active-tap')", app_js)
        self.assertIn("touch-action: pan-y;", app_css)
        self.assertIn(".conv-item:active,\n    .conv-item.mobile-active-tap {\n      background-color: var(--surface-2) !important;\n      opacity: 0.85;\n    }", app_css)
        self.assertNotIn(".conv-item.mobile-active-tap {\n      background-color: var(--surface-2) !important;\n      opacity: 0.85;\n      transform:", app_css)

    def test_relayed_question_renders_inline_in_conv_view(self):
        """The "Session is asking a question" surface is an inline card
        mounted inside the active conversation view (not a body-level
        modal overlay) so it inherits the conv pane's font stack and
        lives where the user is reading. Guarded by class names so a
        future refactor that accidentally reintroduces the modal trips
        this test."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Inline card class + data-role must exist in JS and CSS.
        self.assertIn('ccc-inline-question', app_js)
        self.assertIn('data-role="ccc-inline-question"', app_js)
        self.assertIn(".ccc-inline-question", app_css)
        # Mount must target the active conv view, not document.body.
        self.assertIn("$view.appendChild(modal)", app_js)
        self.assertIn("showRelayedQuestionInline", app_js)
        self.assertIn("closeRelayedQuestionInline", app_js)
        # Old modal shell must be gone — no more body-level overlay.
        self.assertNotIn("cccQuestionModal", app_js)
        self.assertNotIn('upd-overlay ccc-question-modal', app_js)
        # The old .ccc-question-modal selector block must not declare
        # any actual rules (comment mentions of the migrated name are
        # fine; an active selector means the modal CSS came back).
        self.assertNotIn(".ccc-question-modal {", app_css)
        self.assertNotIn(".ccc-question-modal .", app_css)
        # Fonts inherit from the conv pane (rather than the modal's own).
        self.assertIn(".ccc-inline-question {", app_css)
        self.assertIn("font: inherit;", app_css)

    def test_original_ask_prefers_canonical_first_message(self):
        """The right-rail Original ask must not depend on the first user_text
        in an incremental render batch. A later status-summary user event can
        be first in that batch; the canonical row first_message is the stable
        anchor. But first_message is server-truncated (~200 chars), so when
        the rendered event IS that message, the full event text wins (CCC-456)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function originalAskTextForEvent(ev, paneId)", app_js)
        self.assertIn("const canonical = (conv && conv.first_message) || '';", app_js)
        self.assertIn("if (canonPrefix && norm(evText).startsWith(canonPrefix)) return evText;", app_js)
        self.assertIn("return canonical || evText;", app_js)
        self.assertIn("const cleaned = cleanIssuePrompt(originalAskTextForEvent(ev, paneId));", app_js)

    def test_right_rail_uses_metadata_files_and_queue_tabs(self):
        """The right rail keeps activity in Metadata, with Files and Queue as
        their own utility panes."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('<div class="status-rail-title" id="statusRailTitle">Session Utilities</div>', index_html)
        self.assertIn('data-rail-tab="metadata"', index_html)
        self.assertIn('data-rail-tab="files"', index_html)
        self.assertIn('data-rail-tab="queue"', index_html)
        self.assertNotIn('data-rail-tab="activity"', index_html)
        self.assertIn('id="statusRailTopbar"', index_html)
        self.assertIn('id="statusRailAnnotateBtn"', index_html)
        self.assertLess(index_html.index('id="statusRailAnnotateBtn"'), index_html.index('id="statusRailCloseBtn"'))
        self.assertLess(index_html.index('data-rail-tab="metadata"'), index_html.index('data-rail-tab="files"'))
        self.assertLess(index_html.index('data-rail-tab="files"'), index_html.index('data-rail-tab="queue"'))
        self.assertIn('id="statusRailMetadataPane"', index_html)
        self.assertIn('id="statusRailFilesPane"', index_html)
        self.assertIn('id="statusRailQueuePane"', index_html)
        self.assertNotIn('id="statusRailActivityPane"', index_html)
        metadata_block = index_html[index_html.index('id="statusRailMetadataPane"'):index_html.index('id="statusRailFilesPane"')]
        files_block = index_html[index_html.index('id="statusRailFilesPane"'):index_html.index('id="statusRailQueuePane"')]
        self.assertNotIn('id="filesPanel"', metadata_block)
        self.assertIn('id="subagentsPanel"', metadata_block)
        self.assertIn('id="filesPanel"', files_block)
        self.assertNotIn('id="filesViewToggle"', index_html)
        self.assertIn("function setStatusRailTab(tab)", app_js)
        self.assertIn("rail.querySelector('#statusRailMetadataPane')", app_js)
        self.assertIn("rail.querySelector('#statusRailFilesPane')", app_js)
        self.assertIn("rail.querySelector('#statusRailQueuePane')", app_js)
        self.assertNotIn("rail.querySelector('#statusRailActivityPane')", app_js)
        self.assertIn("const next = (tab === 'files' || tab === 'queue') ? tab : 'metadata';", app_js)
        self.assertIn("const $statusRailAnnotateBtn = document.getElementById('statusRailAnnotateBtn');", app_js)
        self.assertIn("$statusRailAnnotateBtn.addEventListener('click', annStart);", app_js)
        self.assertNotIn("getElementById('filesViewToggle')", app_js)
        self.assertIn(".status-rail-topbar", app_css)
        self.assertIn(".status-rail-annotate", app_css)
        self.assertIn(".rail-actions #annotationStartBtn", app_css)
        self.assertIn(".status-rail-tabs", app_css)
        tabs_css = app_css[app_css.index(".status-rail-tabs {"):app_css.index(".status-rail-tab {", app_css.index(".status-rail-tabs {"))]
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", tabs_css)
        self.assertIn(".status-rail-pane.is-active", app_css)
        self.assertIn("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] {", app_css)
        self.assertIn("body.status-pos-right .status-rail-pane[data-rail-pane=\"files\"] {", app_css)
        self.assertIn("body.status-pos-right .status-rail-pane[data-rail-pane=\"files\"] .files-list {", app_css)
        self.assertIn("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] > .csh-col-activity {", app_css)
        metadata_css = app_css[app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] {"):app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"files\"]", app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] {"))]
        self.assertIn("overflow-y: hidden;", metadata_css)
        files_scroll_css = app_css[app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"files\"] .files-list {"):app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] > .csh-col-activity", app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"files\"] .files-list {"))]
        self.assertIn("overflow-y: auto;", files_scroll_css)
        self.assertIn("flex: 1 1 auto;", files_scroll_css)
        self.assertNotIn("max-height: clamp(", files_scroll_css)
        activity_css = app_css[app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] > .csh-col-activity {"):app_css.index("body.status-pos-right .status-rail .status-rail-pane > .csh-ask-original .user-msg", app_css.index("body.status-pos-right .status-rail-pane[data-rail-pane=\"metadata\"] > .csh-col-activity {"))]
        self.assertIn("flex: 1 1 auto;", activity_css)
        self.assertIn("min-height: 0;", activity_css)
        self.assertIn("overflow-y: auto;", activity_css)
        self.assertNotIn("overflow-y: visible;", activity_css)
        self.assertNotIn("flex: 0 0 auto;", activity_css)

    def test_right_rail_hides_conversation_top_chrome(self):
        """Right-rail mode should not leave old top chrome above the
        conversation pane."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        toolbar_empty_css = app_css[
            app_css.index(".toolbar.is-empty {"):
            app_css.index("/* Subagent transcripts", app_css.index(".toolbar.is-empty {"))
        ]
        self.assertIn("height: 0 !important;", toolbar_empty_css)
        self.assertIn("$convToolbar.classList.toggle('is-empty', !hasVisibleContent);", app_js)

        right_rail_start = app_css.index("Status-position: right rail")
        right_rail_css = app_css[
            right_rail_start:
            app_css.index("PWA install banner", right_rail_start)
        ]
        self.assertIn(
            "body.status-pos-right .conv-sticky-header,\nbody.status-pos-right #convToolbar {\n  display: none !important;\n}",
            right_rail_css,
        )

    def test_queue_rows_open_item_detail_modal(self):
        """Queue row clicks should show the ticket payload/screenshot instead
        of trying to jump to a brittle transcript reference."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function _uxqOpenItemModal(item)", app_js)
        self.assertIn("function _uxqItemTitle(item)", app_js)
        self.assertIn("const detailTitle = _uxqItemTitle(item);", app_js)
        self.assertIn('class="uxq-td-title"', app_js)
        self.assertIn('class="uxq-td-ref"', app_js)
        self.assertIn("Click to view ticket details", app_js)
        self.assertIn("_uxqOpenItemDetail(row.getAttribute('data-ref'))", app_js)
        queue_click = app_js[app_js.index("$queueList.addEventListener('click'"):app_js.index("// STUCK badge", app_js.index("$queueList.addEventListener('click'"))]
        self.assertNotIn("_uxqJumpToRef", queue_click)
        self.assertIn(".uxq-td-title-wrap", app_css)
        self.assertIn(".uxq-td-title", app_css)
        self.assertIn(".uxq-detail-meta", app_css)

    def test_queue_all_history_renders_a_bounded_page_at_scale(self):
        """All-history filtering may scan every ticket, but DOM work stays bounded."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        render_js = app_js[
            app_js.index("function _renderQueuePanel"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel"))
        ]

        self.assertIn("const _UXQ_HISTORY_PAGE_SIZE = 80;", app_js)
        self.assertIn(
            "const visibleRows = historyOrder ? rows.slice(historyStart, historyEnd) : rows;",
            render_js,
        )
        self.assertIn("const queueRowsHtml = visibleRows.map(it =>", render_js)
        self.assertNotIn("const queueRowsHtml = rows.map(it =>", render_js)
        self.assertIn("data-uxq-history-page=", render_js)
        self.assertIn("_uxqHistoryPage += direction;", app_js)
        scope_setter = app_js[
            app_js.index("function _uxqSetScopeOverride"):
            app_js.index("const _UXQ_FILTER_LS", app_js.index("function _uxqSetScopeOverride"))
        ]
        self.assertIn("_uxqResetHistoryPage();", scope_setter)
        self.assertIn(".fq-history-pager", app_css)

    def test_queue_state_badges_explain_stuck(self):
        """Queue health badges should answer what each compact state means."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("STUCK means this queue has open claimable work assigned to a worker/session", app_js)
        self.assertIn("Stuck means this auto-drain queue has claimable open tickets", app_js)
        self.assertIn('" aria-label="\' + escapeAttr(badgeTip)', app_js)
        self.assertIn('" aria-label="\' + escapeAttr(stateTip)', app_js)

    def test_queue_drain_toggle_reports_parked_and_failed_updates(self):
        """Turning on drain must explain why zero-claimable queues stay idle."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        toggle_js = app_js[
            app_js.index("const toggleDrain = async (ev) =>"):
            app_js.index("const cycleClaimTypes = async (ev) =>")
        ]

        self.assertIn("if (!res.ok || !data.ok)", toggle_js)
        self.assertIn("has no runnable tickets", toggle_js)
        self.assertIn("Auto-drain enabled for", toggle_js)
        self.assertIn("Auto-drain disabled for", toggle_js)
        self.assertIn("Auto-drain update failed", toggle_js)
        self.assertLess(
            toggle_js.index("const queueHealth ="),
            toggle_js.index("await fetch('/api/queue/drain'"),
        )

    def test_queue_detail_uses_watchtower_timeline_contract(self):
        """Ticket detail should come from WT timeline, not CCC's old private
        progress_notes/answers reconstruction."""
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        modal_js = app_js[
            app_js.index("function _uxqOpenItemModal(item)"):
            app_js.index("function _renderQueuePanel", app_js.index("function _uxqOpenItemModal(item)"))
        ]

        self.assertIn('if path == "/api/ux-fixes/item":', server_py)
        self.assertIn('"timeline"', server_py)
        self.assertNotIn("_wt_q._FileLock", server_py)
        self.assertNotIn("_wt_q._load_unlocked", server_py)
        self.assertIn("async function _uxqOpenItemDetail(ref)", app_js)
        self.assertIn("item.timeline", modal_js)
        self.assertIn("uxq-show-edits", modal_js)
        self.assertNotIn("progress_notes", modal_js)
        self.assertNotIn("item.answers", modal_js)
        self.assertNotIn("item.block_question) {", modal_js)

    def test_blocked_queue_detail_says_the_agent_needs_input(self):
        """A blocked ticket's live activity line must clearly ask the human
        for input, rather than ambiguously saying it is waiting for an answer."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        modal_js = app_js[
            app_js.index("function _uxqOpenItemModal(item)"):
            app_js.index("function _renderQueuePanel", app_js.index("function _uxqOpenItemModal(item)"))
        ]

        self.assertIn("status === 'blocked' ? 'Agent needs your input' : 'Open'", modal_js)

    def test_queue_item_payload_keeps_close_report_without_watchtower_import(self):
        """CCC's stdlib-only queue fallback must still expose a worker's
        close summary to the ticket-detail Activity timeline."""
        server = importlib.import_module("server")
        item = {
            "status": "closed",
            "history": [{
                "event": "close",
                "at": "2026-07-12T13:34:40Z",
                "by": {"kind": "worker", "worker": "ccc-worker"},
                "resolution": {"summary": "Restored the missing label"},
            }],
        }

        with mock.patch.object(server, "_q", object()):
            payload = server._uxq_item_payload(item)

        self.assertEqual(payload["timeline"][0]["event"], "close")
        self.assertEqual(payload["timeline"][0]["resolution"]["summary"], "Restored the missing label")

    def test_queue_status_icons_are_large_and_follow_status_color_mapping(self):
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        status_css = app_css[app_css.index(".fq-status {"):app_css.index(".fq-row.is-open .fq-status", app_css.index(".fq-status {"))]
        self.assertIn("width: 14px;", status_css)
        self.assertIn("height: 14px;", status_css)
        self.assertIn("box-shadow:", status_css)
        self.assertIn("@keyframes fq-status-glow", app_css)
        open_css = app_css[
            app_css.index(".fq-row.is-open .fq-status {"):
            app_css.index(".fq-row.is-in_progress .fq-status", app_css.index(".fq-row.is-open .fq-status {"))
        ]
        in_progress_css = app_css[app_css.index(".fq-row.is-in_progress .fq-status {"):app_css.index(".fq-row.is-closed .fq-status", app_css.index(".fq-row.is-in_progress .fq-status {"))]
        closed_css = app_css[
            app_css.index(".fq-row.is-closed .fq-status {"):
            app_css.index(".fq-row.is-closed .fq-note", app_css.index(".fq-row.is-closed .fq-status {"))
        ]
        self.assertIn("var(--text-muted, #8b949e)", open_css)
        self.assertIn("#0b3d91", in_progress_css)
        self.assertIn("var(--green, #3fb950)", closed_css)
        self.assertIn("animation: fq-status-glow 1.5s ease-in-out infinite;", in_progress_css)
        self.assertIn("will-change: opacity, box-shadow;", in_progress_css)
        self.assertIn("0 0 24px rgba(33,150,243,0.95)", app_css)

    def test_queue_header_can_toggle_wrapped_titles(self):
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn('id="queueWrapToggle"', index_html)
        self.assertIn('aria-pressed="false"', index_html)
        self.assertIn("const _UXQ_WRAP_TITLES_LS = 'ccc-uxq-wrap-titles';", app_js)
        self.assertIn("function _uxqRenderWrapToggle()", app_js)
        self.assertIn("queuePanel.classList.toggle('queue-wrap-titles', _uxqGetWrapTitles());", app_js)
        self.assertIn("queueWrapToggle.addEventListener('click'", app_js)
        self.assertIn(".files-queue-panel.queue-wrap-titles .fq-note {", app_css)
        wrap_css = app_css[
            app_css.index(".files-queue-panel.queue-wrap-titles .fq-note {"):
            app_css.index("/* Status as a compact colored dot", app_css.index(".files-queue-panel.queue-wrap-titles .fq-note {"))
        ]
        self.assertIn("white-space: normal;", wrap_css)
        self.assertIn("overflow: visible;", wrap_css)
        self.assertIn("overflow-wrap: anywhere;", wrap_css)

    def test_queue_add_uses_large_composer(self):
        """Adding a queue item should use a multiline composer, not prompt()."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function openQueueTicketComposer()", app_js)
        self.assertIn("const note = await openQueueTicketComposer();", app_js)
        self.assertNotIn("window.prompt('New queue ticket", app_js)
        self.assertIn('id="filesQueueAdd"', index_html)
        self.assertIn('class="fq-add-row" id="filesQueueAdd"', app_js)
        self.assertGreater(
            app_js.index('class="fq-add-row" id="filesQueueAdd"'),
            app_js.index("$queue.innerHTML = queueRowsHtml"),
        )
        self.assertIn('class="fq-ticket-textarea"', app_js)
        self.assertIn('rows="7"', app_js)
        self.assertIn('data-fq-ticket-submit', app_js)
        self.assertIn(".fq-ticket-textarea", app_css)
        self.assertIn("min-height: 150px;", app_css)
        self.assertIn("resize: vertical;", app_css)

    def test_queue_manager_can_create_and_revise_full_watchtower_config(self):
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertNotIn('id="filesQueueConfigure"', index_html)
        self.assertIn("function openQueueManager", app_js)
        self.assertIn("/api/queue/config-options", app_js)
        self.assertIn("/api/queue/config", app_js)
        self.assertIn('data-fq-config-queue', app_js)
        self.assertIn('id="filesQueueConfigure"', app_js)
        self.assertIn('name="fq-config-backend"', app_js)
        self.assertIn('name="fq-config-claim-type"', app_js)
        self.assertIn('id="fqConfigEffort"', app_js)
        self.assertIn("effort: fields.effort.value", app_js)
        self.assertIn("queue configuration", app_js)
        self.assertIn(".fq-config-dialog", app_css)

    def test_toolbar_controls_move_to_settings_and_metadata_rail(self):
        """Right-rail mode should empty the crowded conversation topbar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("_moveToHome('todayToggleBtn',    $settingsSlot);", app_js)
        self.assertIn("_moveToHome('annotationNotesBtn', $settingsSlot);", app_js)
        self.assertIn("_moveToHome('cooPopButton',      $settingsSlot);", app_js)
        self.assertIn("cooMoveObserver.observe(document.body, { childList: true, subtree: true });", app_js)
        self.assertIn("_captureRailEl(document.getElementById('cccBreadcrumb'));", app_js)
        self.assertIn("_captureRailEl(document.getElementById('convStatus'));", app_js)
        self.assertIn("_captureRailEl(document.getElementById('topbarTtsControl'));", app_js)
        self.assertIn("_captureRailEl(document.getElementById('annotationStartBtn'));", app_js)
        self.assertIn("_captureRailEl(document.getElementById('annotationScreenBtn'));", app_js)
        self.assertIn(".rail-actions #cccBreadcrumb,\n.rail-actions #convStatus {", app_css)
        self.assertIn("flex: 1 1 100%;", app_css)

    def test_rail_breadcrumb_hides_repeated_session_title(self):
        """The status rail already mirrors the session title in its header, so
        the moved breadcrumb should not repeat that same title."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("if (railTitleEl) railTitleEl.textContent = title || category || 'Session';", app_js)
        self.assertIn(".rail-actions #cccBreadcrumb .ccc-breadcrumb-title {", app_css)
        self.assertIn("display: none;", app_css[
            app_css.index(".rail-actions #cccBreadcrumb .ccc-breadcrumb-title {"):
            app_css.index(".rail-actions #convStatus {", app_css.index(".rail-actions #cccBreadcrumb .ccc-breadcrumb-title {"))
        ])

    def test_files_sidebar_sorts_by_recent_mentions(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function _ffcRecencyLine(row)", app_js)
        self.assertIn("allFiles.sort((a, b) => _ffcRecencyLine(b) - _ffcRecencyLine(a));", app_js)

    def test_markdown_file_viewer_temporarily_widens_right_rail(self):
        """Opening a Markdown preview should widen the right rail and closing
        it should restore the previous rail width."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const FILE_VIEWER_RAIL_EXPAND_FACTOR = 2.5;", app_js)
        self.assertIn("let fileViewerPreviousRailWidth = null;", app_js)
        self.assertIn("let fileViewerPreviousRailStoredWidth = null;", app_js)
        self.assertIn("window._cccExpandStatusRailForFileViewer = _expandStatusRailForFileViewer;", app_js)
        self.assertIn("window._cccRestoreStatusRailAfterFileViewer = _restoreStatusRailAfterFileViewer;", app_js)
        self.assertIn("if (typeof window._cccExpandStatusRailForFileViewer === 'function') window._cccExpandStatusRailForFileViewer();", app_js)
        self.assertIn("if (typeof window._cccRestoreStatusRailAfterFileViewer === 'function') window._cccRestoreStatusRailAfterFileViewer();", app_js)
        self.assertIn("_setStatusRailWidth(fileViewerPreviousRailWidth * FILE_VIEWER_RAIL_EXPAND_FACTOR, false);", app_js)
        self.assertIn("_setStatusRailWidth(previousWidth, false);", app_js)
        self.assertIn("if (previousStoredWidth == null) localStorage.removeItem('ccc-status-rail-width');", app_js)

    def test_done_result_can_copy_agent_answer(self):
        """Successful Done rows expose a small copy affordance for the last
        assistant answer, rather than forcing users to select rendered text
        manually from the transcript."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function agentAnswerTextBeforeResult(resultEl)", app_js)
        self.assertIn("data-copy-agent-answer", app_js)
        self.assertIn('aria-label="Copy agent answer"', app_js)
        self.assertIn("copyTextValue(text)", app_js)
        self.assertIn("Copied agent answer", app_js)
        self.assertIn(".result-copy-agent-answer", app_css)
        self.assertIn(".result-copy-agent-answer.copied", app_css)

    def test_assistant_events_have_read_and_copy_actions_next_to_timestamp(self):
        """Assistant message metadata should expose small read/copy actions
        beside the relative timestamp, not only on Done result rows."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function assistantMessageActionsHtml(ev)", app_js)
        self.assertIn("const hasAssistantText = Array.isArray(ev && ev.blocks)", app_js)
        self.assertIn("if (!hasAssistantText) return '';", app_js)
        self.assertIn("data-read-assistant-message", app_js)
        self.assertIn("data-copy-assistant-message", app_js)
        self.assertIn('aria-label="Read assistant message aloud"', app_js)
        self.assertIn('aria-label="Copy assistant message"', app_js)
        self.assertIn("const btn = ev.target.closest('[data-copy-assistant-message]');", app_js)
        self.assertIn("const btn = ev.target.closest('[data-read-assistant-message]');", app_js)
        self.assertIn("assistantNodeTextForCopy(eventEl)", app_js)
        self.assertIn("speakTextDirect(text, convId, paneId, btn)", app_js)
        self.assertIn("let html = assistantMessageActionsHtml(ev)", app_js)
        self.assertIn("const assistantBlocks = Array.isArray(ev.blocks)", app_js)
        self.assertIn("for (const b of assistantBlocks)", app_js)
        self.assertIn("function whatsappBridgeSenderHtml(ev)", app_js)
        self.assertIn("ev.sender_name || ev.pushName || ev.sender_id", app_js)
        self.assertIn("bridgeSenderHtml + linkifyPastedImages", app_js)
        self.assertIn(".assistant-message-actions", app_css)
        self.assertIn(".assistant-message-action", app_css)
        self.assertIn(".conversations-view .whatsapp-bridge-sender", app_css)
        assistant_row_css = app_css[
            app_css.index(".conversations-view .event.assistant {"):
            app_css.index("/* Meta (line number + timestamp)", app_css.index(".conversations-view .event.assistant {"))
        ]
        self.assertIn("padding: 24px 0 4px;", assistant_row_css)
        meta_css = app_css[
            app_css.index(".conversations-view .event.assistant .line-num,"):
            app_css.index("/* line-num is width-bounded", app_css.index(".conversations-view .event.assistant .line-num,"))
        ]
        self.assertIn("font-size: 12px;", meta_css)
        self.assertIn("line-height: 1.35;", meta_css)
        self.assertIn("top: 2px;", meta_css)
        self.assertIn(".conversations-view .event.assistant .msg-ts { right: 104px; }", app_css)

    def test_new_assistant_text_reveals_word_by_word(self):
        """New assistant rows should reveal prose word-by-word without
        replaying old transcript loads."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function _convShouldLiveRevealNewText(opts)", app_js)
        self.assertIn("if (opts && (opts.initialLoad || opts.prepending)) return false;", app_js)
        self.assertIn("if (_convReplayActive) return false;", app_js)
        self.assertIn("_wrapReplayWordsInHtml(container.innerHTML, 'conv-live-word')", app_js)
        self.assertIn("const CONV_LIVE_REVEAL_BOTTOM_EPSILON = 2;", app_js)
        self.assertIn("function _convShouldLiveRevealStickToBottom(view)", app_js)
        self.assertIn("conversationDistanceFromBottom(view) <= CONV_LIVE_REVEAL_BOTTOM_EPSILON", app_js)
        self.assertIn("const shouldStick = _convShouldLiveRevealStickToBottom($view);", app_js)
        self.assertIn("if (ev.type === 'assistant') _convLiveRevealNewText(div, paneId, opts);", app_js)
        self.assertIn("function _replayRevealRun(runEl)", app_js)
        self.assertIn("data-replay-shell-first-run-id", app_js)
        self.assertIn("const shellSelector = 'p, li, ul, ol, blockquote, table, thead, tbody, tr, th, td, h1, h2, h3, h4, h5, h6';", app_js)
        self.assertIn("el.dataset.replayShellFirstRunId = firstRun.dataset.runId;", app_js)
        self.assertIn("_replayRevealRun(wordSpan);", app_js)
        self.assertIn("_replayRevealRun(span);", app_js)
        self.assertIn("_replayRevealAllShells(container);", app_js)
        self.assertIn(".conv-live-word.gc-typing-shimmer", app_css)

    def test_codex_silent_result_is_labeled_as_no_visible_response(self):
        """Codex task_complete rows can lack any assistant text. Those should
        not render as ordinary Done answers, which makes empty turns look like
        mysterious completed sessions."""
        server = importlib.import_module("server")
        parsed = server._parse_codex_event({
            "timestamp": "2026-06-24T01:59:03.062Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "last_agent_message": None,
                "duration_ms": 2270,
            },
        }, 13)
        self.assertEqual(parsed["type"], "result")
        self.assertTrue(parsed["no_agent_output"])
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("no_agent_output", app_js)
        self.assertIn("No visible response", app_js)
        self.assertIn("result-silent", app_js)
        self.assertIn(".event.result.result-silent", app_css)
        self.assertIn(".conversations-view .event.result.result-silent", app_css)
        self.assertIn("not a live stuck process", app_js)
        self.assertIn("Use the wake/follow-up box below", app_js)

    def test_codex_silent_turn_log_lookup_scopes_to_its_thread_repo(self):
        """Opening a silent Codex turn must not scan every recent Codex repo."""
        server = importlib.import_module("server")
        with mock.patch.object(
            server, "_codex_thread_row", return_value={"cwd": "/work/repo/subdir"}
        ), mock.patch.object(
            server, "_git_toplevel_for_existing_dir", return_value="/work/repo"
        ), mock.patch.object(
            server, "_recent_codex_ccc_log_paths", return_value=[]
        ) as recent_logs:
            self.assertEqual(server._codex_logs_for_session("thread-123"), [])

        self.assertEqual(recent_logs.call_args.kwargs["repo_paths"], ["/work/repo"])

    def test_stale_optimistic_thinking_settles_when_no_process_exists(self):
        """The optimistic Thinking pill should not tick forever after the
        status poll proves there is no live/headless/terminal process."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function settleStaleOptimisticAgentIndicator($view)", app_js)
        self.assertIn("No active agent process", app_js)
        self.assertIn("Send a message to resume it headlessly", app_js)
        self.assertIn("or use Launch if typing is unavailable.", app_js)
        self.assertIn("!liveStatus.live && !liveStatus.headlessPresent && !liveStatus.terminalPresent && !liveStatus.bgPresent", app_js)
        self.assertIn("settleStaleOptimisticAgentIndicator($view);", app_js)
        self.assertIn(".conv-live-tool-inline.is-stale-no-process", app_css)

    def test_idle_process_pill_explains_that_the_session_can_resume(self):
        """A dormant session remains usable even when no worker is attached."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("(waking ? 'waking…' : 'idle')", app_js)
        self.assertIn("You can still send a message; CCC will resume the session.", app_js)

    def test_live_inline_indicator_is_singleton(self):
        """A refreshed live status tick should not leave stacked Generating rows."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function getSingleLiveToolInline($view)", app_js)
        self.assertIn("const nodes = Array.from($view.querySelectorAll('.conv-live-tool-inline:not(.optimistic):not(.is-wake-status):not(.wake-breakdown)'));", app_js)
        self.assertIn("const keep = nodes[nodes.length - 1];", app_js)
        self.assertIn("nodes.forEach(node => { if (node !== keep) node.remove(); });", app_js)
        self.assertIn("let _doneInline = getSingleLiveToolInline($view);", app_js)
        self.assertIn("let inline = getSingleLiveToolInline($view);", app_js)
        self.assertIn("inline !== $view.lastElementChild", app_js)

    def test_codex_wake_status_does_not_stack_with_generating(self):
        """Codex wake rows own their progress UI and should not stack with Generating."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn(".conv-live-tool-inline.is-wake-status, .conv-live-tool-inline.wake-breakdown", app_js)
        self.assertIn("function clearLiveGeneratingIndicator($view)", app_js)
        self.assertIn("function _anchorWakeBreakdown($view)", app_js)
        self.assertIn("const hasWakeProgress = !!$view.querySelector('.conv-live-tool-inline.optimistic, .conv-live-tool-inline.is-wake-status, .conv-live-tool-inline.wake-breakdown');", app_js)
        self.assertIn("if (hasWakeProgress) {", app_js)
        self.assertIn("clearLiveGeneratingIndicator($view);", app_js)

    def test_codex_wake_breakdown_starts_during_dormant_send(self):
        """Dormant Codex sends should expose wake progress before inject-input returns."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")

        self.assertIn("startCodexWakeBreakdown($wv, sid);", app_js)
        self.assertIn("appendStageRow('Waiting for wake request', false, true);", app_js)
        self.assertIn("waiting for server wake log", app_js)
        self.assertIn("warning: ' + data.warning", app_js)
        self.assertIn("detail: ' + data.outcome_detail", app_js)
        self.assertIn("effort=reasoning_effort", server_py)
        self.assertIn("overflow-wrap: anywhere;", app_css)

    def test_codex_wake_progress_is_quiet_until_it_stalls(self):
        """A normal Codex wake should not render an alarming diagnostic stack."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function _wakeStageQuietLabel", app_js)
        self.assertIn("return 'Thinking… ' + model + effort;", app_js)
        self.assertIn("const WAKE_STAGE_DETAIL_DELAY_MS = 1000;", app_js)
        self.assertIn("el.classList.toggle('is-detailed', showDetails);", app_js)
        self.assertIn(".wake-breakdown .wb-stage.is-done .wb-label { display: none; }", app_css)
        self.assertNotIn("Waking up&hellip;", app_js)
        self.assertNotIn("Waking up headless&hellip;", app_js)
        self.assertNotIn("Waking the headless agent", app_js)
        self.assertIn("const optimistic = $view.querySelector('.conv-live-tool-inline.optimistic');", app_js)
        self.assertIn("if (optimistic) optimistic.remove();", app_js)

    def test_mobile_live_command_indicator_collapses_command_detail(self):
        """Mobile should show a compact Bash/tool pill instead of a multi-line
        command block at the bottom of the conversation pane."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        base_live_css = app_css[
            app_css.index(".conv-live-tool-inline {"):
            app_css.index("@media (max-width: 700px)", app_css.index(".conv-live-tool-inline {"))
        ]

        self.assertIn(".conv-live-tool-inline:not(.is-expanded) .cl-file.is-command", base_live_css)
        self.assertIn("display: none;", base_live_css)
        self.assertIn(".conv-live-tool-inline.is-expanded .cl-file.is-command", base_live_css)
        self.assertIn("max-height: 7.5em;", base_live_css)
        self.assertIn("@media (max-width: 700px) {", app_css)
        self.assertIn(".conv-live-tool-inline:not(.is-expanded) .cl-file.is-command", app_css)
        self.assertIn("display: none;", app_css)
        self.assertIn(".conv-live-tool-inline.is-expanded .cl-file.is-command", app_css)
        self.assertIn("max-height: 5.2em;", app_css)
        self.assertIn("overflow: auto;", app_css)

    def test_compact_completion_clears_optimistic_thinking(self):
        """A completed compaction should not leave the optimistic Thinking pill."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("Codex compact has completed; any optimistic Thinking pill is stale.", app_js)
        self.assertIn("clearOptimisticAgentIndicator(getConvView());", app_js)
        self.assertIn("clearSessionSending(sid);", app_js)
        self.assertIn("compact boundary proves the pending compact turn is over", app_js)
        self.assertIn("clearOptimisticAgentIndicator($view);", app_js)

    def test_compact_request_has_a_hard_timeout(self):
        """A stalled compact request must release the UI so the user can retry."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        compact_post = app_js[
            app_js.index("async function postCompactSession(sessionId, terminalApp)"):
            app_js.index("// Engines whose /compact", app_js.index("async function postCompactSession(sessionId, terminalApp)"))
        ]
        self.assertIn("const COMPACT_REQUEST_TIMEOUT_MS = 4 * 60 * 1000;", compact_post)
        self.assertIn("const controller = typeof AbortController === 'function' ? new AbortController() : null;", compact_post)
        self.assertIn("setTimeout(() => controller.abort(), COMPACT_REQUEST_TIMEOUT_MS)", compact_post)
        self.assertIn("signal: controller ? controller.signal : undefined", compact_post)
        self.assertIn("finally { if (timer) clearTimeout(timer); }", compact_post)

    def test_compact_waits_for_pending_send_echoes(self):
        """Compaction must not run while a sent message is only an optimistic echo."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function hasPendingSendEchoBeforeCompact($view)", app_js)
        self.assertIn(".event.user_text.pending, .event.user_text.send-queued, .event.user_text.send-delivered", app_js)
        self.assertIn("Wait for the pending message to land in the transcript before compacting.", app_js)
        self.assertIn("if (compactCommand && hasPendingSendEchoBeforeCompact(", app_js)
        self.assertIn("const pendingCompactEcho = hasPendingSendEchoBeforeCompact();", app_js)
        self.assertIn("activeCompactBtn.title = pendingCompactEcho", app_js)

    def test_codex_spawn_log_hides_bare_error_marker(self):
        """A lone Codex CLI [error] marker should not open the log with a
        scary blank error section before any real output exists."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("text === '[error]'", app_js)
        self.assertIn("A bare [error] marker carries no actionable stderr content", app_js)
        self.assertIn("function isBenignCodexErrorItem(item)", app_js)
        self.assertIn("async hooks are not supported yet", app_js)
        self.assertIn("if (isBenignCodexErrorItem(ev.item)) continue;", app_js)

    def test_codex_goal_state_renders_near_composer_and_rows(self):
        """Codex /goal state should be visible where the user types, and
        paused/blocked goals must read differently from ordinary active goals
        in both the composer strip and conversation rows."""
        app_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        goal_js = app_js[app_js.index("function goalStatusUi(status)"):app_js.index("function updateInputBar()", app_js.index("function goalStatusUi(status)"))]
        self.assertIn('data-role="conv-goal-strip"', app_html)
        self.assertIn("function goalStatusUi(status)", goal_js)
        self.assertIn("function renderConversationGoalStrip(paneId, row)", goal_js)
        self.assertIn("Goal blocked", goal_js)
        self.assertIn("Goal paused", goal_js)
        self.assertIn("renderConversationGoalStrip(activePaneId(), currentConversationRow())", app_js)
        self.assertIn("is-paused", goal_js)
        self.assertIn("is-blocked", goal_js)
        self.assertIn("function conversationGoalActionButtonsHtml(statusKey, source, isLive)", goal_js)
        self.assertIn("const needsLiveTui = kind === 'codex' && a.action === 'edit' && !isLive;", goal_js)
        self.assertIn("Clear/pause/resume are server-side goal-store", goal_js)
        self.assertIn('data-role="conv-goal-action"', goal_js)
        self.assertIn("function conversationGoalActionCommand(action)", goal_js)
        self.assertIn("return '/goal clear';", goal_js)
        self.assertIn("return '/goal resume';", goal_js)
        self.assertIn("return '/goal edit';", goal_js)
        self.assertIn("return '/goal pause';", goal_js)
        self.assertIn("async function sendConversationGoalAction(btn)", goal_js)
        self.assertIn("function conversationGoalSourceKind(source)", goal_js)
        self.assertIn("if (kind === 'claude')", goal_js)
        self.assertIn("{ action: 'clear', label: 'Clear', iconHtml: '&times;' }", goal_js)
        self.assertIn("conversationGoalActionButtonsHtml(ui.key, row && row.source, !!(row && row.is_live))", goal_js)
        self.assertNotIn("(row && row.source === 'codex') ? conversationGoalActionButtonsHtml", goal_js)
        self.assertIn(".conv-goal-strip", app_css)
        self.assertIn(".conv-goal-strip-actions", app_css)
        self.assertIn(".conv-goal-action", app_css)
        self.assertIn(".conv-goal-action.is-clear", app_css)
        self.assertIn(".conv-goal-strip.is-paused", app_css)
        self.assertIn(".conv-goal-strip.is-blocked", app_css)
        self.assertIn(".conv-item .conv-goal.is-paused", app_css)
        self.assertIn(".conv-item .conv-goal.is-blocked", app_css)

    def test_dormant_codex_goal_send_does_not_wait_for_transcript_echo(self):
        """The direct goal-store path does not create a user_message event."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        branch = app_js[
            app_js.index("renderConvWakeOutcome(getConvViewForPane"):
            app_js.index("} else if (data.queued && data.cwd_missing)", app_js.index("renderConvWakeOutcome(getConvViewForPane"))
        ]
        self.assertIn("data.via === 'codex-goal-store'", branch)
        self.assertIn("removePendingSendEcho(pendingSend);", branch)
        self.assertIn("Goal updated.", branch)
        self.assertIn("refreshConversationList", branch)

    def test_codex_goal_store_update_mutates_sqlite(self):
        """Dormant Codex /goal actions should update the native goal DB without
        needing a live TUI or trusting a best-effort app-server RPC."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        old_candidates = server.CODEX_GOALS_DB_CANDIDATES
        sid = "019f0000-test-goal"
        with tempfile.TemporaryDirectory() as td:
            db = pathlib.Path(td) / "goals_1.sqlite"
            server.CODEX_GOALS_DB_CANDIDATES = (db,)
            try:
                set_res = server._codex_goal_store_update(sid, "set", objective="ship the thing")
                self.assertTrue(set_res["ok"], set_res)
                snap = server._codex_goals_snapshot()
                self.assertEqual(snap[sid]["objective"], "ship the thing")
                self.assertEqual(snap[sid]["status"], "active")

                pause_res = server._codex_goal_store_update(sid, "pause")
                self.assertTrue(pause_res["ok"], pause_res)
                self.assertEqual(server._codex_goals_snapshot()[sid]["status"], "paused")

                resume_res = server._codex_goal_store_update(sid, "resume")
                self.assertTrue(resume_res["ok"], resume_res)
                self.assertEqual(server._codex_goals_snapshot()[sid]["status"], "active")

                clear_res = server._codex_goal_store_update(sid, "clear")
                self.assertTrue(clear_res["ok"], clear_res)
                self.assertNotIn(sid, server._codex_goals_snapshot())
            finally:
                server.CODEX_GOALS_DB_CANDIDATES = old_candidates
                server._invalidate_codex_goals_cache()

    def test_claude_goal_command_stamps_goal_fields(self):
        """Claude slash-command goal state should be promoted to row fields,
        including headless sessions whose only visible record is JSONL."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "goal.jsonl"
            events = [
                {
                    "type": "user",
                    "isMeta": True,
                    "timestamp": "2026-06-24T18:10:00.000Z",
                    "message": {
                        "content": (
                            "<command-message>goal</command-message>\n"
                            "<command-name>/goal</command-name>\n"
                            "<command-args>- get clear folder with screenshots</command-args>"
                        )
                    },
                },
                {
                    "type": "user",
                    "timestamp": "2026-06-24T18:11:00.000Z",
                    "message": {"content": "normal follow-up"},
                },
                {
                    "type": "user",
                    "isMeta": True,
                    "timestamp": "2026-06-24T18:12:00.000Z",
                    "message": {
                        "content": (
                            "<command-message>goal</command-message>\n"
                            "<command-name>/goal</command-name>\n"
                            "<command-args>pause</command-args>"
                        )
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n")

            meta = server._extract_tail_meta(path)

        self.assertEqual(meta["goal"], "get clear folder with screenshots")
        self.assertEqual(meta["goal_status"], "paused")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn('"goal": tail_meta.get("goal") or "",', server_py)
        self.assertIn('"goal_status": tail_meta.get("goal_status") or "",', server_py)

    def test_live_question_indicator_renders_prompt_and_options(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function liveQuestionDetailHtml", app_js)
        self.assertIn("liveStatus.questionText", app_js)
        self.assertIn("questionPreamble", app_js)
        self.assertIn("question_preamble", app_js)
        self.assertIn("questionOptionDetails", app_js)
        self.assertIn("question_option_details", app_js)
        self.assertIn("liveQuestionOptionParts", app_js)
        self.assertIn("liveQuestionDisplayOptions", app_js)
        self.assertIn("handleLiveQuestionActionClick", app_js)
        self.assertIn("data-live-question-action", app_js)
        self.assertIn("sendToTerminal(paneId || activePaneId(), 'answer')", app_js)
        self.assertIn("liveStatus.questionWaiting", app_js)
        self.assertIn("injectMode = 'answer';", app_js)
        self.assertIn("Type something", app_js)
        self.assertIn("Chat about this", app_js)
        self.assertIn("cl-question-options", app_js)
        self.assertIn(".conv-live-tool-inline .cl-question-detail", app_css)
        self.assertIn(".conv-live-tool-inline .cl-question-preamble", app_css)
        self.assertIn(".conv-live-tool-inline .cl-question-options", app_css)
        self.assertIn("flex-direction: column", app_css)
        self.assertIn("cl-question-option-btn", app_css)
        self.assertIn("cl-question-option-desc", app_css)

    def test_live_refresh_has_active_group_chat_pill(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("activeGroupChatPill", index_html)
        self.assertIn("Active Group chat", index_html)
        self.assertIn(".active-group-chat-pill", app_css)
        self.assertIn("function updateActiveGroupChatPill", app_js)
        self.assertIn("function openActiveGroupChatPillTarget", app_js)
        self.assertIn("orchestrator_timer_active", app_js)
        self.assertIn("orchestrator_last_trigger_at", server_py)

    def test_codex_steer_button_is_distinct_from_send(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        self.assertIn('id="convSteerBtn"', index_html)
        self.assertIn(".conv-input-bar .steer-btn", app_css)
        self.assertIn("sendToTerminal('p1', 'steer')", app_js)
        self.assertIn("mode: injectMode", app_js)
        self.assertIn("function codexTurnSteerable()", app_js)
        self.assertIn("&& codexTurnSteerable();", app_js)
        self.assertIn("function codexSteerUnavailable(data)", app_js)
        self.assertNotIn("if (injectMode === 'send' && currentSession.source === 'codex' && codexTurnSteerable())", app_js)
        send_handler = app_js[
            app_js.index("async function sendToTerminal"):
            app_js.index("function insertPendingSpawnCard")
        ]
        self.assertNotIn("postInjectInput(sid, text, 'send', { announcedFrom })", send_handler)

    def test_announced_sender_is_api_only(self):
        """Injected sender attribution remains available to API callers
        without exposing a brittle manual composer field."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")

        self.assertNotIn('id="convAnnouncedFrom"', index_html)
        self.assertIn("announced_from key", index_html)
        self.assertIn("function announcedFromForPane", app_js)
        self.assertIn("payload.announced_from", app_js)
        self.assertIn("announcedInjectionPreview(text, announcedFrom)", app_js)
        self.assertIn(".conv-announced-from", app_css)

    def test_codex_user_messages_have_inline_steer_action(self):
        """Codex user-message bubbles should be steerable in place."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("function userMessageSteerHtml(text, notification, compactCardHtml)", app_js)
        self.assertIn('data-steer-user-message', app_js)
        self.assertIn("postInjectInput(sid, text, 'steer')", app_js)
        inline_handler = app_js[
            app_js.index("const btn = ev.target.closest('[data-steer-user-message]')"):
            app_js.index("function setCurrentSession", app_js.index("const btn = ev.target.closest('[data-steer-user-message]')"))
        ]
        self.assertNotIn("postInjectInput(sid, text, 'send')", inline_handler)
        self.assertIn("syncUserMessageSteerButtons", app_js)
        self.assertIn('hidden disabled aria-hidden="true"', app_js)
        self.assertIn("btn.hidden = !steerable;", app_js)
        self.assertIn("Steered running Codex turn.", app_js)
        self.assertIn("markPendingSendDelivered(pendingSend, data);", app_js)
        self.assertIn("Steered into the running Codex turn.", app_js)
        self.assertIn("div.classList.add('has-user-steer')", app_js)
        self.assertIn(".conversations-view .event.user_text.has-user-steer .user-msg", app_css)
        self.assertIn(".user-message-steer", app_css)

    def test_cursor_engine_is_wired_in_static_ui(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        self.assertIn('<option value="cursor">cursor</option>', index_html)
        self.assertIn('<option value="cursor">Cursor</option>', index_html)
        self.assertIn("'cursor', 'antigravity'", app_js)
        self.assertIn("/api/sessions/spawn-cursor", app_js)
        self.assertIn("Auto (default)", app_js)
        self.assertIn("composer-2.5-fast", app_js)
        self.assertIn("renderCursorLogHtml", app_js)
        self.assertIn("function isCursorUsageLimitFailure", app_js)
        self.assertIn("Cursor usage limit hit. Cursor says:", app_js)
        self.assertIn(".source-badge.cursor", app_css)
        self.assertIn(".event.system.send-failure", app_css)

    def test_hermes_history_is_wired_in_static_ui(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("const isHermesRow = c.source === 'hermes' || c.engine === 'hermes';", app_js)
        self.assertIn("is-hermes-session", app_js)
        self.assertIn("Resume Hermes and send...", app_js)
        self.assertIn("hermes-resume", app_js)
        self.assertIn("scheduleFireAndWatchRefresh", app_js)
        self.assertIn("currentSession.source !== 'hermes'", app_js)
        self.assertIn("hermes_lineage", app_js)
        self.assertIn("conv-signal hermes-platform", app_js)
        self.assertIn(".conv-session-icon.hermes", app_css)
        self.assertIn(".conv-item .source-badge.hermes", app_css)
        self.assertIn(".event.system.system-hermes", app_css)
        # Agentic-vs-chat chip: tool-using Hermes sessions get a distinct chip
        # from plain conversations.
        self.assertIn("hermes_tool_calls", app_js)
        self.assertIn("conv-signal hermes-agent", app_js)
        self.assertIn("conv-signal hermes-chat", app_js)
        self.assertIn(".conv-signal.hermes-agent", app_css)
        self.assertIn(".conv-signal.hermes-chat", app_css)
        self.assertIn("hermes_tool_calls: Number(c.hermes_tool_calls || c.tool_call_count || 0)", app_js)
        # Injected system-prompt panel in the Hermes reader.
        self.assertIn("hermes_system_prompt", app_js)
        self.assertIn("hermes-sysprompt-body", app_js)
        self.assertIn(".hermes-sysprompt-body", app_css)

    def test_hermes_system_prompt_surfaced_as_event(self):
        """The injected per-session system prompt must surface as a
        hermes_system_prompt system event at the top of the parsed Hermes
        conversation, so CCC can render the otherwise-invisible priming layer."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "20260601_120000_sysp"
        prompt_text = "# Hermes Agent Persona\nYou are helpful.\nMEMORY: user is Elad."
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / "state.db"
            gateway = root / "sessions" / "sessions.json"
            gateway.parent.mkdir(parents=True)
            gateway.write_text("{}")
            con = sqlite3.connect(db)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY, source TEXT, user_id TEXT,
                        model TEXT, system_prompt TEXT, title TEXT,
                        started_at REAL, ended_at REAL, parent_session_id TEXT,
                        message_count INTEGER, tool_call_count INTEGER,
                        cwd TEXT, archived INTEGER
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                        role TEXT, content TEXT, tool_call_id TEXT,
                        tool_calls TEXT, tool_name TEXT, timestamp REAL,
                        token_count INTEGER, finish_reason TEXT,
                        reasoning TEXT, active INTEGER
                    );
                    CREATE VIRTUAL TABLE messages_fts USING fts5(content);
                """)
                con.execute(
                    "INSERT INTO sessions (id, source, model, system_prompt, started_at, cwd, archived) VALUES (?,?,?,?,?,?,0)",
                    (sid, "whatsapp", "m", prompt_text, 1780315200.0, ""),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?,?,?,?,1)",
                    (sid, "user", "hi", 1780315210.0),
                )
                con.commit()
            finally:
                con.close()

            orig_db = server.HERMES_STATE_DB
            orig_gateway = server.HERMES_GATEWAY_SESSIONS
            server.HERMES_STATE_DB = db
            server.HERMES_GATEWAY_SESSIONS = gateway
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._HERMES_GATEWAY_CACHE["key"] = None
            server._HERMES_GATEWAY_CACHE["by_session"] = {}
            try:
                parsed = server._parse_hermes_conversation(sid, after_line=0)
                events = parsed["events"]
                sp = [e for e in events if e.get("subtype") == "hermes_system_prompt"]
                self.assertEqual(len(sp), 1, "exactly one system-prompt event expected")
                self.assertEqual(sp[0]["text"], prompt_text)
                self.assertEqual(sp[0]["char_count"], len(prompt_text))
                self.assertEqual(sp[0]["type"], "system")
                # The turn-summary banner is line 1; the system prompt follows
                # right after it at the top of the transcript.
                self.assertEqual(events[0]["subtype"], "hermes_turn_summary")
                self.assertEqual(sp[0]["line"], 2)
                # Incremental polls past the prompt must NOT repeat the big prompt.
                later = server._parse_hermes_conversation(sid, after_line=sp[0]["line"])
                self.assertFalse(
                    any(e.get("subtype") == "hermes_system_prompt" for e in later["events"]),
                    "system prompt must not repeat on incremental polls")
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_GATEWAY_SESSIONS = orig_gateway
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._HERMES_GATEWAY_CACHE["key"] = None
                server._HERMES_GATEWAY_CACHE["by_session"] = {}

    def test_hermes_decision_summary_distils_structured_reply(self):
        """A JSON-mode router/classifier reply gets a one-line gist; plain text
        and purely-nested objects get nothing."""
        import server
        f = server._hermes_decision_summary
        s = f('{"intent":"work_request","complexity":"nontrivial","confidence":0.95,'
              '"addressed_to":"becky","reply":"","request_text":"a fairly long value '
              'that exceeds the per-field cap and is skipped"}')
        self.assertIn("→ work_request", s)
        self.assertIn("95%", s)
        self.assertIn("to: becky", s)
        # Long scalar fields are skipped so the line stays legible.
        self.assertNotIn("fairly long value", s)
        # Confidence already on a 0-1 scale renders as a percentage.
        self.assertIn("40%", f('{"intent":"conversation","confidence":0.4}'))
        # Not a JSON object -> no summary.
        self.assertEqual(f("just plain text"), "")
        self.assertEqual(f('{"meta":{"nested":1}}'), "")

    def test_sidebar_filter_matches_hermes_platform_metadata(self):
        """The local sidebar filter must match Hermes engine/platform/model
        metadata so `cli`, `whatsapp`, `cron`, and `hermes` all surface the
        right rows. Isolate the filterConversations() body so a stray match
        elsewhere in app.js can't satisfy the assertion."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        start = app_js.index("function filterConversations(q) {")
        # The function ends at the closing of its returned/sorted block; grab a
        # generous slice and stop at the next top-level function declaration.
        body = app_js[start:start + 4000]
        for field in (
            "c.engine",
            "c.model",
            "c.source_platform",
            "c.hermes_source",
            "c.hermes_origin",
            "c.hermes_chat_type",
        ):
            self.assertIn(field, body, f"filterConversations should search {field}")

    def test_visible_search_results_keep_session_name_matches_first(self):
        """The final sidebar render must not let UUID ranking bury a title hit."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        start = app_js.index("function renderConversationList(convs) {")
        body = app_js[start:start + 900]
        self.assertIn("convs = _prioritizeNameMatches(", body)
        self.assertIn("_prioritizeSessionIdMatches(convs, document.getElementById('convSearch')?.value || '')", body)

    def test_archive_view_search_matches_hermes_platform_metadata(self):
        """The all-repos archive view (renderArchiveList) has its own inline
        search separate from filterConversations. It must also match Hermes
        platform/model metadata so `cli`/`whatsapp`/`cron` surface Hermes rows
        in the default (all-repos) view, not just single-repo mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        start = app_js.index("function renderArchiveList(filter, opts) {")
        body = app_js[start:start + 8000]
        for field in ("c.source_platform", "c.hermes_source", "c.model", "c.engine"):
            self.assertIn(field, body, f"renderArchiveList search should include {field}")

    def test_hermes_rows_exempt_from_recency_windows(self):
        """Hermes rows are first-class / always-visible: they must be exempt
        from every client-side recency window (global recency, In Progress
        window, archive window) the same way pinned rows are. Otherwise old
        Hermes conversations silently vanish from the default view even though
        the API returns them."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # All three window filters use the hermes exemption clause.
        self.assertIn(
            "row.pinned || row.source === 'hermes' || row.engine === 'hermes'",
            app_js, "archive window must exempt Hermes rows")
        self.assertIn(
            "showRecentOnly && c.source !== 'hermes' && c.engine !== 'hermes'",
            app_js, "global recency filter must exempt Hermes rows")

    def test_cursor_sidebar_visibility_rejects_bad_input(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        # Empty session id
        self.assertFalse(server._ensure_cursor_session_visible(""))

        # Non-UUID session id
        self.assertFalse(server._ensure_cursor_session_visible("not-a-uuid"))

        # Valid UUID but no cwd/spawn_entry
        self.assertFalse(server._ensure_cursor_session_visible("00000000-0000-4000-8000-000000000001"))

    def test_ensure_cursor_session_visible_creates_store_db(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(server.Path, "home", return_value=pathlib.Path(td)):
                sid = "00000000-0000-4000-8000-000000000001"
                spawn_entry = {
                    "cwd": td,
                    "name": "Test Cursor Session",
                    "started": "20260601T120000",
                }
                res = server._ensure_cursor_session_visible(sid, spawn_entry=spawn_entry)
                self.assertTrue(res)

                import hashlib
                project_hash = hashlib.md5(str(pathlib.Path(td).resolve()).encode("utf-8")).hexdigest()
                db_path = pathlib.Path(td) / ".cursor" / "chats" / project_hash / sid / "store.db"
                self.assertTrue(db_path.is_file())

                import sqlite3
                conn = sqlite3.connect(str(db_path))
                row = conn.execute("SELECT value FROM meta WHERE key = '0'").fetchone()
                conn.close()
                self.assertIsNotNone(row)
                data = json.loads(bytes.fromhex(row[0]).decode("utf-8"))
                self.assertEqual(data["agentId"], sid)
                self.assertEqual(data["name"], "Test Cursor Session")

    def test_ensure_cursor_session_visible_registers_composer(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(server.Path, "home", return_value=pathlib.Path(td)):
                import urllib.parse

                # Mock platforms to Darwin so it uses standard macOS App Support dir in tests
                with mock.patch("sys.platform", "darwin"):
                    # Create workspaceStorage and workspace.json
                    ws_dir = pathlib.Path(td) / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage" / "test-workspace-id"
                    ws_dir.mkdir(parents=True, exist_ok=True)

                    ws_json = ws_dir / "workspace.json"
                    project_dir = pathlib.Path(td) / "my-project"
                    project_dir.mkdir(parents=True, exist_ok=True)

                    with open(ws_json, "w", encoding="utf-8") as f:
                        json.dump({"folder": project_dir.as_uri()}, f)

                    ws_db = ws_dir / "state.vscdb"
                    import sqlite3
                    conn = sqlite3.connect(str(ws_db))
                    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
                    # Seed with some existing composer data
                    conn.execute(
                        "INSERT INTO ItemTable (key, value) VALUES ('composer.composerData', ?)",
                        (json.dumps({"allComposers": []}),)
                    )
                    conn.commit()
                    conn.close()

                    # Create globalStorage and state.vscdb
                    global_dir = pathlib.Path(td) / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage"
                    global_dir.mkdir(parents=True, exist_ok=True)
                    global_db = global_dir / "state.vscdb"
                    conn = sqlite3.connect(str(global_db))
                    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
                    conn.execute(
                        "INSERT INTO ItemTable (key, value) VALUES ('composer.composerHeaders', ?)",
                        (json.dumps([]),)
                    )
                    conn.commit()
                    conn.close()

                    sid = "00000000-0000-4000-8000-000000000001"
                    spawn_entry = {
                        "cwd": str(project_dir),
                        "name": "Test Cursor Session",
                        "started": "20260601T120000",
                    }
                    res = server._ensure_cursor_session_visible(sid, spawn_entry=spawn_entry)
                    self.assertTrue(res)

                    # Assert workspace db updated
                    conn = sqlite3.connect(str(ws_db))
                    row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
                    conn.close()
                    self.assertIsNotNone(row)
                    ws_data = json.loads(row[0])
                    self.assertEqual(len(ws_data["allComposers"]), 1)
                    self.assertEqual(ws_data["allComposers"][0]["composerId"], sid)
                    self.assertEqual(ws_data["allComposers"][0]["name"], "Test Cursor Session")

                    # Assert global db updated
                    conn = sqlite3.connect(str(global_db))
                    row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'").fetchone()
                    conn.close()
                    self.assertIsNotNone(row)
                    global_data = json.loads(row[0])
                    self.assertEqual(len(global_data), 1)
                    self.assertEqual(global_data[0]["composerId"], sid)
                    self.assertEqual(global_data[0]["workspaceIdentifier"]["id"], "test-workspace-id")

    def test_queued_send_echo_self_heals_on_real_event(self):
        """A queued send echo ('⏳ Queued…') has the `pending` class removed, so
        the user_text reconciliation must clear `.send-queued` (and the other
        echo states) by text match when the real event lands — otherwise the
        note sticks forever once the _pendingSends entry is lost (the 'stuck at
        queued' annotation)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # The reconciliation DOM-scan must cover the non-pending echo states.
        self.assertIn(".event.user_text.send-queued", app_js)
        self.assertIn(".event.user_text.send-delivered", app_js)
        self.assertIn(".event.user_text.not-acknowledged", app_js)

    def test_queued_steer_candidates_stay_above_the_composer(self):
        """Queued input is a steer candidate, not history that later events bury."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function syncQueuedSteerTray", app_js)
        self.assertIn("queued-steer-tray", app_js)
        self.assertIn("inputBar.insertBefore(tray, inputBar.firstChild)", app_js)
        self.assertIn("el.dataset.queuedSteerServer === 'true'", app_js)
        self.assertIn("el.classList.contains('send-queued')", app_js)
        self.assertIn("data-steer-queued-message", app_js)
        self.assertIn("el.appendChild(steer)", app_js)
        self.assertIn("data-cancel-queued-message", app_js)
        self.assertIn("el.appendChild(cancel)", app_js)
        cancel_handler = app_js[
            app_js.index("const btn = ev.target.closest('[data-cancel-queued-message]')"):
            app_js.index("const btn = ev.target.closest('[data-steer-queued-message]')")
        ]
        self.assertIn("'/api/pending-input/cancel'", cancel_handler)
        self.assertIn("if (row && row._pendingRef) removePendingSendEcho(row._pendingRef)", cancel_handler)
        self.assertIn("else if (row) row.remove()", cancel_handler)
        queued_handler = app_js[
            app_js.index("const btn = ev.target.closest('[data-steer-queued-message]')"):
            app_js.index("const btn = ev.target.closest('[data-steer-user-message]')")
        ]
        self.assertIn("postInjectInput(sid, text, 'steer', { replaceQueued: true })", queued_handler)
        self.assertNotIn("postInjectInput(sid, text, 'send')", queued_handler)
        self.assertIn("tray.dataset.conversationId", app_js)
        self.assertIn("replace_queued", app_js)
        self.assertIn("is-queued-steer-duplicate", app_js)
        self.assertIn(".event.user_text.is-queued-steer-duplicate", app_css)
        self.assertIn(".queued-steer-tray .msg-image", app_css)
        self.assertIn(".queued-steer-tray .send-queued-steer {", app_css)
        self.assertIn(".queued-steer-tray .cancel-queued-message", app_css)
        self.assertIn("position: absolute;", app_css)
        self.assertIn("top: 6px;", app_css)
        self.assertIn("right: 8px;", app_css)
        self.assertIn(".queued-steer-tray .event.user_text {", app_css)
        self.assertIn("background: rgba(63, 185, 80, 0.045);", app_css)

    def test_codex_app_queued_send_marks_pending_echo_queued(self):
        """Codex app-server queue ACKs must not leave the optimistic echo pending."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        branch = app_js[
            app_js.index("if (data.via === 'codex-app-queued') {"):
            app_js.index("} else if (data.queued && data.cwd_missing)", app_js.index("if (data.via === 'codex-app-queued') {"))
        ]
        self.assertIn("markPendingSendQueued(pendingSend,", branch)
        self.assertIn("Queued for Codex", branch)

    def test_pending_spawn_timeout_stays_visible(self):
        """A Claude pending-spawn placeholder that never materializes must not
        vanish silently. It should turn into a visible failed/not-acknowledged
        card with retry/dismiss actions."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function markPendingSpawnNotAcknowledged", app_js)
        self.assertIn("Spawn was not acknowledged within 30s", app_js)
        # CCC-462: the fixed 30s timer was replaced by a registration watch
        # that forces fresh archive fetches (the stale_ok poll cadence could
        # never confirm a spawn inside 30s) and only marks failure after a
        # fresh fetch past the deadline still has no matching row.
        self.assertIn("_watchPendingSpawnRegistration(pid, id)", app_js)
        self.assertIn("markPendingSpawnNotAcknowledged(pid, fallbackId)", app_js)
        self.assertIn("refreshArchiveData({ force: true })", app_js)
        self.assertIn("c.pending_spawn || c.spawn_failed", app_js)
        self.assertIn("data-pending-spawn-retry", app_js)
        self.assertIn("data-pending-spawn-dismiss", app_js)
        self.assertIn("const spawnFailed = c.spawn_failed ? ' spawn-failed' : '';", app_js)

    def test_spawn_adoption_accepts_spawn_id_without_pid(self):
        """App-server Codex spawns use synthetic spawn ids rather than OS pids."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("adoptPendingSpawnPid(tempPid, data.spawn_id || data.pid, data.log, data.session_id)", app_js)
        self.assertIn("const realSpawnId = data.spawn_id || data.pid;", app_js)
        self.assertIn("insertPendingSpawnCard(data.spawn_id || data.pid, subject", app_js)

    def test_codex_spawn_placeholder_matches_returned_session_id(self):
        """A real app-server row must replace its optimistic card without a duplicate flash."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("placeholder.expected_session_id = sessionId || ''", app_js)
        self.assertIn("row.session_id && placeholder.expected_session_id", app_js)
        self.assertIn("String(row.session_id) === String(placeholder.expected_session_id)", app_js)
        self.assertIn("adoptPendingSpawnPid(tempPid, data.spawn_id || data.pid, data.log, data.session_id)", app_js)

    def test_slash_command_args_surface_in_user_text(self):
        """A /command user turn must render "/cmd <args>", not a bare "/cmd".
        Claude Code wraps the typed arguments in a <command-args> tag; dropping
        it (e.g. the goal text after "/goal") leaves the user with no record of
        what they asked the command to do."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        ev = {"type": "user", "message": {"role": "user", "content":
              "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n"
              "<command-args>fix all issues in CCC-* queue.</command-args>"}}
        out = server._parse_conversation_event(ev, 5)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "user_text")
        self.assertEqual(out["text"], "/goal fix all issues in CCC-* queue.")
        # Bare command (no args) still renders just the command name.
        ev2 = {"type": "user", "message": {"role": "user", "content":
               "<command-name>/compact</command-name>\n<command-message>compact</command-message>"}}
        out2 = server._parse_conversation_event(ev2, 6)
        self.assertEqual(out2["text"], "/compact")


class TestPrStateResolution(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.server._PR_STATE_CACHE.clear()

    def tearDown(self):
        self.server._PR_STATE_CACHE.clear()

    def test_pr_state_falls_back_to_gh_api(self):
        url = f"https://github.com/octo-org/demo-repo/pull/{25}"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            if cmd[1] == "api":
                self.assertIn("repos/octo-org/demo-repo/pulls/25", cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="MERGED\n", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fake_run):
            self.assertEqual(self.server._get_pr_state(url), "MERGED")
            self.assertEqual(self.server._get_pr_state(url), "MERGED")

        self.assertEqual(len(calls), 2, "second lookup should hit cache")
        cached = self.server._PR_STATE_CACHE[url]
        self.assertEqual(cached["state"], "MERGED")
        self.assertEqual(cached["ttl"], self.server._PR_STATE_TTL)

    def test_pr_state_failures_use_short_ttl(self):
        url = f"https://github.com/octo-org/demo-repo/pull/{25}"

        def fail_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fail_run):
            self.assertIsNone(self.server._get_pr_state(url))

        cached = self.server._PR_STATE_CACHE[url]
        self.assertIsNone(cached["state"])
        self.assertEqual(cached["ttl"], self.server._PR_STATE_FAILURE_TTL)


class TestRunScript(unittest.TestCase):
    def test_run_script_syntax_is_valid(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", "-n", str(script)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_script_help_advertises_launchd_service(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", str(script), "--help"],
                                cwd=PROJECT_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--install-service", result.stdout)
        self.assertIn("--uninstall-service", result.stdout)
        self.assertIn("--service-status", result.stdout)

    def test_run_script_help_mentions_systemd_for_linux(self):
        """--install-service now ports to systemd on Linux; --help should say so."""
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", str(script), "--help"],
                                cwd=PROJECT_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("systemd", result.stdout)

    def test_windows_run_script_exists(self):
        script = pathlib.Path(PROJECT_ROOT, "run.ps1")
        text = script.read_text(encoding="utf-8")
        self.assertIn("server.py", text)
        self.assertIn("Resolve-Python", text)
        self.assertIn("--app", text)
        self.assertIn("--service-status", text)

    def test_windows_installer_exists(self):
        script = pathlib.Path(PROJECT_ROOT, "scripts", "install.ps1")
        text = script.read_text(encoding="utf-8")
        self.assertIn("claude-command-center", text)
        self.assertIn("git clone", text)
        self.assertIn("run.ps1", text)


class TestPlatformDocs(unittest.TestCase):
    def test_readme_documents_native_windows_and_wsl2_routes(self):
        readme = pathlib.Path(PROJECT_ROOT, "README.md").read_text(encoding="utf-8")

        self.assertIn("Running on Windows", readme)
        self.assertIn("install.ps1", readme)
        self.assertIn(".\\run.ps1", readme)
        self.assertIn("WSL2", readme)
        self.assertIn("systemd", readme)

    def test_installer_points_windows_users_to_windows_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_uname = pathlib.Path(tmpdir, "uname")
            fake_uname.write_text("#!/bin/sh\nprintf 'MINGW64_NT-10.0\\n'\n", encoding="utf-8")
            fake_uname.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{tmpdir}{os.pathsep}{env.get('PATH', '')}"
            result = subprocess.run(
                ["bash", "-c", "source scripts/install.sh; require_supported_platform"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Windows", result.stderr)
        self.assertIn("install.ps1", result.stderr)
        self.assertIn("WSL2", result.stderr)


class TestLinuxCapabilities(unittest.TestCase):
    """Headless-Linux support: macOS-only desktop features must stub cleanly
    (no crash, structured no-op) and the server must report a capabilities
    flag so the UI can hide dead controls. See docs/linux-support-plan.md."""

    def _server(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        return importlib.import_module("server")

    def test_capabilities_all_true_on_darwin(self):
        server = self._server()
        with mock.patch.object(server.platform, "system", return_value="Darwin"):
            caps = server._platform_capabilities()
        self.assertEqual(caps["platform"], "darwin")
        for key in ("screenshots", "annotate", "terminalJump", "launchTerminal",
                    "folderPicker", "desktopDeepLinks", "revealFile",
                    "openBrowser", "notifications"):
            self.assertTrue(caps[key], f"{key} should be True on Darwin")

    def test_capabilities_hide_native_desktop_features_on_linux(self):
        server = self._server()
        with mock.patch.object(server.platform, "system", return_value="Linux"):
            caps = server._platform_capabilities()
        self.assertEqual(caps["platform"], "linux")
        self.assertTrue(caps["annotate"], "page annotations should be cross-platform")
        for key in ("screenshots", "terminalJump", "launchTerminal",
                    "folderPicker", "desktopDeepLinks", "revealFile",
                    "openBrowser", "notifications"):
            self.assertFalse(caps[key], f"{key} should be False on Linux")

    def test_app_config_exposes_capabilities(self):
        server = self._server()
        server._app_config_cache = None
        server._app_config_cache_ts = 0
        cfg = server.get_app_config()
        self.assertIn("capabilities", cfg)
        self.assertIsInstance(cfg["capabilities"], dict)
        self.assertIn("screenshots", cfg["capabilities"])

    def test_desktop_features_stub_cleanly_on_linux(self):
        """Each gated entry point returns a structured no-op on non-Darwin
        instead of raising or shelling out to a missing macOS tool."""
        server = self._server()
        with mock.patch.object(server.platform, "system", return_value="Linux"), \
             mock.patch.object(server.sys, "platform", "linux"):
            for result in (
                server._native_pick_folder(),
                server._capture_screenshot_native(),
                server._reveal_bug_screenshot("/tmp/x.png"),
                server.launch_terminal_for_session("sess-1"),
                server.focus_terminal_by_tty("ttys001", "Terminal"),
            ):
                self.assertIsInstance(result, dict)
                self.assertFalse(result.get("ok", False))
                self.assertIn("error", result)

    def test_sys_memory_and_cpu_work_on_linux(self):
        """The system-monitor stats must not go blank on Linux: memory comes
        from /proc/meminfo and load/cores from stdlib."""
        server = self._server()
        with mock.patch.object(server.platform, "system", return_value="Linux"):
            mem = server._sys_memory()
            cpu = server._sys_cpu()
        self.assertIsInstance(mem, dict)
        for key in ("total_mb", "used_mb", "available_mb", "swap_total_mb",
                    "pressure"):
            self.assertIn(key, mem)
        self.assertIsInstance(cpu, dict)
        for key in ("load1", "load5", "load15", "cores"):
            self.assertIn(key, cpu)


class TestRepoContextHelpers(unittest.TestCase):
    # watchtower.{queue,workers,config} bind their store paths as module-level
    # constants from Path.home() at import time (only WATCHTOWER_STORE is read
    # fresh per-call). Setting these env vars alone does nothing unless the
    # modules are also re-imported after the env vars change — see setUp below.
    _WATCHTOWER_ENV_FILES = {
        "WATCHTOWER_STORE": "queues.json",
        "WATCHTOWER_CONFIG_FILE": "queue-config.json",
        "WATCHTOWER_WORKERS_FILE": "workers.json",
        "WATCHTOWER_WORKER_SESSIONS_FILE": "worker-sessions.json",
    }
    _WATCHTOWER_MODULES = (
        "server", "morning", "morning_store", "ux_fixes_queue",
        "watchtower.queue", "watchtower.workers", "watchtower.config",
    )

    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-repo-context-home-")
        self._prev_home = os.environ.get("HOME")
        self._prev_ux_fixes_queue_file = os.environ.get("UX_FIXES_QUEUE_FILE")
        self._prev_watchtower_env = {
            var: os.environ.get(var) for var in self._WATCHTOWER_ENV_FILES
        }
        self._prev_watchtower_stop_signals_dir = os.environ.get(
            "WATCHTOWER_STOP_SIGNALS_DIR"
        )
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        self.ux_fixes_queue_file = pathlib.Path(
            self.tmp_home, ".claude", "command-center", "ux-fixes-queue.json"
        ).resolve()
        os.environ["UX_FIXES_QUEUE_FILE"] = str(self.ux_fixes_queue_file)
        # server._q prefers watchtower.queue when installed. Without pointing
        # ALL of its store/config/workers files at this test's tmp_home,
        # enqueue_annotation_ux_fixes_queue() writes real tickets into the live
        # production queue AND dispatch_after_enqueue() nudges real live
        # workers via the real workers.json (seen live as CCC-1/399..403).
        for var, filename in self._WATCHTOWER_ENV_FILES.items():
            os.environ[var] = str(
                pathlib.Path(self.tmp_home, ".watchtower", filename).resolve()
            )
        os.environ["WATCHTOWER_STOP_SIGNALS_DIR"] = str(
            pathlib.Path(self.tmp_home, ".watchtower", "stop-signals").resolve()
        )
        for mod in self._WATCHTOWER_MODULES:
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.repo = pathlib.Path(self.tmp_home, "demo-repo").resolve()
        self.repo.mkdir()
        (self.repo / ".git").mkdir()

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        if self._prev_ux_fixes_queue_file is None:
            os.environ.pop("UX_FIXES_QUEUE_FILE", None)
        else:
            os.environ["UX_FIXES_QUEUE_FILE"] = self._prev_ux_fixes_queue_file
        for var, prev in self._prev_watchtower_env.items():
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev
        if self._prev_watchtower_stop_signals_dir is None:
            os.environ.pop("WATCHTOWER_STOP_SIGNALS_DIR", None)
        else:
            os.environ["WATCHTOWER_STOP_SIGNALS_DIR"] = self._prev_watchtower_stop_signals_dir
        for mod in self._WATCHTOWER_MODULES:
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def test_valid_repo_path_is_accepted(self):
        self.assertEqual(self.server.resolve_repo_path(str(self.repo)), str(self.repo))

    def test_queue_config_payload_has_safe_defaults_and_normalizes_fields(self):
        """The Queue UI may create a config without requiring every advanced field."""
        config = self.server._queue_config_from_payload({
            "queue": " demo_queue ",
            "auto_drain": True,
            "workers": "3",
            "claim_types": ["bug", "invalid", "feature"],
            "engine": "codex",
            "effort": "max",
        })

        self.assertEqual(config["queue"], "DEMO_QUEUE")
        self.assertTrue(config["config"]["auto_drain"])
        self.assertEqual(config["config"]["desired_workers"], 3)
        self.assertEqual(config["config"]["claim_types"], ["bug", "feature"])
        self.assertEqual(config["config"]["backend"], "file")
        self.assertEqual(config["config"]["engine"], "codex")
        self.assertEqual(config["config"]["effort"], "max")
        self.assertNotIn("repo_path", config["config"])
        cleared = self.server._queue_config_from_payload({"queue": "DEMO_QUEUE", "effort": ""})
        self.assertNotIn("effort", cleared["config"])

    def test_queue_config_payload_rejects_bad_queue_name_and_github_without_repo(self):
        with self.assertRaises(ValueError):
            self.server._queue_config_from_payload({"queue": "not valid!"})
        with self.assertRaises(ValueError):
            self.server._queue_config_from_payload({"queue": "DEMO", "backend": "github"})
        with self.assertRaises(ValueError):
            self.server._queue_config_from_payload({"queue": "DEMO", "effort": "ultra"})

    def test_queue_config_api_creates_a_queue_and_returns_suggestions(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            request = urllib.request.Request(
                base + "/api/queue/config",
                data=json.dumps({"queue": "DEMO", "workers": 2}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                saved = json.loads(response.read().decode("utf-8"))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["config"]["desired_workers"], 2)
            request = urllib.request.Request(base + "/api/queue/config-options", data=b"{}", method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                options = json.loads(response.read().decode("utf-8"))
            self.assertIn("DEMO", [row["queue"] for row in options["queues"]])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_queue_config_options_offer_model_and_github_repo_choices(self):
        """The queue manager has useful selectors before another queue saves them."""
        config_path = self.server._wt_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "GITHUB": {
                "backend": "github",
                "github_repo": "example-org/example-repo",
                "engine": "codex",
                "model": "custom-codex-model",
            },
        }), encoding="utf-8")

        options = self.server._queue_config_options()

        self.assertIn("example-org/example-repo", options["github_repos"])
        self.assertIn("custom-codex-model", options["models_by_engine"]["codex"])
        self.assertIn("gpt-5.5", options["models_by_engine"]["codex"])

    def test_ux_fixes_queue_file_is_isolated_to_test_home(self):
        self.assertEqual(
            self.server.ux_fixes_queue.QUEUE_FILE,
            self.ux_fixes_queue_file,
        )
        # server._q prefers watchtower.queue when it's installed, and that
        # engine's store path comes from $WATCHTOWER_STORE, not QUEUE_FILE —
        # assert against whichever store the active engine actually resolves,
        # so this test can't silently write into the real production queue.
        store_path = pathlib.Path(
            self.server._q.store_path()
            if hasattr(self.server._q, "store_path")
            else self.ux_fixes_queue_file
        )
        result = self.server.enqueue_annotation_ux_fixes_queue(
            "Annotation: isolated", meta={"selector": "#demo-anchor"}
        )
        self.assertTrue(result["ok"])
        self.assertTrue(store_path.exists())

    def test_bym_production_repo_routes_to_bymprod_queue(self):
        self.assertEqual(
            self.server.ux_fixes_queue._project_for(
                repo_path="/tmp/BYM+Finie/apps/bookyourmat"
            ),
            "BYMPROD",
        )
        self.assertEqual(
            self.server.ux_fixes_queue._project_for(source="bym"),
            "BYMPROD",
        )

    def test_repo_path_with_plus_resolves_when_query_decoded_to_space(self):
        """A repo with `+` in its name arrives as a space via URL query-string
        decoding (`+` → ` `). resolve_repo_path() must recover by trying `+`
        variants instead of forcing every caller to encode as %2B."""
        plus_repo = pathlib.Path(self.tmp_home, "BYM+Finie").resolve()
        plus_repo.mkdir()
        (plus_repo / ".git").mkdir()
        # 1. Exact path still works.
        self.assertEqual(self.server.resolve_repo_path(str(plus_repo)), str(plus_repo))
        # 2. The +→space mangled form (what a URL query carrying `+`
        #    produces) resolves to the real repo.
        mangled = str(plus_repo).replace("+", " ")
        self.assertEqual(self.server.resolve_repo_path(mangled), str(plus_repo))
        # 3. Genuinely missing paths still 400.
        with self.assertRaises(self.server.RepoContextError):
            self.server.resolve_repo_path(str(pathlib.Path(self.tmp_home, "no such repo")))

    def test_find_conversations_honors_relocation_budget(self):
        """A repo with many transcripts whose recorded cwd no longer exists
        must NOT spend its entire budget walking the filesystem for every
        dead worktree. With the per-request relocation budget in place (and
        the on-disk cache cold), find_conversations() should return within
        a few seconds even with hundreds of seeded sessions.

        This is the perf guard for the BYM+Finie regression where 128
        missing cwds + per-session os.walk burnt ~40s on every cold scan.
        """
        seed_count = 200
        target_seconds = 3.0  # generous CI bound; warm calls return <2s
        # Build a fake project dir matching the slug encoder.
        slug = self.server._encode_project_slug(self.repo)
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", slug)
        project_dir.mkdir(parents=True, exist_ok=True)
        bogus_cwd = str(pathlib.Path(self.tmp_home, "deleted-worktrees", "no-such"))
        # Each seeded JSONL records a cwd that doesn't exist on disk — the
        # exact shape that used to trigger the expensive relocation walk.
        for i in range(seed_count):
            sid = f"00000000-0000-4000-8000-{i:012d}"
            entry = {
                "type": "user",
                "sessionId": sid,
                "cwd": bogus_cwd,
                "timestamp": "2026-06-01T00:00:00.000Z",
                "gitBranch": "main",
                "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            }
            (project_dir / f"{sid}.jsonl").write_text(
                json.dumps(entry) + "\n",
                encoding="utf-8",
            )
        # Tight budget so the test fails loudly if it ever regresses.
        prev_budget = os.environ.get("CCC_CWD_RELOCATION_BUDGET_S")
        os.environ["CCC_CWD_RELOCATION_BUDGET_S"] = "0.5"
        try:
            start = time.monotonic()
            rows = self.server.find_conversations(
                str(self.repo), include_old=True
            )
            elapsed = time.monotonic() - start
        finally:
            if prev_budget is None:
                os.environ.pop("CCC_CWD_RELOCATION_BUDGET_S", None)
            else:
                os.environ["CCC_CWD_RELOCATION_BUDGET_S"] = prev_budget
        self.assertGreater(
            len(rows), 0,
            "scan should still return rows even when relocation budget trips",
        )
        self.assertLess(
            elapsed, target_seconds,
            f"find_conversations took {elapsed:.2f}s for {seed_count} seeded sessions; budget is {target_seconds}s",
        )

    def test_repo_path_plus_fallback_keeps_real_space_repo(self):
        """A repo with a real space in its name still resolves directly — the
        fallback only kicks in when the as-given path does not exist."""
        space_repo = pathlib.Path(self.tmp_home, "Foo Bar").resolve()
        space_repo.mkdir()
        (space_repo / ".git").mkdir()
        self.assertEqual(self.server.resolve_repo_path(str(space_repo)), str(space_repo))

    def test_session_registry_accepts_native_claude_binary_path(self):
        sid = "00000000-0000-4000-8000-000000000001"
        sessions_dir = pathlib.Path(self.server.SESSIONS_REGISTRY)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "123.json").write_text(json.dumps({
            "pid": 123,
            "sessionId": sid,
            "cwd": str(self.repo),
            "kind": "bg",
        }))
        native_bin = pathlib.Path(
            self.tmp_home,
            ".local",
            "share",
            "claude",
            "versions",
            "2.1.144",
        )

        def fake_run(args, **kwargs):
            if args == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=f"123 {native_bin}\n456 /usr/bin/python3\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            registry = self.server._load_session_registry()

        self.assertIn(sid, registry)
        self.assertEqual(registry[sid]["pid"], 123)

    def test_daemon_socket_allows_claude_tmp_path(self):
        allowed = f"/tmp/cc-daemon-{os.getuid()}/abc/spare/session.pty.sock"
        denied = f"/tmp/not-cc-daemon-{os.getuid()}/session.pty.sock"

        self.assertTrue(self.server._daemon_socket_path_allowed(allowed))
        self.assertFalse(self.server._daemon_socket_path_allowed(denied))

    def test_background_agent_pty_inject_frames_paste_and_submit(self):
        base = pathlib.Path("/tmp", f"cc-daemon-{os.getuid()}")
        base.mkdir(parents=True, exist_ok=True)
        frames = []
        errors = []

        def recv_exact(conn, n):
            chunks = []
            remaining = n
            while remaining:
                chunk = conn.recv(remaining)
                if not chunk:
                    raise EOFError("socket closed")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        with tempfile.TemporaryDirectory(dir=base) as td:
            sock_path = pathlib.Path(td, "test.pty.sock")
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(str(sock_path))
            server_sock.listen(1)
            server_sock.settimeout(2)

            def accept_frames():
                try:
                    conn, _ = server_sock.accept()
                    with conn:
                        conn.settimeout(2)
                        for _ in range(2):
                            header = recv_exact(conn, 5)
                            size = int.from_bytes(header[:4], "big")
                            kind = header[4]
                            frames.append((kind, recv_exact(conn, size)))
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=accept_frames)
            thread.start()
            try:
                # Delivery confirmation polls a real transcript (CCC-113);
                # this test asserts the wire framing only.
                with mock.patch.object(self.server, "_transcript_gains_text", return_value=True):
                    result = self.server._inject_bg_agent_via_pty_socket(
                        {"pid": 123, "sessionId": "sid", "ptySock": str(sock_path)},
                        "hi\x1b\nthere",
                    )
                thread.join(timeout=2)
            finally:
                server_sock.close()

        self.assertFalse(errors)
        self.assertTrue(result["ok"])
        self.assertEqual(frames, [
            (0, b"\x1b[200~hi\nthere\x1b[201~"),
            (0, b"\r"),
        ])

    def test_strips_ccc_session_state_instruction_from_visible_text(self):
        text = (
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this "
            "(the Claude Command Center dashboard parses it):\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        self.assertEqual(
            self.server._strip_ccc_session_state_instruction(text),
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png",
        )

    def test_terminal_inject_strips_ccc_session_state_instruction(self):
        text = (
            "follow up\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this:\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "keystroke"},
             ) as inject:
            result = self.server._inject_text_into_session(
                "00000000-0000-4000-8000-000000000001",
                text,
            )
        self.assertTrue(result["ok"])
        inject.assert_called_once_with("/dev/ttys001", "Terminal", "follow up")

    def test_terminal_inject_queues_when_live_session_is_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": "/dev/ttys001",
                         "terminal_app": "Terminal",
                         "status": "busy",
                         "pid": 123,
                     },
                 ), \
                 mock.patch.object(
                     self.server,
                     "_find_live_spawn_entry_for_session",
                     return_value=None,
                 ), \
                 mock.patch.object(
                     self.server,
                     "_spawn_entry_active_tool_child",
                     return_value={"pid": 23456, "command": "grep -r"},
                 ), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(self.server, "_write_stream_json_user_message") as write:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["via"], "terminal-queued")
            write.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_terminal_question_answer_mode_bypasses_picker_queue(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
                 mock.patch.object(self.server, "_is_hermes_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": "/dev/ttys001",
                         "terminal_app": "Terminal",
                         "status": "waiting",
                     },
                 ), \
                 mock.patch.object(self.server, "_ask_question_blocking_inject", return_value=True), \
                 mock.patch.object(self.server, "_notification_blocks_inject", return_value=True), \
                 mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=True), \
                 mock.patch.object(self.server, "_queue_terminal_input") as queue, \
                 mock.patch.object(
                     self.server,
                     "inject_input_via_keystroke",
                     return_value={"ok": True, "via": "terminal-control"},
                 ) as inject:
                result = self.server._inject_text_into_session(
                    sid,
                    "Per-session override value",
                    mode="answer",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["via"], "terminal-control")
            inject.assert_called_once_with(
                "/dev/ttys001",
                "Terminal",
                "Per-session override value",
            )
            queue.assert_not_called()
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_terminal_question_option_text_bypasses_stale_tab_queue(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
            self.server._pending_terminal_input_queue[sid] = [
                "Repo->code mapping",
                "Repo->code mapping",
                "later follow up",
            ]
        try:
            pending_question = {
                "options": [
                    "Per-session override value",
                    "Re-bucket the session's repo",
                    "Repo->code mapping",
                ],
                "option_details": [
                    {"label": "Repo->code mapping"},
                ],
            }
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
                 mock.patch.object(self.server, "_is_hermes_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": "/dev/ttys001",
                         "terminal_app": "Terminal",
                         "status": "waiting",
                     },
                 ), \
                 mock.patch.object(self.server, "_ask_question_blocking_inject", return_value=True), \
                 mock.patch.object(
                     self.server,
                     "_pending_ask_user_question_for_session",
                     return_value=pending_question,
                 ), \
                 mock.patch.object(self.server, "_notification_blocks_inject", return_value=True), \
                 mock.patch.object(self.server, "_queue_terminal_input") as queue, \
                 mock.patch.object(
                     self.server,
                     "inject_input_via_keystroke",
                     return_value={"ok": True, "via": "terminal-control"},
                 ) as inject:
                result = self.server._inject_text_into_session(
                    sid,
                    "Repo->code mapping",
                    mode="send",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["via"], "terminal-control")
            inject.assert_called_once_with(
                "/dev/ttys001",
                "Terminal",
                "Repo->code mapping",
            )
            queue.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["later follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_compact_inject_delegates_to_compact_helper(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "compact_session_context",
            return_value={"ok": True, "compact": True},
        ) as compact, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server._inject_text_into_session(sid, "/compact")

        self.assertTrue(result["ok"])
        compact.assert_called_once_with(sid, _from_terminal_queue=False)
        resume.assert_not_called()

    def test_compact_live_terminal_submits_slash_command(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                     "status": "idle",
                 },
             ), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=False), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/backup.jsonl") as backup, \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control"},
             ) as inject:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["compact"])
        self.assertEqual(result["backup_path"], "/tmp/backup.jsonl")
        backup.assert_called_once_with(sid)
        inject.assert_called_once_with("/dev/ttys001", "Terminal", "/compact")

    def test_compact_dormant_session_returns_manual_when_hidden_pty_fails(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=False), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/backup.jsonl"), \
             mock.patch.object(
                 self.server,
                 "_compact_via_hidden_pty",
                 return_value={"ok": False, "via": "hidden-pty", "error": "stubbed in test"},
             ), \
             mock.patch.object(
                 self.server,
                 "launch_terminal_for_session",
                 return_value={"ok": True, "terminal_app": "Terminal", "command": "claude --resume ..."},
             ) as launch:
            result = self.server.compact_session_context(sid)

        self.assertFalse(result["ok"])
        self.assertTrue(result["compact"])
        self.assertEqual(result["code"], "compact_needs_manual")
        self.assertEqual(result["via"], "manual")
        self.assertEqual(result["backup_path"], "/tmp/backup.jsonl")
        self.assertEqual(result["fallback_from"], "stubbed in test")
        self.assertIn("run /compact yourself", result["error"])
        launch.assert_not_called()

    def test_compact_rejects_unsupported_engine(self):
        result = None
        with mock.patch.object(self.server, "_detect_session_engine", return_value="cursor"), \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server.compact_session_context("cursor-session")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "compact_unsupported_engine")
        launch.assert_not_called()
        resume.assert_not_called()

    def test_compact_routes_codex_to_app_server(self):
        with mock.patch.object(self.server, "_detect_session_engine", return_value="codex"), \
             mock.patch.object(
                 self.server, "_backup_codex_rollout_before_compact",
                 return_value="/tmp/backup.jsonl",
             ) as backup, \
             mock.patch.object(
                 self.server, "_codex_compact_via_app_server",
                 return_value={"ok": True, "via": "codex-compact", "session_id": "codex-session"},
             ) as compact, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server.compact_session_context("codex-session")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-compact")
        self.assertEqual(result["engine"], "codex")
        self.assertEqual(result["backup_path"], "/tmp/backup.jsonl")
        self.assertTrue(result["compact"])
        backup.assert_called_once_with("codex-session")
        compact.assert_called_once_with("codex-session")
        launch.assert_not_called()
        resume.assert_not_called()

    def test_extract_session_slash_commands_returns_codex_catalog(self):
        with mock.patch.object(self.server, "_detect_session_engine", return_value="codex"):
            result = self.server.extract_session_slash_commands("codex-session")

        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "codex")
        self.assertEqual(result["source"], "codex-fallback")
        names = {cmd["name"] for cmd in result["commands"]}
        self.assertIn("/compact", names)
        self.assertIn("/model", names)
        self.assertIn("/status", names)

    def test_compact_live_headless_spawn_queues_when_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {
            "pid": 12345,
            "log": "spawn.log",
        }
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=spawn), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=True), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input", return_value={"ok": True, "queued": True}) as queue, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject, \
             mock.patch.object(self.server, "_write_stream_json_user_message") as write:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "terminal-queued-headless")
        backup.assert_not_called()
        queue.assert_called_once_with(sid, "/compact", {"pid": 12345, "status": "headless"})
        launch.assert_not_called()
        inject.assert_not_called()
        write.assert_not_called()

    def test_compact_live_tty_plus_headless_spawn_runs_in_terminal(self):
        # Concurrent terminal + headless no longer blocks /compact. With the
        # staleness machinery (GH #71) retiring a stale headless the moment CCC
        # would route to it, /compact runs in the terminal (keystroke) and the
        # headless can't be reused with a pre-compact view. (Previously this
        # rejected with compact_headless_running.)
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                     "pid": 54321,
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=spawn), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=None), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(self.server, "_session_status_is_busy", return_value=False), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/bk.jsonl") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input") as queue, \
             mock.patch.object(self.server, "inject_input_via_keystroke", return_value={"ok": True, "submitted": True}) as inject:
            result = self.server.compact_session_context(sid)

        # Runs /compact in the terminal, not rejected as headless-running.
        self.assertNotEqual(result.get("code"), "compact_headless_running")
        inject.assert_called_once()
        backup.assert_called_once()
        queue.assert_not_called()

    def test_compact_live_no_tty_registry_queues_when_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                     "status": "busy",
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input", return_value={"ok": True, "queued": True}) as queue, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "terminal-queued-headless")
        backup.assert_not_called()
        queue.assert_called_once_with(sid, "/compact", {"pid": 12345, "status": "busy"})
        launch.assert_not_called()
        inject.assert_not_called()

    def test_live_background_agent_injects_via_daemon_pty(self):
        sid = "00000000-0000-4000-8000-000000000001"
        worker = {"pid": 12345, "sessionId": sid, "ptySock": "/tmp/cc-daemon-501/x.sock"}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "kind": "bg",
                     "status": "busy",
                     "job_id": "00000000",
                     "pid": 54324,
                 },
             ), \
             mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=True), \
             mock.patch.object(
                 self.server,
                 "_find_live_bg_agent_entry_for_session",
                 return_value=worker,
             ) as find_worker, \
             mock.patch.object(
                 self.server,
                 "_inject_bg_agent_via_pty_socket",
                 return_value={"ok": True, "via": "bg-agent-pty"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "bg-agent-pty")
        find_worker.assert_called_once_with(sid)
        inject.assert_called_once_with(worker, "follow up", session_id=sid)
        resume.assert_not_called()

    def test_live_background_agent_queues_until_prompt_ready(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": None,
                         "terminal_app": None,
                         "kind": "bg",
                         "status": "busy",
                         "job_id": "00000000",
                         "pid": 54324,
                     },
                 ), \
                 mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=False), \
                 mock.patch.object(self.server, "_inject_bg_agent_via_pty_socket") as inject:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "terminal-queued")
            inject.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_annotation_ux_queue_injects_existing_session(self):
        sid = "00000000-0000-4000-8000-000000000010"
        old_root = self.server.CCC_ROOT
        self.server.CCC_ROOT = self.repo
        try:
            with mock.patch.object(
                self.server,
                "_find_annotation_ux_queue_session",
                return_value={"session_id": sid, "display_name": "UX-fixes-queue"},
            ), mock.patch.object(
                self.server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "spawn-fifo"},
            ) as inject, mock.patch.object(self.server, "spawn_session") as spawn:
                # An annotation with no actionable anchor (no selector, no
                # screenshot, no URL) is rejected before enqueue so junk
                # tickets never reach a worker (CCC-396..402).
                rejected = self.server.enqueue_annotation_ux_fixes_queue(
                    "Annotation: bad pill",
                    inject=True,
                )
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected.get("status"), 400)
                inject.assert_not_called()
                # With an anchor present, the inject path runs as before.
                result = self.server.enqueue_annotation_ux_fixes_queue(
                    "Annotation: bad pill",
                    inject=True,
                    meta={"selector": ".pill"},
                )
        finally:
            self.server.CCC_ROOT = old_root

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "injected")
        self.assertEqual(result["session_id"], sid)
        inject.assert_called_once_with(sid, "Annotation: bad pill")
        spawn.assert_not_called()

    def test_inject_input_wraps_announced_from(self):
        sid = "00000000-0000-4000-8000-000000000020"
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "mock"},
            ) as inject:
                req = urllib.request.Request(
                    base + "/api/inject-input",
                    data=json.dumps({
                        "session_id": sid,
                        "text": "STATUS: done",
                        "announced_from": "Gerry",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))

            self.assertTrue(body["ok"])
            inject.assert_called_once_with(
                sid,
                "Announced from: Gerry\n\nSTATUS: done",
                mode="send",
                wt_origin=False,
                skip_wt=False,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_inject_input_accepts_answer_mode(self):
        sid = "00000000-0000-4000-8000-000000000022"
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "mock"},
            ) as inject:
                req = urllib.request.Request(
                    base + "/api/inject-input",
                    data=json.dumps({
                        "session_id": sid,
                        "text": "Use the selected scope",
                        "mode": "answer",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))

            self.assertTrue(body["ok"])
            inject.assert_called_once_with(
                sid,
                "Use the selected scope",
                mode="answer",
                wt_origin=False,
                skip_wt=False,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_inject_input_threads_wt_origin_marker(self):
        """WT-78: a delegate POST from wt carries origin=wt; the route must
        pass wt_origin=True so the wt-send hook is skipped (loop guard)."""
        sid = "00000000-0000-4000-8000-000000000023"
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "mock"},
            ) as inject:
                req = urllib.request.Request(
                    base + "/api/inject-input",
                    data=json.dumps({
                        "session_id": sid,
                        "text": "delivered by wt delegate",
                        "origin": "wt",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))

            self.assertTrue(body["ok"])
            inject.assert_called_once_with(
                sid,
                "delivered by wt delegate",
                mode="send",
                wt_origin=True,
                skip_wt=False,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_inject_input_rejects_invalid_announced_from(self):
        sid = "00000000-0000-4000-8000-000000000021"
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(self.server, "_inject_text_into_session") as inject:
                req = urllib.request.Request(
                    base + "/api/inject-input",
                    data=json.dumps({
                        "session_id": sid,
                        "text": "hello",
                        "announced_from": "Gerry\nbad",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(req, timeout=5)

            self.assertEqual(err.exception.code, 400)
            body = err.exception.read().decode("utf-8")
            err.exception.close()
            self.assertIn("announced_from", body)
            inject.assert_not_called()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_annotation_ux_queue_spawns_named_session_when_missing(self):
        sid = "00000000-0000-4000-8000-000000000011"
        log_path = pathlib.Path(self.tmp_home, "spawn.log")
        log_path.write_text(json.dumps({"session_id": sid}) + "\n", encoding="utf-8")
        old_root = self.server.CCC_ROOT
        self.server.CCC_ROOT = self.repo
        try:
            with mock.patch.object(
                self.server,
                "_find_annotation_ux_queue_session",
                return_value=None,
            ), mock.patch.object(
                self.server,
                "spawn_session",
                return_value={"ok": True, "pid": 123, "log": str(log_path)},
            ) as spawn:
                result = self.server.enqueue_annotation_ux_fixes_queue(
                    "Annotation: bad pill",
                    inject=True,
                    meta={"selector": ".pill"},
                )
        finally:
            self.server.CCC_ROOT = old_root

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "spawned")
        self.assertEqual(result["session_id"], sid)
        self.assertEqual(
            self.server._load_session_name_overrides().get(sid),
            "UX-fixes-queue",
        )
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], "Annotation: bad pill")
        self.assertEqual(spawn.call_args.kwargs["name"], "UX-fixes-queue")
        self.assertEqual(spawn.call_args.kwargs["repo_path"], str(self.repo))

    def test_codex_live_terminal_injects_via_tty(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "hello")
        resume.assert_not_called()

    def test_codex_live_terminal_falls_back_to_resume_when_keystroke_fails(self):
        # Regression: inject_input_via_keystroke is osascript-only, so it always
        # fails on Linux (no AppleScript driver, terminal_app is None). A live
        # Codex tty send must then fall back to resume delivery instead of
        # returning the failure — otherwise the terminal-queue drain re-parks the
        # message every 60s forever ("Queued: the session is busy").
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": True, "tty": "pts/2", "terminal_app": None},
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": False, "via": "terminal-control", "error": "osascript not found"},
             ) as inject, \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-resume"},
             ) as resume:
            result = self.server._inject_text_into_session(
                sid, "hello", _from_terminal_queue=True
            )

        self.assertTrue(result["ok"])
        inject.assert_called_once()
        resume.assert_called_once_with(sid, "hello")

    def test_codex_writer_snapshot_trusts_idle_status_over_mtime(self):
        # Regression for the false-busy/stuck saga: an idle thread whose rollout
        # mtime is fresh (a mobile/desktop app merely OPENED it) must NOT be
        # attributed to an active external writer once the daemon has reported
        # status "idle" via thread/status/changed. Without this, CCC queued every
        # send forever ("session is busy" that never clears).
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        now = 1_000_000.0
        fresh_rollout = {"path": "/tmp/rollout.jsonl", "mtime_ns": int((now - 1) * 1e9)}
        # Idle status present -> external_active must be False even with fresh mtime.
        snap_idle = self.server._codex_thread_writer_snapshot(
            sid, now=now, rollout=fresh_rollout,
            app_state={"status": "idle"}, attached={}, exec_child=False,
        )
        self.assertFalse(snap_idle["external_active"])
        self.assertIsNone(snap_idle["writer"])
        # No status (thread we haven't heard from) -> mtime fallback still flags
        # a busy turn, but does not invent an external owner.
        snap_unknown = self.server._codex_thread_writer_snapshot(
            sid, now=now, rollout=fresh_rollout,
            app_state={}, attached={}, exec_child=False,
        )
        self.assertTrue(snap_unknown["external_active"])
        self.assertEqual(snap_unknown["writer"], "unknown")

    def test_codex_writer_snapshot_attributes_active_status_by_owner(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        old_rollout = {"path": "/tmp/rollout.jsonl", "mtime_ns": 1}
        unknown = self.server._codex_thread_writer_snapshot(
            sid,
            now=1_000_000.0,
            rollout=old_rollout,
            app_state={
                "status": "active",
                "active_turn_id": "external-turn",
                "active_writer": "external",
            },
            attached={},
            exec_child=False,
        )
        self.assertTrue(unknown["external_active"])
        self.assertEqual(unknown["writer"], "unknown")

        ccc = self.server._codex_thread_writer_snapshot(
            sid,
            now=1_000_000.0,
            rollout=old_rollout,
            app_state={
                "status": "active",
                "active_turn_id": "ccc-turn",
                "active_writer": "ccc",
            },
            attached={},
            exec_child=False,
        )
        self.assertFalse(ccc["external_active"])
        self.assertEqual(ccc["writer"], "ccc")

    def test_codex_slash_idle_terminal_submits_with_return(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "idle",
                 },
             ), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control", "submit_key": "return"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "/status")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "/status", submit_key="return")
        resume.assert_not_called()

    def test_codex_slash_busy_terminal_queues_with_tab(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control", "submit_key": "tab"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "/compact")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "/compact", submit_key="tab")
        resume.assert_not_called()

    def test_codex_slash_without_live_tui_rejects_resume(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(self.server, "resume_session_codex") as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "/status")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "codex_slash_requires_live_tui")
        resume.assert_not_called()
        inject.assert_not_called()

    def test_codex_busy_terminal_routes_to_resume_for_app_queue(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "queued": True, "via": "codex-app-queued"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "codex-app-queued")
        resume.assert_called_once_with(sid, "hello")
        inject.assert_not_called()

    def test_codex_steer_mode_routes_to_resume_steer(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-steer"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello", mode="steer")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        resume.assert_called_once_with(sid, "hello", steer=True)
        inject.assert_not_called()

    def test_codex_steer_unavailable_falls_back_to_send(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False},
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 side_effect=[
                     {"ok": False, "code": "codex_steer_unavailable"},
                     {"ok": True, "via": "codex-app-turn"},
                 ],
             ) as resume:
            result = self.server._inject_text_into_session(
                sid, "continue", mode="steer"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-app-turn")
        self.assertEqual(
            resume.call_args_list,
            [mock.call(sid, "continue", steer=True), mock.call(sid, "continue")],
        )

    def test_codex_steer_failed_does_not_retry_as_send(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False},
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": False, "code": "codex_steer_failed"},
             ) as resume:
            result = self.server._inject_text_into_session(
                sid, "continue", mode="steer"
            )

        self.assertFalse(result["ok"])
        resume.assert_called_once_with(sid, "continue", steer=True)

    def test_codex_without_live_tty_uses_resume(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-resume"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-resume")
        resume.assert_called_once_with(sid, "hello")
        inject.assert_not_called()

    def test_linux_question_mark_tty_routes_claude_to_headless_fifo(self):
        """Linux ps reports no controlling tty as '?', not '??'."""
        sid = "00000000-0000-4000-8000-000000000009"
        spawn = {"pid": 4242, "engine": "claude"}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
             mock.patch.object(self.server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(self.server, "_is_hermes_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": True, "tty": "?", "pid": 4242, "kind": "interactive"},
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=spawn), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=None), \
             mock.patch.object(self.server, "_write_stream_json_user_message", return_value=True) as write, \
             mock.patch.object(self.server, "_update_spawn_transcript_watermark") as watermark, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "spawn-fifo")
        write.assert_called_once_with(spawn, "hello")
        watermark.assert_called_once_with(spawn, sid)
        inject.assert_not_called()

    def test_antigravity_resume_falls_back_to_app_when_cli_missing(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_cli_conversation_path",
            return_value=None,
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_app_conversation_path",
                 return_value=pathlib.Path("/tmp/xxx"),
             ), \
             mock.patch.object(
                 self.server,
                 "_resume_session_antigravity_app",
                 return_value={"ok": True, "via": "antigravity-app"},
             ) as app_resume:
            result = self.server.resume_session_antigravity(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "antigravity-app")
        app_resume.assert_called_once_with(sid, "hello")

    def test_antigravity_app_resume_records_interaction_on_success(self):
        sid = "00000000-0000-4000-8000-000000000001"
        user_config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": True, "config": user_config},
             ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_app_rpc",
                 return_value={"ok": True, "port": 1234},
             ) as rpc, \
             mock.patch.object(self.server, "_record_interaction") as record:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["resumed"])
        self.assertEqual(result["via"], "antigravity-app")
        self.assertEqual(result["port"], 1234)
        record.assert_called_once_with(sid)
        rpc.assert_called_once_with(
            "SendUserCascadeMessage",
            {
                "cascadeId": sid,
                "items": [{"text": "hello"}],
                "cascadeConfig": user_config,
            },
            timeout=10,
        )

    def test_antigravity_app_resume_requires_model_config(self):
        """When trajectory loads but has no model picked, surface the
        'pick a model in Antigravity' error (not the RPC-failure error)."""
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": None},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_model_config_missing")
        rpc.assert_not_called()

    def test_antigravity_app_resume_passes_through_rpc_failure(self):
        """When the trajectory RPC itself failed (app not running, etc.),
        surface the actual RPC error instead of the misleading
        'no reusable model config' message."""
        sid = "00000000-0000-4000-8000-000000000001"
        rpc_failure = {
            "ok": False,
            "error": "Antigravity app language server is not running. Open Antigravity, then retry.",
            "code": "antigravity_app_unavailable",
            "via": "antigravity-app",
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": rpc_failure},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_unavailable")
        rpc.assert_not_called()

    def test_antigravity_latest_user_config_reuses_last_valid_model(self):
        config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        trajectory = {
            "steps": [
                {"userInput": {"userConfig": {"plannerConfig": {}}}},
                {"userInput": {"lastUserConfig": config}},
            ],
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_rpc",
            return_value={"ok": True, "response": {"trajectory": trajectory}},
        ) as rpc:
            result = self.server._antigravity_latest_user_config("sid")

        self.assertTrue(result["ok"])
        self.assertEqual(result["config"], config)
        self.assertIsNot(result["config"], config)
        rpc.assert_called_once_with(
            "GetCascadeTrajectory",
            {"cascadeId": "sid"},
            timeout=5,
        )

    def test_finished_spawn_poll_closes_log_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = 0
        log_fh = mock.Mock()
        entry = {
            "pid": 12345,
            "proc": proc,
            "log_fh": log_fh,
            "fifo": None,
            "stdin_fd": None,
        }

        with mock.patch.object(self.server, "_remove_spawn_from_registry") as remove:
            self.assertEqual(self.server._poll_spawn_entry(entry), 0)

        log_fh.close.assert_called_once()
        remove.assert_called_once_with(12345)
        self.assertIsNone(entry["log_fh"])
        self.assertTrue(entry["_cleanup_done"])

    def test_live_headless_spawn_queues_when_tool_child_running(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": None,
                         "terminal_app": None,
                         "pid": 12345,
                     },
                 ), \
                 mock.patch.object(
                     self.server,
                     "_find_live_spawn_entry_for_session",
                     return_value=spawn,
                 ), \
                 mock.patch.object(
                     self.server,
                     "_spawn_entry_active_tool_child",
                     return_value={"pid": 23456, "command": "grep -r"},
                 ), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(self.server, "_write_stream_json_user_message") as write:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["via"], "terminal-queued")
            write.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_live_headless_spawn_restarts_when_fifo_write_fails(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "_find_live_spawn_entry_for_session",
                 return_value=spawn,
             ), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=None), \
             mock.patch.object(self.server, "_write_stream_json_user_message", return_value=False), \
             mock.patch.object(self.server, "_retire_unresponsive_spawn_entry") as retire, \
             mock.patch.object(
                 self.server,
                 "resume_session_headless",
                 return_value={"ok": True, "pid": 67890, "resumed": True},
             ) as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 67890)
        retire.assert_called_once_with(spawn, terminate=True, reason="write_failed")
        resume.assert_called_once_with(sid, "follow up")

    def test_fifo_writer_open_does_not_block_without_reader(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = pathlib.Path(td) / "stdin.fifo"
            os.mkfifo(fifo, 0o600)
            start = time.monotonic()
            fd = self.server._open_fifo_writer(str(fifo))
            elapsed = time.monotonic() - start

        self.assertIsNone(fd)
        self.assertLess(elapsed, 0.5)

    def test_stream_json_fifo_write_does_not_block_when_pipe_full(self):
        read_fd, write_fd = os.pipe()
        try:
            flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            chunk = b"x" * 8192
            while True:
                try:
                    os.write(write_fd, chunk)
                except BlockingIOError:
                    break
            entry = {"stdin_fd": write_fd, "fifo": None, "proc": None}
            start = time.monotonic()
            ok = self.server._write_stream_json_user_message(entry, "hello")
            elapsed = time.monotonic() - start
            write_fd = None

            self.assertFalse(ok)
            self.assertIsNone(entry["stdin_fd"])
            self.assertLess(elapsed, 0.5)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_stream_json_writer_strips_lone_surrogates(self):
        read_fd, write_fd = os.pipe()
        try:
            entry = {"stdin_fd": write_fd, "fifo": None, "proc": None}
            ok = self.server._write_stream_json_user_message(
                entry,
                "queued annotation " + chr(0xD83D) + " after screenshot",
            )
            self.assertTrue(ok)
            os.close(write_fd)
            write_fd = None

            raw = os.read(read_fd, 65536).decode("utf-8")
            self.assertNotIn("\\u" + "d83d", raw.lower())
            payload = json.loads(raw)
            text = payload["message"]["content"][0]["text"]
            self.assertNotIn(chr(0xD83D), text)
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_terminal_inject_timeout_has_actionable_macos_error(self):
        timeout = subprocess.TimeoutExpired(cmd=["osascript", "-e", "secret"], timeout=5)
        with mock.patch.object(self.server.subprocess, "run", side_effect=timeout):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "macos_automation_timeout")
        self.assertIn("app_mode_loader", result["error"])
        self.assertIn("app_node", result["error"])
        self.assertNotIn("secret", result["error"])

    def test_terminal_inject_return_submits_natively_without_focus(self):
        # Return submits via a second empty `do script` write to the tab —
        # no System Events keystroke, no focus steal (the keystroke path
        # silently landed in the wrong window when activation failed).
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["submitted"])
        script = seen["script"]
        self.assertIn('do script "hello" in foundTab', script)
        self.assertIn('do script "" in foundTab', script)
        self.assertNotIn("System Events", script)
        self.assertNotIn("activate", script)

    def test_terminal_inject_tab_still_restores_focus_by_process_id(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke(
                "/dev/ttys001", "Terminal", "hello", submit_key="tab"
            )

        self.assertTrue(result["ok"])
        script = seen["script"]
        self.assertIn("unix id of first application process whose frontmost is true", script)
        self.assertIn("frontmost of first application process whose unix id is prevPid", script)
        self.assertNotIn("tell application prevApp", script)

    def test_terminal_inject_can_submit_tab(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke(
                "/dev/ttys001",
                "Terminal",
                "/compact",
                submit_key="tab",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["submit_key"], "tab")
        self.assertIn("key code 48", seen["script"])

    def test_live_claude_scan_skips_headless_processes_before_lsof(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:4] == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(args, 0, stdout="100 claude\n101 claude\n102 node\n", stderr="")
            if args == ["ps", "-o", "pid,tty", "-p", "100,101"]:
                return subprocess.CompletedProcess(args, 0, stdout="  PID TTY\n  100 ??\n  101 ttys001\n", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        cwd_calls = []

        def fake_cwd(pid):
            cwd_calls.append(pid)
            return "/tmp/demo"

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.server, "_proc_cwd", side_effect=fake_cwd), \
             mock.patch.object(self.server, "_proc_ancestor_terminal", return_value=("Terminal", 9)):
            procs = self.server.find_live_claude_processes()

        self.assertEqual([p["pid"] for p in procs], [101])
        self.assertEqual(cwd_calls, ["101"])

    def test_ask_user_question_tool_detail_surfaces_prompt(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [
                                {"label": "Full auto", "description": "Run everything without checking back."},
                                {"label": "Half auto", "description": "Ask before the risky bits."},
                                {"label": "Skip Whisper"},
                            ],
                        }]
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 7)

        self.assertEqual(parsed["type"], "assistant")
        detail = parsed["blocks"][0]["detail"]
        self.assertEqual(parsed["blocks"][0]["id"], "toolu-question")
        self.assertIn("How automated do you want this?", detail)
        self.assertIn("Full auto", detail)
        self.assertIn("Half auto", detail)
        rich = parsed["blocks"][0]["question"]["questions"][0]["options"]
        self.assertEqual(rich[0]["description"], "Run everything without checking back.")

    def test_tool_use_marks_input_for_lazy_disclosure(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-07-13T03:25:10Z",
            "message": {
                "id": "msg-tool-search",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-tool-search",
                    "name": "ToolSearch",
                    "input": {
                        "query": "select:PushNotification",
                        "limit": 5,
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 8)

        block = parsed["blocks"][0]
        self.assertEqual(block["detail"], "select:PushNotification")
        self.assertTrue(block["has_input"])
        self.assertNotIn("input", block)

    def test_lazy_tool_input_is_complete_and_redacts_secrets(self):
        secret = "sk-testabcdefghijklmnop"
        ev = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-lazy-input",
                    "name": "ToolSearch",
                    "input": {
                        "query": "select:PushNotification",
                        "note": "x" * 20000,
                        "token": secret,
                    },
                }],
            },
        }

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(ev) + "\n")
            path = f.name
        try:
            payload = self.server._tool_input_at_jsonl_line(
                pathlib.Path(path), 1, "toolu-lazy-input"
            )
        finally:
            os.unlink(path)

        self.assertIn('"query": "select:PushNotification"', payload)
        self.assertIn("x" * 20000, payload)
        self.assertNotIn(secret, payload)
        self.assertIn("[redacted]", payload)

    def test_bash_tool_detail_strips_shell_wrapper(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-bash",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-bash",
                    "name": "Bash",
                    "input": {
                        "command": (
                            "true && unsetopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "setopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "python3 render_short_slides.py 2>&1 | grep slide"
                        )
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 8)

        detail = parsed["blocks"][0]["detail"]
        self.assertEqual(parsed["blocks"][0]["id"], "toolu-bash")
        self.assertEqual(detail, "python3 render_short_slides.py 2>&1 | grep slide")
        self.assertNotIn("NO_EXTENDED_GLOB", detail)

    def test_pending_ask_user_question_clears_after_answer(self):
        sid = "00000000-0000-4000-8000-000000000099"
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", "-demo-repo")
        project_dir.mkdir(parents=True)
        jsonl = project_dir / f"{sid}.jsonl"
        preamble_event = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "Locked in. Back to the key flow question.",
                }],
            },
        }
        ask_event = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [{"label": "Half auto", "description": "Ask before destructive steps."}],
                        }]
                    },
                }],
            },
        }
        jsonl.write_text(
            json.dumps(preamble_event) + "\n" + json.dumps(ask_event) + "\n",
            encoding="utf-8",
        )

        pending = self.server._pending_ask_user_question_for_session(sid)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["question"], "How automated do you want this?")
        self.assertEqual(pending["preamble"], "Locked in. Back to the key flow question.")
        self.assertEqual(pending["options"], ["Half auto"])
        self.assertEqual(pending["option_details"][0]["description"], "Ask before destructive steps.")

        answer_event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu-question",
                    "content": "answered",
                }],
            },
        }
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(answer_event) + "\n")

        self.assertIsNone(self.server._pending_ask_user_question_for_session(sid))

    def test_ask_question_blocking_inject_gates_on_relay_request(self):
        """Inject queue gate uses the relay request file, not the transcript.

        The PreToolUse hook answers AskUserQuestion via a deny-reply, so the
        tool never runs and no tool_result lands in the transcript — a
        transcript scan can never see the question clear. Gating the queue on
        it deadlocks ("messages stuck on Queued forever"). The request file is
        the authoritative live signal.
        """
        sid = "00000000-0000-4000-8000-0000000000aa"
        relay_dir = self.server.QUESTION_RELAY_DIR
        relay_dir.mkdir(parents=True, exist_ok=True)
        req_path = relay_dir / f"{sid}.request.json"
        ans_path = relay_dir / f"{sid}.answer.json"
        headless = {}  # no TTY → a relay/headless session

        # No relay request -> nothing blocking, even with a stale transcript.
        self.assertFalse(self.server._ask_question_blocking_inject(sid, headless))

        # Live request, no answer yet -> hold the queue.
        req_path.write_text(json.dumps({
            "nonce": "n-1", "session_id": sid, "pid": os.getpid(),
            "questions": [{"header": "H", "question": "Q?", "options": []}],
        }), encoding="utf-8")
        self.assertTrue(self.server._ask_question_blocking_inject(sid, headless))

        # User relayed a matching-nonce answer -> resolved, drain the queue
        # (the hook hasn't cleared the request file yet).
        ans_path.write_text(json.dumps({"nonce": "n-1", "answers": []}),
                            encoding="utf-8")
        self.assertFalse(self.server._ask_question_blocking_inject(sid, headless))

        # A stale answer from a previous question must NOT unblock a fresh one.
        ans_path.write_text(json.dumps({"nonce": "n-OLD", "answers": []}),
                            encoding="utf-8")
        self.assertTrue(self.server._ask_question_blocking_inject(sid, headless))

    def _write_ask_question_session(self, sid, *, answered):
        """Write a transcript whose last assistant turn asks a question.

        When ``answered`` is True a matching tool_result is appended,
        simulating a question the user already answered or declined.
        """
        self.server.SIDECAR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", "-demo-repo")
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        events = [{
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "Locked in. Back to the key flow question.",
                }],
            },
        }, {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [{
                                "label": "Half auto",
                                "description": "Ask before destructive steps.",
                            }],
                        }]
                    },
                }],
            },
        }]
        if answered:
            # User hit Esc on the prompt — Claude Code returns an error
            # tool_result and never fires PostToolUse, so the in-flight
            # marker lingers. This must NOT keep the row "waiting".
            events.append({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "toolu-question",
                        "is_error": True,
                        "content": "Answer questions?",
                    }],
                },
            })
        jsonl.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        marker = {
            "session_id": sid,
            "tool": "AskUserQuestion",
            "file": "Key flow: How automated do you want this?",
            "question": "How automated do you want this?",
            "header": "Key flow",
            "options": ["Half auto"],
            "option_details": [{
                "label": "Half auto",
                "description": "Ask before destructive steps.",
            }],
            "summary": "Key flow: How automated do you want this?",
            "started_at": 1778813567.0,
        }
        (self.server.SIDECAR_STATE_DIR / f"{sid}_in_flight.json").write_text(
            json.dumps(marker),
            encoding="utf-8",
        )

    def test_inflight_ask_user_question_marks_row_waiting(self):
        sid = "00000000-0000-4000-8000-000000000100"
        self._write_ask_question_session(sid, answered=False)

        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)

        self.assertEqual(entry["sidecar_tool"], "AskUserQuestion")
        self.assertTrue(entry["question_waiting"])
        self.assertEqual(entry["question_text"], "How automated do you want this?")
        self.assertEqual(entry["question_preamble"], "Locked in. Back to the key flow question.")
        self.assertEqual(entry["question_option_details"][0]["description"], "Ask before destructive steps.")

    def test_declined_ask_user_question_does_not_mark_row_waiting(self):
        # Regression: a declined AskUserQuestion (is_error tool_result, no
        # PostToolUse) leaves a stale in-flight marker. The transcript is
        # authoritative — the row must not show a phantom "waiting" box.
        sid = "00000000-0000-4000-8000-000000000101"
        self._write_ask_question_session(sid, answered=True)

        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)

        self.assertFalse(entry["question_waiting"])
        self.assertFalse(entry["sidecar_in_flight"])
        self.assertNotEqual(entry.get("sidecar_tool"), "AskUserQuestion")

    def test_spawn_session_preflights_missing_claude_cli(self):
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(self.repo),
                repo_path=str(self.repo),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        popen.assert_not_called()

    def test_spawn_session_accepts_unregistered_plain_cwd(self):
        scratch = pathlib.Path(self.tmp_home, "scratch-space").resolve()
        scratch.mkdir()
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=None
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(scratch),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn(str(scratch), self.server._load_custom_repos())
        popen.assert_not_called()

    def test_unknown_repo_path_is_rejected(self):
        unknown = pathlib.Path(self.tmp_home, "not-a-repo").resolve()
        unknown.mkdir()
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path(str(unknown))
        self.assertEqual(ctx.exception.code, "repo_not_allowed")

    def test_all_is_not_a_repo_path(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path("ALL")
        self.assertEqual(ctx.exception.code, "invalid_repo_path")

    def test_ambiguous_context_returns_repo_required(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.require_repo_context({}, {}, allow_session=False)
        self.assertEqual(ctx.exception.code, "repo_required")

    def test_session_id_resolves_repo_context(self):
        sid = "00000000-0000-4000-8000-000000000099"
        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": sid,
                "message": {"role": "user", "content": "hello"},
            }) + "\n",
            encoding="utf-8",
        )

        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(self.repo))

    def test_session_root_cwd_resolves_from_effective_repo_evidence(self):
        sid = "00000000-0000-4000-8000-000000000109"
        subprocess.run(["git", "init"], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        touched = self.repo / "server.py"
        touched.write_text("print('ok')\n", encoding="utf-8")

        transcript = self.server._canonical_conversation_path("/", sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": "/",
                "sessionId": sid,
                "gitBranch": "HEAD",
                "message": {"role": "user", "content": "work on the repo"},
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-05-04T00:00:01.000Z",
                "sessionId": sid,
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": str(touched)},
                    }],
                },
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-05-04T00:00:02.000Z",
                "sessionId": sid,
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": f"git -C {self.repo} status"},
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        try:
            ctx = self.server.repo_from_session(sid)
        except self.server.RepoContextError as exc:
            self.fail(
                "repo_from_session rejected root cwd instead of using "
                f"effective repo evidence: {exc.code}"
            )
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(self.repo))

    def test_root_bucket_session_jsonl_moves_to_resume_cwd_bucket(self):
        sid = "00000000-0000-4000-8000-000000000110"
        src = self.server._canonical_conversation_path("/", sid)
        dest = self.server._canonical_conversation_path(str(self.repo), sid)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": "/",
                "sessionId": sid,
                "message": {"role": "user", "content": "hello"},
            }) + "\n",
            encoding="utf-8",
        )
        self.assertTrue(src.is_file())
        self.assertFalse(dest.exists())
        self.assertTrue(
            hasattr(self.server, "_ensure_session_jsonl_for_cwd"),
            "resume should have a JSONL rebucket repair helper",
        )

        result = self.server._ensure_session_jsonl_for_cwd(sid, str(self.repo))

        self.assertTrue(result["ok"])
        self.assertTrue(result["moved"])
        self.assertFalse(src.exists())
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8").splitlines()[0],
                         json.dumps({
                             "type": "user",
                             "timestamp": "2026-05-04T00:00:00.000Z",
                             "cwd": "/",
                             "sessionId": sid,
                             "message": {"role": "user", "content": "hello"},
                         }))

    def test_find_conversations_carries_spawn_parent_session_id(self):
        parent = "10000000-0000-4000-8000-000000000001"
        child = "10000000-0000-4000-8000-000000000002"
        transcript = self.server._canonical_conversation_path(str(self.repo), child)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": child,
                "gitBranch": "main",
                "message": {"role": "user", "content": "review this"},
            }) + "\n",
            encoding="utf-8",
        )
        self.server._record_spawn_to_registry(
            pid=424242,
            name="reviewer",
            log_path=self.repo / ".claude" / "logs" / "spawn-reviewer.log",
            cwd=str(self.repo),
            spawned_at="20260504T000000",
            command_summary="review this",
            fifo=None,
            engine="claude",
            session_id=child,
            repo_path=str(self.repo),
            parent_session_id=parent,
        )

        row = next(r for r in self.server.find_conversations(str(self.repo))
                   if r["session_id"] == child)
        self.assertEqual(row["parent_session_id"], parent)

    def test_find_conversations_recovers_legacy_report_to_parent_from_prompt(self):
        parent = "10000000-0000-4000-8000-000000000003"
        child = "10000000-0000-4000-8000-000000000004"
        prompt = self.server._wrap_prompt_with_return_address(
            "review this",
            parent,
            port=8090,
        )
        transcript = self.server._canonical_conversation_path(str(self.repo), child)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": child,
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }) + "\n",
            encoding="utf-8",
        )

        row = next(r for r in self.server.find_conversations(str(self.repo))
                   if r["session_id"] == child)
        self.assertEqual(row["parent_session_id"], parent)

    def test_archive_rehydrate_restores_spawn_parent_session_id(self):
        parent = "10000000-0000-4000-8000-000000000011"
        child = "10000000-0000-4000-8000-000000000012"
        self.server._record_spawn_to_registry(
            pid=424243,
            name="reviewer",
            log_path=self.repo / ".claude" / "logs" / "spawn-reviewer.log",
            cwd=str(self.repo),
            spawned_at="20260504T000000",
            command_summary="review this",
            fifo=None,
            engine="claude",
            session_id=child,
            repo_path=str(self.repo),
            parent_session_id=parent,
        )

        rows = self.server._rehydrate_archive_cached_rows([{
            "session_id": child,
            "engine": "claude",
            "mtime": 1782417600.0,
            "modified": 1782417600.0,
            "parent_session_id": "",
        }])

        self.assertEqual(rows[0]["parent_session_id"], parent)

    def test_archive_build_stamps_live_row_state(self):
        now = time.time()
        with mock.patch.object(
            self.server,
            "find_all_conversations",
            return_value=[{
                "session_id": "live-build-state",
                "engine": "claude",
                "is_live": True,
                "sidecar_status": "active",
                "sidecar_ts": now,
                "sidecar_in_flight": False,
                "pending_tool": None,
                "last_event_type": "assistant",
            }],
        ):
            rows = self.server._build_archive_conversations()

        self.assertEqual(rows[0]["state"], "working")
        self.assertFalse(rows[0]["ended_blocked"])

    def test_archive_rehydrate_stamps_live_row_state_after_sidecar_refresh(self):
        now = time.time()
        sid = "live-rehydrate-state"

        def add_sidecar(row):
            row.update({
                "sidecar_status": "active",
                "sidecar_ts": now,
                "sidecar_in_flight": False,
                "sidecar_tool": "Bash",
                "sidecar_file": "pytest",
            })

        with mock.patch.object(self.server, "_discover_live_session_ids", return_value={sid}), \
             mock.patch.object(self.server, "_archive_session_is_live", return_value=True), \
             mock.patch.object(self.server, "_add_sidecar_fields", side_effect=add_sidecar), \
             mock.patch.object(self.server, "_load_archived_conversations", return_value=[]), \
             mock.patch.object(self.server, "_load_verified_conversations", return_value=[]), \
             mock.patch.object(self.server, "_load_pinned_conversations", return_value=[]):
            rows = self.server._rehydrate_archive_cached_rows([{
                "session_id": sid,
                "engine": "claude",
                "mtime": now,
                "modified": now,
                "last_event_type": "assistant",
                "pending_tool": None,
            }])

        self.assertEqual(rows[0]["state"], "working")
        self.assertEqual(rows[0]["sidecar_tool"], "Bash")

    def test_archive_rehydrate_recovers_legacy_report_to_parent_from_transcript(self):
        parent = "10000000-0000-4000-8000-000000000013"
        child = "10000000-0000-4000-8000-000000000014"
        prompt = self.server._wrap_prompt_with_return_address(
            "review this",
            parent,
            port=8090,
        )
        transcript = self.server._canonical_conversation_path(str(self.repo), child)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": child,
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }) + "\n",
            encoding="utf-8",
        )
        self.server._record_spawn_to_registry(
            pid=424244,
            name="reviewer",
            log_path=self.repo / ".claude" / "logs" / "spawn-reviewer.log",
            cwd=str(self.repo),
            spawned_at="20260504T000000",
            command_summary="review this",
            fifo=None,
            engine="claude",
            session_id=child,
            repo_path=str(self.repo),
        )

        rows = self.server._rehydrate_archive_cached_rows([{
            "session_id": child,
            "engine": "claude",
            "jsonl_path": str(transcript),
            "mtime": 1782417600.0,
            "modified": 1782417600.0,
            "first_message": "review this",
            "parent_session_id": "",
        }])

        self.assertEqual(rows[0]["parent_session_id"], parent)

    def test_backfill_spawn_parent_session_ids_persists_legacy_return_address(self):
        parent = "10000000-0000-4000-8000-000000000015"
        child = "10000000-0000-4000-8000-000000000016"
        prompt = self.server._wrap_prompt_with_return_address(
            "review this",
            parent,
            port=8090,
        )
        transcript = self.server._canonical_conversation_path(str(self.repo), child)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": child,
                "gitBranch": "main",
                "message": {"role": "user", "content": prompt},
            }) + "\n",
            encoding="utf-8",
        )
        self.server._record_spawn_to_registry(
            pid=424245,
            name="reviewer",
            log_path=self.repo / ".claude" / "logs" / "spawn-reviewer.log",
            cwd=str(self.repo),
            spawned_at="20260504T000000",
            command_summary="review this",
            fifo=None,
            engine="claude",
            session_id=child,
            repo_path=str(self.repo),
        )

        dry_run = self.server.backfill_spawn_parent_session_ids(dry_run=True)
        self.assertEqual(dry_run["updated"], 1)
        self.assertFalse(self.server._load_spawn_registry()[-1]["parent_session_id"])

        result = self.server.backfill_spawn_parent_session_ids()
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["updates"][0]["source"], "transcript")
        self.assertEqual(
            self.server._load_spawn_registry()[-1]["parent_session_id"],
            parent,
        )

    def test_session_cwd_relocates_after_folder_move(self):
        sid = "00000000-0000-4000-8000-000000000100"
        old_cwd = self.repo / "old folder" / "app"
        new_cwd = self.repo / "code" / "old-folder" / "app"
        moved_file = new_cwd / "src" / "main.py"
        moved_file.parent.mkdir(parents=True)
        moved_file.write_text("print('ok')\n", encoding="utf-8")

        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(old_cwd),
                "sessionId": sid,
                "gitBranch": "main",
                "message": {"role": "user", "content": "read src/main.py"},
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-05-04T00:00:01.000Z",
                "sessionId": sid,
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": str(old_cwd / "src" / "main.py")},
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        self.assertEqual(self.server.find_session_cwd(sid), str(new_cwd))
        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(new_cwd))
        row = next(r for r in self.server.find_conversations(str(self.repo))
                   if r["session_id"] == sid)
        self.assertEqual(row["session_cwd"], str(new_cwd))
        self.assertTrue(row["session_cwd_exists"])

    def test_cwd_context_uses_nearest_claude_marker_parent(self):
        project = pathlib.Path(self.tmp_home, "plain-project").resolve()
        cwd = project / "nested" / "tool"
        cwd.mkdir(parents=True)
        (project / ".claude").mkdir()

        ctx = self.server._resolve_cwd_context(str(cwd))
        self.assertEqual(ctx["repo_path"], str(project))
        self.assertEqual(ctx["cwd"], str(cwd))

    def test_repo_required_endpoint_and_switch_compatibility(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(base + "/api/term/cwd", timeout=5)
            self.assertEqual(missing.exception.code, 400)
            missing_body = missing.exception.read().decode("utf-8")
            missing.exception.close()
            self.assertIn("repo_required", missing_body)

            with self.assertRaises(urllib.error.HTTPError) as conv_missing:
                urllib.request.urlopen(base + "/api/conversations", timeout=5)
            self.assertEqual(conv_missing.exception.code, 400)
            conv_missing_body = conv_missing.exception.read().decode("utf-8")
            conv_missing.exception.close()
            self.assertIn("repo_required", conv_missing_body)

            with urllib.request.urlopen(
                base + "/api/term/cwd?repo_path=" + urllib.parse.quote(str(self.repo)),
                timeout=5,
            ) as res:
                self.assertEqual(res.status, 200)
                self.assertIn(str(self.repo), res.read().decode("utf-8"))

            req = urllib.request.Request(
                base + "/api/repo/switch",
                data=json.dumps({"path": str(self.repo)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as gone:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(gone.exception.code, 410)
            gone_body = gone.exception.read().decode("utf-8")
            gone.exception.close()
            self.assertIn("repo_switch_removed", gone_body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_sessions_all_endpoint_returns_archive_and_spawned_payload(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "_build_archive_conversations",
                return_value=[{"session_id": "codex-1", "engine": "codex"}],
            ), mock.patch.object(
                self.server,
                "list_spawned_sessions",
                return_value=[{"spawn_id": "123", "engine": "codex"}],
            ):
                with urllib.request.urlopen(base + "/api/sessions?all=1&engine=codex", timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["sessions"][0]["engine"], "codex")
            self.assertEqual(body["spawned"][0]["spawn_id"], "123")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_unified_spawn_endpoint_accepts_engine(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "spawn_session_codex",
                return_value={"ok": True, "pid": 123, "name": "demo", "log": "/tmp/demo.log"},
            ) as spawn_codex, mock.patch.object(
                self.server,
                "_load_spawn_defaults",
                return_value={"engine": "codex", "models": {"codex": "gpt-5.5"}, "reasoning_effort": "high"},
            ):
                req = urllib.request.Request(
                    base + "/api/sessions/spawn",
                    data=json.dumps({
                        "prompt": "do the thing",
                        "engine": "Codex",
                        "model": "gpt-5.5",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertEqual(body["engine"], "codex")
            spawn_codex.assert_called_once_with(
                "do the thing",
                name=None,
                cwd=None,
                repo_path=None,
                worktree=False,
                model="gpt-5.5",
                reasoning_effort="high",
                parent_session_id=None,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_codex_spawn_endpoint_uses_default_reasoning_effort_when_omitted(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "spawn_session_codex",
                return_value={"ok": True, "pid": 123, "name": "demo", "log": "/tmp/demo.log"},
            ) as spawn_codex, mock.patch.object(
                self.server,
                "_load_spawn_defaults",
                return_value={"engine": "codex", "models": {"codex": "gpt-5.5"}, "reasoning_effort": "xhigh"},
            ):
                req = urllib.request.Request(
                    base + "/api/sessions/spawn-codex",
                    data=json.dumps({"prompt": "do the thing", "model": "gpt-5.5"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertEqual(spawn_codex.call_args.kwargs.get("reasoning_effort"), "xhigh")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_write_port_file_never_publishes_wildcard_bind_address(self):
        url = self.server.write_port_file("0.0.0.0")
        self.assertEqual(url, f"http://127.0.0.1:{self.server.PORT}")
        port_file = self.server.COMMAND_CENTER_STATE_DIR / "port.txt"
        self.assertEqual(port_file.read_text().strip(), url)

    def test_resolve_codex_bin_prefers_env_override(self):
        """`_resolve_codex_bin` must honour CCC_CODEX_BIN when it points
        at an executable file. Verifies the precedence head — env var
        always wins over `which codex` and the app-bundle fallback."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_codex_bin"))

        with tempfile.NamedTemporaryFile(prefix="codex-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"), \
                 mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/sentinel/from/bundle"):
                result = server._resolve_codex_bin()
            # Env override must win over both the PATH lookup and the bundle path.
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_codex_bin_returns_unavailable_when_missing(self):
        """When CCC_CODEX_BIN points at a non-existent path AND the
        Codex.app bundle is absent AND `which codex` finds nothing,
        the resolver must return {available: False, reason: ...}
        rather than raising."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": "/definitely/does/not/exist/codex"}), \
             mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/nope/does-not-exist"):
            result = server._resolve_codex_bin()
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_resolve_claude_bin_prefers_env_override(self):
        """CCC_CLAUDE_BIN must win over PATH so launchd services can pin
        the same CLI path an interactive shell uses."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_claude_bin"))

        with tempfile.NamedTemporaryFile(prefix="claude-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
                result = server._resolve_claude_bin()
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_claude_bin_returns_unavailable_for_bad_env_override(self):
        """A bad CCC_CLAUDE_BIN should fail clearly instead of falling
        through to another binary and hiding the service configuration error."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": "/definitely/does/not/exist/claude"}), \
             mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
            result = server._resolve_claude_bin()
        self.assertFalse(result["available"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn("CCC_CLAUDE_BIN", result.get("reason", ""))

    def test_resolve_gemini_bin_uses_common_candidates(self):
        """Gemini should be available when installed in a user bin dir that
        launchd did not put on PATH."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_gemini_bin"))

        with tempfile.NamedTemporaryFile(prefix="gemini-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = pathlib.Path(f.name)
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(server.shutil, "which", return_value=None), \
                 mock.patch.object(server, "_iter_common_cli_candidates", return_value=[fake_bin]):
                result = server._resolve_gemini_bin()
            self.assertEqual(result["bin"], str(fake_bin))
            self.assertEqual(result["source"], "candidate")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_nextjs_turbo_workspace_uses_dev_filter(self):
        """Workspace Next.js apps should start with the scoped turbo command."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            app = root / "apps" / "bookyourmat"
            finie = root / "apps" / "finie"
            app.mkdir(parents=True)
            finie.mkdir(parents=True)
            (root / "turbo.json").write_text(json.dumps({"tasks": {"dev": {"persistent": True}}}))
            (root / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
            (app / "package.json").write_text(json.dumps({
                "name": "bookyourmat",
                "scripts": {"dev": "next dev --port 39001"},
                "dependencies": {"next": "16.1.6"},
            }))
            (finie / "package.json").write_text(json.dumps({
                "name": "finie",
                "scripts": {"dev": "next dev --port 3000"},
                "dependencies": {"next": "16.1.6"},
            }))

            cmd, cwd = server._resolve_dev_invocation(app)
            (root / ".git").mkdir()
            status = server.nextjs_status(str(root), str(app))
            root_status = server.nextjs_status(str(root))

        self.assertEqual(cmd, ["npx", "turbo", "dev", "--filter=bookyourmat"])
        self.assertEqual(cwd, root)
        self.assertEqual(status["launch_cmd"], "npx turbo dev --filter=bookyourmat")
        self.assertEqual(root_status["target_path"], str(app))
        self.assertEqual(root_status["launch_cmd"], "npx turbo dev --filter=bookyourmat")

    def test_nextjs_process_match_ignores_prompt_text(self):
        """Process rediscovery must not match an agent command line that
        merely pasted the same ps/rg pattern in its prompt."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        info = {
            "target_path": pathlib.Path("/tmp/repo/apps/bookyourmat"),
            "run_cwd": pathlib.Path("/tmp/repo"),
            "package_name": "bookyourmat",
            "filter_expected": True,
            "ports": [3001],
        }
        self.assertTrue(server._nextjs_command_matches(
            "npx turbo dev --filter=bookyourmat", info))
        self.assertTrue(server._nextjs_command_matches(
            "node /tmp/repo/node_modules/.bin/next dev --port 3001", info))
        self.assertFalse(server._nextjs_command_matches(
            "/opt/homebrew/bin/codex exec --json -- prompt contains "
            "turbo dev --filter=bookyourmat and next dev --port 3001",
            info,
        ))

    def test_spawn_session_codex_exists(self):
        """`spawn_session_codex` must exist alongside `spawn_session`
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        import inspect
        sig = inspect.signature(server.spawn_session)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model", "parent_session_id"])
        self.assertTrue(hasattr(server, "spawn_session_codex"))
        sig = inspect.signature(server.spawn_session_codex)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model", "reasoning_effort", "parent_session_id"])

    def test_spawn_session_gemini_exists(self):
        """`spawn_session_gemini` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_gemini"))
        import inspect
        sig = inspect.signature(server.spawn_session_gemini)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model", "parent_session_id"])

    def test_spawn_session_cursor_exists(self):
        """`spawn_session_cursor` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_cursor"))
        import inspect
        sig = inspect.signature(server.spawn_session_cursor)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model", "parent_session_id"])

    def test_orchestration_spawn_engine_normalization(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertEqual(server._normalize_orchestration_spawn_engine(None), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Claude"), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Codex"), "codex")
        self.assertEqual(server._normalize_orchestration_spawn_engine("cursor-agent"), "cursor")
        self.assertEqual(server._normalize_orchestration_spawn_engine("antigravity"), "antigravity")
        self.assertEqual(server._normalize_orchestration_spawn_engine("gemini"), "antigravity")

    def test_record_spawn_to_registry_persists_engine(self):
        """The on-disk spawn registry must round-trip an `engine` field
        so a CCC restart can branch claude-vs-codex reattach logic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            orig = server.SPAWNED_PIDS_FILE
            server.SPAWNED_PIDS_FILE = registry_file
            try:
                server._record_spawn_to_registry(
                    pid=99999, name="t", log_path=pathlib.Path(tmp) / "x.log",
                    cwd=tmp, spawned_at="20260430T000000",
                    command_summary="test", fifo=None, engine="codex",
                    session_id="known-session-id",
                    parent_session_id="parent-session-id",
                )
                with registry_file.open() as f:
                    rows = json.load(f)
                self.assertEqual(rows[-1]["engine"], "codex")
                self.assertEqual(rows[-1]["session_id"], "known-session-id")
                self.assertEqual(rows[-1]["parent_session_id"], "parent-session-id")
            finally:
                server.SPAWNED_PIDS_FILE = orig

    def test_list_spawned_sessions_exposes_correlation_fields(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions[:] = [{
            "pid": 4242,
            "name": "reviewer",
            "log": "/tmp/reviewer.log",
            "prompt": "review this",
            "started": "20260530T120000",
            "proc": proc,
            "engine": "codex",
            "session_id": "codex-thread-1",
            "cwd": "/tmp/repo",
            "repo_path": "/tmp/repo",
            "model": "gpt-test",
        }]
        try:
            rows = server.list_spawned_sessions()
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertEqual(rows[0]["spawn_id"], "4242")
        self.assertEqual(rows[0]["session_id"], "codex-thread-1")
        self.assertFalse(rows[0]["session_id_pending"])
        self.assertEqual(rows[0]["engine"], "codex")
        self.assertEqual(rows[0]["repo_path"], "/tmp/repo")
        self.assertTrue(rows[0]["running"])

    def test_pid_is_engine_process_recognises_codex_and_gemini(self):
        """`_pid_is_engine_process` must accept an `engine` arg and match
        the right argv[0] basename for it."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_pid_is_engine_process"))

        def fake_run(args, **kw):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = ""; r.stderr = ""
            if args[:2] == ["ps", "-p"]:
                pid = args[2]
                if pid == "11111":
                    r.stdout = "/usr/local/bin/claude -p --verbose\n"
                elif pid == "22222":
                    r.stdout = "/Applications/Codex.app/Contents/Resources/codex exec --json\n"
                elif pid == "33333":
                    r.stdout = "/usr/local/bin/node /usr/local/bin/gemini --output-format stream-json\n"
                elif pid == "44444":
                    r.stdout = "/Users/test/.local/bin/cursor-agent --resume 00000000-0000-4000-8000-000000000005\n"
            return r

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            self.assertTrue(server._pid_is_engine_process(11111, "claude"))
            self.assertFalse(server._pid_is_engine_process(11111, "codex"))
            self.assertTrue(server._pid_is_engine_process(22222, "codex"))
            self.assertFalse(server._pid_is_engine_process(22222, "claude"))
            self.assertTrue(server._pid_is_engine_process(33333, "gemini"))
            self.assertFalse(server._pid_is_engine_process(33333, "codex"))
            self.assertTrue(server._pid_is_engine_process(44444, "cursor"))
            self.assertFalse(server._pid_is_engine_process(44444, "codex"))

    def test_pid_is_engine_process_rejects_zombie(self):
        """A defunct reattached resume must not keep a Codex card live."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.object(server, "_pid_is_zombie", return_value=True), \
             mock.patch.object(server.subprocess, "run") as run:
            self.assertFalse(server._pid_is_engine_process(22222, "codex"))
        run.assert_not_called()

    def test_reattached_proc_poll_treats_zombie_as_exited(self):
        """After an in-place server restart, a child may become a zombie
        without a Popen handle. Polling must release queued resumes."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        proc = server._ReattachedProc(22222)
        with mock.patch.object(server.os, "waitpid", side_effect=ChildProcessError), \
             mock.patch.object(server.os, "kill", return_value=None), \
             mock.patch.object(server, "_pid_is_zombie", return_value=True):
            self.assertEqual(proc.poll(), -1)
            self.assertEqual(proc.poll(), -1)

    def test_gemini_chat_parsing_usage_and_row_signals(self):
        """Gemini chat JSON should render as a first-class session row."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        chat = {
            "sessionId": sid,
            "startTime": "2026-05-04T01:00:00.000Z",
            "lastUpdated": "2026-05-04T01:02:00.000Z",
            "kind": "main",
            "messages": [
                {
                    "id": "u1",
                    "timestamp": "2026-05-04T01:00:00.000Z",
                    "type": "user",
                    "content": [{"text": "Create the probe file."}],
                },
                {
                    "id": "g1",
                    "timestamp": "2026-05-04T01:01:00.000Z",
                    "type": "gemini",
                    "content": "I created and committed the probe.",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1200,
                        "output": 30,
                        "cached": 200,
                        "thoughts": 5,
                        "tool": 0,
                        "total": 1235,
                    },
                    "toolCalls": [{
                        "id": "run_shell_command_1",
                        "name": "run_shell_command",
                        "args": {
                            "command": "printf 'ok\\n' > probe.txt && git add probe.txt && git commit -m \"probe: gemini\"",
                            "description": "Create and commit probe file.",
                        },
                        "status": "success",
                        "timestamp": "2026-05-04T01:01:10.000Z",
                        "result": [{
                            "functionResponse": {
                                "response": {
                                    "output": "Output: [feat/demo abc1234] probe: gemini\n 1 file changed, 1 insertion(+)"
                                }
                            }
                        }],
                    }],
                },
                {
                    "id": "g2",
                    "timestamp": "2026-05-04T01:02:00.000Z",
                    "type": "gemini",
                    "content": "Branch: feat/demo\nCommit: abc1234 probe: gemini\nWorktree: /tmp/example-worktree",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1300,
                        "output": 20,
                        "cached": 250,
                        "thoughts": 4,
                        "tool": 0,
                        "total": 1324,
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            gemini_home = pathlib.Path(tmp) / ".gemini"
            project = gemini_home / "tmp" / "example-project"
            chats = project / "chats"
            chats.mkdir(parents=True)
            (project / ".project_root").write_text("/tmp/example-repo")
            chat_path = chats / "session-2026-05-04T01-00-aaaaaaaa.json"
            chat_path.write_text(json.dumps(chat))
            orig_home = server.GEMINI_HOME
            server.GEMINI_HOME = gemini_home
            try:
                self.assertTrue(server._is_gemini_session(sid))
                parsed = server.parse_conversation(sid)
                usage = server.extract_session_usage(sid)
                rows = server.find_gemini_conversations(include_old=True, repo_only=False)
            finally:
                server.GEMINI_HOME = orig_home

        self.assertGreaterEqual(len(parsed["events"]), 4)
        self.assertEqual(usage["latest_input_tokens"], 1300)
        self.assertEqual(usage["total_cache_read_tokens"], 450)
        self.assertEqual(usage["model"], "gemini-test-model")
        row = rows[0]
        self.assertEqual(row["source"], "gemini")
        self.assertTrue(row["has_edit"])
        self.assertTrue(row["has_commit"])
        self.assertEqual(row["effective_branch"], "feat/demo")
        self.assertEqual(row["session_cwd"], "/tmp/example-worktree")

    def test_shell_command_signals_detect_real_git_subcommands(self):
        cases = [
            ("git push", {"push": True}),
            ("git -C /tmp/repo push origin HEAD", {"push": True, "external_cd": True}),
            ("git -c user.name=Bot commit -m ok && git push", {"commit": True, "push": True}),
            ("command git commit -m ok", {"commit": True}),
            ("env GIT_DIR=/tmp/repo/.git git push", {"push": True}),
            ("bash -lc 'git push'", {"push": True}),
            ("gh --repo owner/repo pr create --title ok", {"pr": True}),
        ]
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                for key, value in expected.items():
                    self.assertEqual(signals[key], value)

    def test_shell_command_signals_ignore_git_text_in_other_commands(self):
        cases = [
            'rg -n "git push" server.py',
            'grep "git commit" rollout.jsonl',
            'echo "git push"',
            'python3 - <<\'PY\'\nprint("git push")\nPY',
            'git status | rg "push"',
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                self.assertFalse(signals["commit"])
                self.assertFalse(signals["push"])

        signals = self.server._shell_command_signals(
            'git commit -m "document git push workflow"'
        )
        self.assertTrue(signals["commit"])
        self.assertFalse(signals["push"])

    def test_shell_command_signals_resolve_relative_worktree_add(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"

        signals = self.server._shell_command_signals(
            "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
            base_cwd=str(base),
        )

        self.assertEqual(signals["worktree_branch"], "fix/worktree-ui")
        self.assertEqual(signals["worktree_path"], str(expected.resolve()))

    def test_codex_tail_meta_resolves_relative_worktree_add_from_workdir(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_worktree",
                "arguments": json.dumps({
                    "cmd": "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
                    "workdir": str(base),
                }),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertEqual(meta["tail_branch"], "fix/worktree-ui")
        self.assertEqual(meta["tail_worktree_path"], str(expected.resolve()))

    def test_workspace_uses_explicit_worktree_tail_hint(self):
        worktree = pathlib.Path(self.tmp_home, "repo-wt-ui").resolve()
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/fake/worktrees/repo-wt-ui\n")

        with mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "_session_tail_worktree_hint",
                 return_value={
                     "path": str(worktree),
                     "branch": "fix/worktree-ui",
                     "source": "worktree-add",
                 },
             ), \
             mock.patch.object(self.server, "_infer_effective_repo") as infer:
            workspace = self.server.extract_session_workspace(
                "00000000-0000-4000-8000-000000000001"
            )

        infer.assert_not_called()
        self.assertEqual(workspace["effective_cwd"], str(worktree))
        self.assertEqual(workspace["effective_branch"], "fix/worktree-ui")
        self.assertEqual(workspace["effective_kind"], "worktree")
        self.assertEqual(workspace["effective_source"], "worktree-add")

    def test_tail_meta_ignores_bash_search_for_git_push(self):
        event = {
            "type": "assistant",
            "timestamp": "2026-05-11T12:00:00.000Z",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": 'rg -n "git push" server.py'},
                }],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_codex_tail_meta_ignores_exec_search_for_git_push(self):
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_1",
                "arguments": json.dumps({"cmd": 'rg -n "git push" server.py'}),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_codex_tail_meta_detects_custom_tool_approval(self):
        server = self.server
        event = {
            "timestamp": "2026-07-11T03:12:48.336Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call_approval",
                "status": "completed",
                "input": (
                    "const r = await tools.exec_command({\n"
                    "  cmd: \"wt status -q CHUCK --json\",\n"
                    "  workdir: \"/home/hermes/projects/chuck-realtor-web\",\n"
                    "  sandbox_permissions: \"require_escalated\",\n"
                    "  justification: \"Allow this read-only WatchTower status check?\"\n"
                    "});\n"
                ),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            server._conv_meta_cache.clear()
            server._codex_tail_resume.clear()
            meta = server._extract_codex_tail_meta(path)

            self.assertEqual(meta["pending_tool"], "Bash")
            self.assertTrue(meta["needs_approval"])
            self.assertEqual(
                meta["needs_approval_message"],
                "Allow this read-only WatchTower status check?",
            )

            output = {
                "timestamp": "2026-07-11T03:12:50.000Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call_approval",
                    "output": "Script completed\nWall time 0.1 seconds\nOutput:\n{}",
                },
            }
            path.write_text(
                json.dumps(event) + "\n" + json.dumps(output) + "\n",
                encoding="utf-8",
            )
            server._conv_meta_cache.clear()
            server._codex_tail_resume.clear()
            meta = server._extract_codex_tail_meta(path)

        self.assertIsNone(meta["pending_tool"])
        self.assertFalse(meta["needs_approval"])
        self.assertEqual(meta["needs_approval_message"], "")

    def test_parse_codex_event_renders_custom_tool_call(self):
        event = {
            "timestamp": "2026-07-11T03:12:48.336Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call_custom",
                "input": (
                    "const r = await tools.exec_command({\n"
                    "  cmd: \"wt status -q CHUCK --json\",\n"
                    "  sandbox_permissions: \"require_escalated\",\n"
                    "  justification: \"Allow status?\"\n"
                    "});\n"
                ),
            },
        }

        parsed = self.server._parse_codex_event(event, 12)

        self.assertEqual(parsed["type"], "assistant")
        block = parsed["blocks"][0]
        self.assertEqual(block["name"], "Bash")
        self.assertIn("wt status", block["detail"])
        self.assertTrue(block["approval_required"])
        self.assertEqual(block["approval_message"], "Allow status?")

    def test_parse_codex_event_reads_json_style_custom_tool_arguments(self):
        event = {
            "timestamp": "2026-07-12T04:01:54.740Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call_json_style",
                "input": (
                    'const r = await tools.exec_command({"cmd":"kill -TERM 66784\\n'
                    'printf \\"replacement listener\\\\n\\""});\n'
                ),
            },
        }

        parsed = self.server._parse_codex_event(event, 12)

        block = parsed["blocks"][0]
        self.assertEqual(block["name"], "Bash")
        self.assertIn("kill -TERM 66784", block["detail"])
        self.assertIn("kill -TERM 66784", block["command"])

    def test_codex_app_activity_superseded_by_newer_tail(self):
        app_activity = {
            "sidecar_status": "active",
            "sidecar_tool": "Bash",
            "sidecar_ts": 100.0,
        }
        newer_tail = {
            "last_meaningful_ts": 130.0,
            "pending_tool": None,
            "needs_approval": False,
        }
        current_tail = {
            "last_meaningful_ts": 99.0,
            "pending_tool": None,
            "needs_approval": False,
        }

        self.assertTrue(
            self.server._codex_app_activity_superseded_by_tail(app_activity, newer_tail)
        )
        self.assertFalse(
            self.server._codex_app_activity_superseded_by_tail(app_activity, current_tail)
        )

    def test_reattach_spawned_orphans_defaults_legacy_rows_to_claude(self):
        """A registry row written before the `engine` field existed
        must reattach as engine='claude' — not raise KeyError, not
        silently drop the row."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            log_file = pathlib.Path(tmp) / "fake.log"
            log_file.write_text("")
            # Legacy row — no `engine` key. PID is the current process so
            # the os.kill(pid, 0) liveness check succeeds without faking.
            legacy = [{
                "pid": os.getpid(),
                "session_id": None,
                "name": "legacy",
                "log": str(log_file),
                "fifo": None,
                "cwd": tmp,
                "spawned_at": "20260101T000000",
                "command_summary": "old row",
            }]
            registry_file.write_text(json.dumps(legacy))
            orig_registry = server.SPAWNED_PIDS_FILE
            orig_sessions = list(server._spawned_sessions)
            server.SPAWNED_PIDS_FILE = registry_file
            server._spawned_sessions.clear()
            try:
                # Bypass the real ps-grep — current pid isn't a `claude`
                # process, so without a stub it would be dropped.
                with mock.patch.object(server, "_pid_is_engine_process", return_value=True):
                    server._reattach_spawned_orphans()
                self.assertEqual(len(server._spawned_sessions), 1)
                self.assertEqual(server._spawned_sessions[0]["engine"], "claude")
            finally:
                server.SPAWNED_PIDS_FILE = orig_registry
                server._spawned_sessions.clear()
                server._spawned_sessions.extend(orig_sessions)


    def test_reveal_file_route_registered(self):
        """Smoke check: POST /api/reveal-file branch present in do_POST."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn('"/api/reveal-file"', src)
        # Defense-in-depth: extension clamp must be referenced near the
        # endpoint. Cheap signal that the security control wasn't dropped.
        idx = src.find('"/api/reveal-file"')
        self.assertGreater(idx, 0)
        nearby = src[idx:idx + 2000]
        self.assertIn("FILE_EXT_TO_CATEGORY", nearby,
                      "extension clamp missing near /api/reveal-file route")

    def test_open_target_resolves_relative_session_cwd_markdown(self):
        """Inline transcript links should resolve from the selected session cwd."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            checkpoint = session_cwd / ".claude" / "team-checkpoint.md"
            checkpoint.write_text("# checkpoint\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    ".claude/team-checkpoint.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), checkpoint.resolve())
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(result["session_sandbox"])

    def test_open_launch_allows_markdown_session_cwd_files(self):
        """Markdown transcript links may launch externally via macOS open."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "notes.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_strips_markdown_angle_wrapped_paths(self):
        """Markdown links to paths with spaces use <...>; /api/open should unwrap."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "non-code projects" / "ADS"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            report_dir = repo / "final" / "posthog-export"
            report_dir.mkdir(parents=True)
            report = report_dir / "paid-meta-posthog-analysis-20260512.md"
            report.write_text("# report\n")

            result = server._resolve_open_target(
                f"<{report}>",
                cwd=str(repo),
                repo_path=str(repo),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), report.resolve())
        self.assertTrue(result["core_sandbox"])

    def test_open_target_falls_back_when_archive_repo_path_is_virtual(self):
        """Archive rows may pass a display slug as repo_path; cwd should win."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            result = server._resolve_open_target(
                str(doc),
                session_id="11111111-2222-3333-4444-555555555555",
                cwd=str(session_cwd),
                repo_path="-virtual-archive-folder",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), doc.resolve())

    def test_open_target_allows_exact_session_tool_file_outside_cwd(self):
        """Files explicitly touched by the selected session are revealable."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([str(external)], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["session_file_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_resolves_absolute_looking_project_relative(self):
        """`/foo/bar` inside a transcript should fall back to repo-relative when no FS-root match exists."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            sub = repo / "growth-machine" / "content" / "landing"
            sub.mkdir(parents=True)
            target_file = sub / "index.html"
            target_file.write_text("<!doctype html>\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "/growth-machine/content/landing/index.html",
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"], msg=result)
        self.assertEqual(pathlib.Path(result["path"]), target_file.resolve())

    def test_open_target_allows_files_outside_sandbox(self):
        """Post-sandbox-removal: any resolvable path is allowed through /api/open."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])

    def test_open_launch_allowed_for_any_resolved_target(self):
        """Post-sandbox-removal: _open_launch_allowed returns True unconditionally."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            image = session_cwd / "screenshot.png"
            image.write_bytes(b"not really a png")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "screenshot.png",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_allows_command_center_pasted_images(self):
        """CCC-uploaded pasted images should be revealable from transcript links."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        result = server._resolve_open_target(
            str(image),
            cwd=str(self.repo),
            repo_path=str(self.repo),
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["pasted_image_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_spawn_codex_attaches_command_center_pasted_images(self):
        """Pasted image paths in Codex prompts should be sent as --image args."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        proc = mock.Mock(pid=4242)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.dict(os.environ, {"CCC_CODEX_SPAWN_APP_SERVER": "0"}), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_codex(
                    f"inspect this screenshot {image}",
                    name="image prompt",
                    repo_path=str(self.repo),
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))

    def test_spawn_codex_attaches_managed_drop_images(self):
        """Image drops use the same Codex --image delivery as image paste."""
        server = self.server
        attachment_dir = server.COMMAND_CENTER_ATTACHMENTS_DIR
        attachment_dir.mkdir(parents=True)
        image = attachment_dir / "attachment-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        proc = mock.Mock(pid=4242)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.dict(os.environ, {"CCC_CODEX_SPAWN_APP_SERVER": "0"}), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_codex(
                    f"inspect this screenshot {image}",
                    name="dropped image prompt",
                    repo_path=str(self.repo),
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))

    def test_spawn_session_codex_uses_app_server_when_available(self):
        """Fresh Codex sessions should prefer app-server thread/start."""
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params or {}))
            if method == "thread/start":
                return {"result": {"thread": {"id": sid, "status": {"type": "idle"}, "turns": []}}}
            if method == "thread/name/set":
                return {"result": {}}
            if method == "turn/start":
                server._codex_app_server_handle_message({
                    "jsonrpc": "2.0",
                    "method": "turn/started",
                    "params": {"threadId": sid, "turn": {"id": "turn-1"}},
                })
                return {"result": {"turn": {"id": "turn-1"}}}
            raise AssertionError(f"unexpected method: {method}")

        try:
            with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request), \
                 mock.patch.object(server, "_codex_rollout_stat", return_value=None), \
                 mock.patch.object(server, "_codex_app_server_transport_kind", return_value="managed"), \
                 mock.patch.object(server, "_mark_codex_thread_user_visible", return_value=True), \
                 mock.patch.object(server, "_register_codex_sidebar_project_for_spawn_entry"), \
                 mock.patch.object(server, "_record_spawn_to_registry") as registry, \
                 mock.patch.object(server.subprocess, "Popen", side_effect=AssertionError("exec fallback should not run")), \
                 mock.patch.dict(os.environ, {"CCC_CODEX_WAKE_CONFIRM_TIMEOUT": "0.1"}):
                result = server.spawn_session_codex(
                    "say ok",
                    name="app spawn",
                    repo_path=str(self.repo),
                )
                rows = server.list_spawned_sessions()
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-app-spawn")
        self.assertEqual(result["session_id"], sid)
        self.assertFalse(result["session_id_pending"])
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmation_source"], "app-server-notification")
        self.assertEqual(result["app_server_transport"], "managed")
        self.assertTrue(str(result["spawn_id"]).startswith("codex-app-"))
        self.assertEqual(result["pid"], result["spawn_id"])
        self.assertEqual([method for method, _ in calls], ["thread/start", "thread/name/set", "turn/start"])

        start_params = calls[0][1]
        self.assertEqual(start_params["cwd"], str(self.repo))
        self.assertEqual(start_params["runtimeWorkspaceRoots"], [str(self.repo)])
        self.assertEqual(start_params["approvalPolicy"], "never")
        self.assertEqual(start_params["sandbox"], "danger-full-access")
        self.assertEqual(start_params["model"], "gpt-5.5")
        self.assertEqual(start_params["config"]["model_context_window"], 1000000)

        name_params = calls[1][1]
        self.assertEqual(name_params, {"threadId": sid, "name": "app-spawn"})

        turn_params = calls[2][1]
        self.assertEqual(turn_params["threadId"], sid)
        self.assertEqual(turn_params["cwd"], str(self.repo))
        self.assertEqual(turn_params["input"], [{"type": "text", "text": "say ok"}])
        self.assertEqual(turn_params["approvalPolicy"], "never")
        self.assertEqual(turn_params["sandboxPolicy"], {"type": "dangerFullAccess"})
        registry.assert_not_called()
        self.assertEqual(rows[0]["spawn_id"], result["spawn_id"])
        self.assertEqual(rows[0]["pid"], result["pid"])
        self.assertEqual(rows[0]["session_id"], sid)
        self.assertTrue(rows[0]["running"])
        registry = json.loads(server.CODEX_THREAD_REGISTRY_FILE.read_text())
        reg_thread = registry["threads"][sid]
        self.assertFalse(registry["authoritative"])
        self.assertEqual(reg_thread["thread_id"], sid)
        self.assertEqual(reg_thread["engine"], "codex")
        self.assertEqual(reg_thread["visibility"], "user-visible")
        self.assertEqual(reg_thread["transport_owner"], "ccc-managed-app-server")
        self.assertEqual(reg_thread["transport"], "managed")
        self.assertEqual(reg_thread["cwd"], str(self.repo))
        self.assertEqual(reg_thread["repo_path"], str(self.repo))
        self.assertEqual(reg_thread["model"], "gpt-5.5")
        self.assertEqual(reg_thread["title"], "app-spawn")
        self.assertEqual(reg_thread["ccc"]["spawn_id"], result["spawn_id"])

    def test_spawn_codex_defaults_to_best_model_and_max_context_arg(self):
        """Default Codex spawns should prefer 5.5 while requesting max context."""
        server = self.server
        proc = mock.Mock(pid=4244)
        original_spawns = list(server._spawned_sessions)
        old_defaults = server.SPAWN_DEFAULTS_FILE
        server._spawned_sessions.clear()
        with tempfile.TemporaryDirectory() as td:
            server.SPAWN_DEFAULTS_FILE = pathlib.Path(td) / "spawn-defaults.json"
            try:
                with mock.patch.dict(os.environ, {"CCC_CODEX_SPAWN_APP_SERVER": "0"}, clear=True), \
                     mock.patch.object(
                         server,
                         "_resolve_codex_bin",
                         return_value={"available": True, "bin": "/usr/bin/codex-test"},
                     ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                     mock.patch.object(server, "_record_spawn_to_registry"):
                    result = server.spawn_session_codex(
                        "say ok",
                        name="context prompt",
                        repo_path=str(self.repo),
                    )
            finally:
                for entry in server._spawned_sessions:
                    fh = entry.get("log_fh")
                    if fh:
                        fh.close()
                server._spawned_sessions.clear()
                server._spawned_sessions.extend(original_spawns)
                server.SPAWN_DEFAULTS_FILE = old_defaults

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "model_context_window=1000000")
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-5.5")

    def test_resume_codex_attaches_command_center_pasted_images(self):
        """Resumed Codex sessions need the same pasted-image attachment path."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000003"
        proc = mock.Mock(pid=4243)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.dict(os.environ, {"CCC_CODEX_APP_SERVER": "0"}), \
                 mock.patch.object(
                     server,
                     "_resolve_codex_bin",
                     return_value={"available": True, "bin": "/usr/bin/codex-test"},
                 ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_codex(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))
        self.assertIn(sid, cmd)

    def test_resolve_cursor_bin_honors_env(self):
        server = self.server
        cursor_bin = pathlib.Path(self.tmp_home, "cursor-agent")
        cursor_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cursor_bin.chmod(cursor_bin.stat().st_mode | stat.S_IXUSR)

        with mock.patch.dict(os.environ, {"CCC_CURSOR_BIN": str(cursor_bin)}):
            result = server._resolve_cursor_bin()

        self.assertTrue(result["available"])
        self.assertEqual(result["bin"], str(cursor_bin))
        self.assertEqual(result["source"], "env")

    def test_spawn_cursor_builds_stream_json_command(self):
        server = self.server
        proc = mock.Mock(pid=4247)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry") as record, \
                 mock.patch.object(server, "_wait_for_spawn_session_id", return_value=None):
                result = server.spawn_session_cursor(
                    "do cursor work",
                    name="cursor work",
                    repo_path=str(self.repo),
                    model="composer-2.5",
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "cursor")
        self.assertEqual(result["model"], "composer-2.5")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/cursor-agent-test")
        self.assertIn("--print", cmd)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertIn("--stream-partial-output", cmd)
        self.assertIn("--force", cmd)
        self.assertIn("--trust", cmd)
        self.assertEqual(cmd[cmd.index("--workspace") + 1], str(self.repo))
        self.assertEqual(cmd[cmd.index("--model") + 1], "composer-2.5")
        self.assertEqual(cmd[-1], "do cursor work")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.repo))
        record.assert_called_once()

    def test_resume_cursor_queues_when_resume_already_running(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000004"
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "cursor",
            "resumed_sid": sid,
            "pid": 4248,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_cursor(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "cursor-resume-queued")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_parse_cursor_event_reads_text_and_tool_blocks(self):
        server = self.server
        ev = {
            "role": "assistant",
            "timestamp": "2026-06-01T12:00:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "I will inspect it."},
                    {
                        "type": "tool_use",
                        "id": "toolu-cursor",
                        "name": "run_terminal_cmd",
                        "input": {"command": "git status --short"},
                    },
                ],
            },
        }

        parsed = server._parse_cursor_event(ev, 7)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual(parsed["message_id"], "cursor-7")
        self.assertEqual(parsed["blocks"][0]["kind"], "text")
        self.assertEqual(parsed["blocks"][0]["text"], "I will inspect it.")
        self.assertEqual(parsed["blocks"][1]["kind"], "tool_use")
        self.assertEqual(parsed["blocks"][1]["name"], "run_terminal_cmd")
        self.assertEqual(parsed["blocks"][1]["id"], "toolu-cursor")
        self.assertIn("git status --short", parsed["blocks"][1].get("detail", ""))

    def test_parse_cursor_event_skips_redacted_placeholder_text(self):
        server = self.server
        ev = {
            "role": "assistant",
            "timestamp": "2026-06-01T12:00:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "[REDACTED]"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"path": str(self.repo / "server.py")},
                    },
                    {"type": "text", "text": "Done.\n\n[REDACTED]"},
                ],
            },
        }

        parsed = server._parse_cursor_event(ev, 8)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual([b["kind"] for b in parsed["blocks"]], ["tool_use", "text"])
        self.assertEqual(parsed["blocks"][1]["text"], "Done.")
        self.assertNotIn("[REDACTED]", json.dumps(parsed))

    def test_resume_cursor_prefers_current_default_over_stale_spawn_model(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000006"
        proc = mock.Mock(pid=4249)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.dict(os.environ, {"CCC_CURSOR_MODEL": ""}), \
                 mock.patch.object(
                     server,
                     "_resolve_cursor_bin",
                     return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
                 ), mock.patch.object(
                     server,
                     "_spawn_registry_entry_for_session",
                     return_value={"cwd": str(self.repo), "model": "composer-2.5-fast"},
                 ), mock.patch.object(server, "_cursor_transcript_path", return_value=None), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server, "_spawn_model_for_engine", return_value="auto"), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_cursor(sid, "second")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "auto")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "auto")

    def test_resume_cursor_reports_immediate_usage_limit_failure(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000008"
        proc = mock.Mock(pid=4250)
        proc.poll.return_value = 0
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(
                server,
                "_spawn_registry_entry_for_session",
                return_value={"cwd": str(self.repo), "model": "auto"},
            ), mock.patch.object(server, "_cursor_transcript_path", return_value=None), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                 mock.patch.object(server.time, "sleep"), \
                 mock.patch.object(
                     server,
                     "_antigravity_read_log_tail",
                     return_value="S: You've hit your usage limit Get Cursor Pro for more Agent usage.",
                 ), mock.patch.object(server, "_record_spawn_to_registry") as record:
                result = server.resume_session_cursor(sid, "second")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertFalse(result["ok"])
        self.assertEqual(result["via"], "cursor-resume")
        self.assertIn("usage limit", result["error"])
        record.assert_not_called()

    def test_resolve_cursor_bin_uses_local_bin_candidate(self):
        server = self.server
        cursor_bin = pathlib.Path(self.tmp_home, ".local", "bin", "cursor-agent")
        cursor_bin.parent.mkdir(parents=True)
        cursor_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cursor_bin.chmod(cursor_bin.stat().st_mode | stat.S_IXUSR)

        with mock.patch.dict(os.environ, {"CCC_CURSOR_BIN": ""}), \
             mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server, "CURSOR_LOCAL_BIN", cursor_bin), \
             mock.patch.object(server, "CURSOR_APP_BUNDLE_CANDIDATES", ()):
            result = server._resolve_cursor_bin()

        self.assertTrue(result["available"])
        self.assertEqual(result["bin"], str(cursor_bin))
        self.assertEqual(result["source"], "candidate")

    def test_find_cursor_conversations_reads_agent_transcript(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000005"
        slug = server._cursor_project_slug(self.repo)
        transcript_dir = server.CURSOR_PROJECTS_ROOT / slug / "agent-transcripts" / sid
        transcript_dir.mkdir(parents=True)
        transcript_path = transcript_dir / f"{sid}.jsonl"
        transcript_path.write_text(
            "\n".join([
                json.dumps({
                    "role": "user",
                    "timestamp": "2026-06-01T12:00:00Z",
                    "message": {"content": [{"type": "text", "text": "<user_query>\nPlease inspect\n</user_query>"}]},
                }),
                json.dumps({
                    "role": "assistant",
                    "timestamp": "2026-06-01T12:01:00Z",
                    "message": {
                        "content": [
                            {"type": "text", "text": "I will commit it."},
                            {
                                "type": "tool_use",
                                "name": "run_terminal_cmd",
                                "input": {"command": "git commit -m 'cursor test'"},
                            },
                        ],
                    },
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        rows = server.find_cursor_conversations(
            repo_path=str(self.repo),
            include_old=True,
            repo_only=True,
            resolve_pr_states=False,
            resolve_worktree_dirty=False,
        )
        parsed = server.parse_conversation(sid, use_cache=False)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cursor")
        self.assertEqual(rows[0]["engine"], "cursor")
        self.assertEqual(rows[0]["first_message"], "Please inspect")
        self.assertTrue(rows[0]["has_commit"])
        self.assertEqual(parsed["events"][0]["type"], "user_text")
        self.assertEqual(parsed["events"][1]["type"], "assistant")

    def test_find_cursor_conversations_ignores_redacted_placeholder_tail(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000007"
        slug = server._cursor_project_slug(self.repo)
        transcript_dir = server.CURSOR_PROJECTS_ROOT / slug / "agent-transcripts" / sid
        transcript_dir.mkdir(parents=True)
        transcript_path = transcript_dir / f"{sid}.jsonl"
        transcript_path.write_text(
            "\n".join([
                json.dumps({
                    "role": "user",
                    "timestamp": "2026-06-01T12:00:00Z",
                    "message": {"content": [{"type": "text", "text": "Please inspect"}]},
                }),
                json.dumps({
                    "role": "assistant",
                    "timestamp": "2026-06-01T12:01:00Z",
                    "message": {
                        "content": [
                            {"type": "text", "text": "[REDACTED]"},
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "input": {"pattern": "needle", "path": str(self.repo)},
                            },
                        ],
                    },
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        server._conv_meta_cache.clear()

        rows = server.find_cursor_conversations(
            repo_path=str(self.repo),
            include_old=True,
            repo_only=True,
            resolve_pr_states=False,
            resolve_worktree_dirty=False,
        )
        row = next(r for r in rows if r["session_id"] == sid)
        parsed = server.parse_conversation(sid, use_cache=False)

        self.assertIsNone(row["last_assistant_text"])
        self.assertIsNone(row["pending_tool"])
        self.assertEqual(parsed["events"][1]["blocks"][0]["kind"], "tool_use")
        self.assertNotIn("[REDACTED]", json.dumps(parsed["events"]))

    def test_codex_app_server_active_turn_falls_back_to_durable_queue(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params, timeout))
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "active", "activeFlags": []},
                            "turns": [
                                {"id": "turn-old", "status": "completed"},
                                {"id": "turn-active", "status": "inProgress"},
                            ],
                        }
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_resume_or_steer_via_app_server(
                sid,
                "look here",
                cwd=str(self.repo),
                model="gpt-test",
                image_paths=["/tmp/paste.png"],
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["fallback"], "queue")
        self.assertEqual([call[0] for call in calls], ["thread/resume"])

    def test_resume_codex_active_app_server_turn_uses_durable_queue(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "active", "activeFlags": []},
                            "turns": [
                                {"id": "turn-active", "status": "inProgress"},
                            ],
                        }
                    }
                }
            if method == "turn/start":
                return {"result": {"turn": {"id": "turn-next"}}}
            raise AssertionError(f"unexpected method: {method}")

        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
                result = server.resume_session_codex(sid, "keep this")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "codex-resume-queued")
            self.assertEqual(calls, ["thread/resume"])
            with server._pending_resume_lock:
                self.assertEqual(server._pending_resume_queue.get(sid), ["keep this"])
        finally:
            with server._pending_resume_lock:
                server._pending_resume_queue.clear()
                server._pending_resume_queue.update(original_queue)

    def test_resume_queue_busy_detects_codex_app_server_active_thread(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"

        def fake_request(method, params=None, timeout=20):
            self.assertEqual(method, "thread/resume")
            return {
                "result": {
                    "thread": {
                        "status": {"type": "active", "activeFlags": []},
                        "turns": [{"id": "turn-active", "status": "inProgress"}],
                    }
                }
            }

        with mock.patch.object(server, "_is_codex_session", return_value=True), \
             mock.patch.object(server, "_codex_app_server_is_live", return_value=True), \
             mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            self.assertTrue(server._resume_queue_engine_busy(sid))

    def test_codex_app_server_steers_active_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params, timeout))
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "active", "activeFlags": []},
                            "turns": [
                                {"id": "turn-old", "status": "completed"},
                                {"id": "turn-active", "status": "inProgress"},
                            ],
                        }
                    }
                }
            if method == "turn/steer":
                return {"result": {"turnId": "turn-active"}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_steer_via_app_server(
                sid,
                "look now",
                cwd=str(self.repo),
                model="gpt-test",
                image_paths=["/tmp/paste.png"],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        self.assertEqual(calls[0][0], "thread/resume")
        self.assertEqual(calls[1][0], "turn/steer")
        steer_params = calls[1][1]
        self.assertEqual(steer_params["expectedTurnId"], "turn-active")
        self.assertEqual(
            steer_params["input"],
            [
                {"type": "text", "text": "look now"},
                {"type": "localImage", "path": "/tmp/paste.png"},
            ],
        )

    def test_codex_app_server_steer_requires_active_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "idle"},
                            "turns": [],
                        }
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_steer_via_app_server(sid, "look now")

        self.assertFalse(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        self.assertEqual(result["code"], "codex_no_active_turn")
        self.assertEqual(calls, ["thread/resume"])

    def test_codex_app_server_does_not_start_parallel_turn_when_disallowed(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "idle"},
                            "turns": [],
                        }
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_resume_or_steer_via_app_server(
                sid,
                "second",
                allow_start=False,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["fallback"], "queue")
        self.assertEqual(calls, ["thread/resume"])

    def test_codex_app_server_tracks_notifications(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "turn-active"}},
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {"threadId": sid, "turnId": "turn-active", "delta": "ok"},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["active_turn_id"], "turn-active")
        self.assertEqual(state["last_turn_id"], "turn-active")
        self.assertGreater(state["event_seq"], 0)

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {"threadId": sid, "turnId": "turn-active"},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["last_completed_turn_id"], "turn-active")
        self.assertNotIn("active_turn_id", state)

    def test_codex_turn_completed_schedules_queue_pump(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"

        with mock.patch.object(server, "_schedule_codex_queue_pump") as schedule:
            server._codex_app_server_handle_message({
                "method": "turn/completed",
                "params": {"threadId": sid, "turnId": "turn-1"},
            })

        schedule.assert_called_once_with(sid)

    def test_codex_idle_status_schedules_queue_pump(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"

        with mock.patch.object(server, "_schedule_codex_queue_pump") as schedule:
            server._codex_app_server_handle_message({
                "method": "thread/status/changed",
                "params": {
                    "threadId": sid,
                    "status": {"type": "idle", "activeFlags": []},
                },
            })

        schedule.assert_called_once_with(sid)

    def test_codex_late_tool_output_does_not_resurrect_completed_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()

        server._codex_app_server_handle_message({
            "method": "turn/started",
            "params": {"threadId": sid, "turnId": "turn-done"},
        })
        server._codex_app_server_handle_message({
            "method": "turn/completed",
            "params": {"threadId": sid, "turnId": "turn-done"},
        })
        server._codex_app_server_handle_message({
            "method": "item/commandExecution/outputDelta",
            "params": {
                "threadId": sid,
                "turnId": "turn-done",
                "itemId": "late-shell",
                "delta": "late process output",
            },
        })

        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["status"], "idle")
        self.assertNotIn("active_turn_id", state)
        self.assertNotIn("active_item", state)
        self.assertIsNone(server._codex_app_server_activity_fields(sid)["sidecar_tool"])

    def test_codex_app_server_turn_started_tracks_writer_ownership(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                "ccc_turn_start_pending": True,
            }

        server._codex_app_server_handle_message({
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "ccc-turn"}},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["active_writer"], "ccc")

        server._codex_app_server_handle_message({
            "method": "turn/completed",
            "params": {"threadId": sid, "turn": {"id": "ccc-turn"}},
        })
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE[sid].pop("ccc_turn_start_pending", None)
        server._codex_app_server_handle_message({
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "external-turn"}},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["active_writer"], "unknown")

    def test_codex_app_server_turn_start_response_marks_ccc_owner(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()

        response = {"result": {"turn": {"id": "ccc-turn"}}}
        with mock.patch.object(server, "_ensure_codex_app_server", return_value=object()), \
             mock.patch.object(server, "_codex_app_server_request_to_transport", return_value=response):
            result = server._codex_app_server_request(
                "turn/start", {"threadId": sid, "input": []}, timeout=1,
            )

        self.assertIs(result, response)
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["active_turn_id"], "ccc-turn")
        self.assertEqual(state["active_writer"], "ccc")
        self.assertNotIn("ccc_turn_start_pending", state)

    def test_codex_app_server_item_activity_feeds_live_ui_fields(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "turn-active"}},
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/started",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "startedAtMs": 1783600000000,
                "item": {
                    "id": "item-shell",
                    "type": "commandExecution",
                    "status": "inProgress",
                    "cwd": str(self.repo),
                    "command": "python3 -m pytest tests/test_smoke.py -q",
                    "commandActions": [],
                },
            },
        })

        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["active_item"]["tool"], "Bash")
        self.assertIn("pytest", state["active_item"]["detail"])
        fields = server._codex_app_server_activity_fields(sid)
        self.assertEqual(fields["sidecar_status"], "active")
        self.assertEqual(fields["sidecar_tool"], "Bash")
        self.assertIn("pytest", fields["sidecar_file"])
        self.assertTrue(fields["sidecar_in_flight"])
        self.assertEqual(server._codex_state_fields(sid)["codex_state"], "working")

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "completedAtMs": 1783600005000,
                "item": {
                    "id": "item-shell",
                    "type": "commandExecution",
                    "status": "completed",
                    "cwd": str(self.repo),
                    "command": "python3 -m pytest tests/test_smoke.py -q",
                    "commandActions": [],
                    "exitCode": 0,
                    "aggregatedOutput": "1 passed",
                },
            },
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertNotIn("active_item", state)
        self.assertEqual(state["recent_items"][-1]["tool"], "Bash")
        self.assertIn("1 passed", state["recent_items"][-1]["output"])
        self.assertEqual(server._codex_app_server_activity_fields(sid)["sidecar_tool"], "Thinking")

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {"threadId": sid, "turnId": "turn-active"},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["status"], "idle")
        self.assertNotIn("active_item", state)
        self.assertIsNone(server._codex_app_server_activity_fields(sid)["sidecar_tool"])

    def test_codex_app_server_approval_item_feeds_waiting_ui_fields(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "turn-active"}},
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/started",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "startedAtMs": 1783600000000,
                "item": {
                    "id": "item-approval",
                    "type": "commandExecution",
                    "status": "waiting_for_approval",
                    "command": "rm -rf /tmp/nope",
                    "commandActions": [
                        {"id": "approve", "label": "Approve"},
                        {"id": "deny", "label": "Deny"},
                    ],
                    "approvalMessage": "Allow destructive command?",
                },
            },
        })

        state = server._codex_app_server_thread_state(sid)
        self.assertTrue(state["active_item"]["needs_approval"])
        self.assertEqual(state["active_item"]["approval_message"], "Allow destructive command?")
        fields = server._codex_app_server_activity_fields(sid)
        self.assertTrue(fields["needs_approval"])
        self.assertEqual(fields["needs_approval_message"], "Allow destructive command?")
        self.assertEqual(fields["sidecar_tool"], "Bash")
        self.assertTrue(fields["sidecar_in_flight"])
        state_fields = server._codex_state_fields(sid)
        self.assertEqual(state_fields["codex_state"], "waiting")
        self.assertEqual(state_fields["codex_state_reason"], "Allow destructive command?")
        events = server._get_codex_app_server_item_events_for_session(sid)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["needs_approval"])
        self.assertIn("needs approval", events[0]["text"])

    def test_codex_app_server_request_approval_is_actionable(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_RESPONSES.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "id": "approval-req-1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "itemId": "item-shell",
                "startedAtMs": 1783600000000,
                "command": "rm -rf /tmp/nope",
                "reason": "Allow destructive command?",
                "availableDecisions": ["accept", "decline", "cancel"],
            },
        })

        with server._CODEX_APP_SERVER_LOCK:
            self.assertNotIn("approval-req-1", server._CODEX_APP_SERVER_RESPONSES)
        state = server._codex_app_server_thread_state(sid)
        pending = state["pending_approval_request"]
        self.assertEqual(pending["request_id"], "approval-req-1")
        self.assertEqual(pending["approval_method"], "item/commandExecution/requestApproval")
        self.assertEqual(pending["tool"], "Bash")
        self.assertTrue(pending["can_approve"])
        self.assertTrue(state["thread_needs_approval"])
        public = server._codex_app_server_thread_public_status(sid)
        self.assertEqual(public["active_item"]["request_id"], "approval-req-1")
        self.assertTrue(public["active_item"]["can_approve"])
        self.assertTrue(public["active_item"]["needs_approval"])
        fields = server._codex_app_server_activity_fields(sid)
        self.assertTrue(fields["needs_approval"])
        self.assertEqual(fields["needs_approval_message"], "Allow destructive command?")

    def test_codex_app_server_resolve_approval_sends_json_rpc_response(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"

        class FakeTransport:
            kind = "managed-unix"

            def __init__(self):
                self.sent = []

            def alive(self):
                return True

            def send_json(self, payload):
                self.sent.append(payload)

        fake = FakeTransport()
        with server._CODEX_APP_SERVER_LOCK:
            old_transport = server._CODEX_APP_SERVER_TRANSPORT
            old_initialized = server._CODEX_APP_SERVER_INITIALIZED
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_RESPONSES.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0
            server._CODEX_APP_SERVER_TRANSPORT = fake
            server._CODEX_APP_SERVER_INITIALIZED = True
        try:
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "id": "approval-req-2",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": sid,
                    "turnId": "turn-active",
                    "itemId": "item-shell",
                    "startedAtMs": 1783600000000,
                    "command": "pytest -q",
                    "reason": "Allow test command?",
                },
            })
            result = server._codex_app_server_resolve_approval(sid, "acceptForSession")
        finally:
            with server._CODEX_APP_SERVER_LOCK:
                server._CODEX_APP_SERVER_TRANSPORT = old_transport
                server._CODEX_APP_SERVER_INITIALIZED = old_initialized

        self.assertTrue(result["ok"])
        self.assertEqual(fake.sent, [{
            "jsonrpc": "2.0",
            "id": "approval-req-2",
            "result": {"decision": "acceptForSession"},
        }])
        state = server._codex_app_server_thread_state(sid)
        self.assertNotIn("pending_approval_request", state)
        self.assertFalse(state["thread_needs_approval"])
        self.assertFalse(state["active_item"]["needs_approval"])

    def test_codex_app_server_thread_waiting_on_approval_flag_feeds_ui_fields(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "thread/status/changed",
            "params": {
                "threadId": sid,
                "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
            },
        })

        state = server._codex_app_server_thread_state(sid)
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["active_flags"], ["waitingOnApproval"])
        self.assertTrue(state["thread_needs_approval"])
        fields = server._codex_app_server_activity_fields(sid)
        self.assertTrue(fields["needs_approval"])
        self.assertEqual(fields["sidecar_status"], "active")
        self.assertEqual(fields["sidecar_tool"], "Approval")
        state_fields = server._codex_state_fields(sid)
        self.assertEqual(state_fields["codex_state"], "waiting")
        self.assertIn("approval", state_fields["codex_state_reason"].lower())

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "thread/status/changed",
            "params": {"threadId": sid, "status": {"type": "idle", "activeFlags": []}},
        })
        state = server._codex_app_server_thread_state(sid)
        self.assertFalse(state["thread_needs_approval"])
        self.assertEqual(state["active_flags"], [])
        self.assertFalse(server._codex_app_server_activity_fields(sid)["needs_approval"])

    def test_codex_app_server_public_status_exposes_active_item_and_token_usage(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "turn/started",
            "params": {"threadId": sid, "turn": {"id": "turn-active"}},
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/started",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "item": {
                    "id": "item-shell",
                    "type": "commandExecution",
                    "status": "inProgress",
                    "command": "python3 -m pytest tests/test_smoke.py -q",
                },
            },
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 144000,
                        "cachedInputTokens": 5000,
                        "outputTokens": 25,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 144025,
                    },
                    "total": {
                        "inputTokens": 152712,
                        "cachedInputTokens": 5000,
                        "outputTokens": 171,
                        "reasoningOutputTokens": 106,
                        "totalTokens": 152883,
                    },
                    "modelContextWindow": 258400,
                },
            },
        })

        public = server._codex_app_server_thread_public_status(sid)
        self.assertEqual(public["active_item"]["tool"], "Bash")
        self.assertIn("pytest", public["active_item"]["detail"])
        self.assertEqual(public["last_item_id"], "item-shell")
        usage = public["token_usage"]
        self.assertEqual(usage["input_tokens"], 152712)
        self.assertEqual(usage["cached_input_tokens"], 5000)
        self.assertEqual(usage["output_tokens"], 171)
        self.assertEqual(usage["reasoning_output_tokens"], 106)
        self.assertEqual(usage["total_tokens"], 152883)
        self.assertEqual(usage["context_limit"], 258400)
        self.assertEqual(usage["used_percent"], 59.2)
        self.assertEqual(usage["last"]["input_tokens"], 144000)

    def test_codex_app_server_text_items_do_not_render_as_overlays(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        for item in (
            {"id": "user-1", "type": "userMessage", "text": "hello"},
            {"id": "agent-1", "type": "agentMessage", "text": "hi"},
            {"id": "reason-1", "type": "reasoning", "summary": ["thinking"]},
        ):
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "method": "item/completed",
                "params": {
                    "threadId": sid,
                    "turnId": "turn-active",
                    "completedAtMs": 1783600005000,
                    "item": item,
                },
            })

        self.assertEqual(server._get_codex_app_server_item_events_for_session(sid), [])

    def test_codex_app_server_items_render_as_conversation_overlays(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "completedAtMs": 1783600005000,
                "item": {
                    "id": "item-shell",
                    "type": "commandExecution",
                    "status": "completed",
                    "command": "wt find CHUCK-51 --json",
                    "exitCode": 0,
                    "aggregatedOutput": "{\"ref\":\"CHUCK-51\"}",
                },
            },
        })
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "threadId": sid,
                "turnId": "turn-active",
                "completedAtMs": 1783600006000,
                "item": {"id": "empty-thinking", "type": "reasoning", "status": "completed"},
            },
        })

        events = server._get_codex_app_server_item_events_for_session(sid)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["subtype"], "codex_app_server_item")
        self.assertEqual(events[0]["tool"], "Bash")
        self.assertIn("wt find", events[0]["detail"])
        self.assertIn("CHUCK-51", events[0]["output"])

        with mock.patch.object(server, "_detect_session_engine", return_value="codex"):
            queued = server._get_queued_events_for_session(sid)
        self.assertTrue(any(ev.get("subtype") == "codex_app_server_item" for ev in queued))

    def test_codex_app_server_wake_confirms_from_notification(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {"result": {"thread": {"id": sid, "status": {"type": "idle"}, "turns": []}}}
            if method == "turn/start":
                server._codex_app_server_handle_message({
                    "jsonrpc": "2.0",
                    "method": "turn/started",
                    "params": {"threadId": sid, "turn": {"id": "turn-next"}},
                })
                return {"result": {"turn": {"id": "turn-next"}}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request), \
             mock.patch.object(server, "_codex_rollout_stat", return_value=None), \
             mock.patch.dict(os.environ, {"CCC_CODEX_WAKE_CONFIRM_TIMEOUT": "0.1"}):
            result = server._codex_resume_or_steer_via_app_server(sid, "wake")

        self.assertTrue(result["ok"])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmation_source"], "app-server-notification")
        self.assertEqual(calls, ["thread/resume", "turn/start"])

    def test_codex_app_server_wake_warns_when_no_events_follow(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"

        def fake_request(method, params=None, timeout=20):
            if method == "thread/resume":
                return {"result": {"thread": {"id": sid, "status": {"type": "idle"}, "turns": []}}}
            if method == "turn/start":
                return {"result": {"turn": {"id": "turn-next"}}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request), \
             mock.patch.object(server, "_codex_rollout_stat", return_value=None), \
             mock.patch.dict(os.environ, {"CCC_CODEX_WAKE_CONFIRM_TIMEOUT": "0"}):
            result = server._codex_resume_or_steer_via_app_server(sid, "wake")

        self.assertTrue(result["ok"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["warning"], "turn accepted but no app-server events observed")

    def test_codex_app_server_notifications_persist_state_snapshot(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        old_file = server.CODEX_APP_SERVER_STATE_FILE
        state_file = pathlib.Path(self.tmp_home) / "codex-app-server-state.json"
        server.CODEX_APP_SERVER_STATE_FILE = state_file
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0
        try:
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "method": "turn/started",
                "params": {"threadId": sid, "turn": {"id": "turn-1"}},
            })
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": sid,
                    "turnId": "turn-1",
                    "tokenUsage": {"inputTokens": 10, "outputTokens": 2},
                },
            })
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "method": "turn/completed",
                "params": {"threadId": sid, "turnId": "turn-1"},
            })
            payload = json.loads(state_file.read_text())
        finally:
            server.CODEX_APP_SERVER_STATE_FILE = old_file

        self.assertEqual(payload["schema_version"], 1)
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["source"], "codex-app-server-notifications")
        thread = payload["threads"][sid]
        self.assertEqual(thread["status"], "idle")
        self.assertEqual(thread["last_completed_turn_id"], "turn-1")
        self.assertEqual(thread["token_usage"], {"inputTokens": 10, "outputTokens": 2})
        self.assertNotIn("active_turn_id", thread)

    def test_codex_telemetry_append_writes_jsonl(self):
        server = self.server
        old_file = server.CODEX_TELEMETRY_FILE
        telemetry_file = pathlib.Path(self.tmp_home) / "codex-telemetry.jsonl"
        server.CODEX_TELEMETRY_FILE = telemetry_file
        try:
            server._codex_telemetry_append(
                "codex_metric_test",
                ok=True,
                none_value=None,
                latency_ms=1.5,
            )
            payload = json.loads(telemetry_file.read_text().strip())
        finally:
            server.CODEX_TELEMETRY_FILE = old_file

        self.assertEqual(payload["event"], "codex_metric_test")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["latency_ms"], 1.5)
        self.assertNotIn("none_value", payload)

    def test_codex_app_server_wake_records_latency_telemetry(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        old_file = server.CODEX_TELEMETRY_FILE
        telemetry_file = pathlib.Path(self.tmp_home) / "codex-telemetry.jsonl"
        server.CODEX_TELEMETRY_FILE = telemetry_file

        def fake_request(method, params=None, timeout=20):
            if method == "thread/resume":
                return {"result": {"thread": {"id": sid, "status": {"type": "idle"}, "turns": []}}}
            if method == "turn/start":
                return {"result": {"turn": {"id": "turn-next"}}}
            raise AssertionError(f"unexpected method: {method}")

        try:
            with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request), \
                 mock.patch.object(server, "_codex_rollout_stat", return_value=None), \
                 mock.patch.object(server, "_codex_app_server_is_live", return_value=True), \
                 mock.patch.object(server, "_codex_app_server_transport_kind", return_value="managed"), \
                 mock.patch.dict(os.environ, {"CCC_CODEX_WAKE_CONFIRM_TIMEOUT": "0"}):
                result = server._codex_resume_or_steer_via_app_server(
                    sid,
                    "wake",
                    cwd=str(self.repo),
                    model="gpt-test",
                )
            events = [json.loads(line) for line in telemetry_file.read_text().splitlines()]
        finally:
            server.CODEX_TELEMETRY_FILE = old_file
            server._CODEX_TELEMETRY_TURNS.pop("turn-next", None)

        self.assertTrue(result["ok"])
        self.assertIn("resume_ms", result)
        self.assertIn("turn_start_ms", result)
        self.assertIn("confirm_ms", result)
        wake = next(e for e in events if e["event"] == "codex_wake")
        self.assertTrue(wake["ok"])
        self.assertTrue(wake["app_server_warm"])
        self.assertEqual(wake["transport"], "managed")
        self.assertEqual(wake["session_id"], sid)
        self.assertEqual(wake["turn_id"], "turn-next")
        self.assertGreaterEqual(wake["resume_ms"], 0)
        self.assertGreaterEqual(wake["turn_start_ms"], 0)
        self.assertGreaterEqual(wake["total_ms"], wake["resume_ms"])

    def test_codex_app_server_notification_records_visible_output_telemetry(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        old_file = server.CODEX_TELEMETRY_FILE
        telemetry_file = pathlib.Path(self.tmp_home) / "codex-telemetry.jsonl"
        server.CODEX_TELEMETRY_FILE = telemetry_file
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE.clear()
            server._CODEX_APP_SERVER_TURN_THREAD.clear()
            server._CODEX_APP_SERVER_EVENT_SEQ = 0
        try:
            server._codex_telemetry_register_turn(
                sid,
                "turn-1",
                path="wake",
                started_at_monotonic=time.monotonic() - 0.01,
                transport="managed",
                cwd=str(self.repo),
                model="gpt-test",
            )
            server._codex_app_server_handle_message({
                "jsonrpc": "2.0",
                "method": "item/agentMessage/delta",
                "params": {"threadId": sid, "turnId": "turn-1", "delta": "hello"},
            })
            events = [json.loads(line) for line in telemetry_file.read_text().splitlines()]
        finally:
            server.CODEX_TELEMETRY_FILE = old_file
            server._CODEX_TELEMETRY_TURNS.pop("turn-1", None)

        self.assertEqual(
            [e["event"] for e in events],
            ["codex_turn_first_notification", "codex_turn_first_visible_output"],
        )
        self.assertEqual(events[0]["thread_id"], sid)
        self.assertEqual(events[0]["turn_id"], "turn-1")
        self.assertEqual(events[0]["transport"], "managed")
        self.assertGreaterEqual(events[0]["latency_ms"], 0)

    def test_codex_thread_registry_feeds_spawn_registry_fallback(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        old_file = server.CODEX_THREAD_REGISTRY_FILE
        registry_file = pathlib.Path(self.tmp_home) / "codex-thread-registry.json"
        server.CODEX_THREAD_REGISTRY_FILE = registry_file
        try:
            server._codex_thread_registry_upsert(
                sid,
                source="wt-workers",
                visibility="worker",
                transport_owner="wt-codex-exec",
                transport="codex-exec",
                cwd=str(self.repo),
                repo_path=str(self.repo),
                worker_id="q-12345678",
                queue="Q",
                ref="Q-1",
                model="gpt-test",
                wt={"worker_id": "q-12345678", "log": "/tmp/q.log"},
            )
            entries = server._spawn_registry_entries_by_session(engine="codex")
        finally:
            server.CODEX_THREAD_REGISTRY_FILE = old_file

        self.assertIn(sid, entries)
        self.assertTrue(entries[sid]["codex_thread_registry"])
        self.assertEqual(entries[sid]["engine"], "codex")
        self.assertEqual(entries[sid]["cwd"], str(self.repo))
        self.assertEqual(entries[sid]["worker_id"], "q-12345678")
        self.assertEqual(entries[sid]["queue"], "Q")
        self.assertEqual(entries[sid]["ref"], "Q-1")
        self.assertEqual(entries[sid]["model"], "gpt-test")

    def test_codex_app_server_prefers_managed_socket(self):
        server = self.server

        class FakeTransport:
            kind = "managed-unix"
            proc = None

            def __init__(self):
                self.sent = []

            def alive(self):
                return True

            def send_json(self, payload):
                self.sent.append(payload)

            def close(self):
                pass

        class FakeThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as td:
            sock = pathlib.Path(td) / "app-server.sock"
            sock.write_text("")
            server._codex_app_server_shutdown()
            try:
                with mock.patch.object(server, "_codex_managed_app_server_socket_path", return_value=sock), \
                     mock.patch.object(server, "_connect_codex_managed_app_server", return_value=transport), \
                     mock.patch.object(server, "_codex_app_server_request_to_transport", return_value={"result": {}}), \
                     mock.patch.object(server.threading, "Thread", FakeThread), \
                     mock.patch.object(server.subprocess, "Popen") as popen:
                    result = server._ensure_codex_app_server()
            finally:
                server._codex_app_server_shutdown()

        self.assertIs(result, transport)
        self.assertEqual(result.kind, "managed-unix")
        popen.assert_not_called()

    def test_codex_app_server_falls_back_to_stdio_when_managed_fails(self):
        server = self.server
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.stdin = mock.Mock()
        proc.stdout = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            sock = pathlib.Path(td) / "app-server.sock"
            sock.write_text("")
            server._codex_app_server_shutdown()
            try:
                with mock.patch.object(server, "_codex_managed_app_server_socket_path", return_value=sock), \
                     mock.patch.object(server, "_connect_codex_managed_app_server", side_effect=OSError("nope")), \
                     mock.patch.object(server, "_resolve_codex_bin", return_value={"available": True, "bin": "/usr/bin/codex-test"}), \
                     mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                     mock.patch.object(server, "_codex_app_server_request_to_transport", return_value={"result": {}}), \
                     mock.patch.object(server.threading, "Thread", FakeThread):
                    result = server._ensure_codex_app_server()
            finally:
                server._codex_app_server_shutdown()

        self.assertIsNotNone(result)
        self.assertEqual(result.kind, "stdio")

    def test_codex_app_server_transport_kind_labels_managed_and_stdio(self):
        server = self.server

        class FakeTransport:
            def __init__(self, kind):
                self.kind = kind

            def alive(self):
                return True

        with server._CODEX_APP_SERVER_LOCK:
            old_transport = server._CODEX_APP_SERVER_TRANSPORT
            old_initialized = server._CODEX_APP_SERVER_INITIALIZED
            try:
                server._CODEX_APP_SERVER_INITIALIZED = True
                server._CODEX_APP_SERVER_TRANSPORT = FakeTransport("managed-unix")
                self.assertEqual(server._codex_app_server_transport_kind(), "managed")
                server._CODEX_APP_SERVER_TRANSPORT = FakeTransport("stdio")
                self.assertEqual(server._codex_app_server_transport_kind(), "stdio")
                server._CODEX_APP_SERVER_INITIALIZED = False
                self.assertIsNone(server._codex_app_server_transport_kind())
            finally:
                server._CODEX_APP_SERVER_TRANSPORT = old_transport
                server._CODEX_APP_SERVER_INITIALIZED = old_initialized

    def test_codex_managed_app_server_ui_label_is_present(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("managed app-server", app_js)
        self.assertIn("codex_app_server_event_seq", app_js)
        self.assertIn("codex_app_server_item", app_js)
        self.assertIn("/api/codex/approval", app_js)
        self.assertIn("data-decision=\"acceptForSession\"", app_js)
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("codex_app_server_transport", server_py)
        self.assertIn("_schedule_codex_managed_app_server_warmup()", server_py)
        self.assertIn("name=\"codex-managed-app-server-warmup\"", server_py)

    def test_resume_codex_prefers_app_server_before_queued_cli_resume(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "codex",
            "resumed_sid": sid,
            "pid": 4242,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(
                     server,
                     "_codex_resume_or_steer_via_app_server",
                     return_value={"ok": True, "queued": True, "via": "codex-app-queued"},
                 ) as app_queue, \
                 mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_codex(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)
            with server._pending_resume_lock:
                server._pending_resume_queue.clear()
                server._pending_resume_queue.update(original_queue)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "codex-app-queued")
        app_queue.assert_called_once()
        popen.assert_not_called()

    def test_resume_codex_preserves_existing_queue_order(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
            server._pending_resume_queue[sid] = ["first"]
        try:
            with mock.patch.object(server, "_resolve_codex_bin") as resolve_bin, \
                 mock.patch.object(server, "_codex_resume_or_steer_via_app_server") as app_send, \
                 mock.patch.object(server, "_schedule_codex_queue_pump") as schedule:
                result = server.resume_session_codex(sid, "second")
        finally:
            with server._pending_resume_lock:
                queued = list(server._pending_resume_queue.get(sid, []))
                server._pending_resume_queue.clear()
                server._pending_resume_queue.update(original_queue)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(queued, ["first", "second"])
        schedule.assert_called_once_with(sid)
        resolve_bin.assert_not_called()
        app_send.assert_not_called()

    def test_resume_antigravity_adds_pasted_image_dir(self):
        """AGY needs pasted-image folders in its repeatable --add-dir workspace."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        proc = mock.Mock(pid=4244)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_antigravity(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        add_dirs = [cmd[i + 1] for i, word in enumerate(cmd[:-1]) if word == "--add-dir"]
        self.assertIn(str(self.repo), add_dirs)
        self.assertIn(str(paste_dir.resolve()), add_dirs)

    def test_spawn_antigravity_writes_model_to_cli_settings(self):
        """AGY print mode reads its model from settings.json, not argv."""
        server = self.server
        settings_path = server.ANTIGRAVITY_CLI_SETTINGS
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "colorScheme": "dark",
            "model": "Gemini 3.1 Pro (Low)",
        }))
        proc = mock.Mock(pid=4246)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_antigravity(
                    "hello from agy",
                    name="agy model",
                    repo_path=str(self.repo),
                    model="gemini-3.5-flash-high",
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "Gemini 3.5 Flash (High)")
        self.assertEqual(result["engine"], "antigravity")
        self.assertEqual(result["repo_path"], str(self.repo))
        self.assertEqual(result["cwd"], str(self.repo))
        self.assertRegex(result["session_id"], r"^[0-9a-f-]{36}$")
        self.assertFalse(result["session_id_pending"])
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["model"], "Gemini 3.5 Flash (High)")
        self.assertEqual(settings["colorScheme"], "dark")
        cmd = popen.call_args.args[0]
        self.assertNotIn("--model", cmd)
        self.assertIn("-p", cmd)

    def test_resume_antigravity_queues_when_resume_already_running(self):
        """A second AGY follow-up should queue instead of spawning parallel resumes."""
        server = self.server
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "antigravity",
            "resumed_sid": sid,
            "pid": 4245,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_antigravity(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "antigravity-resume-queued")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_resume_hermes_builds_resume_command(self):
        server = self.server
        sid = "20260601_121000_child"
        proc = mock.Mock(pid=4251)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        spawned_entry = None
        try:
            with mock.patch.object(
                server,
                "_resolve_hermes_bin",
                return_value={"available": True, "bin": "/usr/bin/hermes-test"},
            ), mock.patch.object(
                server,
                "_hermes_session_row",
                return_value={"cwd": str(self.repo), "model": "hermes-test-model"},
            ), mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry") as record:
                result = server.resume_session_hermes(sid, "second")
                spawned_entry = dict(server._spawned_sessions[0])
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "hermes-resume")
        self.assertEqual(result["engine"], "hermes")
        self.assertEqual(result["model"], "hermes-test-model")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[:2], ["/usr/bin/hermes-test", "chat"])
        self.assertEqual(cmd[cmd.index("--resume") + 1], sid)
        self.assertEqual(cmd[cmd.index("--query") + 1], "second")
        self.assertIn("--quiet", cmd)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.repo))
        self.assertEqual(spawned_entry["engine"], "hermes")
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["engine"], "hermes")
        self.assertEqual(record.call_args.kwargs["session_id"], sid)

    def test_resume_hermes_queues_when_resume_already_running(self):
        server = self.server
        sid = "20260601_121000_child"
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "hermes",
            "resumed_sid": sid,
            "pid": 4252,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_hermes_bin",
                return_value={"available": True, "bin": "/usr/bin/hermes-test"},
            ), mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_hermes(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "hermes-resume-queued")
        self.assertEqual(result["engine"], "hermes")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_ask_engine_hermes_waits_for_plain_text_log(self):
        server = self.server
        sid = "20260601_121000_child"
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        proc = mock.Mock(pid=4253)
        proc.poll.return_value = 0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                log_path = pathlib.Path(tmp) / "resume-hermes.log"
                log_path.write_text("yes + 3\n", encoding="utf-8")

                def fake_resume(session_id, text):
                    server._spawned_sessions.append({
                        "engine": "hermes",
                        "resumed_sid": session_id,
                        "pid": proc.pid,
                        "proc": proc,
                        "log": str(log_path),
                    })
                    return {"ok": True, "via": "hermes-resume", "engine": "hermes"}

                with mock.patch.object(server, "resume_session_hermes", side_effect=fake_resume), \
                     mock.patch.object(server, "_remove_spawn_from_registry"):
                    result = server.ask_engine_session_and_wait(sid, "probe", 1000, "hermes")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "hermes-resume")
        self.assertEqual(result["text"], "yes + 3")

    def test_ask_session_routes_hermes_to_engine_resume(self):
        server = self.server
        sid = "20260601_121000_child"
        with mock.patch.object(server, "_detect_session_engine", return_value="hermes"), \
             mock.patch.object(server, "ask_engine_session_and_wait", return_value={"ok": True}) as ask_engine, \
             mock.patch.object(server, "resume_session_headless") as headless:
            result = server.ask_session_and_wait(sid, "probe", timeout_ms=1000)

        self.assertTrue(result["ok"])
        ask_engine.assert_called_once_with(sid, "probe", 1000, "hermes")
        headless.assert_not_called()

    def test_open_target_allows_executable_session_cwd_files(self):
        """Post-sandbox-removal: scripts in the session cwd resolve cleanly."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            script = session_cwd / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "run.sh",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_markdown_path_links_request_external_open(self):
        """The transcript click handler asks /api/open to launch markdown."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _isMarkdownPath", js)
        self.assertIn("function normalizeMarkdownLinkTarget", js)
        self.assertIn("payload.launch = true", js)

    def test_absolute_folder_path_links_are_not_web_routes(self):
        """Extensionless /Users/... folders should still go through /api/open."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _isAbsoluteFilesystemPath", js)
        self.assertIn(
            "Users|Volumes|Applications|Library|System|private|tmp|var|etc|opt|usr|bin|sbin|home",
            js,
        )
        self.assertIn("if (_isAbsoluteFilesystemPath(p)) return false;", js)

    def test_inline_code_skips_placeholder_and_api_path_links(self):
        """Auto-linking should avoid shortened paths and internal API mentions."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _shouldLinkifyInlineCodePath", js)
        self.assertIn("function _isPlaceholderPathToken", js)
        self.assertIn("function _isInternalApiPathToken", js)
        self.assertIn("if (_shouldLinkifyInlineCodePath(inner))", js)

    def test_archive_progress_does_not_replace_search_empty_state(self):
        """Background archive refresh must not clobber no-match search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn(".archive-loading-placeholder, .archive-loading-stages", js)
        self.assertIn("archive-empty-state archive-loading-placeholder", js)
        self.assertIn("No conversations match your filter.", js)
        self.assertNotIn(".archive-empty-state, .archive-loading-stages", js)

    def test_original_ask_renders_pasted_images_inline(self):
        """Pasted-image references should become images in the ask panels."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("PASTED_IMG_MD_LINK_RE", js)
        self.assertIn("function pastedImageTag", js)
        self.assertIn("/api/pasted-image?path=", js)
        self.assertIn("const imagesHtml = renderImageDescriptors(ev.images);", js)
        self.assertIn("h += imagesHtml;", js)

    def test_markdown_attached_above_reference_is_not_rendered_as_a_url(self):
        """UI-only image references must not leave a broken image icon."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function isUnavailableMarkdownImageTarget", js)
        self.assertIn("Image attachment unavailable in this transcript", js)
        self.assertIn("if (isUnavailableMarkdownImageTarget(target))", js)

    def test_codex_inline_visual_reference_renders_as_a_file_link(self):
        """Codex visual markers should be useful links, not raw transcript syntax."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("CODEX_INLINE_VIS_RE", js)
        self.assertIn("Visual: ", js)
        self.assertIn("linkifyPath(filename)", js)

    def test_archive_search_refresh_preserves_scroll(self):
        """Periodic archive refreshes should not snap active search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("let _lastArchiveRenderFilter = null;", js)
        self.assertIn("function _captureArchiveListScroll", js)
        self.assertIn("function _restoreArchiveListScroll", js)
        self.assertIn("_lastArchiveRenderFilter = q;", js)

    def test_files_endpoint_route_registered(self):
        """Smoke check: GET /api/conversations/<id>/files dispatcher
        branch must be present in the do_GET source. Route registration
        in this codebase is by literal regex string, so a substring grep
        is the cheapest assertion."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/conversations/[^/]+/files", src)

    def test_hermes_history_reads_sqlite_lineage(self):
        """Hermes history should read native state.db rows and surface every
        session — parent and child — as its own row, while recording the
        parent/child lineage links so chains can be re-collapsed later."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        parent = "20260601_120000_parent"
        child = "20260601_121000_child"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / "state.db"
            gateway = root / "sessions" / "sessions.json"
            gateway.parent.mkdir(parents=True)
            gateway.write_text(json.dumps({
                "agent:cli:test": {
                    "session_id": child,
                    "platform": "cli",
                    "origin": "cli",
                    "chat_type": "dm",
                    "display_name": "Hermes gateway title",
                    "updated_at": "2026-06-01T12:10:00Z",
                },
            }))
            con = sqlite3.connect(db)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY,
                        source TEXT,
                        user_id TEXT,
                        model TEXT,
                        title TEXT,
                        started_at REAL,
                        ended_at REAL,
                        parent_session_id TEXT,
                        message_count INTEGER,
                        tool_call_count INTEGER,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cache_read_tokens INTEGER,
                        cache_write_tokens INTEGER,
                        reasoning_tokens INTEGER,
                        cwd TEXT,
                        archived INTEGER
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT,
                        role TEXT,
                        content TEXT,
                        tool_call_id TEXT,
                        tool_calls TEXT,
                        tool_name TEXT,
                        timestamp REAL,
                        token_count INTEGER,
                        finish_reason TEXT,
                        reasoning TEXT,
                        active INTEGER
                    );
                    CREATE VIRTUAL TABLE messages_fts USING fts5(content);
                """)
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (parent, "cli", "test-user", "hermes-test-model", "Investigate deployment",
                     1780315200.0, 1780315500.0, "", 2, 0, 100, 20, 5, 0, 0, str(root), 0),
                )
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (child, "whatsapp", "test-user", "hermes-test-model", "Investigate deployment #2",
                     1780315800.0, 1780316100.0, parent, 3, 1, 120, 40, 10, 2, 0, str(root), 0),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, reasoning, active) VALUES (?,?,?,?,?,1)",
                    (parent, "user", "Original Hermes request", 1780315210.0, "",),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, reasoning, active) VALUES (?,?,?,?,?,1)",
                    (parent, "assistant", "Parent answer before compression", 1780315220.0, "hidden chain of thought",),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, reasoning, active) VALUES (?,?,?,?,?,1)",
                    (child, "user", json.dumps({"text": "Please run status"}), 1780315810.0, "",),
                )
                tool_calls = json.dumps([{
                    "id": "call_1",
                    "function": {
                        "name": "Bash",
                        "arguments": json.dumps({"command": "git status --short"}),
                    },
                }])
                con.execute(
                    "INSERT INTO messages (session_id, role, content, tool_calls, timestamp, reasoning, active) VALUES (?,?,?,?,?,?,1)",
                    (child, "assistant", "I'll inspect.", tool_calls, 1780315820.0, "more hidden reasoning"),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, timestamp, active) VALUES (?,?,?,?,?,?,1)",
                    (child, "tool", " M server.py", "call_1", "Bash", 1780315830.0),
                )
                con.commit()
            finally:
                con.close()

            orig_db = server.HERMES_STATE_DB
            orig_gateway = server.HERMES_GATEWAY_SESSIONS
            server.HERMES_STATE_DB = db
            server.HERMES_GATEWAY_SESSIONS = gateway
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._HERMES_GATEWAY_CACHE["key"] = None
            server._HERMES_GATEWAY_CACHE["by_session"] = {}
            server._ENGINE_DETECT_CACHE.clear()
            try:
                rows = server.find_hermes_conversations(repo_only=False)
                # Both parent and child surface as their own rows (no folding);
                # newest-first, so the child (later messages) sorts ahead.
                self.assertEqual(
                    sorted(r["session_id"] for r in rows), sorted([child, parent])
                )
                by_id = {r["session_id"]: r for r in rows}
                row = by_id[child]
                self.assertEqual(row["source"], "hermes")
                self.assertEqual(row["engine"], "hermes")
                self.assertEqual(row["source_platform"], "whatsapp")
                self.assertEqual(row["model"], "hermes-test-model")
                self.assertIn("hermes_tool_calls", row)
                self.assertEqual(row["parent_session_id"], parent)
                self.assertEqual(row["hermes_lineage_session_ids"], [parent, child])
                self.assertEqual(row["hermes_lineage_count"], 2)
                self.assertIn("Please run status", row["first_message"])
                # Child knows nothing below it; parent lists the child and is
                # flagged as a parent so the UI can badge / re-collapse later.
                self.assertEqual(row["hermes_child_session_ids"], [])
                self.assertFalse(row["hermes_is_parent"])
                self.assertEqual(by_id[parent]["hermes_child_session_ids"], [child])
                self.assertTrue(by_id[parent]["hermes_is_parent"])

                parsed = server.parse_conversation(child, use_cache=False)
                events = parsed["events"]
                self.assertEqual(parsed["last_line"], 8)
                # Line 1 is the at-a-glance turn-summary banner; the lineage
                # header follows it.
                self.assertEqual(events[0]["subtype"], "hermes_turn_summary")
                lineage = [e for e in events if e.get("subtype") == "hermes_lineage"]
                self.assertEqual(len(lineage), 1)
                self.assertEqual(lineage[0]["lineage_session_ids"], [parent, child])
                self.assertTrue(any(e.get("type") == "system" and e.get("subtype") == "hermes_segment" for e in events))
                self.assertTrue(any(e.get("type") == "user_text" and e.get("text") == "Original Hermes request" for e in events))
                self.assertTrue(any(e.get("type") == "user_text" and e.get("text") == "Please run status" for e in events))
                tool_blocks = [
                    b
                    for e in events if e.get("type") == "assistant"
                    for b in e.get("blocks", [])
                    if b.get("kind") == "tool_use"
                ]
                self.assertEqual(tool_blocks[0]["name"], "Bash")
                self.assertIn("git status", tool_blocks[0]["detail"])
                self.assertTrue(any(e.get("type") == "tool_result" and "server.py" in e.get("text", "") for e in events))
                self.assertNotIn("hidden chain", json.dumps(events))
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_GATEWAY_SESSIONS = orig_gateway
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._HERMES_GATEWAY_CACHE["key"] = None
                server._HERMES_GATEWAY_CACHE["by_session"] = {}
                server._ENGINE_DETECT_CACHE.clear()

    def test_hermes_reads_profile_worker_dbs(self):
        """Profile workers (e.g. the chuckrealtor 'Becky' agent) keep their own
        state.db under ~/.hermes/profiles/<name>/. CCC must ingest those too —
        they're the sessions that actually write code — tag each row with its
        profile, and route transcript reads to the owning DB."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        def _make_db(path, sid, source, title, msg):
            con = sqlite3.connect(path)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT,
                        started_at REAL, ended_at REAL, parent_session_id TEXT,
                        tool_call_count INTEGER, cwd TEXT, archived INTEGER
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                        role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,
                        tool_call_id TEXT, timestamp REAL, reasoning TEXT, active INTEGER
                    );
                """)
                con.execute(
                    "INSERT INTO sessions (id,source,model,title,started_at,ended_at,"
                    "parent_session_id,tool_call_count,cwd,archived) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, source, "gpt-5.5", title, 1780315800.0, 1780316100.0, "", 3, "/tmp", 0),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, active) "
                    "VALUES (?,?,?,?,1)",
                    (sid, "user", msg, 1780315810.0),
                )
                con.commit()
            finally:
                con.close()

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            main_db = root / "state.db"
            profiles = root / "profiles"
            worker_db = profiles / "chuckrealtor" / "state.db"
            worker_db.parent.mkdir(parents=True)
            gateway = root / "sessions" / "sessions.json"
            gateway.parent.mkdir(parents=True)
            gateway.write_text("{}")
            _make_db(str(main_db), "20260601_120000_gateway", "whatsapp",
                     "Gateway chat", "hi from the gateway")
            _make_db(str(worker_db), "20260601_130000_worker", "cli",
                     "Becky work", "change the price to 785000")

            orig_db = server.HERMES_STATE_DB
            orig_profiles = getattr(server, "HERMES_PROFILES_DIR", None)
            orig_gateway = server.HERMES_GATEWAY_SESSIONS
            server.HERMES_STATE_DB = main_db
            server.HERMES_PROFILES_DIR = profiles
            server.HERMES_GATEWAY_SESSIONS = gateway
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._HERMES_DB_INDEX["key"] = None
            server._HERMES_DB_INDEX["by_session"] = {}
            server._HERMES_GATEWAY_CACHE["key"] = None
            server._HERMES_GATEWAY_CACHE["by_session"] = {}
            server._ENGINE_DETECT_CACHE.clear()
            try:
                rows = server.find_hermes_conversations(repo_only=False)
                by_id = {r["session_id"]: r for r in rows}
                # Both the gateway session and the profile worker surface.
                self.assertIn("20260601_120000_gateway", by_id)
                self.assertIn("20260601_130000_worker", by_id)
                self.assertEqual(by_id["20260601_120000_gateway"]["hermes_profile"], "")
                self.assertEqual(by_id["20260601_130000_worker"]["hermes_profile"], "chuckrealtor")
                # The worker session id is owned by the profile DB and its
                # transcript reads route there.
                self.assertEqual(
                    server._hermes_db_for_session("20260601_130000_worker"), worker_db
                )
                self.assertTrue(server._is_hermes_session("20260601_130000_worker"))
                ev = server._parse_hermes_conversation("20260601_130000_worker")
                self.assertTrue(any("785000" in e.get("text", "") for e in ev["events"]))
            finally:
                server.HERMES_STATE_DB = orig_db
                if orig_profiles is not None:
                    server.HERMES_PROFILES_DIR = orig_profiles
                server.HERMES_GATEWAY_SESSIONS = orig_gateway
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._HERMES_DB_INDEX["key"] = None
                server._HERMES_DB_INDEX["by_session"] = {}
                server._HERMES_GATEWAY_CACHE["key"] = None
                server._HERMES_GATEWAY_CACHE["by_session"] = {}
                server._ENGINE_DETECT_CACHE.clear()

    def test_hermes_kanban_worker_rows_use_task_title(self):
        """Kanban-launched Hermes workers start with a generic
        "work kanban task t_..." user prompt. The actual useful title lives in
        the kanban_show tool result, so CCC should surface that title."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / "profiles" / "chuckrealtor" / "state.db"
            db.parent.mkdir(parents=True)
            con = sqlite3.connect(db)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT,
                        started_at REAL, ended_at REAL, parent_session_id TEXT,
                        tool_call_count INTEGER, cwd TEXT, archived INTEGER
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                        role TEXT, content TEXT, tool_calls TEXT, tool_name TEXT,
                        tool_call_id TEXT, timestamp REAL, reasoning TEXT, active INTEGER
                    );
                """)
                sid = "20260705_173708_48d610"
                con.execute(
                    "INSERT INTO sessions (id,source,model,title,started_at,ended_at,"
                    "parent_session_id,tool_call_count,cwd,archived) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, "cli", "gpt-5.5", "", 1783273030.0, 0.0, "", 22, "/tmp/chuck", 0),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?,?,?,?,1)",
                    (sid, "user", "work kanban task t_5ca440b4", 1783273031.0),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?,?,?,?,1)",
                    (sid, "tool", json.dumps({
                        "task": {
                            "id": "t_5ca440b4",
                            "title": "Ask #19: also skip tracing these expired listings",
                        }
                    }), 1783273032.0),
                )
                con.commit()
            finally:
                con.close()

            orig_db = server.HERMES_STATE_DB
            orig_profiles = server.HERMES_PROFILES_DIR
            orig_gateway = server.HERMES_GATEWAY_SESSIONS
            server.HERMES_STATE_DB = root / "missing-state.db"
            server.HERMES_PROFILES_DIR = root / "profiles"
            server.HERMES_GATEWAY_SESSIONS = root / "sessions" / "sessions.json"
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._HERMES_DB_INDEX["key"] = None
            server._HERMES_DB_INDEX["by_session"] = {}
            server._HERMES_GATEWAY_CACHE["key"] = None
            server._HERMES_GATEWAY_CACHE["by_session"] = {}
            try:
                rows = server.find_hermes_conversations(repo_only=False)
                row = next(r for r in rows if r["session_id"] == sid)
                self.assertIn("Ask #19", row["display_name"])
                self.assertEqual(row["hermes_kanban_task_id"], "t_5ca440b4")
                self.assertEqual(
                    row["hermes_kanban_task_title"],
                    "Ask #19: also skip tracing these expired listings",
                )
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_PROFILES_DIR = orig_profiles
                server.HERMES_GATEWAY_SESSIONS = orig_gateway
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._HERMES_DB_INDEX["key"] = None
                server._HERMES_DB_INDEX["by_session"] = {}
                server._HERMES_GATEWAY_CACHE["key"] = None
                server._HERMES_GATEWAY_CACHE["by_session"] = {}

    def test_hermes_kanban_tool_arguments_are_not_truncated(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        body = (
            "Blocker details for Ask #19: FlexMLS returned a Client Challenge "
            "CAPTCHA instead of listing HTML/data, so the worker needs a CSV, "
            "export, screenshot text, or pasted address list before it can run "
            "the skip trace. This sentence makes the payload long enough to "
            "hit the generic prompt-fragment truncation path that used to hide "
            "the important blocker details in the transcript."
        )
        block = server._hermes_tool_block({
            "id": "call_1",
            "name": "kanban_comment",
            "args": {
                "board": "example-board",
                "task_id": "t_5ca440b4",
                "body": body,
            },
        })

        self.assertEqual(block["command_kind"], "Kanban arguments")
        self.assertEqual(block["detail"], block["command"])
        self.assertIn('"body":', block["detail"])
        self.assertIn(body, block["detail"])
        self.assertNotIn("...", block["detail"])

    def test_hermes_rows_are_not_repo_scoped(self):
        """Hermes is a non-repo-scoped source: a session whose cwd is outside
        the requested repo (or empty) must still surface under repo_only=True,
        so CLI/whatsapp/cron rows appear in every repo's sidebar rather than
        only when their home-dir cwd is selected."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            # A real, unrelated repo dir to scope to. The sessions below live
            # at `root` (its parent) or have no cwd — neither is inside it.
            other_repo = root / "unrelated_repo"
            other_repo.mkdir()
            (other_repo / ".git").mkdir()  # looks-like-repo → resolve_repo_path accepts it
            db = root / "state.db"
            gateway = root / "sessions" / "sessions.json"
            gateway.parent.mkdir(parents=True)
            gateway.write_text("{}")
            con = sqlite3.connect(db)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY, source TEXT, user_id TEXT,
                        model TEXT, title TEXT, started_at REAL, ended_at REAL,
                        parent_session_id TEXT, message_count INTEGER,
                        tool_call_count INTEGER, input_tokens INTEGER,
                        output_tokens INTEGER, cache_read_tokens INTEGER,
                        cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                        cwd TEXT, archived INTEGER
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                        role TEXT, content TEXT, tool_call_id TEXT,
                        tool_calls TEXT, tool_name TEXT, timestamp REAL,
                        token_count INTEGER, finish_reason TEXT,
                        reasoning TEXT, active INTEGER
                    );
                    CREATE VIRTUAL TABLE messages_fts USING fts5(content);
                """)
                cli_sid = "20260601_120000_cli"
                wa_sid = "20260601_121000_wa"
                # CLI session rooted at the home-dir-like parent (outside repo).
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cli_sid, "cli", "u", "m", "CLI chat", 1780315200.0, 0.0,
                     "", 1, 0, 0, 0, 0, 0, 0, str(root), 0),
                )
                # WhatsApp session with no cwd at all.
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wa_sid, "whatsapp", "u", "m", "WA chat", 1780315800.0, 0.0,
                     "", 1, 0, 0, 0, 0, 0, 0, "", 0),
                )
                for sid in (cli_sid, wa_sid):
                    con.execute(
                        "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?,?,?,?,1)",
                        (sid, "user", "hi", 1780315210.0),
                    )
                con.commit()
            finally:
                con.close()

            orig_db = server.HERMES_STATE_DB
            orig_gateway = server.HERMES_GATEWAY_SESSIONS
            server.HERMES_STATE_DB = db
            server.HERMES_GATEWAY_SESSIONS = gateway
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._HERMES_GATEWAY_CACHE["key"] = None
            server._HERMES_GATEWAY_CACHE["by_session"] = {}
            try:
                rows = server.find_hermes_conversations(
                    repo_path=str(other_repo), repo_only=True,
                    resolve_pr_states=False, resolve_worktree_dirty=False,
                )
                sids = {r["session_id"] for r in rows}
                self.assertIn(cli_sid, sids, "CLI row with out-of-repo cwd must still show")
                self.assertIn(wa_sid, sids, "empty-cwd whatsapp row must still show")
                platforms = {r["source_platform"] for r in rows}
                self.assertEqual(platforms, {"cli", "whatsapp"})
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_GATEWAY_SESSIONS = orig_gateway
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._HERMES_GATEWAY_CACHE["key"] = None
                server._HERMES_GATEWAY_CACHE["by_session"] = {}

    def test_hermes_history_accepts_non_timestamp_session_ids(self):
        """Gateway-created Hermes sessions may use non-timestamp ids."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "cron_789eec75aeaa_20260616_100045"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / "state.db"
            con = sqlite3.connect(db)
            try:
                con.executescript("""
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY,
                        source TEXT,
                        model TEXT,
                        title TEXT,
                        started_at REAL,
                        cwd TEXT
                    );
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT,
                        role TEXT,
                        content TEXT,
                        timestamp REAL,
                        active INTEGER
                    );
                """)
                con.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                    (sid, "cron", "hermes-test-model", "Scheduled check", 1780315200.0, str(root)),
                )
                con.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?,?,?,?,1)",
                    (sid, "user", "Run the scheduled check", 1780315210.0),
                )
                con.commit()
            finally:
                con.close()

            orig_db = server.HERMES_STATE_DB
            server.HERMES_STATE_DB = db
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._ENGINE_DETECT_CACHE.clear()
            try:
                self.assertTrue(server._is_hermes_session(sid))
                rows = server.find_hermes_conversations(repo_only=False)
                self.assertEqual([r["session_id"] for r in rows], [sid])
                parsed = server.parse_conversation(sid, use_cache=False)
                self.assertTrue(any(
                    e.get("type") == "user_text" and e.get("text") == "Run the scheduled check"
                    for e in parsed["events"]
                ))
            finally:
                server.HERMES_STATE_DB = orig_db
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._ENGINE_DETECT_CACHE.clear()

    def test_hermes_history_missing_db_is_empty(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            orig_db = server.HERMES_STATE_DB
            orig_bridge = server.HERMES_WHATSAPP_BRIDGE_LOG
            orig_pending = server.HERMES_CHUCK_PENDING_DIR
            server.HERMES_STATE_DB = pathlib.Path(tmp) / "missing-state.db"
            server.HERMES_WHATSAPP_BRIDGE_LOG = pathlib.Path(tmp) / "missing-bridge.log"
            server.HERMES_CHUCK_PENDING_DIR = pathlib.Path(tmp) / "missing-pending"
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            try:
                self.assertEqual(server.find_hermes_conversations(repo_only=False), [])
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_WHATSAPP_BRIDGE_LOG = orig_bridge
                server.HERMES_CHUCK_PENDING_DIR = orig_pending
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()

    def test_hermes_reads_whatsapp_bridge_and_pending_sources(self):
        """WhatsApp bridge.log and active pending-ask JSON files are Hermes
        session sources too; they can precede or outlive state.db rows."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bridge = root / "bridge.log"
            pending_dir = root / "chuck_realtor_pending"
            pending_dir.mkdir()
            chat_id = "group-test@g.us"
            bridge.write_text(
                json.dumps({
                    "event": "upsert",
                    "type": "notify",
                    "fromMe": False,
                    "chatId": chat_id,
                    "senderId": "user-test@lid",
                    "body": "Can you run these five sample leads?",
                }) + "\n" +
                json.dumps({
                    "event": "upsert",
                    "type": "append",
                    "fromMe": True,
                    "chatId": "other-chat@g.us",
                    "senderId": "other-chat@g.us",
                    "body": "Separate bridge-only chat",
                }) + "\n",
                encoding="utf-8",
            )
            pending_path = pending_dir / f"{chat_id}.json"
            pending_path.write_text(json.dumps({
                "created_at": 1780315200.0,
                "reason": "nontrivial_or_ambiguous",
                "change_id": "Ask #22",
                "request_text": "Can you run these five sample leads?",
                "group_chat_id": chat_id,
                "group_chat_name": "Example Group",
                "sender_id": "user-test@lid",
                "sender_name": "Example User",
                "notes": ["Needs approval."],
                "private_last_planning_response": "This should pause for approval.",
            }), encoding="utf-8")

            orig_db = server.HERMES_STATE_DB
            orig_bridge = server.HERMES_WHATSAPP_BRIDGE_LOG
            orig_pending = server.HERMES_CHUCK_PENDING_DIR
            server.HERMES_STATE_DB = root / "missing-state.db"
            server.HERMES_WHATSAPP_BRIDGE_LOG = bridge
            server.HERMES_CHUCK_PENDING_DIR = pending_dir
            server._HERMES_ID_CACHE["key"] = None
            server._HERMES_ID_CACHE["ids"] = set()
            server._ENGINE_DETECT_CACHE.clear()
            try:
                rows = server.find_hermes_conversations(repo_only=False)
                by_id = {r["session_id"]: r for r in rows}
                pending_sid = "hermes-whatsapp-pending:" + chat_id
                bridge_sid = "hermes-whatsapp-bridge:other-chat@g.us"
                self.assertIn(pending_sid, by_id)
                self.assertIn(bridge_sid, by_id)
                self.assertTrue(by_id[pending_sid]["needs_approval"])
                self.assertEqual(by_id[pending_sid]["pending_tool"], "Ask approval")
                self.assertEqual(server._detect_session_engine(pending_sid), "hermes")

                pending_events = server.parse_conversation(pending_sid, use_cache=False)["events"]
                self.assertTrue(any("Ask #22" in e.get("text", "") for e in pending_events))
                self.assertTrue(any("sample leads" in e.get("text", "") for e in pending_events))
                for ev in pending_events:
                    if ev.get("type") == "assistant":
                        self.assertIsInstance(ev.get("blocks"), list)
                        self.assertTrue(ev["blocks"])

                bridge_events = server.parse_conversation(bridge_sid, use_cache=False)["events"]
                self.assertTrue(any("Separate bridge-only chat" in e.get("text", "") for e in bridge_events))
                for ev in bridge_events:
                    if ev.get("type") == "assistant":
                        self.assertIsInstance(ev.get("blocks"), list)
                        self.assertTrue(ev["blocks"])
            finally:
                server.HERMES_STATE_DB = orig_db
                server.HERMES_WHATSAPP_BRIDGE_LOG = orig_bridge
                server.HERMES_CHUCK_PENDING_DIR = orig_pending
                server._HERMES_ID_CACHE["key"] = None
                server._HERMES_ID_CACHE["ids"] = set()
                server._ENGINE_DETECT_CACHE.clear()

    def test_session_initial_scan_keeps_recent_and_live_rows(self):
        """Initial /api/sessions scans should avoid cold history while keeping
        live sessions even when their transcript mtime is old."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            files = []
            for name, age in (
                ("old-cold", 20_000),
                ("old-live", 20_000),
                ("recent-newest", 10),
                ("recent-older", 20),
            ):
                p = root / f"{name}.jsonl"
                p.write_text("{}\n")
                os.utime(p, (now - age, now - age))
                files.append(p)

            selected, meta = server._filter_conversation_jsonls(
                files,
                include_old=False,
                always_include_sids={"old-live"},
                cutoff_ts=now - 1000,
                max_files=2,
            )

        stems = [p.stem for p in selected]
        self.assertEqual(len(stems), 2)
        self.assertIn("old-live", stems)
        self.assertIn("recent-newest", stems)
        self.assertNotIn("old-cold", stems)
        self.assertTrue(meta["limited"])

    def test_session_usage_falls_back_to_diagnostic_context_sample(self):
        """Newer Claude transcripts can omit `message.usage` while still
        carrying a diagnostic context-size hint. The footer should use that
        instead of showing no context data at all."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        event = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "diagnostics": {
                    "cache_miss_reason": {
                        "type": "tools_changed",
                        "cache_missed_input_tokens": 57261,
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(event) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 57261)
        self.assertEqual(usage["peak_input_tokens"], 57261)
        self.assertEqual(usage["model"], "claude-opus-4-7")

    def test_tail_meta_extracts_assistant_model(self):
        """Conversation rows should carry the model even before the usage
        endpoint finishes, so the footer has a stable fallback."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        event = {
            "type": "assistant",
            "timestamp": "2026-05-03T12:00:00.000Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "done"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = server._extract_tail_meta(path)

        self.assertEqual(meta["model"], "claude-sonnet-4-6")


    def test_coordinate_sessions_helper_exists(self):
        """_coordinate_sessions must exist and reject empty topic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_coordinate_sessions"))
        result = server._coordinate_sessions({"session_ids": ["abc"], "topic": ""})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_group_chat_read_helper_exists_and_rejects_traversal(self):
        """_group_chat_read must exist and block path traversal outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_read"))
        result, forbidden = server._group_chat_read("/etc/passwd")
        self.assertIsNone(result)
        self.assertEqual(forbidden, "forbidden")

    def test_group_chat_post_helper_exists_and_rejects_traversal(self):
        """_group_chat_post must exist and block writes outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_post"))
        result = server._group_chat_post("/etc/passwd", "hacked")
        self.assertFalse(result["ok"])
        self.assertIn("forbidden", result.get("error", ""))

    def test_group_chat_reader_restores_composer_for_new_session(self):
        """New session must clear group-chat reader chrome before showing composer."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function stopGroupChatReader", js)
        self.assertIn("stopGroupChatReader({ rerenderSidebar: true });", js)
        self.assertIn("function enterNewSessionMode()", js)
        self.assertIn("currentConversation = '__new__';", js)

    def test_group_chat_reader_has_tts_and_conversation_typography(self):
        """Group-chat reader should expose TTS and reuse assistant markdown styling."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("function renderGroupChatMarkdown", js)
        self.assertIn("gc-message-body assistant-text", js)
        self.assertIn('id="gcTtsBtn"', js)
        self.assertIn(".conv-input-bar .tts-btn, .gc-reader .tts-btn", js)
        self.assertIn(".conversations-view .gc-message-body.assistant-text", css)
        self.assertIn(".gc-reader-input-row .tts-btn", css)

    # ── Codex desktop↔CCC single-writer coordination ────────────────────────
    def test_parse_lsof_open_rollouts_maps_pids_and_filters(self):
        """`lsof -Fpn` field output → {rollout path: pid}. Only paths under
        `/sessions/` ending `.jsonl` survive; pids map to the paths opened
        under them; garbage/pid-less lines are ignored."""
        server = self.server
        output = "\n".join([
            "n/orphan/sessions/early.jsonl",           # no pid yet → dropped
            "p100",
            "n/home/.codex/sessions/thread-a.jsonl",   # kept → 100
            "n/home/.codex/config.toml",               # no /sessions/ → dropped
            "n/home/.codex/sessions/notes.txt",        # /sessions/ but not .jsonl → dropped
            "xgarbage-line",                            # unknown tag → ignored
            "p200",
            "n/var/sessions/thread-b.jsonl",           # kept → 200
            "p",                                        # empty pid → ValueError → pid None
            "n/late/sessions/thread-c.jsonl",          # pid None → dropped
        ])
        self.assertEqual(
            server._parse_lsof_open_rollouts(output),
            {
                "/home/.codex/sessions/thread-a.jsonl": 100,
                "/var/sessions/thread-b.jsonl": 200,
            },
        )
        self.assertEqual(server._parse_lsof_open_rollouts(""), {})

    def test_codex_thread_writer_snapshot_attribution(self):
        """Pure writer attribution via injection kwargs (no subprocess/RPC)."""
        server = self.server
        now = 1_800_000_000.0
        path = "/home/.codex/sessions/thread.jsonl"
        recent = {"path": path, "mtime_ns": int((now - 1) * 1e9)}
        old = {"path": path, "mtime_ns": int((now - 300) * 1e9)}

        # recent rollout + desktop-attached + quiet app-state → desktop writer
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent, app_state={},
            attached={path: 4242}, exec_child=False,
        )
        self.assertEqual(snap["writer"], "desktop")
        self.assertTrue(snap["external_active"])
        self.assertTrue(snap["desktop_attached"])

        # same, but nothing attached to a desktop app-server → owner unknown
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent, app_state={},
            attached={}, exec_child=False,
        )
        self.assertEqual(snap["writer"], "unknown")
        self.assertTrue(snap["external_active"])
        self.assertFalse(snap["desktop_attached"])

        # CCC owns an active turn → ccc writer, never external (even recent mtime)
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent,
            app_state={"active_turn_id": "t1", "active_writer": "ccc"},
            attached={path: 4242}, exec_child=False,
        )
        self.assertEqual(snap["writer"], "ccc")
        self.assertFalse(snap["external_active"])

        # An authoritative active turn observed after reconnect is busy, but
        # its owner is unknown unless CCC or desktop ownership is proven.
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent,
            app_state={"status": "active", "active_turn_id": "t2", "active_writer": "unknown"},
            attached={}, exec_child=False,
        )
        self.assertEqual(snap["writer"], "unknown")
        self.assertTrue(snap["external_active"])

        # a CCC-spawned `codex exec` child owns the thread → ccc writer
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent, app_state={},
            attached={}, exec_child=True,
        )
        self.assertEqual(snap["writer"], "ccc")
        self.assertFalse(snap["external_active"])

        # recent rollout but CCC's own events are fresh → quiet, not external
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=recent,
            app_state={"last_activity_at": now - 2},
            attached={path: 4242}, exec_child=False,
        )
        self.assertIsNone(snap["writer"])
        self.assertFalse(snap["external_active"])

        # stale rollout + desktop-attached → attached, but no active writer
        snap = server._codex_thread_writer_snapshot(
            "sid", now, rollout=old, app_state={},
            attached={path: 4242}, exec_child=False,
        )
        self.assertIsNone(snap["writer"])
        self.assertFalse(snap["external_active"])
        self.assertTrue(snap["desktop_attached"])

    def test_resume_or_steer_gate_blocks_external_desktop_writer(self):
        """A desktop turn in flight gates the CCC send to fallback:queue and
        never reaches the app-server RPC."""
        server = self.server
        sid = "test-sid-gate"
        with mock.patch.object(
            server, "_codex_thread_writer_snapshot",
            return_value={"writer": "desktop", "desktop_attached": True, "external_active": True},
        ), mock.patch.object(
            server, "_codex_app_server_request",
            side_effect=AssertionError("must not be called"),
        ), mock.patch.object(server, "_resume_ledger_append"), \
             mock.patch.object(server, "_codex_telemetry_append"), \
             mock.patch.object(server, "_codex_coordination_event"):
            result = server._codex_resume_or_steer_via_app_server(sid, "hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["fallback"], "queue")
        self.assertEqual(result["stage"], "writer-gate")
        self.assertEqual(result["writer"], "desktop")

    def test_resume_or_steer_serializes_with_per_thread_mutex(self):
        """Holding a thread's turn mutex forces a concurrent CCC send to
        fallback:queue (writer attributed to ccc)."""
        server = self.server
        sid = "test-sid-lock"
        lock = server._codex_thread_turn_lock(sid)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with mock.patch.object(
                server, "_codex_thread_writer_snapshot",
                return_value={"writer": None, "external_active": False, "desktop_attached": False},
            ), mock.patch.object(server, "_resume_ledger_append"), \
                 mock.patch.object(server, "_codex_telemetry_append"), \
                 mock.patch.object(server, "_codex_coordination_event"):
                result = server._codex_resume_or_steer_via_app_server(sid, "hello")
            self.assertFalse(result["ok"])
            self.assertEqual(result["fallback"], "queue")
            self.assertEqual(result["writer"], "ccc")
        finally:
            lock.release()

    def test_resume_queue_engine_busy_short_circuits_on_external_writer(self):
        """An external writer short-circuits busy=True without the app-server
        activity RPC (the cheap stat+lsof path wins first)."""
        server = self.server
        sid = "test-sid-busy"
        with mock.patch.object(server, "_is_codex_session", return_value=True), \
             mock.patch.object(
                 server, "_codex_thread_writer_snapshot",
                 return_value={"writer": "desktop", "external_active": True, "desktop_attached": True},
             ), mock.patch.object(server, "_codex_note_external_writer_transition"), \
             mock.patch.object(
                 server, "_codex_app_server_thread_is_active",
                 side_effect=AssertionError("must not be reached"),
             ):
            self.assertTrue(server._resume_queue_engine_busy(sid))

    def test_coordination_events_are_durable_and_stable(self):
        """A coordination event becomes a synthetic system/codex_coordination
        conversation event with a STABLE line id (idempotent re-polls)."""
        server = self.server
        sid = "test-sid-coord"
        prev_loaded = server._codex_coord_state_loaded
        server._codex_coord_state_loaded = True  # skip disk load
        try:
            with mock.patch.object(server, "_save_codex_app_server_state_unlocked"):
                server._codex_coordination_event(sid, "external_turn_started", writer="desktop")
            events = server._get_codex_coordination_events_for_session(sid)
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev["type"], "system")
            self.assertEqual(ev["subtype"], "codex_coordination")
            self.assertTrue(ev["line"].startswith("coord-"))
            self.assertTrue(ev["text"])
            # re-poll must be idempotent: identical synthetic line ids
            again = server._get_codex_coordination_events_for_session(sid)
            self.assertEqual([e["line"] for e in events], [e["line"] for e in again])
        finally:
            server._codex_coord_state_loaded = prev_loaded
            with server._CODEX_APP_SERVER_LOCK:
                server._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)

    def test_coordination_events_do_not_claim_an_unproven_external_writer(self):
        """Reconnect-era events describe the active turn without inventing a
        second process or person."""
        server = self.server
        sid = "test-sid-unknown-writer-copy"
        prev_loaded = server._codex_coord_state_loaded
        server._codex_coord_state_loaded = True
        try:
            with server._CODEX_APP_SERVER_LOCK:
                server._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                    "coordination_events": [
                        {"ts": 1.0, "kind": "external_turn_started", "writer": "unknown"},
                        {"ts": 2.0, "kind": "input_queued", "writer": "unknown"},
                    ],
                }
            events = server._get_codex_coordination_events_for_session(sid)
            self.assertEqual(
                [event["text"] for event in events],
                ["Active Codex turn detected", "Message queued behind the active turn"],
            )
        finally:
            server._codex_coord_state_loaded = prev_loaded
            with server._CODEX_APP_SERVER_LOCK:
                server._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)

    def test_app_server_state_payload_includes_coordination_events(self):
        """The persisted app-server payload surfaces a thread's durable
        coordination_events."""
        server = self.server
        sid = "test-sid-payload"
        with server._CODEX_APP_SERVER_LOCK:
            server._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                "coordination_events": [{"ts": 1.0, "kind": "input_queued"}],
            }
        try:
            payload = server._codex_app_server_state_payload_unlocked()
        finally:
            with server._CODEX_APP_SERVER_LOCK:
                server._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)
        thread = payload["threads"][sid]
        self.assertIn("coordination_events", thread)
        self.assertEqual(
            thread["coordination_events"],
            [{"ts": 1.0, "kind": "input_queued"}],
        )

    def test_codex_state_fields_external_desktop_overlay(self):
        """With no CCC turn but a desktop writer active, the state chip reads
        working/fresh and names the desktop writer."""
        server = self.server
        sid = "test-sid-state"
        with mock.patch.object(server, "_codex_app_server_thread_state", return_value={}), \
             mock.patch.object(
                 server, "_codex_thread_writer_snapshot",
                 return_value={"writer": "desktop", "desktop_attached": True, "external_active": True},
             ), mock.patch.object(server, "_codex_note_external_writer_transition"):
            fields = server._codex_state_fields(sid)
        self.assertEqual(fields["codex_state"], "working")
        self.assertTrue(fields["codex_fresh"])
        self.assertEqual(fields["codex_writer"], "desktop")
        self.assertTrue(fields["codex_desktop_attached"])
        self.assertIn("desktop", fields["codex_state_reason"])

    def test_codex_state_fields_unknown_writer_uses_neutral_reason(self):
        """An active turn with unproven ownership must not be presented as a
        second Codex process."""
        server = self.server
        sid = "test-sid-unknown-state"
        with mock.patch.object(server, "_codex_app_server_thread_state", return_value={}), \
             mock.patch.object(
                 server, "_codex_thread_writer_snapshot",
                 return_value={"writer": "unknown", "desktop_attached": False, "external_active": True},
             ), mock.patch.object(server, "_codex_note_external_writer_transition"):
            fields = server._codex_state_fields(sid)
        self.assertEqual(fields["codex_state"], "working")
        self.assertEqual(fields["codex_writer"], "unknown")
        self.assertEqual(fields["codex_state_reason"], "An active Codex turn is writing this thread")

    def test_codex_unknown_writer_is_not_offered_steer(self):
        """Unknown ownership remains queue-only until the active turn ends."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("writer !== 'unknown'", js)


class TestModelPicker(unittest.TestCase):
    def test_short_model_alias_strips_claude_prefix_and_1m_suffix(self):
        """`/model` slash command takes the alias form, not the full id —
        the helper has to round-trip both shapes consistently."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6[1m]"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("opus-4-7"), "opus-4-7")
        self.assertEqual(server._short_model_alias("sonnet"), "sonnet")
        self.assertEqual(server._short_model_alias(""), "")
        self.assertEqual(server._short_model_alias(None), "")

    def test_build_slash_model_command_appends_1m_suffix_when_requested(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        # Full prefixed model IDs — the TUI /model command rejects bare
        # versioned aliases ("Model 'haiku-4-5' not found"), so we emit the
        # claude- prefixed id (verified accepted live).
        self.assertEqual(server._build_slash_model_command("opus-4-7", False), "/model claude-opus-4-7")
        self.assertEqual(server._build_slash_model_command("opus-4-7", True), "/model claude-opus-4-7[1m]")
        self.assertEqual(server._build_slash_model_command("haiku-4-5", False), "/model claude-haiku-4-5")
        # Sonnet has no 1M tier, so the [1m] suffix is dropped.
        self.assertEqual(server._build_slash_model_command("claude-sonnet-4-6", True), "/model claude-sonnet-4-6")
        self.assertEqual(server._build_slash_model_command("", True), "")

    def test_session_override_roundtrip_through_sidecar(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "session-overrides.json"
            orig = server.SESSION_OVERRIDES_FILE
            server.SESSION_OVERRIDES_FILE = path
            try:
                self.assertIsNone(server._get_session_override("sid-1"))
                server._set_session_override("sid-1", "claude-opus-4-8", True, "claude")
                got = server._get_session_override("sid-1")
                self.assertEqual(got["model"], "claude-opus-4-8")
                self.assertTrue(got["context_1m"])
                server._set_session_override("sid-1", "claude-sonnet-4-6", True, "claude")
                got = server._get_session_override("sid-1")
                self.assertEqual(got["model"], "claude-sonnet-4-6")
                self.assertFalse(got["context_1m"])
                self.assertEqual(got["engine"], "claude")
                server._clear_session_override("sid-1")
                self.assertIsNone(server._get_session_override("sid-1"))
            finally:
                server.SESSION_OVERRIDES_FILE = orig

    def test_sonnet_is_not_marked_as_one_m_context_in_model_picker(self):
        """Sonnet is a 200k-context model; only Opus variants get the 1M badge."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()

        self.assertIn("function claudeModelSupportsOneM(model)", js)
        self.assertIn("return n === 'opus-4-8' || n === 'opus-4-7';", js)
        self.assertIn("const modelSupportsOneM = engine === 'claude' && claudeModelSupportsOneM(displayModel);", js)
        self.assertIn("const isOneM = modelSupportsOneM && (", js)
        self.assertIn("{ id: 'sonnet-5',  label: 'sonnet-5',  oneM: false }", js)
        self.assertNotIn("{ id: 'sonnet-4-6'", js)  # removed in CCC-484

    def test_pinned_conversations_roundtrip_and_sort_first(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp)
            path = state_dir / "pinned-conversations.json"
            orig_file = server.PINNED_CONVERSATIONS_FILE
            orig_state = server.LOG_VIEWER_STATE_DIR
            server.PINNED_CONVERSATIONS_FILE = path
            server.LOG_VIEWER_STATE_DIR = state_dir
            try:
                server._save_pinned_conversations(["sid-2", "sid-1"])
                self.assertEqual(server._load_pinned_conversations(), ["sid-2", "sid-1"])
                rows = [
                    {"session_id": "sid-3", "modified": 30},
                    {"session_id": "sid-1", "modified": 10},
                    {"session_id": "sid-2", "modified": 20},
                ]
                server._apply_pinned_conversation_fields(rows)
                server._sort_pinned_conversations_first(rows)
                self.assertEqual([r["session_id"] for r in rows], ["sid-2", "sid-1", "sid-3"])
                self.assertTrue(rows[0]["pinned"])
                self.assertEqual(rows[0]["pin_rank"], 0)
            finally:
                server.PINNED_CONVERSATIONS_FILE = orig_file
                server.LOG_VIEWER_STATE_DIR = orig_state

    def test_pin_route_and_row_action_hooks_are_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("/api/conversations/[^/]+/files", src)
        self.assertIn("class=\"conv-pin-btn", js)
        self.assertIn("mergeBtn + startBtn + pinBtn + lifecycleButtons", js)
        self.assertIn("Pinned to top", js)
        self.assertIn("_minPinnedRank", js)
        self.assertNotIn("conv-pinned-section", js)
        self.assertIn("if (c.pinned) return true", js)
        self.assertIn("c.pinned ||", js)
        self.assertIn("applyOptimisticOverrides(rowsForRender)", js)
        self.assertIn("function _restoreConversationListScrollTop", js)
        self.assertIn("const pinScrollTop = $convList ? $convList.scrollTop : null", js)
        self.assertIn("_restoreConversationListScrollTop($convList, pinScrollTop)", js)
        self.assertNotIn("scrollConversationRowIntoView(convId, data.pinned ? 'start' : 'nearest')", js)
        self.assertIn(".conv-item .conv-pin-btn", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-row-actions:not(:empty)", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-pin-btn.is-unpin", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::before", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::after", css)
        self.assertNotIn("#convList .conv-pinned-section", css)

    def test_session_model_route_registered_and_check_same_origin_gates_post(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        # Routes registered
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model", src)
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model/clear", src)
        # do_POST gates everything through _check_same_origin first
        post_idx = src.find("def do_POST")
        self.assertGreater(post_idx, 0)
        self.assertIn("_check_same_origin", src[post_idx:post_idx + 200])

    def test_extract_session_slash_commands_from_init_event(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps({
                    "type": "system",
                    "subtype": "init",
                    "slash_commands": [
                        "/compact",
                        {"name": "project:ship", "description": "Ship this repo"},
                        {"command": "/review", "purpose": "Review changes"},
                    ],
                }) + "\n"
            )
            orig = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                result = server.extract_session_slash_commands(sid)
            finally:
                server.PROJECTS_ROOT = orig

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "transcript")
        commands = {c["name"]: c.get("description", "") for c in result["commands"]}
        self.assertIn("/compact", commands)
        self.assertIn("/mcp", commands)
        self.assertEqual(commands["/project:ship"], "Ship this repo")
        self.assertEqual(commands["/review"], "Review changes")

    def test_slash_command_files_and_skills_are_discovered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            command_dir = root / "commands"
            command_dir.mkdir()
            (command_dir / "ship.md").write_text("# Ship\n\nRun the release flow.\n")
            nested = command_dir / "commit-commands"
            nested.mkdir()
            (nested / "commit.md").write_text("---\ndescription: Commit current work\n---\n")
            (command_dir / "old.md.bak").write_text("# Ignore\n")

            skill_dir = root / "skills"
            skill = skill_dir / "screenshot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\ndescription: Inspect screenshots\n---\n")

            commands = server._merge_slash_commands(
                server._slash_commands_from_command_dir(command_dir),
                server._slash_commands_from_command_dir(command_dir, prefix="plugin-name"),
                server._slash_commands_from_skill_dir(skill_dir),
            )

        names = {c["name"]: c.get("description", "") for c in commands}
        self.assertEqual(names["/ship"], "Ship")
        self.assertEqual(names["/commit-commands:commit"], "Commit current work")
        self.assertEqual(names["/plugin-name:ship"], "Ship")
        self.assertEqual(names["/screenshot"], "Inspect screenshots")
        self.assertNotIn("/old.md", names)

    def test_session_slash_commands_route_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/session/[a-zA-Z0-9_-]+/slash-commands", src)

    def test_extract_session_usage_resets_at_compact_boundary(self):
        """`/compact` emits a `compact_boundary` system event; assistant
        turns before that boundary no longer contribute to the live
        context window. The pre-fix behavior accumulated peak across
        the whole file, overstating usage."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-666666666666"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 80_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 100_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "manual", "preTokens": 180_500},
        }
        small_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "after compact"}],
                "usage": {
                    "input_tokens": 1_200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 9_000,
                    "output_tokens": 80,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
                + json.dumps(small_turn) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root
        # latest = post-compact small turn's window
        self.assertEqual(usage["latest_input_tokens"], 1_200 + 9_000)
        # peak resets at the boundary, so it's the post-compact peak — NOT the big pre-compact value
        self.assertEqual(usage["peak_input_tokens"], 1_200 + 9_000)
        self.assertEqual(usage["compact_count"], 1)

    def test_extract_session_usage_uses_compact_post_tokens_until_next_turn(self):
        """A compact boundary is the first reliable post-compact signal.
        Use its postTokens count immediately instead of leaving the footer
        pinned to the pre-compact peak until another assistant turn lands."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-777777777777"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 260_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 210_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-posttokens"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 13_781)
        self.assertEqual(usage["peak_input_tokens"], 13_781)
        self.assertEqual(usage["compact_count"], 1)
        self.assertEqual(usage["context_limit"], 1_000_000)

    def test_extract_session_usage_captures_slash_context_output(self):
        """The footer can show Claude's live `/context` count separately
        from CCC's post-compact transcript estimate."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-999999999999"
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 773_985,
                "postTokens": 12_673,
            },
        }
        context_output = {
            "type": "system",
            "subtype": "local_command",
            "sessionId": sid,
            "timestamp": "2026-05-26T19:52:30.316Z",
            "content": (
                "<local-command-stdout>## Context Usage\n\n"
                "**Model:** claude-opus-4-7  \n"
                "**Tokens:** 47.3k / 1m (5%)\n\n"
                "### Estimated usage by category\n"
                "| Category | Tokens | Percentage |\n"
                "| Messages | 21.5k | 2.2% |\n"
                "</local-command-stdout>"
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-context-output"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(boundary) + "\n"
                + json.dumps(context_output) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 12_673)
        self.assertEqual(usage["live_context_tokens"], 47_300)
        self.assertEqual(usage["live_context_limit"], 1_000_000)
        self.assertEqual(usage["live_context_percent"], 5)
        self.assertEqual(usage["live_context_source"], "/context")
        self.assertEqual(usage["live_context_timestamp"], "2026-05-26T19:52:30.316Z")
        self.assertEqual(usage["model"], "claude-opus-4-7")

    def test_context_footer_renders_calc_and_slash_context_values(self):
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("Calculated estimate:", js)
        self.assertIn("Latest /context output:", js)
        self.assertIn("'calc'", js)
        self.assertIn("' · /ctx '", js)

    def test_codex_model_picker_marks_current_reasoning_effort(self):
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("const currentReasoningEffort = (ovr && ovr.reasoning_effort) || u.reasoning_effort || '';", js)
        self.assertIn("const effortInner = currentReasoningEffort", js)
        self.assertIn("wp-model-effort", js)
        self.assertIn('data-reasoning="\' + escapeHtml(currentReasoningEffort) + \'"', js)
        self.assertIn("const currentReasoning = btn.dataset.reasoning || '';", js)
        self.assertIn("const isActive = lvl.id === currentReasoning;", js)

    def test_new_codex_session_composer_sends_selected_reasoning_effort(self):
        """New Codex sessions need an effort picker alongside their model picker."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text()
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text()

        self.assertIn('id="convInputEffortSelect"', html)
        self.assertIn('id="spawnDefaultsEffort"', html)
        self.assertIn("const $convInputEffortSelect", js)
        self.assertIn("reasoning_effort: spawnDefaultsState.reasoning_effort", js)
        self.assertIn("spawnDefaultsDraft.reasoning_effort = $spawnDefaultsEffort.value", js)
        self.assertIn("let spawnEffortChoiceDirty = false;", js)
        self.assertIn("if (!spawnEffortChoiceDirty && $convInputEffortSelect)", js)
        self.assertIn("spawnEffortChoiceDirty = true;", js)
        self.assertIn("spawnEffortChoiceDirty = false;\n    syncSpawnEngineDependentUi();", js)
        self.assertIn("($convInputEffortSelect.value || spawnEffortChoiceDirty)", js)
        self.assertIn("spawnBody.reasoning_effort = $convInputEffortSelect.value", js)
        self.assertIn("def spawn_session_codex(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, reasoning_effort=\"\", parent_session_id=None):", server_py)
        self.assertIn('cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])', server_py)

    def test_context_footer_renders_token_optimizer_quality_score(self):
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("function _formatTokenOptimizerQuality", js)
        self.assertIn("wp-quality-pill", js)
        footer_quality = js[
            js.index("function _formatTokenOptimizerQuality"):
            js.index("function renderSessionUsageIntoStrip", js.index("function _formatTokenOptimizerQuality"))
        ]
        self.assertIn("const label = (grade ? grade + ' ' : '') + rounded;", footer_quality)
        self.assertNotIn("const label = 'Q ' +", footer_quality)
        self.assertLess(js.index("qualityPill + '<span class=\"' + cls"), js.index("+ sourceLabel + ' ' + _formatTokens(displayTokens)"))
        self.assertIn(".conv-input-context .wp-quality-pill", css)

    def test_extract_session_usage_includes_token_optimizer_quality_score(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-4333-8444-999999999999"
        assistant = {
            "type": "assistant",
            "timestamp": "2026-05-26T19:52:30.316Z",
            "sessionId": sid,
            "message": {
                "id": "msg-quality-1",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input_tokens": 12_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 3_000,
                    "output_tokens": 500,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            home = root / "home"
            quality_dir = home / ".claude" / "token-optimizer"
            quality_dir.mkdir(parents=True)
            (quality_dir / f"quality-cache-{sid}.json").write_text(
                json.dumps({
                    "score": 79.2,
                    "grade": "B",
                    "timestamp": "2026-06-25T19:53:10.896627+00:00",
                    "breakdown": {
                        "context_fill_degradation": {"detail": "45% fill, peak zone"},
                        "stale_reads": {"detail": "1 stale file read"},
                    },
                }),
                encoding="utf-8",
            )
            project = root / "projects" / "-tmp-project-quality"
            project.mkdir(parents=True)
            (project / f"{sid}.jsonl").write_text(json.dumps(assistant) + "\n", encoding="utf-8")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root / "projects"
            try:
                with mock.patch.object(server.Path, "home", return_value=home), \
                     mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_is_cursor_session", return_value=False), \
                     mock.patch.object(server, "_is_antigravity_session", return_value=False), \
                     mock.patch.object(server, "_is_kilo_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["quality_score"], 79.2)
        self.assertEqual(usage["quality_grade"], "B")
        self.assertEqual(usage["quality_timestamp"], "2026-06-25T19:53:10.896627+00:00")
        self.assertIn("45% fill, peak zone", usage["quality_summary"])

    def test_truncate_session_name_clamps_long_pastes(self):
        """A row title that's a full annotation context blob would stretch
        the sidebar and bloat /api/sessions responses; clamp it instead."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertIsNone(server._truncate_session_name(None))
        self.assertEqual(server._truncate_session_name(""), "")
        self.assertEqual(server._truncate_session_name("   "), "")
        self.assertEqual(server._truncate_session_name("Short title"), "Short title")
        self.assertEqual(
            server._truncate_session_name("hello\n\n   world"),
            "hello world",
        )
        long = "Annotation note: " + ("blah " * 4000)
        clipped = server._truncate_session_name(long)
        self.assertLessEqual(len(clipped), server.SESSION_NAME_MAX_CHARS)
        self.assertTrue(clipped.endswith("…"))
    def test_codex_rows_keep_a_full_title_for_the_status_rail(self):
        """CCC-566: the rail should not inherit the sidebar's 120-char cap."""
        server_text = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn('status_rail_title = title if title and title != first_message else display_name', server_text)
        self.assertIn("const railTitle = row && row.status_rail_title || title || category || 'Session';", app_js)
        self.assertIn("addParam('status_rail_title', row.status_rail_title || '', 500);", app_js)


    def test_parse_conversation_surfaces_compact_boundary(self):
        """The transcript pane should show feedback when `/compact` finishes."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-888888888888"
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "timestamp": "2026-05-25T01:54:30.071Z",
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-event"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(boundary) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False):
                    result = server.parse_conversation(sid, use_cache=False)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["type"], "system")
        self.assertEqual(event["subtype"], "compact_boundary")
        self.assertEqual(event["session"], sid)
        self.assertEqual(event["compact"]["trigger"], "manual")
        self.assertEqual(event["compact"]["pre_tokens"], 470_054)
        self.assertEqual(event["compact"]["post_tokens"], 13_781)
        self.assertEqual(event["compact"]["duration_ms"], 130_502)

    def test_extract_antigravity_usage_rpc(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "22222222-3333-4444-5555-777777777777"

        # Test case 1: RPC succeeds and returns usage metrics
        fake_response = {
            "trajectory": {
                "steps": [
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "5000",
                                "outputTokens": "150",
                                "cacheReadTokens": "1000",
                                "thinkingTokens": "400",
                            }
                        }
                    },
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "6000",
                                "outputTokens": "200",
                                "cacheReadTokens": "1200",
                                "cacheCreationTokens": 100,
                                "thinkingTokens": 600,
                            }
                        }
                    }
                ]
            }
        }

        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": True, "response": fake_response}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)

            # check stats for second step: input (6000) + cacheRead (1200) + cacheCreation (100) = 7300
            self.assertEqual(usage["latest_input_tokens"], 7300)
            self.assertEqual(usage["peak_input_tokens"], 7300)
            self.assertEqual(usage["total_input_tokens"], 5000 + 6000)
            self.assertEqual(usage["total_cache_read_tokens"], 1000 + 1200)
            self.assertEqual(usage["total_cache_creation_tokens"], 100)
            self.assertEqual(usage["total_output_tokens"], 150 + 200)
            # Per-turn thinking tokens are summed for the bottom-bar totals.
            self.assertEqual(usage["total_thinking_tokens"], 400 + 600)
            self.assertEqual(usage["model"], "gemini-1.5-pro")
            self.assertEqual(usage["engine"], "antigravity")
            self.assertEqual(usage["context_limit"], 1_000_000)

        # Test case 2: RPC fails, it should fall back to empty defaults
        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": False}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)
            self.assertEqual(usage["latest_input_tokens"], 0)
            self.assertEqual(usage["peak_input_tokens"], 0)
            self.assertEqual(usage["total_thinking_tokens"], 0)
            self.assertEqual(usage["model"], "")
            self.assertEqual(usage["engine"], "antigravity")

    def test_parse_antigravity_event_attaches_per_turn_tokens(self):
        """Assistant events should carry tokens_in/out/thinking when the
        trajectory's modelUsage covers the event's step_index. This is what
        feeds the per-turn chips in the conversation pane."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        usage_map = {
            13: {"in": 11200, "out": 2600, "thinking": 1000,
                 "cache_read": 8400, "cache_create": 0, "model": "agy-1"},
        }
        ev_with_step = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 13,
            "created_at": "2026-05-22T10:00:00Z",
            "content": "Here is the plan.",
            "tool_calls": [],
        }
        out = server._parse_antigravity_event(ev_with_step, 99, usage_map=usage_map)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "assistant")
        self.assertEqual(out["tokens_in"], 11200)
        self.assertEqual(out["tokens_out"], 2600)
        self.assertEqual(out["tokens_thinking"], 1000)
        self.assertEqual(out["tokens_cached"], 8400)

        # Step index with no matching trajectory entry → no token fields,
        # so the frontend falls back to the no-chip render path.
        ev_no_match = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 999,
            "created_at": "2026-05-22T10:00:01Z",
            "content": "Another step.",
            "tool_calls": [],
        }
        out2 = server._parse_antigravity_event(ev_no_match, 100, usage_map=usage_map)
        self.assertIsNotNone(out2)
        self.assertNotIn("tokens_in", out2)
        self.assertNotIn("tokens_out", out2)
        self.assertNotIn("tokens_thinking", out2)

        # Old call shape (no usage_map kw) must still work.
        out3 = server._parse_antigravity_event(ev_with_step, 101)
        self.assertIsNotNone(out3)
        self.assertNotIn("tokens_in", out3)



class TestGroupChatSidecarHelpers(unittest.TestCase):
    """Cover the small helpers that load/merge sidecar JSON, list chats,
    and flip the archived flag. Uses a tempdir-backed fake group-chats dir
    so we don't touch the user's real ~/.claude/group-chats."""

    def _setup_fake_dir(self, server, tmpdir):
        """Patch the helpers to look at a tempdir instead of ~/.claude/group-chats."""
        gcd = pathlib.Path(tmpdir) / "group-chats"
        gcd.mkdir()
        # Patch os.path.expanduser via monkeypatching os.path.expanduser only
        # for the specific path. Simpler: just write into the real ~ via a
        # subdirectory the helpers don't know about. The helpers all derive
        # the dir from os.path.expanduser("~/.claude/group-chats") — so
        # monkey-patch that lookup.
        return gcd

    def test_sidecar_round_trip(self):
        """_load_group_chat_sidecar / _update_group_chat_sidecar must merge
        fields atomically and survive missing files."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "demo.md")
            with open(md, "w") as fh:
                fh.write("# demo\n")
            # No sidecar yet → load returns {}
            self.assertEqual(server._load_group_chat_sidecar(md), {})
            # Update creates the sidecar
            ok = server._update_group_chat_sidecar(
                md, archived=True, archived_at=1234.5, topic="hi"
            )
            self.assertTrue(ok)
            data = server._load_group_chat_sidecar(md)
            self.assertEqual(data.get("topic"), "hi")
            self.assertIs(data.get("archived"), True)
            self.assertEqual(data.get("archived_at"), 1234.5)
            # Subsequent merge preserves prior fields
            server._update_group_chat_sidecar(md, archived=False)
            data2 = server._load_group_chat_sidecar(md)
            self.assertEqual(data2.get("topic"), "hi")
            self.assertIs(data2.get("archived"), False)

    def test_list_group_chats_backfills_uuid_identity(self):
        """Legacy path-keyed group chats should gain stable UUIDs."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            gcd = pathlib.Path(tmp) / "group-chats"
            gcd.mkdir()
            md = gcd / "demo.md"
            md.write_text("# Group Chat — Demo\n", encoding="utf-8")
            (gcd / "demo.json").write_text(json.dumps({
                "session_ids": [],
                "topic": "Demo",
                "mode": "topic",
                "name_map": {},
                "archived": False,
            }), encoding="utf-8")

            orig_expanduser = server.os.path.expanduser

            def fake_expanduser(path):
                if path == "~/.claude/group-chats":
                    return str(gcd)
                return orig_expanduser(path)

            with mock.patch.object(server.os.path, "expanduser", side_effect=fake_expanduser):
                chats = server._list_group_chats(include_archived=False)

            self.assertEqual(len(chats), 1)
            self.assertRegex(chats[0]["uuid"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            self.assertEqual(chats[0]["id"], chats[0]["uuid"])
            sidecar = json.loads((gcd / "demo.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["uuid"], chats[0]["uuid"])

    def test_list_active_group_chat_summaries_includes_path_and_id(self):
        """CCC-508: the lightweight polling summary must still carry
        id/uuid/path/path_tilde. The sidebar's "In Group Chat" row click
        handler only opens the reader when one of those is present
        (`if (path || chatId) openGroupChatReader(...)`); dropping them
        silently turned every row unclickable."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            gcd = pathlib.Path(tmp) / "group-chats"
            gcd.mkdir()
            (gcd / "demo.md").write_text("# Group Chat — Demo\n", encoding="utf-8")
            (gcd / "demo.json").write_text(json.dumps({
                "session_ids": [],
                "topic": "Demo",
                "mode": "topic",
                "name_map": {},
                "archived": False,
                "last_message_at": time.time(),
            }), encoding="utf-8")

            orig_expanduser = server.os.path.expanduser

            def fake_expanduser(path):
                if path == "~/.claude/group-chats":
                    return str(gcd)
                return orig_expanduser(path)

            with mock.patch.object(server.os.path, "expanduser", side_effect=fake_expanduser):
                summaries = server._list_active_group_chat_summaries()

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0]["state"], "active")
            self.assertTrue(summaries[0]["id"])
            self.assertEqual(summaries[0]["id"], summaries[0]["uuid"])
            self.assertTrue(summaries[0]["path"].endswith("demo.md"))
            self.assertEqual(summaries[0]["path_tilde"], "~/.claude/group-chats/demo.md")

    def test_group_chat_header_syncs_sidecar_topic_and_participants(self):
        """Reader refresh should repair stale markdown headers without
        touching the message history.
        """
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "**Wake-status:**\n"
                "- (no participants)\n"
                "---\n\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — Human\n\n"
                "please sync\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {sid: "Agent One"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(
                server,
                "_group_chat_participant_meta",
                return_value={"is_live": False, "last_activity": 0},
            ):
                server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("# Group Chat — APIFY sync", updated)
            self.assertIn("**Participants:** `Agent One`, `human`", updated)
            self.assertIn("- `Agent One` (aaaaaaaa): offline", updated)
            self.assertIn("## 2026-05-27 Wednesday 12:07:59 PDT — Human", updated)
            self.assertIn("please sync", updated)

    def test_group_chat_header_rewrite_preserves_pre_boundary_history(self):
        """Header repair must preserve legacy text before the first separator."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "**Wake-status:**\n"
                "- (no participants)\n"
                "Agent pre-history line one\n"
                "- Agent pre-history bullet\n"
                "> _2026-05-27 12:00:00 PDT — system: created chat_\n"
                "---\n\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — Human\n\n"
                "please sync\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {sid: "Agent One"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(
                server,
                "_group_chat_participant_meta",
                return_value={"is_live": False, "last_activity": 0},
            ):
                server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("# Group Chat — APIFY sync", updated)
            self.assertIn("Agent pre-history line one", updated)
            self.assertIn("- Agent pre-history bullet", updated)
            self.assertIn("system: created chat", updated)
            self.assertIn("## 2026-05-27 Wednesday 12:07:59 PDT — Human", updated)
            self.assertIn("please sync", updated)

    def test_group_chat_header_rewrite_preserves_system_log_without_boundary(self):
        """Creation logs can exist before any message separator is present."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "> _2026-05-27 12:00:00 PDT — system: created empty chat_\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {},
                "include_human": True,
            }), encoding="utf-8")

            server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("**Wake-status:**", updated)
            self.assertIn("- (no participants)", updated)
            self.assertIn("system: created empty chat", updated)

    def test_group_chat_nudge_only_reminds_once_per_latest_post(self):
        """Repeated nudges for one chat turn must not flood recipients."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid_a = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            sid_b = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
            md.write_text(
                "# Group Chat - Demo\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — aaaaaaaa: Agent A\n\n"
                "I need another agent to review this.\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid_a, sid_b],
                "topic": "Demo",
                "mode": "topic",
                "name_map": {sid_a: "Agent A", sid_b: "Agent B"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(server, "_resolve_group_chat_ref", return_value=str(md)), \
                    mock.patch.object(server, "_inject_text_into_session", return_value={"ok": True}) as inject:
                first = server._group_chat_nudge(str(md))
                second = server._group_chat_nudge(str(md))
                md.write_text(
                    md.read_text(encoding="utf-8")
                    + "## 2026-05-27 Wednesday 12:08:30 PDT — Human\n\n"
                    + "Agent A, please follow up.\n",
                    encoding="utf-8",
                )
                third = server._group_chat_nudge(str(md))

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(second.get("skipped"), "already reminded")
            self.assertTrue(third["ok"])
            # First turn: everyone except the author (Agent A) -> only B.
            # Third turn: Human post without an explicit @mention pings all
            # agents ("Agent A" in prose does not narrow targeting).
            self.assertEqual(inject.call_count, 3)
            self.assertEqual(inject.call_args_list[0].args[0], sid_b)
            self.assertEqual(
                {call.args[0] for call in inject.call_args_list[1:]},
                {sid_a, sid_b},
            )

    def test_message_count_counts_h2_lines(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write("# header\n## one\nbody\n## two\n## three — author\n")
            self.assertEqual(server._group_chat_message_count(md), 3)

    def test_latest_message_snapshot_uses_latest_post(self):
        """The injected wake-up hint should show the latest post only."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:00 PDT — aaaaaaaa: ALPHA\n\n"
                    "old body\n\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "new body\n"
                    "## markdown subheading inside the message\n"
                    "more detail\n"
                    "> _2026-05-13 10:02:00 PDT — system: pinged `ALPHA`_\n"
                )
            snapshot = server._group_chat_latest_message_snapshot(md)
            self.assertIn("Human", snapshot)
            self.assertIn("new body", snapshot)
            self.assertIn("markdown subheading inside the message", snapshot)
            self.assertNotIn("old body", snapshot)
            self.assertNotIn("system: pinged", snapshot)

    def test_group_chat_inject_text_includes_latest_snapshot(self):
        """Participants get a bounded advisory snapshot in the injection."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "please respond\n"
                )
            text = server._group_chat_inject_text(
                md, 'topic with "quotes"', "topic", "abc12345-session"
            )
            # CCC-108: no leading "/" — slash-form only dispatches in a live
            # Claude TUI; Codex / headless Claude need an instruction.
            self.assertFalse(text.startswith("/"))
            self.assertIn("group-chat-checkin skill", text)
            self.assertIn(f'chat="{md}"', text)
            self.assertIn('topic="topic with \\"quotes\\""', text)
            self.assertIn('sid="abc12345-session"', text)
            self.assertIn("CCC pointer: a new post just landed", text)
            self.assertIn("## 2026-05-13 10:01 PDT — Human", text)

    def test_resolve_group_chat_path_rejects_outside_dir(self):
        """The path validator must clamp to ~/.claude/group-chats/ and
        reject anything outside (no path traversal)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Outside the group-chats dir → ""
        self.assertEqual(server._resolve_group_chat_path("/etc/passwd"), "")
        self.assertEqual(server._resolve_group_chat_path(""), "")
        self.assertEqual(server._resolve_group_chat_path("../../../tmp/x.md"), "")

    def test_group_chat_add_participant_preserves_display_name(self):
        """Adding a participant with an explicit display_name should preserve
        it in the sidecar name_map and log the added participant."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            md.write_text("# Group Chat - Test\n", encoding="utf-8")
            js_path = pathlib.Path(tmp) / "chat.json"
            js_path.write_text(json.dumps({
                "session_ids": [],
                "name_map": {},
                "topic": "Test",
                "mode": "topic"
            }), encoding="utf-8")

            sid = "cccccccc-3333-4333-8333-cccccccccccc"
            display_name = "My Awesome Agent"

            with mock.patch.object(server, "_resolve_group_chat_ref", return_value=str(md)), \
                 mock.patch.object(server, "_inject_text_into_session", return_value={"ok": True}):
                res = server._group_chat_add_participant(str(md), sid, display_name=display_name)

            self.assertTrue(res["ok"])
            self.assertEqual(res["session_id"], sid)

            # Load sidecar and verify
            sidecar = server._load_group_chat_sidecar(str(md))
            self.assertIn(sid, sidecar.get("session_ids", []))
            self.assertEqual(sidecar.get("name_map", {}).get(sid), "My Awesome Agent")

    def test_group_chat_reader_poller_primes_redesign_state(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("_gcReaderSessionIds = data.session_ids || [];", app_js)
        self.assertIn("_gcReaderNameMap = data.name_map || {};", app_js)
        self.assertIn("buildAgentFallbackNames(data.content, _gcReaderSessionIds);", app_js)
        self.assertIn("_gcReplayData = data;", app_js)
        self.assertIn("updateGcInfoBar(data);", app_js)

    def test_group_chat_reader_uses_numbered_agent_fallbacks(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertNotIn("return 'Agent-' + shortSid;", app_js)
        self.assertIn("looksLikeShortHash", app_js)
        self.assertIn("_gcAgentFallbackNames[key] = 'Agent-' +", app_js)

    def test_group_chat_reader_panel_controls_keep_chat_ref(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn('data-gc-enable data-gc-path="${escapeAttr(chatPath)}" data-gc-id="${escapeAttr(chatId)}"', app_js)
        self.assertIn('data-gc-stop data-gc-path="${escapeAttr(chatPath)}" data-gc-id="${escapeAttr(chatId)}"', app_js)


class TestTemplateGallery(unittest.TestCase):
    def test_templates_json_parses_and_has_required_shape(self):
        """The New Session modal's template gallery is driven by
        static/templates.json. Every template must carry the fields the
        UI binds to — id, name, description, engine, worktree, prompt —
        and the JSON must be valid so the gallery doesn't render blank."""
        path = pathlib.Path(PROJECT_ROOT, "static", "templates.json")
        self.assertTrue(path.is_file(), "static/templates.json missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        templates = data.get("templates")
        self.assertIsInstance(templates, list)
        self.assertGreaterEqual(
            len(templates), 5,
            "issue #46 ships with at least five starter templates",
        )
        seen_ids = set()
        for t in templates:
            for key in ("id", "name", "description", "engine", "worktree", "prompt"):
                self.assertIn(key, t, f"template missing {key!r}: {t.get('id')}")
            self.assertIsInstance(t["id"], str)
            self.assertNotIn(t["id"], seen_ids, "duplicate template id")
            seen_ids.add(t["id"])
            self.assertIn(t["engine"], ("claude", "codex", "gemini"))
            self.assertIsInstance(t["worktree"], bool)
            self.assertIsInstance(t["prompt"], str)
            self.assertGreater(len(t["prompt"].strip()), 0)


class TestHealthcheck(unittest.TestCase):
    def test_healthcheck_returns_structured_result(self):
        """_run_healthcheck must always return a dict with 'checks' and
        'overall' keys, even on a fresh install with nothing configured."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        result = server._run_healthcheck()
        self.assertIn("checks", result)
        self.assertIn("overall", result)
        self.assertIn(result["overall"], ("ok", "warn", "error"))
        self.assertIsInstance(result["checks"], list)
        self.assertGreater(len(result["checks"]), 0)


class TestPendingInputs(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-pending-inputs-")
        self.server.PENDING_INPUTS_FILE = pathlib.Path(self.tmp_dir) / "pending-inputs.json"

        # Clear locks/queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def test_save_and_load_pending_inputs(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["hello resume"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["hello term"]

        # Save to disk
        self.server._save_pending_inputs()
        self.assertTrue(self.server.PENDING_INPUTS_FILE.is_file())

        # Clear memory queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

        # Load from disk
        self.server._load_pending_inputs()

        # Verify loaded correctly
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue.get(sid), ["hello resume"])
        with self.server._pending_terminal_input_lock:
            self.assertEqual(self.server._pending_terminal_input_queue.get(sid), ["hello term"])

    def test_pending_inputs_watcher_lock_rejects_another_process(self):
        """Only one CCC server may drain a shared durable input queue."""
        import fcntl

        lock_path = pathlib.Path(self.tmp_dir) / "pending-inputs.watcher.lock"
        with open(lock_path, "a+") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIsNone(self.server._acquire_pending_inputs_watcher_lock(lock_path))

    def test_pending_inputs_watcher_retries_when_another_server_owns_lock(self):
        """A sibling server must wait for ownership rather than give up forever."""
        source = inspect.getsource(self.server._start_resume_queue_watcher)
        self.assertIn("while _pending_inputs_watcher_lock_file is None:", source)
        self.assertIn("time.sleep(5)", source)

    def test_queue_codex_resume_schedules_conversation_pump(self):
        with mock.patch.object(self.server, "_schedule_codex_queue_pump") as schedule:
            self.server._queue_codex_resume("sid-a", "first")

        schedule.assert_called_once_with("sid-a")

    def test_codex_queue_pump_delivers_and_removes_only_fifo_head(self):
        sid = "sid-fifo"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["first", "second"]

        with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
             mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=False), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "accepted": True},
             ) as resume:
            result = self.server._pump_codex_resume_queue(sid)

        self.assertTrue(result["delivered"])
        resume.assert_called_once_with(sid, "first", _from_queue=True)
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue[sid], ["second"])

    def test_codex_queue_pump_holds_while_turn_is_active(self):
        sid = "sid-active"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["wait"]

        with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
             mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=True), \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._pump_codex_resume_queue(sid)

        self.assertEqual(result["waiting"], "busy")
        resume.assert_not_called()

    def test_codex_queue_pump_retains_head_after_delivery_failure(self):
        sid = "sid-failure"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["keep"]

        with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
             mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=False), \
             mock.patch.object(self.server, "resume_session_codex", return_value={"ok": False}):
            self.server._pump_codex_resume_queue(sid)

        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue[sid], ["keep"])

    def test_codex_queue_pump_suppresses_concurrent_delivery(self):
        sid = "sid-concurrent"
        lock = self.server._codex_queue_pump_lock(sid)
        lock.acquire()
        try:
            result = self.server._pump_codex_resume_queue(sid)
        finally:
            lock.release()

        self.assertEqual(result["waiting"], "already-pumping")

    def test_get_queued_events_for_session(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["r1", "r2"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["t1"]

        events = self.server._get_queued_events_for_session(sid)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["text"], "r1")
        self.assertTrue(events[0]["pending"])
        self.assertEqual(events[1]["text"], "r2")
        self.assertEqual(events[2]["text"], "t1")
        self.assertTrue(events[2]["pending"])

    def test_synthetic_events_merge_by_timestamp_instead_of_appending(self):
        transcript = [
            {"line": 1, "ts": "2026-07-12T10:00:00Z", "type": "assistant"},
            {"line": 3, "ts": "2026-07-12T10:02:00Z", "type": "assistant"},
        ]
        synthetic = [
            {"line": "coord-1", "ts": "2026-07-12T10:01:00Z", "type": "system"},
            {"line": None, "ts": 1783857000.0, "type": "user_text", "pending": True},
        ]

        merged = self.server._merge_synthetic_conversation_events(transcript, synthetic)

        self.assertEqual([event.get("line") for event in merged[:3]], [1, "coord-1", 3])
        self.assertTrue(merged[-1]["pending"])

    def test_consume_matching_pending_input_removes_only_one_copy(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["repeat", "repeat", "keep"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["repeat"]

        removed = self.server._consume_matching_pending_input(sid, " repeat ")

        self.assertEqual(removed, 1)
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue[sid], ["repeat", "keep"])
        with self.server._pending_terminal_input_lock:
            self.assertEqual(self.server._pending_terminal_input_queue[sid], ["repeat"])

    def test_consume_matching_pending_input_falls_back_to_terminal_queue(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["keep"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["queued", "later"]

        removed = self.server._consume_matching_pending_input(sid, "queued")

        self.assertEqual(removed, 1)
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue[sid], ["keep"])
        with self.server._pending_terminal_input_lock:
            self.assertEqual(self.server._pending_terminal_input_queue[sid], ["later"])

    def test_consume_matching_pending_input_persists_cancel(self):
        sid = "cancel-session"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["cancel me", "keep me"]

        with mock.patch.object(self.server, "_save_pending_inputs") as save:
            removed = self.server._consume_matching_pending_input(sid, "cancel me")

        self.assertEqual(removed, 1)
        self.assertEqual(self.server._pending_resume_queue[sid], ["keep me"])
        save.assert_called_once_with()

    def test_pending_input_cancel_endpoint_is_wired(self):
        source = inspect.getsource(self.server.CommandCenterHandler.do_POST)
        self.assertIn('path == "/api/pending-input/cancel"', source)
        self.assertIn("_consume_matching_pending_input(sid, text)", source)
        self.assertIn('"cancelled": 1', source)

    def test_conv_bytes_cache_misses_when_pending_input_queued(self):
        """Pre-serialized /api/conversations bodies must not hide dynamic overlays."""
        sid = "cache-pending-test-session"
        # Mock PROJECTS_ROOT to a tmp dir so the test fixture doesn't leak
        # into the user's real `~/.claude/projects` and surface as a ghost
        # session row in the live CCC UI. The previous version of this test
        # only mocked PENDING_INPUTS_FILE in setUp and used the real
        # PROJECTS_ROOT here, which left
        # `~/.claude/projects/-cache-pending/cache-pending-test-session.jsonl`
        # on disk after every run.
        tmp_projects = tempfile.mkdtemp(prefix="ccc-cache-pending-proj-")
        prev_projects_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(tmp_projects)
        try:
            proj = self.server.PROJECTS_ROOT / "-cache-pending"
            proj.mkdir(parents=True, exist_ok=True)
            jsonl = proj / f"{sid}.jsonl"
            jsonl.write_text(
                json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n",
                encoding="utf-8",
            )
            result = self.server.parse_conversation(sid, after_line=0, use_cache=False)
            raw = json.dumps(result).encode()
            self.server._conv_response_bytes_put(sid, 0, raw, None)
            self.assertIsNotNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue[sid] = ["still waiting"]
            self.assertIsNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()
            self.server._conv_response_bytes_put(sid, 0, raw, None)
            self.assertIsNotNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._CODEX_APP_SERVER_LOCK:
                self.server._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                    "recent_items": [{
                        "id": "item-shell",
                        "type": "commandExecution",
                        "tool": "Bash",
                        "detail": "wt find CHUCK-51 --json",
                        "status": "completed",
                        "ts": 1783600005.0,
                        "updated_at": 1783600005.0,
                    }]
                }
            self.assertIsNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._CODEX_APP_SERVER_LOCK:
                self.server._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)
        finally:
            self.server.PROJECTS_ROOT = prev_projects_root
            shutil.rmtree(tmp_projects, ignore_errors=True)


class TestSessionUsageDedup(unittest.TestCase):
    """Claude Code's JSONL re-records the same API response (same
    `message.id`) under fresh event UUIDs whenever a session is resumed
    or forked. Cost/token totals must dedupe by `message.id` so a session
    that resumed 4 times doesn't show 4x the real cost — see issue #60."""

    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp = tempfile.mkdtemp(prefix="ccc-usage-")
        self.prev_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(self.tmp)

    def tearDown(self):
        self.server.PROJECTS_ROOT = self.prev_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, events):
        proj = pathlib.Path(self.tmp) / "-some-project"
        proj.mkdir(parents=True, exist_ok=True)
        jsonl = proj / f"{sid}.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return jsonl

    def _assistant(self, uuid, msg_id, usage, model="claude-opus-4-7"):
        return {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": "any",
            "message": {
                "id": msg_id,
                "role": "assistant",
                "model": model,
                "usage": usage,
                "content": [{"type": "text", "text": "ok"}],
            },
        }

    def test_duplicate_message_ids_counted_once(self):
        """Two assistant events carrying the same `message.id` come from
        one Anthropic API response replayed by a session resume — totals
        and cost must count them exactly once."""
        sid = "00000000-0000-4000-8000-000000000abc"
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 1_000,
            "cache_read_input_tokens": 10_000,
            "output_tokens": 200,
        }
        # Same msg_id replayed 4 times under different event uuids — the
        # exact pattern observed in real ~/.claude/projects/*.jsonl files
        # after multiple resumes.
        events = [
            self._assistant(f"uuid-{i}", "msg_unique", usage)
            for i in range(4)
        ]
        # Plus one genuinely-different turn.
        other = {
            "input_tokens": 50,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 5_000,
            "output_tokens": 100,
        }
        events.append(self._assistant("uuid-other", "msg_other", other))
        self._write_session(sid, events)

        result = self.server.extract_session_usage(sid)

        # Each input bucket should be counted ONCE for msg_unique plus
        # ONCE for msg_other — not 4x + 1x = 5x.
        self.assertEqual(result["total_input_tokens"], 150)
        self.assertEqual(result["total_cache_creation_tokens"], 1_500)
        self.assertEqual(result["total_cache_read_tokens"], 15_000)
        self.assertEqual(result["total_output_tokens"], 300)

        # Current Opus 4.7 rates: 5 / 6.25 / 0.50 / 25 per Mtok.
        expected = (150 * 5 + 1_500 * 6.25
                    + 15_000 * 0.50 + 300 * 25) / 1_000_000
        self.assertAlmostEqual(result["cost_usd"], round(expected, 4), places=4)

    def test_events_without_message_id_still_summed(self):
        """Defensive: if the JSONL ever lacks `message.id` we must still
        count usage — falling back to a per-event identity rather than
        silently dropping the turn."""
        sid = "00000000-0000-4000-8000-000000000abd"
        usage = {
            "input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 20,
        }
        events = [
            {
                "type": "assistant",
                "uuid": f"u-{i}",
                "sessionId": "any",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "usage": usage,
                    "content": [{"type": "text", "text": "ok"}],
                },
            }
            for i in range(3)
        ]
        self._write_session(sid, events)

        result = self.server.extract_session_usage(sid)
        # Three distinct events, none deduped (no shared id to dedupe by).
        self.assertEqual(result["total_input_tokens"], 30)
        self.assertEqual(result["total_output_tokens"], 60)


class TestThroughputCacheAdjusted(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp = tempfile.mkdtemp(prefix="ccc-throughput-")
        self.prev_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(self.tmp)
        self.server._ENGINE_DETECT_CACHE.clear()

    def tearDown(self):
        self.server.PROJECTS_ROOT = self.prev_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, events):
        proj = pathlib.Path(self.tmp) / "-throughput-project"
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{sid}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return path

    def test_claude_throughput_uses_cache_adjusted_input(self):
        sid = "00000000-0000-4000-8000-000000000abe"
        events = [
            {
                "type": "user",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "sessionId": sid,
                "message": {"role": "user", "content": "measure this"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:30.000Z",
                "sessionId": sid,
                "message": {
                    "id": "msg-throughput-1",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "done"}],
                    "usage": {
                        "input_tokens": 1_000,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 5_000,
                        "output_tokens": 300,
                    },
                },
            },
        ]
        self._write_session(sid, events)

        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
             mock.patch.object(self.server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(self.server, "_is_kilo_session", return_value=False), \
             mock.patch.object(self.server, "_load_desktop_app_metadata", return_value={}):
            payload, status = self.server._throughput_payload(sid)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["turns"]), 1)
        turn = payload["turns"][0]
        self.assertEqual(turn["tokens_in"], 6_200)
        self.assertEqual(turn["fresh_input_tokens"], 1_000)
        self.assertEqual(turn["cache_write_tokens"], 200)
        self.assertEqual(turn["cache_read_tokens"], 5_000)
        # Sonnet cache math: 1000 + 200*1.25 + 5000*0.10.
        self.assertEqual(turn["effective_input_tokens"], 1_750)
        self.assertEqual(turn["effective_input_tpm"], 3_500)

        summary = payload["summary"]
        self.assertEqual(summary["total_raw_context_tokens"], 6_200)
        self.assertEqual(summary["total_effective_input_tokens"], 1_750)
        self.assertEqual(summary["avg_input_tpm"], 12_400)
        self.assertEqual(summary["avg_effective_input_tpm"], 3_500)
        self.assertAlmostEqual(summary["cache_hit_ratio"], 5_000 / 6_200, places=4)
        self.assertGreater(summary["cost_usd"], 0)

    def test_codex_cached_input_is_subset_of_input_tokens(self):
        usage = self.server._throughput_normalize_usage(
            {
                "input_tokens": 1_000,
                "cached_input_tokens": 800,
                "output_tokens": 50,
            },
            engine="codex",
            model="gpt-5.5",
        )

        self.assertEqual(usage["raw_context_tokens"], 1_000)
        self.assertEqual(usage["fresh_input_tokens"], 200)
        self.assertEqual(usage["cache_read_tokens"], 800)
        self.assertEqual(usage["effective_input_tokens"], 280)
        self.assertTrue(usage["cost_available"])
        self.assertGreater(usage["cost_usd"], 0)

    def test_claude_cache_creation_duration_changes_effective_burn(self):
        usage = self.server._throughput_normalize_usage(
            {
                "input_tokens": 100,
                "cache_creation_input_tokens": 300,
                "cache_creation_5m_input_tokens": 100,
                "cache_creation_1h_input_tokens": 200,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
            engine="claude",
            model="claude-sonnet-4-6",
        )

        self.assertEqual(usage["cache_write_tokens"], 300)
        self.assertEqual(usage["cache_write_5m_tokens"], 100)
        self.assertEqual(usage["cache_write_1h_tokens"], 200)
        # 5m cache writes use 1.25x input; 1h cache writes use 2.0x input.
        self.assertEqual(usage["effective_input_tokens"], 625)
        expected_cost = (100 * 3 + 100 * 3.75 + 200 * 6) / 1_000_000
        self.assertAlmostEqual(usage["cost_usd"], expected_cost)

    def test_codex_throughput_uses_each_token_count_event(self):
        sid = "codex-throughput-session"
        path = pathlib.Path(self.tmp) / "rollout-codex.jsonl"
        events = [
            {
                "type": "turn_context",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:01.000Z",
                "payload": {"type": "user_message", "message": "measure codex"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:11.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1_000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 1_050,
                        },
                        "total_token_usage": {
                            "input_tokens": 1_000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 1_050,
                        },
                    },
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:21.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 2_000,
                            "cached_input_tokens": 1_900,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 2_100,
                        },
                        "total_token_usage": {
                            "input_tokens": 3_000,
                            "cached_input_tokens": 2_700,
                            "output_tokens": 150,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 3_150,
                        },
                    },
                },
            },
        ]
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        with mock.patch.object(self.server, "_resolve_codex_rollout_path", return_value=path):
            turns = self.server._throughput_codex_turns_from_file(sid, model_hint="gpt-5.5")

        self.assertEqual(len(turns), 2)
        summary = self.server._throughput_summary(turns)
        self.assertEqual(summary["total_raw_context_tokens"], 3_000)
        self.assertEqual(summary["total_fresh_input_tokens"], 300)
        self.assertEqual(summary["total_cache_read_tokens"], 2_700)
        self.assertEqual(summary["total_output_tokens"], 160)
        self.assertEqual(summary["total_effective_input_tokens"], 570)
        self.assertGreater(summary["cost_usd"], 0)

    def test_codex_usage_extracts_reasoning_effort(self):
        sid = "codex-usage-effort-session"
        path = pathlib.Path(self.tmp) / "rollout-codex-effort.jsonl"
        events = [
            {
                "type": "turn_context",
                "timestamp": "2026-07-11T03:00:00.000Z",
                "payload": {"model": "gpt-5.6-sol", "effort": "high"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-11T03:00:01.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "model_context_window": 353_000,
                        "last_token_usage": {
                            "input_tokens": 61_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 120,
                        },
                        "total_token_usage": {
                            "input_tokens": 61_000,
                            "cached_input_tokens": 0,
                            "output_tokens": 120,
                        },
                    },
                },
            },
        ]
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        with mock.patch.object(self.server, "_resolve_codex_rollout_path", return_value=path), \
             mock.patch.object(self.server, "_codex_thread_row", return_value={}), \
             mock.patch.object(self.server, "_get_session_override", return_value=None):
            usage = self.server._extract_codex_usage(sid)

        self.assertEqual(usage["model"], "gpt-5.6-sol")
        self.assertEqual(usage["reasoning_effort"], "high")

    def test_claude_throughput_dedupes_message_snapshots(self):
        sid = "00000000-0000-4000-8000-000000000abf"
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 1_000,
            "output_tokens": 50,
        }
        events = [
            {
                "type": "user",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "sessionId": sid,
                "message": {"role": "user", "content": "fan out"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:05.000Z",
                "sessionId": sid,
                "requestId": "req-throughput-1",
                "message": {
                    "id": "msg-throughput-dup",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "thinking"}],
                    "usage": usage,
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:20.000Z",
                "sessionId": sid,
                "requestId": "req-throughput-1",
                "message": {
                    "id": "msg-throughput-dup",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}],
                    "usage": usage,
                },
            },
        ]
        self._write_session(sid, events)

        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
             mock.patch.object(self.server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(self.server, "_is_kilo_session", return_value=False), \
             mock.patch.object(self.server, "_load_desktop_app_metadata", return_value={}):
            payload, status = self.server._throughput_payload(sid)

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["turns"]), 1)
        self.assertEqual(payload["turns"][0]["message_id"], "msg-throughput-dup")
        self.assertEqual(payload["turns"][0]["request_id"], "req-throughput-1")
        self.assertEqual(payload["turns"][0]["dur_sec"], 20)
        self.assertEqual(payload["summary"]["total_raw_context_tokens"], 1_120)
        self.assertEqual(payload["summary"]["total_output_tokens"], 50)


class TestCodexEsc(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_interrupt_codex_session_sends_sigint(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "session_live_status") as mock_status, \
             mock.patch.object(
                 self.server,
                 "_codex_interrupt_via_app_server",
                 return_value={"ok": False, "code": "codex_interrupt_unavailable"},
             ), \
             mock.patch.object(self.server.os, "kill") as mock_kill:

            mock_status.return_value = {
                "live": True,
                "pid": 12345,
                "tty": None,
                "terminal_app": None,
            }

            res = self.server._interrupt_session("some-codex-session-id")
            self.assertTrue(res["ok"])
            self.assertEqual(res["via"], "spawn-sigint")
            self.assertEqual(res["pid"], 12345)
            mock_kill.assert_called_once_with(12345, self.server.signal.SIGINT)

    def test_interrupt_non_live_codex_session(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(
                 self.server,
                 "_codex_interrupt_via_app_server",
                 return_value={"ok": False, "code": "codex_no_active_turn"},
             ), \
             mock.patch.object(self.server, "session_live_status") as mock_status:

            mock_status.return_value = {
                "live": False,
                "pid": None,
                "tty": None,
                "terminal_app": None,
            }

            res = self.server._interrupt_session("some-codex-session-id")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "Codex session is not live — nothing to interrupt")

    def test_interrupt_codex_app_server_turn(self):
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params, timeout))
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "id": "some-codex-session-id",
                            "status": {"type": "active"},
                            "turns": [{"id": "turn-active", "status": "inProgress"}],
                        }
                    }
                }
            if method == "turn/interrupt":
                return {"result": {}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "session_live_status", return_value={"live": False, "pid": None}), \
             mock.patch.object(self.server, "_codex_app_server_is_live", return_value=True), \
             mock.patch.object(self.server, "_codex_app_server_request", side_effect=fake_request):

            res = self.server._interrupt_session("some-codex-session-id")

        self.assertTrue(res["ok"])
        self.assertEqual(res["via"], "codex-app-interrupt")
        self.assertEqual(res["turn_id"], "turn-active")
        self.assertEqual([call[0] for call in calls], ["thread/resume", "turn/interrupt"])
        self.assertEqual(calls[1][1], {"threadId": "some-codex-session-id", "turnId": "turn-active"})

    def test_codex_liveness_fallback_to_spawned_sessions(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "_resolve_codex_rollout_path", return_value=None), \
             mock.patch.object(self.server, "_spawn_registry_has_session", return_value=False), \
             mock.patch.object(self.server, "_live_spawn_registry_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session") as mock_find_spawn, \
             mock.patch.object(self.server, "_process_tty", return_value=None), \
             mock.patch.object(self.server, "_proc_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "_proc_ancestor_terminal", return_value=(None, None)):

            mock_find_spawn.return_value = {
                "pid": 12345,
                "engine": "codex",
                "cwd": "/tmp",
            }

            res = self.server.session_live_status("some-codex-session-id", "/tmp")
            self.assertTrue(res["live"])
            self.assertEqual(res["pid"], 12345)
            mock_find_spawn.assert_called_once_with("some-codex-session-id")

    def test_live_engine_session_ids_includes_memory_spawns(self):
        fake_spawn = {
            "pid": 12345,
            "engine": "codex",
            "log": "/tmp/spawn-codex-foo.log",
            "session_id": None,
        }
        with mock.patch.object(self.server, "_spawned_sessions", [fake_spawn]), \
             mock.patch.object(self.server, "_poll_spawn_entry", return_value=None), \
             mock.patch.object(self.server, "_extract_codex_thread_id_from_log", return_value="dynamic-codex-sid"), \
             mock.patch.object(self.server, "find_live_codex_processes", return_value=[]), \
             mock.patch.object(self.server, "find_live_gemini_processes", return_value=[]), \
             mock.patch.object(self.server, "find_live_cursor_processes", return_value=[]):

            self.server._engine_live_sids_cache = {"ts": 0.0, "sids": frozenset()}
            sids = self.server._live_engine_session_ids()
            self.assertIn("dynamic-codex-sid", sids)


class TestQuestionRelay(unittest.TestCase):
    """AskUserQuestion relay: dashboard answers a blocked headless session."""

    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-question-relay-home-")
        self._prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def _write_request(self, sid, nonce="N1"):
        self.server.QUESTION_RELAY_DIR.mkdir(parents=True, exist_ok=True)
        (self.server.QUESTION_RELAY_DIR / f"{sid}.request.json").write_text(json.dumps({
            "nonce": nonce,
            "session_id": sid,
            "questions": [{
                "header": "Color",
                "question": "Pick a color",
                "multiSelect": False,
                "options": [{"label": "Red", "description": ""},
                            {"label": "Blue", "description": ""}],
            }],
        }))

    def test_relay_env_opts_in(self):
        env = self.server._question_relay_env()
        self.assertEqual(env.get(self.server.QUESTION_RELAY_ENV), "1")

    def test_read_request_none_when_absent(self):
        self.assertIsNone(self.server._read_question_request("missing-sid"))

    def test_answer_roundtrip_indexed(self):
        sid = "relay-sid-1"
        self._write_request(sid, nonce="abc")
        req = self.server._read_question_request(sid)
        self.assertEqual(req["nonce"], "abc")

        result = self.server._write_question_answer(sid, [{"index": 1, "text": ""}])
        self.assertTrue(result["ok"])
        ans = json.loads(
            (self.server.QUESTION_RELAY_DIR / f"{sid}.answer.json").read_text()
        )
        self.assertEqual(ans["nonce"], "abc")
        self.assertEqual(ans["answers"], [{"index": 1, "text": ""}])

    def test_answer_without_pending_question_fails(self):
        result = self.server._write_question_answer("no-such-sid", [{"index": 0}])
        self.assertFalse(result["ok"])
        self.assertIn("no pending", result["error"])

    def test_answer_rejects_empty_list(self):
        sid = "relay-sid-2"
        self._write_request(sid)
        self.assertFalse(self.server._write_question_answer(sid, [])["ok"])

    def test_hook_process_not_counted_as_active_tool_child(self):
        # Regression: the blocking PreToolUse hook is a child process of the
        # spawn; if treated as a running "Bash" tool it clobbers the
        # AskUserQuestion sidecar and suppresses the answer modal.
        self.assertTrue(self.server._is_ccc_hook_command(
            "python3 /Users/x/.claude/command-center/hooks/pre-tool-use.py"))
        self.assertFalse(self.server._is_ccc_hook_command("bash -c 'npm test'"))

    def test_hook_installer_migrates_python_hooks_to_absolute_interpreter(self):
        """Hook commands must not depend on Claude Code's PATH containing python3."""
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            installed_hooks = home / ".claude" / "command-center" / "hooks"
            settings_path = home / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(json.dumps({
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "",
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {installed_hooks / 'pre-tool-use.py'}",
                        }],
                    }],
                    "PostToolUse": [{
                        "matcher": "",
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {installed_hooks / 'post-tool-use.py'}",
                        }],
                    }],
                    "Notification": [{
                        "matcher": "",
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {installed_hooks / 'notification.py'}",
                        }],
                    }],
                    "Stop": [{
                        "matcher": "",
                        "hooks": [{
                            "type": "command",
                            "command": f"python3 {installed_hooks / 'stop.py'}",
                        }],
                    }],
                },
            }))

            with mock.patch.object(self.server.Path, "home", return_value=home), \
                 mock.patch.object(self.server, "HOOK_SCRIPTS_DIR", installed_hooks), \
                 mock.patch.object(self.server.sys, "executable", "/opt/ccc-test/python3"):
                self.server.ensure_hooks_installed()

            settings = json.loads(settings_path.read_text())
            commands = [
                h["command"]
                for entries in settings["hooks"].values()
                for entry in entries
                for h in entry.get("hooks", [])
                if "command-center/hooks/" in h.get("command", "")
            ]
            self.assertEqual(len(commands), 4)
            expected_python = "/usr/bin/python3" if self.server.sys.platform == "darwin" else "/opt/ccc-test/python3"
            for command in commands:
                self.assertTrue(command.startswith(expected_python + " "), command)
                self.assertNotIn("python3 ", command[:8])


class TestQuestionRelayHook(unittest.TestCase):
    """The PreToolUse hook's answer-rendering logic (hooks/pre-tool-use.py)."""

    def setUp(self):
        import importlib.util
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "ccc_pre_tool_use_hook", str(repo_root / "hooks" / "pre-tool-use.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.hook = mod

    def test_full_questions_preserves_all(self):
        qs = self.hook.full_questions({"questions": [
            {"header": "A", "question": "q1", "options": [{"label": "x"}]},
            {"header": "B", "question": "q2", "options": [{"label": "y"}, {"label": "z"}]},
        ]})
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[1]["options"][1]["label"], "z")

    def test_build_reason_maps_index_to_label(self):
        questions = [{"question": "Pick a color",
                      "options": [{"label": "Red"}, {"label": "Blue"}]}]
        reason = self.hook.build_answer_reason(questions, [{"index": 1, "text": ""}])
        self.assertIn('"Pick a color" = "Blue"', reason)
        self.assertIn("do not ask again", reason)

    def test_build_reason_uses_free_text_when_no_index(self):
        questions = [{"question": "Pick a color",
                      "options": [{"label": "Red"}, {"label": "Blue"}]}]
        reason = self.hook.build_answer_reason(questions, [{"index": -1, "text": "Teal"}])
        self.assertIn('"Pick a color" = "Teal"', reason)

    def test_build_reason_multi_question_plural(self):
        questions = [
            {"question": "q1", "options": [{"label": "a"}]},
            {"question": "q2", "options": [{"label": "b"}]},
        ]
        reason = self.hook.build_answer_reason(
            questions, [{"index": 0, "text": ""}, {"index": 0, "text": ""}]
        )
        self.assertIn("answered the questions", reason)
        self.assertIn("these answers", reason)


class TestCodexStateWiring(unittest.TestCase):
    def test_server_exposes_codex_state_helpers(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        for name in ("_codex_row_state", "_codex_state_fields", "_codex_pool_alive"):
            self.assertTrue(hasattr(server, name), name)

    def test_static_renders_codex_state(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        js = (root / "static" / "app.js").read_text()
        css = (root / "static" / "app.css").read_text()
        self.assertIn("codex_state", js)
        self.assertIn("'codex_state', 'codex_fresh', 'codex_state_reason',", js)
        self.assertIn("row.codex_state = data.codex_state || null;", js)
        self.assertIn("codex_state: data.codex_state || null,", js)
        self.assertIn("const _codexStateWorking = isCodexRow && c.codex_state === 'working';", js)
        self.assertIn("|| _codexStateWorking", js)
        self.assertIn("const codexStateWorking = liveStatusMatchesOpenConv() && liveStatus.codexState === 'working';", js)
        self.assertIn("if (!liveStatus.live && !codexStateWorking)", js)
        self.assertIn("updateCodexStateBadge", js)
        self.assertIn("const wakeFeedback = badge.querySelector('.ccs-wake') || badge;", js)
        self.assertIn("wakeCodexSession(sid, wakeFeedback);", js)
        self.assertIn("flow-chip.offline", css)
        self.assertIn("conv-codex-state", css)

    def test_outcome_banner_offers_codex_wakeup(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        js = (root / "static" / "app.js").read_text()
        css = (root / "static" / "app.css").read_text()

        self.assertIn("async function wakeCodexSession(sessionId, feedbackEl, opts)", js)
        self.assertIn("renderConvWakeOutcome(opts.view, data);", js)
        self.assertIn("data-role=\"outcome-wake-codex\"", js)
        self.assertIn("wakeCodexSession(sid, wakeBtn, { view });", js)
        self.assertIn(".conv-outcome-banner .cob-wake-btn", css)


class TestSpawnReturnAddress(unittest.TestCase):
    """The 'return address' lets a spawned session report back to its
    dispatcher on completion. See /api/sessions/spawn report_to field."""

    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_normalize_accepts_canonical_and_aliases(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        for key in ("report_to", "return_to", "reply_to"):
            val, err = self.server._normalize_return_address({key: sid})
            self.assertIsNone(err, key)
            self.assertEqual(val, sid, key)

    def test_normalize_none_when_absent(self):
        self.assertEqual(self.server._normalize_return_address({}), (None, None))

    def test_normalize_rejects_shell_metachars(self):
        val, err = self.server._normalize_return_address({"report_to": "x; rm -rf /"})
        self.assertIsNone(val)
        self.assertTrue(err)

    def test_normalize_rejects_too_short(self):
        val, err = self.server._normalize_return_address({"report_to": "abc"})
        self.assertIsNone(val)
        self.assertTrue(err)

    def test_spawn_parent_defaults_to_return_address(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        val, err = self.server._normalize_spawn_parent_session_id({}, report_to=sid)
        self.assertIsNone(err)
        self.assertEqual(val, sid)

    def test_spawn_parent_prefers_explicit_parent(self):
        parent = "parent-session-id"
        report_to = "report-session-id"
        val, err = self.server._normalize_spawn_parent_session_id(
            {"parent_session_id": parent},
            report_to=report_to,
        )
        self.assertIsNone(err)
        self.assertEqual(val, parent)

    def test_spawn_parent_rejects_shell_metachars(self):
        val, err = self.server._normalize_spawn_parent_session_id(
            {"parent_session_id": "x; rm -rf /"}
        )
        self.assertIsNone(val)
        self.assertTrue(err)

    def test_spawn_parent_extracts_legacy_return_address_footer(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        text = self.server._wrap_prompt_with_return_address("do x", sid, port=8090)
        self.assertEqual(
            self.server._parent_session_id_from_return_address_text(text),
            sid,
        )

    def test_wrap_is_noop_without_address(self):
        self.assertEqual(
            self.server._wrap_prompt_with_return_address("do x", None), "do x"
        )

    def test_wrap_embeds_address_and_inject_api(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        out = self.server._wrap_prompt_with_return_address("do x", sid, port=8090)
        self.assertIn("do x", out)
        self.assertIn(sid, out)
        self.assertIn("/api/inject-input", out)
        self.assertIn('"announced_from": "<your session name or id>"', out)
        self.assertIn("STATUS", out)


class TestObjectsStore(unittest.TestCase):
    """Durable Flow-object persistence (GOAL-3/4). Drives objects_store
    directly with a tmpdir-backed file via the CCC_OBJECTS_FILE override, so
    no HTTP server or external system is involved."""

    def setUp(self):
        import importlib
        self.tmpdir = tempfile.mkdtemp(prefix="ccc-objects-")
        self.objfile = os.path.join(self.tmpdir, "objects.json")
        self._prev = os.environ.get("CCC_OBJECTS_FILE")
        os.environ["CCC_OBJECTS_FILE"] = self.objfile
        # Fresh import each test so the in-process (mtime,size) cache is clean.
        sys.modules.pop("objects_store", None)
        self.store = importlib.import_module("objects_store")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CCC_OBJECTS_FILE", None)
        else:
            os.environ["CCC_OBJECTS_FILE"] = self._prev
        sys.modules.pop("objects_store", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_when_no_file(self):
        self.assertFalse(os.path.exists(self.objfile))
        self.assertEqual(
            self.store.load_state(),
            {"objects": [], "parents": {}, "order": {}, "drafts": []},
        )

    def test_create_persists_and_round_trips(self):
        obj = self.store.create_object("Ship billing")
        self.assertTrue(obj["id"])
        self.assertEqual(obj["title"], "Ship billing")
        self.assertIn("created_at", obj)
        self.assertIn("updated_at", obj)
        self.assertTrue(os.path.exists(self.objfile))
        # File on disk is valid JSON in the schema shape.
        on_disk = json.load(open(self.objfile))
        self.assertEqual(len(on_disk["objects"]), 1)
        self.assertEqual(on_disk["objects"][0]["id"], obj["id"])

    def test_create_with_explicit_id_and_optional_fields(self):
        obj = self.store.create_object(
            "Admin", id="obj-1", status="todo", objective="file taxes"
        )
        self.assertEqual(obj["id"], "obj-1")
        self.assertEqual(obj["status"], "todo")
        self.assertEqual(obj["objective"], "file taxes")

    def test_create_same_id_is_upsert_not_duplicate(self):
        self.store.create_object("First", id="obj-1")
        self.store.create_object("Second", id="obj-1", status="wip")
        objs = self.store.load_state()["objects"]
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["title"], "Second")
        self.assertEqual(objs[0]["status"], "wip")

    def test_update_patches_only_supplied_fields(self):
        self.store.create_object("Orig", id="obj-1", status="wip", objective="x")
        updated = self.store.update_object("obj-1", title="Renamed")
        self.assertEqual(updated["title"], "Renamed")
        # status/objective owned by a sibling session must survive a rename.
        self.assertEqual(updated["status"], "wip")
        self.assertEqual(updated["objective"], "x")

    def test_update_missing_returns_none(self):
        self.assertIsNone(self.store.update_object("nope", title="x"))

    def test_assign_and_unassign(self):
        self.store.create_object("Obj", id="obj-1")
        self.store.assign_session("session:abc", "obj-1")
        parents = self.store.load_state()["parents"]
        self.assertEqual(parents["session:abc"], "object:obj-1")
        self.store.unassign_session("session:abc")
        self.assertNotIn("session:abc", self.store.load_state()["parents"])

    def test_delete_removes_object_and_its_parent_links(self):
        self.store.create_object("Obj", id="obj-1")
        self.store.assign_session("session:abc", "obj-1")
        self.store.assign_session("session:def", "obj-1")
        self.assertTrue(self.store.delete_object("obj-1"))
        state = self.store.load_state()
        self.assertEqual(state["objects"], [])
        # Links that pointed at the deleted object are gone too.
        self.assertNotIn("session:abc", state["parents"])
        self.assertNotIn("session:def", state["parents"])

    def test_delete_missing_returns_false(self):
        self.assertFalse(self.store.delete_object("nope"))

    def test_import_merge_upserts_and_does_not_wipe(self):
        # Server already holds one object + a parent link.
        self.store.create_object("Server obj", id="srv-1")
        self.store.assign_session("session:keep", "srv-1")
        # Browser imports a different object and a new link.
        merged = self.store.import_state(
            objects=[{"id": "brow-1", "title": "Browser obj"}],
            parents={"session:new": "object:brow-1"},
            order={"object:brow-1": 5},
        )
        ids = {o["id"] for o in merged["objects"]}
        # Both survive — import is additive, never destructive.
        self.assertEqual(ids, {"srv-1", "brow-1"})
        self.assertEqual(merged["parents"]["session:keep"], "object:srv-1")
        self.assertEqual(merged["parents"]["session:new"], "object:brow-1")
        self.assertEqual(merged["order"]["object:brow-1"], 5)

    def test_import_merge_updates_existing_object_fields(self):
        self.store.create_object("Old title", id="obj-1")
        self.store.import_state(
            objects=[{"id": "obj-1", "title": "New title", "status": "done"}]
        )
        objs = self.store.load_state()["objects"]
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["title"], "New title")
        self.assertEqual(objs[0]["status"], "done")

    def test_malformed_file_degrades_gracefully(self):
        with open(self.objfile, "w") as f:
            f.write("{ this is not valid json")
        # Read does not throw; returns empty state.
        self.assertEqual(
            self.store.load_state(),
            {"objects": [], "parents": {}, "order": {}, "drafts": []},
        )
        # And a subsequent write still works (overwrites the garbage).
        obj = self.store.create_object("Recover", id="obj-1")
        self.assertEqual(obj["id"], "obj-1")
        self.assertEqual(len(self.store.load_state()["objects"]), 1)

    def test_non_dict_file_degrades_gracefully(self):
        with open(self.objfile, "w") as f:
            json.dump([1, 2, 3], f)  # valid JSON, wrong shape
        self.assertEqual(
            self.store.load_state(),
            {"objects": [], "parents": {}, "order": {}, "drafts": []},
        )

    # --- drafts (lightweight not-yet-started tasks) ---------------------------
    def test_draft_upsert_persists_and_round_trips(self):
        draft = self.store.upsert_draft(
            {
                "id": "draft-1",
                "title": "Draft release notes",
                "repo_path": "/tmp/repo",
                "parent_node_id": "object:obj-1",
                "prompt": "write the notes",
            }
        )
        self.assertEqual(draft["id"], "draft-1")
        self.assertEqual(draft["title"], "Draft release notes")
        self.assertEqual(draft["repo_path"], "/tmp/repo")
        self.assertEqual(draft["parent_node_id"], "object:obj-1")
        self.assertEqual(draft["prompt"], "write the notes")
        self.assertIn("created_at", draft)
        self.assertIn("updated_at", draft)
        # Round-trips through the JSON file and load_state.
        drafts = self.store.load_state()["drafts"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["id"], "draft-1")
        on_disk = json.load(open(self.objfile))
        self.assertEqual(on_disk["drafts"][0]["id"], "draft-1")

    def test_draft_repo_path_may_be_empty_reminder(self):
        draft = self.store.upsert_draft({"id": "draft-r", "title": "Call vendor"})
        self.assertEqual(draft["repo_path"], "")
        self.assertNotIn("prompt", draft)

    def test_draft_upsert_same_id_is_not_duplicate(self):
        self.store.upsert_draft({"id": "draft-1", "title": "First"})
        self.store.upsert_draft({"id": "draft-1", "title": "Second"})
        drafts = self.store.load_state()["drafts"]
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["title"], "Second")

    def test_draft_upsert_without_id_returns_none(self):
        self.assertIsNone(self.store.upsert_draft({"title": "no id"}))

    def test_draft_delete_removes_one(self):
        self.store.upsert_draft({"id": "draft-1", "title": "Keep then drop"})
        self.store.upsert_draft({"id": "draft-2", "title": "Survivor"})
        self.assertTrue(self.store.delete_draft("draft-1"))
        ids = [d["id"] for d in self.store.load_state()["drafts"]]
        self.assertEqual(ids, ["draft-2"])

    def test_draft_delete_missing_returns_false(self):
        self.assertFalse(self.store.delete_draft("nope"))

    def test_import_merges_drafts_additively(self):
        # Server already holds one draft; import brings another + updates one.
        self.store.upsert_draft({"id": "srv-d", "title": "Server draft"})
        merged = self.store.import_state(
            drafts=[
                {"id": "srv-d", "title": "Renamed by browser"},
                {"id": "brow-d", "title": "Browser draft", "repo_path": "/x"},
            ]
        )
        by_id = {d["id"]: d for d in merged["drafts"]}
        # Both survive — import is additive, never destructive.
        self.assertEqual(set(by_id), {"srv-d", "brow-d"})
        self.assertEqual(by_id["srv-d"]["title"], "Renamed by browser")
        self.assertEqual(by_id["brow-d"]["repo_path"], "/x")

    def test_import_draft_parent_node_id_seeds_parent_map(self):
        merged = self.store.import_state(
            drafts=[
                {"id": "draft-1", "title": "Call vendor", "parent_node_id": "object:obj-1"},
            ]
        )

        self.assertEqual(merged["parents"]["draft-session:draft-1"], "object:obj-1")

    def test_import_drafts_does_not_disturb_objects(self):
        self.store.create_object("Obj", id="obj-1")
        merged = self.store.import_state(drafts=[{"id": "d1", "title": "T"}])
        self.assertEqual([o["id"] for o in merged["objects"]], ["obj-1"])
        self.assertEqual([d["id"] for d in merged["drafts"]], ["d1"])

    def test_drafts_round_trip_via_file_with_objects(self):
        # Drafts coexist with objects/parents/order in one durable file.
        self.store.create_object("Obj", id="obj-1")
        self.store.assign_session("session:abc", "obj-1")
        self.store.upsert_draft(
            {"id": "d1", "title": "Task", "parent_node_id": "object:obj-1"}
        )
        state = self.store.load_state()
        self.assertEqual(len(state["objects"]), 1)
        self.assertEqual(state["parents"]["session:abc"], "object:obj-1")
        self.assertEqual(state["drafts"][0]["parent_node_id"], "object:obj-1")


class TestWTQueueIntegration(unittest.TestCase):
    """WT-26: verify watchtower.queue interop when WT is installed."""

    def test_wt_queue_importable(self):
        """If WT is installed, its queue module must be importable and expose answer()."""
        try:
            import watchtower.queue as wq
            self.assertTrue(
                callable(getattr(wq, "answer", None)),
                "watchtower.queue must expose answer()",
            )
        except ImportError:
            self.skipTest("watchtower not installed — Phase 0 not yet applied")

    def test_wt_queue_shim_resolves(self):
        """The _queue_answer shim in server.py must be callable regardless of
        whether WT is installed (it falls back to ux_fixes_queue.answer)."""
        import server
        self.assertTrue(
            callable(getattr(server, "_queue_answer", None)),
            "server._queue_answer must be callable after WT-26 Phase 1 shim",
        )

    def test_wt_availability_flag_is_bool(self):
        """_WT_QUEUE_AVAILABLE must be a bool so callers can branch on it."""
        import server
        self.assertIsInstance(
            getattr(server, "_WT_QUEUE_AVAILABLE", None),
            bool,
            "server._WT_QUEUE_AVAILABLE must be a bool",
        )


class TestWTMessagingBackendStage2(unittest.TestCase):
    """WT-56: CCC_MESSAGING_BACKEND=wt stage-2 handover — flag/availability
    gating plus the pure wt-ask JSON -> CCC result mapping. Delivery itself
    (subprocess calls to `wt send` / `wt ask`) is intentionally not exercised
    here; these are the parts that can be tested without shelling out."""

    def setUp(self):
        import server
        self.server = server
        # Reset the cached availability check between tests so one test's
        # monkeypatch of shutil.which doesn't leak into the next.
        server._WT_CLI_PATH_CACHE = None
        self.addCleanup(setattr, server, "_WT_CLI_PATH_CACHE", None)

    def test_messaging_disabled_by_default(self):
        """Flag unset -> _wt_messaging_enabled() is False (byte-identical
        default behaviour requirement)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCC_MESSAGING_BACKEND", None)
            self.assertFalse(self.server._wt_messaging_enabled())

    def test_messaging_enabled_requires_exact_value(self):
        """Only CCC_MESSAGING_BACKEND=wt (case/whitespace-insensitive) opts in;
        any other value leaves the flag off."""
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}):
            self.assertTrue(self.server._wt_messaging_enabled())
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": " WT "}):
            self.assertTrue(self.server._wt_messaging_enabled())
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "true"}):
            self.assertFalse(self.server._wt_messaging_enabled())

    def test_wt_cli_unavailable_reports_disabled(self):
        """Flag on but `wt` missing from PATH -> _wt_cli_available() is False,
        so the hooks fall through to the native path."""
        with mock.patch("shutil.which", return_value=None):
            self.server._WT_CLI_PATH_CACHE = None
            self.assertFalse(self.server._wt_cli_available())

    def test_wt_cli_available_and_cached(self):
        """When `wt` is found, availability is True and the lookup is cached
        (shutil.which only called once across repeated checks)."""
        with mock.patch("shutil.which", return_value="/usr/local/bin/wt") as which:
            self.server._WT_CLI_PATH_CACHE = None
            self.assertTrue(self.server._wt_cli_available())
            self.assertTrue(self.server._wt_cli_available())
            which.assert_called_once()

    def test_wt_ask_mapping_success(self):
        """A successful wt-ask payload maps answer -> CCC's `text` field with
        source tagged wt-ask."""
        result = self.server._map_wt_ask_json_to_ccc_result(
            {"ok": True, "answer": "42", "source": "resume"}
        )
        self.assertEqual(result, {
            "ok": True,
            "text": "42",
            "cost_usd": None,
            "duration_ms": None,
            "num_turns": None,
            "source": "wt-ask",
        })

    def test_wt_ask_mapping_timeout_keeps_partial(self):
        """A wt-mediated timeout is a real (parseable) answer, not a
        transport failure — it must map through with partial text intact,
        not fall through to a native retry that would double-deliver."""
        result = self.server._map_wt_ask_json_to_ccc_result(
            {"ok": False, "error": "timeout", "partial": "still thinking", "source": "resume"}
        )
        self.assertEqual(result, {
            "ok": False,
            "error": "timeout",
            "partial": "still thinking",
            "source": "wt-ask",
        })

    def test_wt_ask_mapping_failure_without_partial(self):
        result = self.server._map_wt_ask_json_to_ccc_result(
            {"ok": False, "error": "no such session"}
        )
        self.assertEqual(result, {
            "ok": False,
            "error": "no such session",
            "source": "wt-ask",
        })

    def test_wt_ask_mapping_rejects_non_dict(self):
        """Unparseable/unexpected shapes return None so the caller falls
        through to the native resume path instead of trusting garbage."""
        self.assertIsNone(self.server._map_wt_ask_json_to_ccc_result(None))
        self.assertIsNone(self.server._map_wt_ask_json_to_ccc_result("not json"))
        self.assertIsNone(self.server._map_wt_ask_json_to_ccc_result([1, 2, 3]))

    def test_send_hook_noop_when_flag_off(self):
        """_try_wt_send_for_headless_delivery must return None (native
        fall-through) with the flag off, without even checking wt
        availability — verifies via a subprocess spy that it never shells out."""
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("subprocess.run") as run:
            os.environ.pop("CCC_MESSAGING_BACKEND", None)
            result = self.server._try_wt_send_for_headless_delivery("sid-123", "hi")
            self.assertIsNone(result)
            run.assert_not_called()

    def test_send_hook_uses_no_queue_and_disables_delegate(self):
        """wt send must run with --no-queue (a parked message exits 0 and CCC
        would falsely report "delivered") and with WATCHTOWER_DELEGATE_URL=off
        (CCC is wt's delegate; without it a failed resume recurses CCC->wt->CCC).
        A nonzero rc falls through (None) to the native resume path."""
        done = mock.Mock()
        done.returncode = 1
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}), \
             mock.patch("shutil.which", return_value="/usr/local/bin/wt"), \
             mock.patch.object(self.server.subprocess, "run", return_value=done) as run:
            self.server._WT_CLI_PATH_CACHE = None
            result = self.server._try_wt_send_for_headless_delivery("sid-123", "hi")
        self.assertIsNone(result)
        argv = run.call_args[0][0]
        self.assertIn("--no-queue", argv)
        env = run.call_args[1].get("env") or {}
        self.assertEqual(env.get("WATCHTOWER_DELEGATE_URL"), "off")

    def test_send_hook_requires_parseable_ok_json_for_delivered(self):
        """A zero exit from `wt send --json` is not enough to call delivery
        confirmed. CCC needs parseable JSON with ok:true; otherwise it falls
        through to its native path instead of showing a delivered WT state."""
        done = mock.Mock(returncode=0, stdout="not json")
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}), \
             mock.patch("shutil.which", return_value="/usr/local/bin/wt"), \
             mock.patch.object(self.server.subprocess, "run", return_value=done):
            self.server._WT_CLI_PATH_CACHE = None
            result = self.server._try_wt_send_for_headless_delivery("sid-123", "hi")
        self.assertIsNone(result)

    def test_send_hook_surfaces_wt_queued_json_as_queued_not_delivered(self):
        """If WT ever reports queued, CCC must surface queued/outbox state and
        must not convert that into via=wt-send delivered feedback."""
        done = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": False,
                "queued": True,
                "id": "msg-123",
                "error": "session busy",
            }),
        )
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}), \
             mock.patch("shutil.which", return_value="/usr/local/bin/wt"), \
             mock.patch.object(self.server.subprocess, "run", return_value=done):
            self.server._WT_CLI_PATH_CACHE = None
            result = self.server._try_wt_send_for_headless_delivery("sid-123", "hi")
        self.assertEqual(result["via"], "wt-send-queued")
        self.assertTrue(result["queued"])
        self.assertEqual(result["id"], "msg-123")
        self.assertIn("session busy", result["queued_reason"])

    def test_send_hook_maps_ok_json_to_wt_send_with_receipt(self):
        done = mock.Mock(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "transport": "resume",
                "receipt_id": "rcpt-123",
            }),
        )
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}), \
             mock.patch("shutil.which", return_value="/usr/local/bin/wt"), \
             mock.patch.object(self.server.subprocess, "run", return_value=done):
            self.server._WT_CLI_PATH_CACHE = None
            result = self.server._try_wt_send_for_headless_delivery("sid-123", "hi")
        self.assertEqual(result["via"], "wt-send")
        self.assertEqual(result["transport"], "resume")
        self.assertEqual(result["receipt_id"], "rcpt-123")

    def test_ask_hook_noop_when_wt_missing(self):
        """Flag on but wt not on PATH -> _try_wt_ask_for_headless_delivery
        returns None without shelling out."""
        with mock.patch.dict(os.environ, {"CCC_MESSAGING_BACKEND": "wt"}), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("subprocess.run") as run:
            self.server._WT_CLI_PATH_CACHE = None
            result = self.server._try_wt_ask_for_headless_delivery("sid-123", "hi", 5000)
            self.assertIsNone(result)
            run.assert_not_called()


class TestTerminalQueueDrainSafety(unittest.TestCase):
    """CCC-455: the terminal-queue drain must never silently consume an
    entry. A wt-send handoff is provisional until its receipt verifies
    `landed`; a verified `lost` re-queues the text (front) and forces the
    retry to bypass wt."""

    SID = "00000000-0000-4000-8000-00000000c455"

    def setUp(self):
        import server
        self.server = server
        self._cleanup_state()
        self.addCleanup(self._cleanup_state)

    def _cleanup_state(self):
        self.server._terminal_drain_receipts.clear()
        self.server._terminal_drain_skip_wt.discard(self.SID)
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.pop(self.SID, None)
        self.server._pending_terminal_retry_after.pop(self.SID, None)

    def _receipt_item(self, **over):
        item = {
            "sid": self.SID,
            "text": "hello from the queue",
            "receipt_id": "rcpt-feedc0dec455",
            "deadline": time.time() + 600,
            "last_check": 0.0,
        }
        item.update(over)
        return item

    def _run_verify(self, wt_stdout, returncode=0):
        self.server._terminal_drain_receipts.append(self._receipt_item())
        fake = mock.Mock(returncode=returncode, stdout=wt_stdout)
        with mock.patch.object(self.server.subprocess, "run", return_value=fake), \
             mock.patch.object(self.server, "_save_pending_inputs"):
            self.server._verify_terminal_drain_receipts()

    def test_lost_receipt_requeues_front_and_skips_wt(self):
        self._run_verify(json.dumps({"status": "lost"}))
        self.assertEqual(self.server._terminal_drain_receipts, [])
        self.assertIn(self.SID, self.server._terminal_drain_skip_wt)
        with self.server._pending_terminal_input_lock:
            self.assertEqual(
                self.server._pending_terminal_input_queue.get(self.SID),
                ["hello from the queue"],
            )

    def test_landed_receipt_is_consumed(self):
        self._run_verify(json.dumps({"status": "landed"}))
        self.assertEqual(self.server._terminal_drain_receipts, [])
        self.assertNotIn(self.SID, self.server._terminal_drain_skip_wt)
        with self.server._pending_terminal_input_lock:
            self.assertNotIn(self.SID, self.server._pending_terminal_input_queue)

    def test_pending_receipt_keeps_tracking_until_deadline(self):
        self._run_verify(json.dumps({"status": "pending"}))
        self.assertEqual(len(self.server._terminal_drain_receipts), 1)
        # Past the deadline it is dropped WITHOUT re-sending (double-delivery
        # guard) — the loud log is the trail.
        self.server._terminal_drain_receipts[0]["deadline"] = time.time() - 1
        self.server._terminal_drain_receipts[0]["last_check"] = 0.0
        fake = mock.Mock(returncode=0, stdout=json.dumps({"status": "pending"}))
        with mock.patch.object(self.server.subprocess, "run", return_value=fake), \
             mock.patch.object(self.server, "_save_pending_inputs"):
            self.server._verify_terminal_drain_receipts()
        self.assertEqual(self.server._terminal_drain_receipts, [])
        with self.server._pending_terminal_input_lock:
            self.assertNotIn(self.SID, self.server._pending_terminal_input_queue)

    def test_drain_loop_requeues_on_failed_delivery(self):
        """Source pin: the watcher checks the inject result and re-queues at
        the front on failure instead of fire-and-forget."""
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("_requeue_terminal_input_front(sid, text)", server_py)
        self.assertIn('skip_wt=(sid in _terminal_drain_skip_wt)', server_py)
        self.assertIn("_verify_terminal_drain_receipts()", server_py)


def test_inject_input_honors_wt_origin_marker():
    """WT-78: a delegate POST from wt carries origin=wt; the inject route must
    thread that into _inject_text_into_session and skip the wt-send hook there,
    or a failed delivery recurses CCC -> wt -> CCC."""
    server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
    assert 'wt_origin=(str(payload.get("origin") or "").lower() == "wt")' in server_py
    assert "if not wt_origin and not skip_wt:" in server_py


def test_wt_receipt_route_and_staged_send_feedback():
    """CCC-452: WT-routed sends surface their pipeline stage. The server
    parses `wt send --json` (transport/receipt_id), proxies `wt receipts get`
    at /api/wt/receipt/<id>, and honors skip_wt for the client's receipt-lost
    native fallback; the client stages the pending echo and polls."""
    server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
    assert '"--no-queue", "--json"' in server_py
    assert 'elif path.startswith("/api/wt/receipt/"):' in server_py
    assert '["wt", "receipts", "get", rid]' in server_py
    assert 'skip_wt=bool(payload.get("skip_wt"))' in server_py
    app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
    assert "function beginWtReceiptTracking(" in app_js
    assert "'/api/wt/receipt/'" in app_js
    assert "skip_wt: true" in app_js
    assert "data.via === 'wt-send'" in app_js


def test_throughput_initial_route_is_registered():
    server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
    assert 'elif path == "/api/throughput/initial":' in server_py
    assert "_throughput_initial_payload(" in server_py


def test_throughput_refresh_routes_are_registered():
    server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
    assert 'elif path == "/api/throughput/refresh/start":' in server_py
    assert 'elif path == "/api/throughput/refresh/status":' in server_py
    assert "_throughput_refresh_start(" in server_py
    assert "_throughput_refresh_status(" in server_py


if __name__ == "__main__":
    unittest.main()
