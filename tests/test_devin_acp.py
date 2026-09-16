"""Devin ACP harness registration and defensive live-steer wiring.

Devin's ACP path (``ccc_server/acp.py``'s ``_devin_acp_try_steer``) has never
been exercised against a live ``devin acp`` process -- the binary is not
installed anywhere this integration was developed. Every ACP primitive is
mocked here; these tests verify the Python *logic* (opt-in gating, fail-closed
behavior on any missing/erroring dependency, and wiring into the message
dispatcher) -- they do not and cannot verify the actual wire protocol.
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
    # Devin is deliberately NOT worker-routed (see the comment above
    # _ACP_WORKER_HARNESSES in acp.py): its canonical transport stays the
    # one-shot CLI, which already runs dashboard/worker-local with no
    # control-plane hop, unlike Kimi/Grok whose ACP connection is their
    # only transport.
    assert "devin" not in server._ACP_WORKER_HARNESSES
    assert server._acp_harness_enabled("devin") is True


# ---------------------------------------------------------------------------
# Opt-in feature flag
# ---------------------------------------------------------------------------

def test_devin_acp_steer_disabled_by_default(monkeypatch):
    server = _server()
    monkeypatch.delenv("CCC_DEVIN_ACP_STEER", raising=False)
    assert server._devin_acp_steer_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
def test_devin_acp_steer_enabled_via_env(monkeypatch, value):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", value)
    assert server._devin_acp_steer_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_devin_acp_steer_stays_disabled_for_falsy_values(monkeypatch, value):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", value)
    assert server._devin_acp_steer_enabled() is False


# ---------------------------------------------------------------------------
# _devin_acp_steer_capable -- the "could a steer be attempted" UI signal
# ---------------------------------------------------------------------------

def test_devin_acp_steer_capable_disabled_by_default(monkeypatch):
    """With the opt-in flag off the row field must stay false — no Steer
    affordance, and _devin_acp_try_steer would decline anyway."""
    server = _server()
    monkeypatch.delenv("CCC_DEVIN_ACP_STEER", raising=False)
    with mock.patch.object(server, "_acp_resolve_bin") as resolve_bin:
        assert server._devin_acp_steer_capable() is False
    resolve_bin.assert_not_called()


def test_devin_acp_steer_capable_true_when_enabled_and_bin_resolves(monkeypatch):
    """No existing connection is required — the first steer attaches it
    lazily, so capability must not depend on _devin_acp_session_loaded."""
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True, "bin": "/usr/bin/devin"}), \
         mock.patch.object(server, "_devin_acp_session_loaded", return_value=False):
        assert server._devin_acp_steer_capable() is True


def test_devin_acp_steer_capable_false_when_harness_disabled(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    with mock.patch.object(server, "_acp_harness_enabled", return_value=False), \
         mock.patch.object(server, "_acp_resolve_bin") as resolve_bin:
        assert server._devin_acp_steer_capable() is False
    resolve_bin.assert_not_called()


def test_devin_acp_steer_capable_false_when_bin_unavailable(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
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


def test_devin_acp_try_steer_noop_when_feature_disabled(monkeypatch):
    """Default production state: the opt-in flag is off, so the ACP path
    must never even probe the binary or call _acp_prompt."""
    server = _server()
    monkeypatch.delenv("CCC_DEVIN_ACP_STEER", raising=False)
    with mock.patch.object(server, "_acp_resolve_bin") as resolve_bin, \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    resolve_bin.assert_not_called()
    prompt.assert_not_called()


def test_devin_acp_try_steer_noop_when_harness_disabled(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    with mock.patch.object(server, "_acp_harness_enabled", return_value=False), \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_not_called()


def test_devin_acp_try_steer_noop_when_bin_unavailable(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": False}), \
         mock.patch.object(server, "_acp_prompt") as prompt:
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_not_called()


def test_devin_acp_try_steer_swallows_exceptions(monkeypatch):
    """Any surprise (unexpected wire shape, hang that raises) degrades to
    None (fall back to the queue) instead of propagating out of the
    dispatcher and surfacing a new failure mode."""
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
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

def test_devin_acp_try_steer_happy_path(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
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


def test_devin_acp_try_steer_only_a_conclusive_ok_is_returned(monkeypatch):
    """A non-busy failure (not just busy) must also fall back to the queue,
    not surface as a new error to the caller."""
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value={"ok": False, "error": "empty prompt"}):
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None


def test_devin_acp_try_steer_cancels_and_retries_on_busy(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
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


def test_devin_acp_try_steer_gives_up_when_cancel_fails(monkeypatch):
    server = _server()
    monkeypatch.setenv("CCC_DEVIN_ACP_STEER", "1")
    busy = {"ok": False, "code": "busy"}
    with mock.patch.object(server, "_acp_harness_enabled", return_value=True), \
         mock.patch.object(server, "_acp_resolve_bin", return_value={"available": True}), \
         mock.patch.object(server, "_acp_prompt", return_value=busy) as prompt, \
         mock.patch.object(server, "_acp_cancel", return_value={"ok": False}):
        result = server._devin_acp_try_steer("devincli-x", "raw-1", None, "steer this")
    assert result is None
    prompt.assert_called_once()


# ---------------------------------------------------------------------------
# _devin_acp_session_loaded -- per-session UI signal
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
    ]


def test_devin_steer_falls_back_to_queue_when_acp_declines(monkeypatch):
    """No-regression guarantee: with CCC_DEVIN_ACP_STEER unset (today's
    production default), mode="steer" on a devincli- session must still land
    in exactly the same one-shot durable queue as before this harness
    existed. This exercises the REAL _devin_acp_try_steer (not mocked) so a
    future change to its default-off gate would be caught here."""
    server = _server()
    monkeypatch.delenv("CCC_DEVIN_ACP_STEER", raising=False)
    sid = "devincli-steer-fallback-test"
    with mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_steer", return_value=True) as queue_steer, \
         mock.patch.object(server, "_note_pending_queued") as note_queued, \
         mock.patch.object(server, "resume_session_devin") as resume, \
         mock.patch.object(server, "_control_plane_engine_call") as cp:
        stack = _devin_dispatch_common_mocks(server, sid)
        for cm in stack:
            cm.__enter__()
        try:
            result = server._inject_text_into_session(sid, "steer text", mode="steer")
        finally:
            for cm in reversed(stack):
                cm.__exit__(None, None, None)

    assert result["ok"] is True
    assert result["via"] == "devin-resume-queued"
    resume.assert_not_called()
    cp.assert_not_called()
    pump.assert_called_once_with(sid)
    queue_steer.assert_called_once_with(sid, "steer text")
    note_queued.assert_called_once()


def test_devin_steer_returns_acp_result_when_available(monkeypatch):
    """When _devin_acp_try_steer reaches a conclusive success, the dispatcher
    must return it directly and must NOT also enqueue the one-shot fallback
    (that would double-send)."""
    server = _server()
    sid = "devincli-steer-acp-test"
    acp_ok = {"ok": True, "via": "acp-prompt", "harness": "devin", "session_id": "steer-acp-test"}
    stack = _devin_dispatch_common_mocks(server, sid) + [
        mock.patch.object(server, "_devin_acp_try_steer", return_value=acp_ok),
    ]
    with mock.patch.object(server, "_pump_devin_resume_queue") as pump, \
         mock.patch.object(server, "_queue_devin_steer") as queue_steer, \
         mock.patch.object(server, "_save_pending_inputs") as save:
        for cm in stack:
            cm.__enter__()
        try:
            result = server._inject_text_into_session(sid, "steer text", mode="steer")
        finally:
            for cm in reversed(stack):
                cm.__exit__(None, None, None)

    assert result["ok"] is True
    assert result["via"] == "acp-prompt"
    queue_steer.assert_not_called()
    pump.assert_not_called()


def test_devin_non_steer_send_never_calls_acp_try_steer():
    """A plain follow-up (mode="send", the default) must not even consult the
    ACP path -- only mode="steer" does, matching Kimi/Grok's steer-only
    cancel/retry behavior."""
    server = _server()
    sid = "devincli-plain-send-test"
    acp_try_patch = mock.patch.object(server, "_devin_acp_try_steer")
    stack = _devin_dispatch_common_mocks(server, sid) + [acp_try_patch]
    with mock.patch.object(server, "_pump_devin_resume_queue"), \
         mock.patch.object(server, "_save_pending_inputs"):
        acp_try = None
        for cm in stack:
            entered = cm.__enter__()
            if cm is acp_try_patch:
                acp_try = entered
        try:
            server._inject_text_into_session(sid, "follow up")
        finally:
            for cm in reversed(stack):
                cm.__exit__(None, None, None)

    acp_try.assert_not_called()
