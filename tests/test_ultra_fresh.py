"""Message-only, bounded live row feed; no list polling required."""
import json
from unittest import mock

import pytest

import server
from ccc_server import ultra_fresh
from ccc_server.ultra_fresh import FreshSession, message_text, session_ids


@pytest.fixture(autouse=True)
def _no_transcript_throttle(monkeypatch):
    # Tests drive read() back to back; the production throttle is covered below.
    monkeypatch.setattr(ultra_fresh, "TRANSCRIPT_CHECK_S", 0)


def event(text, line=2, ts="2026-10-23T10:00:02Z", **kw):
    return {"type": "assistant", "line": line, "ts": ts,
            "blocks": [{"kind": "text", "text": text}], **kw}


def test_subscription_is_bounded_and_never_accepts_paths():
    assert session_ids({"sessions": ["a,b,c,d,e,a,../../secret"]}) == ["a", "b", "c", "d"]
    assert session_ids({"sessions": ["../../secret,a,a"]}) == ["a"]


def test_only_agent_message_text_is_explanation():
    assert message_text(event("Checking the fix.")) == "Checking the fix."
    assert message_text(event("child", parent_tool_use_id="tool-1")) == ""
    for kind in ("thinking", "tool_use", "plan", "tool_result"):
        assert message_text({"type": "assistant", "blocks": [{"kind": kind, "text": "secret tool detail"}]}) == ""
    assert message_text({"type": "result", "text": "tool output"}) == ""


def test_new_message_arrives_from_transcript_without_archive_poll():
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", side_effect=[(1, 1), (2, 2), (3, 3)]), \
         mock.patch.object(server, "parse_conversation", side_effect=[
             {"events": [event("Old explanation")], "last_line": 2},
             {"events": [event("Old explanation"), event("New explanation", line=3, ts="2026-10-23T10:00:03Z")], "last_line": 3},
             {"events": [{"type": "assistant", "line": 4, "blocks": [{"kind": "tool_use", "name": "Bash", "detail": "noisy command"}]}], "last_line": 4},
         ]) as parse, \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read()[-1]["text"] == "Old explanation"
        assert reader.read()[-1]["text"] == "New explanation"
        assert reader.read() == []
        assert reader.last_text == "New explanation"
        assert all(call.kwargs == {"tail": 80} for call in parse.call_args_list)


def test_new_user_is_a_barrier_for_old_messages():
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [
             {"type": "user_text", "line": 3, "ts": "2026-10-23T10:01:00Z", "text": "New request"},
             event("Late previous turn", line=4),
             event("Current reply", line=5, ts="2026-10-23T10:01:01Z")], "last_line": 5}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        updates = reader.read()
        assert updates[0]["user_ts"] == "2026-10-23T10:01:00Z"
        assert [u["text"] for u in updates if "text" in u] == ["Current reply"]


def test_spawn_partial_message_uses_existing_normalizer_and_excludes_tools(tmp_path):
    log = tmp_path / "spawn.log"
    log.write_text("")
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(log, True)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read() == []
        rows = [
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Checking "}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "tool detail"}}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "the fix."}}},
        ]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        assert reader.read()[-1]["text"] == "Checking the fix."
        assert reader.read() == []


def test_fractional_timestamp_and_timezone_order_are_chronological():
    reader = FreshSession("test-session")
    assert reader._message("first", "2026-10-23T10:00:00Z")
    assert reader._message("new fraction", "2026-10-23T10:00:00.001Z")
    assert reader._message("old timezone", "2026-10-23T10:30:00+01:00") is None


def test_stream_coalesces_messages_and_caps_readers_without_archive_calls():
    from ccc_server.ultra_fresh import stream
    import io

    class Handler:
        wfile = io.BytesIO()
        send_response = lambda *args: None
        send_header = lambda *args: None
        end_headers = lambda *args: None

    handler = Handler()
    original_flush = handler.wfile.flush
    def disconnect():
        original_flush()
        raise BrokenPipeError()
    handler.wfile.flush = disconnect
    with mock.patch.object(FreshSession, "read", autospec=True,
                           side_effect=lambda reader: [{"session_id": reader.sid, "text": "Fresh agent message"}]) as read:
        stream(handler, {"sessions": ["a,b,c,d,e"]})
    assert [call.args[0].sid for call in read.call_args_list] == ["a", "b", "c", "d"]
    assert len(json.loads(handler.wfile.getvalue().decode().split("data: ")[1])["updates"]) == 4


