"""Perf-event beacon sink + breach-pattern self-filing ticket checker.

Covers ccc_server/perf_events.py in isolation: state files are redirected
to a tempdir per test (never ~/.claude/command-center), and the `wt`
subprocess boundary is replaced with an in-memory fake so no real
WatchTower CLI is invoked. Fast / no server spawn — matches the rest of
tests/ (not tests/test_smoke.py).
"""

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- required so ccc_server.core's _core proxy can
                # resolve COMMAND_CENTER_STATE_DIR at perf_events import time.
from ccc_server import perf_events as pe


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _append_raw(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _row(kind, ms, ts, threshold_ms, warm=False, conv_id="", boot_id="boot1"):
    return {
        "ts": _iso(ts),
        "kind": kind,
        "ms": ms,
        "boot_id": boot_id,
        "conv_id": conv_id,
        "warm": warm,
        "threshold_ms": threshold_ms,
        "breach": ms >= threshold_ms,
        "detail": None,
    }


class FakeWt:
    """Stand-in for the real `wt` CLI, injected via pe._WT_RUNNER."""

    def __init__(self):
        self.calls = []
        self.find_rc = 0
        self.find_status = "open"
        self.add_rc = 0
        self.add_ref = "CCC-501"

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        if args[0] == "find":
            if self.find_rc != 0:
                return (self.find_rc, "")
            return (0, json.dumps({"status": self.find_status, "ref": args[1]}))
        if args[0] == "add":
            if self.add_rc != 0:
                return (self.add_rc, "")
            return (0, f"Filed ticket {self.add_ref}\n")
        return (1, "")


class PerfEventsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_override = pe._STATE_DIR_OVERRIDE
        pe._STATE_DIR_OVERRIDE = Path(self._tmp.name)
        self._orig_runner = pe._WT_RUNNER
        self.addCleanup(self._restore)

    def _restore(self):
        pe._STATE_DIR_OVERRIDE = self._orig_override
        pe._WT_RUNNER = self._orig_runner

    def events_path(self):
        return pe._events_path()


class TestRecordEvent(PerfEventsTestBase):
    def test_archive_load_cold_breach(self):
        with mock.patch.object(pe, "archive_is_warm", return_value=False):
            result = pe.record_event("archive_load", 6000)
        self.assertTrue(result["ok"])
        self.assertFalse(result["warm"])
        self.assertEqual(result["threshold_ms"], pe.ARCHIVE_COLD_MS)
        self.assertTrue(result["breach"])

        lines = self.events_path().read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["kind"], "archive_load")
        self.assertEqual(row["ms"], 6000)
        self.assertFalse(row["warm"])
        self.assertTrue(row["breach"])

    def test_archive_load_warm_no_breach(self):
        with mock.patch.object(pe, "archive_is_warm", return_value=True):
            result = pe.record_event("archive_load", 500, conv_id="c1", boot_id="b1")
        self.assertTrue(result["warm"])
        self.assertEqual(result["threshold_ms"], pe.ARCHIVE_WARM_MS)
        self.assertFalse(result["breach"])

    def test_conv_open_breach_ignores_warm(self):
        with mock.patch.object(pe, "archive_is_warm", return_value=True):
            result = pe.record_event("conv_open", pe.CONV_OPEN_MS + 1)
        # conv_open never reads as warm, regardless of archive_is_warm.
        self.assertFalse(result["warm"])
        self.assertTrue(result["breach"])

    def test_bad_kind_raises(self):
        with self.assertRaises(ValueError):
            pe.record_event("not_a_real_kind", 100)

    def test_non_numeric_ms_raises(self):
        with self.assertRaises(ValueError):
            pe.record_event("conv_open", "abc")

    def test_negative_ms_raises(self):
        with self.assertRaises(ValueError):
            pe.record_event("conv_open", -1)


class TestReadEvents(PerfEventsTestBase):
    def test_filters_by_since_and_skips_corrupt_lines(self):
        now = time.time()
        path = self.events_path()
        _append_raw(path, _row("conv_open", 1000, now - 3600, pe.CONV_OPEN_MS))
        with open(path, "a", encoding="utf-8") as f:
            f.write("{not valid json\n")
        _append_raw(path, _row("conv_open", 2000, now - 10, pe.CONV_OPEN_MS))

        events = pe.read_events(now - 60)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ms"], 2000)

        events_all = pe.read_events(now - 7200)
        self.assertEqual(len(events_all), 2)

    def test_missing_file_returns_empty(self):
        self.assertEqual(pe.read_events(0), [])


