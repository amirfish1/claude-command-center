from types import SimpleNamespace
from unittest import mock
import ast
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

import pytest

import server


def _cross_process_claim_worker(
    pending_file, session_id, stale_loaded, claimed, release, released, results,
):
    import server as process_server

    try:
        process_server.PENDING_INPUTS_FILE = Path(pending_file)
        process_server._pending_resume_queue.clear()
        process_server._pending_terminal_input_queue.clear()
        process_server._load_pending_inputs()
        if not stale_loaded.wait(5):
            raise AssertionError("pump process did not load its stale snapshot")
        lock = process_server._codex_queue_pump_lock(session_id)
        with lock:
            claim = process_server._claim_matching_pending_input(
                session_id, "target",
            )
            results.put(("claim", os.getpid(), claim))
            claimed.set()
            if not release.wait(5):
                raise AssertionError("claim process was not released")
        released.set()
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


def _cross_process_pump_worker(
    pending_file, session_id, stale_loaded, claimed, release, released, results,
):
    import server as process_server

    try:
        process_server.PENDING_INPUTS_FILE = Path(pending_file)
        process_server._pending_resume_queue.clear()
        process_server._pending_terminal_input_queue.clear()
        process_server._load_pending_inputs()
        process_server._schedule_codex_queue_pump = lambda sid: None
        stale_loaded.set()
        if not claimed.wait(5):
            raise AssertionError("claim process did not claim the row")
        process_server._pending_resume_retry_after.clear()
        process_server._resume_queue_engine_busy = lambda sid: False
        deliveries = []

        def deliver(sid, text, _from_queue=False, **kwargs):
            deliveries.append(text)
            return {"ok": True, "accepted": True, "confirmed": True}

        process_server._control_plane_engine_call = lambda *a, **k: None
        process_server._resume_session_codex_native_delivery = deliver
        first = process_server._pump_codex_resume_queue(session_id)
        results.put(("first-pump", os.getpid(), first, list(deliveries)))
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


def _cross_process_rmw_worker(
    pending_file, session_id, action, both_loaded, first_saved, results,
):
    import server as process_server

    try:
        process_server.PENDING_INPUTS_FILE = Path(pending_file)
        process_server._pending_resume_queue.clear()
        process_server._pending_terminal_input_queue.clear()
        process_server._pending_devin_steers.clear()
        process_server._auto_resume_opt_in.clear()
        process_server._load_pending_inputs()
        both_loaded.put(os.getpid())
        if action == "update" and not first_saved.wait(5):
            raise AssertionError("delete writer did not persist")
        with process_server._pending_resume_lock:
            if action == "delete":
                process_server._pending_resume_queue.pop(session_id, None)
            else:
                process_server._pending_resume_queue[session_id] = ["new-b"]
        ok = process_server._save_pending_inputs({session_id})
        if action == "delete":
            first_saved.set()
        results.put((action, os.getpid(), ok))
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


def _cross_process_stale_enqueue_worker(
    pending_file, session_id, stale_loaded, claimed, finished, results,
):
    import server as process_server

    try:
        process_server.PENDING_INPUTS_FILE = Path(pending_file)
        process_server._pending_resume_queue.clear()
        process_server._pending_terminal_input_queue.clear()
        process_server._load_pending_inputs()
        stale_loaded.set()
        if not claimed.wait(5):
            raise AssertionError("worker did not claim queued row")
        result = process_server._apply_pending_input_operations(session_id, [{
            "field": "resume", "action": "append_tail", "value": "new-row",
        }])
        finished.set()
        results.put(("enqueue", os.getpid(), result))
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


def _cross_process_nested_file_lock_worker(pending_file, results):
    import ccc_server.pending_inputs as pending_inputs_module
    import server as process_server

    process_server.PENDING_INPUTS_FILE = Path(pending_file)
    try:
        with pending_inputs_module._pending_inputs_file_exclusive_lock():
            with pending_inputs_module._pending_inputs_file_exclusive_lock():
                results.put(("nested", os.getpid(), True))
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


@pytest.fixture(autouse=True)
def clean_pending_queues(monkeypatch):
    monkeypatch.setattr(
        server, "_refresh_pending_inputs_for_session", lambda session_id: True,
    )
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._pending_terminal_handoff_ids.clear()
    server._pending_primary_conflicts.clear()
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        server._CODEX_APP_SERVER_TURN_THREAD.clear()
    suppressions = getattr(
        server, "_CODEX_QUEUED_STEER_ACK_SUPPRESSIONS", None,
    )
    if suppressions is not None:
        suppressions.clear()
    yield
    server._pending_resume_queue.clear()
    server._pending_terminal_input_queue.clear()
    server._pending_terminal_handoff_ids.clear()
    server._pending_primary_conflicts.clear()
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        server._CODEX_APP_SERVER_TURN_THREAD.clear()
    if suppressions is not None:
        suppressions.clear()


@pytest.fixture
def router_env(monkeypatch):
    resume = mock.Mock()
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=True),
    )
    monkeypatch.setattr(server, "_inject_budget_check", lambda *args: None)
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: "/tmp")
    monkeypatch.setattr(
        server,
        "session_live_status",
        lambda sid, cwd=None: {"live": True, "status": "working", "tty": None},
    )
    monkeypatch.setattr(server, "_is_cursor_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_hermes_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: None)
    monkeypatch.setattr(server, "_is_opencode_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_devin_cli_session", lambda sid: False)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", resume)
    monkeypatch.setattr(
        server, "_save_codex_app_server_state_unlocked", mock.Mock(),
    )
    return SimpleNamespace(resume=resume)


def _notify_user_message(session_id, text, turn_id="turn-active"):
    server._codex_app_server_handle_message({
        "jsonrpc": "2.0",
        "method": "item/completed",
        "params": {
            "threadId": session_id,
            "turnId": turn_id,
            "item": {
                "id": f"user-{turn_id}",
                "type": "userMessage",
                "text": text,
            },
        },
    })


def test_claim_removes_one_duplicate_and_restore_preserves_fifo(monkeypatch):
    sid = "claim-restore"
    server._pending_resume_queue[sid] = ["first", "target", "target", "last"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=True),
    )

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim.items() >= {
        "session_id": sid,
        "queue_name": "resume",
        "index": 1,
        "item": "target",
    }.items()
    assert claim["claim_sequence"] > 0
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]
    assert server._restore_pending_input_claim(claim)
    assert server._pending_resume_queue[sid] == ["first", "target", "target", "last"]


def test_claim_prefers_resume_queue_and_claims_at_most_one(monkeypatch):
    sid = "claim-precedence"
    server._pending_resume_queue[sid] = ["target", "keep"]
    server._pending_terminal_input_queue[sid] = ["target"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=True),
    )

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim["queue_name"] == "resume"
    assert server._pending_resume_queue[sid] == ["keep"]
    assert server._pending_terminal_input_queue[sid] == ["target"]


def test_claim_persistence_failure_restores_memory_and_fails_closed(
    monkeypatch,
):
    sid = "claim-save-failure"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=False),
    )

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim["code"] == "queued_claim_persistence_failed"
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]


def test_claim_persistence_failure_never_calls_codex(router_env, monkeypatch):
    sid = "steer-claim-save-failure"
    server._pending_resume_queue[sid] = ["target"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=False),
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["code"] == "queued_claim_persistence_failed"
    assert not result.get("queued_preserved")
    assert server._pending_resume_queue[sid] == ["target"]
    router_env.resume.assert_not_called()


def test_rollback_persistence_failure_is_distinct_and_not_preserved(
    router_env, monkeypatch,
):
    sid = "steer-rollback-save-failure"
    server._pending_resume_queue[sid] = ["target"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current",
        mock.Mock(side_effect=[True, False]),
    )
    router_env.resume.return_value = {
        "ok": False,
        "via": "codex-steer",
        "code": "codex_steer_failed",
    }

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["code"] == "queued_rollback_persistence_failed"
    assert not result.get("queued_preserved")
    assert sid not in server._pending_resume_queue


def test_successful_explicit_queued_steer_claims_before_delivery(router_env):
    sid = "steer-success"
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    seen_during_delivery = []

    def deliver(*args, **kwargs):
        seen_during_delivery.append(list(server._pending_resume_queue[sid]))
        return {"ok": True, "via": "codex-steer"}

    router_env.resume.side_effect = deliver
    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert seen_during_delivery == [["target", "last"]]
    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_successful_handoff_claim_completes_authoritative_file(
    router_env, monkeypatch, tmp_path,
):
    sid = "steer-handoff-success"
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text("{}")
    item = server._PendingInputHandoff("target", "handoff-success", handoff_path)
    server._pending_terminal_input_queue[sid] = [item]
    server._pending_terminal_handoff_ids[item.handoff_id] = handoff_path
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["queued_consumed"] == 1
    assert not handoff_path.exists()
    assert item.handoff_id not in server._pending_terminal_handoff_ids


