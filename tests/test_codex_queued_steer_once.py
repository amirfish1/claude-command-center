from types import SimpleNamespace
from unittest import mock
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
    pending_file, session_id, stale_loaded, claimed, release, results,
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
    except BaseException as exc:
        results.put(("error", os.getpid(), repr(exc)))


def _cross_process_pump_worker(
    pending_file, session_id, stale_loaded, claimed, release, results,
):
    import server as process_server

    try:
        process_server.PENDING_INPUTS_FILE = Path(pending_file)
        process_server._pending_resume_queue.clear()
        process_server._pending_terminal_input_queue.clear()
        process_server._load_pending_inputs()
        stale_loaded.set()
        if not claimed.wait(5):
            raise AssertionError("claim process did not claim the row")
        process_server._pending_resume_retry_after.clear()
        process_server._resume_queue_engine_busy = lambda sid: False
        deliveries = []

        def deliver(sid, text, _from_queue=False):
            deliveries.append(text)
            return {"ok": True, "accepted": True, "confirmed": True}

        process_server.resume_session_codex = deliver
        first = process_server._pump_codex_resume_queue(session_id)
        results.put(("first-pump", os.getpid(), first, list(deliveries)))
        if not release.wait(5):
            raise AssertionError("pump process was not released")
        second = process_server._pump_codex_resume_queue(session_id)
        results.put(("second-pump", os.getpid(), second, list(deliveries)))
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
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.clear()
        server._CODEX_APP_SERVER_TURN_THREAD.clear()
    if suppressions is not None:
        suppressions.clear()


@pytest.fixture
def router_env(monkeypatch):
    resume = mock.Mock()
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))
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
    monkeypatch.setattr(server, "resume_session_codex", resume)
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
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim == {
        "session_id": sid,
        "queue_name": "resume",
        "index": 1,
        "item": "target",
    }
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]
    assert server._restore_pending_input_claim(claim)
    assert server._pending_resume_queue[sid] == ["first", "target", "target", "last"]


def test_claim_prefers_resume_queue_and_claims_at_most_one(monkeypatch):
    sid = "claim-precedence"
    server._pending_resume_queue[sid] = ["target", "keep"]
    server._pending_terminal_input_queue[sid] = ["target"]
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=True))

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim["queue_name"] == "resume"
    assert server._pending_resume_queue[sid] == ["keep"]
    assert server._pending_terminal_input_queue[sid] == ["target"]


def test_claim_persistence_failure_restores_memory_and_fails_closed(
    monkeypatch,
):
    sid = "claim-save-failure"
    server._pending_resume_queue[sid] = ["first", "target", "last"]
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=False))

    claim = server._claim_matching_pending_input(sid, "target")

    assert claim["code"] == "queued_claim_persistence_failed"
    assert server._pending_resume_queue[sid] == ["first", "target", "last"]


def test_claim_persistence_failure_never_calls_codex(router_env, monkeypatch):
    sid = "steer-claim-save-failure"
    server._pending_resume_queue[sid] = ["target"]
    monkeypatch.setattr(server, "_save_pending_inputs", mock.Mock(return_value=False))

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
        server, "_save_pending_inputs", mock.Mock(side_effect=[True, False]),
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
    assert server._pending_resume_queue[sid] == ["target"]


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
    assert server._pending_resume_queue[sid] == ["last"]
    _notify_user_message(sid, "target", turn_id="claimed-turn")
    assert server._pending_resume_queue[sid] == ["last"]


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
    assert server._pending_resume_queue[sid] == ["target", "last"]


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
    states = ["unbound", "pending", "committed"] * 86
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
    assert server._pending_resume_queue[sid] == ["first", "last"]


def test_legacy_steer_claims_matching_queue_copy(router_env):
    sid = "stale-client"
    server._pending_resume_queue[sid] = ["target"]
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}

    result = server._inject_text_into_session_router(sid, "target", mode="steer")

    assert result["queued_consumed"] == 1
    assert sid not in server._pending_resume_queue


def test_codex_steer_never_crosses_control_plane_worker_boundary(
    router_env, monkeypatch,
):
    router_env.resume.return_value = {"ok": True, "via": "codex-steer"}
    route = mock.Mock(side_effect=AssertionError(
        "Codex steer crossed the dashboard/worker boundary"
    ))
    monkeypatch.setattr(server, "_control_plane_engine_call", route)

    result = server._inject_text_into_session_router(
        "codex-local-route", "historical correction", mode="steer",
    )

    assert result["ok"]
    route.assert_not_called()
    router_env.resume.assert_called_once_with(
        "codex-local-route", "historical correction", steer=True,
    )


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

    pump_result = server._pump_codex_resume_queue(sid)

    assert pump_result == {"ok": True, "waiting": "already-pumping"}
    assert router_env.resume.call_count == 1
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert thread_errors == []
    assert steer_result["ok"]
    assert steer_result["queued_consumed"] == 1


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
    results = context.Queue()
    claimant = context.Process(
        target=_cross_process_claim_worker,
        args=(pending_file, sid, stale_loaded, claimed, release, results),
    )
    pump = context.Process(
        target=_cross_process_pump_worker,
        args=(pending_file, sid, stale_loaded, claimed, release, results),
    )
    claimant.start()
    pump.start()
    try:
        claim_result = results.get(timeout=10)
        first_pump = results.get(timeout=10)
        by_label = {claim_result[0]: claim_result, first_pump[0]: first_pump}
        assert "error" not in by_label, by_label.get("error")
        assert by_label["claim"][2]["item"] == "target"
        assert by_label["first-pump"][2] == {
            "ok": True,
            "waiting": "already-pumping",
        }
        assert by_label["first-pump"][3] == []
        assert by_label["claim"][1] != by_label["first-pump"][1]
        release.set()
        second_pump = results.get(timeout=10)
        assert second_pump[0] == "second-pump"
        assert second_pump[2] == {"ok": True, "empty": True}
        assert second_pump[3] == []
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
