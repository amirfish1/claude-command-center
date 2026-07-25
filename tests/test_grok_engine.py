"""Tests for the read-only Grok CLI engine.

Two tools install a `grok` binary under ~/.grok and CCC supports both:
variant A (xAI "Grok Build") stores sessions/<url-encoded-cwd>/<uuid>/ dirs
with summary.json + updates.jsonl/chat_history.jsonl; variant B
(superagent-ai/grok-cli) stores a single grok.db SQLite database. Fixtures
here build a fake GROK_HOME in a tmp dir. All fixture data is obviously fake.
"""
import json
import sqlite3

import server

SID_A = "01999999-aaaa-7bbb-8ccc-dddddddddddd"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"
FAKE_CWD = "/home/tester/fake-proj"
FAKE_CWD_ENCODED = "%2Fhome%2Ftester%2Ffake-proj"


def _write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write((ev if isinstance(ev, str) else json.dumps(ev)) + "\n")


def _fixture_updates():
    return [
        {
            "sessionUpdate": "user_message_chunk",
            "timestamp": "2026-07-20T09:00:05Z",
            "content": {"type": "text", "text": "route the plasma relay"},
        },
        {
            "sessionUpdate": "tool_call",
            "timestamp": "2026-07-20T09:00:06Z",
            "toolCallId": "tc-1",
            "title": "run_shell",
            "rawInput": {"command": "ls fake"},
        },
        {
            "sessionUpdate": "tool_call_update",
            "timestamp": "2026-07-20T09:00:07Z",
            "toolCallId": "tc-1",
            "status": "completed",
            "rawOutput": "fake.txt",
        },
        {
            "sessionUpdate": "agent_message_chunk",
            "timestamp": "2026-07-20T09:00:09Z",
            "content": {"type": "text", "text": "Relay routed."},
        },
        # Unknown future update kinds must be skipped, never crash the parse.
        {"sessionUpdate": "plan", "timestamp": "2026-07-20T09:00:10Z", "entries": []},
        {"sessionUpdate": "some_future_kind", "timestamp": "2026-07-20T09:00:11Z"},
        # Malformed lines must be skipped too.
        "{not json at all",
        json.dumps(["a", "list", "line"]),
    ]


def _make_variant_a_home(tmp_path):
    home = tmp_path / ".grok"
    session_dir = home / "sessions" / FAKE_CWD_ENCODED / SID_A
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(
        json.dumps({
            "title": "Route the plasma relay",
            "createdAt": "2026-07-20T09:00:00Z",
            "updatedAt": "2026-07-20T09:05:00Z",
            "model": "grok-fake-1",
            "messageCount": 4,
        }),
        encoding="utf-8",
    )
    _write_jsonl(session_dir / "updates.jsonl", _fixture_updates())
    return home


def _make_variant_b_home(tmp_path):
    home = tmp_path / ".grok"
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(home / "grok.db"))
    try:
        con.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, scope_key TEXT, "
            "canonical_path TEXT, git_root TEXT, display_name TEXT, last_seen_at TEXT)"
        )
        con.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, workspace_id TEXT, "
            "title TEXT, recap_text TEXT, model TEXT, mode TEXT, "
            "cwd_at_start TEXT, cwd_last TEXT, status TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        con.execute(
            "CREATE TABLE messages (session_id TEXT, seq INTEGER, role TEXT, "
            "message_json TEXT, created_at TEXT)"
        )
        con.execute(
            "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?)",
            ("ws-1", "scope", FAKE_CWD, FAKE_CWD, "fake-proj", "2026-07-20T08:05:00Z"),
        )
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                SID_B, "ws-1", "Calibrate the flux band", None, "grok-fake-2",
                "code", FAKE_CWD, FAKE_CWD, "idle",
                "2026-07-20T08:00:00Z", "2026-07-20T08:06:00Z",
            ),
        )
        con.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (
                SID_B, 1, "user",
                json.dumps({"role": "user", "content": [
                    {"type": "text", "text": "calibrate the flux band"},
                ]}),
                "2026-07-20T08:00:05Z",
            ),
        )
        con.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (
                SID_B, 2, "assistant",
                json.dumps({"role": "assistant", "content": "Band calibrated."}),
                "2026-07-20T08:00:09Z",
            ),
        )
        con.commit()
    finally:
        con.close()
    return home


# --- Variant A (xAI "Grok Build" session dirs) ------------------------------