def test_failed_handoff_claim_restores_without_completing(
    router_env, monkeypatch, tmp_path,
):
    sid = "steer-handoff-rollback"
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text("{}")
    item = server._PendingInputHandoff("target", "handoff-rollback", handoff_path)
    server._pending_terminal_input_queue[sid] = [item]
    server._pending_terminal_handoff_ids[item.handoff_id] = handoff_path
    complete = mock.Mock(wraps=server._complete_pending_input_handoff)
    monkeypatch.setattr(server, "_complete_pending_input_handoff", complete)
    router_env.resume.return_value = {
        "ok": False,
        "via": "codex-steer",
        "code": "codex_steer_failed",
    }

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["queued_preserved"]
    assert server._pending_terminal_input_queue[sid] == [item]
    assert handoff_path.exists()
    complete.assert_not_called()


def test_handoff_rollback_refreshes_without_duplicating_authority(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "steer-handoff-real-refresh"
    pending_file = tmp_path / "pending-inputs.json"
    handoff_dir = tmp_path / "handoffs"
    pending_file.write_text(json.dumps({
        "resume_queue": {},
        "devin_steers": {},
        "terminal_queue": {},
        "auto_resume_opt_in": {},
    }))
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", handoff_dir)
    monkeypatch.setattr(
        server,
        "_refresh_pending_inputs_for_session",
        pending_inputs_module._refresh_pending_inputs_for_session,
    )
    handoff_path = server._write_pending_input_handoff(sid, "target")
    assert handoff_path is not None

    claim = server._claim_matching_pending_input(sid, "target")
    assert server._restore_pending_input_claim(claim)

    restored = server._pending_terminal_input_queue[sid]
    assert len(restored) == 1
    assert isinstance(restored[0], server._PendingInputHandoff)
    assert restored[0].handoff_id == claim["item"].handoff_id


@pytest.mark.parametrize("notification_timing", ["before_response", "after_response"])
def test_successful_claim_suppresses_its_real_delivery_callback(
    router_env, monkeypatch, notification_timing,
):
    sid = f"steer-callback-{notification_timing}"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            if notification_timing == "before_response":
                _notify_user_message(sid, "target", turn_id="claimed-turn")
            return {"result": {"turnId": "claimed-turn"}}
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )
    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )
    if notification_timing == "after_response":
        _notify_user_message(sid, "target", turn_id="claimed-turn")

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_claim_suppresses_only_the_matching_native_steer_turn(
    router_env, monkeypatch,
):
    sid = "steer-callback-turn-match"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            _notify_user_message(sid, "target", turn_id="other-turn")
            return {"result": {"turnId": "claimed-turn"}}
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]
    _notify_user_message(sid, "target", turn_id="claimed-turn")
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_unbound_claim_does_not_suppress_resume_notification_before_failure(
    router_env, monkeypatch,
):
    sid = "steer-callback-before-bind"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            _notify_user_message(sid, "target", turn_id="other-turn")
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            return {"error": {"message": "steer rejected"}}
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert not result["ok"]
    assert result["code"] == "codex_steer_failed"
    assert result["queued_preserved"]
    assert server._pending_resume_queue[sid] == ["target", "target", "last"]


def test_matching_delivery_ack_commits_claim_before_failed_rpc_response(
    router_env, monkeypatch,
):
    sid = "steer-ack-before-failed-response"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            _notify_user_message(sid, "target", turn_id="claimed-turn")
            return {"error": {"message": "late RPC failure"}}
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["ok"]
    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_matching_delivery_ack_commits_claim_before_rpc_exception(
    router_env, monkeypatch,
):
    sid = "steer-ack-before-exception"
    server._pending_resume_queue[sid] = ["target", "target", "last"]

    def request(method, params=None, timeout=20):
        if method == "thread/resume":
            return {"result": {"thread": {
                "id": sid,
                "status": {"type": "active"},
                "turns": [{"id": "claimed-turn", "status": "inProgress"}],
            }}}
        if method == "turn/steer":
            _notify_user_message(sid, "target", turn_id="claimed-turn")
            raise RuntimeError("response channel closed")
        raise AssertionError(f"unexpected app-server method: {method}")

    monkeypatch.setattr(server, "_codex_app_server_request", request)
    router_env.resume.side_effect = lambda session_id, text, **kwargs: (
        server._codex_steer_via_app_server(session_id, text, cwd="/tmp")
    )

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["ok"]
    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_ack_suppression_total_cap_includes_every_state():
    now = time.monotonic()
    states = (["unbound", "pending", "committed"] * 85) + ["unbound"]
    with server._CODEX_QUEUED_STEER_ACK_LOCK:
        for index, state in enumerate(states):
            key = (f"sid-{index}", "target")
            server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS[key] = [{
                "token_id": f"token-{index}",
                "state": state,
                "acknowledged": False,
                "created_at": now + index,
                "expires_at": now + 1000,
            }]
        server._prune_codex_queued_steer_acks_unlocked(now)
        entries = [
            entry
            for values in server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS.values()
            for entry in values
        ]

    assert len(entries) == server._CODEX_QUEUED_STEER_ACK_MAX_TOTAL
    assert {entry["state"] for entry in entries} == {
        "unbound", "pending", "committed",
    }


def test_ack_suppression_ttl_expires_every_state():
    now = time.monotonic()
    with server._CODEX_QUEUED_STEER_ACK_LOCK:
        for index, state in enumerate(("unbound", "pending", "committed")):
            key = (f"expired-{index}", "target")
            server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS[key] = [{
                "token_id": f"expired-token-{index}",
                "state": state,
                "acknowledged": False,
                "created_at": now - 10,
                "expires_at": now - 1,
            }]
        server._prune_codex_queued_steer_acks_unlocked(now)

    assert server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS == {}


def test_failed_queued_steer_restores_original_fifo_position(router_env):
    sid = "steer-failure"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    router_env.resume.return_value = {
        "ok": False,
        "via": "codex-steer",
        "code": "codex_steer_failed",
    }

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert not result["ok"]
    assert result["queued_preserved"]
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]


def test_exception_restores_claim_and_does_not_suppress_a_later_ack(router_env):
    sid = "steer-exception"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    router_env.resume.side_effect = RuntimeError("steer exploded")

    with pytest.raises(RuntimeError, match="steer exploded"):
        server._inject_text_into_session_router(
            sid, "target", mode="steer", preserve_queued_steer=True,
        )

    assert server._pending_resume_queue[sid] == ["first", "target", "last"]
    _notify_user_message(sid, "target", turn_id="later-turn")
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]


def test_legacy_steer_claims_matching_queue_copy(router_env):
    sid = "stale-client"
    server._pending_resume_queue[sid] = ["target"]
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}

    result = server._inject_text_into_session_router(sid, "target", mode="steer")

    assert result["queued_consumed"] == 1
    assert sid not in server._pending_resume_queue


