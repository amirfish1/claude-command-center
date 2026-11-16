# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Reap children left over from before an in-place dashboard restart.

The dashboard restarts with os.execvp, which keeps the pid. Children of the
previous image (helpers, ssh masters, engine processes) stay children of the
new one, but the Popen objects that would have waited on them are gone, so
every one that exits afterwards stays a zombie until the next cold start.

start() must run before this process spawns anything: the children present at
that moment are exactly the inherited ones. Only pids that are already zombies
are reaped, so code that re-attaches to a surviving child (spawn registry)
still sees it alive and can collect its exit status first.
"""

import os
from pathlib import Path
import subprocess
import threading
import time
from ccc_server.paths import _is_pid_alive

POLL_S = 5.0


def _children():
    """Direct child pids of this process."""
    pids = set()
    task_dir = Path(f"/proc/{os.getpid()}/task")
    try:
        tasks = list(task_dir.iterdir())
    except OSError:
        tasks = None
    if tasks is not None:
        for task in tasks:
            try:
                pids.update(int(p) for p in (task / "children").read_text().split())
            except (OSError, ValueError):
                pass
        return pids
    try:  # no /proc (macOS): one pgrep at boot
        out = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True,
                             text=True, timeout=5).stdout
        pids.update(int(p) for p in out.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return pids


def _is_zombie(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        pass
    if Path("/proc").is_dir():
        return False
    return True  # no /proc: let waitpid(WNOHANG) decide


def reap_once(pids):
    """Reap exited pids in `pids`; return the set still worth watching."""
    remaining = set()
    for pid in pids:
        if not _is_zombie(pid):
            if not _is_pid_alive(pid, perm_alive=True, unknown_alive=True):
                continue  # already reaped by someone else
            remaining.add(pid)
            continue
        try:
            reaped, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            continue  # not ours, or already reaped
        except OSError:
            remaining.add(pid)
            continue
        if reaped == 0:
            remaining.add(pid)
    return remaining


def _loop(pids):
    while pids:
        pids = reap_once(pids)
        if pids:
            time.sleep(POLL_S)


def start():
    """Call first in main(), before any child is spawned."""
    pids = _children()
    if not pids:
        return None
    thread = threading.Thread(target=_loop, args=(pids,),
                              name="inherited-children-reaper", daemon=True)
    thread.start()
    return thread
