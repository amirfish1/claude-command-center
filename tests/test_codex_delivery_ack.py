"""The turn-confirmation path must recognize codex's real UserMessage shape.

The delivery ack used to expect `item.type == "userMessage"` + `.text` on the
notification and a `user_message` event_msg in the rollout. Current codex emits
`UserMessage` items carrying `content[].text` (and records them as
`item_completed` event_msg rows), so every send burned the full 5s confirm
window with `confirmed: False` before returning.
"""
import importlib
import json

import pytest
from unittest import mock

server = importlib.import_module("server")
from ccc_server import codex  # noqa: E402

SID = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"


def _reset_thread_state(sid=SID):
    # Leftover last_activity_at/active_turn_id in the shared thread-state map
    # shields neighbouring tests ("recent activity → app-server is busy, not
    # wedged") because they freeze time.time() far below the real epoch.
    with server._CODEX_APP_SERVER_LOCK:
        server._CODEX_APP_SERVER_THREAD_STATE.pop(sid, None)


def _feed_item_completed(item, sid=SID, turn_id="turn-1"):
    # Feeding a notification stamps LAST_MSG_AT/EVENT_SEQ — save and restore
    # so neighbouring tests that freeze time.time() do not see a "recent
    # traffic" shortcut from our messages.
    saved_msg_at = codex._CODEX_APP_SERVER_LAST_MSG_AT
    saved_seq = codex._core._CODEX_APP_SERVER_EVENT_SEQ
    try:
        codex._codex_app_server_handle_message({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {"threadId": sid, "turnId": turn_id, "item": item},
        })
    finally:
        codex._CODEX_APP_SERVER_LAST_MSG_AT = saved_msg_at
        codex._core._CODEX_APP_SERVER_EVENT_SEQ = saved_seq


def _state(sid=SID):
    return codex._core._CODEX_APP_SERVER_THREAD_STATE.get(sid) or {}


def test_item_completed_usermessage_capitalized_records_delivery():
    _reset_thread_state()
    _feed_item_completed({
        "type": "UserMessage",
        "id": "item-1",
        "content": [{"type": "text", "text": "ok waht else is left"}],
    })
    state = _state()
    assert state.get("last_delivered_user_text") == "ok waht else is left"
    assert state.get("last_delivered_user_turn_id") == "turn-1"


def test_item_completed_legacy_lowercase_shape_still_records_delivery():
    _reset_thread_state()
    _feed_item_completed({"type": "userMessage", "text": "legacy text"})
    assert _state().get("last_delivered_user_text") == "legacy text"


def test_item_completed_non_user_items_do_not_mark_delivery():
    _reset_thread_state()
    _feed_item_completed({"type": "AgentMessage", "text": "agent said hi"})
    assert _state().get("last_delivered_user_text") is None


def test_rollout_contains_user_text_matches_item_completed_row(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    baseline_size = 0
    rollout.write_text(json.dumps({
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "thread_id": SID,
            "turn_id": "turn-9",
            "item": {
                "type": "UserMessage",
                "id": "item-2",
                "content": [{"type": "text", "text": "the real prompt"}],
            },
        },
    }) + "\n")
    baseline = {"path": str(rollout), "size": baseline_size, "mtime_ns": 0}
    with mock.patch.object(
        server, "_resolve_codex_rollout_path", return_value=str(rollout),
    ):
        assert codex._codex_rollout_contains_user_text_since(
            baseline, SID, "the real prompt",
        )
        assert not codex._codex_rollout_contains_user_text_since(
            baseline, SID, "a different prompt",
        )


def test_rollout_contains_user_text_matches_legacy_user_message_row(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(json.dumps({
        "type": "event_msg",
        "payload": {"type": "user_message", "message": "old shape"},
    }) + "\n")
    baseline = {"path": str(rollout), "size": 0, "mtime_ns": 0}
    with mock.patch.object(
        server, "_resolve_codex_rollout_path", return_value=str(rollout),
    ):
        assert codex._codex_rollout_contains_user_text_since(
            baseline, SID, "old shape",
        )


def test_wait_for_turn_activity_confirms_from_usermessage_notification():
    _reset_thread_state()
    _feed_item_completed({
        "type": "UserMessage",
        "content": [{"type": "text", "text": "the prompt"}],
    }, turn_id="turn-7")
    with mock.patch.object(server, "_codex_rollout_stat", return_value=None):
        result = codex._codex_wait_for_turn_activity(
            SID, "turn-7", expected_text="the prompt", timeout=1.0,
        )
    assert result["confirmed"] is True
    assert result["source"] == "app-server-notification"


@pytest.fixture(autouse=True)
def _clean_thread_state():
    yield
    _reset_thread_state()
