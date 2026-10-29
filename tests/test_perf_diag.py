"""CCC-142: server-only diagnostic perf samples (list_server, archive_rebuild).

They share the perf-events JSONL so scripts/perf_gate.py can read them, but
must never produce a breach verdict, a summary row, or a self-filed ticket.
"""
import json
import tempfile
import unittest
from pathlib import Path

import server  # noqa: F401 -- core proxy needs server's state dir first
from ccc_server import perf_events as pe


class TestRecordDiag(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        orig = pe._STATE_DIR_OVERRIDE
        pe._STATE_DIR_OVERRIDE = Path(self._tmp.name)
        self.addCleanup(setattr, pe, "_STATE_DIR_OVERRIDE", orig)

    def rows(self):
        path = pe._events_path()
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def test_diag_kinds_append_without_breach_fields(self):
        pe.record_diag("list_server", 1234.4, {"source_ms": 1200.0})
        pe.record_diag("archive_rebuild", 9000, {"worker_ms": 8000})
        rows = self.rows()
        self.assertEqual([r["kind"] for r in rows], ["list_server", "archive_rebuild"])
        self.assertEqual(rows[0]["ms"], 1234)
        self.assertEqual(rows[0]["detail"]["source_ms"], 1200.0)
        self.assertNotIn("breach", rows[0])
        self.assertNotIn("threshold_ms", rows[0])

    def test_diag_kinds_stay_out_of_watchdog_kinds(self):
        for kind in pe._DIAG_KINDS:
            self.assertNotIn(kind, pe._VALID_KINDS)
            with self.assertRaises(ValueError):
                pe.record_event(kind, 1)

    def test_unknown_or_bad_values_are_dropped_silently(self):
        pe.record_diag("conv_open", 10)
        pe.record_diag("list_server", -1)
        pe.record_diag("list_server", float("nan"))
        pe.record_diag("list_server", "x")
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
