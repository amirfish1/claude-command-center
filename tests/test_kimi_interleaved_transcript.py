"""Kimi (ACP) transcript richness — the CCC pane must read like the Kimi
terminal: thought → text → tool row (with the command's output) → thought →
… in stream order, not "every tool row first, one mashed text block last".

Drives _acp_handle_session_update / _acp_finalize_turn directly with the
exact `session/update` shapes kimi's acp-adapter emits (docs/kimi-code-
reference.md); no subprocess, no network.
"""

from __future__ import annotations

import pathlib

import pytest

import server

HARNESS = "kimi"


@pytest.fixture(autouse=True)
def isolated_acp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_ACP_TRANSCRIPT_DIR", pathlib.Path(tmp_path) / "acp")
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", pathlib.Path(tmp_path))
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {HARNESS: {}})
    monkeypatch.setattr(server, "_ACP_TERMINAL_OUTPUT_CACHE", server.collections.OrderedDict())
    # No wire.jsonl for these synthetic sessions — usage lookup is a no-op.
    monkeypatch.setattr(server, "_kimi_wire_turn_usage_since", lambda *_a, **_k: None)
    yield
    with server._ACP_TERMINALS_LOCK:
        leftovers = list(server._ACP_TERMINALS.items())
        server._ACP_TERMINALS.clear()
    for _, entry in leftovers:
        if entry["proc"].poll() is None:
            entry["proc"].kill()
        entry["exit_event"].set()


def _start_turn(sid, msg_id="acp-kimi-1"):
    with server._ACP_LOCK:
        state = server._acp_session(HARNESS, sid, create=True, cwd="/tmp")
        state["status"] = "active"
        state["active_turn"] = {
            "req_id": 1, "msg_id": msg_id, "text": "", "thought": "",
            "tools": {}, "prompt": "go", "from_queue": False,
            "wire_offset": None,
        }
        return state


def _update(sid, **update):
    server._acp_handle_session_update(HARNESS, sid, update)


def _chunk(sid, kind, text):
    _update(sid, sessionUpdate=kind, content={"type": "text", "text": text})


def _events(sid):
    with server._ACP_LOCK:
        return list((server._acp_session(HARNESS, sid) or {}).get("events") or [])


def _shape(events):
    """[(type, [block kinds])] — the transcript's visible order."""
    out = []
    for ev in events:
        if ev.get("type") == "assistant":
            out.append(("assistant", [b.get("kind") for b in ev.get("blocks") or []]))
        else:
            out.append((ev.get("type"), ev.get("subtype")))
    return out


def _finish(sid, stop="end_turn"):
    entry = {"is_active": True, "req_id": 1}
    server._acp_finalize_turn(HARNESS, sid, {"result": {"stopReason": stop}}, entry)
    return entry


def test_live_turn_keeps_stream_order_around_tool_calls():
    sid = "session_interleave"
    _start_turn(sid)
    # Step 1: think, say, run.
    _chunk(sid, "agent_thought_chunk", "URL works. ")
    _chunk(sid, "agent_thought_chunk", "Download it.")
    _chunk(sid, "agent_message_chunk", "The URL works. Downloading.")
    _update(sid, sessionUpdate="tool_call", toolCallId="0:t1", title="Bash",
            kind="execute", status="in_progress", rawInput={"command": "curl -sI x"})
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:t1", status="completed",
            content=[{"type": "content", "content": {"type": "text", "text": "HTTP/2 200"}}])
    # Step 2: think, say, run two in parallel.
    _chunk(sid, "agent_thought_chunk", "Specs check out.")
    _chunk(sid, "agent_message_chunk", "Specs confirmed. Extracting frames.")
    _update(sid, sessionUpdate="tool_call", toolCallId="0:t2", title="Bash",
            kind="execute", status="in_progress", rawInput={"command": "ffmpeg a"})
    _update(sid, sessionUpdate="tool_call", toolCallId="0:t3", title="Bash",
            kind="execute", status="in_progress", rawInput={"command": "ffmpeg b"})
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:t2", status="completed",
            rawOutput="frame_0.7s.jpg")
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:t3", status="completed",
            rawOutput="audio.wav")
    # Answer.
    _chunk(sid, "agent_message_chunk", "You're right — the eight shots were trimmed segments.")
    entry = _finish(sid)

    events = _events(sid)
    assert _shape(events) == [
        ("assistant", ["thinking", "text"]),
        ("assistant", ["tool_use"]),
        ("assistant", ["thinking", "text"]),
        ("assistant", ["tool_use"]),
        ("assistant", ["tool_use"]),
        ("assistant", ["text"]),
        ("result", "end_turn"),
    ]
    first = events[0]["blocks"]
    assert first[0]["text"] == "URL works. Download it."
    assert first[1]["text"] == "The URL works. Downloading."
    t1 = events[1]["blocks"][0]
    assert t1["name"] == "Bash" and t1["detail"] == "curl -sI x"
    assert t1["tool_status"] == "completed" and t1["output_preview"] == "HTTP/2 200"
    assert events[2]["blocks"][1]["text"] == "Specs confirmed. Extracting frames."
    assert [e["blocks"][0]["output_preview"] for e in events[3:5]] == ["frame_0.7s.jpg", "audio.wav"]
    assert events[5]["blocks"][0]["text"].startswith("You're right")
    # Every row of the turn shares the turn's message id (stream hand-off key).
    assert {e["message_id"] for e in events if e["type"] == "assistant"} == {"acp-kimi-1"}
    # The synchronous-ask answer is still the WHOLE turn's text.
    assert entry["final_text"] == (
        "The URL works. Downloading.Specs confirmed. Extracting frames."
        "You're right — the eight shots were trimmed segments.")
    # And the transcript file mirrors the in-memory order.
    lines = server._acp_transcript_path(HARNESS, sid).read_text().splitlines()
    assert len(lines) == len(events)


