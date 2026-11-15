"""Peer routing resolves cwd without interpreting transcript activity."""

import json

import server
from ccc_server import session_graph


def test_codex_cwd_uses_latest_complete_context_without_activity_parse(tmp_path, monkeypatch):
    initial = tmp_path / "initial"
    latest = tmp_path / "latest"
    initial.mkdir()
    latest.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    events = [
        {"type": "session_meta", "payload": {"cwd": str(initial)}},
        {"type": "response_item", "payload": {"type": "function_call", "cwd": "/ignored"}},
        {"type": "turn_context", "payload": {"cwd": str(latest)}},
        {"type": "turn_context", "payload": {"cwd": ""}},
    ]
    rollout.write_text("\n".join(json.dumps(e) for e in events) + "\n"
                       + json.dumps({"type": "turn_context", "payload": {"cwd": "/partial"}}))
    monkeypatch.setattr(server, "_session_cwd_cache", {})
    monkeypatch.setattr(server, "_session_cwd_override", {})
    monkeypatch.setattr(server, "_hermes_session_row", lambda sid: None)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_codex_thread_row", lambda sid: {"cwd": str(initial)})
    monkeypatch.setattr(server, "_codex_rollout_path_from_row", lambda row: rollout)

    def activity_parse(path):
        raise AssertionError("cwd lookup must not parse tool activity")

    monkeypatch.setattr(server, "_extract_codex_tail_meta", activity_parse)
    assert session_graph.find_session_cwd("thread") == str(latest)
    assert server._session_cwd_cache["thread"] == str(latest)


def test_codex_cwd_retains_override_and_native_fallback(tmp_path, monkeypatch):
    cwd = str(tmp_path)
    monkeypatch.setattr(server, "_session_cwd_cache", {})
    monkeypatch.setattr(server, "_session_cwd_override", {"override": cwd})
    monkeypatch.setattr(server, "_hermes_session_row", lambda sid: None)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_codex_thread_row", lambda sid: {"cwd": cwd})
    monkeypatch.setattr(server, "_codex_rollout_path_from_row", lambda row: tmp_path / "missing.jsonl")
    assert session_graph.find_session_cwd("override") == cwd
    assert session_graph.find_session_cwd("fallback") == cwd