def test_codex_live_snapshot_only_changes_on_new_message_and_never_replays_retired_turn():
    from ccc_server.codex_conversation import CODEX_CONVERSATIONS

    def snapshot(cursor, tid="t1", text="Retained reply", tools=False):
        items = [{"id": "m1", "type": "agentMessage", "text": text}]
        if tools:
            items.append({"id": "cmd", "type": "commandExecution", "command": "noisy command"})
        return {"generation": 1, "cursor": cursor, "thread": {"turns": [
            {"id": tid, "status": "inProgress", "items": items}]}}

    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="codex"), \
         mock.patch.object(CODEX_CONVERSATIONS, "snapshot", side_effect=[
             snapshot(1), snapshot(2), snapshot(3, text="Fresh text"),
             snapshot(4, text="Fresh text", tools=True), snapshot(5, tid="t2", text="New turn"),
             snapshot(6, tid="t1", text="Stale turn")]):
        assert reader.read() == []  # Retained baseline.
        assert reader.read() == []  # Another session changes the global cursor.
        assert reader.read()[-1]["text"] == "Fresh text"
        assert reader.read() == []  # Tools cannot replace explanation.
        assert reader.read()[-1]["text"] == "New turn"
        assert reader.read() == []  # Old turn cannot overwrite newer text.


def test_spawn_user_tool_result_cannot_clear_message_explanation(tmp_path):
    log = tmp_path / "spawn.log"
    log.write_text("")
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)), \
         mock.patch.object(server, "parse_conversation", return_value={"events": [event("Keep the agent explanation")], "last_line": 2}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(log, True)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        assert reader.read()[-1]["text"] == "Keep the agent explanation"
        log.write_text(json.dumps({"type": "user", "timestamp": "2026-10-23T10:01:00Z", "message": {
            "role": "user", "content": [{"type": "tool_result", "content": "tool output", "tool_use_id": "tool-1"}]}}) + "\n")
        assert reader.read() == []
        assert reader.last_text == "Keep the agent explanation"


def test_transcript_source_key_is_throttled_between_loop_ticks(monkeypatch):
    # CCC-152: per-tick source-key resolution (Codex SQLite, Hermes/Devin DB
    # keys) across every open stream starved the GIL and stalled /list.
    monkeypatch.setattr(ultra_fresh, "TRANSCRIPT_CHECK_S", 0.5)
    clock = [100.0]
    monkeypatch.setattr(ultra_fresh.time, "monotonic", lambda: clock[0])
    reader = FreshSession("test-session")
    with mock.patch.object(server, "_conv_parse_jsonl_mtime", return_value=(1, 1)) as key, \
         mock.patch.object(server, "parse_conversation", return_value={"events": [], "last_line": 0}), \
         mock.patch.object(server, "_resolve_spawn_log_for_session", return_value=(None, False)), \
         mock.patch.object(server, "_detect_session_engine", return_value="claude"):
        for _ in range(5):
            reader.read()
            clock[0] += 0.1
        assert key.call_count == 1
        clock[0] += 0.5
        reader.read()
        assert key.call_count == 2


def test_codex_source_key_reuses_resolved_reader_path(tmp_path, monkeypatch):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n")
    monkeypatch.setattr(server, "_CODEX_READER_PATH_CACHE", {})
    with mock.patch.object(server, "_detect_session_engine", return_value="codex"), \
         mock.patch.object(server, "_resolve_conversation_reader",
                           return_value=(rollout, None)) as resolve:
        first = server._conv_parse_jsonl_mtime("codex-thread")
        rollout.write_text("{}\n{}\n")
        second = server._conv_parse_jsonl_mtime("codex-thread")
    assert resolve.call_count == 1
    assert first != second
