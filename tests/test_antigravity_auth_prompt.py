import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import server  # noqa: F401  (import order: avoids circular import)
from ccc_server import engines


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


if __name__ == "__main__":
    unittest.main()
