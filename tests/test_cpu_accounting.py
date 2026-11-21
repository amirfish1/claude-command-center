import json
import sys
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

    def test_request_accounting_does_not_wait_for_thread_snapshot(self):
        sampling = threading.Event()
        release = threading.Event()
        recorded = threading.Event()
        first_window = []
        due = 1000.0 + ca.WINDOW_S

        def snapshot():
            sampling.set()
            release.wait(5)
            return {}

        def finish_request():
            ca.record("/ready", 0.002, 0.003, now=due + 1)
            recorded.set()

        ca.record("/initial", 0.004, 0.005, now=1001.0)
        with mock.patch.object(ca, "_thread_snapshot", snapshot), mock.patch("builtins.print"):
            flusher = threading.Thread(target=lambda: first_window.append(ca.maybe_flush(now=due)))
            request = threading.Thread(target=finish_request)
            flusher.start()
            try:
                self.assertTrue(sampling.wait(2))
                request.start()
                self.assertTrue(recorded.wait(2))
            finally:
                release.set()
                flusher.join()
                if request.ident is not None:
                    request.join()
            second_window = ca.maybe_flush(now=due + ca.WINDOW_S)

        self.assertEqual(first_window[0]["req_cpu_ms"], 4)
        self.assertEqual(second_window["routes"], [
            {"route": "/ready", "cpu_ms": 2, "wall_ms": 3, "n": 1},
        ])

    def test_snapshot_does_not_read_request_thread_stat(self):
        threads = [
            mock.Mock(native_id=10),
            mock.Mock(native_id=11),
            mock.Mock(native_id=12),
        ]
        for thread, name in zip(threads, ["http-admission", "process_request_thread",
                                          "Thread-78 (process_request_thread)"]):
            thread.name = name
        with mock.patch.object(ca.threading, "enumerate", return_value=threads), \
                mock.patch.object(ca, "_thread_cpu_s", return_value=0.25) as read_cpu:
            self.assertEqual(ca._thread_snapshot(), {10: ("http-admission", 0.25)})
        read_cpu.assert_called_once_with(10)

    @unittest.skipUnless(sys.platform.startswith("linux"), "live thread CPU snapshots require Linux /proc")
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

    @unittest.skipUnless(sys.platform.startswith("linux"), "live thread CPU snapshots require Linux /proc")
    def test_thread_born_inside_the_window_is_attributed(self):
        """Boot-time background jobs start after the first snapshot (CCC-142)."""
        with mock.patch("builtins.print"):
            ca.maybe_flush(now=1000.0 + ca.WINDOW_S)          # baseline, before the thread exists
        stop = threading.Event()

        def _boot_job():
            while not stop.is_set():
                sum(range(2000))

        t = threading.Thread(target=_boot_job, daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join()))
        time.sleep(0.4)
        with mock.patch("builtins.print"):
            row = ca.maybe_flush(now=1000.0 + 2 * ca.WINDOW_S + 1)
        names = {r["name"]: r["cpu_ms"] for r in row["threads"]}
        self.assertGreater(names.get("_boot_job", 0), 50)

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
