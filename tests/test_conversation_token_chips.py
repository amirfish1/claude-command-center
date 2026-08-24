"""Regression coverage for token chips on non-Claude conversation turns."""

import json

import server


def test_codex_usage_written_after_message_enriches_that_assistant_turn(tmp_path, monkeypatch):
    """Codex emits token_count after its agent_message, not before it."""
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("\n".join([
        json.dumps({
            "type": "event_msg", "timestamp": "2026-07-30T10:00:00Z",
            "payload": {"type": "user_message", "message": "hello"},
        }),
        json.dumps({
            "type": "event_msg", "timestamp": "2026-07-30T10:00:01Z",
            "payload": {"type": "agent_message", "message": "hello back"},
        }),
        json.dumps({
            "type": "event_msg", "timestamp": "2026-07-30T10:00:02Z",
            "payload": {"type": "token_count", "info": {"last_token_usage": {
                "input_tokens": 120, "cached_input_tokens": 80,
                "output_tokens": 15, "reasoning_output_tokens": 5,
            }}},
        }),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "codex")
    monkeypatch.setattr(
        server, "_resolve_conversation_reader",
        lambda _sid, repo_path=None: (str(rollout), server._parse_codex_event),
    )

    events = server.parse_conversation("codex-token-chip", use_cache=False)["events"]
    assistant = next(event for event in events if event["type"] == "assistant")

    assert assistant["tokens_in"] == 120
    assert assistant["tokens_out"] == 20
    assert assistant["tokens_cached"] == 80
    assert assistant["token_usage"]["cached_input_tokens"] == 80


def test_kimi_wire_usage_enriches_the_preceding_assistant_turn(tmp_path, monkeypatch):
    """Kimi's per-step usage is durable in wire.jsonl, outside ACP updates."""
    monkeypatch.setattr(server, "_ACP_TRANSCRIPT_DIR", tmp_path / "transcripts")
    harness, sid = "kimi", "session-token-chip"
    with server._ACP_LOCK:
        state = server._acp_session(harness, sid, create=True)
        state["wire_watch"] = True
        state["attached"] = True

    server._acp_wire_fold(harness, sid, [
        {"type": "context.append_loop_event", "event": {
            "type": "content.part", "part": {"type": "text", "text": "Kimi reply"},
        }},
        {"type": "usage.record", "usageScope": "turn", "usage": {
            "inputOther": 120, "inputCacheRead": 80,
            "inputCacheCreation": 20, "output": 15,
        }},
    ])

    with server._ACP_LOCK:
        assistant = next(event for event in state["events"] if event["type"] == "assistant")

    assert assistant["tokens_in"] == 220
    assert assistant["tokens_out"] == 15
    assert assistant["tokens_cached"] == 80
    assert assistant["token_usage"]["cache_creation_input_tokens"] == 20


def test_kimi_turn_usage_sums_wire_steps_since_the_prompt_boundary(tmp_path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text("\n".join([
        json.dumps({"type": "usage.record", "usageScope": "turn", "usage": {
            "inputOther": 100, "inputCacheRead": 80,
            "inputCacheCreation": 20, "output": 10,
        }}),
        json.dumps({"type": "usage.record", "usageScope": "turn", "usage": {
            "inputOther": 120, "inputCacheRead": 90,
            "inputCacheCreation": 0, "output": 15,
        }}),
    ]) + "\n", encoding="utf-8")

    usage = server._kimi_wire_turn_usage_since(wire, 0)

    assert usage == {
        "input_tokens": 220,
        "cache_read_input_tokens": 170,
        "cache_creation_input_tokens": 20,
        "output_tokens": 25,
    }
