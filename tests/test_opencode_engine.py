"""Unit tests for `ccc_server/opencode.py` (OpenCode adapter).

Covers the three surfaces the board depends on and that were almost entirely
untested (~13% coverage): reading OpenCode's SQLite store into cards and
transcripts, classifying a session id as OpenCode (Kilo shares the `ses_`
prefix, so the DB probe is what keeps the two engines apart), and building the
`opencode run --session ...` follow-up invocation.

The store is a temporary SQLite file pointed at by `$OPENCODE_DB`; the CLI is a
recording shell script, so `resume_session_opencode` really spawns a process
and we assert on the argv it was handed. Names still living in server.py are
reached through `ccc_server.core`, which proxies `sys.modules["server"]` — the
tests install a stub module there rather than importing the real server.
"""
import json
import os
import sqlite3
import stat
import sys
import threading
import time
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import opencode  # noqa: E402


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


def _add_message(con, mid, sid, role, parts, created=0):
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?)",
        (mid, sid, json.dumps({"role": role, "time": {"created": created}}), created),
    )
    for i, part in enumerate(parts):
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?)",
            (f"{mid}-p{i}", mid, json.dumps(part), created + i),
        )
    con.commit()


@pytest.fixture()
def opencode_db(tmp_path, monkeypatch):
    db_path = tmp_path / "opencode.db"
    con = _make_db(db_path)
    monkeypatch.setenv("OPENCODE_DB", str(db_path))
    yield con
    con.close()


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
        _spawned_sessions=[],
    )
    for k, v in overrides.items():
        setattr(stub, k, v)
    monkeypatch.setitem(sys.modules, "server", stub)
    return stub


# --------------------------------------------------------------------------
# store location
# --------------------------------------------------------------------------

def test_db_path_prefers_the_env_override(tmp_path, monkeypatch):
    db = tmp_path / "custom.db"
    db.touch()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    assert opencode._opencode_db_path() == db


def test_env_override_expands_a_user_relative_path(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    db.touch()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DB", "~/opencode.db")
    assert opencode._opencode_db_path() == db


def test_db_path_falls_back_to_the_share_dir_and_is_none_when_absent(
        tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setattr(opencode.Path, "home", staticmethod(lambda: tmp_path))
    assert opencode._opencode_db_path() is None

    db = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    db.touch()
    assert opencode._opencode_db_path() == db


def test_connection_is_read_only(opencode_db):
    con = opencode._opencode_connect()
    assert con is not None
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO session (id) VALUES ('nope')")
    con.close()


def test_connect_returns_none_without_a_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing.db"))
    assert opencode._opencode_connect() is None


# --------------------------------------------------------------------------
# _resolve_opencode_bin
# --------------------------------------------------------------------------

def test_bin_resolution_prefers_the_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CCC_OPENCODE_BIN", str(fake))
    assert opencode._resolve_opencode_bin() == {
        "available": True, "bin": str(fake), "source": "env",
    }


def test_bin_resolution_rejects_a_non_executable_override(tmp_path, monkeypatch):
    dud = tmp_path / "opencode"
    dud.write_text("not executable")
    monkeypatch.setenv("CCC_OPENCODE_BIN", str(dud))
    resolved = opencode._resolve_opencode_bin()
    assert resolved["available"] is False and resolved["bin"] is None
    assert resolved["code"] == "opencode_unavailable"
    assert str(dud) in resolved["reason"]


def test_bin_resolution_falls_back_to_path(monkeypatch):
    monkeypatch.delenv("CCC_OPENCODE_BIN", raising=False)
    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/local/bin/opencode")
    assert opencode._resolve_opencode_bin() == {
        "available": True, "bin": "/usr/local/bin/opencode", "source": "path",
    }


def test_bin_resolution_reports_an_install_hint_when_missing(monkeypatch):
    monkeypatch.delenv("CCC_OPENCODE_BIN", raising=False)
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)
    resolved = opencode._resolve_opencode_bin()
    assert resolved["available"] is False
    assert resolved["code"] == "opencode_unavailable"
    assert "CCC_OPENCODE_BIN" in resolved["reason"]


# --------------------------------------------------------------------------
# session rows + text extraction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, ""),
    (json.dumps({"providerID": "anthropic", "modelID": "sonnet"}), "anthropic/sonnet"),
    (json.dumps({"id": "gpt-5", "providerID": "openai"}), "openai/gpt-5"),
    (json.dumps({"modelID": "already/qualified", "providerID": "x"}), "already/qualified"),
    (json.dumps({"providerID": "openai"}), ""),
    ("bare-string", "bare-string"),
    (json.dumps([1]), "[1]"),
])
def test_model_string_rendering(raw, expected):
    assert opencode._opencode_model_str(raw) == expected


