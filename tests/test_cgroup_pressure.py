"""CCC-713: gc_pause and slow list_server rows carry cgroup throttle/pressure
deltas, so an off-CPU stall can be told apart from CCC's own work."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- core proxy needs server's state dir first
from ccc_server import cgroup_pressure as cp
from ccc_server import perf_events as pe


def _write_cgroup(root, *, throttled_us, nr_throttled, high, cpu_some, mem_some,
                  mem_full, io_some):
    (root / "cpu.stat").write_text(
        "usage_usec 100\nnr_periods 9\n"
        f"nr_throttled {nr_throttled}\nthrottled_usec {throttled_us}\n")
    (root / "memory.events").write_text(f"low 0\nhigh {high}\nmax 0\noom 0\n")
    (root / "cpu.pressure").write_text(
        f"some avg10=0.00 avg60=0.00 avg300=0.00 total={cpu_some}\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=1\n")
    (root / "memory.pressure").write_text(
        f"some avg10=0.00 avg60=0.00 avg300=0.00 total={mem_some}\n"
        f"full avg10=0.00 avg60=0.00 avg300=0.00 total={mem_full}\n")
    (root / "io.pressure").write_text(
        f"some avg10=0.00 avg60=0.00 avg300=0.00 total={io_some}\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")


class CgroupTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(cp.close_files)
        state = {"dir": str(self.root), "resolved": True, "thread": None}
        patcher = mock.patch.dict(cp._state, state)
        patcher.start()
        self.addCleanup(patcher.stop)
        saved = list(cp._ring)
        cp._ring.clear()
        self.addCleanup(lambda: (cp._ring.clear(), cp._ring.extend(saved)))
        self.clock = [1000.0]
        clock = mock.patch.object(cp.time, "monotonic", lambda: self.clock[0])
        clock.start()
        self.addCleanup(clock.stop)

    def write(self, **values):
        base = dict(throttled_us=0, nr_throttled=0, high=0, cpu_some=0,
                    mem_some=0, mem_full=0, io_some=0)
        base.update(values)
        _write_cgroup(self.root, **base)


class TestAttribution(CgroupTestCase):
    def test_read_parses_cgroup_v2_files(self):
        self.write(throttled_us=1231047895, nr_throttled=10803, high=44067,
                   cpu_some=5715670123, mem_some=749345612, mem_full=708750515,
                   io_some=42)
        reading = cp.read()
        self.assertEqual(reading["throttled_us"], 1231047895)
        self.assertEqual(reading["nr_throttled"], 10803)
        self.assertEqual(reading["mem_high"], 44067)
        self.assertEqual(reading["cpu_some_us"], 5715670123)
        self.assertEqual(reading["memory_some_us"], 749345612)
        self.assertEqual(reading["memory_full_us"], 708750515)
        self.assertEqual(reading["io_some_us"], 42)

    def test_deltas_start_from_the_reading_before_the_event(self):
        self.write(throttled_us=1_000_000, nr_throttled=5, high=10, cpu_some=2_000_000)
        cp._sample_once()                       # t=1000.0, before the event
        self.clock[0] = 1001.0
        self.write(throttled_us=1_100_000, nr_throttled=6, high=10, cpu_some=2_100_000)
        cp._sample_once()                       # t=1001.0, inside the event
        self.clock[0] = 1001.5
        # A 1.2 s event ending now began at 1000.3: the 1000.0 reading is the
        # newest one taken before it, so the in-event reading is not the base.
        self.write(throttled_us=1_945_000, nr_throttled=8, high=13,
                   cpu_some=2_990_000, mem_some=4_000, io_some=500)
        got = cp.attribution(1.2)
        self.assertEqual(got["window_ms"], 1500.0)
        self.assertEqual(got["lead_ms"], 300.0)
        self.assertEqual(got["throttled_ms"], 945.0)
        self.assertEqual(got["nr_throttled"], 3)
        self.assertEqual(got["mem_high"], 3)
        self.assertEqual(got["cpu_some_ms"], 990.0)
        self.assertEqual(got["mem_some_ms"], 4.0)
        self.assertEqual(got["io_some_ms"], 0.5)

    def test_event_older_than_the_ring_reports_its_shorter_window(self):
        self.write()
        cp._sample_once()
        self.clock[0] = 1002.0
        got = cp.attribution(10.0)
        self.assertEqual(got["window_ms"], 2000.0)
        self.assertEqual(got["lead_ms"], -8000.0)

    def test_no_readings_or_no_cgroup_gives_none(self):
        self.write()
        self.assertIsNone(cp.attribution(1.0))     # sampler never ran
        cp._state["dir"] = None
        self.assertIsNone(cp.read())
        self.assertFalse(cp.start_sampler())
        self.assertIsNone(cp._state["thread"])

    def test_unresolvable_cgroup_path_is_unsupported(self):
        cp._state.update(dir=None, resolved=False)
        with mock.patch("builtins.open", side_effect=OSError("no /proc")):
            self.assertIsNone(cp._cgroup_dir())
        self.assertIsNone(cp.read())


class TestRecordDiagCarriesCgroup(CgroupTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = pe._STATE_DIR_OVERRIDE
        pe._STATE_DIR_OVERRIDE = Path(tmp.name)
        self.addCleanup(setattr, pe, "_STATE_DIR_OVERRIDE", orig)
        self.write(throttled_us=0)
        cp._sample_once()
        self.clock[0] = 1002.0
        self.write(throttled_us=900_000, nr_throttled=2)

    def rows(self):
        path = pe._events_path()
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def test_gc_pause_and_slow_list_reads_get_detail_cgroup(self):
        gc_detail = {"generation": 0, "cpu_ms": 292.0}
        pe.record_diag("gc_pause", 1237, gc_detail)
        pe.record_diag("list_server", 900, {"window": "7d"})
        gc_row, list_row = self.rows()
        self.assertEqual(gc_row["detail"]["cgroup"]["throttled_ms"], 900.0)
        self.assertEqual(gc_row["detail"]["cgroup"]["nr_throttled"], 2)
        self.assertEqual(gc_row["detail"]["cpu_ms"], 292.0)
        self.assertEqual(list_row["detail"]["cgroup"]["throttled_ms"], 900.0)
        self.assertNotIn("cgroup", gc_detail)    # caller's dict untouched

    def test_fast_list_reads_and_other_kinds_skip_the_reading(self):
        with mock.patch.object(cp, "read", wraps=cp.read) as spy:
            pe.record_diag("list_server", 120, {"window": "1d"})
            pe.record_diag("archive_rebuild", 9000, {"worker_ms": 8000})
            pe.record_diag("live_read", 5000, {"x": 1})
        self.assertEqual(spy.call_count, 0)
        self.assertTrue(all("cgroup" not in (r["detail"] or {}) for r in self.rows()))

    def test_unsupported_host_records_the_row_unchanged(self):
        cp._state["dir"] = None
        pe.record_diag("gc_pause", 300, {"generation": 2})
        self.assertEqual(self.rows()[0]["detail"], {"generation": 2})


if __name__ == "__main__":
    unittest.main()
