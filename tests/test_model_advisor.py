"""Tests for the model-drift advisor scorer + recommendation log.

Synthetic fixtures only (no real transcripts — those carry PII and never ship).
Each fixture encodes one drift pattern and asserts the recommendation matches.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import model_advisor as ma
import server


def _u(text):
    return {"type": "user", "message": {"content": text}}


def _a(text="", tools=None, model="claude-opus-4-8"):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for name in tools or []:
        content.append({"type": "tool_use", "name": name, "input": {}})
    return {"type": "assistant", "message": {"model": model, "content": content}}


def _write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


class ScorerTest(unittest.TestCase):
    def test_mechanical_worker_downgrades(self):
        rows = []
        for _ in range(8):
            rows.append(_u("continue"))
            rows.append(_a("", tools=["Bash", "Edit", "Bash"]))
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path)
        rec = ma.recommend("opus", turns)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["action"], "downgrade")
        self.assertEqual(rec["to_model"], "sonnet")
        self.assertLessEqual(ma.score_window(turns)["score"], ma._LOW)

    def test_strategy_session_keeps_opus(self):
        rows = [
            _u("What do you think — should we use a DAG or a flat queue here, and why?"),
            _a("Two options. Option A is a DAG; the tradeoff is complexity. I recommend...",
               tools=["WebSearch"]),
            _u("How does backpressure retry compare? Give an example of the tradeoff."),
            _a("On the other hand, the real question is durability. My rec: ...",
               tools=["AskUserQuestion"]),
        ]
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path)
        self.assertIsNone(ma.recommend("opus", turns))
        self.assertGreater(ma.score_window(turns)["score"], ma._LOW)

    def test_cheap_model_doing_hard_reasoning_upgrades(self):
        rows = [
            _u("Why would we choose this architecture? What are the tradeoffs vs the alternative?"),
            _a("Let's consider the options. Option A vs Option B; the tradeoff is...",
               tools=["WebSearch", "AskUserQuestion"], model="claude-sonnet-4-6"),
            _u("How should we decide? Which approach do you recommend and why?"),
            _a("Weighing the design tradeoffs, I recommend...",
               tools=["WebFetch"], model="claude-sonnet-4-6"),
        ]
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path)
        rec = ma.recommend("sonnet", turns)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["action"], "upgrade")
        self.assertEqual(rec["to_model"], "opus")

    def test_plan_then_execute_suggests_spawn_worker(self):
        # Substantial planning phase, then it flips to mechanical execution.
        rows = []
        for _ in range(4):
            rows.append(_u("What's the best approach here? Which option do you recommend and why?"))
            rows.append(_a("Two options; the tradeoff is... I recommend option A.",
                           tools=["AskUserQuestion"]))
        for _ in range(5):
            rows.append(_u("continue"))
            rows.append(_a("", tools=["Edit", "Bash", "Write"]))
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path, max_turns=20)
        rec = ma.recommend("opus", turns)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["action"], "spawn_worker")
        self.assertTrue(rec["transition"])

    def test_non_claude_engine_abstains(self):
        rows = [_u("continue"), _a("", tools=["Bash"], model="gpt-5")]
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path)
        self.assertIsNone(ma.recommend("gpt-5-codex", turns))

    def test_gray_zone_abstains(self):
        # Balanced signals -> score in (LOW, HIGH) -> no recommendation.
        rows = [
            _u("ok"),
            _a("Here's a quick thought on the approach.", tools=["Read"]),
            _u("what about the other file?"),
            _a("Done.", tools=["Edit"]),
        ]
        path = _write_jsonl(rows)
        self.addCleanup(os.remove, path)
        turns = ma.read_recent_turns(path)
        s = ma.score_window(turns)["score"]
        if ma._LOW < s < ma._HIGH:
            self.assertIsNone(ma.recommend("opus", turns))


class LogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "model-advisor-log.json")

    def _rec(self, action="downgrade"):
        return {
            "action": action, "from_model": "opus", "to_model": "sonnet",
            "score": 12, "phase": "executing", "transition": False,
            "reason": "mechanical", "confidence": "high",
        }

    def test_log_dedup_within_cooldown(self):
        a = ma.log_recommendation(self.path, "sid1", "worker", self._rec(), 1000)
        b = ma.log_recommendation(self.path, "sid1", "worker", self._rec(), 1500)
        self.assertEqual(a["id"], b["id"])  # deduped
        data = ma._load_log(self.path)
        self.assertEqual(len(data["recommendations"]), 1)

    def test_dismissed_rec_stays_sticky_past_cooldown(self):
        e = ma.log_recommendation(self.path, "sid1", "worker", self._rec(), 1000)
        ma.mark(self.path, e["id"], "dismissed")
        data = ma._load_log(self.path)
        data["recommendations"][0]["ts"] = "2000-01-01T00:00:00Z"  # ancient
        ma._save_log(self.path, data)
        again = ma.log_recommendation(self.path, "sid1", "worker", self._rec(), 1500)
        self.assertEqual(again["id"], e["id"])
        self.assertEqual(again["status"], "dismissed")

    def test_applied_downgrade_accrues_realized_savings(self):
        e = ma.log_recommendation(self.path, "sid1", "worker", self._rec(), 0)
        ma.mark(self.path, e["id"], "applied")
        # Session produced 1,000,000 output tokens after applying.
        ma.refresh_savings(self.path, lambda sid: 1_000_000)
        data = ma._load_log(self.path)
        entry = data["recommendations"][0]
        self.assertEqual(entry["status"], "applied")
        # opus(75) - sonnet(15) = $60 per Mtok output.
        self.assertAlmostEqual(entry["realized_savings_usd"], 60.0, places=1)
        self.assertEqual(entry["missed_savings_usd"], 0.0)

    def test_pending_downgrade_accrues_missed_savings(self):
        e = ma.log_recommendation(self.path, "sid2", "worker", self._rec(), 0)
        ma.refresh_savings(self.path, lambda sid: 500_000)
        data = ma._load_log(self.path)
        entry = data["recommendations"][0]
        self.assertEqual(entry["status"], "pending")
        self.assertAlmostEqual(entry["missed_savings_usd"], 30.0, places=1)

    def test_summarize_rolls_up(self):
        e1 = ma.log_recommendation(self.path, "s1", "", self._rec(), 0)
        ma.mark(self.path, e1["id"], "applied")
        ma.log_recommendation(self.path, "s2", "", self._rec(), 0)
        ma.refresh_savings(self.path, lambda sid: 1_000_000 if sid == "s1" else 0)
        s = ma.summarize(ma._load_log(self.path))
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["applied"], 1)
        self.assertEqual(s["pending"], 1)


class ReportCacheTest(unittest.TestCase):
    def test_cached_read_never_builds(self):
        cache = ma.AdvisorReportCache()

        report = cache.get_cached()

        self.assertTrue(report["ok"])
        self.assertEqual(report["live"], [])
        self.assertEqual(report["scanned"], [])
        self.assertIn("summary", report)

    def test_concurrent_refreshes_share_one_build(self):
        cache = ma.AdvisorReportCache(min_refresh_seconds=0)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def build():
            calls.append(True)
            started.set()
            release.wait(2)
            return {"ok": True, "live": [{"id": "one"}]}

        results = []
        threads = [
            threading.Thread(target=lambda: results.append(cache.refresh(build)))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(started.wait(1))
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(2)

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [{"ok": True, "live": [{"id": "one"}]}] * 2)

    def test_normal_refresh_is_limited_but_forced_refresh_is_immediate(self):
        now = [1000.0]
        cache = ma.AdvisorReportCache(min_refresh_seconds=300, clock=lambda: now[0])
        calls = []

        def build():
            calls.append(now[0])
            return {"ok": True, "live": [], "generation": len(calls)}

        self.assertEqual(cache.refresh(build)["generation"], 1)
        now[0] += 299
        self.assertEqual(cache.refresh(build)["generation"], 1)
        self.assertEqual(calls, [1000.0])
        self.assertEqual(cache.refresh(build, force=True)["generation"], 2)

    def test_failed_refresh_preserves_last_report_and_can_retry(self):
        cache = ma.AdvisorReportCache(min_refresh_seconds=300, clock=lambda: 1000.0)
        first = cache.refresh(lambda: {"ok": True, "generation": 1})

        with self.assertRaisesRegex(RuntimeError, "boom"):
            cache.refresh(
                lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                force=True,
            )

        self.assertEqual(cache.get_cached(), first)
        second = cache.refresh(
            lambda: {"ok": True, "generation": 2},
            force=True,
        )
        self.assertEqual(second["generation"], 2)


class ServerReportAccessTest(unittest.TestCase):
    def setUp(self):
        self.original_cache = server._model_advisor_report_cache
        server._model_advisor_report_cache = ma.AdvisorReportCache(
            min_refresh_seconds=300
        )
        self.addCleanup(
            setattr,
            server,
            "_model_advisor_report_cache",
            self.original_cache,
        )

    def test_default_access_returns_cache_without_scanning(self):
        original = server.build_model_advisor_report
        server.build_model_advisor_report = lambda: self.fail(
            "cached read started a scan"
        )
        self.addCleanup(setattr, server, "build_model_advisor_report", original)

        report = server.get_model_advisor_report()

        self.assertTrue(report["ok"])
        self.assertEqual(report["live"], [])

    def test_fresh_and_force_modes_delegate_to_cache(self):
        calls = []
        original = server.build_model_advisor_report
        server.build_model_advisor_report = lambda: calls.append(True) or {
            "ok": True,
            "live": [],
            "scanned": [],
            "generation": len(calls),
        }
        self.addCleanup(setattr, server, "build_model_advisor_report", original)

        self.assertEqual(server.get_model_advisor_report("1")["generation"], 1)
        self.assertEqual(server.get_model_advisor_report("1")["generation"], 1)
        self.assertEqual(server.get_model_advisor_report("force")["generation"], 2)


if __name__ == "__main__":
    unittest.main()
