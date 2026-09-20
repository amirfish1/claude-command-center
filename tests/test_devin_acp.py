"""Devin ACP harness registration and live-send/steer wiring.

``_devin_acp_try_steer`` (``ccc_server/acp.py``) is the normal delivery path
for devincli- sessions: sends and steers both go over ``devin acp`` -- the
same transport Devin Desktop / Devin - Next use -- and only an inconclusive
result falls back to the durable one-shot queue. The wire side has been
verified against a real ``devin acp`` process (initialize -> authenticate
-> session/list -> session/load -> session/prompt); these tests mock every
ACP primitive and verify the Python *logic*: capability gating, fail-closed
behavior, busy/cancel/retry for steer, and ownership checks that keep CCC
from spawning an ACP connection it cannot use.
"""
import importlib
from unittest import mock

import pytest


def _server():
    return importlib.import_module("server")


# ---------------------------------------------------------------------------
# Harness registration
# ---------------------------------------------------------------------------

def test_devin_harness_is_registered():
    server = _server()
    cfg = server._ACP_HARNESSES["devin"]
    assert cfg["bin_names"] == ("devin",)
    assert cfg["acp_args"] == ("acp",)
    assert cfg["kill_env"] == "CCC_DEVIN_ACP"
    # Devin's ACP server uses the stored CLI login on its own; the browser
    # auth method stays advertised but lazy, so _acp_ensure only runs the
    # authenticate handshake once a request comes back auth-required.
    assert cfg["auth_method"] == "devin-browser"
    assert cfg["auth_lazy"] is True
    # Devin is deliberately NOT worker-routed (see the comment above
    # _ACP_WORKER_HARNESSES in acp.py): its ACP connection is
    # attach-on-demand, owned by whichever process first steers/loads --
    # the same posture as "glm".
    assert "devin" not in server._ACP_WORKER_HARNESSES
    assert server._acp_harness_enabled("devin") is True


# ---------------------------------------------------------------------------
# _devin_acp_steer_capable -- the "could a delivery be attempted" UI signal
# ---------------------------------------------------------------------------

def test_devin_acp_steer_capable_true_when_bin_resolves():
    """No flag, no existing connection required — the first prompt attaches
    the connection lazily, so capability must depend only on the harness
    being enabled and the devin binary resolving."""
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True, "bin": "/usr/bin/devin"}), \
         mock.patch.object(server, "_devin_acp_session_loaded", return_value=False):
        assert server._devin_acp_steer_capable() is True


def test_devin_acp_steer_capable_false_when_harness_disabled():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=False), \
         mock.patch.object(server, "_acp_resolve_bin") as resolve_bin:
        assert server._devin_acp_steer_capable() is False
    resolve_bin.assert_not_called()


def test_devin_acp_steer_capable_false_when_bin_unavailable():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": False}):
        assert server._devin_acp_steer_capable() is False


# ---------------------------------------------------------------------------
# _devin_acp_try_steer -- fail-closed behavior
# ---------------------------------------------------------------------------

def test_devin_acp_try_steer_requires_raw_id_and_text():
    server = _server()
    assert server._devin_acp_try_steer("sid", "", None, "text") is None
    assert server._devin_acp_try_steer("sid", "raw", None, "") is None


def test_devin_acp_try_steer_noop_when_harness_disabled():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=False), \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_not_called()


def test_devin_acp_try_steer_noop_when_bin_unavailable():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": False}), \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_not_called()


def test_devin_acp_try_steer_swallows_exceptions():
    """Any surprise (unexpected wire shape, hang that raises) degrades to
    None (fall back to the queue) instead of propagating out of the
    dispatcher and surfacing a new failure mode."""
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", side_effect=RuntimeError("boom")), \
         mock.patch.object(server, "_log_activity") as log_activity:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    assert log_activity.call_args[0][0] == "inject"
    assert log_activity.call_args[0][1] == "DEVIN_ACP_ERROR"


# ---------------------------------------------------------------------------
# _devin_acp_try_steer -- happy path and busy/cancel/retry, all mocked
# ---------------------------------------------------------------------------

