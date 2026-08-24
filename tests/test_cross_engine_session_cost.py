"""End-to-end cost coverage for Codex and Kimi session usage extractors."""

import json

import pytest

import server


def test_codex_usage_payload_contains_list_price(tmp_path, monkeypatch):
    sid = "codex-cost-session"
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("\n".join(json.dumps(event) for event in [
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 800_000,
                "output_tokens": 100_000,
                "total_tokens": 1_100_000,
            },
            "total_token_usage": {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 800_000,
                "output_tokens": 100_000,
                "total_tokens": 1_100_000,
            },
            "model_context_window": 1_000_000,
        }}},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda _: rollout)
    monkeypatch.setattr(server, "_codex_thread_row", lambda _: {})

    usage = server._extract_codex_usage(sid)

    assert usage["cost_usd"] == pytest.approx(0.2 * 5 + 0.8 * 0.5 + 0.1 * 30)
    assert usage["cost_basis"] == "api_list_price"
    assert usage["cost_model"] == "gpt-5.6-sol"


def test_kimi_usage_payload_contains_list_price(tmp_path, monkeypatch):
    sid = "session_kimi-cost"
    main = tmp_path / "agents" / "main"
    main.mkdir(parents=True)
    (main / "wire.jsonl").write_text("\n".join(json.dumps(event) for event in [
        {"type": "config.update", "modelAlias": "kimi-code/k3"},
        {"type": "usage.record", "usage": {
            "inputOther": 200_000,
            "inputCacheCreation": 100_000,
            "inputCacheRead": 800_000,
            "output": 100_000,
        }},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        server,
        "_kimi_session_index",
        lambda: {sid: {"session_dir": str(tmp_path)}},
    )

    usage = server._extract_kimi_usage(sid)

    assert usage["cost_usd"] == pytest.approx(0.2 * 3 + 0.1 * 3 + 0.8 * 0.3 + 0.1 * 15)
    assert usage["cost_basis"] == "api_list_price"
    assert usage["cost_model"] == "kimi-code/k3"