def test_text_before_a_plan_update_lands_above_the_plan():
    sid = "session_plan_order"
    _start_turn(sid)
    _chunk(sid, "agent_message_chunk", "Here's the plan.")
    _update(sid, sessionUpdate="plan", entries=[
        {"content": "do a", "status": "in_progress", "priority": "medium"}])
    _chunk(sid, "agent_message_chunk", "Starting on a.")
    _finish(sid)
    assert _shape(_events(sid)) == [
        ("assistant", ["text"]),
        ("assistant", ["plan"]),
        ("assistant", ["text"]),
        ("result", "end_turn"),
    ]


def test_turn_with_only_tools_and_no_trailing_text_still_finalizes():
    sid = "session_tools_only"
    _start_turn(sid)
    _update(sid, sessionUpdate="tool_call", toolCallId="0:t1", title="Read",
            kind="read", status="in_progress", rawInput={"path": "/tmp/a"})
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:t1", status="completed",
            rawOutput="contents")
    entry = _finish(sid, stop="cancelled")
    assert _shape(_events(sid)) == [("assistant", ["tool_use"]), ("result", "cancelled")]
    assert entry["final_text"] == ""


def test_terminal_backed_bash_row_carries_output_after_release(monkeypatch):
    """Kimi's Bash completion update carries only {type:'terminal'} — and the
    agent releases the terminal before sending it. The row must still show
    the command's output."""
    responses = {}
    monkeypatch.setattr(server, "_acp_respond",
                        lambda harness, req_id, result=None, error=None: responses.__setitem__(req_id, result))
    sid = "session_terminal_output"
    _start_turn(sid)
    _chunk(sid, "agent_message_chunk", "Checking codecs.")
    _update(sid, sessionUpdate="tool_call", toolCallId="0:tb", title="Bash",
            kind="execute", status="in_progress", rawInput={"command": "/bin/echo codec_name=hevc"})
    # Agent runs the command through CCC's terminal capability …
    server._acp_handle_terminal_request(HARNESS, 1, "terminal/create", {
        "sessionId": sid, "command": "/bin/echo", "args": ["codec_name=hevc"]})
    tid = responses[1]["terminalId"]
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:tb",
            content=[{"type": "terminal", "terminalId": tid}])
    server._acp_handle_terminal_request(HARNESS, 2, "terminal/wait_for_exit", {
        "sessionId": sid, "terminalId": tid})
    with server._ACP_TERMINALS_LOCK:
        entry = server._ACP_TERMINALS[tid]
    entry["exit_event"].wait(10)
    # … reads it, releases it, THEN reports completion pointing at the
    # (now gone) terminal.
    server._acp_handle_terminal_request(HARNESS, 3, "terminal/release", {
        "sessionId": sid, "terminalId": tid})
    with server._ACP_TERMINALS_LOCK:
        assert tid not in server._ACP_TERMINALS
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:tb", status="completed",
            content=[{"type": "terminal", "terminalId": tid}])
    _finish(sid)

    events = _events(sid)
    assert _shape(events) == [
        ("assistant", ["text"]),
        ("assistant", ["tool_use"]),
        ("result", "end_turn"),
    ]
    row = events[1]["blocks"][0]
    assert row["name"] == "Bash"
    assert row["tool_status"] == "completed"
    assert row["output_preview"] == "codec_name=hevc"
    # The live tool_result delta carried it too.
    with server._ACP_LOCK:
        deltas = [d["event"] for d in server._acp_session(HARNESS, sid)["deltas"]]
    results = [b for d in deltas for b in d.get("blocks", []) if b.get("type") == "tool_result"]
    assert results and results[0]["text"] == "codec_name=hevc"