class TestSummarize(PerfEventsTestBase):
    def test_percentiles_and_breaches(self):
        now = time.time()
        path = self.events_path()
        for ms in (100, 200, 300, 6000, 7000):
            _append_raw(path, _row("archive_load", ms, now - 60, pe.ARCHIVE_COLD_MS))

        summary = pe.summarize(hours=24, now=now)
        kind = summary["kinds"]["archive_load"]
        self.assertEqual(kind["count"], 5)
        self.assertEqual(kind["max"], 7000)
        self.assertEqual(kind["breaches"], 2)
        self.assertEqual(kind["p50"], 300)
        self.assertIn(kind["p95"], (6000, 7000))
        self.assertEqual(len(kind["worst"]), 5)
        self.assertEqual(kind["worst"][0]["ms"], 7000)
        self.assertEqual(summary["thresholds"]["archive_load_cold_ms"], pe.ARCHIVE_COLD_MS)
        self.assertIn("ticket", summary)

    def test_hours_clamped(self):
        summary = pe.summarize(hours=99999)
        self.assertEqual(summary["hours"], 720)
        summary2 = pe.summarize(hours=0)
        self.assertEqual(summary2["hours"], 1)


class TestEvaluateBreachPattern(PerfEventsTestBase):
    def test_no_pattern_returns_none(self):
        now = time.time()
        events = [_row("conv_open", 100, now, pe.CONV_OPEN_MS)]
        self.assertIsNone(pe.evaluate_breach_pattern(events))

    def test_two_breaches_qualify(self):
        now = time.time()
        events = [
            _row("conv_open", 6000, now, pe.CONV_OPEN_MS),
            _row("conv_open", 5500, now, pe.CONV_OPEN_MS),
            _row("conv_open", 100, now, pe.CONV_OPEN_MS),
        ]
        pattern = pe.evaluate_breach_pattern(events)
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["kind"], "conv_open")
        self.assertEqual(pattern["count"], 3)

    def test_single_3x_sample_qualifies(self):
        now = time.time()
        events = [_row("archive_load", 16000, now, pe.ARCHIVE_COLD_MS)]
        pattern = pe.evaluate_breach_pattern(events)
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["kind"], "archive_load")

    def test_single_2x_sample_no_longer_qualifies(self):
        # The single-sample bar moved to 3x (CCC-1154): a lone 2x sample
        # still counts toward the >=2-breaches rule but does not file alone.
        now = time.time()
        events = [_row("archive_load", 11000, now, pe.ARCHIVE_COLD_MS)]
        self.assertIsNone(pe.evaluate_breach_pattern(events))

    def test_picks_worse_p95_between_qualifying_kinds(self):
        now = time.time()
        events = [
            _row("conv_open", 6000, now, pe.CONV_OPEN_MS),
            _row("conv_open", 6100, now, pe.CONV_OPEN_MS),
            _row("archive_load", 50000, now, pe.ARCHIVE_COLD_MS),
        ]
        pattern = pe.evaluate_breach_pattern(events)
        self.assertEqual(pattern["kind"], "archive_load")