def test_devin_acp_try_steer_happy_path():
    server = _server()
    ok_result = {"ok": True, "via": "acp-prompt", "harness": "devin", "session_id": "raw-1"}
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True, "bin": "/usr/bin/devin"}), \
         mock.patch.object(server, "_acp_prompt", return_value=ok_result) as prompt:
        result = server._devin_acp_try_steer(
            "devincli-x", "raw-1", None, "steer this", idempotency_key="inject:1",
        )
    assert result == ok_result
    prompt.assert_called_once_with(
        "devin", "raw-1", "steer this", mode="steer", idempotency_key="inject:1",
    )


def test_devin_acp_try_steer_send_mode_passes_send_through():
    """mode="send" forwards to _acp_prompt unchanged -- a plain follow-up
    rides the same live pipe a steer would."""
    server = _server()
    ok_result = {"ok": True, "via": "acp-prompt", "harness": "devin"}
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value=ok_result) as prompt:
        result = server._devin_acp_try_steer(
            "devincli-x", "raw-1", None, "follow up", mode="send",
        )
    assert result == ok_result
    prompt.assert_called_once_with(
        "devin", "raw-1", "follow up", mode="send", idempotency_key=None,
    )


def test_devin_acp_try_steer_only_a_conclusive_ok_is_returned():
    """A non-busy failure (not just busy) must also fall back to the queue,
    not surface as a new error to the caller."""
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value={"ok": False, "error": "empty prompt"}):
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None


def test_devin_acp_try_steer_send_busy_returns_none_without_cancelling():
    """mode="send" on a busy session must NOT interrupt the running turn --
    the durable queue delivers the text after the turn ends."""
    server = _server()
    busy = {"ok": False, "code": "busy", "error": "turn already in progress"}
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value=busy) as prompt, \
         mock.patch.object(server, "_acp_cancel") as cancel:
        result = server._devin_acp_try_steer(
            "devincli-x", "raw-1", None, "follow up", mode="send",
        )
    assert result is None
    prompt.assert_called_once()
    cancel.assert_not_called()


def test_devin_acp_try_steer_cancels_and_retries_on_busy():
    server = _server()
    busy = {"ok": False, "code": "busy", "error": "turn already in progress"}
    retried_ok = {"ok": True, "via": "acp-prompt", "harness": "devin"}
    prompt_calls = []

    def fake_prompt(harness, sid, text, mode=None, idempotency_key=None):
        prompt_calls.append((harness, sid, text, mode, idempotency_key))
        return busy if len(prompt_calls) == 1 else retried_ok

    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", side_effect=fake_prompt), \
         mock.patch.object(server, "_acp_cancel", return_value={"ok": True}) as cancel, \
         mock.patch.object(server, "_acp_session_snapshot", return_value={"status": "idle"}):
        result = server._devin_acp_try_steer(
            "devincli-x", "raw-1", None, "steer this", idempotency_key="inject:1",
        )
    assert result == retried_ok
    cancel.assert_called_once_with("devin", "raw-1")
    assert len(prompt_calls) == 2
    assert prompt_calls[1][4] == "inject:1:steer-retry"


def test_devin_acp_try_steer_gives_up_when_cancel_fails():
    server = _server()
    busy = {"ok": False, "code": "busy"}
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value=busy) as prompt, \
         mock.patch.object(server, "_acp_cancel", return_value={"ok": False}):
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_called_once()


# ---------------------------------------------------------------------------
# _devin_acp_session_loaded -- per-session UI signal + ownership check
# ---------------------------------------------------------------------------

