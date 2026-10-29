"""ccc_server/health_report.py: the local /api/telemetry/health-report payload."""

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- lets ccc_server.core's proxy resolve state paths
from ccc_server import health_report as hr
from ccc_server import perf_events as pe


NOW = 1_800_000_000.0


def _ev(kind, ms, **detail):
    return {"kind": kind, "ms": ms, "detail": detail or None}


class BucketTest(unittest.TestCase):
    def test_bucket_labels(self):
        self.assertEqual(hr.bucket_count(0), "0")
        self.assertEqual(hr.bucket_count(1), "1")
        self.assertEqual(hr.bucket_count(4), "2-5")
        self.assertEqual(hr.bucket_count(50), "21-50")
        self.assertEqual(hr.bucket_count(9999), "2001+")
        self.assertIsNone(hr.bucket_count("x"))

    def test_latency_histogram_is_mergeable(self):
        s = hr.latency_stats([50, 99, 100, 999, 20000])
        self.assertEqual(s["count"], 5)
        self.assertEqual(len(s["hist"]), len(hr.LATENCY_BUCKETS_MS) + 1)
        self.assertEqual(sum(s["hist"]), 5)
        self.assertEqual(s["hist"][0], 2)    # < 100
        self.assertEqual(s["hist"][1], 1)    # 100..249
        self.assertEqual(s["hist"][3], 1)    # 500..999
        self.assertEqual(s["hist"][-1], 1)   # >= 16000
        self.assertLessEqual(s["p50"], s["p75"])
        self.assertLessEqual(s["p75"], s["p95"])


class ReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "activity.log"
        old = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(NOW - 3 * 86400))
        new = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(NOW - 600))
        self.log.write_text(
            f"{old} UTC  inject          INJECT   session=a mode=send source=composer text=x\n"
            f"{new} UTC  inject          INJECT   session=a mode=send source=composer text=x\n"
            f"{new} UTC  inject          INJECT   session=b mode=send source=wt text=x\n"
            f"{new} UTC  inject          INJECT   session=b mode=send source=secret-thing text=x\n"
            f"{new} UTC  tts             SPEAK    chars=1\n"
        )
        self.rows = [
            {"session_id": "s1", "mtime": NOW - 60, "is_live": True},
            {"session_id": "s2", "mtime": NOW - 60,
             "session_state": {"next_step_user": "Approve the deploy."}},
            {"session_id": "s3", "mtime": NOW - 60,
             "session_state": {"next_step_user": "None."}},
            {"session_id": "s4", "mtime": NOW - 5 * 86400},   # not active
        ]
        self.cards = [
            {"status": "open", "source": {"session_id": "s1"}},
            {"status": "open", "options": [{"action": {"session_id": "s4"}}]},
            {"status": "dismissed", "source": {"session_id": "s3"}},
        ]
        self.events = [
            _ev("view_switch", 400, to="sessions", mode="in_page"),
            _ev("view_switch", 2600, to="decision-inbox", mode="navigate"),
            _ev("view_switch", 900, to="my-private-app", mode="navigate"),
            _ev("rail_apps", 30, ok=True, attempt=0),
            _ev("rail_apps", 5000, ok=False, attempt=0),
            _ev("rail_apps", 40, ok=True, attempt=1),
            _ev("conv_open", 1200),
        ]

    def tearDown(self):
        self._tmp.cleanup()

    def _report(self, exact=False, hours=24):
        return hr.build_report(exact=exact, now=NOW, rows=self.rows, cards=self.cards,
                               wt_counts=(3, 42, 2, 9), events=self.events,
                               activity_path=self.log, hours=hours)

    def test_seven_day_window(self):
        r = self._report(exact=True, hours=168)
        self.assertEqual(r["window_hours"], 168)
        # The 3-day-old inject and the 5-day-old session now count.
        self.assertEqual(r["messages"]["by_source"]["composer"], 2)
        self.assertEqual(r["sessions"]["active"], 4)

    def test_window_is_clamped(self):
        self.assertEqual(self._report(hours=99999)["window_hours"], hr.MAX_WINDOW_HOURS)
        self.assertEqual(self._report(hours=0)["window_hours"], 1)
        self.assertEqual(self._report(hours="junk")["window_hours"], 24)

    def test_shape_and_buckets(self):
        r = self._report()
        self.assertEqual(r["report_version"], hr.REPORT_VERSION)
        self.assertFalse(r["exact"])
        self.assertEqual(r["sessions"]["active"], "2-5")
        self.assertEqual(r["watchtower"]["tickets_open"], "21-50")
        self.assertEqual(r["messages"]["sent"], "2-5")
        json.dumps(r)  # serializable

    def test_exact_mode(self):
        r = self._report(exact=True)
        self.assertEqual(r["sessions"]["active"], 3)
        self.assertEqual(r["messages"]["by_source"], {"composer": 1, "other": 1, "wt": 1})
        self.assertEqual(r["watchtower"]["workers_alive"], 2)

    def test_decision_and_waiting_percentages(self):
        s = self._report()["sessions"]
        # s1 of 3 active sessions has an open card (s4's card is not active).
        self.assertEqual(s["pct_with_open_decision"], 35)
        # s2 names a real next step; s3's "None." does not count.
        self.assertEqual(s["pct_waiting_on_user"], 35)
        self.assertEqual(s["decision_cards_open"], "2-5")

    def test_view_switch_and_rail(self):
        r = self._report()
        self.assertIn("sessions:in_page", r["view_switch"])
        self.assertIn("decision-inbox:navigate", r["view_switch"])
        self.assertIn("custom:navigate", r["view_switch"])
        self.assertNotIn("my-private-app", json.dumps(r))
        self.assertEqual(r["rail_apps"], {"ok": 2, "failed": 1, "retries": 1})
        self.assertEqual(r["latency"]["view_switch"]["count"], 3)

    def test_no_identifying_values(self):
        blob = json.dumps(self._report())
        for needle in ("s1", "s2", "session=", "secret-thing", "Approve the deploy"):
            self.assertNotIn(needle, blob)

    def test_no_subprocess_when_sources_given(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("spawned")):
            self._report()

    def test_large_row_count_is_linear(self):
        self.rows = [{"session_id": "s%d" % i, "mtime": NOW - 60} for i in range(20000)]
        t = time.time()
        self._report()
        self.assertLess(time.time() - t, 2.0)