def test_legacy_local_fallback_does_not_consume_second_duplicate(router_env):
    sid = "legacy-local-duplicate"
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    router_env.resume.return_value = {
        "ok": True, "via": "codex-steer", "queued_consumed": 1,
    }

    result = server._inject_text_into_session_router(sid, "target", mode="steer")

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_legacy_worker_result_does_not_consume_dashboard_duplicate(
    router_env, monkeypatch,
):
    sid = "legacy-worker-duplicate"
    server._pending_resume_queue[sid] = ["target", "last"]
    worker_resume = mock.Mock(return_value={
        "ok": True, "via": "codex-steer", "queued_consumed": 1,
    })
    monkeypatch.setattr(server, "resume_session_codex", worker_resume)

    result = server._inject_text_into_session_router(sid, "target", mode="steer")

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_codex_router_propagates_explicit_replacement_to_resume(monkeypatch):
    resume = mock.Mock(return_value={"ok": True, "via": "codex-steer"})
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "resume_session_codex", resume)
    monkeypatch.setattr(server, "_inject_budget_check", lambda *args: None)
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: True)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: "/tmp")
    monkeypatch.setattr(
        server, "session_live_status",
        lambda sid, cwd=None: {"live": True, "status": "working", "tty": None},
    )
    monkeypatch.setattr(server, "_is_cursor_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_hermes_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: None)
    monkeypatch.setattr(server, "_is_opencode_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_devin_cli_session", lambda sid: False)

    result = server._inject_text_into_session_router(
        "codex-worker-route", "queued correction", mode="steer",
        preserve_queued_steer=True,
    )

    assert result["ok"]
    resume.assert_called_once_with(
        "codex-worker-route", "queued correction",
        steer=True,
        preserve_queued_steer=True,
    )


def test_real_resume_worker_owns_acknowledged_ambiguous_steer(
    monkeypatch,
):
    sid = "real-worker-transaction"
    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(server, "_pending_writer_compatibility_status", lambda: {
        "ok": True, "restart_required": False, "incompatible_writers": [],
    })
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    monkeypatch.setattr(
        server, "_persist_pending_inputs_current", mock.Mock(return_value=True),
    )

    def native_delivery(session_id, text, **kwargs):
        server._bind_codex_queued_steer_ack_suppression(
            session_id, text, "worker-turn",
        )
        _notify_user_message(session_id, text, turn_id="worker-turn")
        raise RuntimeError("ambiguous worker response")

    monkeypatch.setattr(
        server, "_resume_session_codex_native_delivery", native_delivery,
        raising=False,
    )
    routed_args = []

    def route(engine, operation, args, **kwargs):
        if os.environ.get("CCC_WORKER_PROCESS") == "1":
            return None
        routed_args.append(dict(args))
        assert args["preserve_queued_steer"] is True
        assert args["queued_steer_transaction_protocol"] == 1
        with mock.patch.dict(os.environ, {"CCC_WORKER_PROCESS": "1"}):
            return server.resume_session_codex(
                args["session_id"], args["text"],
                steer=args["steer"],
                _from_queue=args["from_queue"],
                preserve_queued_steer=args["preserve_queued_steer"],
                queued_steer_transaction_protocol=args[
                    "queued_steer_transaction_protocol"
                ],
            )

    monkeypatch.setattr(server, "_control_plane_engine_call", route)

    result = server.resume_session_codex(
        sid, "target", steer=True, preserve_queued_steer=True,
    )

    assert routed_args[0]["preserve_queued_steer"] is True
    assert result.items() >= {
        "ok": True,
        "queued_consumed": 1,
        "delivery_acknowledged": True,
    }.items()
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_worker_engine_forwards_explicit_replacement_semantics():
    from worker_engines import EngineHost

    resume = mock.Mock(return_value={"ok": True, "via": "codex-steer"})
    host = object.__new__(EngineHost)
    host._legacy = lambda: SimpleNamespace(resume_session_codex=resume)

    result = host._call("codex", "resume", {
        "session_id": "worker-forward",
        "text": "target",
        "steer": True,
        "from_queue": False,
        "preserve_queued_steer": True,
        "queued_steer_transaction_protocol": 1,
    })

    assert result["ok"]
    resume.assert_called_once_with(
        "worker-forward",
        "target",
        steer=True,
        _from_queue=False,
        preserve_queued_steer=True,
        queued_steer_transaction_protocol=1,
        queued_delivery_transaction_protocol=0,
    )


def test_old_dashboard_new_worker_uses_native_compatibility_without_transaction(
    monkeypatch,
):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    transaction = mock.Mock(side_effect=AssertionError("worker reacquired transaction"))
    native = mock.Mock(return_value={"ok": True, "via": "codex-steer"})
    monkeypatch.setattr(server, "_codex_queued_steer_transaction", transaction)
    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", native)

    result = server.resume_session_codex(
        "rolling-upgrade", "target", steer=True,
        queued_steer_transaction_protocol=0,
    )

    assert result["ok"]
    transaction.assert_not_called()
    native.assert_called_once_with("rolling-upgrade", "target", steer=True)


def test_second_explicit_steer_cannot_deliver_after_first_claim(router_env):
    sid = "concurrent-steer"
    server._pending_resume_queue[sid] = ["target"]
    entered = threading.Event()
    release = threading.Event()

    def deliver(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "via": "codex-steer"}

    router_env.resume.side_effect = deliver
    first_result = {}
    thread_errors = []

    def steer_into(result):
        try:
            result.update(server._inject_text_into_session_router(
                sid, "target", mode="steer", preserve_queued_steer=True,
            ))
        except BaseException as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=steer_into, args=(first_result,))
    thread.start()
    assert entered.wait(2)
    second_result = {}
    second_thread = threading.Thread(target=steer_into, args=(second_result,))
    second_thread.start()
    time.sleep(0.05)
    assert second_thread.is_alive()
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    second_thread.join(2)

    assert not thread.is_alive()
    assert not second_thread.is_alive()
    assert thread_errors == []
    assert first_result["ok"]
    assert first_result["queued_consumed"] == 1
    assert second_result["code"] == "queued_message_missing"
    assert router_env.resume.call_count == 1


def test_direct_steer_excludes_codex_resume_queue_pump(router_env):
    sid = "steer-vs-pump"
    server._pending_resume_queue[sid] = ["target", "next"]
    entered = threading.Event()
    release = threading.Event()
    thread_errors = []
    steer_result = {}

    def deliver(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "via": "codex-steer"}

    def steer():
        try:
            steer_result.update(server._inject_text_into_session_router(
                sid, "target", mode="steer", preserve_queued_steer=True,
            ))
        except BaseException as exc:
            thread_errors.append(exc)

    router_env.resume.side_effect = deliver
    thread = threading.Thread(target=steer)
    thread.start()
    assert entered.wait(2)

    pump_result = {}
    pump_thread = threading.Thread(
        target=lambda: pump_result.update(server._pump_codex_resume_queue(sid))
    )
    pump_thread.start()
    time.sleep(0.05)
    assert pump_thread.is_alive()
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    pump_thread.join(2)
    assert not thread.is_alive()
    assert not pump_thread.is_alive()
    assert thread_errors == []
    assert steer_result["ok"]
    assert steer_result["queued_consumed"] == 1
    assert pump_result["ok"]
    assert pump_result["delivered"]


def test_codex_claim_and_pump_share_cross_process_session_ownership(tmp_path):
    sid = "cross-process-session"
    pending_file = tmp_path / "pending-inputs.json"
    pending_file.write_text(json.dumps({
        "resume_queue": {sid: ["target"]},
        "devin_steers": {},
        "terminal_queue": {},
        "auto_resume_opt_in": {},
    }))
    context = multiprocessing.get_context("spawn")
    stale_loaded = context.Event()
    claimed = context.Event()
    release = context.Event()
    released = context.Event()
    results = context.Queue()
    claimant = context.Process(
        target=_cross_process_claim_worker,
        args=(
            pending_file, sid, stale_loaded, claimed, release, released, results,
        ),
    )
    pump = context.Process(
        target=_cross_process_pump_worker,
        args=(
            pending_file, sid, stale_loaded, claimed, release, released, results,
        ),
    )
    claimant.start()
    pump.start()
    try:
        claim_result = results.get(timeout=10)
        release.set()
        first_pump = results.get(timeout=10)
        by_label = {claim_result[0]: claim_result, first_pump[0]: first_pump}
        assert "error" not in by_label, by_label.get("error")
        assert by_label["claim"][2]["item"] == "target"
        assert by_label["first-pump"][2] == {"ok": True, "empty": True}
        assert by_label["first-pump"][3] == []
        assert by_label["claim"][1] != by_label["first-pump"][1]
        assert released.is_set()
    finally:
        release.set()
        claimant.join(10)
        pump.join(10)
        if claimant.is_alive():
            claimant.terminate()
            claimant.join(5)
        if pump.is_alive():
            pump.terminate()
            pump.join(5)
    assert claimant.exitcode == 0
    assert pump.exitcode == 0


def test_two_process_session_rmw_neither_resurrects_nor_loses_rows(tmp_path):
    pending_file = tmp_path / "pending-inputs.json"
    pending_file.write_text(json.dumps({
        "resume_queue": {"sid-a": ["old-a"], "sid-b": ["old-b"]},
        "devin_steers": {"other-sid": "preserve-steer"},
        "terminal_queue": {},
        "auto_resume_opt_in": {"sid-a": True, "other-sid": True},
    }))
    context = multiprocessing.get_context("spawn")
    both_loaded = context.Queue()
    first_saved = context.Event()
    results = context.Queue()
    delete_writer = context.Process(
        target=_cross_process_rmw_worker,
        args=(pending_file, "sid-a", "delete", both_loaded, first_saved, results),
    )
    update_writer = context.Process(
        target=_cross_process_rmw_worker,
        args=(pending_file, "sid-b", "update", both_loaded, first_saved, results),
    )
    delete_writer.start()
    update_writer.start()
    assert both_loaded.get(timeout=10) != both_loaded.get(timeout=10)
    outcomes = [results.get(timeout=10), results.get(timeout=10)]
    delete_writer.join(10)
    update_writer.join(10)

    assert all(outcome[0] != "error" for outcome in outcomes), outcomes
    assert all(outcome[2] is True for outcome in outcomes)
    payload = json.loads(pending_file.read_text())
    assert "sid-a" not in payload["resume_queue"]
    assert payload["resume_queue"]["sid-b"] == ["new-b"]
    assert payload["pending_entry_ids"]["resume_queue"]["sid-b"][0]
    assert payload["devin_steers"] == {"other-sid": "preserve-steer"}
    assert payload["auto_resume_opt_in"] == {
        "sid-a": True,
        "other-sid": True,
    }
    assert delete_writer.exitcode == 0
    assert update_writer.exitcode == 0


def test_stale_same_session_enqueue_cannot_resurrect_worker_claim(tmp_path):
    sid = "same-session-race"
    pending_file = tmp_path / "pending-inputs.json"
    pending_file.write_text(json.dumps({
        "resume_queue": {sid: ["target", "keep"]},
        "devin_steers": {},
        "terminal_queue": {},
        "auto_resume_opt_in": {},
    }))
    context = multiprocessing.get_context("spawn")
    stale_loaded = context.Event()
    claimed = context.Event()
    release = context.Event()
    released = context.Event()
    enqueue_finished = context.Event()
    results = context.Queue()
    claimant = context.Process(
        target=_cross_process_claim_worker,
        args=(
            pending_file, sid, stale_loaded, claimed, release, released, results,
        ),
    )
    stale_enqueue = context.Process(
        target=_cross_process_stale_enqueue_worker,
        args=(
            pending_file, sid, stale_loaded, claimed, enqueue_finished, results,
        ),
    )
    claimant.start()
    stale_enqueue.start()
    assert claimed.wait(10)
    time.sleep(0.1)
    assert not enqueue_finished.is_set()
    release.set()
    outcomes = [results.get(timeout=10), results.get(timeout=10)]
    claimant.join(10)
    stale_enqueue.join(10)

    assert released.is_set()
    assert all(outcome[0] != "error" for outcome in outcomes), outcomes
    payload = json.loads(pending_file.read_text())
    assert payload["resume_queue"][sid] == ["keep", "new-row"]
    metadata = payload["pending_entry_ids"]["resume_queue"][sid]
    assert len({row["id"] for row in metadata}) == 2
    assert all(row["fingerprint"] for row in metadata)
    assert claimant.exitcode == 0
    assert stale_enqueue.exitcode == 0


