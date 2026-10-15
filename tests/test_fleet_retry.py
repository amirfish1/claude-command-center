"""Confirmed Fleet steps survive lost replies without repeating effects."""

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import server  # adopts extracted modules before importing their helpers
import federation
from ccc_server import fleet, fleet_jobs, fleet_receipts


@pytest.fixture
def action():
    return {
        "coordinator_node": "node-a", "node_id": "node-b", "plan_id": "plan-1",
        "id": "finish-1", "kind": "finish_worktree",
        "repo_identity": "example.test/org/app", "target": "topic",
        "evidence": {"candidate_sessions": [{"session_id": "test-session"}]},
    }


def test_replay_survives_process_restart(tmp_path, action):
    script = """
import json, sys
from pathlib import Path
from ccc_server import fleet_receipts
root, action = Path(sys.argv[1]), json.loads(sys.argv[2])
def apply(action):
    with (root / 'effects').open('a') as stream:
        stream.write('executed\\n')
    return {'ok': True, 'pinged': 'test-session'}
print(json.dumps(fleet_receipts.execute(root, action, apply)))
"""
    command = [sys.executable, "-c", script, str(tmp_path), json.dumps(action)]
    first = json.loads(subprocess.check_output(command, text=True))
    second = json.loads(subprocess.check_output(command, text=True))
    assert first == {"ok": True, "pinged": "test-session"}
    assert second == {**first, "replayed": True}
    assert (tmp_path / "effects").read_text() == "executed\n"


def test_overlapping_retry_does_not_poison_completed_result(tmp_path, action):
    started, release = threading.Event(), threading.Event()
    calls = []

    def apply(step):
        calls.append(step)
        started.set()
        assert release.wait(5)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(fleet_receipts.execute, tmp_path, action, apply)
        try:
            assert started.wait(5)
            retry = fleet_receipts.execute(tmp_path, action, apply)
            assert retry["error"] == "execution_uncertain"
        finally:
            release.set()
        assert first.result(timeout=5) == {"ok": True}
    assert fleet_receipts.execute(tmp_path, action, apply) == {"ok": True, "replayed": True}
    assert len(calls) == 1


def test_interrupted_effect_is_never_repeated(tmp_path, action):
    effects = []

    def interrupted(step):
        effects.append(step)
        raise RuntimeError("stopped after effect, before saving result")

    with pytest.raises(RuntimeError):
        fleet_receipts.execute(tmp_path, action, interrupted)
    result = fleet_receipts.execute(tmp_path, action, interrupted)
    assert result["error"] == "execution_uncertain"
    assert len(effects) == 1


@pytest.mark.parametrize("content", ["", "{", "[]", "null"])
def test_unreadable_receipt_fails_closed(tmp_path, action, content):
    (tmp_path / (fleet_receipts.request_id(action) + ".json")).write_text(content)
    result = fleet_receipts.execute(tmp_path, action, lambda _: pytest.fail("must not execute"))
    assert result["error"] == "execution_uncertain"


def test_changed_intent_conflicts_but_display_state_does_not(tmp_path, action):
    fleet_receipts.execute(tmp_path, action, lambda _: {"ok": True})

    def forbidden(_):
        pytest.fail("must not execute")

    result = fleet_receipts.execute(tmp_path, {**action, "target": "different"}, forbidden)
    assert result["error"] == "idempotency_conflict"
    result = fleet_receipts.execute(
        tmp_path, {**action, "status": "running", "node_name": "renamed"}, forbidden)
    assert result["replayed"]


@pytest.mark.parametrize("field", ["coordinator_node", "plan_id", "id"])
def test_new_coordinator_plan_or_action_is_independent(tmp_path, action, field):
    calls = []

    def apply(step):
        calls.append(step)
        return {"ok": True}

    fleet_receipts.execute(tmp_path, action, apply)
    fleet_receipts.execute(tmp_path, {**action, field: "another"}, apply)
    assert len(calls) == 2


def test_failed_outcome_replays_and_new_plan_can_revalidate(tmp_path, action):
    failure = {"ok": False, "error": "revalidation_failed", "detail": "tree is dirty"}
    assert fleet_receipts.execute(tmp_path, action, lambda _: failure) == failure
    result = fleet_receipts.execute(tmp_path, action, lambda _: pytest.fail("must not retry"))
    assert result == {**failure, "replayed": True}
    result = fleet_receipts.execute(
        tmp_path, {**action, "plan_id": "plan-2"}, lambda _: {"ok": True})
    assert result["ok"]


