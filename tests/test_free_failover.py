"""Limit-hit failover with approval (L06).

The real incident this answers: a fleet of Devin lanes all stopped with
"Reached free model rate limit. ... Your limit will reset in 52 minutes
(at 06:22 UTC)." These tests cover the detection of that exact message,
the parsed reset time, the approved continue-on-free resume, the approved
auto-resume at reset (staggered <= 5/minute), and the switch-back path.
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

DEVIN_LIMIT_TEXT = (
    "Reached free model rate limit. Upgrade to Max for higher limits, or "
    "switch to a different model. Your limit will reset in 52 minutes "
    "(at 06:22 UTC)."
)


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


class _FailoverBase(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-free-failover-")
        self.server.USAGE_LIMIT_RESUME_FILE = (
            Path(self.tmp_dir) / "usage_limit_resumes.json"
        )
        self.server.COMMAND_CENTER_STATE_DIR = Path(self.tmp_dir) / "ccstate"
        self.server.COMMAND_CENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        with self.server._usage_limit_resume_lock:
            self.server._usage_limit_resume_cache["data"] = None
        self.server._free_failover_cache_clear()
        self.server._free_ready_cache_clear()
        # _usage_limit_attach_continuation_fields -> _archive_all_rows_cached
        # would otherwise build the REAL session archive under ~/.claude on a
        # cold cache (minutes). These tests never need a real row.
        self._archive_patch = mock.patch.object(
            self.server, "_archive_all_rows_cached", return_value=([], {}))
        self._archive_patch.start()

    def tearDown(self):
        self._archive_patch.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.server._free_failover_cache_clear()
        self.server._free_ready_cache_clear()

    def _failover_store(self):
        return self.server._load_free_failovers()

    def _tracked_entry(self, sid, *, engine="claude", resume_in=3600, now=None):
        now = time.time() if now is None else now
        return {
            "engine": engine,
            "detected_at": now - 60,
            "resume_at": now + resume_in,
            "resume_at_estimated": False,
            "source_text_snippet": "limit",
            "model": "opus",
            "cwd": self.tmp_dir,
            "display_name": "Lane " + sid[:6],
        }

    def _no_candidates(self):
        return [
            mock.patch.object(
                self.server, "_usage_limit_kimi_candidates", return_value=[]),
            mock.patch.object(
                self.server, "_usage_limit_codex_candidates", return_value=[]),
            mock.patch.object(
                self.server, "_usage_limit_claude_candidates", return_value=[]),
            mock.patch.object(
                self.server, "_free_failover_devin_candidates", return_value=[]),
            mock.patch.object(self.server, "_log_activity"),
        ]


class DevinLimitMessageTests(_FailoverBase):
    """The exact coordinator incident text must parse a real reset time."""

    def _acp_file(self, raw_id, events):
        acp_dir = self.server.COMMAND_CENTER_STATE_DIR / "acp" / "devin"
        acp_dir.mkdir(parents=True, exist_ok=True)
        path = acp_dir / f"{raw_id}.jsonl"
        with open(path, "w") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
        return path

    def test_devin_limit_message_detected_with_utc_reset(self):
        raw = "11111111-2222-3333-4444-555555555555"
        sid = f"devincli-{raw}"
        path = self._acp_file(raw, [
            {"type": "user", "message": {"role": "user", "content": "work"}},
            {"type": "result", "subtype": "error", "error": DEVIN_LIMIT_TEXT,
             "ts": "2026-10-06T05:30:00.000Z"},
        ])
        found = self.server._detect_devin_usage_limit_stop(sid, path)
        self.assertIsNotNone(found)
        self.assertEqual(found["engine"], "devin")
        self.assertFalse(found["resume_at_estimated"])
        # Detected 05:30 UTC, reset quoted as 06:22 UTC -> 52 minutes out.
        import datetime as _dt
        resume = _dt.datetime.fromtimestamp(
            found["resume_at"], tz=_dt.timezone.utc)
        self.assertEqual((resume.hour, resume.minute), (6, 22))

    def test_devin_reset_clock_already_passed_rolls_to_tomorrow(self):
        reset, est = self.server._devin_reset_epoch(
            "reset in 5 minutes (at 01:00 UTC)", detected_at=0)
        # detected_at=0 -> 1970-01-01T00:00Z; 01:00 UTC is an hour out.
        self.assertFalse(est)
        self.assertEqual(reset, 3600)
        # Same text detected at 02:30 UTC -> 01:00 already passed -> +24h.
        reset2, _ = self.server._devin_reset_epoch(
            "reset in 5 minutes (at 01:00 UTC)", detected_at=9000)
        self.assertEqual(reset2, 9000 - 9000 + 86400 + 3600)

    def test_devin_reset_relative_only(self):
        reset, est = self.server._devin_reset_epoch(
            "Your limit will reset in 52 minutes", detected_at=1000)
        self.assertFalse(est)
        self.assertEqual(reset, 1000 + 52 * 60)

    def test_devin_reset_hours_relative(self):
        reset, est = self.server._devin_reset_epoch(
            "rate limit. reset in 2 hours", detected_at=1000)
        self.assertFalse(est)
        self.assertEqual(reset, 1000 + 7200)

    def test_devin_reset_unparseable_falls_back_estimated(self):
        reset, est = self.server._devin_reset_epoch(
            "Reached free model rate limit. Upgrade to Max.", detected_at=1000)
        self.assertTrue(est)
        self.assertEqual(reset, 1000 + 5 * 3600)

    def test_devin_non_limit_error_ignored(self):
        raw = "22222222-3333-4444-5555-666666666666"
        sid = f"devincli-{raw}"
        path = self._acp_file(raw, [
            {"type": "result", "subtype": "error",
             "error": "model output empty"},
        ])
        self.assertIsNone(
            self.server._detect_devin_usage_limit_stop(sid, path))

    def test_devin_clean_result_after_limit_supersedes_stop(self):
        raw = "33333333-4444-5555-6666-777777777777"
        sid = f"devincli-{raw}"
        path = self._acp_file(raw, [
            {"type": "result", "subtype": "error", "error": DEVIN_LIMIT_TEXT},
            {"type": "result", "subtype": "success"},
        ])
        self.assertIsNone(
            self.server._detect_devin_usage_limit_stop(sid, path))

    def test_scan_folds_devin_stop_into_tracked_store(self):
        raw = "44444444-5555-6666-7777-888888888888"
        sid = f"devincli-{raw}"
        self._acp_file(raw, [
            {"type": "result", "subtype": "error", "error": DEVIN_LIMIT_TEXT},
        ])
        with mock.patch.object(
                self.server, "_usage_limit_attach_continuation_fields",
                side_effect=lambda f, s, p: f):
            self.server._free_failover_scan_devin(time.time())
        entry = self.server._load_usage_limit_resumes().get(sid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["engine"], "devin")
        self.assertIn("rate limit", entry["source_text_snippet"].lower())


class ContinueFreeTests(_FailoverBase):
    def test_claude_continue_refuses_without_free_env(self):
        sid = "aaaaaaaa-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid))
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in ("CCC_FREE_ROUTER_BASE_URL", "CCC_FREE_ROUTER_TOKEN",
                    "CCC_FREE_ROUTER_MODEL"):
            os.environ.pop(key, None)
        res = self.server.free_failover_continue(sid)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "free_not_ready")

    def test_claude_continue_resumes_with_free_env_and_marks_fired(self):
        sid = "bbbbbbbb-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid))
        transcript = Path(self.tmp_dir) / f"{sid}.jsonl"
        transcript.write_text("")
        free_env = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3017",
            "ANTHROPIC_AUTH_TOKEN": "unified-test-token",
            "ANTHROPIC_MODEL": "free-test-model",
        }
        resume = mock.Mock(return_value={"ok": True, "pid": 4242})
        with mock.patch.object(
                self.server, "_free_spawn_env", return_value=free_env), \
             mock.patch.object(
                self.server, "_retire_idle_headless_for_session",
                return_value={"retired": True, "pid": 1111}), \
             mock.patch.object(
                self.server, "resume_session_headless", resume), \
             mock.patch.object(
                self.server, "_usage_limit_session_path",
                return_value=transcript), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_continue(sid, always=True)
        self.assertTrue(res["ok"], res)
        resume.assert_called_once()
        _args, kwargs = resume.call_args
        self.assertEqual(kwargs.get("extra_env"), free_env)
        entry = self.server._load_usage_limit_resumes()[sid]
        self.assertTrue(entry["fired"])
        rec = self._failover_store()[sid]
        self.assertEqual(rec["state"], "free")
        self.assertTrue(rec["always"])
        lines = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        self.assertEqual(lines[-1]["subtype"], "ccc_free_runtime")
        self.assertEqual(lines[-1]["event"], "failover_start")

    def test_claude_continue_refuses_when_busy(self):
        sid = "cccccccc-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid))
        free_env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:3017"}
        with mock.patch.object(
                self.server, "_free_spawn_env", return_value=free_env), \
             mock.patch.object(
                self.server, "_retire_idle_headless_for_session",
                return_value={"retired": False, "reason": "busy"}), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_continue(sid)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "busy")

    def test_codex_continue_is_not_free_routed(self):
        sid = "dddddddd-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid, engine="codex"))
        res = self.server.free_failover_continue(sid)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "unsupported_engine")

    def test_devin_continue_sets_override_and_free_model(self):
        raw = "55555555-6666-7777-8888-999999999999"
        sid = f"devincli-{raw}"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid, engine="devin"))
        resume = mock.Mock(return_value={"ok": True, "pid": 777})
        override = mock.Mock()
        with mock.patch.object(
                self.server, "_devin_acp_steer_capable", return_value=False), \
             mock.patch.object(
                self.server, "_devin_free_model_candidates",
                return_value=["swe-2-medium", "swe-2-high"]), \
             mock.patch.object(
                self.server, "_devin_cli_session_cwd",
                return_value=self.tmp_dir), \
             mock.patch.object(
                self.server, "resume_session_devin", resume), \
             mock.patch.object(
                self.server, "_set_session_override", override), \
             mock.patch.object(
                self.server, "_acp_transcript_path", return_value=None), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_continue(sid)
        self.assertTrue(res["ok"], res)
        resume.assert_called_once()
        self.assertEqual(resume.call_args.kwargs.get("model"), "swe-2-medium")
        override.assert_called_once()
        self.assertEqual(override.call_args.args[:2], (sid, "swe-2-medium"))
        rec = self._failover_store()[sid]
        self.assertEqual(rec["state"], "free")
        self.assertEqual(rec["free_model"], "swe-2-medium")


class AutoResumeArmTests(_FailoverBase):
    def test_arm_then_due_fires_via_injector(self):
        sid = "eeeeeeee-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid, resume_in=-1))
        self.assertTrue(self.server.free_failover_arm(sid)["ok"])
        inject = mock.Mock(return_value={"ok": True, "via": "test"})
        patches = self._no_candidates() + [
            mock.patch.object(
                self.server, "_usage_limit_session_path", return_value=None),
            mock.patch.object(
                self.server, "_inject_text_into_session", inject),
        ]
        for p in patches:
            p.start()
        try:
            self.server._usage_limit_scan_once(now=time.time())
        finally:
            for p in reversed(patches):
                p.stop()
        inject.assert_called_once()
        args, kwargs = inject.call_args
        self.assertEqual(args[0], sid)
        self.assertEqual(args[1], "continue")
        rec = self._failover_store()[sid]
        self.assertTrue(rec["auto_resume_done"])

    def test_unarmed_due_stop_stays_inert(self):
        """Approval required: detection alone must never send."""
        sid = "ffffffff-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid, resume_in=-1))
        inject = mock.Mock(return_value={"ok": True})
        patches = self._no_candidates() + [
            mock.patch.object(
                self.server, "_usage_limit_session_path", return_value=None),
            mock.patch.object(
                self.server, "_inject_text_into_session", inject),
        ]
        for p in patches:
            p.start()
        try:
            self.server._usage_limit_scan_once(now=time.time())
        finally:
            for p in reversed(patches):
                p.stop()
        inject.assert_not_called()
        self.assertTrue(
            self.server._load_usage_limit_resumes()[sid]["fired"])

    def test_six_armed_sessions_stagger_five_per_minute(self):
        """The whole point of staggering: 6 parked lanes must not all fire
        the same minute the reset hits."""
        sids = [f"sid{i:08d}-0000-0000-0000-000000000000" for i in range(6)]
        now = time.time()
        resume_at = now - 10
        for sid in sids:
            self.server._save_usage_limit_resume_entry(sid, {
                "engine": "claude", "detected_at": now - 600,
                "resume_at": resume_at, "display_name": sid,
            })
            self.assertTrue(self.server.free_failover_arm(sid)["ok"])
        inject = mock.Mock(return_value={"ok": True})
        patches = self._no_candidates() + [
            mock.patch.object(
                self.server, "_usage_limit_session_path", return_value=None),
            mock.patch.object(
                self.server, "_inject_text_into_session", inject),
        ]
        for p in patches:
            p.start()
        try:
            self.server._free_failover_auto_pass(now)
        finally:
            for p in reversed(patches):
                p.stop()
        # Five fire in the first minute slot; the sixth waits +60s.
        self.assertEqual(inject.call_count, 5)
        sixth = self._failover_store()[sids[5]]
        self.assertEqual(sixth["auto_resume_fire_at"], now + 60)
        self.assertFalse(sixth.get("auto_resume_done"))

    def test_disarm_stops_the_fire(self):
        sid = "12121212-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid, resume_in=-1))
        self.server.free_failover_arm(sid)
        self.server.free_failover_arm(sid, armed=False)
        inject = mock.Mock(return_value={"ok": True})
        patches = self._no_candidates() + [
            mock.patch.object(
                self.server, "_usage_limit_session_path", return_value=None),
            mock.patch.object(
                self.server, "_inject_text_into_session", inject),
        ]
        for p in patches:
            p.start()
        try:
            self.server._free_failover_auto_pass(time.time())
        finally:
            for p in reversed(patches):
                p.stop()
        inject.assert_not_called()


class SwitchBackTests(_FailoverBase):
    def test_switch_back_retires_free_headless(self):
        sid = "34343434-1111-2222-3333-444444444444"
        transcript = Path(self.tmp_dir) / f"{sid}.jsonl"
        transcript.write_text("")
        self.server._free_failover_save(sid, {
            "state": "free", "engine": "claude",
            "free_since": time.time() - 600,
        })
        retire = mock.Mock(return_value={"retired": True, "pid": 42})
        with mock.patch.object(
                self.server, "_retire_idle_headless_for_session", retire), \
             mock.patch.object(
                self.server, "_usage_limit_session_path",
                return_value=transcript), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_switch_back(sid)
        self.assertTrue(res["ok"])
        self.assertFalse(res["pending"])
        retire.assert_called_once()
        rec = self._failover_store()[sid]
        self.assertNotEqual(rec.get("state"), "free")
        lines = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        self.assertEqual(lines[-1]["event"], "failover_back")

    def test_switch_back_defers_while_busy(self):
        sid = "45454545-1111-2222-3333-444444444444"
        self.server._free_failover_save(sid, {
            "state": "free", "engine": "claude",
            "free_since": time.time() - 600,
        })
        with mock.patch.object(
                self.server, "_retire_idle_headless_for_session",
                return_value={
                    "retired": False, "deferred": True, "reason": "busy"}), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_switch_back(sid)
        self.assertTrue(res["ok"])
        self.assertTrue(res["pending"])
        rec = self._failover_store()[sid]
        self.assertEqual(rec["state"], "switch_back_pending")

    def test_switch_back_devin_clears_override(self):
        sid = "devincli-56565656-7777-8888-9999-000000000000"
        self.server._free_failover_save(sid, {
            "state": "free", "engine": "devin",
            "free_since": time.time() - 600,
            "origin_model": "swe-2-max",
        })
        clear = mock.Mock()
        with mock.patch.object(
                self.server, "_clear_session_override", clear), \
             mock.patch.object(
                self.server, "_devin_acp_steer_capable", return_value=False), \
             mock.patch.object(
                self.server, "_acp_transcript_path", return_value=None), \
             mock.patch.object(self.server, "_log_activity"):
            res = self.server.free_failover_switch_back(sid)
        self.assertTrue(res["ok"])
        clear.assert_called_once_with(sid)


class StatusPayloadTests(_FailoverBase):
    def test_status_reports_limited_session_and_free_readiness(self):
        sid = "67676767-1111-2222-3333-444444444444"
        self.server._save_usage_limit_resume_entry(
            sid, self._tracked_entry(sid))
        with mock.patch.dict(os.environ, {
                "CCC_FREE_ROUTER_BASE_URL": "http://127.0.0.1:3017",
                "CCC_FREE_ROUTER_MODEL": "free-test-model"}), \
             mock.patch.object(
                self.server, "_devin_free_model_candidates",
                return_value=["swe-2-medium"]):
            self.server._free_ready_cache_clear()
            status = self.server.free_failover_status()
        self.assertTrue(status["ok"])
        self.assertTrue(status["free_ready"])
        self.assertIn(sid, status["sessions"])
        item = status["sessions"][sid]
        self.assertEqual(item["state"], "limited")
        self.assertTrue(item["supports_continue_free"])
        self.assertTrue(item["supports_auto_resume"])
        self.assertEqual(
            status["auto_resume_max_per_minute"], 5)

    def test_free_state_offers_switch_back_after_reset(self):
        sid = "78787878-1111-2222-3333-444444444444"
        now = time.time()
        self.server._free_failover_save(sid, {
            "state": "free", "engine": "claude",
            "free_since": now - 3600,
            "origin_resume_at": now - 60,
            "origin_detected_at": now - 3600,
        })
        status = self.server.free_failover_status()
        item = status["sessions"][sid]
        self.assertEqual(item["state"], "free")
        self.assertTrue(item["switch_back_offered"])


if __name__ == "__main__":
    unittest.main()
