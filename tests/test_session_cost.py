"""Regression coverage for cache-aware Claude session cost estimates."""

import json

import pytest

import server


def test_claude_usage_cost_breakdown_prices_each_cache_bucket():
    cost = server._claude_usage_cost_breakdown(
        "claude-opus-5",
        {
            "input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        },
    )

    assert cost["input"] == pytest.approx(5.0)
    assert cost["cache_creation"] == pytest.approx(6.25)
    assert cost["cache_read"] == pytest.approx(0.5)
    assert cost["output"] == pytest.approx(25.0)
    assert cost["total"] == pytest.approx(36.75)


def test_claude_usage_cost_breakdown_uses_one_hour_cache_write_rate():
    cost = server._claude_usage_cost_breakdown(
        "claude-opus-5",
        {
            "cache_creation_input_tokens": 1_000_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000},
        },
    )

    assert cost["cache_creation"] == pytest.approx(10.0)
    assert cost["total"] == pytest.approx(10.0)


def test_claude_tail_meta_exposes_cache_aware_session_cost(tmp_path):
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg-1",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            },
            "content": [],
        },
    }) + "\n", encoding="utf-8")

    meta = server._extract_tail_meta(transcript)

    assert meta["cost_usd"] == pytest.approx(36.75)
    assert meta["cost_breakdown_usd"]["cache_read"] == pytest.approx(0.5)
    assert meta["total_cache_creation_tokens"] == 1_000_000
    assert meta["total_cache_read_tokens"] == 1_000_000