def test_fetch_sessions_is_newest_first_and_limited(opencode_db):
    for i in range(3):
        _add_session(opencode_db, f"ses_{i}", title=f"t{i}", updated=(i + 1) * 1_000)
    con = opencode._opencode_connect()
    rows = opencode._opencode_fetch_sessions(con)
    assert [r["id"] for r in rows] == ["ses_2", "ses_1", "ses_0"]
    assert rows[0]["updated"] == 3.0
    assert [r["id"] for r in opencode._opencode_fetch_sessions(con, limit=1)] == ["ses_2"]
    con.close()


def test_fetch_sessions_tolerates_a_foreign_schema(tmp_path, monkeypatch):
    db = tmp_path / "other.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    con = opencode._opencode_connect()
    assert opencode._opencode_fetch_sessions(con) == []
    con.close()


def test_first_user_and_last_assistant_text(opencode_db):
    _add_session(opencode_db, "ses_1")
    _add_message(opencode_db, "m1", "ses_1", "user", [
        {"type": "file"}, {"type": "text", "text": " ask "},
    ], created=10)
    _add_message(opencode_db, "m2", "ses_1", "assistant", [
        {"type": "text", "text": "older"},
    ], created=20)
    _add_message(opencode_db, "m3", "ses_1", "assistant", [
        {"type": "text", "text": "answer a"}, {"type": "text", "text": "answer b"},
    ], created=30)

    con = opencode._opencode_connect()
    assert opencode._opencode_first_user_text(con, "ses_1") == "ask"
    assert opencode._opencode_last_assistant_text(con, "ses_1") == "answer a\nanswer b"
    assert opencode._opencode_first_user_text(con, "ses_none") == ""
    assert opencode._opencode_last_assistant_text(con, "ses_none") == ""
    con.close()


# --------------------------------------------------------------------------
# find_opencode_conversations
# --------------------------------------------------------------------------

def test_find_conversations_without_a_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing.db"))
    assert opencode.find_opencode_conversations() == []


