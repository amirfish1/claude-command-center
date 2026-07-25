"""Tests for the read-only VS Code Copilot Chat engine.

Builds fake VS Code user-data dirs in tmp dirs (workspaceStorage/<hash>/
chatSessions/<sid>.json|jsonl + workspace.json, and globalStorage/
emptyWindowChatSessions/) and points CCC_VSCODE_USER_DIRS at them — that env
var is the documented test injection point. All fixture data is obviously
fake.
"""
import json
import os

import server

SID = "aaaaaaaa-1111-2222-3333-444444444444"
SID2 = "bbbbbbbb-1111-2222-3333-444444444444"
SID3 = "cccccccc-1111-2222-3333-444444444444"

FAKE_FOLDER_URI = "file:///Users/tester/fake%20proj"
FAKE_FOLDER = "/Users/tester/fake proj"

# Epoch-millisecond timestamps (the on-disk format's native unit).
T_CREATE = 1784503200000
T_REQ1 = 1784503205000
T_REQ2 = 1784503210000
T_REQ3 = 1784503215000


def _write_flat_session(user_dir, sid, data, ws_hash="fakehash01",
                        folder_uri=FAKE_FOLDER_URI):
    chat = user_dir / "workspaceStorage" / ws_hash / "chatSessions"
    chat.mkdir(parents=True, exist_ok=True)
    if folder_uri is not None:
        (chat.parent / "workspace.json").write_text(
            json.dumps({"folder": folder_uri}), encoding="utf-8"
        )
    (chat / f"{sid}.json").write_text(json.dumps(data), encoding="utf-8")
    return chat / f"{sid}.json"


def _write_journal(user_dir, sid, records, ws_hash="fakehash01",
                   folder_uri=FAKE_FOLDER_URI, raw_lines=None):
    chat = user_dir / "workspaceStorage" / ws_hash / "chatSessions"
    chat.mkdir(parents=True, exist_ok=True)
    if folder_uri is not None:
        (chat.parent / "workspace.json").write_text(
            json.dumps({"folder": folder_uri}), encoding="utf-8"
        )
    path = chat / f"{sid}.jsonl"
    if raw_lines is not None:
        path.write_bytes(raw_lines)
    else:
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write((rec if isinstance(rec, str) else json.dumps(rec)) + "\n")
    return path


def _flat_session_data():
    return {
        "version": 3,
        "sessionId": SID,
        "creationDate": T_CREATE,
        "requests": [
            {
                "requestId": "req-1",
                "message": {"text": "rename the fake comet module"},
                "response": [{"value": "Sure — renaming the comet module now."}],
                "result": {
                    "metadata": {
                        "toolCallRounds": [
                            {"toolCalls": [{"name": "replace_string_in_file"}]}
                        ]
                    }
                },
                "timestamp": T_REQ1,
            },
            {
                "requestId": "req-2",
                "message": {"parts": [{"text": "now update the fake docs"}]},
                "response": [{"kind": "markdownContent", "value": "Docs updated."}],
                "timestamp": T_REQ2,
            },
        ],
    }


def _journal_records():
    return [
        # Base snapshot record.
        {
            "kind": "snapshot",
            "requests": [
                {
                    "requestId": "req-1",
                    "message": {"text": "plot the fake orbit"},
                    "response": [{"value": "Plotting the orbit."}],
                    "timestamp": T_REQ1,
                }
            ],
        },
        # Two append ops carrying request entries.
        {
            "kind": "append",
            "request": {
                "requestId": "req-2",
                "message": {"text": "add fake moons"},
                "response": [
                    {"kind": "toolInvocation", "toolName": "run_in_terminal"}
                ],
                "result": {
                    "metadata": {
                        "toolCallRounds": [
                            {"toolCalls": [{"name": "run_in_terminal"}]}
                        ]
                    }
                },
                "timestamp": T_REQ2,
            },
        },
        {
            "kind": "append",
            "request": {
                "requestId": "req-3",
                "message": {"text": "color them fake-blue"},
                "response": [{"value": "Moons colored fake-blue."}],
                "timestamp": T_REQ3,
            },
        },
    ]


# --- (a) flat .json session -------------------------------------------------

