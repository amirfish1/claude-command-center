from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from unittest import mock

from tests.test_fleet_failover import _FleetBase
from ccc_server import limit_events


class FleetDetectionTests(_FleetBase):
    def _file(self, name, events):
        path = Path(self.tmp_dir) / name
        path.write_text("\n".join(json.dumps(ev) for ev in events) + "\n")
        return path

    def _claude(self, text="Weekly usage limit reached"):
        return {"type": "result", "is_error": True, "result": text,
                "timestamp": datetime.now(timezone.utc).isoformat()}

    def test_claude_weekly_exhaustion_uses_weekly_reset(self):
        now = time.time()
        path = self._file("weekly.jsonl", [self._claude()])
        with mock.patch.object(self.server, "_live_weekly_usage", return_value={
            "session_pct": 20, "session_resets_at": now + 3600,
            "weekly_pct": 100, "weekly_resets_at": now + 6 * 86400,
        }):
            found = self.server._detect_claude_usage_limit_stop("weekly", path)
        self.assertEqual(found["resume_at"], now + 6 * 86400)
        self.assertEqual(found["limit_window"], "weekly")
        self.assertFalse(found["resume_at_estimated"])

    def test_both_windows_exhausted_waits_for_later_reset(self):
        now = time.time()
        path = self._file("both.jsonl", [self._claude("Usage limit reached")])
        with mock.patch.object(self.server, "_live_weekly_usage", return_value={
            "session_pct": 100, "session_resets_at": now + 7200,
            "weekly_pct": 100, "weekly_resets_at": now + 86400,
        }):
            found = self.server._detect_claude_usage_limit_stop("both", path)
        self.assertEqual(found["resume_at"], now + 86400)

    def test_claude_synthetic_assistant_error_and_success(self):
        error = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                 "message": {"content": [{"type": "text", "text": "Usage limit reached"}]}}
        path = self._file("assistant.jsonl", [error])
        with mock.patch.object(self.server, "_live_weekly_usage", return_value={}):
            self.assertIsNotNone(self.server._detect_claude_usage_limit_stop("a", path))
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": "Done"}}) + "\n")
        self.assertIsNone(self.server._detect_claude_usage_limit_stop("a", path))

    def test_transient_system_error_and_quoted_limit_do_not_stop_session(self):
        path = self._file("transient.jsonl", [
            {"type": "system", "subtype": "api_error", "error": "429 rate limit"},
            {"type": "user", "message": {"content": "Explain usage limit reached"}},
            {"type": "tool_result", "content": "usage limit reached"},
        ])
        self.assertIsNone(self.server._detect_claude_usage_limit_stop("a", path))

    def test_weekly_unknown_reset_is_estimated_seven_days(self):
        path = self._file("unknown.jsonl", [self._claude()])
        with mock.patch.object(self.server, "_live_weekly_usage", return_value={}):
            found = self.server._detect_claude_usage_limit_stop("a", path)
        self.assertTrue(found["resume_at_estimated"])
        self.assertEqual(found["resume_at"] - found["detected_at"], 7 * 86400)

    def test_codex_weekly_secondary_wins_and_later_success_clears(self):
        now = time.time()
        path = self._file("codex.jsonl", [
            {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
                "primary": {"used_percent": 10, "resets_at": now + 3600},
                "secondary": {"used_percent": 100, "resets_at": now + 5 * 86400},
            }}},
            {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": None,
                "error": {"message": "Usage limit exceeded", "codex_error_info": "usage_limit_exceeded"}}},
        ])
        found = self.server._detect_codex_usage_limit_stop("c", path)
        self.assertEqual(found["resume_at"], now + 5 * 86400)
        self.assertEqual(found["limit_window"], "weekly")
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "last_agent_message": "Done"}}) + "\n")
        self.assertIsNone(self.server._detect_codex_usage_limit_stop("c", path))

    def test_codex_headless_flat_failed_turn(self):
        path = self._file("codex.log", [{"type": "turn.failed", "error": {
            "message": "Weekly usage limit exceeded"}}])
        found = self.server._detect_codex_usage_limit_stop("c", path)
        self.assertEqual(found["limit_window"], "weekly")
        self.assertTrue(found["resume_at_estimated"])

    def test_codex_structured_limit_code_survives_generic_message(self):
        path = self._file("codex-structured.jsonl", [
            {"type": "event_msg", "payload": {"type": "task_complete",
                "last_agent_message": None,
                "error": {"message": "Request failed",
                          "codex_error_info": "usage_limit_exceeded"}}},
        ])
        found = self.server._detect_codex_usage_limit_stop("c", path)
        self.assertIsNotNone(found)
        self.assertEqual(found["engine"], "codex")

    def test_codex_generic_error_without_limit_shape_is_ignored(self):
        path = self._file("codex-plain.jsonl", [
            {"type": "event_msg", "payload": {"type": "task_complete",
                "last_agent_message": None,
                "error": {"message": "Request failed",
                          "codex_error_info": "stream_disconnected"}}},
        ])
        self.assertIsNone(self.server._detect_codex_usage_limit_stop("c", path))

    def test_codex_structured_stop_cleared_by_later_clean_completion(self):
        path = self._file("codex-cleared.jsonl", [
            {"type": "event_msg", "payload": {"type": "task_complete",
                "last_agent_message": None,
                "error": {"message": "Request failed",
                          "codex_error_info": "usage_limit_exceeded"}}},
            {"type": "event_msg", "payload": {"type": "task_complete",
                "last_agent_message": "All done"}},
        ])
        self.assertIsNone(self.server._detect_codex_usage_limit_stop("c", path))

    def test_devin_days_and_weeks_parse(self):
        self.assertEqual(self.server._devin_reset_epoch("reset in 2 days", 1000), (173800, False))
        self.assertEqual(self.server._devin_reset_epoch("reset in 1 week", 1000), (605800, False))

    def test_registered_headless_and_queue_worker_captures_all_engines(self):
        now = time.time()
        claude = self._file("claude.log", [self._claude("Usage limit reached")])
        codex = self._file("codex.log", [{"type": "turn.failed", "error": {"message": "Usage limit exceeded"}}])
        devin = self._file("devin.log", [{"type": "result", "subtype": "error",
            "error": "Reached free model rate limit. Your limit will reset in 2 days."}])
        records = [{"engine": engine, "session_id": sid, "log": str(path), "name": "Worker task",
                    "cwd": self.tmp_dir, "spawned_via": "watchtower"}
                   for engine, sid, path in (("claude", "worker-a", claude),
                                            ("codex", "worker-b", codex),
                                            ("devin", "devincli-worker-c", devin))]
        with mock.patch.object(self.server, "_spawned_sessions", []), \
             mock.patch.object(self.server, "_disk_spawn_entries_cached", return_value=records), \
             mock.patch.object(self.server, "_usage_limit_claude_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_codex_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_kimi_candidates", return_value=[]), \
             mock.patch.object(self.server, "_live_weekly_usage", return_value={}):
            self.server._usage_limit_scan_once(now)
        tracked = self.server._load_usage_limit_resumes()
        self.assertEqual(set(tracked), {"worker-a", "worker-b", "devincli-worker-c"})
        self.assertTrue(all(row["display_name"] == "Worker task" for row in tracked.values()))
        self.assertTrue(all("transcript_path" in row for row in tracked.values()))

    def test_capture_cache_persists_negative_and_only_reparses_changed_file(self):
        first = self._file("first.jsonl", [{"type": "result", "is_error": False}])
        second = self._file("second.jsonl", [self._claude("Usage limit reached")])
        detector = mock.Mock(side_effect=lambda sid, path: None)
        for _ in range(3):
            for path in (first, second):
                limit_events.cached_stop("claude", path.stem, path, detector)
        self.assertEqual(detector.call_count, 2)
        limit_events.flush()
        limit_events._cache_path = None
        limit_events.cached_stop("claude", first.stem, first, detector)
        self.assertEqual(detector.call_count, 2)
        with second.open("a") as fh:
            fh.write('{"type":"result","is_error":false}\n')
        limit_events.cached_stop("claude", second.stem, second, detector)
        self.assertEqual(detector.call_count, 3)

    def test_cached_stop_replaces_raw_snippet_and_persists_safe_text(self):
        raw = "synthetic provider blob 9f27 keep out"
        path = self._file("privacy.jsonl", [{"type": "result", "is_error": True}])
        detector = mock.Mock(return_value={
            "engine": "claude", "detected_at": 10.0, "resume_at": 20.0,
            "source_text_snippet": raw})
        found = limit_events.cached_stop("claude", "privacy", path, detector)
        self.assertEqual(
            found["source_text_snippet"], "claude usage / rate limit reached")
        limit_events.flush()
        persisted = (
            self.server.COMMAND_CENTER_STATE_DIR / "limit-detection-cache.json"
        ).read_text()
        self.assertNotIn(raw, persisted)
        self.assertIn("claude usage / rate limit reached", persisted)

    def test_success_after_registered_stop_clears_watcher_record(self):
        path = self._file("cleared.log", [self._claude("Usage limit reached")])
        record = {"engine": "claude", "session_id": "cleared", "log": str(path), "cwd": self.tmp_dir}
        with mock.patch.object(self.server, "_spawned_sessions", [record]), \
             mock.patch.object(self.server, "_disk_spawn_entries_cached", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_claude_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_codex_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_kimi_candidates", return_value=[]), \
             mock.patch.object(self.server, "_free_failover_devin_candidates", return_value=[]), \
             mock.patch.object(self.server, "_live_weekly_usage", return_value={}):
            self.server._usage_limit_scan_once()
            self.assertIn("cleared", self.server._load_usage_limit_resumes())
            with path.open("a") as fh:
                fh.write('{"type":"result","is_error":false}\n')
            self.server._usage_limit_scan_once()
        self.assertNotIn("cleared", self.server._load_usage_limit_resumes())

    def test_sibling_store_replacements_refresh_warm_caches(self):
        self._track("old")
        self.server._load_free_failovers()
        self.assertIn("old", self.server._load_usage_limit_resumes())
        replacement = self.server.USAGE_LIMIT_RESUME_FILE.with_suffix(".worker")
        replacement.write_text(json.dumps({"new": {"engine": "claude", "resume_at": time.time() + 10000,
                                                   "detected_at": time.time()}}))
        os.replace(replacement, self.server.USAGE_LIMIT_RESUME_FILE)
        free_file = self.server.COMMAND_CENTER_STATE_DIR / "free-failover.json"
        free_file.write_text(json.dumps({"new": {"engine": "claude", "auto_resume": True,
                                                "auto_resume_detected_at": None}}))
        self.assertEqual(set(self.server._load_usage_limit_resumes()), {"new"})
        self.assertIn("new", self.server._load_free_failovers())
        self.assertEqual(self.server.free_failover_fleet()["groups"][0]["sessions"][0]["session_id"], "new")

    def test_stagger_survives_multiple_watcher_passes(self):
        now = time.time()
        for i in range(12):
            self._track(f"fleet-{i:02}", resume_in=0, now=now)
            self.server.free_failover_arm(f"fleet-{i:02}")
        inject = mock.Mock(return_value={"ok": True})
        with mock.patch.object(self.server, "_free_failover_devin_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_session_path", return_value=None), \
             mock.patch.object(self.server, "_inject_text_into_session", inject), \
             mock.patch.object(self.server, "_free_failover_marker"), \
             mock.patch.object(self.server, "_log_activity"):
            for delta, expected in ((0, 5), (45, 5), (60, 10), (105, 10), (120, 12)):
                with mock.patch("time.time", return_value=now + delta):
                    self.server._free_failover_auto_pass(now + delta)
                self.assertEqual(inject.call_count, expected)

    def test_delayed_watcher_pass_restaggers_remaining_slots(self):
        now = time.time()
        for i in range(12):
            self._track(f"late-{i:02}", resume_in=0, now=now)
            self.server.free_failover_arm(f"late-{i:02}")
        inject = mock.Mock(return_value={"ok": True})
        with mock.patch.object(self.server, "_free_failover_devin_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_session_path", return_value=None), \
             mock.patch.object(self.server, "_inject_text_into_session", inject), \
             mock.patch.object(self.server, "_free_failover_marker"), \
             mock.patch.object(self.server, "_log_activity"):
            for delta, expected in ((0, 5), (300, 10), (345, 10), (360, 12)):
                with mock.patch("time.time", return_value=now + delta):
                    self.server._free_failover_auto_pass(now + delta)
                self.assertEqual(inject.call_count, expected)

    def test_differing_resets_never_pack_five_into_one_minute(self):
        now = time.time()
        resets = {}
        for i in range(6):
            resets[f"early-{i:02}"] = now + 100
            resets[f"later-{i:02}"] = now + 120
        for sid, resume_at in resets.items():
            self._track(sid, resume_in=resume_at - now, now=now)
            self.server.free_failover_arm(sid)
        inject = mock.Mock(return_value={"ok": True})
        with mock.patch.object(self.server, "_free_failover_devin_candidates", return_value=[]), \
             mock.patch.object(self.server, "_usage_limit_session_path", return_value=None), \
             mock.patch.object(self.server, "_inject_text_into_session", inject), \
             mock.patch.object(self.server, "_free_failover_marker"), \
             mock.patch.object(self.server, "_log_activity"):
            self.server._free_failover_auto_pass(now)
        self.assertEqual(inject.call_count, 0)
        store = self.server._load_free_failovers()
        slots = []
        for sid, resume_at in resets.items():
            fire_at = store[sid]["auto_resume_fire_at"]
            self.assertGreaterEqual(fire_at, resume_at)
            slots.append(fire_at)
        self.assertEqual(len(slots), 12)
        for anchor in slots:
            window = [s for s in slots if anchor - 60 < s < anchor + 60]
            self.assertLessEqual(len(window), 5, (anchor, sorted(slots)))

    def test_old_approval_cannot_resume_a_later_stop(self):
        now = time.time()
        self._track("again", now=now)
        self.server.free_failover_arm("again")
        self._track("again", resume_in=-1, now=now + 10)
        with mock.patch.object(self.server, "_free_failover_devin_candidates", return_value=[]), \
             mock.patch.object(self.server, "_inject_text_into_session") as inject:
            self.server._free_failover_auto_pass(now + 20)
        inject.assert_not_called()

    def test_unknown_session_cannot_spawn_and_malformed_selection_rejected(self):
        with mock.patch.object(self.server, "free_failover_continue") as continuation:
            result = self.server.free_failover_fleet_action("continue", ["unknown"])
        self.assertFalse(result["ok"])
        continuation.assert_not_called()
        for ids in ([None], [1], [{}], [""], ["a"] * 129, "a"):
            result = self.server.free_failover_fleet_action("continue", ids)
            self.assertFalse(result["ok"])
            self.assertNotIn("action", result)

    def test_duplicate_concurrent_continue_returns_busy(self):
        self._track("concurrent")
        entered = threading.Event()
        release = threading.Event()
        results = []

        def continuation(sid, always=False):
            entered.set()
            release.wait(3)
            return {"ok": True}

        with mock.patch.object(self.server, "free_failover_continue", side_effect=continuation) as resume:
            worker = threading.Thread(target=lambda: results.append(
                self.server.free_failover_fleet_action("continue", ["concurrent"])))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                second = self.server.free_failover_fleet_action("continue", ["concurrent"])
                self.assertEqual(second["results"]["concurrent"]["code"], "busy")
            finally:
                release.set()
                worker.join(3)
        self.assertEqual(resume.call_count, 1)
        self.assertTrue(results[0]["ok"])