def test_find_conversations_shapes_an_opencode_card(opencode_db, monkeypatch, tmp_path):
    _stub_core(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    _add_session(opencode_db, "ses_1", directory=str(repo), title="Ship the adapter",
                 updated=int(time.time() * 1000))
    _add_message(opencode_db, "m1", "ses_1", "user", [{"type": "text", "text": "go"}])
    _add_message(opencode_db, "m2", "ses_1", "assistant", [{"type": "text", "text": "ok"}])

    card = opencode.find_opencode_conversations(repo_path=str(repo))[0]
    assert card["source"] == card["engine"] == "opencode"
    assert card["display_name"] == card["ai_title"] == "Ship the adapter"
    assert card["first_message"] == card["last_prompt"] == "go"
    assert card["last_assistant_text"] == "ok"
    assert card["session_state"] == "idle"
    assert card["is_live"] is True


def test_placeholder_title_falls_back_to_prompt_then_generic_name(
        opencode_db, monkeypatch):
    _stub_core(monkeypatch)
    _add_session(opencode_db, "ses_prompt", title="New session - 2026", updated=20)
    _add_message(opencode_db, "m1", "ses_prompt", "user",
                 [{"type": "text", "text": "the prompt"}])
    _add_session(opencode_db, "ses_bare", title="", updated=10)

    cards = {c["id"]: c for c in opencode.find_opencode_conversations(repo_path="/repo")}
    assert cards["ses_prompt"]["ai_title"] is None
    assert cards["ses_prompt"]["display_name"] == "the prompt"
    assert cards["ses_bare"]["display_name"] == "OpenCode session"


def test_repo_scoping_drops_sessions_pinned_to_another_repo(opencode_db, monkeypatch):
    _stub_core(
        monkeypatch,
        _load_repo_pins=lambda: {"ses_away": "/other"},
        _codex_cwd_matches_repo=lambda cwd, repo, cache: cwd == "/repo",
    )
    _add_session(opencode_db, "ses_away", directory="/repo", updated=30)
    _add_session(opencode_db, "ses_here", directory="/repo", updated=20)
    _add_session(opencode_db, "ses_elsewhere", directory="/nope", updated=10)

    ids = [c["id"] for c in opencode.find_opencode_conversations(repo_path="/repo")]
    assert ids == ["ses_here"]


def test_cutoff_and_row_limit_apply_only_to_recent_scans(opencode_db, monkeypatch):
    _stub_core(
        monkeypatch,
        _session_scan_cutoff_ts=lambda include_old: 5.0 if not include_old else 0,
        _session_scan_file_limit=lambda include_old: 1 if not include_old else 0,
    )
    _add_session(opencode_db, "ses_new", updated=9_000, created=9_000)
    _add_session(opencode_db, "ses_mid", updated=8_000, created=8_000)
    _add_session(opencode_db, "ses_old", updated=1_000, created=1_000)

    assert [c["id"] for c in opencode.find_opencode_conversations(
        repo_path="/repo", include_old=False)] == ["ses_new"]
    assert len(opencode.find_opencode_conversations(repo_path="/repo")) == 3


# --------------------------------------------------------------------------
# _is_opencode_session / _opencode_session_cwd
# --------------------------------------------------------------------------

def test_session_classification_matches_the_spawn_registry(opencode_db, monkeypatch):
    _stub_core(monkeypatch, _spawned_sessions=[
        {"engine": "opencode", "session_id": "spawned-1"},
        {"engine": "opencode", "resumed_sid": "resumed-1"},
        {"engine": "opencode", "name": "named-1"},
        {"engine": "kilo", "session_id": "kilo-1"},
    ])
    assert opencode._is_opencode_session("spawned-1") is True
    assert opencode._is_opencode_session("resumed-1") is True
    assert opencode._is_opencode_session("named-1") is True
    assert opencode._is_opencode_session("kilo-1") is False


def test_prefixed_ids_are_only_opencode_when_the_store_knows_them(
        opencode_db, monkeypatch):
    _stub_core(monkeypatch)
    _add_session(opencode_db, "ses_known")
    assert opencode._is_opencode_session("ses_known") is True
    assert opencode._is_opencode_session("ses_unknown") is False, (
        "Kilo shares the ses_ prefix, so the id must exist in this engine's DB")
    assert opencode._is_opencode_session("00000000-uuid") is False
    assert opencode._is_opencode_session(None) is False


def test_session_cwd_lookup(opencode_db, monkeypatch):
    _add_session(opencode_db, "ses_1", directory="/repo/one")
    _add_session(opencode_db, "ses_blank", directory="")
    assert opencode._opencode_session_cwd("ses_1") == "/repo/one"
    assert opencode._opencode_session_cwd("ses_blank") is None
    assert opencode._opencode_session_cwd("ses_missing") is None
    assert opencode._opencode_session_cwd("") is None


def test_session_cwd_is_none_without_a_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing.db"))
    assert opencode._opencode_session_cwd("ses_1") is None


# --------------------------------------------------------------------------
# _parse_opencode_conversation
# --------------------------------------------------------------------------

def test_parse_conversation_renders_turns_tools_and_results(opencode_db):
    _add_session(opencode_db, "ses_1")
    _add_message(opencode_db, "m1", "ses_1", "user",
                 [{"type": "text", "text": "build it"}], created=1_700_000_000_000)
    _add_message(opencode_db, "m2", "ses_1", "assistant", [
        {"type": "reasoning", "text": "planning"},
        {"type": "tool", "tool": "bash", "callID": "c1",
         "state": {"input": {"command": "make"}, "output": "built", "status": "done"}},
    ], created=1_700_000_000_000)

    result = opencode._parse_opencode_conversation("ses_1")
    assert [e["type"] for e in result["events"]] == [
        "user_text", "assistant", "tool_result"]
    assert result["events"][1]["message_id"] == "opencode-2"
    assert result["events"][1]["blocks"][1]["detail"] == "make"
    assert result["events"][2]["text"] == "built"
    assert result["events"][2]["is_error"] is False
    assert result["last_line"] == 3


def test_parse_conversation_truncates_long_tool_output(opencode_db):
    _add_session(opencode_db, "ses_1")
    _add_message(opencode_db, "m1", "ses_1", "assistant", [
        {"type": "tool", "tool": "bash", "callID": "c1",
         "state": {"input": {"command": "yes"}, "output": "x" * 2000, "status": "error"}},
    ])
    tool_result = opencode._parse_opencode_conversation("ses_1")["events"][-1]
    assert len(tool_result["text"]) == 800
    assert tool_result["is_error"] is True


def test_parse_conversation_pages_from_after_line(opencode_db):
    _add_session(opencode_db, "ses_1")
    _add_message(opencode_db, "m1", "ses_1", "user",
                 [{"type": "text", "text": "one"}], created=1)
    _add_message(opencode_db, "m2", "ses_1", "user",
                 [{"type": "text", "text": "two"}], created=2)
    tail = opencode._parse_opencode_conversation("ses_1", after_line=1)
    assert [e["text"] for e in tail["events"]] == ["two"]
    assert tail["last_line"] == 2


def test_parse_conversation_without_a_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "missing.db"))
    assert opencode._parse_opencode_conversation("ses_1") == {
        "events": [], "last_line": 0}


