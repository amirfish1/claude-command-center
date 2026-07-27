"""Regression coverage for CCC's Kimi ``/goal`` compatibility shim."""

from __future__ import annotations

from unittest import mock

import server


def test_kimi_goal_command_uses_native_goal_tool_without_forwarding_slash():
    translated = server._kimi_goal_prompt_text(
        "/goal please fix the spawn failure so it never happens again"
    )

    assert not translated.startswith("/")
    assert "CreateGoal" in translated
    assert "please fix the spawn failure so it never happens again" in translated


def test_kimi_bare_goal_command_reads_native_goal():
    translated = server._kimi_goal_prompt_text("/goal")

    assert not translated.startswith("/")
    assert "GetGoal" in translated


def test_kimi_goal_translation_leaves_other_prompts_unchanged():
    assert server._kimi_goal_prompt_text("ordinary follow-up") == "ordinary follow-up"
    assert server._kimi_goal_prompt_text("/goalkeeper status") == "/goalkeeper status"


def test_kimi_goal_command_replays_as_the_original_visible_text():
    original = "/goal keep the queue empty"
    translated = server._kimi_goal_prompt_text(original)

    event = server._acp_message_event(
        {"sid": "session_test-kimi-goal", "next_line": 1},
        "user",
        translated,
    )

    assert event == {"type": "user_text", "text": original}


def test_kimi_goal_remote_busy_requeues_original_command():
    sid = "session_test-kimi-goal-busy"
    original = "/goal keep the queue empty"

    def request_async(
        _harness,
        _method,
        _params,
        *,
        sid=None,
        on_registered=None,
        **_kwargs,
    ):
        entry = {"sid": sid}
        if on_registered:
            on_registered(7, entry)
        return 7

    with mock.patch.object(server, "_ACP_SESSION_STATE", {"kimi": {}}), \
         mock.patch.object(server, "_control_plane_engine_call", return_value=None), \
         mock.patch.object(server, "_kimi_wire_turn_active", return_value=False), \
         mock.patch.object(server, "_acp_ensure_session_loaded", return_value=None), \
         mock.patch.object(server, "_acp_request_async", side_effect=request_async), \
         mock.patch.object(server, "_acp_emit_event_unlocked"), \
         mock.patch.object(server, "_queue_terminal_input") as queue:
        result = server._acp_prompt("kimi", sid, original)
        assert result["ok"] is True

        server._acp_finalize_turn(
            "kimi",
            sid,
            {
                "error": {
                    "message": (
                        "Invalid request: Cannot launch a new turn while "
                        "another turn (ID 7) is active"
                    ),
                },
            },
            {"req_id": 7, "is_active": True},
        )

    queue.assert_called_once_with(sid, original, {"status": "running"})


def test_kimi_acp_prompt_preserves_visible_goal_command():
    sid = "session_test-kimi-goal"
    sent = {}
    events = []

    def request_async(
        _harness,
        _method,
        params,
        *,
        sid=None,
        on_registered=None,
        on_send_failed=None,
        **_kwargs,
    ):
        sent["params"] = params
        entry = {"sid": sid}
        if on_registered:
            on_registered(91, entry)
        return 91

    def emit(_harness, _sid, event, save=False):
        events.append(dict(event))

    try:
        with mock.patch.object(server, "_control_plane_engine_call", return_value=None), \
             mock.patch.object(server, "_acp_ensure_session_loaded", return_value=None), \
             mock.patch.object(server, "_acp_request_async", side_effect=request_async), \
             mock.patch.object(server, "_acp_emit_event_unlocked", side_effect=emit):
            with server._ACP_LOCK:
                server._ACP_SESSION_STATE.setdefault("kimi", {}).pop(sid, None)
            result = server._acp_prompt(
                "kimi", sid, "/goal keep the queue empty"
            )

        assert result["ok"] is True
        wire_text = sent["params"]["prompt"][0]["text"]
        assert not wire_text.startswith("/")
        assert "CreateGoal" in wire_text
        assert events == [{
            "type": "user_text",
            "text": "/goal keep the queue empty",
        }]
    finally:
        with server._ACP_LOCK:
            server._ACP_SESSION_STATE.setdefault("kimi", {}).pop(sid, None)