def test_terminal_output_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(server, "_ACP_TERMINAL_OUTPUT_CACHE_MAX", 3)
    with server._ACP_TERMINALS_LOCK:
        for i in range(5):
            server._acp_terminal_cache_output_unlocked(f"t{i}", {"output": str(i)})
    assert list(server._ACP_TERMINAL_OUTPUT_CACHE) == ["t2", "t3", "t4"]
    assert server._acp_terminal_output_snapshot("t4") == {"output": "4"}
    assert server._acp_terminal_output_snapshot("t0") is None


def test_raw_output_shapes():
    assert server._acp_raw_output_text(" plain ") == "plain"
    assert server._acp_raw_output_text([
        {"type": "text", "text": "<image path=a.jpg>"},
        {"type": "image", "data": "..."},
        {"type": "text", "text": "done"},
    ]) == "<image path=a.jpg>\ndone"
    assert server._acp_raw_output_text({"output": "o", "exitCode": 0}) == "o"
    assert server._acp_raw_output_text(None) == ""


def test_failed_terminal_command_without_output_reports_exit_code():
    with server._ACP_TERMINALS_LOCK:
        server._acp_terminal_cache_output_unlocked("tdead", {
            "output": "", "truncated": False,
            "exitStatus": {"exitCode": 127, "signal": None}})
    text = server._acp_tool_content_text({
        "status": "failed", "content": [{"type": "terminal", "terminalId": "tdead"}]})
    assert text == "(no output, exit code 127)"


def test_session_load_replay_persists_tool_rows_in_order():
    """A resumed session with no CCC transcript replays its history over
    session/load; tool calls used to be dropped there (only text/thought
    survived), so the pane showed a tool-less conversation."""
    sid = "session_replay_tools"
    with server._ACP_LOCK:
        state = server._acp_session(HARNESS, sid, create=True, cwd="/tmp")
        state["replay"] = {"kind": None, "text": ""}
    _chunk(sid, "user_message_chunk", "review the mp4")
    _chunk(sid, "agent_thought_chunk", "Need to probe it.")
    _chunk(sid, "agent_message_chunk", "Probing the file.")
    _update(sid, sessionUpdate="tool_call", toolCallId="0:r1", title="Bash",
            kind="execute", status="in_progress", rawInput={"command": "ffprobe x.mp4"})
    _update(sid, sessionUpdate="tool_call_update", toolCallId="0:r1", status="completed",
            rawOutput="duration=15.0")
    _chunk(sid, "agent_message_chunk", "It's exactly 15s.")
    # A call whose terminal status never replayed (interrupted turn) flushes
    # at load end, still in order.
    _update(sid, sessionUpdate="tool_call", toolCallId="0:r2", title="Read",
            kind="read", status="in_progress", rawInput={"path": "/tmp/notes.md"})
    with server._ACP_LOCK:
        state = server._acp_session(HARNESS, sid)
        server._acp_replay_flush_unlocked(HARNESS, sid, state, state["replay"])
        state["replay"] = None

    events = _events(sid)
    assert _shape(events) == [
        ("user_text", None),
        ("assistant", ["thinking"]),
        ("assistant", ["text"]),
        ("assistant", ["tool_use"]),
        ("assistant", ["text"]),
        ("assistant", ["tool_use"]),
    ]
    assert events[0]["text"] == "review the mp4"
    probe = events[3]["blocks"][0]
    assert probe["name"] == "Bash" and probe["detail"] == "ffprobe x.mp4"
    assert probe["output_preview"] == "duration=15.0" and probe["tool_status"] == "completed"
    assert events[4]["blocks"][0]["text"] == "It's exactly 15s."
    read = events[5]["blocks"][0]
    assert read["name"] == "Read" and read["detail"] == "/tmp/notes.md"
    assert "tool_status" not in read  # never reached a terminal status
