"""Timeline polls preserve parser output without rescanning unchanged files."""
import concurrent.futures
import io
import json
import os

import pytest

from ccc_server import timeline_cache


@pytest.fixture(autouse=True)
def empty_cache():
    timeline_cache._CACHE.clear()
    yield
    timeline_cache._CACHE.clear()


def test_reuses_unchanged_file_and_detaches_nested_results(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("first\n")
    calls = []

    def read(stream):
        calls.append(stream.read())
        return {"events": [{"subject": calls[-1]}], "total_turns": 1}

    first = timeline_cache.read_timeline(source, read, namespace="test")
    first["events"][0]["subject"] = "edited by caller"
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: timeline_cache.read_timeline(
            source, read, namespace="test"), range(16)))
    assert calls == ["first\n"]
    assert all(result["events"][0]["subject"] == "first\n" for result in results)


@pytest.mark.parametrize("change", ["append", "truncate", "rewrite", "replace", "delete"])
def test_source_changes_invalidate_even_preserved_mtime(tmp_path, change):
    source = tmp_path / "session.jsonl"
    source.write_text("first\n")
    calls = []

    def read(stream):
        calls.append(stream.read())
        return {"events": [], "total_turns": len(calls[-1])}

    timeline_cache.read_timeline(source, read, namespace="test")
    before = source.stat()
    if change == "delete":
        source.unlink()
    else:
        if change == "replace":
            replacement = tmp_path / "new.jsonl"
            replacement.write_text("other\n")
            replacement.replace(source)
        else:
            source.write_text({"append": "first\nsecond\n", "truncate": "", "rewrite": "other\n"}[change])
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    result = timeline_cache.read_timeline(source, read, namespace="test")
    assert result["total_turns"] == (len(source.read_text()) if source.exists() else 0)
    assert len(calls) == (1 if change == "delete" else 2)


def test_changing_or_failed_read_is_not_cached(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("first\n")
    calls = []

    def read(stream):
        calls.append(stream.read())
        source.write_text("next\n")
        return {"events": [], "total_turns": 1}

    timeline_cache.read_timeline(source, read, namespace="test")
    assert len(timeline_cache._CACHE) == 0

    def fail(stream):
        raise OSError("temporary read failure")

    assert timeline_cache.read_timeline(source, fail, namespace="test")["total_turns"] == 0
    assert len(timeline_cache._CACHE) == 0
    timeline_cache.read_timeline(source, read, namespace="test")
    assert len(calls) == 2


def test_concurrent_cold_reads_are_single_flight(tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text("first\n")
    calls = []

    def read(stream):
        calls.append(stream.read())
        return {"events": [], "total_turns": 1}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: timeline_cache.read_timeline(
            source, read, namespace="test"), range(16)))
    assert calls == ["first\n"]
    assert all(result["total_turns"] == 1 for result in results)


def test_oversized_results_still_work_without_retaining_them(tmp_path, monkeypatch):
    from ccc_server.bounded_cache import ByteBudgetCache

    monkeypatch.setattr(timeline_cache, "_CACHE", ByteBudgetCache(max_bytes=16, max_entries=1))
    source = tmp_path / "session.jsonl"
    source.write_text("first\n")
    expected = {"events": [{"subject": "large" * 20}], "total_turns": 1}
    assert timeline_cache.read_timeline(source, lambda _: expected, namespace="test") == expected
    assert len(timeline_cache._CACHE) == 0


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_real_timeline_parser_retains_results_and_appends(tmp_path, monkeypatch, engine):
    import server
    from ccc_server import morning_launch, queue_events

    source = tmp_path / "session.jsonl"
    if engine == "codex":
        records = [
            {"type": "event_msg", "payload": {"type": "agent_message"}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
             "call_id": "call-1", "arguments": json.dumps({"cmd": 'git commit -m "test"'})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1",
             "output": "[main abc1234] test"}},
        ]
        parser = queue_events._read_codex_timeline
        monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda _: source)
        extract = lambda: server._extract_codex_timeline("example")
    else:
        project = tmp_path / "project"
        project.mkdir()
        source = project / "example.jsonl"
        records = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
             "id": "call-1", "input": {"command": 'git commit -m "test"'}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1",
             "content": "[main abc1234] test"}]}},
        ]
        parser = morning_launch._read_claude_timeline
        monkeypatch.setattr(server, "PROJECTS_ROOT", tmp_path)
        for name in ("codex", "gemini", "cursor", "antigravity", "hermes", "devin_cli"):
            monkeypatch.setattr(server, f"_is_{name}_session", lambda _: False)
        extract = lambda: server.extract_session_timeline("example")
    text = "\n".join(json.dumps(row) for row in records) + "\n"
    source.write_text(text)
    expected = parser(io.StringIO(text))
    assert expected["events"][0]["sha"] == "abc1234"
    assert expected["events"][0]["success"] is True
    assert extract() == expected
    assert extract() == expected
    with source.open("a") as stream:
        stream.write(text)
    assert extract() == parser(io.StringIO(text + text))
