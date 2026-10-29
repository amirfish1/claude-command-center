import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts/e2e-failover.sh"
SPEC = importlib.util.spec_from_file_location("e2e_failover", REPO / "scripts/e2e_failover.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def analytics_fixture(rows):
    summary = {
        "totalRequests": len(rows),
        "totalInputTokens": sum(row["inputTokens"] for row in rows),
        "totalOutputTokens": sum(row["outputTokens"] for row in rows),
        "estimatedCostSavings": 4.25,
    }
    return summary, {"total": len(rows), "rows": rows}


def request_row(request_id=1, status="success", platform="kilo", model="test-model:free"):
    return {"id": request_id, "status": status, "platform": platform, "modelId": model, "inputTokens": 10, "outputTokens": 5, "clientIp": "127.0.0.1", "clientUserAgent": "test"}


def test_script_shell_syntax():
    proc = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_requires_explicit_opt_in():
    env = dict(os.environ, CCC_E2E_FREE="0")
    proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0
    assert "CCC_E2E_FREE=1" in proc.stderr
    assert "E2E_RESULT" not in proc.stdout


def test_clean_environment_drops_credentials_and_live_server_pointers(tmp_path):
    source = {
        "ANTHROPIC_API_KEY": "sk-ant-test-XXXX",
        "ANTHROPIC_AUTH_TOKEN": "router-test-XXXX",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:3017",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-XXXX",
        "CLAUDE_CODE_SESSION_KEY": "session-test-XXXX",
        "CLAUDE_CONFIG_DIR": "/real-config",
        "CLAUDECODE": "1",
        "CCC_WORKER_SOCKET": "/real-worker.sock",
        "CCC_FREE_ROUTER_HOME": "/real-router",
        "CCC_CONV_META_CACHE_FILE": "/real-cache",
        "CCC_SESSION_RUNTIME": "free",
        "FREEAPI_DB_PATH": "/real-db",
        "ENCRYPTION_KEY": "encryption-test-XXXX",
        "PORT": "8090",
        "GIT_DIR": "/real-repo/.git",
        "HOME": "/real-home",
        "PATH": "/usr/bin",
        "PYTHONPATH": "/test-dependencies",
        "PYTHONUSERBASE": "/test-userbase",
    }
    env = harness.clean_env(source, tmp_path)
    assert env == {"HOME": str(tmp_path), "PATH": "/usr/bin", "PYTHONPATH": "/test-dependencies", "PYTHONUSERBASE": "/test-userbase"}


def test_home_refuses_existing_data_real_home_and_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    with pytest.raises(harness.FailoverError, match="real HOME"):
        harness.validate_home(real, real)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep me")
    with pytest.raises(harness.FailoverError, match="empty directory"):
        harness.validate_home(occupied, real)
    linked = tmp_path / "link"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(harness.FailoverError, match="symlink"):
        harness.validate_home(linked, real)
    assert (occupied / "keep.txt").read_text() == "keep me"
    assert harness.validate_home(tmp_path / "fresh", real) == tmp_path / "fresh"


@pytest.mark.parametrize("port", [8090, 3017, 80, 65536])
def test_home_protected_ports_rejected(port):
    with pytest.raises(harness.FailoverError):
        harness.validate_port(port)


def test_foreign_listener_refused():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        with pytest.raises(harness.FailoverError, match="already in use"):
            harness.validate_port(port)
        with pytest.raises(harness.FailoverError, match="distinct"):
            harness.validate_port(port, port)


def test_analytics_proves_free_traffic_not_estimated_billing():
    before = harness.snapshot_analytics(*analytics_fixture([request_row()]))
    after = harness.snapshot_analytics(*analytics_fixture([request_row(), request_row(2)]))
    evidence = harness.free_evidence(before, after, {"test-model:free"})
    assert evidence["router_requests_delta"] == 1
    assert evidence["free_success_count"] == 1
    assert evidence["inference_cost_usd"] == 0
    assert evidence["cost_basis"] == harness.COST_BASIS
    assert set(after["rows"][0]) == set(harness.REQUEST_FIELDS)
    assert "estimatedCostSavings" not in after["summary"]


@pytest.mark.parametrize("mutation", ["missing-counter", "bool-counter", "missing-field", "truncated", "duplicate-id", "disagreeing-count", "disagreeing-tokens"])
def test_analytics_rejects_incomplete_evidence(mutation):
    summary, requests = analytics_fixture([request_row()])
    if mutation == "missing-counter":
        summary.pop("totalRequests")
    elif mutation == "bool-counter":
        summary["totalRequests"] = True
    elif mutation == "missing-field":
        requests["rows"][0].pop("outputTokens")
    elif mutation == "truncated":
        requests["total"] = 501
    elif mutation == "duplicate-id":
        requests["rows"].append(copy.deepcopy(requests["rows"][0]))
        requests["total"] = 2
    elif mutation == "disagreeing-tokens":
        summary["totalInputTokens"] = 100
    else:
        summary["totalRequests"] = 2
    with pytest.raises(harness.FailoverError):
        harness.snapshot_analytics(summary, requests)


@pytest.mark.parametrize("mutation", ["no-traffic", "error-only", "wrong-provider", "paid-model", "no-tokens", "lost-history", "changed-history", "wrong-delta"])
def test_free_evidence_rejects_false_success(mutation):
    before = harness.snapshot_analytics(*analytics_fixture([request_row()]))
    row = request_row(2)
    if mutation == "error-only":
        row["status"] = "error"
    elif mutation == "wrong-provider":
        row["platform"] = "anthropic"
    elif mutation == "paid-model":
        row["modelId"] = "paid-test-model"
    elif mutation == "no-tokens":
        row.update(inputTokens=0, outputTokens=0)
    after = harness.snapshot_analytics(*analytics_fixture([request_row(), row]))
    if mutation == "no-traffic":
        after = before
    elif mutation == "lost-history":
        after["rows"] = [row]
    elif mutation == "changed-history":
        after["rows"][0]["outputTokens"] += 1
    elif mutation == "wrong-delta":
        after["summary"]["totalRequests"] = 3
    with pytest.raises(harness.FailoverError):
        harness.free_evidence(before, after, {"test-model:free", "paid-test-model"})


@pytest.mark.skipif(os.environ.get("CCC_E2E_FREE") != "1", reason="real failover E2E: set CCC_E2E_FREE=1 and CCC_FREELLMAPI_SRC")
def test_real_failover_same_session_and_switch_back(tmp_path):
    home = tmp_path / "e2e-home"
    port = os.environ.get("CCC_E2E_CCC_PORT") or str(harness.free_port())
    env = dict(os.environ, CCC_E2E_HOME=str(home), CCC_E2E_KEEP="1", CCC_E2E_CCC_PORT=port, CCC_PYTHON=sys.executable)
    proc = subprocess.Popen(["bash", str(SCRIPT)], cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=int(os.environ.get("CCC_E2E_TEST_TIMEOUT_S", "1500")))
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=10)
        pytest.fail(f"Failover E2E timed out. Private logs: {home / 'logs'}\n{stdout}\n{stderr}")
    print(stdout)
    print(stderr, file=sys.stderr)
    assert proc.returncode == 0, f"Failover E2E failed. Private logs: {home / 'logs'}"
    results = [json.loads(line[len("E2E_RESULT "):]) for line in stdout.splitlines() if line.startswith("E2E_RESULT ")]
    assert len(results) == 1
    result = results[0]
    assert result["ok"] is True
    assert result["router_requests_delta"] > 0
    assert result["free_success_count"] > 0
    assert result["inference_cost_usd"] == 0
    assert result["cost_basis"] == harness.COST_BASIS
    assert result["switch_back_complete"] is True
    assert result["synthetic_limit"] is True
    assert result["seed_runtime"] == "free bootstrap"
    assert Path(result["proof_file"]).read_text().rstrip("\n") == harness.PROOF_TEXT
    rows = harness.events(Path(result["transcript_file"]))
    markers = [row for row in rows if row.get("subtype") == "ccc_free_runtime"]
    assert [row["event"] for row in markers] == ["failover_start", "failover_back"]
    assert all(row["sessionId"] == result["session_id"] for row in markers)
