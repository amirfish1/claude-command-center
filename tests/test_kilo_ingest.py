"""Unit tests for `ccc_server/kilo.py` (Kilo Code session ingestion).

Kilo sessions are read out of a live, WAL-mode SQLite DB that CCC never
writes. The board's Kilo cards and their transcripts are built entirely from
the queries in this module, so a schema-shape mistake shows up as an empty or
mislabelled card rather than an exception. The module was at ~10% coverage.

Everything here runs against a temporary SQLite file shaped like Kilo's
`session` / `message` / `part` tables, with `_kilo_db_path` pointed at it.
Names that still live in server.py are reached through `ccc_server.core`, a
proxy over `sys.modules["server"]`, so the board-level test installs a stub
module there instead of importing the real server.
"""
import json
import os
import sqlite3
import sys
import time
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import kilo  # noqa: E402


def _make_db(path):
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY, directory TEXT, title TEXT, model TEXT,
            agent TEXT, time_created INTEGER, time_updated INTEGER,
            time_archived INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
        );
        """
    )
    con.commit()
    return con


def _add_session(con, sid, directory="/repo", title="", model=None,
                 created=1_000_000, updated=2_000_000, archived=None):
    con.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?)",
        (sid, directory, title, model, "build", created, updated, archived),
    )
    con.commit()


def _add_message(con, mid, sid, role, parts, created=0, extra=None):
    data = {"role": role, "time": {"created": created}}
    data.update(extra or {})
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?)", (mid, sid, json.dumps(data), created))
    for i, part in enumerate(parts):
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?)",
            (f"{mid}-p{i}", mid, json.dumps(part), created + i),
        )
    con.commit()


@pytest.fixture()
def kilo_db(tmp_path, monkeypatch):
    db_path = tmp_path / "kilo.db"
    con = _make_db(db_path)
    monkeypatch.setattr(kilo, "_kilo_db_path", lambda: db_path)
    yield con
    con.close()


# --------------------------------------------------------------------------
# connection helpers
# --------------------------------------------------------------------------

def test_db_path_is_none_when_the_store_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(kilo.Path, "home", staticmethod(lambda: tmp_path))
    assert kilo._kilo_db_path() is None
    assert kilo._kilo_connect() is None


def test_db_path_resolves_under_the_kilo_share_dir(tmp_path, monkeypatch):
    db = tmp_path / ".local" / "share" / "kilo" / "kilo.db"
    db.parent.mkdir(parents=True)
    db.touch()
    monkeypatch.setattr(kilo.Path, "home", staticmethod(lambda: tmp_path))
    assert kilo._kilo_db_path() == db


def test_connection_is_read_only(kilo_db):
    con = kilo._kilo_connect()
    assert con is not None
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO session (id) VALUES ('nope')")
    con.close()


def test_connect_returns_none_when_the_store_cannot_be_opened(tmp_path, monkeypatch):
    unopenable = tmp_path / "kilo.db"
    unopenable.mkdir()  # a directory where sqlite expects a file
    monkeypatch.setattr(kilo, "_kilo_db_path", lambda: unopenable)
    assert kilo._kilo_connect() is None


# --------------------------------------------------------------------------
# _kilo_model_str
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, ""),
    ("", ""),
    (json.dumps({"providerID": "anthropic", "modelID": "claude-sonnet"}),
     "anthropic/claude-sonnet"),
    (json.dumps({"providerID": "anthropic", "modelID": "openai/gpt"}), "openai/gpt"),
    (json.dumps({"modelID": "claude-sonnet"}), "claude-sonnet"),
    (json.dumps({"id": "gpt-5", "providerID": "openai"}), "openai/gpt-5"),
    ({"providerID": "openai", "modelID": "gpt-5"}, "openai/gpt-5"),
    (json.dumps({"providerID": "openai"}), ""),
    ("plain-model-name", "plain-model-name"),
    (json.dumps(["a"]), '["a"]'),
])
def test_model_string_rendering(raw, expected):
    assert kilo._kilo_model_str(raw) == expected


# --------------------------------------------------------------------------
# _kilo_fetch_sessions
# --------------------------------------------------------------------------

def test_fetch_sessions_returns_newest_first_with_seconds_timestamps(kilo_db):
    _add_session(kilo_db, "ses_old", title=" older ", updated=1_000)
    _add_session(kilo_db, "ses_new", title="newer", updated=9_000,
                 model=json.dumps({"providerID": "anthropic", "modelID": "sonnet"}),
                 archived=123)
    con = kilo._kilo_connect()
    rows = kilo._kilo_fetch_sessions(con)
    con.close()

    assert [r["id"] for r in rows] == ["ses_new", "ses_old"]
    assert rows[0]["title"] == "newer"
    assert rows[0]["model"] == "anthropic/sonnet"
    assert rows[0]["updated"] == 9.0 and rows[0]["created"] == 1000.0
    assert rows[0]["archived"] is True
    assert rows[1]["title"] == "older", "titles are stripped"
    assert rows[1]["archived"] is False


def test_fetch_sessions_honours_the_limit(kilo_db):
    for i in range(5):
        _add_session(kilo_db, f"ses_{i}", updated=i)
    con = kilo._kilo_connect()
    assert len(kilo._kilo_fetch_sessions(con, limit=2)) == 2
    assert len(kilo._kilo_fetch_sessions(con, limit=0)) == 5
    con.close()


def test_fetch_sessions_tolerates_a_missing_session_table(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setattr(kilo, "_kilo_db_path", lambda: db)
    con = kilo._kilo_connect()
    assert kilo._kilo_fetch_sessions(con) == []
    con.close()


# --------------------------------------------------------------------------
# message text extraction
# --------------------------------------------------------------------------

def test_first_user_text_skips_blank_and_non_text_parts(kilo_db):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m1", "ses_1", "user", [
        {"type": "file", "text": "attachment"},
        {"type": "text", "text": "   "},
        {"type": "text", "text": "  the real prompt  "},
    ], created=10)
    _add_message(kilo_db, "m2", "ses_1", "user", [{"type": "text", "text": "later"}],
                 created=20)
    con = kilo._kilo_connect()
    assert kilo._kilo_first_user_text(con, "ses_1") == "the real prompt"
    assert kilo._kilo_first_user_text(con, "ses_missing") == ""
    con.close()


def test_last_assistant_text_joins_parts_of_the_newest_answering_message(kilo_db):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m1", "ses_1", "assistant",
                 [{"type": "text", "text": "old answer"}], created=10)
    _add_message(kilo_db, "m2", "ses_1", "assistant", [
        {"type": "tool", "state": {}},
        {"type": "text", "text": "part one"},
        {"type": "text", "text": "part two"},
    ], created=20)
    _add_message(kilo_db, "m3", "ses_1", "assistant", [{"type": "tool", "state": {}}],
                 created=30)
    con = kilo._kilo_connect()
    assert kilo._kilo_last_assistant_text(con, "ses_1") == "part one\npart two"
    assert kilo._kilo_last_assistant_text(con, "ses_none") == ""
    con.close()


def test_text_extraction_survives_corrupt_part_json(kilo_db):
    _add_session(kilo_db, "ses_1")
    kilo_db.execute("INSERT INTO message VALUES (?,?,?,?)",
                    ("m1", "ses_1", json.dumps({"role": "user"}), 1))
    kilo_db.execute("INSERT INTO part VALUES (?,?,?,?)",
                    ("p1", "m1", '{"type": "text", broken', 1))
    kilo_db.commit()
    con = kilo._kilo_connect()
    assert kilo._kilo_first_user_text(con, "ses_1") == ""
    con.close()


# --------------------------------------------------------------------------
# _parse_kilo_conversation
# --------------------------------------------------------------------------

def test_parse_conversation_builds_user_assistant_and_tool_result_events(kilo_db):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m1", "ses_1", "user", [
        {"type": "text", "text": "please build it"},
    ], created=1_700_000_000_000)
    _add_message(kilo_db, "m2", "ses_1", "assistant", [
        {"type": "reasoning", "text": "thinking"},
        {"type": "text", "text": "on it"},
        {"type": "tool", "tool": "bash", "callID": "call-1",
         "state": {"input": {"command": "ls"}, "output": "a\nb", "status": "completed"}},
    ], created=1_700_000_001_000)

    result = kilo._parse_kilo_conversation("ses_1")
    events = result["events"]
    assert result["last_line"] == 3
    assert [e["type"] for e in events] == ["user_text", "assistant", "tool_result"]
    assert events[0]["text"] == "please build it"
    assert events[0]["ts"] == "2023-11-14T22:13:20Z"
    assert [b["kind"] for b in events[1]["blocks"]] == ["text", "text", "tool_use"]
    tool_block = events[1]["blocks"][2]
    assert tool_block["name"] == "bash"
    assert tool_block["detail"] == "ls" and tool_block["command"] == "ls"
    assert events[2]["text"] == "a\nb"
    assert events[2]["tool_use_id"] == "call-1" and events[2]["is_error"] is False


def test_parse_conversation_marks_failed_tool_calls_as_errors(kilo_db):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m1", "ses_1", "assistant", [
        {"type": "tool", "tool": "bash", "callID": "c",
         "state": {"input": {}, "output": "boom", "status": "error"}},
    ])
    events = kilo._parse_kilo_conversation("ses_1")["events"]
    assert events[-1]["is_error"] is True


@pytest.mark.parametrize("state,expected_detail", [
    ({"input": {"command": "npm test"}}, "npm test"),
    ({"input": {"description": "run the suite"}}, "run the suite"),
    ({"input": {}, "title": "Read file"}, "Read file"),
    ({"input": {"path": "/tmp/x"}}, '{"path": "/tmp/x"}'),
    ({}, ""),
])
def test_tool_detail_falls_back_through_command_description_title_then_json(
        kilo_db, state, expected_detail):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m1", "ses_1", "assistant",
                 [{"type": "tool", "tool": "t", "state": state}])
    blocks = kilo._parse_kilo_conversation("ses_1")["events"][0]["blocks"]
    assert blocks[0]["detail"] == expected_detail


def test_parse_conversation_drops_empty_turns_and_pages_from_after_line(kilo_db):
    _add_session(kilo_db, "ses_1")
    _add_message(kilo_db, "m0", "ses_1", "user", [{"type": "file"}], created=1)
    _add_message(kilo_db, "m1", "ses_1", "user", [{"type": "text", "text": "one"}], created=2)
    _add_message(kilo_db, "m2", "ses_1", "assistant", [{"type": "text", "text": "  "}], created=3)
    _add_message(kilo_db, "m3", "ses_1", "user", [{"type": "text", "text": "two"}], created=4)

    full = kilo._parse_kilo_conversation("ses_1")
    assert [e["text"] for e in full["events"]] == ["one", "two"]
    assert full["last_line"] == 2

    tail = kilo._parse_kilo_conversation("ses_1", after_line=1)
    assert [e["text"] for e in tail["events"]] == ["two"]
    assert tail["last_line"] == 2, "last_line stays absolute so the client can resume"


def test_parse_conversation_without_a_reachable_db(monkeypatch):
    monkeypatch.setattr(kilo, "_kilo_db_path", lambda: None)
    assert kilo._parse_kilo_conversation("ses_1") == {"events": [], "last_line": 0}


# --------------------------------------------------------------------------
# find_kilo_conversations
# --------------------------------------------------------------------------

def _stub_core(monkeypatch, **overrides):
    """Install a stub `server` module for ccc_server.core to proxy onto."""
    stub = types.SimpleNamespace(
        resolve_repo_path=lambda p: p or "/repo",
        _load_repo_pins=dict,
        _load_session_name_overrides=dict,
        _load_conversation_lifecycle_sets=lambda: (set(), set()),
        _load_verified_conversations=list,
        _load_last_interactions=dict,
        _session_scan_cutoff_ts=lambda include_old: 0,
        _session_scan_file_limit=lambda include_old: 0,
        _codex_cwd_matches_repo=lambda cwd, repo, cache: True,
        _strip_ccc_session_state_instruction=lambda s: s,
        _truncate_session_name=lambda s: s,
        _first_existing_dir=lambda *dirs: next((d for d in dirs if d), None),
        _find_git_root=lambda p: p,
        _resolve_dir_case=lambda p: p,
        _worktree_dirty_cached=lambda cwd, mtime: False,
        _parse_session_state=lambda text: "idle",
    )
    for k, v in overrides.items():
        setattr(stub, k, v)
    monkeypatch.setitem(sys.modules, "server", stub)
    return stub


def test_find_conversations_returns_an_empty_board_without_a_db(monkeypatch):
    monkeypatch.setattr(kilo, "_kilo_db_path", lambda: None)
    assert kilo.find_kilo_conversations() == []


def test_find_conversations_shapes_a_kilo_card(kilo_db, monkeypatch, tmp_path):
    _stub_core(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    now_ms = int(time.time() * 1000)
    _add_session(kilo_db, "ses_1", directory=str(repo), title="Wire up the parser",
                 updated=now_ms)
    _add_message(kilo_db, "m1", "ses_1", "user", [{"type": "text", "text": "prompt text"}])
    _add_message(kilo_db, "m2", "ses_1", "assistant", [{"type": "text", "text": "done"}])

    cards = kilo.find_kilo_conversations(repo_path=str(repo))
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == card["session_id"] == "ses_1"
    assert card["source"] == card["engine"] == "kilo"
    assert card["display_name"] == "Wire up the parser"
    assert card["ai_title"] == "Wire up the parser"
    assert card["first_message"] == "prompt text"
    assert card["last_assistant_text"] == "done"
    assert card["session_state"] == "idle"
    assert card["session_cwd"] == str(repo) and card["session_cwd_exists"] is True
    assert card["is_live"] is True


def test_placeholder_titles_are_not_reported_as_ai_summaries(kilo_db, monkeypatch):
    _stub_core(monkeypatch)
    _add_session(kilo_db, "ses_1", title="New session - 2026-01-01")
    _add_message(kilo_db, "m1", "ses_1", "user", [{"type": "text", "text": "first prompt"}])

    card = kilo.find_kilo_conversations(repo_path="/repo")[0]
    assert card["ai_title"] is None
    assert card["display_name"] == "first prompt", "falls back to the opening prompt"


def test_untitled_empty_session_falls_back_to_a_generic_name(kilo_db, monkeypatch):
    _stub_core(monkeypatch)
    _add_session(kilo_db, "ses_1", title="")
    assert kilo.find_kilo_conversations(repo_path="/repo")[0]["display_name"] == "Kilo session"


def test_name_override_wins_over_the_kilo_title(kilo_db, monkeypatch):
    _stub_core(monkeypatch, _load_session_name_overrides=lambda: {"ses_1": "My name"})
    _add_session(kilo_db, "ses_1", title="Kilo's title")
    card = kilo.find_kilo_conversations(repo_path="/repo")[0]
    assert card["display_name"] == "My name" and card["name_overridden"] is True


def test_repo_filtering_honours_pins_and_cwd_matching(kilo_db, monkeypatch):
    _stub_core(
        monkeypatch,
        _load_repo_pins=lambda: {"ses_pinned_here": "/repo", "ses_pinned_away": "/other"},
        _codex_cwd_matches_repo=lambda cwd, repo, cache: cwd == "/repo",
    )
    _add_session(kilo_db, "ses_pinned_here", directory="/elsewhere", updated=40)
    _add_session(kilo_db, "ses_pinned_away", directory="/repo", updated=30)
    _add_session(kilo_db, "ses_matching_cwd", directory="/repo", updated=20)
    _add_session(kilo_db, "ses_other_cwd", directory="/nope", updated=10)

    ids = [c["id"] for c in kilo.find_kilo_conversations(repo_path="/repo")]
    assert ids == ["ses_pinned_here", "ses_matching_cwd"]
    assert kilo.find_kilo_conversations(repo_path="/repo")[0]["pinned_repo"] is True

    all_ids = {c["id"] for c in kilo.find_kilo_conversations(repo_only=False)}
    assert all_ids == {
        "ses_pinned_here", "ses_pinned_away", "ses_matching_cwd", "ses_other_cwd",
    }


def test_recent_only_scans_drop_sessions_older_than_the_cutoff(kilo_db, monkeypatch):
    _stub_core(monkeypatch, _session_scan_cutoff_ts=lambda include_old: 5.0)
    _add_session(kilo_db, "ses_fresh", updated=9_000, created=9_000)
    _add_session(kilo_db, "ses_stale", updated=1_000, created=1_000)

    ids = [c["id"] for c in kilo.find_kilo_conversations(
        repo_path="/repo", include_old=False)]
    assert ids == ["ses_fresh"]


def test_a_recent_interaction_keeps_an_otherwise_stale_session(kilo_db, monkeypatch):
    _stub_core(
        monkeypatch,
        _session_scan_cutoff_ts=lambda include_old: 5.0,
        _load_last_interactions=lambda: {"ses_stale": 100.0},
    )
    _add_session(kilo_db, "ses_stale", updated=1_000, created=1_000)
    cards = kilo.find_kilo_conversations(repo_path="/repo", include_old=False)
    assert [c["id"] for c in cards] == ["ses_stale"]
    assert cards[0]["last_interacted"] == 100.0


def test_recent_only_scans_stop_at_the_file_limit(kilo_db, monkeypatch):
    _stub_core(monkeypatch, _session_scan_file_limit=lambda include_old: 2)
    for i in range(4):
        _add_session(kilo_db, f"ses_{i}", updated=(i + 1) * 1_000)
    cards = kilo.find_kilo_conversations(repo_path="/repo", include_old=False)
    assert [c["id"] for c in cards] == ["ses_3", "ses_2"]


def test_lifecycle_sets_and_kilo_archive_flag_both_mark_a_card_archived(
        kilo_db, monkeypatch):
    _stub_core(
        monkeypatch,
        _load_conversation_lifecycle_sets=lambda: ({"ses_ccc"}, {"ses_trashed"}),
        _load_verified_conversations=lambda: ["ses_kilo"],
    )
    _add_session(kilo_db, "ses_ccc", updated=30)
    _add_session(kilo_db, "ses_kilo", updated=20, archived=1_700_000_000_000)
    _add_session(kilo_db, "ses_trashed", updated=10)

    cards = {c["id"]: c for c in kilo.find_kilo_conversations(repo_path="/repo")}
    assert cards["ses_ccc"]["archived"] is True
    assert cards["ses_kilo"]["archived"] is True
    assert cards["ses_kilo"]["verified"] is True
    assert cards["ses_trashed"]["trashed"] is True


def test_worktree_suffix_is_split_out_of_the_folder_label(kilo_db, monkeypatch):
    _stub_core(monkeypatch)
    _add_session(kilo_db, "ses_1", directory="/src/myrepo-wt-feature")
    card = kilo.find_kilo_conversations(repo_path="/repo")[0]
    assert card["folder_label"] == "/src/myrepo"
    assert card["worktree_label"] == "feature"


def test_cards_sort_by_last_interaction_then_modified(kilo_db, monkeypatch):
    _stub_core(monkeypatch, _load_last_interactions=lambda: {"ses_old": 9_999_999_999})
    _add_session(kilo_db, "ses_old", updated=1_000)
    _add_session(kilo_db, "ses_recent", updated=8_000_000)
    assert [c["id"] for c in kilo.find_kilo_conversations(repo_path="/repo")] == [
        "ses_old", "ses_recent",
    ]


def test_core_loader_failures_degrade_to_empty_defaults(kilo_db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("store unavailable")

    _stub_core(
        monkeypatch,
        _load_repo_pins=boom,
        _load_session_name_overrides=boom,
        _load_conversation_lifecycle_sets=boom,
        _load_verified_conversations=boom,
        _load_last_interactions=boom,
    )
    _add_session(kilo_db, "ses_1", title="Still rendered")
    card = kilo.find_kilo_conversations(repo_path="/repo")[0]
    assert card["display_name"] == "Still rendered"
    assert card["archived"] is False and card["verified"] is False
    assert card["last_interacted"] is None