def test_devin_acp_session_loaded_false_when_no_connection(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_ACP_CONNS", {}, raising=False)
    assert server._devin_acp_session_loaded("any-sid") is False


def test_devin_acp_session_loaded_false_when_transport_dead(monkeypatch):
    server = _server()
    transport = mock.Mock()
    transport.alive.return_value = False
    monkeypatch.setattr(server, "_ACP_CONNS", {"devin": {"transport": transport}}, raising=False)
    assert server._devin_acp_session_loaded("any-sid") is False


def test_devin_acp_session_loaded_true_when_attached(monkeypatch):
    server = _server()
    transport = mock.Mock()
    transport.alive.return_value = True
    conn = {"transport": transport}
    monkeypatch.setattr(server, "_ACP_CONNS", {"devin": conn}, raising=False)
    with mock.patch.object(server, "_acp_session", return_value={"loaded_conn": id(conn)}):
        assert server._devin_acp_session_loaded("any-sid") is True


# ---------------------------------------------------------------------------
# Dispatcher wiring (_inject_text_into_session_router) -- no-regression +
# success-path tests, following tests/test_devin_queue.py's mocking style.
# ---------------------------------------------------------------------------

def _devin_dispatch_common_mocks(server, sid):
    """Context managers shared by every dispatcher-level test below, mirroring
    tests/test_devin_queue.py's engine-detection mock list."""
    return [
        mock.patch.object(server, "_is_codex_session", return_value=False),
        mock.patch.object(server, "_is_kimi_session", return_value=False),
        mock.patch.object(server, "_is_gemini_session", return_value=False),
        mock.patch.object(server, "_is_cursor_session", return_value=False),
        mock.patch.object(server, "_is_antigravity_session", return_value=False),
        mock.patch.object(server, "_is_hermes_session", return_value=False),
        mock.patch.object(server, "_is_opencode_session", return_value=False),
        mock.patch.object(server, "_is_aider_session", return_value=False),
        mock.patch.object(server, "_is_devin_cli_session", return_value=True),
        mock.patch.object(server, "find_session_cwd", return_value="/tmp"),
        mock.patch.object(server, "session_live_status", return_value={
            "live": False, "status": None, "kind": None,
            "tty": None, "terminal_app": None,
        }),
        mock.patch.object(server, "_find_live_spawn_entry_for_session", return_value=None),
        mock.patch.object(server, "_devin_cli_session_live", return_value=False),
        mock.patch.object(server, "_devin_acp_session_loaded", return_value=False),
    ]


def _enter_stack(stack):
    for cm in stack:
        cm.__enter__()


def _exit_stack(stack):
    for cm in reversed(stack):
        cm.__exit__(None, None, None)


def test_devin_steer_falls_back_to_queue_when_acp_declines():
    """No-regression guarantee: when the ACP path returns None (binary
    missing, auth pending, transport failure), mode="steer" on a devincli-
    session must still land in exactly the same durable queue as before
    this transport existed."""
    server = _server()
    sid = "devincli-steer-fallback-test"
    stack = _devin_dispatch_common_mocks(server, sid) + [
        mock.patch.object(server, "_devin_acp_try_steer", return_value=None),
    ]
    with mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_steer", return_value=True) as queue_steer, \
         mock.patch.object(server, "_note_pending_queued") as note_queued, \
         mock.patch.object(server, "resume_session_devin") as resume, \
         mock.patch.object(server, "_control_plane_engine_call") as cp:
        _enter_stack(stack)
        try:
            result = server._inject_text_into_session(sid, "steer text", mode="steer")
        finally:
            _exit_stack(stack)

    assert result["ok"] is True
    assert result["via"] == "devin-resume-queued"
    resume.assert_not_called()
    cp.assert_not_called()
    pump.assert_called_once_with(sid)
    queue_steer.assert_called_once_with(sid, "steer text")
    note_queued.assert_called_once()


def test_devin_steer_returns_acp_result_when_available():
    """When _devin_acp_try_steer reaches a conclusive success, the dispatcher
    must return it directly and must NOT also enqueue the one-shot fallback
    (that would double-send)."""
    server = _server()
    sid = "devincli-steer-acp-test"
    acp_ok = {"ok": True, "via": "acp-prompt", "harness": "devin", "session_id": "steer-acp-test"}
    stack = _devin_dispatch_common_mocks(server, sid)
    with mock.patch.object(server, "_devin_acp_try_steer", return_value=acp_ok) as acp_try, \
         mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_steer") as queue_steer, \
         mock.patch.object(server, "_save_pending_inputs") as save:
        _enter_stack(stack)
        try:
            result = server._inject_text_into_session(sid, "steer text", mode="steer")
        finally:
            _exit_stack(stack)

    assert result["ok"] is True
    assert result["via"] == "acp-prompt"
    acp_try.assert_called_once()
    assert acp_try.call_args.kwargs["mode"] == "steer"
    queue_steer.assert_not_called()
    pump.assert_not_called()


def test_devin_send_attempts_acp_with_send_mode_then_falls_back():
    """A plain follow-up (mode="send", the default) goes over ACP too --
    same live pipe a steer would use -- and only an inconclusive attempt
    falls back to the durable resume queue."""
    server = _server()
    sid = "devincli-plain-send-test"
    stack = _devin_dispatch_common_mocks(server, sid)
    with mock.patch.object(server, "_devin_acp_try_steer", return_value=None) as acp_try, \
         mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_resume_input") as queue_resume, \
         mock.patch.object(server, "_save_pending_inputs"):
        _enter_stack(stack)
        try:
            result = server._inject_text_into_session(sid, "follow up")
        finally:
            _exit_stack(stack)

    acp_try.assert_called_once()
    assert acp_try.call_args.kwargs["mode"] == "send"
    assert result["ok"] is True
    assert result["via"] == "devin-resume-queued"
    queue_resume.assert_called_once_with(sid, "follow up")
    pump.assert_called_once_with(sid)


def test_devin_send_returns_acp_result_when_available():
    server = _server()
    sid = "devincli-send-acp-test"
    acp_ok = {"ok": True, "via": "acp-prompt", "harness": "devin"}
    stack = _devin_dispatch_common_mocks(server, sid)
    with mock.patch.object(server, "_devin_acp_try_steer", return_value=acp_ok) as acp_try, \
         mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_resume_input") as queue_resume:
        _enter_stack(stack)
        try:
            result = server._inject_text_into_session(sid, "follow up")
        finally:
            _exit_stack(stack)

    assert result["ok"] is True
    assert result["via"] == "acp-prompt"
    acp_try.assert_called_once()
    assert acp_try.call_args.kwargs["mode"] == "send"
    queue_resume.assert_not_called()
    pump.assert_not_called()


def test_devin_send_skips_acp_when_external_owner_holds_lock():
    """A session lock held by a live foreign writer (Devin Desktop / Next,
    a sibling CCC's ACP conn) means session/load would fail -32015 -- the
    dispatcher must go straight to the durable queue without spawning."""
    server = _server()
    sid = "devincli-send-external-owner-test"
    stack = _devin_dispatch_common_mocks(server, sid)
    _enter_stack(stack)
    try:
        with mock.patch.object(server, "_devin_acp_try_steer") as acp_try, \
             mock.patch.object(server, "_devin_cli_session_live", return_value=True), \
             mock.patch.object(server, "_pump_devin_resume_queue"), \
             mock.patch.object(server, "_queue_devin_resume_input"), \
             mock.patch.object(server, "_note_pending_queued"):
            result = server._inject_text_into_session(sid, "follow up")
    finally:
        _exit_stack(stack)

    acp_try.assert_not_called()
    assert result["ok"] is True
    assert result["external_devin_owner"] is True


def test_devin_send_skips_acp_when_ccc_live_spawn_holds_session():
    """A live CCC `devin -p` spawn owns the session lock until it exits --
    same skip-the-ACP-spawn logic as the external-owner case."""
    server = _server()
    sid = "devincli-send-live-spawn-test"
    stack = _devin_dispatch_common_mocks(server, sid)
    _enter_stack(stack)
    try:
        with mock.patch.object(server, "_devin_acp_try_steer") as acp_try, \
             mock.patch.object(
                 server, "_find_live_spawn_entry_for_session", return_value={"pid": 1234},
             ), \
             mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
             mock.patch.object(server, "_queue_devin_resume_input"), \
             mock.patch.object(server, "_note_pending_queued"):
            result = server._inject_text_into_session(sid, "follow up")
    finally:
        _exit_stack(stack)

    acp_try.assert_not_called()
    assert result["ok"] is True
    assert result["via"] == "devin-resume-queued"
    pump.assert_called_once_with(sid)


def test_devin_send_uses_acp_when_our_own_conn_holds_the_lock():
    """Our own ACP connection holding the session lock is NOT an external
    owner -- the send must still be attempted over ACP."""
    server = _server()
    sid = "devincli-send-ours-loaded-test"
    acp_ok = {"ok": True, "via": "acp-prompt", "harness": "devin"}
    stack = _devin_dispatch_common_mocks(server, sid)
    _enter_stack(stack)
    try:
        with mock.patch.object(server, "_devin_acp_try_steer", return_value=acp_ok) as acp_try, \
             mock.patch.object(server, "_devin_cli_session_live", return_value=True), \
             mock.patch.object(server, "_devin_acp_session_loaded", return_value=True), \
             mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
             mock.patch.object(server, "_queue_devin_resume_input"):
            result = server._inject_text_into_session(sid, "follow up")
    finally:
        _exit_stack(stack)

    assert result["ok"] is True
    assert result["via"] == "acp-prompt"
    acp_try.assert_called_once()
    pump.assert_not_called()


# ---------------------------------------------------------------------------
# _devin_acp_spawn_new_session -- ACP-first spawn (the Devin Desktop path)
# ---------------------------------------------------------------------------

def test_devin_acp_spawn_new_session_happy_path():
    """session/new -> mode+model config -> initial session/prompt, all over
    the shared devin acp conn (the same calls Devin Desktop makes)."""
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(
             server, "_acp_new_session",
             return_value={"ok": True, "session_id": "raw-new-session"},
         ) as new_session, \
         mock.patch.object(server, "_acp_set_config") as set_config, \
         mock.patch.object(
             server, "_acp_prompt",
             return_value={"ok": True, "via": "acp-prompt", "req_id": 9},
         ) as prompt:
        result = server._devin_acp_spawn_new_session(
            "do work", "/tmp/work", model="swe-2-high",
            permission_mode="dangerous",
        )

    assert result["ok"] is True
    assert result["session_id"] == "raw-new-session"
    new_session.assert_called_once_with("devin", "/tmp/work")
    config_calls = {c.args[2]: c.args[3] for c in set_config.call_args_list}
    # The one-shot CLI's --permission-mode dangerous maps onto Bypass
    # Permissions so spawned sessions behave like the old devin -p path.
    assert config_calls == {"mode": "bypass", "model": "swe-2-high"}
    prompt.assert_called_once_with(
        "devin", "raw-new-session", "do work", mode="send",
    )


def test_devin_acp_spawn_new_session_declines_when_uncapable():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=False), \
         mock.patch.object(server, "_acp_new_session") as new_session:
        result = server._devin_acp_spawn_new_session("do work", "/tmp/work")

    assert result["ok"] is False
    new_session.assert_not_called()


