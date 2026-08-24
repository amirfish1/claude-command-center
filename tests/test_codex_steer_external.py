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