def test_flat_json_session_row(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_flat_session(user_dir, SID, _flat_session_data())
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["source"] == "copilotchat"
    assert row["engine"] == "copilotchat"
    assert row["display_name"] == "rename the fake comet module"
    assert row["first_message"] == "rename the fake comet module"
    assert row["last_assistant_text"] == "Docs updated."
    # session_cwd decoded from the workspace.json file:// URI.
    assert row["session_cwd"] == FAKE_FOLDER
    # Epoch-ms timestamps normalized to seconds.
    assert abs(row["modified"] - (T_REQ2 / 1000.0)) < 1
    assert row["modified_human"]
    assert row["jsonl_path"].endswith(f"{SID}.json")


def test_flat_json_transcript(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_flat_session(user_dir, SID, _flat_session_data())
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    result = server._parse_copilotchat_conversation(SID)
    events = result["events"]
    assert [e["type"] for e in events] == [
        "user_text", "assistant", "user_text", "assistant"
    ]
    assert events[0]["text"] == "rename the fake comet module"
    blocks1 = events[1]["blocks"]
    assert blocks1[0] == {"kind": "text", "text": "Sure — renaming the comet module now."}
    assert blocks1[1]["kind"] == "tool_use"
    assert blocks1[1]["name"] == "replace_string_in_file"
    assert events[2]["text"] == "now update the fake docs"
    assert events[3]["blocks"] == [{"kind": "text", "text": "Docs updated."}]
    assert all(e["ts"] for e in events)
    assert result["last_line"] == 4

    # Incremental tail: only events after line 2.
    tail = server._parse_copilotchat_conversation(SID, after_line=2)
    assert [e["line"] for e in tail["events"]] == [3, 4]
    assert tail["last_line"] == 4


# --- (b) .jsonl journal replay ----------------------------------------------

def test_jsonl_journal_row_and_replayed_transcript(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_journal(user_dir, SID, _journal_records())
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["engine"] == "copilotchat"
    assert row["display_name"] == "plot the fake orbit"
    assert row["last_assistant_text"] == "Moons colored fake-blue."
    assert row["session_cwd"] == FAKE_FOLDER
    assert abs(row["modified"] - (T_REQ3 / 1000.0)) < 1
    assert row["jsonl_path"].endswith(f"{SID}.jsonl")

    result = server._parse_copilotchat_conversation(SID)
    events = result["events"]
    assert [e["type"] for e in events] == [
        "user_text", "assistant", "user_text", "assistant",
        "user_text", "assistant",
    ]
    assert events[0]["text"] == "plot the fake orbit"
    assert events[1]["blocks"] == [{"kind": "text", "text": "Plotting the orbit."}]
    assert events[2]["text"] == "add fake moons"
    # The tool appears both as a response part and in toolCallRounds — it is
    # rendered once.
    tool_blocks = [b for b in events[3]["blocks"] if b["kind"] == "tool_use"]
    assert [b["name"] for b in tool_blocks] == ["run_in_terminal"]
    assert events[4]["text"] == "color them fake-blue"
    assert events[5]["blocks"] == [{"kind": "text", "text": "Moons colored fake-blue."}]
    assert result["last_line"] == 6


# --- (c) .jsonl wins when both formats exist ---------------------------------

def test_jsonl_wins_over_flat_json(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_flat_session(user_dir, SID, {
        "creationDate": T_CREATE,
        "requests": [{
            "message": {"text": "from the flat json"},
            "response": [{"value": "flat reply"}],
            "timestamp": T_REQ1,
        }],
    })
    _write_journal(user_dir, SID, [{
        "kind": "snapshot",
        "requests": [{
            "message": {"text": "from the journal"},
            "response": [{"value": "journal reply"}],
            "timestamp": T_REQ2,
        }],
    }])
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["jsonl_path"].endswith(f"{SID}.jsonl")
    assert row["first_message"] == "from the journal"

    result = server._parse_copilotchat_conversation(SID)
    assert result["events"][0]["text"] == "from the journal"


# --- (d) corrupt inputs never crash ------------------------------------------

def test_journal_bom_and_truncated_last_line(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    good = json.dumps({
        "kind": "snapshot",
        "requests": [{
            "message": {"text": "survives the fake corruption"},
            "response": [{"value": "still readable"}],
            "timestamp": T_REQ1,
        }],
    })
    raw = b"\xef\xbb\xbf" + good.encode() + b"\n" + b'{"kind": "append", "requ'
    _write_journal(user_dir, SID, None, raw_lines=raw)
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    result = server._parse_copilotchat_conversation(SID)
    assert [e["type"] for e in result["events"]] == ["user_text", "assistant"]
    assert result["events"][0]["text"] == "survives the fake corruption"

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    assert rows[0]["display_name"] == "survives the fake corruption"


def test_empty_stub_overwrite_still_lists_row(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_flat_session(user_dir, SID, {})  # empty-stub overwrite
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["display_name"] == "Copilot Chat session"
    assert row["modified"] > 0  # falls back to the file mtime

    result = server._parse_copilotchat_conversation(SID)
    assert result == {"events": [], "last_line": 0}


def test_unknown_op_shapes_are_skipped(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_journal(user_dir, SID, [
        {"kind": "someFutureOp", "weird": [1, 2, 3]},
        {"kind": 17, "payload": {"nested": {"nothing": "request-shaped"}}},
        ["a", "list", "line"],
        "{not json at all",
        {
            "kind": "snapshot",
            "requests": [{
                "message": {"text": "only the real turn"},
                "response": [{"value": "real reply"}],
                "timestamp": T_REQ1,
            }],
        },
        {"kind": "anotherFutureOp", "op": {"unrelated": True}},
    ])
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    result = server._parse_copilotchat_conversation(SID)
    assert [e["type"] for e in result["events"]] == ["user_text", "assistant"]
    assert result["events"][0]["text"] == "only the real turn"


# --- (e) empty-window sessions ------------------------------------------------

def test_empty_window_session_row(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    empty = user_dir / "globalStorage" / "emptyWindowChatSessions"
    empty.mkdir(parents=True)
    (empty / f"{SID}.json").write_text(json.dumps({
        "creationDate": T_CREATE,
        "requests": [{
            "message": {"text": "quick fake question with no folder"},
            "response": [{"value": "fake answer"}],
            "timestamp": T_REQ1,
        }],
    }), encoding="utf-8")
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == SID
    assert row["session_cwd"] == ""
    assert row["folder_label"] == "Copilot Chat"
    assert row["display_name"] == "quick fake question with no folder"


# --- (f) multiple apps scanned -------------------------------------------------

def test_two_apps_both_scanned(monkeypatch, tmp_path):
    user_code = tmp_path / "Code" / "User"
    user_insiders = tmp_path / "Code - Insiders" / "User"
    _write_flat_session(user_code, SID, {
        "creationDate": T_CREATE,
        "requests": [{
            "message": {"text": "stable fake session"},
            "response": [{"value": "reply"}],
            "timestamp": T_REQ1,
        }],
    })
    _write_flat_session(user_insiders, SID2, {
        "creationDate": T_CREATE,
        "requests": [{
            "message": {"text": "insiders fake session"},
            "response": [{"value": "reply"}],
            "timestamp": T_REQ2,
        }],
    })
    monkeypatch.setenv(
        "CCC_VSCODE_USER_DIRS", os.pathsep.join([str(user_code), str(user_insiders)])
    )

    rows = server.find_copilotchat_conversations(repo_only=False, include_old=True)
    assert {r["session_id"] for r in rows} == {SID, SID2}
    assert all(r["engine"] == "copilotchat" for r in rows)


# --- (g) nothing on disk --------------------------------------------------------

def test_finder_returns_empty_when_nothing_on_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(tmp_path / "nope"))
    assert server.find_copilotchat_conversations(repo_only=False, include_old=True) == []
    assert server._parse_copilotchat_conversation(SID) == {"events": [], "last_line": 0}


# --- (h) cheap probe + engine detection -----------------------------------------

def test_is_copilotchat_session(monkeypatch, tmp_path):
    user_dir = tmp_path / "Code" / "User"
    _write_flat_session(user_dir, SID, _flat_session_data())
    _write_journal(user_dir, SID3, _journal_records())
    monkeypatch.setenv("CCC_VSCODE_USER_DIRS", str(user_dir))

    assert server._is_copilotchat_session(SID) is True    # flat .json
    assert server._is_copilotchat_session(SID3) is True   # .jsonl journal
    assert server._is_copilotchat_session("deadbeef-0000-0000-0000-000000000000") is False
    assert server._is_copilotchat_session("") is False
    assert server._is_copilotchat_session("not-a-uuid") is False
    # Path-traversal-shaped ids must never resolve outside the store.
    assert server._is_copilotchat_session("../../etc/passwd") is False

    # Engine detection routes a copilotchat sid to its parser and leaves
    # foreign sids on the claude fallback.
    assert server._detect_session_engine_uncached(SID) == "copilotchat"
    assert server._detect_session_engine_uncached(SID3) == "copilotchat"
    assert server._detect_session_engine_uncached("deadbeef-0000-0000-0000-000000000000") == "claude"