def test_devin_acp_spawn_new_session_propagates_new_failure():
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(
             server, "_acp_new_session",
             return_value={"ok": False, "error": "conn unavailable"},
         ), \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_spawn_new_session("do work", "/tmp/work")

    assert result["ok"] is False
    assert result["error"] == "conn unavailable"
    prompt.assert_not_called()


def test_devin_acp_spawn_new_session_prompt_failure_keeps_session():
    """A session that was created but whose first prompt failed is still a
    real session -- report session_created so the caller can surface it."""
    server = _server()
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(
             server, "_acp_new_session",
             return_value={"ok": True, "session_id": "raw-orphan"},
         ), \
         mock.patch.object(server, "_acp_set_config"), \
         mock.patch.object(
             server, "_acp_prompt",
             return_value={"ok": False, "error": "send failed", "code": "busy"},
         ):
        result = server._devin_acp_spawn_new_session("do work", "/tmp/work")

    assert result["ok"] is False
    assert result["session_created"] is True
    assert result["session_id"] == "raw-orphan"


# ---------------------------------------------------------------------------
# spawn_session_devin -- ACP-first with devin -p fallback
# ---------------------------------------------------------------------------

def _spawn_common_mocks(server):
    """Mocks every spawn_session_devin dependency that is NOT under test."""
    return [
        mock.patch.object(
            server, "_resolve_devin_bin",
            return_value={"available": True, "bin": "/usr/bin/devin-test"},
        ),
        mock.patch.object(
            server, "_spawn_repo_context",
            return_value={"cwd": "/tmp/work", "repo_path": "/tmp/work"},
        ),
        mock.patch.object(server, "_devin_resolve_model", return_value="swe-2-high"),
        mock.patch.object(server, "_set_session_model"),
        mock.patch.object(server, "_record_spawn_to_registry"),
    ]


