"""Regression coverage for steering an active Codex thread."""

from unittest import mock

import server


def test_codex_steer_attempts_native_rpc_despite_external_writer_snapshot():
    session_id = "019fca00-af1d-7771-bffa-bc81f46b4b53"
    with (
        mock.patch.object(
            server,
            "_codex_thread_writer_snapshot",
            return_value={"external_active": True, "writer": "unknown"},
        ),
        mock.patch.object(server, "_codex_note_external_writer_transition"),
        mock.patch.object(
            server,
            "_codex_app_server_request",
            side_effect=[
                {
                    "result": {
                        "thread": {
                            "status": {"type": "active"},
                            "turns": [{"id": "turn-1", "status": "inProgress"}],
                        }
                    }
                },
                {"result": {"turnId": "turn-1"}},
            ],
        ) as request,
    ):
        result = server._codex_steer_via_app_server(session_id, "Please steer now")

    assert result["ok"]
    assert result["via"] == "codex-steer"
    assert request.call_args_list[0] == mock.call(
        "thread/resume", {"threadId": session_id, "excludeTurns": False}, timeout=20
    )
    steer_args, steer_kwargs = request.call_args_list[1]
    assert steer_args[0] == "turn/steer"
    assert steer_args[1]["threadId"] == session_id
    assert steer_args[1]["expectedTurnId"] == "turn-1"
    assert steer_kwargs == {"timeout": 20}


def test_codex_steer_fallback_send_uses_a_key_the_ledger_has_not_burned():
    """Steer on an idle Codex thread must fall back to a real send.

    The steer attempt is already recorded as failed under the caller's
    idempotency key; reusing it dedupes the fallback turn/start back to that
    failure, so the message is never sent and the UI shows "No running Codex
    turn to steer".
    """
    sid = "codex-steer-idle-session"
    calls = []

    def _resume(session_id, text, **kwargs):
        calls.append(kwargs)
        if kwargs.get("steer"):
            return {
                "ok": False,
                "code": "codex_no_active_turn",
                "error": "No running Codex turn to steer",
            }
        return {"ok": True, "via": "codex-resume"}

    with mock.patch.object(server, "_is_codex_session", return_value=True), \
         mock.patch.object(server, "find_session_cwd", return_value="/tmp"), \
         mock.patch.object(server, "session_live_status", return_value={
             "live": True, "status": "idle", "kind": "codex",
             "tty": None, "terminal_app": None,
         }), \
         mock.patch.object(server, "resume_session_codex", side_effect=_resume), \
         mock.patch.object(server, "_consume_matching_pending_input"):
        result = server._inject_text_into_session(
            sid, "actually, stop", mode="steer",
            idempotency_key="inject:deadbeef",
        )

    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == "inject:deadbeef"
    assert calls[1]["idempotency_key"] != calls[0]["idempotency_key"]
    assert result["ok"]
