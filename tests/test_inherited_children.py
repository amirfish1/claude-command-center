"""Children inherited across an in-place (execvp) restart must be reaped."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not Path("/proc/self/task").is_dir(),
                                reason="needs Linux /proc")

# Stage 1 spawns a short-lived and a long-lived child, then execs stage 2 in
# place (same pid, Popen objects gone) -- the dashboard restart shape.
STAGE1 = r"""
import os, subprocess, sys
short = subprocess.Popen([sys.executable, "-c", "pass"])
long_ = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
os.execv(sys.executable, [sys.executable, "-c", sys.argv[1], sys.argv[2],
                          str(short.pid), str(long_.pid)])
"""

STAGE2 = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from ccc_server import inherited_children as ic
short, long_ = int(sys.argv[2]), int(sys.argv[3])
ic.POLL_S = 0.05
assert ic._children() >= {short, long_}, ic._children()
ic.start()
deadline = time.time() + 10
while time.time() < deadline and Path(f"/proc/{short}").exists():
    time.sleep(0.05)
assert not Path(f"/proc/{short}").exists(), "exited child was not reaped"
assert Path(f"/proc/{long_}").exists(), "live child must be left alone"
os.kill(long_, 9)
deadline = time.time() + 10
while time.time() < deadline and Path(f"/proc/{long_}").exists():
    time.sleep(0.05)
assert not Path(f"/proc/{long_}").exists(), "late-exiting child was not reaped"
print("OK")
"""


def test_inherited_children_are_reaped_after_exec():
    proc = subprocess.run([sys.executable, "-c", STAGE1, STAGE2, str(ROOT)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_reap_once_ignores_pids_that_are_not_children():
    sys.path.insert(0, str(ROOT))
    from ccc_server import inherited_children as ic
    assert ic.reap_once({os.getppid()}) == {os.getppid()}
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert ic.reap_once({child.pid}) == set()
    assert time.time() > 0
