"""Agent lifetime must survive the short request thread that launches it."""
import sys
import threading

import pytest

import server  # wires the extracted module's core proxy
from ccc_server import acp


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux parent-death signal')
def test_parent_death_agent_survives_request_thread():
    processes = []

    def request():
        proc = acp._acp_start_process([
            sys.executable, '-c',
            'import ctypes,signal,time; '
            'assert ctypes.CDLL(None).prctl(1,signal.SIGTERM)==0; '
            'print("ready",flush=True); print(input(),flush=True); time.sleep(30)',
        ])
        processes.append(proc)
        assert proc.stdout.readline().strip() == 'ready'

    request_thread = threading.Thread(target=request)
    request_thread.start()
    request_thread.join(timeout=10)
    assert not request_thread.is_alive()
    assert len(processes) == 1
    proc = processes[0]
    try:
        # A real pipe round trip proves the agent remained alive after its
        # request thread exited; no timing-sensitive sleep/poll assertion.
        proc.stdin.write('probe\n')
        proc.stdin.flush()
        assert proc.stdout.readline().strip() == 'probe'
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdin.close()
        proc.stdout.close()


def test_launch_failure_returns_none():
    assert acp._acp_start_process(['/does-not-exist/acp-agent']) is None
