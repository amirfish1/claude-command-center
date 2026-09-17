"""Unit tests for Windows compatibility (CCC-GH-116)."""

import os
from pathlib import Path
import subprocess
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWindowsCompatibility(unittest.TestCase):
    def test_fcntl_shim_exports_required_symbols(self):
        import fcntl
        self.assertTrue(hasattr(fcntl, "flock"))
        self.assertTrue(hasattr(fcntl, "fcntl"))
        self.assertTrue(hasattr(fcntl, "ioctl"))
        self.assertTrue(hasattr(fcntl, "LOCK_SH"))
        self.assertTrue(hasattr(fcntl, "LOCK_EX"))
        self.assertTrue(hasattr(fcntl, "LOCK_NB"))
        self.assertTrue(hasattr(fcntl, "LOCK_UN"))
        self.assertTrue(hasattr(fcntl, "F_GETFL"))
        self.assertTrue(hasattr(fcntl, "F_SETFL"))

    def test_watchtower_msg_af_unix_guard(self):
        src = (REPO_ROOT / "ccc_server" / "watchtower_msg.py").read_text(encoding="utf-8")
        self.assertIn('not hasattr(socket, "AF_UNIX")', src)

    def test_ccc_peer_uds_af_unix_guard(self):
        src = (REPO_ROOT / "ccc_peer_uds.py").read_text(encoding="utf-8")
        self.assertIn('not hasattr(socket, "AF_UNIX")', src)

    def test_run_ps1_sets_utf8_env(self):
        src = (REPO_ROOT / "run.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:PYTHONUTF8 = "1"', src)
        self.assertIn('$env:PYTHONIOENCODING = "utf-8"', src)

    def test_codex_app_server_detached_on_windows(self):
        src = (REPO_ROOT / "ccc_server" / "codex.py").read_text(encoding="utf-8")
        self.assertIn('sys.platform == "win32"', src)
        self.assertIn("DETACHED_PROCESS", src)
        self.assertIn("popen_kwargs", src)


if __name__ == "__main__":
    unittest.main()
