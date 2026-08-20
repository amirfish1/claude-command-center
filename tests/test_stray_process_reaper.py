"""Reap leaked CCC/worker processes so a zombie can't run stale code forever.

Incident: a 5-day-old orphaned server.py process (leaked from a federation
test harness, faked $HOME under TMPDIR) held the pending-inputs watcher's
flock and ran pre-fix code, injecting "continue" into a live Codex session
118 times over ~2h. The periodic reaper here kills stray server.py /
ccc_worker.py processes older than 10 minutes that are not recognized as the
dashboard's or worker's own launchd-managed PID, so a leaked process can no
longer sit around running code nobody restarted.

Never sends a real signal or spawns a real process here -- subprocess.run,
os.kill, os.getpid, and time.time are all mocked.
"""
from __future__ import annotations

import importlib
import sys
import time
import unittest
from datetime import datetime
from unittest import mock


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def _lstart_for_age(now, age_s):
    return datetime.fromtimestamp(now - age_s).strftime("%a %b %d %H:%M:%S %Y")


class _RunResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _make_run(*, pgrep_pids=None, ps_args=None, ps_lstart=None, launchctl=None):
    """Build a subprocess.run side_effect keyed by the exact argv shapes the
    reaper is expected to use: pgrep -f <path>, ps -p <pid> -o args=/lstart=,
    launchctl list <label>."""
    pgrep_pids = pgrep_pids or {}
    ps_args = ps_args or {}
    ps_lstart = ps_lstart or {}
    launchctl = launchctl or {}

    def _run(cmd, **kwargs):
        if cmd[0] == "pgrep":
            path = cmd[-1]
            pids = pgrep_pids.get(path, [])
            return _RunResult("\n".join(str(p) for p in pids))
        if cmd[0] == "ps" and cmd[-1] == "args=":
            pid = int(cmd[2])
            return _RunResult(ps_args.get(pid, ""))
        if cmd[0] == "ps" and cmd[-1] == "lstart=":
            pid = int(cmd[2])
            return _RunResult(ps_lstart.get(pid, ""))
        if cmd[0] == "launchctl":
            label = cmd[-1]
            return _RunResult(launchctl.get(label, ""))
        return _RunResult("")

    return _run


class StrayProcessReaperTests(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()
        self.server._STRAY_REAPER_LOG.clear()
        self.server._STRAY_REAPER_LAST_RUN["ts"] = 0.0

    def _paths(self):
        return self.server._stray_reaper_target_paths()

    def test_self_pid_never_reaped(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [4242]},
            ps_args={4242: "python3 " + server_path},
            ps_lstart={4242: _lstart_for_age(now, 3600)},
        )
        kill = mock.Mock()
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=4242):
            self.server._run_stray_process_reaper_once(now=now)
        kill.assert_not_called()
        self.assertEqual(self.server._STRAY_REAPER_LOG, [])

    def test_archive_refresh_worker_never_reaped_regardless_of_age(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [5555]},
            ps_args={5555: "python3 " + server_path + " --archive-refresh-worker"},
            ps_lstart={5555: _lstart_for_age(now, 24 * 3600)},
        )
        kill = mock.Mock()
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=1):
            self.server._run_stray_process_reaper_once(now=now)
        kill.assert_not_called()
        self.assertEqual(self.server._STRAY_REAPER_LOG, [])

    def test_launchctl_listed_pid_never_reaped(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [7777]},
            ps_args={7777: "python3 " + server_path},
            ps_lstart={7777: _lstart_for_age(now, 24 * 3600)},
            launchctl={"com.github.claude-command-center": '"PID" = 7777;\n'},
        )
        kill = mock.Mock()
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=1):
            self.server._run_stray_process_reaper_once(now=now)
        kill.assert_not_called()
        self.assertEqual(self.server._STRAY_REAPER_LOG, [])

    def test_young_stray_left_alone(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [9001]},
            ps_args={9001: "python3 " + server_path},
            ps_lstart={9001: _lstart_for_age(now, 30)},
        )
        kill = mock.Mock()
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=1):
            self.server._run_stray_process_reaper_once(now=now)
        kill.assert_not_called()
        self.assertEqual(self.server._STRAY_REAPER_LOG, [])

    def test_stray_older_than_10min_gets_sigterm_and_dies(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [9002]},
            ps_args={9002: "python3 " + server_path},
            ps_lstart={9002: _lstart_for_age(now, 900)},
        )
        # First os.kill(9002, 0) liveness poll (right after SIGTERM) reports dead.
        kill = mock.Mock(side_effect=[None, ProcessLookupError()])

        def _kill(pid, sig):
            if sig == 0:
                raise ProcessLookupError()
            return None

        kill = mock.Mock(side_effect=_kill)
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=1), \
             mock.patch.object(self.server.time, "sleep", return_value=None):
            self.server._run_stray_process_reaper_once(now=now)
        calls = [c.args for c in kill.call_args_list]
        self.assertIn((9002, self.server.signal.SIGTERM), calls)
        self.assertNotIn((9002, self.server.signal.SIGKILL), calls)
        self.assertEqual(len(self.server._STRAY_REAPER_LOG), 1)
        entry = self.server._STRAY_REAPER_LOG[0]
        self.assertEqual(entry["pid"], 9002)
        self.assertIn(server_path, entry["args"])
        self.assertGreaterEqual(entry["age_s"], 900)

    def test_stray_older_than_10min_gets_sigkill_if_still_alive(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = _make_run(
            pgrep_pids={server_path: [9003]},
            ps_args={9003: "python3 " + server_path},
            ps_lstart={9003: _lstart_for_age(now, 900)},
        )

        def _kill(pid, sig):
            if sig == 0:
                return None  # still alive on every liveness poll
            return None

        kill = mock.Mock(side_effect=_kill)
        with mock.patch.object(self.server.subprocess, "run", side_effect=run), \
             mock.patch.object(self.server.os, "kill", kill), \
             mock.patch.object(self.server.os, "getpid", return_value=1), \
             mock.patch.object(self.server.time, "sleep", return_value=None):
            self.server._run_stray_process_reaper_once(now=now)
        calls = [c.args for c in kill.call_args_list]
        self.assertIn((9003, self.server.signal.SIGTERM), calls)
        self.assertIn((9003, self.server.signal.SIGKILL), calls)
        self.assertEqual(len(self.server._STRAY_REAPER_LOG), 1)

    def test_scan_is_throttled_to_once_per_60s(self):
        now = time.time()
        server_path, _worker_path = self._paths()
        run = mock.Mock(side_effect=_make_run(pgrep_pids={server_path: []}))
        with mock.patch.object(self.server.subprocess, "run", run), \
             mock.patch.object(self.server.os, "getpid", return_value=1):
            self.server._run_stray_process_reaper_once(now=now)
            first_call_count = run.call_count
            self.assertGreater(first_call_count, 0)
            self.server._run_stray_process_reaper_once(now=now + 1)
            self.assertEqual(run.call_count, first_call_count)


if __name__ == "__main__":
    unittest.main()