class TestPerfTicketCheckOnce(PerfEventsTestBase):
    def _seed_breach(self, now, kind="archive_load"):
        path = self.events_path()
        threshold = pe.ARCHIVE_COLD_MS if kind == "archive_load" else pe.CONV_OPEN_MS
        _append_raw(path, _row(kind, threshold * 3, now - 60, threshold))
        _append_raw(path, _row(kind, threshold * 2, now - 30, threshold))

    def test_no_data_is_ok(self):
        result = pe.perf_ticket_check_once(now=time.time())
        self.assertEqual(result, "ok")

    def test_stale_breach_does_not_file_a_new_ticket(self):
        now = time.time()
        threshold = pe.CONV_OPEN_MS
        path = self.events_path()
        _append_raw(path, _row("conv_open", threshold * 3, now - 7200, threshold))
        _append_raw(path, _row("conv_open", threshold * 2, now - 7100, threshold))
        fake = FakeWt()
        pe._WT_RUNNER = fake

        result = pe.perf_ticket_check_once(now=now)

        self.assertEqual(result, "ok")
        self.assertFalse(any(call[0] == "add" for call in fake.calls))

    def test_files_ticket_then_dedupes_same_day(self):
        now = time.time()
        self._seed_breach(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake

        result = pe.perf_ticket_check_once(now=now)
        self.assertEqual(result, f"filed:{fake.add_ref}")
        state = pe._load_ticket_state()
        self.assertEqual(state["last_ref"], fake.add_ref)
        self.assertEqual(state["last_status"], "open")

        add_calls = [c for c in fake.calls if c[0] == "add"]
        self.assertEqual(len(add_calls), 1)

        # Second check later the same UTC day must not file again, even
        # though the breach pattern is still present.
        fake.find_status = "in_progress"
        result2 = pe.perf_ticket_check_once(now=now + 5)
        self.assertEqual(result2, "already-filed-today")
        add_calls_after = [c for c in fake.calls if c[0] == "add"]
        self.assertEqual(len(add_calls_after), 1, "must not file a second time same day")

    def test_open_ticket_suppresses_refiling_next_day(self):
        now = time.time()
        self._seed_breach(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake
        first = pe.perf_ticket_check_once(now=now)
        self.assertTrue(first.startswith("filed:"))

        next_day = now + 86400
        self._seed_breach(next_day)
        fake.find_status = "in_progress"
        result = pe.perf_ticket_check_once(now=next_day)
        self.assertEqual(result, "open-ticket-exists")
        add_calls = [c for c in fake.calls if c[0] == "add"]
        self.assertEqual(len(add_calls), 1, "an open prior ticket must suppress refiling")

    def test_closed_ticket_regression_refiles_with_note(self):
        now = time.time()
        self._seed_breach(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake
        first = pe.perf_ticket_check_once(now=now)
        self.assertTrue(first.startswith("filed:"))
        first_ref = first.split(":", 1)[1]

        next_day = now + 86400
        self._seed_breach(next_day)
        fake.find_status = "closed"
        fake.add_ref = "CCC-777"
        result = pe.perf_ticket_check_once(now=next_day)
        self.assertEqual(result, "filed:CCC-777")

        add_calls = [c for c in fake.calls if c[0] == "add"]
        self.assertEqual(len(add_calls), 2)
        note = add_calls[-1][add_calls[-1].index("--note") + 1]
        self.assertIn("Regression", note)
        self.assertIn(first_ref, note)

    def test_wt_unavailable(self):
        now = time.time()
        self._seed_breach(now)
        fake = FakeWt()
        fake.add_rc = 127
        pe._WT_RUNNER = fake
        result = pe.perf_ticket_check_once(now=now)
        self.assertEqual(result, "wt-unavailable")
        state = pe._load_ticket_state()
        self.assertNotIn("last_ref", state)


class TestWarmupSuppression(PerfEventsTestBase):
    """Samples recorded inside the post-boot warmup window are real data but
    must never qualify a breach pattern on their own -- a giant first-paint
    outlier during startup contention is expected, not a regression."""

    def test_record_event_stamps_warmup_flag(self):
        with mock.patch.object(pe, "_PROCESS_STARTED_AT", time.time()):
            pe.record_event("archive_load", 300, detail={"since_nav_ms": 400})
        row = json.loads(self.events_path().read_text().strip())
        self.assertTrue(row["warmup"])

    def test_record_event_clears_warmup_after_window(self):
        with mock.patch.object(
            pe, "_PROCESS_STARTED_AT", time.time() - pe.WARMUP_S - 1
        ):
            pe.record_event("conv_open", 100)
        row = json.loads(self.events_path().read_text().strip())
        self.assertFalse(row["warmup"])

    def test_warmup_2x_sample_does_not_qualify(self):
        now = time.time()
        row = _row("archive_load", 50000, now, pe.ARCHIVE_COLD_MS)
        row["warmup"] = True
        self.assertIsNone(pe.evaluate_breach_pattern([row]))
        # Same sample without the flag still qualifies.
        del row["warmup"]
        self.assertIsNotNone(pe.evaluate_breach_pattern([row]))

    def test_warmup_rows_dont_file_tickets(self):
        now = time.time()
        row = _row("archive_load", 30000, now - 60, pe.ARCHIVE_COLD_MS)
        row["warmup"] = True
        _append_raw(self.events_path(), row)
        fake = FakeWt()
        pe._WT_RUNNER = fake
        self.assertEqual(pe.perf_ticket_check_once(now=now), "ok")
        self.assertFalse(any(call[0] == "add" for call in fake.calls))


if __name__ == "__main__":
    unittest.main()


class TestArchiveWarmLedger(PerfEventsTestBase):
    """Warm means: a build landed BEFORE the page started and within the
    warm window. A build the page itself triggered must not count."""

    def setUp(self):
        super().setUp()
        with pe._ARCHIVE_BUILD_LOCK:
            pe._ARCHIVE_BUILD_TS.clear()

    def test_no_builds_is_cold(self):
        self.assertFalse(pe.archive_is_warm(page_start_ts=1000.0, now=1010.0))

    def test_build_before_page_start_is_warm(self):
        pe.note_archive_build(900.0)
        self.assertTrue(pe.archive_is_warm(page_start_ts=1000.0, now=1010.0))

    def test_build_during_page_load_does_not_count(self):
        pe.note_archive_build(1005.0)  # the page's own cold scan finishing
        self.assertFalse(pe.archive_is_warm(page_start_ts=1000.0, now=1010.0))

    def test_build_older_than_window_is_cold(self):
        pe.note_archive_build(1000.0 - pe.WARM_WINDOW_S - 1)
        self.assertFalse(pe.archive_is_warm(page_start_ts=1000.0, now=1010.0))

    def test_record_event_derives_page_start_from_since_nav_ms(self):
        now = time.time()
        pe.note_archive_build(now - 30)  # 30s ago, i.e. before the page began
        # Page has been loading for 6s of archive time / 8s since navigation:
        # the build 30s ago predates it -> warm -> 1000ms threshold -> breach.
        res = pe.record_event("archive_load", 6000, detail={"since_nav_ms": 8000})
        self.assertTrue(res["warm"])
        self.assertTrue(res["breach"])
        # A build 2s ago happened during this page load -> must not flip warm
        # on a page that started 8s ago when no earlier build exists.
        with pe._ARCHIVE_BUILD_LOCK:
            pe._ARCHIVE_BUILD_TS.clear()
        pe.note_archive_build(now - 2)
        res = pe.record_event("archive_load", 6000, detail={"since_nav_ms": 8000})
        self.assertFalse(res["warm"])
        self.assertTrue(res["breach"])  # 6000 >= cold 5000


class TestTicketFilingRobustness(PerfEventsTestBase):
    """`wt add` prints FILED then dispatches a worker, which can outlive the
    subprocess timeout. The ticket exists either way; dedupe must arm."""

    def _seed_pattern(self):
        now = time.time()
        _append_raw(pe._events_path(), _row("archive_load", 112000, now - 60, 5000))

    def test_timeout_with_partial_output_still_records_ref(self):
        self._seed_pattern()
        calls = []
        def runner(args, timeout):
            calls.append(list(args))
            if args[0] == "add":
                return (pe._WT_TIMEOUT_RC, "FILED: CCC-77  [perf] slow archive load\n")
            return (1, "")
        with mock.patch.object(pe, "_WT_RUNNER", runner):
            self.assertEqual(pe.perf_ticket_check_once(), "filed:CCC-77")
            self.assertEqual(pe.perf_ticket_check_once(), "already-filed-today")
        self.assertEqual(pe._load_ticket_state()["last_ref"], "CCC-77")
        self.assertEqual(sum(1 for c in calls if c[0] == "add"), 1)

    def test_timeout_without_output_still_arms_same_day_dedupe(self):
        self._seed_pattern()
        def runner(args, timeout):
            return (pe._WT_TIMEOUT_RC, "") if args[0] == "add" else (1, "")
        with mock.patch.object(pe, "_WT_RUNNER", runner):
            self.assertEqual(pe.perf_ticket_check_once(), "filed:?")
            self.assertEqual(pe.perf_ticket_check_once(), "already-filed-today")

    def test_clean_failure_does_not_arm_dedupe(self):
        self._seed_pattern()
        def runner(args, timeout):
            return (1, "") if args[0] == "add" else (1, "")
        with mock.patch.object(pe, "_WT_RUNNER", runner):
            self.assertEqual(pe.perf_ticket_check_once(), "error")
            self.assertEqual(pe.perf_ticket_check_once(), "error")
        self.assertIsNone(pe._load_ticket_state().get("last_filed_date"))

    def test_add_uses_long_timeout(self):
        self._seed_pattern()
        seen = {}
        def runner(args, timeout):
            if args[0] == "add":
                seen["timeout"] = timeout
                return (0, "FILED: CCC-78  x")
            return (1, "")
        with mock.patch.object(pe, "_WT_RUNNER", runner):
            pe.perf_ticket_check_once()
        self.assertGreaterEqual(seen["timeout"], 120)


class TestSaturationDowngrade(PerfEventsTestBase):
    """CCC-1154 (B): breaches recorded while the machine was saturated are
    downgraded — they don't qualify a per-kind perf ticket, but roll up into
    ONE explicit 'machine saturated' alert so the overload stays visible."""

    def _saturated_row(self, kind, ms, ts, threshold_ms, load1=50.0, ncpu=8):
        row = _row(kind, ms, ts, threshold_ms)
        row["load1"] = load1
        row["ncpu"] = ncpu
        return row

    def test_record_event_stamps_load(self):
        with mock.patch.object(pe, "_machine_load", return_value=(42.0, 8)):
            result = pe.record_event("conv_open", 100)
        self.assertTrue(result["saturated"])
        row = json.loads(self.events_path().read_text().strip())
        self.assertEqual(row["load1"], 42.0)
        self.assertEqual(row["ncpu"], 8)

    def test_record_event_saturated_false_under_normal_load(self):
        with mock.patch.object(pe, "_machine_load", return_value=(3.0, 8)):
            result = pe.record_event("conv_open", 100)
        self.assertFalse(result["saturated"])

    def test_saturated_breaches_dont_qualify(self):
        now = time.time()
        events = [
            self._saturated_row("conv_open", 6000, now, pe.CONV_OPEN_MS),
            self._saturated_row("conv_open", 6500, now, pe.CONV_OPEN_MS),
            self._saturated_row("conv_open", 7000, now, pe.CONV_OPEN_MS),
        ]
        self.assertIsNone(pe.evaluate_breach_pattern(events))

    def test_saturated_single_3x_still_qualifies(self):
        # Condition (2): a single >=3x sample files even under saturation.
        now = time.time()
        events = [
            self._saturated_row("archive_load", 16000, now, pe.ARCHIVE_COLD_MS)
        ]
        pattern = pe.evaluate_breach_pattern(events)
        self.assertIsNotNone(pattern)
        self.assertEqual(pattern["kind"], "archive_load")

    def test_grossly_saturated_single_3x_does_not_qualify(self):
        # CCC-39: load1 ~173 on 8 cores stalled every endpoint; a lone 40s
        # conv_open then is machine contention, not a CCC regression.
        now = time.time()
        row = self._saturated_row(
            "conv_open", 40844, now, pe.CONV_OPEN_MS, load1=172.9
        )
        self.assertIsNone(pe.evaluate_breach_pattern([row]))
        sat = pe.evaluate_saturation([row])
        self.assertIsNotNone(sat)
        self.assertEqual(sat["count"], 1)

    def test_one_clean_plus_saturated_does_not_qualify(self):
        now = time.time()
        events = [
            _row("conv_open", 6000, now, pe.CONV_OPEN_MS),  # clean breach
            self._saturated_row("conv_open", 6500, now, pe.CONV_OPEN_MS),
            self._saturated_row("conv_open", 7000, now, pe.CONV_OPEN_MS),
        ]
        self.assertIsNone(pe.evaluate_breach_pattern(events))

    def test_warmup_saturated_rows_file_nothing(self):
        now = time.time()
        row = self._saturated_row("archive_load", 30000, now, pe.ARCHIVE_COLD_MS)
        row["warmup"] = True
        _append_raw(self.events_path(), row)
        _append_raw(self.events_path(), dict(row))
        fake = FakeWt()
        pe._WT_RUNNER = fake
        self.assertEqual(pe.perf_ticket_check_once(now=now), "ok")
        self.assertFalse(any(call[0] == "add" for call in fake.calls))

    def _seed_saturated_breaches(self, now):
        path = self.events_path()
        _append_raw(
            path,
            self._saturated_row("archive_load", 9000, now - 60, pe.ARCHIVE_COLD_MS),
        )
        _append_raw(
            path,
            self._saturated_row("conv_open", 8000, now - 30, pe.CONV_OPEN_MS),
        )

    def test_saturated_breaches_file_one_machine_ticket(self):
        now = time.time()
        self._seed_saturated_breaches(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake

        result = pe.perf_ticket_check_once(now=now)

        self.assertEqual(result, f"saturated-filed:{fake.add_ref}")
        add_calls = [c for c in fake.calls if c[0] == "add"]
        self.assertEqual(len(add_calls), 1)
        add = add_calls[0]
        title = add[add.index("--title") + 1]
        self.assertIn("machine saturated", title)
        self.assertIn("load", title)
        self.assertEqual(add[add.index("--priority") + 1], "p2")
        note = add[add.index("--note") + 1]
        self.assertIn("downgraded", note)
        state = pe._load_ticket_state()
        self.assertEqual(state["sat_ref"], fake.add_ref)
        self.assertEqual(state["sat_status"], "open")

    def test_open_saturation_ticket_refreshes_via_throttled_comment(self):
        now = time.time()
        self._seed_saturated_breaches(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake
        pe.perf_ticket_check_once(now=now)

        # Same ticket still open a moment later: no refile, no comment spam
        # (the filing itself armed the comment timestamp).
        self._seed_saturated_breaches(now + 5)
        result = pe.perf_ticket_check_once(now=now + 5)
        self.assertEqual(result, f"saturated-open:{fake.add_ref}")
        self.assertFalse(any(c[0] == "comment" for c in fake.calls))
        self.assertEqual(len([c for c in fake.calls if c[0] == "add"]), 1)

        # Past the comment interval the open ticket gets a 'still saturated'
        # refresh instead of a duplicate filing.
        later = now + pe.SAT_COMMENT_MIN_INTERVAL_S + 1
        self._seed_saturated_breaches(later)
        result = pe.perf_ticket_check_once(now=later)
        self.assertEqual(result, f"saturated-open:{fake.add_ref}")
        comments = [c for c in fake.calls if c[0] == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][1], fake.add_ref)
        self.assertIn("still saturated", comments[0][2])
        self.assertEqual(len([c for c in fake.calls if c[0] == "add"]), 1)

    def test_closed_saturation_ticket_dedupes_same_day_then_refiles(self):
        now = time.time()
        self._seed_saturated_breaches(now)
        fake = FakeWt()
        pe._WT_RUNNER = fake
        self.assertTrue(pe.perf_ticket_check_once(now=now).startswith("saturated-filed:"))

        # Closed, same day: still deduped by the calendar day.
        fake.find_status = "closed"
        self._seed_saturated_breaches(now + 60)
        self.assertEqual(
            pe.perf_ticket_check_once(now=now + 60), "saturated-filed-today"
        )
        self.assertEqual(len([c for c in fake.calls if c[0] == "add"]), 1)

        # Closed, next day, still saturated: a fresh alert files.
        next_day = now + 86400
        self._seed_saturated_breaches(next_day)
        fake.add_ref = "CCC-777"
        self.assertEqual(
            pe.perf_ticket_check_once(now=next_day), "saturated-filed:CCC-777"
        )
        self.assertEqual(len([c for c in fake.calls if c[0] == "add"]), 2)

    def test_saturation_does_not_suppress_real_perf_ticket(self):
        # A qualifying clean pattern files the normal p1; the saturation
        # path never runs — the two dedupe tracks are independent.
        now = time.time()
        path = self.events_path()
        _append_raw(path, _row("conv_open", 16000, now - 60, pe.CONV_OPEN_MS))
        _append_raw(
            path,
            self._saturated_row("archive_load", 9000, now - 30, pe.ARCHIVE_COLD_MS),
        )
        fake = FakeWt()
        pe._WT_RUNNER = fake
        self.assertEqual(
            pe.perf_ticket_check_once(now=now), f"filed:{fake.add_ref}"
        )
        state = pe._load_ticket_state()
        self.assertNotIn("sat_ref", state)