def _enter_stack(stack):
    for ctx in stack:
        ctx.__enter__()


def _exit_stack(stack):
    for ctx in reversed(stack):
        ctx.__exit__(None, None, None)


def test_spawn_session_devin_uses_acp_when_available(tmp_path):
    """ACP spawn returns the devincli- id in-band -- no pid, no DB poll."""
    server = _server()
    stack = _spawn_common_mocks(server)
    _enter_stack(stack)
    try:
        with mock.patch.object(
            server, "_devin_acp_spawn_new_session",
            return_value={"ok": True, "session_id": "raw-acp-spawn"},
        ) as acp_spawn, \
             mock.patch.object(server.subprocess, "Popen") as popen:
            result = server.spawn_session_devin(
                "do work", name="acp spawn", repo_path=str(tmp_path),
            )
    finally:
        _exit_stack(stack)

    acp_spawn.assert_called_once()
    popen.assert_not_called()
    assert result["ok"] is True
    assert result["via"] == "devin-acp"
    assert result["session_id"] == "devincli-raw-acp-spawn"
    assert result["session_id_pending"] is False


def test_spawn_session_devin_falls_back_to_cli_when_acp_fails(tmp_path):
    """When ACP can't produce a session the one-shot devin -p path runs,
    exactly as before."""
    server = _server()
    proc = mock.Mock(pid=7777)
    proc.poll.return_value = None
    stack = _spawn_common_mocks(server)
    _enter_stack(stack)
    original_spawns = list(server._spawned_sessions)
    server._spawned_sessions.clear()
    try:
        with mock.patch.object(
            server, "_devin_acp_spawn_new_session",
            return_value={"ok": False, "error": "acp down"},
        ), \
             mock.patch.object(
                 server, "_devin_cli_session_id_for_spawn_entry",
                 return_value=None,
             ), \
             mock.patch.object(
                 server.subprocess, "Popen", return_value=proc,
             ) as popen:
            result = server.spawn_session_devin(
                "do work", name="cli spawn", repo_path=str(tmp_path),
            )
    finally:
        for entry in server._spawned_sessions:
            fh = entry.get("log_fh")
            if fh:
                fh.close()
        server._spawned_sessions.clear()
        server._spawned_sessions.extend(original_spawns)
        _exit_stack(stack)

    popen.assert_called_once()
    cmd = popen.call_args.args[0]
    assert cmd[0] == "/usr/bin/devin-test"
    assert "-p" in cmd
    assert result["ok"] is True
    assert result["engine"] == "devin"


