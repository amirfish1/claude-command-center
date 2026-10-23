import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import cpu_accounting as ca


class RouteOfTests(unittest.TestCase):
    def test_collapses_session_ids(self):
        self.assertEqual(ca.route_of("/api/conversations/01a16571-84cd-7bd1-8fb4-2b388763b1b1?tail=120"),
                         "/api/conversations/:id")
        self.assertEqual(ca.route_of("/api/conversations/devincli-cookie-college"),
                         "/api/conversations/:id")
        self.assertEqual(ca.route_of("/api/session/1e78c0ba-64be-4622-b918-24440a0a367d/usage"),
                         "/api/session/:id/usage")

    def test_keeps_named_routes(self):
        self.assertEqual(ca.route_of("/api/conversations/list?window=7d"), "/api/conversations/list")
        self.assertEqual(ca.route_of("/api/sessions/live-activity"), "/api/sessions/live-activity")


class FlushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "cpu.jsonl"
        patches = [
            mock.patch.object(ca, "LOG_PATH", self.log),
            mock.patch.object(ca, "_routes", {}),
            mock.patch.object(ca, "_window_t0", 1000.0),
            mock.patch.object(ca, "_thread_cpu0", {}),
            mock.patch.object(ca, "_exited", {}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_no_flush_before_window(self):
        ca.record("/api/sessions", 0.010, 0.020, now=1000.0 + ca.WINDOW_S / 2)
        self.assertFalse(self.log.exists())

    def test_flush_ranks_routes_by_cpu_and_writes_jsonl(self):
        ca.record("/api/sessions", 0.010, 0.020, now=1001.0)
        ca.record("/api/conversations/list", 0.300, 0.900, now=1002.0)
        ca.record("/api/conversations/list", 0.200, 0.400, now=1003.0)
        with mock.patch("builtins.print"):
            row = ca.maybe_flush(now=1000.0 + ca.WINDOW_S + 1)
        self.assertEqual(row["routes"][0], {"route": "/api/conversations/list",
                                            "cpu_ms": 500, "wall_ms": 1300, "n": 2})
        self.assertEqual(row["req_cpu_ms"], 510)
        on_disk = json.loads(self.log.read_text().strip())
        self.assertEqual(on_disk["routes"][0]["route"], "/api/conversations/list")
        # Window resets.
        self.assertIsNone(ca.maybe_flush(now=1000.0 + ca.WINDOW_S + 2))

    def test_background_thread_cpu_is_attributed_by_name(self):
        stop = threading.Event()

        def _busy_loop():
            while not stop.is_set():
                sum(range(2000))

        t = threading.Thread(target=_busy_loop, daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join()))
        with mock.patch("builtins.print"):
            ca.maybe_flush(now=1000.0 + ca.WINDOW_S)          # baseline snapshot
            time.sleep(0.4)
            row = ca.maybe_flush(now=1000.0 + 2 * ca.WINDOW_S + 1)
        names = {r["name"]: r["cpu_ms"] for r in row["threads"]}
        self.assertIn("_busy_loop", names)
        self.assertGreater(names["_busy_loop"], 50)

    def test_short_lived_thread_reports_cpu_at_exit(self):
        ca.install_thread_exit_accounting()
        ca.install_thread_exit_accounting()  # idempotent

        def _burst():
            end = time.thread_time() + 0.15
            while time.thread_time() < end:
                pass

        with mock.patch.object(ca, "_window_t0", time.monotonic() - 1):
            t = threading.Thread(target=_burst)
            t.start()
            t.join()
            with mock.patch("builtins.print"):
                row = ca.maybe_flush(now=time.monotonic() + ca.WINDOW_S)
        names = {r["name"]: r["cpu_ms"] for r in row["threads"]}
        self.assertGreater(names.get("_burst", 0), 100)
        self.assertIn("other_cpu_ms", row)

    def test_thread_cpu_parses_comm_with_spaces(self):
        stat = "123 (my (odd) name) S " + " ".join(["0"] * 10) + " 250 50 0 0"
        with mock.patch("builtins.open", mock.mock_open(read_data=stat.encode())):
            self.assertAlmostEqual(ca._thread_cpu_s(123), 300 / ca._clk_tck())


if __name__ == "__main__":
    unittest.main()
