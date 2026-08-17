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
    assert tool_block["detail"] == "ls fake"
    assert tool_block["command"] == "ls fake"
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


def _wrap_rpc(update, timestamp="2026-07-20T09:00:05Z"):
    """Wrap an update in the newer Grok Build JSON-RPC envelope."""
    return {
        "timestamp": timestamp,
        "method": "session/update",
        "params": {"sessionId": SID_A, "update": update},
    }


def _fixture_updates_rpc():
    """Same logical events as _fixture_updates but wrapped in JSON-RPC and
    enriched with agent_thought_chunk, hook_execution, image_dropped, and
    retry_state updates."""
    base = _fixture_updates()
    return [
        _wrap_rpc({
            "sessionUpdate": "hook_execution",
            "event_name": "session_start",
            "runs": [
                {"name": "global/orca-status:session_start[0].hooks[0]", "status": {"status": "success", "elapsed_ms": 11}},
                {"name": "global/settings:session_start[0].hooks[0]", "status": {"status": "failed", "error": "not found", "elapsed_ms": 0}},
            ],
        }, timestamp="2026-07-20T09:00:04Z"),
        _wrap_rpc(base[0], timestamp="2026-07-20T09:00:05Z"),
        _wrap_rpc({
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "The user wants to route the plasma relay."},
        }, timestamp="2026-07-20T09:00:05.500Z"),
        _wrap_rpc(base[1], timestamp="2026-07-20T09:00:06Z"),
        _wrap_rpc({
            "sessionUpdate": "hook_execution",
            "event_name": "pre_tool_use",
            "tool_name": "run_shell",
            "runs": [
                {"name": "global/orca-status:pre_tool_use[0].hooks[0]", "status": {"status": "success", "elapsed_ms": 5}},
            ],
        }, timestamp="2026-07-20T09:00:06.100Z"),
        _wrap_rpc(base[2], timestamp="2026-07-20T09:00:07Z"),
        _wrap_rpc({
            "sessionUpdate": "image_dropped",
            "notes": ["This request failed over its images; 1 image(s) were left out of the retry."],
        }, timestamp="2026-07-20T09:00:07.500Z"),
        _wrap_rpc({
            "sessionUpdate": "retry_state",
            "type": "retrying",
            "attempt": 1,
            "max_retries": 5,
            "reason": "request error: connection refused",
        }, timestamp="2026-07-20T09:00:07.600Z"),
        _wrap_rpc(base[3], timestamp="2026-07-20T09:00:09Z"),
    ]


def test_variant_a_transcript_from_updates_jsonl_rpc_envelope(monkeypatch, tmp_path):
    """Newer Grok Build wraps updates in JSON-RPC; the parser must unwrap and
    surface hook_execution, agent_thought_chunk, image_dropped, and retry_state
    events."""
    home = tmp_path / ".grok"
    session_dir = home / "sessions" / FAKE_CWD_ENCODED / SID_A
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(json.dumps({"title": "RPC session"}), encoding="utf-8")
    _write_jsonl(session_dir / "updates.jsonl", _fixture_updates_rpc())
    monkeypatch.setenv("GROK_HOME", str(home))

    result = server._parse_grok_conversation(SID_A)
    events = result["events"]
    types = [e["type"] for e in events]
    assert types == [
        "system",       # session_start hook summary
        "user_text",
        "assistant",    # thinking
        "assistant",    # tool_call run_shell
        "system",       # pre_tool_use hook summary
        "tool_result",
        "system",       # image_dropped
        "system",       # retry_state
        "assistant",    # final assistant text
    ]
    assert events[0]["subtype"] == "grok_hook_execution"
    assert "session_start" in events[0]["text"]
    assert "1 ok, 1 failed" in events[0]["text"]
    assert events[2]["blocks"][0]["kind"] == "thinking"
    assert "plasma relay" in events[2]["blocks"][0]["text"]
    assert events[3]["blocks"][0]["name"] == "run_shell"
    assert events[4]["subtype"] == "grok_hook_execution"
    assert "pre_tool_use" in events[4]["text"]
    assert events[5]["text"] == "fake.txt"
    assert events[6]["subtype"] == "grok_note"
    assert "image" in events[6]["text"]
    assert events[7]["subtype"] == "grok_retry"
    assert "Retrying (1/5)" in events[7]["text"]
    assert all(e["ts"] for e in events)


