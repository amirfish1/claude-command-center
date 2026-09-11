"""Regression tests for CCC's own --port and --host CLI flags.

Previously only the env var PORT (and module-level default 8090) controlled
where the dashboard bound; ephemeral verification servers that passed
`--port 8091` still bound to 8090 and collided with the primary instance.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import unittest

CCC_ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_health(port: int, timeout: float = 20.0):
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2.0
            ) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError) as e:
            last_err = e
        time.sleep(0.25)
    raise RuntimeError(f"server not healthy on port {port}: {last_err}")


class TestPortFlagBinding(unittest.TestCase):
    def _spawn(self, port: int, extra_args=None, extra_env=None):
        tmp = tempfile.mkdtemp(prefix="ccc-port-flag-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        home = Path(tmp) / "home"
        home.mkdir(parents=True)
        state_dir = home / ".claude" / "command-center"
        state_dir.mkdir(parents=True)
        (state_dir / "fleet.json").write_text('{"automap": false}\n')

        env = {
            **os.environ,
            "HOME": str(home),
            "CCC_EPHEMERAL": "1",
            "CCC_SKIP_SKILL_INSTALL": "1",
            "CCC_TELEMETRY_DISABLED": "1",
            "CCC_CHAT_ORCHESTRATOR": "builtin",
            "CCC_WARM_CACHE_ON_STARTUP": "0",
        }
        env.pop("CCC_SSH_HOST", None)
        env.update(extra_env or {})

        log_path = Path(tmp) / "server.log"
        log_fh = open(log_path, "w")
        args = [sys.executable, str(CCC_ROOT / "server.py")]
        if extra_args:
            args.extend(extra_args)
        proc = subprocess.Popen(
            args,
            cwd=str(CCC_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        self.addCleanup(self._stop_proc, proc, log_fh)
        return proc, log_path, log_fh

    def _stop_proc(self, proc, log_fh):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        try:
            log_fh.close()
        except Exception:
            pass

    def test_port_flag_overrides_default(self):
        """`--port <n>` makes the dashboard bind and report port n."""
        port = free_port()
        proc, log_path, log_fh = self._spawn(port, extra_args=["--port", str(port)])
        _wait_for_health(port)
        self.assertIsNone(proc.poll(), "server exited unexpectedly")
        # Stop before reading log so output is flushed.
        self._stop_proc(proc, log_fh)
        log = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn(f"Command Center running at http://localhost:{port}", log)

    def test_port_flag_env_var_still_default(self):
        """`PORT=...` still works when no --port flag is passed."""
        port = free_port()
        proc, log_path, log_fh = self._spawn(port, extra_env={"PORT": str(port)})
        _wait_for_health(port)
        self.assertIsNone(proc.poll(), "server exited unexpectedly")
        self._stop_proc(proc, log_fh)
        log = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn(f"Command Center running at http://localhost:{port}", log)

    def test_invalid_port_exits_cleanly(self):
        """A non-numeric --port produces a clean usage exit."""
        port = free_port()
        proc, log_path, log_fh = self._spawn(port, extra_args=["--port", "not-a-number"])
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