# --------------------------------------------------------------------------
# resume_session_opencode
# --------------------------------------------------------------------------

@pytest.fixture()
def recording_opencode_bin(tmp_path, monkeypatch):
    """A stand-in `opencode` CLI that records the argv it was invoked with."""
    argv_log = tmp_path / "argv.txt"
    script = tmp_path / "opencode"
    script.write_text(
        f'#!/bin/sh\nfor a in "$@"; do echo "$a" >> "{argv_log}"; done\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CCC_OPENCODE_BIN", str(script))
    return argv_log


def _resume_core(monkeypatch, tmp_path, **overrides):
    log_dir = tmp_path / "logs"
    recorded = {}

    def _record_spawn_to_registry(**kw):
        recorded.update(kw)

    defaults = dict(
        _spawned_sessions=[],
        _poll_spawn_entry=lambda entry: None,
        _pending_resume_lock=threading.Lock(),
        _pending_resume_queue={},
        _save_pending_inputs=lambda: None,
        _spawn_registry_entry_for_session=lambda sid, engine: None,
        find_session_cwd=lambda sid: str(tmp_path),
        _git_toplevel_for_existing_dir=lambda cwd: cwd,
        repo_log_dir=lambda repo: log_dir,
        _get_session_override=lambda sid: None,
        _record_spawn_to_registry=_record_spawn_to_registry,
    )
    defaults.update(overrides)
    return _stub_core(monkeypatch, **defaults), recorded


def _wait_for_argv(argv_log, expected_lines):
    for _ in range(100):
        if argv_log.exists() and len(argv_log.read_text().splitlines()) >= expected_lines:
            break
        time.sleep(0.05)
    return argv_log.read_text().splitlines()


@pytest.mark.parametrize("session_id,text", [("", "hi"), ("ses_1", "")])
def test_resume_requires_a_session_and_text(monkeypatch, tmp_path, session_id, text):
    _resume_core(monkeypatch, tmp_path)
    assert opencode.resume_session_opencode(session_id, text) == {
        "ok": False, "error": "missing session_id or text"}


def test_resume_reports_a_missing_cli(monkeypatch, tmp_path):
    _resume_core(monkeypatch, tmp_path)
    monkeypatch.delenv("CCC_OPENCODE_BIN", raising=False)
    monkeypatch.setattr(opencode.shutil, "which", lambda name: None)
    result = opencode.resume_session_opencode("ses_1", "hello")
    assert result["ok"] is False and result["code"] == "opencode_unavailable"


def test_resume_spawns_the_cli_and_registers_the_process(
        monkeypatch, tmp_path, recording_opencode_bin):
    stub, recorded = _resume_core(monkeypatch, tmp_path)
    result = opencode.resume_session_opencode("ses_1", "keep going")

    assert result["ok"] is True and result["resumed"] is True
    assert result["via"] == "opencode-resume" and result["engine"] == "opencode"
    assert _wait_for_argv(recording_opencode_bin, 5) == [
        "run", "--session", "ses_1", "--auto", "keep going"]
    assert os.path.basename(result["log"]).startswith("resume-opencode-ses_1")
    assert stub._spawned_sessions[0]["pid"] == result["pid"]
    assert stub._spawned_sessions[0]["resumed_sid"] == "ses_1"
    assert recorded["engine"] == "opencode" and recorded["session_id"] == "ses_1"


@pytest.mark.parametrize("overrides,env,expected_model", [
    ({"_get_session_override": lambda sid: {"model": "picked/model"}}, None, "picked/model"),
    ({"_spawn_registry_entry_for_session": lambda sid, engine: {"model": "spawn/model"}},
     None, "spawn/model"),
    ({}, "env/model", "env/model"),
])
def test_resume_model_precedence(monkeypatch, tmp_path, recording_opencode_bin,
                                 overrides, env, expected_model):
    _resume_core(monkeypatch, tmp_path, **overrides)
    if env:
        monkeypatch.setenv("CCC_OPENCODE_MODEL", env)
    else:
        monkeypatch.delenv("CCC_OPENCODE_MODEL", raising=False)

    result = opencode.resume_session_opencode("ses_1", "go")
    assert result["model"] == expected_model
    argv = _wait_for_argv(recording_opencode_bin, 7)
    assert argv[4:6] == ["--model", expected_model]
    assert argv[-1] == "go", "the prompt stays last so it isn't read as a flag"


def test_resume_omits_the_model_flag_when_nothing_is_known(
        monkeypatch, tmp_path, recording_opencode_bin):
    _resume_core(monkeypatch, tmp_path)
    monkeypatch.delenv("CCC_OPENCODE_MODEL", raising=False)
    result = opencode.resume_session_opencode("ses_1", "go")
    assert result["model"] == ""
    assert "--model" not in _wait_for_argv(recording_opencode_bin, 5)


def test_resume_queues_the_prompt_while_a_turn_is_still_running(
        monkeypatch, tmp_path, recording_opencode_bin):
    running = {"engine": "opencode", "resumed_sid": "ses_1", "pid": 4242}
    saved = []
    stub, _ = _resume_core(
        monkeypatch, tmp_path,
        _spawned_sessions=[running],
        _poll_spawn_entry=lambda entry: None,  # None == still running
        _save_pending_inputs=lambda: saved.append(True),
    )

    result = opencode.resume_session_opencode("ses_1", "follow-up")
    assert result == {
        "ok": True, "queued": True, "pid": 4242, "via": "opencode-resume-queued",
        "queued_reason": "waiting for the current OpenCode turn to finish",
        "engine": "opencode",
    }
    assert stub._pending_resume_queue == {"ses_1": ["follow-up"]}
    assert saved == [True], "the queue must be persisted, not just held in memory"
    assert not recording_opencode_bin.exists(), "no second CLI process while queued"


def test_resume_spawns_when_the_previous_turn_has_exited(
        monkeypatch, tmp_path, recording_opencode_bin):
    finished = {"engine": "opencode", "resumed_sid": "ses_1", "pid": 4242}
    stub, _ = _resume_core(
        monkeypatch, tmp_path,
        _spawned_sessions=[finished],
        _poll_spawn_entry=lambda entry: 0,  # exit code == process is done
    )
    result = opencode.resume_session_opencode("ses_1", "follow-up")
    assert result.get("queued") is None and result["ok"] is True
    assert stub._pending_resume_queue == {}


def test_resume_falls_back_to_home_when_the_session_cwd_is_gone(
        monkeypatch, tmp_path, recording_opencode_bin):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(opencode.Path, "home", staticmethod(lambda: home))
    _, recorded = _resume_core(
        monkeypatch, tmp_path, find_session_cwd=lambda sid: "/definitely/not/here")

    assert opencode.resume_session_opencode("ses_1", "go")["ok"] is True
    assert recorded["cwd"] == str(home)