def test_variant_a_chat_history_with_type_and_tool_calls(monkeypatch, tmp_path):
    """Real Grok Build chat_history.jsonl uses `type` (not `role`) and places
    tool_calls on assistant turns; tool_result turns carry `content`."""
    home = tmp_path / ".grok"
    session_dir = home / "sessions" / FAKE_CWD_ENCODED / SID_A
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(json.dumps({"title": "CH session"}), encoding="utf-8")
    _write_jsonl(session_dir / "chat_history.jsonl", [
        {"type": "user", "content": [{"type": "text", "text": "ping the fake array"}]},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Need to ping the array."}],
            "encrypted_content": "abc123",
        },
        {
            "type": "assistant",
            "content": "I will ping it.",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "name": "run_shell",
                    "arguments": {"command": "ping fake-array"},
                },
            ],
        },
        {"type": "tool_result", "tool_call_id": "tc-1", "content": "pong"},
        {"type": "assistant", "content": "Array ponged."},
    ])
    # No updates.jsonl, so chat_history is the fallback.
    monkeypatch.setenv("GROK_HOME", str(home))

    result = server._parse_grok_conversation(SID_A)
    events = result["events"]
    types = [e["type"] for e in events]
    assert types == ["user_text", "assistant", "assistant", "tool_result", "assistant"]
    assert events[0]["text"] == "ping the fake array"
    assert events[1]["blocks"][0]["kind"] == "thinking"
    assert events[2]["blocks"][0]["kind"] == "text"
    assert events[2]["blocks"][1]["kind"] == "tool_use"
    assert events[2]["blocks"][1]["name"] == "run_shell"
    assert events[2]["blocks"][1]["detail"] == "ping fake-array"
    assert events[3]["text"] == "pong"
    assert events[3]["tool_use_id"] == "tc-1"
    assert events[4]["blocks"][0]["text"] == "Array ponged."


# --- Codex-style single-writer coordination (terminal vs CCC ACP) ---------


def test_command_targets_engine_session_grok_resume(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))

    assert server._command_targets_engine_session(
        f"grok --resume {SID_A}", SID_A, "grok"
    ) is True
    assert server._command_targets_engine_session(
        f"grok --resume {SID_A} --dangerously-skip-permissions", SID_A, "grok"
    ) is True
    assert server._command_targets_engine_session(
        f"grok chat --resume {SID_A}", SID_A, "grok"
    ) is True
    assert server._command_targets_engine_session(
        "grok agent stdio", SID_A, "grok"
    ) is False
    assert server._command_targets_engine_session(
        f"grok --resume {SID_A}", SID_B, "grok"
    ) is False


def test_grok_external_writer_active_false_when_no_tui(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))
    monkeypatch.setattr(server, "_raw_engine_process_commands", lambda engine: iter([]))
    server._grok_external_writer_cache.clear()
    assert server._grok_external_writer_active(SID_A) is False


def test_grok_conversation_source_prefers_disk_when_acp_not_loaded(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))
    # Ensure no ACP session is loaded for this sid.
    server._ACP_CONNS.pop("grok", None)
    server._ACP_SESSION_STATE.setdefault("grok", {}).pop(SID_A, None)

    src = server._grok_conversation_source(SID_A)
    assert src.name == "updates.jsonl"


def test_grok_conversation_source_prefers_acp_when_loaded(monkeypatch, tmp_path):
    home = _make_variant_a_home(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(home))
    server._grok_external_writer_cache.clear()

    # Fake an alive ACP connection with the session loaded.
    class _FakeTransport:
        def alive(self):
            return True

    conn = {"transport": _FakeTransport()}
    server._ACP_CONNS["grok"] = conn
    server._ACP_SESSION_STATE.setdefault("grok", {})[SID_A] = {
        "loaded_conn": id(conn),
    }
    try:
        src = server._grok_conversation_source(SID_A)
        assert src == server._acp_transcript_path("grok", SID_A)
    finally:
        server._ACP_CONNS.pop("grok", None)
        server._ACP_SESSION_STATE.setdefault("grok", {}).pop(SID_A, None)


def test_acp_transcript_last_line_missing_returns_zero():
    assert server._acp_transcript_last_line("grok", "does-not-exist-0000") == 0