def test_spawn_session_devin_acp_prompt_failure_still_returns_session(tmp_path):
    """session_created-but-prompt-failed surfaces the session card with a
    warning instead of falling back to a second, duplicate spawn."""
    server = _server()
    stack = _spawn_common_mocks(server)
    _enter_stack(stack)
    try:
        with mock.patch.object(
            server, "_devin_acp_spawn_new_session",
            return_value={
                "ok": False, "session_id": "raw-orphan",
                "session_created": True, "error": "send failed",
            },
        ), \
             mock.patch.object(server.subprocess, "Popen") as popen:
            result = server.spawn_session_devin(
                "do work", name="orphan", repo_path=str(tmp_path),
            )
    finally:
        _exit_stack(stack)

    popen.assert_not_called()
    assert result["ok"] is True
    assert result["session_id"] == "devincli-raw-orphan"
    assert result["prompt_pending"] is True


# ---------------------------------------------------------------------------
# _acp_authenticate -- single-flight the browser sign-in
# ---------------------------------------------------------------------------

def test_devin_authenticate_single_flights_concurrent_callers():
    """devin-browser authenticate parks for human-scale time. A concurrent
    caller must wait on the in-flight attempt -- firing authenticate again
    pops another browser tab per retry (observed live: three tabs from
    three racing ensures)."""
    import threading
    import time

    import ccc_server.acp as acp_mod

    conn = {"authenticated": False, "auth_demanded": True,
            "auth_methods": [{"id": "devin-browser"}]}
    calls = []

    def slow_auth(harness, method, params=None, timeout=None, sid=None):
        calls.append(method)
        time.sleep(0.3)
        return {"ok": True, "result": {}}

    results = []
    with mock.patch.object(acp_mod, "_acp_request", side_effect=slow_auth):
        t = threading.Thread(
            target=lambda: results.append(
                acp_mod._acp_authenticate("devin", conn)
            )
        )
        t.start()
        time.sleep(0.05)  # let the owner claim auth_inflight
        results.append(acp_mod._acp_authenticate("devin", conn))
        t.join()

    assert sorted(results) == [True, True]
    assert calls == ["authenticate"]


