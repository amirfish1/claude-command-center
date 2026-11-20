"""The sidebar's Codex tail keeps messages and cursors without tool formatting."""
import json

import pytest

import server
from ccc_server.ultra_fresh import FreshSession, message_text


def record(kind, **payload):
    return {"type": "event_msg", "timestamp": "2026-01-01T12:00:00Z",
            "payload": {"type": kind, **payload}}


def consumed(result):
    fields = ("line", "ts", "type", "blocks", "text", "pending", "parent_tool_use_id")
    return [{key: event[key] for key in fields if key in event}
            for event in result["events"]
            if event.get("type") in ("user_text", "user") or message_text(event)]


@pytest.mark.parametrize("tool_count", [4, 100])
def test_message_tail_matches_full_tail_and_preserves_absolute_cursor(tmp_path, monkeypatch,
                                                                    tool_count):
    rows = [record("user_message", message="Check the result.")]
    rows += [{"type": "response_item", "payload": {
        "type": "function_call", "name": "exec_command", "call_id": f"tool-{i}",
        "arguments": json.dumps({"cmd": "python3 -c 'print(1)'"})}}
        for i in range(tool_count)]
    rows += [record("agent_message", message="Checking the result."),
             record("item_completed", item={"id": "user", "type": "UserMessage",
                    "content": [{"type": "text", "text": "Now check the next result."}]}),
             record("item_completed", item={"id": "reply", "type": "AgentMessage",
                    "content": [{"type": "Text", "text": "The next result is ready."}]}),
             record("token_count", info={"last_token_usage": {
                 "input_tokens": 100, "output_tokens": 20}})]
    source = tmp_path / "rollout.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])
    regular = server._parse_conversation_windowed(
        "test-session", source, 80, None, server._parse_codex_event)
    messages = server._parse_conversation_windowed(
        "test-session", source, 80, None, server._parse_codex_event, messages_only=True)
    assert consumed(messages) == consumed(regular)
    assert messages["last_line"] == regular["last_line"] == len(rows)
    assert [event["type"] for event in messages["events"]] == [
        "user_text", "assistant", "user_text", "assistant"]
    assert messages["events"][0]["line"] == 1, "retain the user outside the tail"


def test_message_tail_does_not_format_discarded_shell_tools(tmp_path, monkeypatch):
    rows = [record("user_message", message="Check this command."),
            {"type": "response_item", "payload": {
                "type": "custom_tool_call", "name": "exec_command", "call_id": "tool",
                "input": '{"cmd":"python3 -c \'print(1)\'"}'}},
            record("agent_message", message="The command completed.")]
    source = tmp_path / "rollout.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])

    def no_tool_formatting(*args, **kwargs):
        raise AssertionError("message-only tail formatted a discarded command")

    monkeypatch.setattr(server, "_codex_custom_tool_detail", no_tool_formatting)
    result = server._parse_conversation_windowed(
        "test-session", source, 80, None, server._parse_codex_event, messages_only=True)
    assert [event["type"] for event in result["events"]] == ["user_text", "assistant"]
    assert result["last_line"] == 3


def test_default_parser_still_formats_tools_and_opt_in_reaches_window(tmp_path, monkeypatch):
    source = tmp_path / "rollout.jsonl"
    rows = [record("user_message", message="Check this command."),
            {"type": "response_item", "payload": {
                "type": "function_call", "name": "exec_command", "call_id": "tool",
                "arguments": '{"cmd":"echo example"}'}}]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "codex")
    monkeypatch.setattr(server, "_resolve_conversation_reader",
                        lambda *args, **kwargs: (source, server._parse_codex_event))
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])
    regular = server.parse_conversation("test-session", tail=80)
    messages = server.parse_conversation("test-session", tail=80, messages_only=True)
    assert any(block.get("kind") == "tool_use" for event in regular["events"]
               for block in event.get("blocks", []))
    assert len(messages["events"]) == 1
    assert consumed(messages) == consumed(regular)
    assert messages["last_line"] == regular["last_line"] == 2


