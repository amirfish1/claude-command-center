"""Leaked/runaway process detector feeding the confirm-to-kill prompt."""

import importlib
import unittest
from unittest import mock


def row(pid, ppid, cmd, rss_mb=10, etime_min=1.0, cpu=0.0):
    return {"pid": pid, "ppid": ppid, "rss_mb": rss_mb, "cpu": cpu,
            "etime_min": etime_min, "tty": "?", "cmd": cmd}


class AnomalyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_hung_tsc_tree_is_one_anomaly_with_wrapper_pids(self):
        rows = [
            row(10, 1, "/bin/bash -c npm exec tsc --noEmit", etime_min=1170),
            row(11, 10, "npm exec tsc --noEmit -p tsconfig.json", etime_min=1170),
            row(12, 11, "sh -c tsc --noEmit -p tsconfig.json", etime_min=1170),
            row(13, 12, "node /app/node_modules/.bin/tsc --noEmit -p tsconfig.json",
                rss_mb=3400, etime_min=1170),
        ]
        out = self.server._sys_anomalies(rows, total_mb=15000)
        self.assertEqual(len(out), 1)
        a = out[0]
        self.assertEqual((a["pid"], a["kind"]), (13, "runaway"))
        # wrappers die with it; the launching bash shell does not
        self.assertEqual(a["tree_pids"], [11, 12, 13])

    def test_young_or_small_processes_are_ignored(self):
        rows = [
            row(20, 1, "node /x/.bin/tsc --noEmit", etime_min=5, rss_mb=3000),
            row(21, 1, "python3 worker.py", etime_min=600, rss_mb=500),
        ]
        self.assertEqual(self.server._sys_anomalies(rows, total_mb=15000), [])

    def test_memory_hog_flagged_but_sessions_and_daemons_protected(self):
        rows = [
            row(30, 1, "/usr/bin/some-leaky-tool --x", rss_mb=4000, etime_min=200),
            row(31, 1, "/home/u/.local/bin/claude -p", rss_mb=4000, etime_min=900),
            row(32, 1, "/usr/bin/postgres -D /d", rss_mb=4000, etime_min=900),
        ]
        out = self.server._sys_anomalies(rows, total_mb=15000)
        self.assertEqual([(a["pid"], a["kind"]) for a in out], [(30, "memory")])

    def test_kill_only_touches_currently_flagged_trees(self):
        rows = [row(40, 1, "node /x/.bin/tsc --noEmit", etime_min=500, rss_mb=3000)]
        srv = self.server
        with mock.patch.object(srv, "_sys_process_rows", return_value=rows), \
             mock.patch.object(srv, "_sys_memory", return_value={"total_mb": 15000}), \
             mock.patch.object(srv, "system_process_kill",
                               return_value={"ok": True, "killed": [40], "blocked": [], "errors": {}}) as k:
            res = srv.system_anomaly_kill([40, 4242])
        k.assert_called_once_with([40], force=False)
        self.assertIn(4242, res["blocked"])


if __name__ == "__main__":
    unittest.main()
