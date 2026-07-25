"""Tests for the read-only GitHub Copilot CLI engine.

Builds a fake ~/.copilot store in a tmp dir (session-store.db SQLite index +
session-state/<uuid>/events.jsonl event logs) and points COPILOT_HOME at it.
All fixture data is obviously fake.
"""
import json
import sqlite3

import server

SID = "11111111-2222-3333-4444-555555555555"


def _write_events_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _fixture_events():
    return [
        {
            "type": "session.start",
            "timestamp": "2026-07-20T10:00:00Z",
            "data": {"context": {"repository": "octo/demo", "branch": "main"}},
        },
        {
            "type": "user.message",
            "timestamp": "2026-07-20T10:00:05Z",
            "data": {"message": "fix the flaky widget test"},
        },
        {
            "type": "tool.execution_start",
            "timestamp": "2026-07-20T10:00:06Z",
            "data": {"toolName": "grep", "arguments": {"description": "search widget"}},
        },
        {
            "type": "tool.execution_complete",
            "timestamp": "2026-07-20T10:00:07Z",
            "data": {"toolName": "grep", "result": "widget.py:12"},
        },
        {
            "type": "assistant.message",
            "timestamp": "2026-07-20T10:00:09Z",
            "data": {"content": "Found the flake in widget.py."},
        },
        # Unknown future event types must be skipped, never crash the parse.
        {"type": "session.checkpoint", "timestamp": "2026-07-20T10:00:10Z", "data": {"id": "cp1"}},
        {"type": "some.future.event", "timestamp": "2026-07-20T10:00:11Z", "data": {"x": 1}},
        {
            "type": "session.shutdown",
            "timestamp": "2026-07-20T10:05:00Z",
            "data": {"modelMetrics": {"fake-model": {"requests": 3}}},
        },
    ]


def _make_copilot_home(tmp_path, with_db=True):
    home = tmp_path / ".copilot"
    events_path = home / "session-state" / SID / "events.jsonl"
    _write_events_jsonl(events_path, _fixture_events())
    if with_db:
        con = sqlite3.connect(str(home / "session-store.db"))
        try:
            con.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, "
                "repository TEXT, branch TEXT, created_at TEXT, updated_at TEXT)"
            )
            con.execute(
                "CREATE TABLE turns (session_id TEXT, role TEXT, content TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SID,
                    "Fix flaky widget test",
                    "octo/demo",
                    "main",
                    "2026-07-20T10:00:00Z",
                    "2026-07-20T10:05:00Z",
                ),
            )
            con.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?)",
                (SID, "user", "fix the flaky widget test", "2026-07-20T10:00:05Z"),
            )
            con.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?)",
                (SID, "assistant", "Found the flake in widget.py.", "2026-07-20T10:00:09Z"),
            )
            con.commit()
        finally:
            con.close()
    return home


def test_finder_returns_copilot_row_from_db(monkeypatch, tmp_path):
    home = _make_copilot_home(tmp_path, with_db=True)
    monkeypatch.setenv("COPILOT_HOME", str(home))

    rows = server.find_copilot_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["source"] == "copilot"
    assert row["engine"] == "copilot"
    assert row["display_name"] == "Fix flaky widget test"
    assert row["ai_title"] == "Fix flaky widget test"
    assert row["first_message"] == "fix the flaky widget test"
    assert row["git_branch"] == "main"
    assert row["modified"] > 0
    assert row["modified_human"]
    assert row["jsonl_path"].endswith("events.jsonl")


def test_finder_falls_back_to_event_logs_without_db(monkeypatch, tmp_path):
    home = _make_copilot_home(tmp_path, with_db=False)
    monkeypatch.setenv("COPILOT_HOME", str(home))

    rows = server.find_copilot_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["engine"] == "copilot"
    # No DB title -> display name falls back to the first user message.
    assert row["display_name"] == "fix the flaky widget test"
    assert row["first_message"] == "fix the flaky widget test"
    assert row["last_assistant_text"] == "Found the flake in widget.py."
    assert row["git_branch"] == "main"
    assert row["folder_label"] == "octo/demo"
    assert row["modified"] > 0


def test_finder_returns_empty_when_store_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "nope"))
    assert server.find_copilot_conversations(repo_only=False, include_old=True) == []


def test_transcript_parser_handles_unknown_event_types(monkeypatch, tmp_path):
    home = _make_copilot_home(tmp_path, with_db=False)
    monkeypatch.setenv("COPILOT_HOME", str(home))

    result = server._parse_copilot_conversation(SID)
    events = result["events"]
    types = [e["type"] for e in events]
    assert types == ["user_text", "assistant", "tool_result", "assistant"]
    assert events[0]["text"] == "fix the flaky widget test"
    tool_block = events[1]["blocks"][0]
    assert tool_block["kind"] == "tool_use"
    assert tool_block["name"] == "grep"
    assert events[2]["text"] == "widget.py:12"
    assert events[2]["is_error"] is False
    assert events[3]["blocks"][0]["text"] == "Found the flake in widget.py."
    assert all(e["ts"] for e in events)
    assert result["last_line"] == 4

    # Incremental tail: only events after line 2.
    tail = server._parse_copilot_conversation(SID, after_line=2)
    assert [e["line"] for e in tail["events"]] == [3, 4]
    assert tail["last_line"] == 4


def test_transcript_parser_missing_session(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / ".copilot"))
    assert server._parse_copilot_conversation(SID) == {"events": [], "last_line": 0}


def test_is_copilot_session(monkeypatch, tmp_path):
    home = _make_copilot_home(tmp_path, with_db=False)
    monkeypatch.setenv("COPILOT_HOME", str(home))

    assert server._is_copilot_session(SID) is True
    assert server._is_copilot_session("deadbeef-0000-0000-0000-000000000000") is False
    assert server._is_copilot_session("") is False
    # Path-traversal-shaped ids must never resolve outside the store.
    assert server._is_copilot_session("../../etc/passwd") is False

    # Engine detection routes a copilot sid to the copilot parser and leaves
    # foreign sids on the claude fallback.
    assert server._detect_session_engine_uncached(SID) == "copilot"
    assert server._detect_session_engine_uncached("deadbeef-0000-0000-0000-000000000000") == "claude"