def test_pending_save_enforces_authoritative_session_transaction():
    import inspect

    source = inspect.getsource(server._save_pending_inputs)
    mutation_source = inspect.getsource(server._mutate_pending_inputs)
    assert "_codex_queue_pump_lock" in mutation_source
    assert "_refresh_pending_inputs_for_session" in mutation_source
    assert "mutation()" in mutation_source


def test_all_pending_input_writers_identify_affected_sessions():
    repo_root = Path(server.__file__).resolve().parent
    offenders = []
    for source_path in (repo_root / "ccc_server").glob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else ""
            )
            if name == "_save_pending_inputs" and not (node.args or node.keywords):
                offenders.append(f"{source_path.name}:{node.lineno}")
    assert offenders == []


def test_missing_pending_file_refreshes_as_empty_and_ingests_handoff(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "fresh-install-handoff"
    pending_file = tmp_path / "missing-pending-inputs.json"
    handoff_dir = tmp_path / "handoffs"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", handoff_dir)
    monkeypatch.setattr(
        server, "_refresh_pending_inputs_for_session",
        pending_inputs_module._refresh_pending_inputs_for_session,
    )
    assert server._write_pending_input_handoff(sid, "from-worker") is not None

    assert server._refresh_pending_inputs_for_session(sid)
    assert server._pending_resume_queue.get(sid) is None
    assert list(server._pending_terminal_input_queue[sid]) == ["from-worker"]


def test_fresh_install_historical_steer_reaches_native_codex(
    router_env, monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "fresh-install-historical-steer"
    monkeypatch.setattr(
        server, "PENDING_INPUTS_FILE", tmp_path / "missing.json",
    )
    monkeypatch.setattr(
        server, "PENDING_INPUT_HANDOFF_DIR", tmp_path / "handoffs",
    )
    monkeypatch.setattr(
        server, "_refresh_pending_inputs_for_session",
        pending_inputs_module._refresh_pending_inputs_for_session,
    )
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}

    result = server._inject_text_into_session_router(
        sid, "historical correction", mode="steer",
    )

    assert result["ok"]
    router_env.resume.assert_called_once_with(
        sid, "historical correction", steer=True,
    )


@pytest.mark.parametrize("code", [
    "queued_claim_persistence_failed",
    "queued_claim_refresh_failed",
    "queued_rollback_persistence_failed",
    "queued_handoff_commit_failed",
    "queued_ack_capacity_exhausted",
])
def test_finalizer_preserves_transaction_terminal_error(code):
    original = {"ok": False, "code": code, "error": "terminal"}
    assert server._finalize_queued_steer_result(
        "terminal-error", "target", original,
    ) == original


def test_ack_capacity_rejects_without_evicting_live_transaction():
    now = time.monotonic()
    with server._CODEX_QUEUED_STEER_ACK_LOCK:
        for index in range(server._CODEX_QUEUED_STEER_ACK_MAX_TOTAL):
            server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS[
                (f"capacity-{index}", "target")
            ] = [{
                "token_id": f"capacity-token-{index}",
                "state": ("unbound", "pending", "committed")[index % 3],
                "acknowledged": False,
                "created_at": now + index,
                "expires_at": now + 1000,
            }]
    admission = server._begin_codex_queued_steer_ack_suppression(
        "capacity-new", "target",
    )

    assert admission["code"] == "queued_ack_capacity_exhausted"
    entries = sum(
        len(values)
        for values in server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS.values()
    )
    assert entries == server._CODEX_QUEUED_STEER_ACK_MAX_TOTAL
    assert ("capacity-0", "target") in server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS


def test_ack_capacity_restores_claim_before_native_delivery(router_env):
    sid = "capacity-claim-restore"
    server._pending_resume_queue[sid] = ["target"]
    now = time.monotonic()
    with server._CODEX_QUEUED_STEER_ACK_LOCK:
        for index in range(server._CODEX_QUEUED_STEER_ACK_MAX_TOTAL):
            server._CODEX_QUEUED_STEER_ACK_SUPPRESSIONS[
                (f"capacity-live-{index}", "target")
            ] = [{
                "token_id": f"capacity-live-token-{index}",
                "state": "pending",
                "acknowledged": False,
                "created_at": now + index,
                "expires_at": now + 1000,
            }]

    result = server._inject_text_into_session_router(
        sid, "target", mode="steer", preserve_queued_steer=True,
    )

    assert result["code"] == "queued_ack_capacity_exhausted"
    assert server._pending_resume_queue[sid] == ["target"]
    router_env.resume.assert_not_called()


def test_rollback_stops_when_authoritative_refresh_fails(monkeypatch):
    claim = {
        "session_id": "rollback-refresh-failure",
        "queue_name": "resume",
        "index": 0,
        "item": "target",
    }
    save = mock.Mock(return_value=True)
    monkeypatch.setattr(
        server, "_refresh_pending_inputs_for_session", lambda sid: False,
    )
    monkeypatch.setattr(server, "_save_pending_inputs", save)

    assert not server._restore_pending_input_claim(claim)
    assert server._pending_resume_queue.get(claim["session_id"]) is None
    save.assert_not_called()


def test_handoff_cleanup_failure_leaves_atomic_tombstone(monkeypatch, tmp_path):
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text("{}")
    item = server._PendingInputHandoff("target", "tombstone", handoff_path)
    real_unlink = Path.unlink

    def fail_tombstone_unlink(target_path, *args, **kwargs):
        if str(target_path).endswith(".delivered"):
            raise OSError("cleanup failed")
        return real_unlink(target_path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_unlink)

    assert not server._complete_pending_input_handoff(item)
    assert not handoff_path.exists()
    assert list(tmp_path.glob("*.json")) == []
    assert list(tmp_path.glob("*.delivered"))


class _ImmediateThread:
    def __init__(self, target, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_devin_proof_removal_persists_steer_field(monkeypatch, tmp_path):
    sid = "devincli-proof-steer"
    text_value = "steer target"
    pending_file = tmp_path / "pending-inputs.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_devin_steers[sid] = text_value
    assert server._save_pending_inputs(
        {sid}, include_devin_steers=True,
    )
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        server, "_devin_cli_prompt_history_count", lambda *args: 1,
    )

    server._start_devin_delivery_proof_watchdog(
        SimpleNamespace(poll=lambda: None), sid, text_value, time.time(),
        delivery_slot="steer",
    )

    payload = json.loads(pending_file.read_text())
    assert sid not in payload["devin_steers"]


def test_devin_unproven_restore_persists_steer_field(monkeypatch, tmp_path):
    sid = "devincli-unproven-steer"
    text_value = "restore target"
    pending_file = tmp_path / "pending-inputs.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    assert server._save_pending_inputs(
        {sid}, include_devin_steers=True,
    )
    monkeypatch.setattr(server.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        server, "_devin_cli_prompt_history_count", lambda *args: 0,
    )

    server._start_devin_delivery_proof_watchdog(
        SimpleNamespace(poll=lambda: 1), sid, text_value, time.time(),
        delivery_slot="steer",
    )

    payload = json.loads(pending_file.read_text())
    assert payload["devin_steers"][sid] == text_value


