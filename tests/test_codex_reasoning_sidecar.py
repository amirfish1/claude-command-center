"""CCC-885: Codex reasoning-summary deltas should update the live sidecar.

Before this fix, `item/reasoning/summaryTextDelta` notifications from the
Codex app-server were received but discarded, so the "Thinking…" sidecar
stayed static (no live text) for the entire reasoning phase of a turn --
sometimes 5-45s with zero progress feedback. This asserts the streamed
delta text now lands in the active item and is surfaced via
`_codex_app_server_activity_fields`.
"""
import server


def _reset_state(thread_id):
    server._CODEX_APP_SERVER_THREAD_STATE.pop(thread_id, None)


def test_reasoning_summary_delta_updates_live_sidecar_text(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    thread_id = "thread-ccc-885"
    turn_id = "turn-ccc-885"
    _reset_state(thread_id)
    try:
        server._codex_app_server_handle_notification("turn/started", {
            "threadId": thread_id, "turnId": turn_id,
        })
        server._codex_app_server_handle_notification(
            "item/reasoning/summaryTextDelta",
            {
                "threadId": thread_id, "turnId": turn_id,
                "itemId": "item-1", "delta": "Looking at the ",
            },
        )
        server._codex_app_server_handle_notification(
            "item/reasoning/summaryTextDelta",
            {
                "threadId": thread_id, "turnId": turn_id,
                "itemId": "item-1", "delta": "spawn path first",
            },
        )
        fields = server._codex_app_server_activity_fields(thread_id)
        assert fields["sidecar_tool"] == "Thinking"
        assert fields["sidecar_file"] == "Looking at the spawn path first"
    finally:
        _reset_state(thread_id)


def test_reasoning_text_delta_without_item_id_is_ignored(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    thread_id = "thread-ccc-885-noid"
    turn_id = "turn-ccc-885-noid"
    _reset_state(thread_id)
    try:
        server._codex_app_server_handle_notification("turn/started", {
            "threadId": thread_id, "turnId": turn_id,
        })
        server._codex_app_server_handle_notification(
            "item/reasoning/textDelta",
            {"threadId": thread_id, "turnId": turn_id, "delta": "orphaned"},
        )
        fields = server._codex_app_server_activity_fields(thread_id)
        assert fields["sidecar_tool"] == "Thinking"
        assert fields["sidecar_file"] == ""
    finally:
        _reset_state(thread_id)
