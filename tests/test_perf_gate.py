"""scripts/perf_gate.py: the cmd: acceptance gate over perf-events.jsonl."""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "perf_gate", Path(__file__).resolve().parent.parent / "scripts" / "perf_gate.py")
perf_gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(perf_gate)


def _iso(minutes_ago):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.isoformat().replace("+00:00", "Z")


class PerfGateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "perf-events.jsonl"

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _run(self, *args):
        return perf_gate.main(["--events", str(self.path), "--commit", "", *args])

    def test_passes_when_p95_within_budget(self):
        self._write([{"ts": _iso(5), "kind": "conv_open", "ms": 300 + i} for i in range(20)])
        self.assertEqual(self._run("--kind", "conv_open", "--p95-ms", "1000"), 0)

    def test_fails_when_p95_over_budget(self):
        rows = [{"ts": _iso(5), "kind": "conv_open", "ms": 300} for _ in range(18)]
        rows += [{"ts": _iso(5), "kind": "conv_open", "ms": 9000} for _ in range(2)]
        self._write(rows)
        self.assertEqual(self._run("--kind", "conv_open", "--p95-ms", "1000"), 1)

    def test_fails_without_enough_samples(self):
        self._write([{"ts": _iso(5), "kind": "conv_open", "ms": 100}])
        self.assertEqual(self._run("--kind", "conv_open", "--p95-ms", "1000"), 1)

    def test_ignores_other_kinds_old_rows_and_excluded_status(self):
        rows = [{"ts": _iso(5), "kind": "list_read", "ms": 50,
                 "detail": {"status": 200}} for _ in range(20)]
        rows += [{"ts": _iso(5), "kind": "list_read", "ms": 1,
                  "detail": {"status": 304}} for _ in range(100)]
        rows += [{"ts": _iso(60 * 48), "kind": "list_read", "ms": 99999} for _ in range(50)]
        rows += [{"ts": _iso(5), "kind": "conv_open", "ms": 99999} for _ in range(50)]
        self._write(rows)
        self.assertEqual(self._run("--kind", "list_read", "--p95-ms", "100",
                                   "--exclude-status", "304"), 0)
        # 304s excluded => exactly the 20 real reads count
        self.assertEqual(self._run("--kind", "list_read", "--p95-ms", "100",
                                   "--exclude-status", "304", "--min-samples", "21"), 1)

    def test_where_scores_one_detail_slice(self):
        # CCC-144: prepared speaker starts are gated apart from cold ones,
        # and selection reads count in neither.
        rows = [{"ts": _iso(5), "kind": "tts_start", "ms": 30,
                 "detail": {"head": "ready", "target": "turn"}} for _ in range(20)]
        rows += [{"ts": _iso(5), "kind": "tts_start", "ms": 1500,
                  "detail": {"head": h, "target": "turn"}} for h in ("none", "generating") * 10]
        rows += [{"ts": _iso(5), "kind": "tts_start", "ms": 9000,
                  "detail": {"head": "none", "target": "selection"}} for _ in range(5)]
        self._write(rows)
        prepared = ("--kind", "tts_start", "--where", "head=ready", "--where", "target!=selection")
        cold = ("--kind", "tts_start", "--where", "head=none,generating", "--where", "target!=selection")
        self.assertEqual(self._run(*prepared, "--p95-ms", "300", "--min-samples", "20"), 0)
        self.assertEqual(self._run(*cold, "--p95-ms", "2000", "--min-samples", "20"), 0)
        self.assertEqual(self._run(*cold, "--p95-ms", "300", "--min-samples", "20"), 1)
        # unfiltered, the selection reads blow the budget
        self.assertEqual(self._run("--kind", "tts_start", "--p95-ms", "2000"), 1)
        with self.assertRaises(SystemExit):
            self._run("--kind", "tts_start", "--p95-ms", "1", "--where", "head")

    def test_unknown_commit_counts_no_samples(self):
        self._write([{"ts": _iso(5), "kind": "conv_open", "ms": 100, "build": "deadbeef"}
                     for _ in range(20)])
        rc = perf_gate.main(["--events", str(self.path), "--commit", "0" * 40,
                             "--repo", self._tmp.name, "--kind", "conv_open",
                             "--p95-ms", "1000"])
        self.assertEqual(rc, 1)


class SteadyStateTest(PerfGateTest):
    """--steady-state: warmup=false, load1<=ncpu, >=30 min after boot (Q968)."""

    def _row(self, ms, **over):
        r = {"ts": _iso(5), "kind": "list_read", "ms": ms, "warmup": False,
             "load1": 2.0, "ncpu": 16, "up_s": 7200}
        r.update(over)
        return r

    def test_excludes_warmup_load_and_boot_samples(self):
        calm = [self._row(300) for _ in range(5)]
        bad = ([self._row(9000, warmup=True)] + [self._row(9000, load1=30.0)]
               + [self._row(9000, up_s=60)])
        self._write(calm + bad)
        self.assertEqual(self._run("--kind", "list_read", "--p95-ms", "1000",
                                   "--min-samples", "5", "--steady-state"), 0)
        self.assertEqual(self._run("--kind", "list_read", "--p95-ms", "1000",
                                   "--min-samples", "5"), 1)

    def test_filtering_alone_does_not_pass(self):
        self._write([self._row(300)] + [self._row(9000, load1=30.0) for _ in range(9)])
        self.assertEqual(self._run("--kind", "list_read", "--p95-ms", "1000",
                                   "--min-samples", "5", "--steady-state"), 1)

    def test_reports_included_and_excluded_counts(self):
        import contextlib, io
        self._write([self._row(300) for _ in range(3)] + [self._row(9, warmup=True)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run("--kind", "list_read", "--p95-ms", "1000", "--min-samples", "1",
                      "--steady-state")
        self.assertIn("steady-state: 3 included, 1 excluded", buf.getvalue())
        self.assertIn("warmup=1", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
