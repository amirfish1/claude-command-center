import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

import server


@pytest.mark.skipif(not hasattr(signal, "SIGUSR2"), reason="SIGUSR2 is unavailable")
def test_sigusr2_bursts_write_python_stacks_without_crashing(tmp_path):
    # CCC-145: ~25 SIGUSR2s at 0.5s segfaulted the dashboard when the handler
    # was faulthandler.register(all_threads=True). Hammer it with busy threads.
    stack_log = tmp_path / "python-stacks.log"
    script = f"""
import threading
import time

import server

def spin():
    while True:
        sum(range(1000))

for _ in range(4):
    threading.Thread(target=spin, daemon=True).start()
threading.Thread(target=time.sleep, args=(60,), daemon=True, name="sleeper").start()
server._install_python_stack_dump_handler({str(stack_log)!r})
print("ready", flush=True)
time.sleep(60)
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
        for _ in range(60):
            os.kill(child.pid, signal.SIGUSR2)
            time.sleep(0.01)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if stack_log.exists() and "[sleeper]" in stack_log.read_text():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        assert child.poll() is None, child.stderr.read()
        contents = stack_log.read_text()
        assert "=== SIGUSR2 sample 1/1" in contents
        assert "Thread 0x" in contents
        assert "[sleeper]" in contents
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_dump_python_stacks_samples_and_clamps(tmp_path, monkeypatch):
    log = (tmp_path / "s.log").open("a")
    monkeypatch.setattr(server, "_PYTHON_STACK_DUMP_FILE", log)
    out = server._dump_python_stacks("test", samples="3", interval_s="0")
    assert len(out) == 3
    names = {t["name"] for t in out[0]["threads"]}
    assert threading.current_thread().name in names
    assert sum(t["current"] for t in out[0]["threads"]) == 1
    assert len(server._dump_python_stacks("test", samples=999, interval_s=0)) == server.PYTHON_STACK_SAMPLES_MAX
    assert len(server._dump_python_stacks("test", samples="x", interval_s=0)) == 1
    log.flush()
    assert (tmp_path / "s.log").read_text().count("=== test sample 1/3") == 1


def test_debug_stacks_endpoint_returns_samples(monkeypatch):
    monkeypatch.setattr(server, "_PYTHON_STACK_DUMP_FILE", None)
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/debug/stacks?samples=2&interval=0", timeout=10
        ) as r:
            body = json.loads(r.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert body["ok"] is True
    assert body["pid"] == os.getpid()
    assert len(body["samples"]) == 2
    assert any("serve_forever" in t["stack"] for t in body["samples"][0]["threads"])
