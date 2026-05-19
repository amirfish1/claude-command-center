"""Smoke tests for scripts/install.sh.

The bar matches `tests/test_smoke.py`: existence, executable bit, sane
shebang, optional shellcheck pass. We don't exercise the script end-to-end
because it clones a repo, hits the network, and launches a server — too
heavy for a smoke test.
"""
import os
import shutil
import stat
import subprocess
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "install.sh")


class TestInstallScript(unittest.TestCase):
    def test_install_script_exists(self):
        self.assertTrue(
            os.path.isfile(INSTALL_SCRIPT),
            "scripts/install.sh must exist",
        )

    def test_install_script_is_executable(self):
        mode = os.stat(INSTALL_SCRIPT).st_mode
        self.assertTrue(
            mode & stat.S_IXUSR,
            "scripts/install.sh must have the executable bit set",
        )

    def test_install_script_has_bash_shebang(self):
        with open(INSTALL_SCRIPT, "rb") as fh:
            first_line = fh.readline().rstrip(b"\n").decode("utf-8", "replace")
        self.assertIn(
            first_line,
            ("#!/usr/bin/env bash", "#!/bin/bash"),
            f"unexpected shebang: {first_line!r}",
        )

    def test_install_script_passes_shellcheck_when_available(self):
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck not installed; skipping lint check")
        result = subprocess.run(
            ["shellcheck", INSTALL_SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"shellcheck failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
