"""The shadow fingerprint runs off-process and never becomes a restart gate."""

import subprocess
import sys
from pathlib import Path

import pytest

import server
from ccc_server import bg_helper, import_closure


@pytest.fixture
def closure_repo(tmp_path):
    (tmp_path / "ccc_worker.py").write_text("import dependency\n")
    (tmp_path / "dependency.py").write_text("value = 1\n")
    return tmp_path


def current_worker(monkeypatch, root, loaded_hash):
    monkeypatch.setattr(server, "_control_plane_request", lambda *a, **k: {
        "ok": True, "worker": {"server_version": "1.0", "worker_closure_hash": loaded_hash},
    })
    monkeypatch.setattr(server, "_repo_version_on_disk", lambda: "1.0")
    monkeypatch.setattr(server, "_install_dir", lambda: root)
    logs = []
    monkeypatch.setattr(server, "_log_activity", lambda *args: logs.append(args))
    monkeypatch.setattr(server, "_restart_worker_process", lambda *a, **k: pytest.fail("current worker restarted"))
    return logs


def test_shadow_closure_uses_real_helper_and_observes_edits(closure_repo, tmp_path, monkeypatch):
    expected = import_closure.compute_closure(closure_repo)["hash"]
    logs = current_worker(monkeypatch, closure_repo, expected)
    address = str(tmp_path / "helper.sock")
    child_code = """
import socket, sys
from pathlib import Path
from types import SimpleNamespace
from ccc_server.bg_helper import _Child
listener = socket.socket(socket.AF_UNIX)
listener.bind(sys.argv[1])
listener.listen()
child = _Child(SimpleNamespace(_install_dir=lambda: Path(sys.argv[2])))
print('ready', flush=True)
while True:
    conn, _ = listener.accept()
    child.handle(conn)
"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", child_code, address, str(closure_repo)],
        cwd=Path(__file__).resolve().parent.parent,
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        monkeypatch.setitem(bg_helper._state, "sock", address)
        monkeypatch.setitem(bg_helper._state, "ready", True)
        monkeypatch.setattr(import_closure, "compute_closure", lambda *a, **k: pytest.fail("dashboard parsed imports"))
        assert server._restart_stale_worker()["reason"] == "current"
        assert f"disk_new_hash={expected}" in logs[-1][2]
        assert "new_stale=False" in logs[-1][2]

        (closure_repo / "dependency.py").write_text("value = 222\n")
        assert server._restart_stale_worker()["reason"] == "current"
        assert f"disk_new_hash={expected}" not in logs[-1][2]
        assert "new_stale=True" in logs[-1][2]
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()


def test_unavailable_helper_keeps_local_shadow_and_restart_decision(closure_repo, monkeypatch):
    expected = import_closure.compute_closure(closure_repo)["hash"]
    logs = current_worker(monkeypatch, closure_repo, "older-closure")
    monkeypatch.setitem(bg_helper._state, "ready", False)
    assert server._restart_stale_worker()["reason"] == "current"
    assert f"disk_new_hash={expected}" in logs[-1][2]
    assert "new_stale=True" in logs[-1][2]
