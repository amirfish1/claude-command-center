"""Exercise service ownership and supervisor health with real child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ccc_server.oncall import agent_install

ROOT = Path(__file__).resolve().parents[1]


def test_owned_service_reuses_live_record_and_rejects_exited_child(tmp_path):
    url = "http://127.0.0.1:18090"
    child = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)",
        str(agent_install.REPO / "server.py"), "--port", "18090",
    ])
    (tmp_path / "oncall-agent-process.json").write_text(
        json.dumps({"pid": child.pid, "url": url})
    )
    try:
        assert agent_install.owned_service(tmp_path) == url
    finally:
        child.terminate()
        child.wait(timeout=5)
    assert agent_install.owned_service(tmp_path) is None


def test_supervisor_health_fails_for_exited_child(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=5)
    (tmp_path / "oncall-service-pids.json").write_text(json.dumps([child.pid]))
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/oncall-supervisor.py"), "--health"],
        env=dict(os.environ, CCC_STATE_DIR=str(tmp_path)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 1
    assert not result.stderr
