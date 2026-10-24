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

    def test_unknown_commit_counts_no_samples(self):
        self._write([{"ts": _iso(5), "kind": "conv_open", "ms": 100, "build": "deadbeef"}
                     for _ in range(20)])
        rc = perf_gate.main(["--events", str(self.path), "--commit", "0" * 40,
                             "--repo", self._tmp.name, "--kind", "conv_open",
                             "--p95-ms", "1000"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