def test_variant_a_finder_returns_row(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    rows = server.find_grok_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID_A
    assert row["source"] == "grok"
    assert row["engine"] == "grok"
    assert row["display_name"] == "Route the plasma relay"
    assert row["ai_title"] == "Route the plasma relay"
    assert row["first_message"] == "route the plasma relay"
    assert row["last_assistant_text"] == "Relay routed."
    assert row["model"] == "grok-fake-1"
    assert row["session_cwd"] == FAKE_CWD  # URL-decoded bucket name
    assert row["modified"] > 0
    assert row["modified_human"]
    assert row["jsonl_path"].endswith("updates.jsonl")


def test_variant_a_transcript_from_updates_jsonl(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    result = server._parse_grok_conversation(SID_A)
    events = result["events"]
    types = [e["type"] for e in events]
    assert types == ["user_text", "assistant", "tool_result", "assistant"]
    assert events[0]["text"] == "route the plasma relay"
    tool_block = events[1]["blocks"][0]
    assert tool_block["kind"] == "tool_use"
    assert tool_block["name"] == "run_shell"
    assert events[2]["text"] == "fake.txt"
    assert events[2]["is_error"] is False
    assert events[3]["blocks"][0]["text"] == "Relay routed."
    assert all(e["ts"] for e in events)
    assert result["last_line"] == 4

    # Incremental tail: only events after line 2.
    tail = server._parse_grok_conversation(SID_A, after_line=2)
    assert [e["line"] for e in tail["events"]] == [3, 4]
    assert tail["last_line"] == 4


def test_variant_a_falls_back_to_chat_history(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    # Drop updates.jsonl; chat_history.jsonl is the transcript fallback.
    sid_b = "02999999-aaaa-7bbb-8ccc-dddddddddddd"
    session_dir = home / "sessions" / FAKE_CWD_ENCODED / sid_b
    _write_jsonl(session_dir / "chat_history.jsonl", [
        {"role": "user", "content": "ping the fake array"},
        {"role": "assistant", "content": [{"type": "text", "text": "Array ponged."}]},
        "garbage line",
    ])
    monkeypatch.setenv("GROK_HOME", str(home))

    result = server._parse_grok_conversation(sid_b)
    assert [e["type"] for e in result["events"]] == ["user_text", "assistant"]
    assert result["events"][0]["text"] == "ping the fake array"
    assert result["events"][1]["blocks"][0]["text"] == "Array ponged."


def test_variant_a_dot_cwd_file_wins_over_bucket_name(monkeypatch, tmp_path):
    home = tmp_path / ".grok"
    bucket = home / "sessions" / "fake-proj-a1b2c3"
    session_dir = bucket / SID_A
    session_dir.mkdir(parents=True)
    (bucket / ".cwd").write_text(FAKE_CWD + "\n", encoding="utf-8")
    (session_dir / "summary.json").write_text(
        json.dumps({"title": "Slug bucket session"}), encoding="utf-8"
    )
    monkeypatch.setenv("GROK_HOME", str(home))

    rows = server.find_grok_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    assert rows[0]["session_cwd"] == FAKE_CWD


# --- Variant B (superagent-ai/grok-cli grok.db) -----------------------------

def test_variant_b_finder_returns_row(monkeypatch, tmp_path):
    home = _make_variant_b_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    rows = server.find_grok_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID_B
    assert row["source"] == "grok"
    assert row["engine"] == "grok"
    assert row["display_name"] == "Calibrate the flux band"
    assert row["first_message"] == "calibrate the flux band"
    assert row["last_assistant_text"] == "Band calibrated."
    assert row["model"] == "grok-fake-2"
    assert row["session_cwd"] == FAKE_CWD
    assert row["jsonl_path"] == ""


def test_variant_b_transcript_from_messages_table(monkeypatch, tmp_path):
    home = _make_variant_b_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    result = server._parse_grok_conversation(SID_B)
    events = result["events"]
    assert [e["type"] for e in events] == ["user_text", "assistant"]
    assert events[0]["text"] == "calibrate the flux band"
    assert events[1]["blocks"][0]["text"] == "Band calibrated."
    assert result["last_line"] == 2


# --- Both variants / nothing / detection ------------------------------------

def test_both_variants_coexist_and_merge(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    _make_variant_b_home(tmp_path)  # same tmp_path -> same .grok home
    monkeypatch.setenv("GROK_HOME", str(home))

    rows = server.find_grok_conversations(repo_only=False, include_old=True)
    sids = {r["session_id"] for r in rows}
    assert sids == {SID_A, SID_B}
    assert all(r["engine"] == "grok" for r in rows)


def test_finder_returns_empty_when_store_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "nope"))
    assert server.find_grok_conversations(repo_only=False, include_old=True) == []


def test_transcript_parser_missing_session(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / ".grok"))
    assert server._parse_grok_conversation(SID_A) == {"events": [], "last_line": 0}


def test_is_grok_session(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    _make_variant_b_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    assert server._is_grok_session(SID_A) is True   # variant A dir probe
    assert server._is_grok_session(SID_B) is True   # variant B DB probe
    assert server._is_grok_session("deadbeef-0000-0000-0000-000000000000") is False
    assert server._is_grok_session("") is False
    # Path-traversal-shaped ids must never resolve outside the store.
    assert server._is_grok_session("../../etc/passwd") is False

    # Engine detection routes a grok sid to the grok parser and leaves
    # foreign sids on the claude fallback.
    assert server._detect_session_engine_uncached(SID_A) == "grok"
    assert server._detect_session_engine_uncached(SID_B) == "grok"
    assert server._detect_session_engine_uncached("deadbeef-0000-0000-0000-000000000000") == "claude"
