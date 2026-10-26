import json
from unittest import mock

import server


def test_codex_tail_keeps_preceding_prompt_without_changing_pagination(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    rows = [{"type": "event_msg", "payload": {"type": "agent_message", "message": f"Earlier {i}"}} for i in range(20)]
    rows += [{"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "turn-1",
        "item": {"type": "UserMessage", "content": [{"type": "text", "text": "Please repair this installation"}]}}}]
    rows += [{"type": "event_msg", "payload": {"type": "agent_message", "message": f"Progress {i}"}} for i in range(20)]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with mock.patch.object(server, "_resolve_conversation_reader", return_value=(transcript, server._parse_codex_event)), mock.patch.object(server, "_detect_session_engine", return_value="codex"), mock.patch.object(server, "_get_queued_events_for_session", return_value=[]):
        tail = server.parse_conversation("test-thread", tail=5, use_cache=False)
        assert tail["events"][0]["type"] == "user_text"
        assert tail["events"][0]["text"] == "Please repair this installation"
        assert tail["first_line"] == 21
        assert tail["truncated_before"]
        earlier = server.parse_conversation("test-thread", tail=30, before=tail["first_line"], use_cache=False)
    assert [e["line"] for e in earlier["events"]] == list(range(1, 21))
    assert [e["line"] for e in tail["events"]] == list(range(21, 42))


def test_tail_starting_with_user_does_not_duplicate_prompt(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    rows = [{"type": "event_msg", "payload": {"type": "user_message", "message": "old"}},
            {"type": "turn_context", "payload": {"model": "example"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}}]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = server._parse_conversation_windowed("test-thread", transcript, 2, None, server._parse_codex_event)
    assert len(result["events"]) == 1 and result["events"][0]["text"] == "hello"
    assert not result["events"][0].get("window_context")


def test_lookback_uses_new_snapshot_cursor_when_transcript_grows(tmp_path):
    rows = [{"type": "event_msg", "payload": {"type": "user_message", "message": "request"}}]
    rows += [{"type": "event_msg", "payload": {"type": "agent_message", "message": f"response {i}"}} for i in range(6)]
    numbered = [(n + 1, json.dumps(row)) for n, row in enumerate(rows)]
    with mock.patch.object(server, "_read_tail_lines", side_effect=[(6, numbered[4:6]), (7, numbered)]):
        result = server._parse_conversation_windowed("test-thread", tmp_path / "unused.jsonl", 2, None, server._parse_codex_event)
    assert result["last_line"] == 7
    assert result["events"][-1]["line"] == 7


def test_codex_window_inherits_turn_id_from_turn_context_before_window(tmp_path):
    # CCC-219: turn_context precedes the prompt, so a window that starts at
    # the prompt (or mid-turn) must still stamp the turn's rows with turn_id,
    # or the client re-appends that turn's live app-server overlay.
    transcript = tmp_path / "rollout.jsonl"
    rows = [{"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "example"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "first ask"}}]
    rows += [{"type": "event_msg", "payload": {"type": "agent_message", "message": f"work {i}"}} for i in range(10)]
    rows += [{"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "done"}}]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    tail = server._parse_conversation_windowed("test-thread", transcript, 5, None, server._parse_codex_event)
    assert tail["first_line"] == 2
    assert tail["events"] and all(e.get("turn_id") == "turn-1" for e in tail["events"])
    earlier = server._parse_conversation_windowed("test-thread", transcript, 3, 8, server._parse_codex_event)
    assert earlier["events"] and all(e.get("turn_id") == "turn-1" for e in earlier["events"])