def test_codex_session_lock_releases_thread_lock_when_unlock_raises(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    lock = pending_inputs_module._CodexQueueSessionLock(tmp_path / "lock")
    thread_lock = mock.Mock()
    handle = mock.Mock()
    handle.fileno.return_value = 7
    lock._thread_lock = thread_lock
    lock._depth = 1
    lock._handle = handle
    monkeypatch.setattr(
        pending_inputs_module.fcntl, "flock",
        mock.Mock(side_effect=OSError("unlock failed")),
    )

    with pytest.raises(OSError, match="unlock failed"):
        lock.release()
    thread_lock.release.assert_called_once_with()


def test_global_pending_file_lock_is_reentrant(tmp_path):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(
        target=_cross_process_nested_file_lock_worker,
        args=(tmp_path / "pending-inputs.json", results),
    )
    process.start()
    process.join(2)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 0
    assert results.get(timeout=2)[0] == "nested"


def test_missing_handoff_source_and_tombstone_clears_metadata(tmp_path):
    missing = tmp_path / "gone.json"
    item = server._PendingInputHandoff("target", "stale-metadata", missing)
    server._pending_terminal_handoff_ids[item.handoff_id] = missing

    assert server._complete_pending_input_handoff(item)
    assert item.handoff_id not in server._pending_terminal_handoff_ids


def test_auto_resume_paths_acquire_session_before_barrier():
    import inspect

    for function in (
        server._queue_terminal_input,
        server._requeue_terminal_input_front,
        server._disable_session_auto_resume,
        server._write_pending_input_handoff,
    ):
        source = inspect.getsource(function)
        assert source.index("_codex_queue_pump_lock") < source.index(
            "_auto_resume_exclusive_lock"
        )


def test_reverse_order_auto_resume_operations_finish(monkeypatch, tmp_path):
    sid = "auto-resume-lock-order"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._auto_resume_opt_in[sid] = True
    assert server._save_pending_inputs({sid}, include_auto_resume=True)
    start = threading.Event()
    results = []

    def enqueue():
        start.wait()
        results.append(server._queue_terminal_input(sid, "continue"))

    def disable():
        start.wait()
        results.append(server._disable_session_auto_resume(sid))

    threads = [threading.Thread(target=enqueue), threading.Thread(target=disable)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2


def test_handoff_ingest_pop_save_delivery_cannot_redeliver(monkeypatch, tmp_path):
    sid = "handoff-baseline-delivery"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", tmp_path / "handoffs")
    assert server._save_pending_inputs({sid})
    assert server._write_pending_input_handoff(sid, "same-text") is not None
    assert server._ingest_pending_input_handoffs() == 1
    with server._pending_terminal_input_lock:
        delivered = server._pending_terminal_input_queue[sid].pop(0)
        server._pending_terminal_input_queue.pop(sid, None)
    assert server._save_pending_inputs({sid})
    assert server._complete_pending_input_handoff(delivered)

    assert server._pending_terminal_input_queue.get(sid) is None
    assert server._ingest_pending_input_handoffs() == 0


def _run_resume_operations(monkeypatch, tmp_path, initial, operations):
    sid = "operation-corpus"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = list(initial)
    assert server._save_pending_inputs({sid})
    result = server._apply_pending_input_operations(sid, operations)
    assert result["ok"]
    return list(server._pending_resume_queue.get(sid) or [])


def test_delta_duplicate_deletion_is_occurrence_aware(monkeypatch, tmp_path):
    assert _run_resume_operations(monkeypatch, tmp_path, ["x", "x", "tail"], [{
        "field": "resume", "action": "remove_matching", "match": "x",
        "occurrence": 1,
    }]) == ["x", "tail"]


def test_delta_middle_insert_uses_retained_neighbor_anchor(monkeypatch, tmp_path):
    assert _run_resume_operations(
        monkeypatch, tmp_path, ["front", "a", "b", "tail"], [{
            "field": "resume", "action": "insert_before_matching",
            "match": "b", "value": "middle",
        }],
    ) == ["front", "a", "middle", "b", "tail"]


def test_delta_concurrent_append_keeps_transaction_order(monkeypatch, tmp_path):
    assert _run_resume_operations(monkeypatch, tmp_path, ["a", "concurrent"], [{
        "field": "resume", "action": "append_tail", "value": "local",
    }]) == ["a", "concurrent", "local"]


def test_delta_front_insert_stays_at_front(monkeypatch, tmp_path):
    assert _run_resume_operations(monkeypatch, tmp_path, ["a", "concurrent-tail"], [{
        "field": "resume", "action": "insert_front", "value": "front",
    }]) == ["front", "a", "concurrent-tail"]


def test_delta_distinguishes_handoff_and_plain_identical_text(monkeypatch, tmp_path):
    sid = "handoff-plain-corpus"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", tmp_path / "handoffs")
    server._pending_terminal_input_queue[sid] = ["same", "tail"]
    assert server._save_pending_inputs({sid})
    assert server._write_pending_input_handoff(sid, "same", front=True)
    assert server._ingest_pending_input_handoffs() == 1
    handoff = server._pending_terminal_input_queue[sid][0]
    result_tx = server._apply_pending_input_operations(sid, [{
        "field": "terminal", "action": "remove_matching",
        "identity": "handoff_id", "match": handoff.handoff_id,
    }])
    result = list(server._pending_terminal_input_queue[sid])
    assert result_tx["ok"]
    assert result == ["same", "tail"]
    assert not isinstance(result[0], server._PendingInputHandoff)


def test_gemini_queue_save_does_not_persist_devin_steer_flag():
    import inspect

    source = inspect.getsource(server.resume_session_gemini)
    assert "include_devin_steers" not in source


def test_production_writers_use_explicit_pending_mutations():
    repo_root = Path(server.__file__).resolve().parent
    offenders = []
    direct_persist = []
    ignored_transactions = []
    for source_path in (repo_root / "ccc_server").glob("*.py"):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name)
                else ""
            )
            if name == "_save_pending_inputs":
                offenders.append(f"{source_path.name}:{node.lineno}")
            if name == "_persist_pending_inputs_current":
                owner = node
                while owner in parents and not isinstance(owner, ast.FunctionDef):
                    owner = parents[owner]
                if not (
                    source_path.name == "pending_inputs.py"
                    and isinstance(owner, ast.FunctionDef)
                    and owner.name in {
                        "_mutate_pending_inputs",
                        "_refresh_pending_inputs_for_session",
                    }
                ):
                    direct_persist.append(f"{source_path.name}:{node.lineno}")
            if name in {"_apply_pending_input_operations", "_mutate_pending_inputs"}:
                if isinstance(parents.get(node), ast.Expr):
                    ignored_transactions.append(f"{source_path.name}:{node.lineno}")
    assert offenders == []
    assert direct_persist == []
    assert ignored_transactions == []
    pending_source = (repo_root / "ccc_server" / "pending_inputs.py").read_text()
    assert "_pending_inputs_session_baselines" not in pending_source
    assert "_apply_pending_queue_delta" not in pending_source


def test_deliver_barrier_acquires_session_first():
    import inspect

    source = inspect.getsource(server._deliver_with_auto_resume_barrier)
    assert source.index("_codex_queue_pump_lock") < source.index(
        "_auto_resume_exclusive_lock"
    )
    ingest_source = inspect.getsource(server._ingest_pending_input_handoffs)
    assert ingest_source.index("_load_pending_inputs") < ingest_source.index(
        "_auto_resume_exclusive_lock"
    )


def test_mutation_exception_rolls_back_memory_and_disk(monkeypatch, tmp_path):
    sid = "mutation-exception"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["first", "second"]
    assert server._save_pending_inputs({sid})

    def explode():
        server._pending_resume_queue[sid].pop(0)
        raise RuntimeError("mutation exploded")

    result = server._mutate_pending_inputs({sid}, explode)

    assert not result["ok"]
    assert "value" not in result
    assert server._pending_resume_queue[sid] == ["first", "second"]
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "first", "second",
    ]


def test_persist_failure_rolls_back_memory_and_hides_value(monkeypatch, tmp_path):
    sid = "mutation-persist-failure"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = ["first", "second"]
    assert server._save_pending_inputs({sid})
    monkeypatch.setattr(server, "_persist_pending_inputs_current", lambda *a, **k: False)

    result = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "pop_head",
    }])

    assert not result["ok"]
    assert "value" not in result
    assert server._pending_resume_queue[sid] == ["first", "second"]


def test_authority_snapshot_hash_and_stat_come_from_one_open_fd(
    monkeypatch, tmp_path,
):
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    pending_file.write_text(json.dumps({
        "resume_queue": {"authority-fd": ["old"]},
        "terminal_queue": {},
    }))

    with server._pending_inputs_file_exclusive_lock():
        snapshot = server._read_pending_inputs_authority_unlocked(
            missing_is_empty=False,
        )

    assert snapshot["payload"]["resume_queue"]["authority-fd"] == ["old"]
    assert snapshot["primary_hash"] == __import__("hashlib").sha256(
        pending_file.read_bytes()
    ).hexdigest()
    current = pending_file.stat()
    assert snapshot["primary_stat"] == (
        current.st_ino, current.st_mtime_ns, current.st_size,
    )


def test_compare_and_mutate_rejects_stale_primary_authority(
    monkeypatch, tmp_path,
):
    sid = "expected-authority-conflict"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["captured", "newer"]
    assert server._save_pending_inputs({sid})

    result = server._mutate_pending_inputs(
        {sid},
        lambda: server._pending_resume_queue[sid].pop(0),
        expected_authority={
            "primary_hash": "0" * 64,
            "revisions": {"resume_queue": {sid: 0}},
        },
    )

    assert result == {"ok": False, "code": "pending_input_authority_conflict"}
    assert server._pending_resume_queue[sid] == ["captured", "newer"]
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "captured", "newer",
    ]


def test_compare_and_mutate_rechecks_after_temp_fsync_before_replace(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "expected-authority-pre-replace"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["captured", "tail"]
    assert server._save_pending_inputs({sid})
    with server._pending_inputs_file_exclusive_lock():
        captured = server._read_pending_inputs_authority_unlocked(
            missing_is_empty=False,
        )
    expected = {
        "primary_hash": captured["primary_hash"],
        "revisions": {"resume_queue": {sid: 0}},
    }
    newer = json.loads(pending_file.read_text())
    newer["resume_queue"][sid] = ["new-writer"]
    newer["pending_queue_revisions"]["resume_queue"][sid] = 1
    real_read = server._read_pending_inputs_authority_unlocked
    reads = {"count": 0}

    def interleaving_read(*, missing_is_empty):
        reads["count"] += 1
        snapshot = real_read(missing_is_empty=missing_is_empty)
        if reads["count"] == 2:
            pending_file.write_text(json.dumps(newer, indent=2, sort_keys=True))
        return snapshot

    monkeypatch.setattr(
        pending_inputs_module,
        "_read_pending_inputs_authority_unlocked",
        interleaving_read,
    )
    result = server._mutate_pending_inputs(
        {sid}, lambda: server._pending_resume_queue[sid].pop(0),
        expected_authority=expected,
    )

    assert result == {"ok": False, "code": "pending_input_authority_conflict"}
    assert reads["count"] >= 3
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "new-writer",
    ]


