# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""MEMO-FIX-19: session_fts coverage for Kimi Code, Gemini CLI and Cursor
(previously only Claude Code + Codex), plus the file_cache schema migration
and search_sessions_enriched -- the shared retrieval primitive now behind
both sidebar search endpoints.

Fixture shapes below are modeled on real on-disk transcripts on this machine
(~/.kimi-code, ~/.gemini/tmp, ~/.cursor/projects), not guessed -- in
particular Kimi's state.json key is `cwd`, not `workDir` (an earlier draft of
parse_kimi read the wrong key and silently indexed every Kimi session with an
empty cwd).

Gemini/Cursor parsing goes through ccc_server.gemini / ccc_server.cursor,
which resolve a couple of module-level constants (GEMINI_HOME,
CURSOR_PROJECTS_ROOT) via ccc_server.core's lazy proxy onto the real `server`
module -- importing `server` here registers that, matching the pattern
already used by tests/test_gemini_listing_meta_memo.py and
tests/test_cursor_antigravity_tail_incremental.py.
"""
import json
import time
from pathlib import Path

import pytest

import server  # noqa: F401  (registers the core module gemini/cursor bind to)
from ccc_server import cursor as ccc_cursor
from ccc_server import session_fts
from ccc_server import ship_graph


@pytest.fixture
def fts_env(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    kimi_dir = tmp_path / "kimi"
    gemini_dir = tmp_path / "gemini"
    cursor_dir = tmp_path / "cursor"
    for d in (projects_dir, codex_dir, kimi_dir, gemini_dir, cursor_dir):
        d.mkdir(parents=True)

    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(tmp_path / "session_fts.sqlite"))
    # search_sessions_enriched()'s lineage collapsing (MEMO-FIX-lineage) reads
    # continuation_origin from ship_graph's own DB -- point it at an isolated
    # file too, or it falls through to the real ~/.claude/command-center one.
    monkeypatch.setenv("CCC_SHIP_GRAPH_DB", str(tmp_path / "ship_graph.sqlite"))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(kimi_dir))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(gemini_dir))
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(cursor_dir))
    # Isolate the Hermes messages_fts channel (S9) -- see test_session_fts.py.
    monkeypatch.setattr(server, "HERMES_STATE_DB", tmp_path / "hermes" / "state.db")
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "hermes" / "profiles")
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")

    for mod in (session_fts, ship_graph):
        if hasattr(mod._tls, "conn") and mod._tls.conn:
            mod._tls.conn.close()
            mod._tls.conn = None
    session_fts._last_sync_ts = 0.0
    session_fts._ollama_state["ts"] = 0.0
    session_fts._ollama_state["ok"] = False
    session_fts._vec_cache["sids"] = []
    session_fts._vec_cache["vecs"] = []
    session_fts._bg_sync_running = False
    session_fts._backfill_running = False
    ship_graph._last_sync_ts = 0.0

    return {
        "projects": projects_dir,
        "codex": codex_dir,
        "kimi": kimi_dir,
        "gemini": gemini_dir,
        "cursor": cursor_dir,
    }


def _write_kimi_session(kimi_dir: Path, workdir_slug: str, sid: str, cwd: str, events: list[dict]):
    session_dir = kimi_dir / workdir_slug / sid
    wire_dir = session_dir / "agents" / "main"
    wire_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "state.json").write_text(
        json.dumps({"id": sid, "cwd": cwd}), encoding="utf-8"
    )
    with open(wire_dir / "wire.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return wire_dir / "wire.jsonl"


def _kimi_prompt_event(text, time_ms=1700000000000):
    return {"type": "turn.prompt", "input": [{"type": "text", "text": text}], "time": time_ms}


def _kimi_text_event(text, time_ms=1700000001000):
    return {
        "type": "context.append_loop_event",
        "event": {"type": "content.part", "part": {"type": "text", "text": text}},
        "time": time_ms,
    }


def _kimi_tool_result_event(output, time_ms=1700000002000):
    return {
        "type": "context.append_loop_event",
        "event": {"type": "tool.result", "result": {"output": output}},
        "time": time_ms,
    }


def test_parse_kimi_reads_cwd_from_state_json(fts_env):
    """Regression: state.json's key is `cwd`, not `workDir` -- a wrong key
    here silently indexes every Kimi session with cwd == ''."""
    wire = _write_kimi_session(
        fts_env["kimi"], "wd_proj_abc123", "session_11111111-1111-1111-1111-111111111111",
        "/Users/x/dev/proj",
        [_kimi_prompt_event("fix the flaky retry test")],
    )
    r = session_fts.parse_kimi(str(wire))
    assert r is not None
    assert r["cwd"] == "/Users/x/dev/proj"
    assert r["engine"] == "kimi"
    # Kimi's on-disk dir is `session_<uuid>`; the recall index stores the bare UUID.
    assert r["sid"] == "11111111-1111-1111-1111-111111111111"


def test_parse_kimi_extracts_prompt_and_tool_output(fts_env):
    wire = _write_kimi_session(
        fts_env["kimi"], "wd_proj_abc123", "session_22222222-2222-2222-2222-222222222222",
        "/Users/x/dev/proj",
        [
            _kimi_prompt_event("investigate the flaky retry test failure"),
            _kimi_text_event("I ran the tests and found a race condition"),
            _kimi_tool_result_event("3 passed, 0 failed after the fix"),
        ],
    )
    r = session_fts.parse_kimi(str(wire))
    assert r is not None
    assert "flaky retry test" in r["user_text"]
    assert "race condition" in r["assistant_text"]


def test_parse_kimi_missing_state_json_yields_empty_cwd(fts_env):
    session_dir = fts_env["kimi"] / "wd_proj_x" / "session_33333333-3333-3333-3333-333333333333"
    wire_dir = session_dir / "agents" / "main"
    wire_dir.mkdir(parents=True)
    with open(wire_dir / "wire.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(_kimi_prompt_event("no state.json here")) + "\n")
    r = session_fts.parse_kimi(str(wire_dir / "wire.jsonl"))
    assert r is not None
    assert r["cwd"] == ""


def test_parse_kimi_empty_transcript_returns_none(fts_env):
    wire = _write_kimi_session(
        fts_env["kimi"], "wd_proj_empty", "session_44444444-4444-4444-4444-444444444444",
        "/tmp/empty", [{"type": "metadata", "time": 1700000000000}],
    )
    assert session_fts.parse_kimi(str(wire)) is None


def _write_gemini_session(gemini_dir: Path, slug: str, sid: str, cwd: str, messages: list[dict]):
    proj_dir = gemini_dir / slug
    chats_dir = proj_dir / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / ".project_root").write_text(cwd, encoding="utf-8")
    chat = {
        "sessionId": sid,
        "projectHash": slug,
        "startTime": "2026-07-01T00:00:00.000Z",
        "lastUpdated": "2026-07-01T00:05:00.000Z",
        "messages": messages,
        "kind": "chat",
    }
    path = chats_dir / f"session-2026-07-01T00-00-{sid[:8]}.json"
    path.write_text(json.dumps(chat), encoding="utf-8")
    return path


def test_parse_gemini_reads_cwd_from_project_root(fts_env):
    path = _write_gemini_session(
        fts_env["gemini"], "my-gemini-proj", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "/Users/x/dev/gemini-proj",
        [
            {"id": "m1", "timestamp": "2026-07-01T00:00:10.000Z", "type": "user",
             "content": [{"text": "add a title search feature to the library app"}]},
            {"id": "m2", "timestamp": "2026-07-01T00:00:20.000Z", "type": "gemini",
             "content": [{"text": "added GoogleBooksTitleSearch export"}], "toolCalls": []},
        ],
    )
    r = session_fts.parse_gemini(str(path))
    assert r is not None
    assert r["cwd"] == "/Users/x/dev/gemini-proj"
    assert r["engine"] == "gemini"
    assert "title search feature" in r["user_text"]
    assert "GoogleBooksTitleSearch" in r["assistant_text"]


def test_parse_gemini_missing_project_root_yields_empty_cwd(fts_env):
    proj_dir = fts_env["gemini"] / "no-root-proj"
    chats_dir = proj_dir / "chats"
    chats_dir.mkdir(parents=True)
    chat = {
        "sessionId": "ffffffff-0000-0000-0000-000000000000",
        "messages": [{"id": "m1", "timestamp": "2026-07-01T00:00:00Z", "type": "user",
                      "content": [{"text": "hello"}]}],
    }
    path = chats_dir / "session-2026-07-01T00-00-ffffffff.json"
    path.write_text(json.dumps(chat), encoding="utf-8")
    r = session_fts.parse_gemini(str(path))
    assert r is not None
    assert r["cwd"] == ""


def test_parse_gemini_empty_transcript_returns_none(fts_env):
    path = _write_gemini_session(
        fts_env["gemini"], "empty-proj", "00000000-0000-0000-0000-000000000000",
        "/tmp/empty", [],
    )
    assert session_fts.parse_gemini(str(path)) is None


def _write_cursor_session(cursor_dir: Path, real_cwd: Path, sid: str, events: list[dict]):
    slug = ccc_cursor._cursor_project_slug(real_cwd)
    session_dir = cursor_dir / slug / "agent-transcripts" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{sid}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def test_parse_cursor_decodes_cwd_from_slug(fts_env, tmp_path, monkeypatch):
    real_cwd = tmp_path / "real-cursor-repo"
    real_cwd.mkdir()
    # _cursor_cwd_from_project_slug's fallback heuristics (decode the slug
    # back into a path, or naively replace '-' with '/') can't reverse a deep
    # macOS tmp path -- the reliable path it takes in production is a known
    # repo list match, so make this dir "known" the same way.
    monkeypatch.setattr(ccc_cursor._core, "_known_repo_paths", lambda: [str(real_cwd)])
    path = _write_cursor_session(
        fts_env["cursor"], real_cwd, "cccccccc-cccc-cccc-cccc-cccccccccccc",
        [
            {"role": "user", "timestamp": "2026-07-02T00:00:00Z",
             "message": {"content": "why is the deploy script slow"}},
            {"role": "assistant", "timestamp": "2026-07-02T00:00:05Z",
             "message": {"content": "the deploy script re-downloads deps every run"}},
        ],
    )
    r = session_fts.parse_cursor(str(path))
    assert r is not None
    assert r["cwd"] == str(real_cwd)
    assert r["engine"] == "cursor"
    assert "deploy script slow" in r["user_text"]
    assert "re-downloads deps" in r["assistant_text"]


def test_parse_cursor_unresolvable_slug_yields_empty_cwd(fts_env):
    session_dir = fts_env["cursor"] / "totally-unknown-slug-xyz" / "agent-transcripts" / "dddddddd-dddd-dddd-dddd-dddddddddddd"
    session_dir.mkdir(parents=True)
    path = session_dir / "dddddddd-dddd-dddd-dddd-dddddddddddd.jsonl"
    path.write_text(json.dumps({
        "role": "user", "timestamp": "2026-07-02T00:00:00Z",
        "message": {"content": "hello from an unresolvable slug"},
    }) + "\n", encoding="utf-8")
    r = session_fts.parse_cursor(str(path))
    assert r is not None
    assert r["cwd"] == ""


def test_parse_cursor_empty_transcript_returns_none(fts_env, tmp_path):
    real_cwd = tmp_path / "empty-cursor-repo"
    real_cwd.mkdir()
    path = _write_cursor_session(
        fts_env["cursor"], real_cwd, "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", [],
    )
    assert session_fts.parse_cursor(str(path)) is None


def test_search_sessions_finds_kimi_gemini_and_cursor_sessions(fts_env, tmp_path):
    """Integration: all three new harnesses are actually reachable through
    the public search_sessions() entry point, not just their own parsers."""
    _write_kimi_session(
        fts_env["kimi"], "wd_proj_search", "session_55555555-5555-5555-5555-555555555555",
        "/Users/x/dev/proj",
        [_kimi_prompt_event("debug the frobnicator timeout bug")],
    )
    _write_gemini_session(
        fts_env["gemini"], "search-proj", "66666666-6666-6666-6666-666666666666",
        "/Users/x/dev/proj2",
        [{"id": "m1", "timestamp": "2026-07-01T00:00:00Z", "type": "user",
          "content": [{"text": "debug the frobnicator timeout bug in gemini"}]}],
    )
    real_cwd = tmp_path / "cursor-search-repo"
    real_cwd.mkdir()
    _write_cursor_session(
        fts_env["cursor"], real_cwd, "77777777-7777-7777-7777-777777777777",
        [{"role": "user", "timestamp": "2026-07-02T00:00:00Z",
          "message": {"content": "debug the frobnicator timeout bug in cursor"}}],
    )

    results = session_fts.search_sessions("frobnicator timeout")
    sids = {r["session_id"] for r in results}
    assert "55555555-5555-5555-5555-555555555555" in sids
    assert "66666666-6666-6666-6666-666666666666" in sids
    assert "77777777-7777-7777-7777-777777777777" in sids


def test_file_cache_schema_migration_adds_cwd_and_engine_columns(fts_env):
    """DBs built before MEMO-FIX-19 lack file_cache.cwd/.engine -- _init_db
    must ALTER them in, not crash on an old on-disk index."""
    import sqlite3

    conn = session_fts._get_connection()
    conn.executescript("DROP TABLE IF EXISTS file_cache;")
    conn.executescript(
        """
        CREATE TABLE file_cache (
            path TEXT PRIMARY KEY,
            sid TEXT,
            mtime REAL,
            size INTEGER,
            indexed INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(file_cache)")}
    assert "cwd" not in cols_before
    assert "engine" not in cols_before

    session_fts._init_db(conn)

    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(file_cache)")}
    assert "cwd" in cols_after
    assert "engine" in cols_after
    # Must not raise on a second call against an already-migrated db either.
    session_fts._init_db(conn)


def test_search_sessions_enriched_reports_cwd_engine_and_snippet(fts_env):
    dir_sid = "session_88888888-8888-8888-8888-888888888888"
    sid = dir_sid[len("session_"):]  # index normalizes Kimi ids to bare UUID
    _write_kimi_session(
        fts_env["kimi"], "wd_proj_enrich", dir_sid, "/Users/x/dev/enrichproj",
        [_kimi_prompt_event("investigate the widget rendering glitch")],
    )
    out = session_fts.search_sessions_enriched("widget rendering glitch", limit=5)
    assert out
    hit = next(h for h in out if h["session_id"] == sid)
    assert hit["cwd"] == "/Users/x/dev/enrichproj"
    assert hit["type"] == "kimi"
    assert hit["_source"] == "bm25"
    assert "widget" in hit["snippet"].lower()
    assert hit["ts_unix"] > 0
    assert hit["git_branch"] == ""


def test_search_sessions_enriched_cwd_like_filters_results(fts_env):
    _write_kimi_session(
        fts_env["kimi"], "wd_a", "session_99999999-9999-9999-9999-999999999991",
        "/Users/x/dev/repo-alpha",
        [_kimi_prompt_event("zzzqux marker term in repo alpha")],
    )
    _write_kimi_session(
        fts_env["kimi"], "wd_b", "session_99999999-9999-9999-9999-999999999992",
        "/Users/x/dev/repo-beta",
        [_kimi_prompt_event("zzzqux marker term in repo beta")],
    )
    all_hits = session_fts.search_sessions_enriched("zzzqux marker term", limit=10)
    assert len(all_hits) == 2

    filtered = session_fts.search_sessions_enriched(
        "zzzqux marker term", limit=10, cwd_like="repo-alpha",
    )
    assert len(filtered) == 1
    assert filtered[0]["cwd"] == "/Users/x/dev/repo-alpha"


def test_search_sessions_enriched_since_ts_excludes_old_sessions(fts_env):
    dir_sid = "session_aaaaaaaa-0000-0000-0000-000000000001"
    sid = dir_sid[len("session_"):]  # index normalizes Kimi ids to bare UUID
    wire = _write_kimi_session(
        fts_env["kimi"], "wd_old", dir_sid, "/Users/x/dev/oldproj",
        [_kimi_prompt_event("zzzfoo stale marker term")],
    )
    old_ts = time.time() - 30 * 86400
    import os
    os.utime(wire, (old_ts, old_ts))

    hits_unbounded = session_fts.search_sessions_enriched("zzzfoo stale marker", limit=10)
    assert any(h["session_id"] == sid for h in hits_unbounded)

    hits_recent = session_fts.search_sessions_enriched(
        "zzzfoo stale marker", limit=10, since_ts=time.time() - 86400,
    )
    assert not any(h["session_id"] == sid for h in hits_recent)