@pytest.mark.parametrize("context_line, suffix_reads", [(250, 1), (95, 2)])
def test_message_tail_reuses_suffix_and_recovers_earlier_turn_context(
        tmp_path, monkeypatch, context_line, suffix_reads):
    source = tmp_path / "rollout.jsonl"
    rows = [{"type": "unrendered"} for _ in range(1206)]
    rows[context_line - 1] = {"type": "turn_context", "payload": {"turn_id": "earlier-turn"}}
    rows[399] = record("user_message", message="Check the result.")
    rows[-1] = record("agent_message", message="The result is ready.")
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "codex")
    monkeypatch.setattr(server, "_resolve_conversation_reader",
                        lambda *args, **kwargs: (source, server._parse_codex_event))
    baseline = server.parse_conversation("test-session", tail=80)
    read_tail = server._read_tail_lines
    reads = []

    def count_read(filepath, lines, **kwargs):
        reads.append(lines)
        return read_tail(filepath, lines, **kwargs)

    monkeypatch.setattr(server, "_read_tail_lines", count_read)
    result = server.parse_conversation("test-session", tail=80, messages_only=True)

    assert len(reads) == suffix_reads
    assert [ev["turn_id"] for ev in result["events"]] == ["earlier-turn", "earlier-turn"]
    assert consumed(result) == consumed(baseline)
    assert result["last_line"] == len(rows)


def test_live_reader_keeps_new_user_barrier_and_recovers_after_file_rewrite(tmp_path, monkeypatch):
    source = tmp_path / "rollout.jsonl"

    def at(kind, ts, **payload):
        return {**record(kind, **payload), "timestamp": ts}

    def write(rows, mode="w"):
        with source.open(mode) as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "codex")
    monkeypatch.setattr(server, "_resolve_conversation_reader",
                        lambda *args, **kwargs: (source, server._parse_codex_event))
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])
    monkeypatch.setattr(server, "_resolve_spawn_log_for_session", lambda sid: (None, False))
    monkeypatch.setattr(server, "_conv_parse_jsonl_mtime",
                        lambda sid: (source.stat().st_mtime_ns, source.stat().st_size))
    from ccc_server import ultra_fresh
    monkeypatch.setattr(ultra_fresh, "TRANSCRIPT_CHECK_S", 0)
    write([at("user_message", "2026-01-01T12:00:00Z", message="First request"),
           at("agent_message", "2026-01-01T12:00:01Z", message="First reply")])
    reader = FreshSession("test-message-tail-session")
    assert reader.read()[-1]["text"] == "First reply"
    write([at("user_message", "2026-01-01T12:01:00Z", message="Next request"),
           at("agent_message", "2026-01-01T12:00:02Z", message="Late old reply"),
           at("agent_message", "2026-01-01T12:01:01Z", message="Current reply")], "a")
    update = reader.read()[-1]
    assert update["text"] == "Current reply"
    assert update["user_ts"] == "2026-01-01T12:01:00Z"
    assert reader.last_line == 5
    write([at("user_message", "2026-01-01T12:02:00Z", message="Restored request"),
           at("agent_message", "2026-01-01T12:02:01Z", message="Restored reply")])
    assert reader.read()[-1]["text"] == "Restored reply"
    assert reader.last_line == 2


@pytest.mark.parametrize("gap, skipped_history", [(0, True), (80, True), (81, False)])
def test_message_cursor_limits_small_appends_but_keeps_long_gap_prompt(
        tmp_path, monkeypatch, gap, skipped_history):
    source = tmp_path / "rollout.jsonl"
    filler = {"type": "unrendered"}
    rows = [record("user_message", message="Earlier request."), *([filler] * 199)]
    if gap:
        rows += [record("user_message", message="New request."), *([filler] * (gap - 1))]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(server, "_get_queued_events_for_session", lambda sid: [])
    read_tail = server._read_tail_lines
    read_lengths = []

    def count_read(filepath, lines, **kwargs):
        result = read_tail(filepath, lines, **kwargs)
        read_lengths.append(len(result[1]))
        return result

    baseline = server._parse_conversation_windowed(
        "test-session", source, 80, None, server._parse_codex_event, messages_only=True)
    monkeypatch.setattr(server, "_read_tail_lines", count_read)
    result = server._parse_conversation_windowed(
        "test-session", source, 80, None, server._parse_codex_event,
        messages_only=True, message_cursor=200)

    assert result["last_line"] == baseline["last_line"] == 200 + gap
    assert [event for event in consumed(result) if event["line"] > 200] == [
        event for event in consumed(baseline) if event["line"] > 200
    ]
    assert read_lengths[0] == (gap if skipped_history else len(rows))
    if gap == 81:
        assert consumed(result)[-1]["text"] == "New request."