def test_receipt_must_be_persisted_before_effect(tmp_path, action, monkeypatch):
    def broken_sync(_fd):
        raise OSError("disk unavailable")

    monkeypatch.setattr(fleet_receipts.os, "fsync", broken_sync)
    with pytest.raises(OSError):
        fleet_receipts.execute(tmp_path, action, lambda _: pytest.fail("must not execute"))


def test_failure_saving_outcome_keeps_claim_after_effect(tmp_path, action, monkeypatch):
    effects = []

    def broken_replace(*_args):
        raise OSError("outcome could not be saved")

    def apply(step):
        effects.append(step)
        return {"ok": True}

    monkeypatch.setattr(fleet_receipts.os, "replace", broken_replace)
    with pytest.raises(OSError):
        fleet_receipts.execute(tmp_path, action, apply)
    assert fleet_receipts.execute(tmp_path, action, apply)["error"] == "execution_uncertain"
    assert len(effects) == 1
    assert not list(tmp_path.glob("*.tmp-*"))


def test_unplanned_local_step_remains_compatible(tmp_path):
    result = fleet_receipts.execute(tmp_path, {"kind": "push"}, lambda _: {"ok": True})
    assert result == {"ok": True}
    assert list(tmp_path.iterdir()) == []


def test_remote_step_carries_stable_identity_in_route_envelope(tmp_path, action, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(federation, "node_id", lambda: "node-a")
    monkeypatch.setattr(federation, "get_peer", lambda _: {"node_id": "node-b"})
    envelopes = []

    def request(self, method, path, body, timeout):
        envelopes.append(body)
        return {"ok": True, "result": {"ok": True}}

    monkeypatch.setattr(federation.PeerClient, "request", request)
    monkeypatch.setattr(server, "_federation_proxy_session_action", fleet._federation_proxy_session_action)
    for _ in range(2):
        assert fleet_jobs._fleet_revalidate_and_execute(action)["ok"]
    assert envelopes[0]["req_id"] == envelopes[1]["req_id"] == fleet_receipts.request_id(action)
    assert "req_id" not in envelopes[0]["args"]


def test_route_retries_read_receipt_after_transport_failure(tmp_path, action, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(fleet_jobs, "FLEET_JOBS_DIR", tmp_path / "jobs")
    effects = []

    def apply(step):
        effects.append(step)
        return {"ok": True}

    monkeypatch.setattr(fleet_jobs, "_fleet_apply_local_step", apply)
    envelope = federation.make_route_envelope("fleet_step", {"action": action})
    envelope["req_id"] = fleet_receipts.request_id(action)
    # A transient route failure must not hide the executor's durable outcome.
    federation.record_request_result(envelope["req_id"], {"ok": False, "error": "timeout"})

    def self_api(method, path, body, timeout):
        assert path == "/api/fleet/step"
        return fleet_jobs._fleet_execute_local_step(body["action"])

    monkeypatch.setattr(fleet, "_federation_self_api", self_api)
    first, status = fleet._federation_execute_route(envelope)
    assert status == 200 and first["result"]["ok"]
    federation._dedupe_file().unlink()  # generic cache eviction cannot erase the receipt
    second, status = fleet._federation_execute_route(envelope)
    assert status == 200 and second["result"]["replayed"]
    assert len(effects) == 1


@pytest.mark.parametrize("route_action", ["fleet_step", "inject"])
def test_other_mutating_routes_keep_generic_deduplication(tmp_path, monkeypatch, route_action):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def self_api(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(fleet, "_federation_self_api", self_api)
    envelope = federation.make_route_envelope(route_action, {"action": {"kind": "push"}})
    assert fleet._federation_execute_route(envelope)[1] == 200
    duplicate, status = fleet._federation_execute_route(envelope)
    assert status == 200 and duplicate["duplicate"]
    assert len(calls) == 1


@pytest.mark.parametrize("peer,hops,error", [
    ({"scopes": ["memory:read"]}, 2, "scope_forbidden"),
    (None, 0, "routing_loop"),
])
def test_planned_steps_still_enforce_route_guards(action, monkeypatch, peer, hops, error):
    def forbidden(*args, **kwargs):
        pytest.fail("guarded request must not reach executor")

    monkeypatch.setattr(fleet, "_federation_self_api", forbidden)
    envelope = federation.make_route_envelope("fleet_step", {"action": action}, hops=hops)
    result, status = fleet._federation_execute_route(envelope, peer=peer)
    assert status >= 400 and result["error"] == error
