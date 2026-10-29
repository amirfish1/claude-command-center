import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server  # noqa: F401  (import order: avoids circular import)
from ccc_server import credential_broker, engines


class AntigravityAuthPromptTests(unittest.TestCase):
    def _run(self, script):
        d = tempfile.mkdtemp()
        log = Path(d) / "out.log"
        with open(log, "w") as fh:
            proc = subprocess.Popen([sys.executable, "-c", script], stdout=fh, stderr=subprocess.STDOUT)
        try:
            return engines._antigravity_auth_prompt_seen(proc, log, wait_seconds=3.0)
        finally:
            proc.kill()
            proc.wait()

    def test_detects_login_prompt(self):
        self.assertTrue(self._run(
            "import time;print('Authentication required. Please visit',flush=True);time.sleep(30)"))

    def test_normal_run_is_not_flagged(self):
        self.assertFalse(self._run("print('hello')"))


class AntigravityAuthRelayTests(unittest.TestCase):
    """A logged-out agy reads the sign-in code from its terminal. The pty
    spawn must deliver the dashboard-entered code, and must not leave a
    signed-in run waiting on input."""

    def _spawn(self, script):
        d = tempfile.mkdtemp()
        log = Path(d) / "out.log"
        fh = open(log, "w")
        self.addCleanup(fh.close)
        proc = engines._antigravity_popen([sys.executable, "-c", script], d, fh, log, "sid-test")
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc, log

    def test_code_from_dashboard_reaches_agy(self):
        proc, log = self._spawn(
            "import sys;print('Authentication required. Please visit',flush=True);"
            "print('GOT:' + sys.stdin.readline().strip(), flush=True)")
        broker = credential_broker.broker()

        def answer():
            for _ in range(80):
                rows = [r for r in broker.pending() if r["name"] == "antigravity-sign-in"]
                if rows:
                    broker.resolve({"id": rows[0]["id"], "value": " 4/test-code "})
                    return
                time.sleep(0.1)

        threading.Thread(target=answer, daemon=True).start()
        proc.wait(timeout=15)
        self.assertIn("GOT:4/test-code", log.read_text())

    def test_signed_in_run_gets_end_of_input(self):
        proc, log = self._spawn(
            "import sys;print('hello',flush=True)\n"
            "try:\n sys.stdin.read()\nexcept OSError:\n pass\nprint('done',flush=True)")
        proc.wait(timeout=15)
        self.assertIn("done", log.read_text())


if __name__ == "__main__":
    unittest.main()