def test_deferred_exact_id_removal_rechecks_authority_before_write(
    monkeypatch, tmp_path,
):
    sid = "deferred-compare-and-mutate"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["same", "tail"]
    assert server._save_pending_inputs({sid})
    snapshot = server._snapshot_matching_pending_input_id_quick(sid, "same")
    assert snapshot["pending_id"]
    newer = json.loads(pending_file.read_text())
    newer["resume_queue"][sid] = ["same", "newer"]
    newer["pending_entry_ids"]["resume_queue"][sid] = [
        {"id": "new-row-id", "fingerprint": server._pending_text_fingerprint("same")},
        {"id": "new-tail-id", "fingerprint": server._pending_text_fingerprint("newer")},
    ]
    revisions = newer["pending_queue_revisions"]["resume_queue"]
    revisions[sid] = int(revisions.get(sid) or 0) + 1
    refresh_calls = {"count": 0}

    def replace_during_refresh(_session_id):
        refresh_calls["count"] += 1
        if refresh_calls["count"] == 1:
            pending_file.write_text(json.dumps(newer, indent=2, sort_keys=True))
        return True

    monkeypatch.setattr(
        server, "_refresh_pending_inputs_for_session", replace_during_refresh,
    )

    assert server._consume_deferred_pending_snapshot(sid, snapshot) == 0
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "same", "newer",
    ]


def test_busy_reader_defers_stable_id_with_full_authority_snapshot(monkeypatch):
    sid = "busy-reader-authority"
    pending = {
        "queue_name": "resume",
        "pending_id": "captured-id",
        "revision": 7,
        "primary_hash": "a" * 64,
        "primary_stat": (1, 2, 3),
    }

    class BusyOwnership:
        def acquire(self, blocking=True):
            assert blocking is False
            return False

    started = {}

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            started["target"] = target
            started["args"] = args

        def start(self):
            started["started"] = True

    monkeypatch.setattr(server, "_codex_queue_pump_lock", lambda _sid: BusyOwnership())
    monkeypatch.setattr(
        server, "_snapshot_matching_pending_input_id_quick",
        lambda *_args: pending,
    )
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    assert server._reconcile_codex_delivery_ack_nonblocking(sid, "same") == 0
    assert started == {
        "target": server._consume_deferred_pending_snapshot,
        "args": (sid, pending),
        "started": True,
    }


def test_post_replace_old_writer_conflict_blocks_without_rollback_or_retry(
    monkeypatch, tmp_path,
):
    sid = "post-replace-authority-conflict"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["captured", "later"]
    assert server._save_pending_inputs({sid})
    old_writer_payload = json.loads(pending_file.read_text())
    old_writer_payload["resume_queue"][sid] = ["old-writer"]
    real_replace = os.replace
    replaced = {"primary": 0}

    def replace_then_clobber(source, destination):
        real_replace(source, destination)
        if Path(destination) == pending_file:
            replaced["primary"] += 1
            pending_file.write_text(json.dumps(old_writer_payload))

    monkeypatch.setattr(os, "replace", replace_then_clobber)
    result = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "pop_head",
    }])

    assert result["ok"] is False
    assert result["code"] == "pending_primary_commit_uncertain"
    assert replaced["primary"] == 1
    assert server._pending_resume_queue[sid] == ["later"]
    assert server._pending_input_recovery_blocked(sid)
    status = server._pending_input_recovery_status(sid)
    assert status["primary_conflicts"] == 1
    assert status["primary_conflicts_volatile"] == 0
    assert status["recovery_volatile"] is False
    assert status["restart_safe"] is True
    assert status["metadata_backup"]["durability_uncertain"] is True
    server._pending_primary_conflicts.clear()
    server._pending_metadata_backup_health.update({
        "durability_uncertain": False,
        "durability_error": None,
    })
    with server._pending_inputs_file_exclusive_lock():
        server._read_pending_inputs_authority_unlocked(missing_is_empty=False)
    restarted = server._pending_input_recovery_status(sid)
    assert restarted["blocked"] is True
    assert restarted["primary_conflicts"] == 1
    assert restarted["metadata_backup"]["durability_uncertain"] is True
    monkeypatch.setattr(os, "replace", real_replace)
    server._pending_resume_queue[sid] = ["unrelated-write"]
    assert not server._save_pending_inputs({sid})
    still_blocked = server._pending_input_recovery_status(sid)
    assert still_blocked["blocked"] is True
    assert still_blocked["metadata_backup"]["durability_uncertain"] is True


def test_normalized_read_initializes_backup_health_and_success_clears_timestamp(
    monkeypatch, tmp_path,
):
    sid = "backup-health-read"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["first"]
    assert server._save_pending_inputs({sid})
    backup_file = server._pending_inputs_metadata_backup_path()
    backup_file.write_text('{"integrity":"corrupt"}')
    server._pending_metadata_backup_health.update({
        "degraded": False,
        "last_error": None,
        "last_error_at": None,
    })

    with server._pending_inputs_file_exclusive_lock():
        assert server._read_pending_inputs_payload_unlocked(
            missing_is_empty=False,
        ) is not None
    degraded = server._pending_input_recovery_status(sid)["metadata_backup"]
    assert degraded["degraded"] is True
    assert degraded["last_error"] == "metadata_backup_invalid"
    assert degraded["last_error_at"] is not None

    server._pending_resume_queue[sid].append("second")
    assert server._save_pending_inputs({sid})
    healthy = server._pending_input_recovery_status(sid)["metadata_backup"]
    assert healthy["degraded"] is False
    assert healthy["last_error"] is None
    assert healthy["last_error_at"] is None


def test_refresh_dirty_ids_is_read_decode_only(monkeypatch, tmp_path):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "dirty-refresh-read-only"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    pending_file.write_text(json.dumps({
        "resume_queue": {sid: ["legacy"]},
        "terminal_queue": {},
        "devin_steers": {},
        "auto_resume_opt_in": {},
        "pending_entry_ids": {"version": 1, "resume_queue": {}, "terminal_queue": {}},
    }))
    persist = mock.Mock(return_value=True)
    monkeypatch.setattr(server, "_persist_pending_inputs_current", persist)

    result = pending_inputs_module._refresh_pending_inputs_for_session(sid)

    assert result["ok"] is True
    assert result["ids_dirty"] is True
    assert result["authority"]["primary_hash"] == hashlib.sha256(
        pending_file.read_bytes()
    ).hexdigest()
    persist.assert_not_called()


def test_dirty_id_repair_cas_stops_before_mutation_when_writer_races(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "dirty-repair-race"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    pending_file.write_text(json.dumps({
        "resume_queue": {sid: ["legacy"]},
        "terminal_queue": {},
        "devin_steers": {},
        "auto_resume_opt_in": {},
        "applied_claim_journal_ids": {},
        "pending_entry_ids": {"version": 1, "resume_queue": {}, "terminal_queue": {}},
        "pending_queue_revisions": {"resume_queue": {}, "terminal_queue": {}},
    }, indent=2, sort_keys=True))
    newer = json.loads(pending_file.read_text())
    newer["resume_queue"][sid] = ["newer-authority"]
    real_read = pending_inputs_module._read_pending_inputs_authority_unlocked
    reads = {"count": 0}
    mutation = mock.Mock(return_value=True)

    def race_after_refresh(*, missing_is_empty):
        reads["count"] += 1
        if reads["count"] == 2:
            pending_file.write_text(json.dumps(newer, indent=2, sort_keys=True))
        return real_read(missing_is_empty=missing_is_empty)

    monkeypatch.setattr(
        pending_inputs_module,
        "_read_pending_inputs_authority_unlocked",
        race_after_refresh,
    )
    monkeypatch.setattr(
        server,
        "_refresh_pending_inputs_for_session",
        pending_inputs_module._refresh_pending_inputs_for_session,
    )
    result = server._mutate_pending_inputs({sid}, mutation)

    assert result["code"] == "pending_input_authority_conflict"
    mutation.assert_not_called()
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "newer-authority",
    ]


def test_conflict_blocks_generic_queue_and_flag_mutations(monkeypatch, tmp_path):
    sid = "all-writers-conflict-block"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["existing"]
    assert server._save_pending_inputs({sid})
    before = pending_file.read_bytes()
    server._pending_primary_conflicts[sid] = {
        "session_id": sid,
        "intended_hash": "a" * 64,
        "base_hash": "b" * 64,
        "observed_hash": "c" * 64,
        "recorded_at": time.time(),
    }

    for operation in (
        {"field": "resume", "action": "append_tail", "value": "enqueue"},
        {"field": "resume", "action": "remove_matching", "match": "existing"},
        {"field": "terminal", "action": "append_tail", "value": "terminal"},
        {"field": "auto_resume", "action": "set", "value": True},
    ):
        result = server._apply_pending_input_operations(sid, [operation])
        assert result == {
            "ok": False,
            "code": "pending_primary_commit_uncertain",
        }
    assert pending_file.read_bytes() == before


