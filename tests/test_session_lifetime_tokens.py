"""Regression coverage for lifetime token totals on conversation rows."""

import json
from pathlib import Path

import server


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_claude_tail_meta_sums_reported_usage_once_per_message(tmp_path):
    transcript = tmp_path / "claude.jsonl"
    first = {
        "type": "assistant",
        "timestamp": "2026-07-30T10:00:00Z",
        "message": {
            "id": "msg-1",
            "model": "claude-test",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "output_tokens": 40,
            },
            "content": [{"type": "text", "text": "First reply"}],
        },
    }
    _write_jsonl(transcript, [
        first,
        first,  # Resumed transcripts can replay the same message id.
        {
            "type": "assistant",
            "timestamp": "2026-07-30T10:01:00Z",
            "message": {
                "id": "msg-2",
                "model": "claude-test",
                "usage": {"input_tokens": 50, "output_tokens": 5},
                "content": [{"type": "text", "text": "Second reply"}],
            },
        },
    ])

    meta = server._extract_tail_meta(transcript)

    assert meta["lifetime_tokens"] == 245


def test_codex_tail_meta_uses_latest_cumulative_total(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    _write_jsonl(rollout, [
        {
            "type": "event_msg",
            "timestamp": "2026-07-30T10:00:00Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                },
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-30T10:01:00Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 390,
                        "output_tokens": 66,
                        "total_tokens": 456,
                    },
                },
            },
        },
    ])

    meta = server._extract_codex_tail_meta(rollout)

    assert meta["lifetime_tokens"] == 456


def test_kimi_lifetime_tokens_increment_without_double_counting(tmp_path):
    session_dir = tmp_path / "session"
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    _write_jsonl(wire, [
        {
            "type": "usage.record",
            "usageScope": "turn",
            "usage": {
                "inputOther": 100,
                "inputCacheRead": 20,
                "inputCacheCreation": 10,
                "output": 5,
            },
        },
        {
            "type": "usage.record",
            "usageScope": "session",
            "usage": {"inputOther": 999},
        },
    ])

    assert server._kimi_wire_lifetime_tokens(session_dir) == 135

    with wire.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "usage.record",
            "usageScope": "turn",
            "usage": {"inputOther": 50, "output": 15},
        }) + "\n")

    assert server._kimi_wire_lifetime_tokens(session_dir) == 200


def test_gemini_tail_meta_sums_message_totals(tmp_path, monkeypatch):
    chat = tmp_path / "chat.json"
    chat.write_text(json.dumps({
        "sessionId": "gemini-test",
        "messages": [
            {
                "type": "gemini",
                "timestamp": "2026-07-30T10:00:00Z",
                "model": "gemini-test",
                "tokens": {"input": 100, "output": 20, "total": 120},
            },
            {
                "type": "gemini",
                "timestamp": "2026-07-30T10:01:00Z",
                "model": "gemini-test",
                "tokens": {"input": 250, "output": 86, "total": 336},
            },
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(server, "_gemini_project_root_for_chat", lambda _path: str(tmp_path))

    meta = server._extract_gemini_tail_meta(chat)

    assert meta["lifetime_tokens"] == 456


def test_sidebar_renders_lifetime_tokens_beside_context_percent():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    app_css = Path("static/app.css").read_text(encoding="utf-8")
    server_py = Path("server.py").read_text(encoding="utf-8")

    assert "conv-lifetime-tokens" in app_js
    assert "lifetimeTokensHtml" in app_js
    assert app_js.index("lifetimeTokensHtml || ''") < app_js.index("pctBadgeHtml || ''")
    assert ".conv-lifetime-tokens" in app_css
    assert '"lifetime_tokens"' in server_py
