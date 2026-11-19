"""Repeated usage reads stay current without reparsing unchanged transcripts."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ccc_server import morning_launch as usage
from ccc_server.bounded_cache import ByteBudgetCache


def assistant(number=1, tokens=12):
    return {"type": "assistant", "effort": "high", "message": {
        "id": f"message-{number}", "model": "example-model",
        "usage": {"input_tokens": tokens, "output_tokens": 3}}}


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    target = tmp_path / "session.jsonl"
    target.write_text(json.dumps(assistant()) + "\n")
    core = SimpleNamespace(
        PROJECTS_ROOT=tmp_path, _get_session_override=lambda sid: {},
        _find_session_jsonl=lambda sid: target,
        _load_desktop_app_metadata=lambda: {},
        _safe_parse_message=lambda message: message,
        _codex_int=lambda value: int(value or 0),
    )
    for engine in ("codex", "gemini", "cursor", "antigravity", "hermes",
                   "kimi", "grok", "devin_cli"):
        setattr(core, "_is_" + engine + "_session", lambda sid: False)
    monkeypatch.setattr(usage, "_core", core)
    monkeypatch.setattr(usage, "_CLAUDE_USAGE_SNAPSHOTS",
                        ByteBudgetCache(max_bytes=4 << 20, max_entries=32))
    return target


def test_repeated_reads_reuse_parse_but_results_are_independent(transcript, monkeypatch):
    parse = usage._read_claude_usage_snapshot
    calls = []
    def counted(*args):
        calls.append(args)
        return parse(*args)
    monkeypatch.setattr(usage, "_read_claude_usage_snapshot", counted)
    first = usage.extract_session_usage("session")
    expected = usage.extract_session_usage("session")
    first["turn_series"][0]["tokens_in"] = -1
    first["cost_breakdown_usd"]["input"] = -1
    assert usage.extract_session_usage("session") == expected
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["append", "rewrite", "replace", "truncate"])
def test_file_changes_invalidate_even_with_preserved_mtime(transcript, change):
    assert usage.extract_session_usage("session")["total_input_tokens"] == 12
    info = transcript.stat()
    if change == "append":
        with transcript.open("a") as handle:
            handle.write(json.dumps(assistant(2, 24)) + "\n")
        expected = 36
    elif change == "truncate":
        transcript.write_text("")
        expected = 0
    else:
        text = transcript.read_text().replace('"input_tokens": 12', '"input_tokens": 24')
        assert len(text) == info.st_size
        if change == "replace":
            other = transcript.with_suffix(".new")
            other.write_text(text)
            other.replace(transcript)
        else:
            transcript.write_text(text)
        expected = 24
    os.utime(transcript, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert usage.extract_session_usage("session")["total_input_tokens"] == expected


def test_overrides_desktop_model_and_prices_are_live(transcript, monkeypatch):
    transcript.write_text(json.dumps({"type": "assistant", "message": {
        "usage": {"input_tokens": 10}}}) + "\n")
    core = usage._core
    monkeypatch.setattr(core, "_get_session_override", lambda sid: {"reasoning_effort": "low"})
    monkeypatch.setattr(core, "_load_desktop_app_metadata", lambda: {"session": {"model": "one"}})
    monkeypatch.setattr(usage, "_rates_for_model", lambda model: (1, 0, 0, 0))
    first = usage.extract_session_usage("session")
    monkeypatch.setattr(core, "_get_session_override", lambda sid: {"reasoning_effort": "high"})
    monkeypatch.setattr(usage, "_rates_for_model", lambda model: (10, 0, 0, 0))
    second = usage.extract_session_usage("session")
    assert first["reasoning_effort"] == "low" and second["reasoning_effort"] == "high"
    assert first["cost_usd"] == 0 and second["cost_usd"] == 0.0001
    monkeypatch.setattr(core, "_load_desktop_app_metadata", lambda: {"session": {"model": "two"}})
    assert usage.extract_session_usage("session")["model"] == "two"


def test_write_during_scan_is_not_cached(transcript, monkeypatch):
    parse = usage._read_claude_usage_snapshot
    calls = []
    def writing(*args):
        result = parse(*args)
        calls.append(True)
        if len(calls) == 1:
            with transcript.open("a") as handle:
                handle.write(json.dumps(assistant(2, 24)) + "\n")
        return result
    monkeypatch.setattr(usage, "_read_claude_usage_snapshot", writing)
    assert usage.extract_session_usage("session")["total_input_tokens"] == 12
    assert usage.extract_session_usage("session")["total_input_tokens"] == 36
    assert len(calls) == 2
    transcript.unlink()
    assert usage.extract_session_usage("session")["total_input_tokens"] == 0


def test_concurrent_reads_share_parse(transcript, monkeypatch):
    parse = usage._read_claude_usage_snapshot
    calls = []
    entered = threading.Event()
    release = threading.Event()
    def counted(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return parse(*args)
    monkeypatch.setattr(usage, "_read_claude_usage_snapshot", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(usage.extract_session_usage, "session")]
        assert entered.wait(5)
        futures.extend(pool.submit(usage.extract_session_usage, "session") for _ in range(15))
        release.set()
        results = [future.result() for future in futures]
    assert all(row == results[0] for row in results)
    assert len(calls) == 1


def test_oversized_metadata_is_returned_without_retention(transcript, monkeypatch):
    transcript.write_text("")
    cache = ByteBudgetCache(max_bytes=4096, max_entries=32)
    monkeypatch.setattr(usage, "_CLAUDE_USAGE_SNAPSHOTS", cache)
    model = "example-" + "x" * 2048
    assert usage._claude_usage_snapshot(transcript, model)["model"] == model
    assert len(cache) == cache.bytes == 0


def test_compaction_and_message_dedupe_survive_reuse(transcript):
    rows = [assistant(1, 300_000), assistant(1, 300_000),
            {"type": "system", "subtype": "compact_boundary",
             "compactMetadata": {"preTokens": 300_000, "postTokens": 20}},
            assistant(2, 25)]
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
    for _ in range(2):
        result = usage.extract_session_usage("session")
        assert result["total_input_tokens"] == 300_025
        assert result["latest_input_tokens"] == result["peak_input_tokens"] == 25
        assert result["context_limit"] == 1_000_000
        assert result["compact_count"] == 1
        assert len(result["turn_series"]) == 2