def test_primary_conflict_record_has_complete_transaction_and_integrity(
    monkeypatch, tmp_path,
):
    sid = "complete-conflict-record"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["claim", "later"]
    assert server._save_pending_inputs({sid})
    base_hash = hashlib.sha256(pending_file.read_bytes()).hexdigest()
    old_writer = json.loads(pending_file.read_text())
    old_writer["resume_queue"][sid] = ["observed"]
    real_replace = os.replace

    def replace_then_clobber(source, destination):
        real_replace(source, destination)
        if Path(destination) == pending_file:
            pending_file.write_text(json.dumps(old_writer))

    monkeypatch.setattr(os, "replace", replace_then_clobber)
    result = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "pop_head",
    }])
    assert result["code"] == "pending_primary_commit_uncertain"
    record_path = next(server._pending_primary_conflict_dir().glob("*.json"))
    record = json.loads(record_path.read_text())
    integrity = record.pop("integrity")

    assert record["version"] == 2
    assert record["affected_sessions"] == [sid]
    assert record["base_hash"] == base_hash
    assert record["observed_hash"] == hashlib.sha256(
        pending_file.read_bytes()
    ).hexdigest()
    assert hashlib.sha256(record["intended_json"].encode()).hexdigest() == record[
        "intended_hash"
    ]
    assert json.loads(record["intended_json"])["resume_queue"][sid] == ["later"]
    assert integrity == hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_incompatible_active_writer_blocks_until_capability_refresh(
    monkeypatch, tmp_path,
):
    import ccc_server.pending_inputs as pending_inputs_module

    sid = "writer-protocol-gate"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = ["existing"]
    assert server._save_pending_inputs({sid})
    compatible = {"value": False}
    monkeypatch.setattr(
        pending_inputs_module,
        "_pending_writer_compatibility_status",
        lambda: {
            "ok": compatible["value"],
            "restart_required": not compatible["value"],
            "incompatible_writers": ["worker:old"] if not compatible["value"] else [],
        },
    )

    blocked = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "append_tail", "value": "new",
    }])
    assert blocked["code"] == "pending_writer_upgrade_required"
    compatible["value"] = True
    allowed = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "append_tail", "value": "new",
    }])
    assert allowed["ok"] is True


def test_dashboard_does_not_route_pending_write_to_incompatible_worker(
    monkeypatch,
):
    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    route = mock.Mock(return_value={"ok": True, "routed": True})
    monkeypatch.setattr(server, "_control_plane_engine_call", route)
    monkeypatch.setattr(server, "_pending_writer_compatibility_status", lambda: {
        "ok": False,
        "restart_required": True,
        "incompatible_writers": ["worker:old"],
    })

    result = server.resume_session_codex(
        "old-worker-route", "queued", _from_queue=True,
        queued_delivery_transaction_protocol=1,
    )

    assert result == {
        "ok": False,
        "code": "pending_writer_upgrade_required",
        "restart_required": True,
        "incompatible_writers": ["worker:old"],
        "error": (
            "Restart the CCC dashboard and worker before changing pending input"
        ),
    }
    route.assert_not_called()


def _install_primary_conflict_fixture(server_module, sid, intended_payload):
    pending_file = server_module.PENDING_INPUTS_FILE
    base_hash = hashlib.sha256(pending_file.read_bytes()).hexdigest()
    sidecar = intended_payload["pending_entry_ids"]
    for field in ("resume_queue", "terminal_queue"):
        rows = intended_payload[field].get(sid) or []
        metadata = (sidecar.get(field) or {}).get(sid) or []
        if len(rows) == len(metadata):
            for text, entry in zip(rows, metadata):
                entry["fingerprint"] = server_module._pending_text_fingerprint(text)
    intended_bytes = json.dumps(
        intended_payload, indent=2, sort_keys=True,
    ).encode("utf-8")
    import ccc_server.pending_inputs as pending_inputs_module
    pending_inputs_module._record_pending_primary_conflict(
        {sid}, intended_payload, intended_bytes,
        base_hash, base_hash, "test commit uncertainty",
    )
    return base_hash, hashlib.sha256(intended_bytes).hexdigest(), intended_bytes


def test_conflict_reconcile_auto_applies_when_base_still_current(
    monkeypatch, tmp_path,
):
    sid = "reconcile-base-current"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    intended["resume_queue"][sid] = ["intended"]
    _install_primary_conflict_fixture(server, sid, intended)

    result = server._reconcile_pending_primary_conflict(sid)

    assert result["ok"] is True
    assert result["resolution"] == "accept_intended"
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "intended",
    ]
    assert not server._pending_input_recovery_blocked(sid)


def test_conflict_reconcile_marks_commit_won_when_intended_is_current(
    monkeypatch, tmp_path,
):
    sid = "reconcile-intended-current"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    intended["resume_queue"][sid] = ["intended"]
    _, _, intended_bytes = _install_primary_conflict_fixture(server, sid, intended)
    pending_file.write_bytes(intended_bytes)

    result = server._reconcile_pending_primary_conflict(sid)

    assert result["ok"] is True
    assert result["resolution"] == "commit_won"
    assert not server._pending_input_recovery_blocked(sid)


def test_divergent_conflict_requires_explicit_resolution_and_exposes_options(
    monkeypatch, tmp_path,
):
    sid = "reconcile-divergent"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    intended["resume_queue"][sid] = ["intended"]
    base_hash, intended_hash, _ = _install_primary_conflict_fixture(
        server, sid, intended,
    )
    divergent = json.loads(pending_file.read_text())
    divergent["resume_queue"][sid] = ["divergent"]
    pending_file.write_text(json.dumps(divergent, indent=2, sort_keys=True))
    current_hash = hashlib.sha256(pending_file.read_bytes()).hexdigest()

    unresolved = server._reconcile_pending_primary_conflict(sid)
    status = server._pending_input_recovery_status(sid)

    assert unresolved == {
        "ok": False,
        "code": "pending_primary_commit_uncertain",
        "resolution_required": True,
        "base_hash": base_hash,
        "intended_hash": intended_hash,
        "current_hash": current_hash,
        "resolution_options": ["accept_intended", "accept_current"],
    }
    conflict = status["conflict_details"][0]
    assert conflict.items() >= {
        "affected_sessions": [sid],
        "base_hash": base_hash,
        "intended_hash": intended_hash,
        "current_hash": current_hash,
        "resolution_options": ["accept_intended", "accept_current"],
        "durable": True,
    }.items()

    accepted = server._reconcile_pending_primary_conflict(
        sid, resolution="accept_current",
    )
    assert accepted["ok"] is True
    assert accepted["resolution"] == "accept_current"
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "divergent",
    ]


def test_divergent_conflict_can_explicitly_accept_intended(monkeypatch, tmp_path):
    sid = "reconcile-accept-intended"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    intended["resume_queue"][sid] = ["intended"]
    _install_primary_conflict_fixture(server, sid, intended)
    divergent = json.loads(pending_file.read_text())
    divergent["resume_queue"][sid] = ["divergent"]
    pending_file.write_text(json.dumps(divergent, indent=2, sort_keys=True))

    result = server._reconcile_pending_primary_conflict(
        sid, resolution="accept_intended",
    )

    assert result == {"ok": True, "resolution": "accept_intended"}
    assert json.loads(pending_file.read_text())["resume_queue"][sid] == [
        "intended",
    ]


def test_corrupt_primary_conflict_is_quarantined_without_blocking(
    monkeypatch, tmp_path,
):
    sid = "corrupt-conflict-record"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    intended["resume_queue"][sid] = ["intended"]
    _install_primary_conflict_fixture(server, sid, intended)
    record_path = next(server._pending_primary_conflict_dir().glob("*.json"))
    record = json.loads(record_path.read_text())
    record["intended_json"] = "{}"
    record_path.write_text(json.dumps(record))
    server._pending_primary_conflicts.clear()

    server._load_pending_primary_conflicts()

    assert not server._pending_input_recovery_blocked(sid)
    assert not record_path.exists()
    assert record_path.with_suffix(".invalid").exists()


