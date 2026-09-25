"""The duplicate-instance guard must never treat this process as a duplicate.

The in-place restart (/api/restart, the in-app update) replaces the dashboard
with os.execvp, which keeps the pid. The registry entry written before the
exec still names that pid, and the new image isn't listening yet, so the
guard read its own entry as a hung duplicate and SIGTERMed itself: the
dashboard went down on every in-place restart.
"""
import os
from unittest import mock

import server  # noqa: F401  (initialises ccc_server.core)
from ccc_server import core as _core
from ccc_server import fleet_jobs


def _entry(pid):
    return {
        "pid": pid,
        "port": 8090,
        "install_path": str(_core.CCC_ROOT),
        "repo_common_dir": fleet_jobs._git_common_dir(_core.CCC_ROOT),
        "started_at": "2026-09-25T05:50:00-07:00",
    }


def _run_guard(entries):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CCC_EPHEMERAL", "CCC_ALLOW_DUPLICATE_REPO")}
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(fleet_jobs, "_read_registry_pruned", return_value=entries), \
         mock.patch.object(fleet_jobs, "_pid_command", return_value="python3 server.py"), \
         mock.patch.object(fleet_jobs, "_other_instance_responding", return_value=False), \
         mock.patch.object(fleet_jobs, "_kill_stale_duplicate") as kill:
        fleet_jobs._check_duplicate_repo_instance()
    return kill


def test_guard_skips_its_own_pid_after_exec_restart():
    kill = _run_guard([_entry(os.getpid())])
    kill.assert_not_called()


def test_guard_still_reaps_a_hung_other_instance():
    other = os.getpid() + 100000
    kill = _run_guard([_entry(other)])
    kill.assert_called_once_with(other, 8090)
