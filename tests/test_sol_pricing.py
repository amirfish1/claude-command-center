"""Sol list-price estimates and invalidation of older unpriced turn caches."""
import importlib

import pytest

server = importlib.import_module("server")
usage = importlib.import_module("ccc_server.usage_stats")


def test_sol_known_rate_prices_uncached_cached_and_output_tokens():
    normalized = usage._throughput_normalize_usage(
        {"input_tokens": 100_000, "cached_input_tokens": 80_000, "output_tokens": 10_000},
        engine="codex", model="gpt-6.1-sol",
    )
    assert normalized["cost_available"] is True
    assert normalized["cost_usd"] == pytest.approx(0.148)
    assert normalized["effective_input_tokens"] == pytest.approx(24_000)


def test_sol_old_unpriced_disk_cache_reparsed(tmp_path, monkeypatch):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n")
    monkeypatch.setattr(usage, "_THROUGHPUT_TURN_CACHE", {})
    monkeypatch.setattr(usage, "_throughput_disk_get", lambda *args: [
        {"engine": "codex", "codex_usage_schema": 3, "cost_available": False}
    ])
    monkeypatch.setattr(usage, "_throughput_disk_put", lambda *args: None)
    calls = []

    def extract():
        calls.append(True)
        return [{"engine": "codex", "codex_usage_schema": 4, "cost_available": True}]

    assert usage._throughput_file_turns(rollout, extract)[0]["cost_available"]
    assert usage._throughput_file_turns(rollout, extract)[0]["cost_available"]
    assert len(calls) == 1
