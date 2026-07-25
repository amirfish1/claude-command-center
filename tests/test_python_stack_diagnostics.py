import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(not hasattr(signal, "SIGUSR2"), reason="SIGUSR2 is unavailable")
def test_sigusr2_writes_python_stacks_for_all_threads(tmp_path):
    stack_log = tmp_path / "python-stacks.log"
    script = f"""
import threading
import time

import server

threading.Thread(target=time.sleep, args=(30,), daemon=True).start()
server._install_python_stack_dump_handler({str(stack_log)!r})
print("ready", flush=True)
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        os.kill(child.pid, signal.SIGUSR2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if stack_log.exists() and "Thread" in stack_log.read_text():
                break
            time.sleep(0.05)
        contents = stack_log.read_text()
        assert "Thread 0x" in contents
        assert "Current thread" in contents
    finally:
        child.terminate()
        child.wait(timeout=5)
