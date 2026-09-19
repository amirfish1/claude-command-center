"""A second worker must never unlink a live worker's socket.

Regression: under heavy load a healthy worker missed its health probe, a
second instance treated it as dead, unlinked the socket, then crashed. The
first worker stayed alive on an orphaned socket and the dashboard reported
"Execution worker is down".
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import ccc_worker


class WorkerPidAliveTests(unittest.TestCase):
    def _pidfile(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "worker.pid"
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_or_garbage_pidfile_is_not_alive(self):
        self.assertFalse(ccc_worker._worker_pid_alive(Path(tempfile.mkdtemp()) / "nope"))
        self.assertFalse(ccc_worker._worker_pid_alive(self._pidfile("not-a-pid\n")))

    def test_own_pid_is_not_another_worker(self):
        self.assertFalse(ccc_worker._worker_pid_alive(self._pidfile(f"{os.getpid()}\n")))

    def test_dead_pid_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(ccc_worker._worker_pid_alive(self._pidfile(f"{proc.pid}\n")))

    @unittest.skipUnless(os.path.isdir("/proc"), "needs /proc")
    def test_unrelated_live_process_is_not_a_worker(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertFalse(ccc_worker._worker_pid_alive(self._pidfile(f"{proc.pid}\n")))
        finally:
            proc.kill()
            proc.wait()

    @unittest.skipUnless(os.path.isdir("/proc"), "needs /proc")
    def test_live_ccc_worker_process_is_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "ccc_worker.py"])
        try:
            self.assertTrue(ccc_worker._worker_pid_alive(self._pidfile(f"{proc.pid}\n")))
        finally:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    unittest.main()