def test_devin_authenticate_waiter_reports_failure_and_retries_later():
    """When the in-flight authenticate fails, waiters get False (queue the
    send) and the 30s retry gate leaves room for a fresh attempt."""
    import threading
    import time

    import ccc_server.acp as acp_mod

    conn = {"authenticated": False, "auth_demanded": True,
            "auth_methods": [{"id": "devin-browser"}]}

    def failing_auth(harness, method, params=None, timeout=None, sid=None):
        time.sleep(0.2)
        return {"ok": False, "error": "login cancelled"}

    results = []
    with mock.patch.object(acp_mod, "_acp_request", side_effect=failing_auth):
        t = threading.Thread(
            target=lambda: results.append(
                acp_mod._acp_authenticate("devin", conn)
            )
        )
        t.start()
        time.sleep(0.05)
        results.append(acp_mod._acp_authenticate("devin", conn))
        t.join()

    assert results == [False, False]
    assert conn["authenticated"] is False
    assert "devin" in acp_mod._core._ACP_ENSURE_ERROR
    acp_mod._core._ACP_ENSURE_ERROR.pop("devin", None)


def test_devin_authenticate_is_lazy_until_agent_demands_it():
    """`devin acp` uses the stored CLI login on its own, and devin-browser
    always opens a browser tab -- so a fresh connection must NOT call
    authenticate (observed live: a login tab on every message)."""
    import ccc_server.acp as acp_mod

    conn = {"auth_methods": [{"id": "devin-browser"}]}
    with mock.patch.object(acp_mod, "_acp_request") as req:
        assert acp_mod._acp_authenticate("devin", conn) is True
    req.assert_not_called()


def test_devin_generic_server_error_is_not_an_auth_demand():
    """-32000 is the generic JSON-RPC server error; only a sign-in shaped
    message may arm the browser handshake for an auth_lazy harness."""
    import ccc_server.acp as acp_mod

    conn = {"initialized": True}
    core = acp_mod._core

    def run(message):
        with mock.patch.dict(core._ACP_CONNS, {"devin": conn}), \
                mock.patch.object(core, "_acp_request_async", return_value=7), \
                mock.patch.object(acp_mod, "_acp_wait_response", return_value={
                    "error": {"code": -32000, "message": message}}):
            return acp_mod._acp_request("devin", "session/load", {})

    out = run("internal error: session store busy")
    assert not out.get("auth_required")
    assert not conn.get("auth_demanded")

    out = run("Authentication required")
    assert out.get("auth_required") is True
    assert conn.get("auth_demanded") is True