def test_semantically_invalid_conflict_payload_is_quarantined(
    monkeypatch, tmp_path,
):
    sid = "invalid-conflict-payload"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    server._pending_resume_queue[sid] = ["base"]
    assert server._save_pending_inputs({sid})
    intended = json.loads(pending_file.read_text())
    _install_primary_conflict_fixture(server, sid, intended)
    record_path = next(server._pending_primary_conflict_dir().glob("*.json"))
    record = json.loads(record_path.read_text())
    payload = json.loads(record["intended_json"])
    payload["devin_steers"][sid] = 7
    record["intended_json"] = json.dumps(payload, indent=2, sort_keys=True)
    record["intended_hash"] = hashlib.sha256(
        record["intended_json"].encode()
    ).hexdigest()
    body = {key: value for key, value in record.items() if key != "integrity"}
    record["integrity"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    record_path.write_text(json.dumps(record))
    server._pending_primary_conflicts.clear()

    server._load_pending_primary_conflicts()

    assert not server._pending_input_recovery_blocked(sid)
    assert record_path.with_suffix(".invalid").exists()


def test_writer_gate_uses_active_worker_capability_evidence(monkeypatch, tmp_path):
    import ccc_server.pending_inputs as pending_inputs_module

    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    evidence = {
        "reachable": True,
        "pid": 42,
        "capabilities": ["engine-execution-v1"],
    }
    monkeypatch.setattr(server, "_get_worker_compat_cache", lambda: dict(evidence))
    monkeypatch.setattr(server, "_read_registry_pruned", lambda: [])

    stale = pending_inputs_module._pending_writer_compatibility_status()
    evidence["capabilities"].append("pending-input-cas-v1")
    restarted = pending_inputs_module._pending_writer_compatibility_status()

    assert stale["ok"] is False
    assert stale["restart_required"] is True
    assert stale["incompatible_writers"] == ["worker:42"]
    assert restarted == {
        "ok": True,
        "restart_required": False,
        "incompatible_writers": [],
    }


def test_pending_writer_protocol_is_advertised_by_dashboard_and_worker():
    repo_root = Path(server.__file__).resolve().parent
    worker_source = (repo_root / "ccc_worker.py").read_text()
    registry_source = (repo_root / "ccc_server" / "fleet_jobs.py").read_text()

    assert '"pending-input-cas-v1"' in worker_source
    assert '"pending_input_protocol": 1' in registry_source


def test_codex_pump_persist_failure_prevents_native_delivery(
    monkeypatch, tmp_path,
):
    sid = "pump-claim-persist-failure"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = ["target", "next"]
    assert server._save_pending_inputs({sid})
    monkeypatch.setattr(server, "_persist_pending_inputs_current", lambda *a, **k: False)
    monkeypatch.setattr(server, "_pending_resume_retry_due", lambda sid: True)
    monkeypatch.setattr(server, "_resume_queue_engine_busy", lambda sid: False)
    native = mock.Mock()
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", native)

    result = server._pump_codex_resume_queue(sid)

    assert not result["ok"]
    assert result["code"] == "pending_input_persist_failed"
    native.assert_not_called()
    assert server._pending_resume_queue[sid] == ["target", "next"]


def test_codex_pump_delivery_failure_restores_exact_claim(monkeypatch, tmp_path):
    sid = "pump-delivery-rollback"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = ["target", "target", "next"]
    assert server._save_pending_inputs({sid})
    monkeypatch.setattr(server, "_pending_resume_retry_due", lambda sid: True)
    monkeypatch.setattr(server, "_resume_queue_engine_busy", lambda sid: False)
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(
        server, "_resume_session_codex_native_delivery",
        lambda *a, **k: {"ok": False, "code": "delivery_failed"},
    )

    result = server._pump_codex_resume_queue(sid)

    assert not result["ok"]
    assert server._pending_resume_queue[sid] == ["target", "target", "next"]
    assert json.loads(
        (tmp_path / "pending.json").read_text()
    )["resume_queue"][sid] == ["target", "target", "next"]


def test_devin_clear_if_matching_reports_truthful_boolean(monkeypatch, tmp_path):
    sid = "devin-clear-truth"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_devin_steers[sid] = "newer"
    assert server._save_pending_inputs({sid}, include_devin_steers=True)

    result = server._apply_pending_input_operations(sid, [{
        "field": "devin_steer", "action": "clear_if_matching", "match": "older",
    }])

    assert result["ok"]
    assert result["value"] == [False]
    assert server._pending_devin_steers[sid] == "newer"


def test_real_dashboard_worker_pump_ack_consumes_one_duplicate(
    monkeypatch, tmp_path,
):
    sid = "real-worker-pump-ack"
    monkeypatch.delenv("CCC_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", tmp_path / "pending.json")
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    assert server._save_pending_inputs({sid})
    monkeypatch.setattr(server, "_pending_resume_retry_due", lambda sid: True)
    monkeypatch.setattr(server, "_resume_queue_engine_busy", lambda sid: False)
    monkeypatch.setattr(server, "_schedule_codex_queue_pump", lambda sid: None)

    def native(session_id, text, **kwargs):
        server._CODEX_APP_SERVER_THREAD_STATE.setdefault(session_id, {})[
            "ccc_turn_start_pending"
        ] = True
        server._codex_app_server_handle_message({
            "jsonrpc": "2.0", "method": "turn/started",
            "params": {"threadId": session_id, "turnId": "pump-turn"},
        })
        _notify_user_message(session_id, text, turn_id="pump-turn")
        return {"ok": True, "via": "codex-app-server", "accepted": True}

    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", native)

    def route(engine, operation, args, **kwargs):
        if os.environ.get("CCC_WORKER_PROCESS") == "1":
            return None
        assert args["queued_delivery_transaction_protocol"] == 1
        with mock.patch.dict(os.environ, {"CCC_WORKER_PROCESS": "1"}):
            return server.resume_session_codex(
                args["session_id"], args["text"],
                _from_queue=args["from_queue"],
                queued_delivery_transaction_protocol=args[
                    "queued_delivery_transaction_protocol"
                ],
            )

    monkeypatch.setattr(server, "_control_plane_engine_call", route)

    result = server._pump_codex_resume_queue(sid)

    assert result["ok"] and result["delivered"]
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_claim_journal_recovers_duplicate_once_after_restart(monkeypatch, tmp_path):
    sid = "journal-duplicate-recovery"
    pending_file = tmp_path / "pending.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", tmp_path / "handoffs")
    server._pending_resume_queue[sid] = ["target", "target", "last"]
    assert server._save_pending_inputs({sid})
    claim_tx = server._apply_pending_input_operations(sid, [{
        "field": "resume", "action": "pop_head",
    }])
    claim = {
        "session_id": sid, "queue_name": "resume", "index": 0,
        "item": claim_tx["value"][0],
    }
    real_persist = server._persist_pending_inputs_current
    monkeypatch.setattr(server, "_persist_pending_inputs_current", lambda *a, **k: False)
    assert server._codex_restore_or_journal_claim(claim) == "journaled"
    monkeypatch.setattr(server, "_persist_pending_inputs_current", real_persist)

    server._pending_resume_queue.clear()
    server._load_pending_inputs()
    assert server._pending_resume_queue[sid] == ["target", "target", "last"]
    server._load_pending_inputs()
    assert server._pending_resume_queue[sid] == ["target", "target", "last"]
    assert not list((tmp_path / "handoffs" / "claim-recovery").glob("*.json"))


def test_finalizer_does_not_consume_a_second_copy_after_router_commit():
    sid = "already-consumed"
    server._pending_resume_queue[sid] = ["target", "last"]

    result = server._finalize_queued_steer_result(
        sid,
        "target",
        {"ok": True, "via": "codex-steer", "queued_consumed": 1},
    )

    assert result["queued_consumed"] == 1
    assert server._pending_resume_queue[sid] == ["target", "last"]


def test_finalizer_preserves_missing_replacement_conflict():
    result = server._finalize_queued_steer_result(
        "missing",
        "target",
        {
            "ok": False,
            "via": "codex-steer",
            "code": "queued_message_missing",
            "queued_consumed": 0,
            "error": "queued message no longer exists",
        },
    )

    assert result == {
        "ok": False,
        "via": "codex-steer",
        "code": "queued_message_missing",
        "queued_consumed": 0,
        "error": "queued message no longer exists",
    }


def test_inject_input_returns_http_409_for_missing_queued_replacement():
    sid = "http-missing-queued-replacement"
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    request = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_address[1]}/api/inject-input",
        data=json.dumps({
            "session_id": sid,
            "text": "target",
            "mode": "steer",
            "replace_queued": True,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    missing = {
        "ok": False,
        "via": "codex-steer",
        "code": "queued_message_missing",
        "queued_consumed": 0,
        "error": "queued message no longer exists",
    }
    try:
        with mock.patch.object(server, "_resolve_bridge_session_alias", return_value=sid), \
             mock.patch.object(server, "_handoff_lease_guard", return_value=None), \
             mock.patch.object(server, "_record_interaction"), \
             mock.patch.object(server, "_inject_text_into_session", return_value=missing):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            body = json.loads(error.value.read().decode("utf-8"))
            error.value.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert error.value.code == 409
    assert body == missing


def test_inject_input_preserves_transaction_terminal_error_body():
    sid = "http-terminal-transaction-error"
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    request = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_address[1]}/api/inject-input",
        data=json.dumps({
            "session_id": sid,
            "text": "target",
            "mode": "steer",
            "replace_queued": True,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    terminal = {
        "ok": False,
        "via": "codex-steer",
        "code": "queued_claim_persistence_failed",
        "queued_consumed": 0,
        "error": "could not persist queued message claim",
    }
    try:
        with mock.patch.object(server, "_resolve_bridge_session_alias", return_value=sid), \
             mock.patch.object(server, "_handoff_lease_guard", return_value=None), \
             mock.patch.object(server, "_record_interaction"), \
             mock.patch.object(server, "_inject_text_into_session", return_value=terminal):
            with urllib.request.urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert body == terminal
    assert "queued_preserved" not in body