class PerfWindowReadTest(unittest.TestCase):
    def test_reads_window_past_the_2mb_summary_tail(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "perf-events.jsonl"
            def row(ts, kind, ms, detail=None):
                iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".5Z"
                return json.dumps({"ts": iso, "kind": kind, "ms": ms, "boot_id": "",
                                   "conv_id": "", "detail": detail}) + "\n"
            with open(path, "w") as f:
                f.write(row(NOW - 9 * 86400, "conv_open", 1))       # outside 7d
                f.write(row(NOW - 6 * 86400, "conv_open", 700))
                pad = row(NOW - 3 * 86400, "list_read", 5)
                f.write(pad * (3 * 1024 * 1024 // len(pad)))       # > 2 MB filler
                f.write(row(NOW - 60, "view_switch", 2400, {"to": "queues", "mode": "navigate"}))
                f.write("not json\n")
            ev = hr.read_perf_window(NOW - 7 * 86400, path=path)
            kinds = [e["kind"] for e in ev]
            self.assertEqual(kinds[0], "conv_open")
            self.assertEqual(ev[0]["ms"], 700)
            self.assertEqual(ev[-1]["detail"], {"to": "queues", "mode": "navigate"})
            self.assertNotIn(1, [e["ms"] for e in ev if e["kind"] == "conv_open"])
            capped = hr.read_perf_window(NOW - 7 * 86400, path=path, max_bytes=1024)
            self.assertLess(len(capped), 20)                     # bounded read
            self.assertEqual(capped[-1]["kind"], "view_switch")


class PerfKindsTest(unittest.TestCase):
    def test_new_kinds_record_without_self_filing(self):
        with tempfile.TemporaryDirectory() as d:
            old = pe._STATE_DIR_OVERRIDE
            pe._STATE_DIR_OVERRIDE = Path(d)
            try:
                for kind in ("view_switch", "rail_apps"):
                    res = pe.record_event(kind, 120, detail={"to": "sessions"})
                    self.assertTrue(res["ok"])
                    self.assertIn(kind, pe._NO_SELF_FILE_KINDS)
                rows = [json.loads(x) for x in (Path(d) / "perf-events.jsonl").read_text().splitlines()]
                self.assertEqual([r["kind"] for r in rows], ["view_switch", "rail_apps"])
            finally:
                pe._STATE_DIR_OVERRIDE = old


if __name__ == "__main__":
    unittest.main()
