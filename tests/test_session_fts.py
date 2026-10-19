# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unit tests for ccc_server/session_fts.py."""

import json
import os
import time
from pathlib import Path

import pytest

import server
from ccc_server import session_fts


@pytest.fixture
def fts_env(tmp_path, monkeypatch):
    """Set up isolated directories for session_fts tests."""
    db_path = tmp_path / "session_fts.sqlite"
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    kimi_dir = tmp_path / "kimi"
    gemini_dir = tmp_path / "gemini"
    cursor_dir = tmp_path / "cursor"
    harvested_dir = tmp_path / "harvested"
    projects_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    kimi_dir.mkdir(parents=True)
    gemini_dir.mkdir(parents=True)
    cursor_dir.mkdir(parents=True)

    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(db_path))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    # Isolate the three MEMO-FIX-19 harnesses too -- without these, a bare
    # search_sessions() call falls back to the real ~/.kimi-code, ~/.gemini,
    # ~/.cursor on this machine and leaks real session data into the test.
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(kimi_dir))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(gemini_dir))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(cursor_dir))
    # Isolate the S8b harvested-sandbox root too -- same leak risk as above.
    monkeypatch.setenv("CCC_HARVESTED_ROOT", str(harvested_dir))
    # Isolate the Hermes messages_fts channel (S9) too -- HERMES_STATE_DB is
    # resolved once at server.py import time, so an env var alone would be
    # too late; point the module attribute at a path that doesn't exist so
    # search_sessions() never queries a real ~/.hermes/state.db on this box.
    monkeypatch.setattr(server, "HERMES_STATE_DB", tmp_path / "hermes" / "state.db")
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "hermes" / "profiles")
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")  # disable cutoff for tests
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    # These tests exercise FTS mechanics, not the optional embeddings channel;
    # keep them hermetic and fast regardless of whether the dev box happens to
    # have a local Ollama daemon running. See test_session_fts_embeddings.py
    # for the embeddings/RRF-fusion behavior, mocked so it never hits a real
    # network service.
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")

    # Reset thread-local connection and throttle timestamp
    if hasattr(session_fts._tls, "conn"):
        if session_fts._tls.conn:
            session_fts._tls.conn.close()
        session_fts._tls.conn = None
    session_fts._last_sync_ts = 0.0
    session_fts._ollama_state["ts"] = 0.0
    session_fts._ollama_state["ok"] = False
    session_fts._vec_cache["sids"] = []
    session_fts._vec_cache["vecs"] = []
    session_fts._bg_sync_running = False
    session_fts._backfill_running = False

    return {
        "db": db_path,
        "projects": projects_dir,
        "codex": codex_dir,
        "kimi": kimi_dir,
        "gemini": gemini_dir,
        "cursor": cursor_dir,
        "harvested": harvested_dir,
    }


def _write_claude_jsonl(path: Path, sid: str, turns: list[dict], custom_title: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if custom_title:
        lines.append(json.dumps({"type": "custom-title", "customTitle": custom_title}))
    for t in turns:
        lines.append(json.dumps(t))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_search_sessions_empty_query(fts_env):
    assert session_fts.search_sessions("") == []
    assert session_fts.search_sessions("   ") == []


def test_search_sessions_finds_claude_prompt(fts_env):
    sid = "11111111-2222-3333-4444-555555555555"
    file_path = fts_env["projects"] / "repo-a" / f"{sid}.jsonl"
    turns = [
        {
            "type": "user",
            "cwd": "/Users/test/repo-a",
            "message": {"role": "user", "content": "investigate quantum teleportation anomalies"},
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "analyzing quantum logs"}]},
        },
    ]
    _write_claude_jsonl(file_path, sid, turns)

    results = session_fts.search_sessions("teleportation anomalies")
    assert len(results) > 0
    assert results[0]["session_id"] == sid
    assert "score" in results[0]


def test_search_sessions_path_query_matches_exactly(fts_env):
    """CCC-1256: a pasted path must match only the session that has that
    exact path, not every session mentioning any pasted image."""
    target = "aaaaaaaa-0000-0000-0000-000000000001"
    other = "aaaaaaaa-0000-0000-0000-000000000002"
    for sid, num in ((target, "1791146905566"), (other, "1791124336680")):
        _write_claude_jsonl(fts_env["projects"] / "repo-p" / f"{sid}.jsonl", sid, [{
            "type": "user",
            "cwd": "/Users/test/repo-p",
            "message": {"role": "user", "content": f"look at /Users/test/pasted-images/paste-{num}.png please"},
        }])

    hits = session_fts.search_sessions("/Users/test/pasted-images/paste-1791146905566.png")
    assert [h["session_id"] for h in hits] == [target]
    assert session_fts.search_sessions("/Users/test/pasted-images/paste-999.png") == []
    assert session_fts._literal_phrase_query("two words") == "two words"


def test_search_sessions_finds_assistant_text(fts_env):
    sid = "22222222-3333-4444-5555-666666666666"
    file_path = fts_env["projects"] / "repo-b" / f"{sid}.jsonl"
    turns = [
        {
            "type": "user",
            "cwd": "/Users/test/repo-b",
            "message": {"role": "user", "content": "run diagnostic"},
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "discovered subterranean crystal formations"}]},
        },
    ]
    _write_claude_jsonl(file_path, sid, turns)

    results = session_fts.search_sessions("subterranean crystal")
    assert len(results) > 0
    assert results[0]["session_id"] == sid


def test_search_sessions_finds_by_title_and_tickets(fts_env):
    sid = "33333333-4444-5555-6666-777777777777"
    file_path = fts_env["projects"] / "repo-c" / f"{sid}.jsonl"
    turns = [
        {
            "type": "user",
            "cwd": "/Users/test/repo-c",
            "message": {"role": "user", "content": "working on PROJ-9981 database migration"},
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "migration completed"}]},
        },
    ]
    _write_claude_jsonl(file_path, sid, turns, custom_title="Fix Database Deadlock")

    res_title = session_fts.search_sessions("Database Deadlock")
    assert len(res_title) > 0
    assert res_title[0]["session_id"] == sid

    res_ticket = session_fts.search_sessions("PROJ-9981")
    assert len(res_ticket) > 0
    assert res_ticket[0]["session_id"] == sid


def test_search_sessions_finds_codex_session(fts_env):
    sid = "44444444-5555-6666-7777-888888888888"
    file_path = fts_env["codex"] / "2026" / "09" / f"rollout-{sid}.jsonl"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": "/Users/test/codex-proj"}}),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": "optimize matrix multiplication kernel",
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": "implemented AVX512 vectorization",
            },
        }),
    ]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    results = session_fts.search_sessions("AVX512 vectorization")
    assert len(results) > 0
    assert results[0]["session_id"] == sid


def test_search_sessions_finds_harvested_sandbox_session(fts_env):
    """S8b: a transcript copied out of a wiped worker sandbox into the
    harvested/ root must be indexed and searchable like any other root."""
    sid = "99999999-1111-2222-3333-444444444444"
    file_path = fts_env["harvested"] / "ccc-local-sbx1" / ".claude" / "projects" / "some-repo" / f"{sid}.jsonl"
    _write_claude_jsonl(file_path, sid, [
        {"type": "user", "message": {"role": "user", "content": "harvested sandbox marker phrase"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "found it"}
        ]}},
    ])

    results = session_fts.search_sessions("harvested sandbox marker phrase")
    assert len(results) > 0
    assert results[0]["session_id"] == sid


def test_search_sessions_skips_scratch_and_empty(fts_env, monkeypatch):
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "0")
    # Empty (0 user messages)
    sid_empty = "55555555-6666-7777-8888-999999999999"
    f_empty = fts_env["projects"] / "repo" / f"{sid_empty}.jsonl"
    _write_claude_jsonl(f_empty, sid_empty, [
        {"type": "assistant", "message": {"role": "assistant", "content": "solo assistant message"}},
    ])

    # Scratch session (cwd matches scratch pattern)
    sid_scratch = "66666666-7777-8888-9999-000000000000"
    f_scratch = fts_env["projects"] / "repo" / f"{sid_scratch}.jsonl"
    _write_claude_jsonl(f_scratch, sid_scratch, [
        {"type": "user", "cwd": "/tmp/command-center-scratch/demo", "message": {"role": "user", "content": "scratch work unique_topic_term"}},
    ])

    results = session_fts.search_sessions("unique_topic_term")
    assert not any(r["session_id"] == sid_scratch for r in results)

    results2 = session_fts.search_sessions("solo assistant")
    assert not any(r["session_id"] == sid_empty for r in results2)


def test_search_sessions_incremental_update_and_deletion(fts_env):
    sid = "77777777-8888-9999-0000-111111111111"
    f = fts_env["projects"] / "repo" / f"{sid}.jsonl"
    _write_claude_jsonl(f, sid, [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "first version zebracrossing"}},
    ])

    res1 = session_fts.search_sessions("zebracrossing", force_refresh=True)
    assert len(res1) == 1 and res1[0]["session_id"] == sid

    # Modify file
    time.sleep(0.05)
    _write_claude_jsonl(f, sid, [
        {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "second version giraffeneck"}},
    ])
    os.utime(f, None)  # update mtime

    res2_old = session_fts.search_sessions("zebracrossing", force_refresh=True)
    assert len(res2_old) == 0

    res2_new = session_fts.search_sessions("giraffeneck", force_refresh=True)
    assert len(res2_new) == 1 and res2_new[0]["session_id"] == sid

    # Delete file
    f.unlink()
    res3 = session_fts.search_sessions("giraffeneck", force_refresh=True)
    assert len(res3) == 0


def test_search_sessions_cold_start_offloads_to_background(fts_env, monkeypatch):
    """MEMO-FIX-12: a catch-up too big to parse inline (cold start, or a big
    batch of new/changed transcripts) must not block search_sessions() for
    the whole parse -- it hands off to a background thread and answers
    immediately with is_indexing() True and whatever is already indexed."""
    # _BG_SYNC_THRESHOLD is read from its env var once at import time, so a
    # monkeypatched env var wouldn't take effect here -- set the module
    # attribute directly instead.
    monkeypatch.setattr(session_fts, "_BG_SYNC_THRESHOLD", 3)

    for i in range(6):
        sid = f"aaaaaaaa-bbbb-cccc-dddd-{i:012d}"
        f = fts_env["projects"] / "repo" / f"{sid}.jsonl"
        _write_claude_jsonl(f, sid, [
            {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "warp core diagnostics"}},
        ])

    assert session_fts.is_indexing() is False
    t0 = time.time()
    results = session_fts.search_sessions("warp core diagnostics")
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"cold-start search_sessions() blocked for {elapsed:.2f}s instead of offloading"
    assert results == []  # nothing indexed yet -- background sync just started
    assert session_fts.is_indexing() is True

    deadline = time.time() + 10.0
    while session_fts.is_indexing() and time.time() < deadline:
        time.sleep(0.05)
    assert session_fts.is_indexing() is False

    results2 = session_fts.search_sessions("warp core diagnostics")
    assert len(results2) == 6


def test_drain_embeddings_queues_jobs_when_ollama_down(fts_env, monkeypatch):
    """Root-cause regression: sessions parsed while Ollama is unreachable must
    be queued to semb_pending, not silently dropped -- previously
    _drain_embeddings returned early on an unavailable Ollama without ever
    recording the job, so those sessions were never embedded again."""
    conn = session_fts._get_connection()
    session_fts._init_db(conn)
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: False)

    def boom(*a, **kw):
        raise AssertionError("must not call Ollama when unavailable")

    monkeypatch.setattr(session_fts, "_embed_texts", boom)

    session_fts._drain_embeddings(conn, [("sid-outage", [("card", "some text")])])

    pending = [r[0] for r in conn.execute("SELECT sid FROM semb_pending")]
    assert pending == ["sid-outage"]


def _insert_sdoc_row(conn, sid: str) -> None:
    conn.execute(
        "INSERT INTO sdoc VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, "title", "prompt text", "report text", "body text", "meta", ""),
    )


def test_backfill_missing_embeddings_queues_unembedded_sdoc_sids(fts_env):
    """sdoc rows with no matching semb row (the historical-outage backlog, or
    any other sdoc/semb gap) get queued into semb_pending; rows that already
    have a semb row or are already pending are left alone."""
    conn = session_fts._get_connection()
    session_fts._init_db(conn)

    _insert_sdoc_row(conn, "sid-missing-1")
    _insert_sdoc_row(conn, "sid-missing-2")
    _insert_sdoc_row(conn, "sid-has-semb")
    _insert_sdoc_row(conn, "sid-already-pending")
    conn.execute("INSERT INTO semb (sid, kind, vec) VALUES (?, ?, ?)", ("sid-has-semb", "card", b""))
    conn.execute("INSERT INTO semb_pending (sid) VALUES (?)", ("sid-already-pending",))
    conn.commit()

    queued = session_fts._backfill_missing_embeddings(conn)

    assert queued == 2
    pending = {r[0] for r in conn.execute("SELECT sid FROM semb_pending")}
    assert pending == {"sid-missing-1", "sid-missing-2", "sid-already-pending"}


def test_backfill_missing_embeddings_is_bounded_per_call(fts_env, monkeypatch):
    """Never an O(all sessions) scan queued in one shot -- each call queues at
    most _MAX_EMBED_SESSIONS_PER_SYNC sids, matching the existing drain cap."""
    conn = session_fts._get_connection()
    session_fts._init_db(conn)
    monkeypatch.setattr(session_fts, "_MAX_EMBED_SESSIONS_PER_SYNC", 3)

    for i in range(10):
        _insert_sdoc_row(conn, f"sid-{i:02d}")
    conn.commit()

    queued = session_fts._backfill_missing_embeddings(conn)

    assert queued == 3
    assert conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0] == 3


def test_run_embedding_backfill_drains_full_backlog_when_ollama_returns(fts_env, monkeypatch):
    """End-to-end: a backlog of un-embedded sdoc rows (simulating everything
    indexed during the OPS-1251 Ollama outage) gets fully drained by the
    background backfill loop once Ollama is available, in bounded slices
    rather than one big batch."""
    monkeypatch.setattr(session_fts, "_MAX_EMBED_SESSIONS_PER_SYNC", 2)
    monkeypatch.setattr(session_fts, "_BACKFILL_RETRY_INTERVAL", 0.05)
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        session_fts, "_embed_texts",
        lambda texts, batch=32, timeout=None: [[1.0, 0.0] for _ in texts],
    )

    conn = session_fts._get_connection()
    session_fts._init_db(conn)
    for i in range(7):
        _insert_sdoc_row(conn, f"sid-{i:02d}")
    conn.commit()

    session_fts._run_embedding_backfill()

    deadline = time.time() + 5.0
    while session_fts._backfill_running and time.time() < deadline:
        time.sleep(0.05)

    assert not session_fts._backfill_running, "backfill loop did not finish in time"
    assert conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0] == 0
    embedded = conn.execute("SELECT COUNT(DISTINCT sid) FROM semb").fetchone()[0]
    assert embedded == 7


def test_run_embedding_backfill_backs_off_while_ollama_down(fts_env, monkeypatch):
    """While Ollama stays unreachable the loop must not spin hot or drop the
    backlog -- it keeps the sids queued and retries on its backoff interval."""
    monkeypatch.setattr(session_fts, "_BACKFILL_RETRY_INTERVAL", 0.05)
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: False)

    def boom(*a, **kw):
        raise AssertionError("must not call Ollama while unavailable")

    monkeypatch.setattr(session_fts, "_embed_texts", boom)

    conn = session_fts._get_connection()
    session_fts._init_db(conn)
    _insert_sdoc_row(conn, "sid-stuck")
    conn.commit()

    session_fts._run_embedding_backfill()
    time.sleep(0.3)

    assert session_fts._backfill_running, "loop should still be retrying, not exited"
    pending = [r[0] for r in conn.execute("SELECT sid FROM semb_pending")]
    assert pending == ["sid-stuck"]

    # Ollama recovers -- the same running loop should pick it up without a
    # fresh trigger.
    monkeypatch.setattr(session_fts, "_ollama_available", lambda: True)
    monkeypatch.setattr(
        session_fts, "_embed_texts",
        lambda texts, batch=32, timeout=None: [[1.0, 0.0] for _ in texts],
    )

    deadline = time.time() + 5.0
    while session_fts._backfill_running and time.time() < deadline:
        time.sleep(0.05)

    assert not session_fts._backfill_running
    assert conn.execute("SELECT COUNT(*) FROM semb_pending").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM semb WHERE sid = ?", ("sid-stuck",)).fetchone()[0] > 0


# --- MEMO-FIX-21: long sessions indexed as sections --------------------------

def _long_session_turns(n_turns: int, needle_turn: int, needle: str) -> list[dict]:
    """A session long enough that sdoc's capped assistant_text drops its
    middle, with `needle` said only once, by the assistant, at `needle_turn`."""
    filler = "routine refactor progress report with nothing notable " * 30
    turns = []
    for i in range(1, n_turns + 1):
        turns.append({"type": "user", "cwd": "/Users/test/long-repo",
                      "message": {"role": "user", "content": f"step {i}: keep going"}})
        text = filler + (f" also we discussed {needle} here" if i == needle_turn else "")
        turns.append({"type": "assistant", "message": {"role": "assistant", "content": text}})
    return turns


def test_mid_session_mention_is_searchable(fts_env):
    sid = "aaaaaaaa-0000-0000-0000-000000000021"
    path = fts_env["projects"] / "long-repo" / f"{sid}.jsonl"
    _write_claude_jsonl(path, sid, _long_session_turns(120, 60, "zanzibar spreadsheets"))

    parsed = session_fts.parse_claude(str(path))
    assert "zanzibar" not in parsed["assistant_text"], "fixture must exercise the dropped middle"
    assert parsed["sections"], "a capped session must be indexed as sections"

    res = session_fts.search_sessions("zanzibar spreadsheets")
    assert [r["session_id"] for r in res] == [sid]

    m = session_fts.section_matches("zanzibar spreadsheets", [sid])[sid]
    assert m["turn"] <= 60 <= m["turn_end"]
    assert "[zanzibar]" in m["snippet"].lower()


def test_sections_split_on_turn_count_and_compaction():
    stream = [("u", i, f"prompt {i}") for i in range(1, 121)]
    secs = session_fts._build_sections(stream)
    assert [s["turn0"] for s in secs] == [1, 51, 101]
    assert secs[-1]["turn1"] == 120

    stream = [("u", 1, "before"), ("b", 1, ""), ("u", 2, "after")]
    secs = session_fts._build_sections(stream)
    assert [s["body"] for s in secs] == ["before", "after"]


def test_task_text_is_searchable_even_in_short_sessions(fts_env):
    sid = "bbbbbbbb-0000-0000-0000-000000000021"
    path = fts_env["projects"] / "repo-t" / f"{sid}.jsonl"
    _write_claude_jsonl(path, sid, [
        {"type": "user", "cwd": "/Users/test/repo-t", "message": {"role": "user", "content": "plan the posts"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "TaskCreate", "input": {
                "subject": "SMB post", "description": "corner shop writing a marmalade campaign"}},
        ]}},
    ])
    res = session_fts.search_sessions("marmalade campaign")
    assert [r["session_id"] for r in res] == [sid]


def test_reindex_replaces_sections_without_duplicates(fts_env):
    sid = "cccccccc-0000-0000-0000-000000000021"
    path = fts_env["projects"] / "long-repo" / f"{sid}.jsonl"
    _write_claude_jsonl(path, sid, _long_session_turns(120, 60, "quokka"))
    session_fts.search_sessions("quokka", force_refresh=True)
    conn = session_fts._get_connection()
    n1 = conn.execute("SELECT COUNT(*) FROM ssec_map WHERE sid = ?", (sid,)).fetchone()[0]

    _write_claude_jsonl(path, sid, _long_session_turns(121, 60, "quokka"))
    session_fts.search_sessions("quokka", force_refresh=True)
    n2 = conn.execute("SELECT COUNT(*) FROM ssec_map WHERE sid = ?", (sid,)).fetchone()[0]
    rows = conn.execute("SELECT COUNT(*) FROM ssec").fetchone()[0]
    assert n1 == n2 == rows

    path.unlink()
    session_fts.search_sessions("quokka", force_refresh=True)
    assert conn.execute("SELECT COUNT(*) FROM ssec").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ssec_map").fetchone()[0] == 0


def test_section_migration_runs_in_background_never_inline(fts_env, monkeypatch):
    """A DB indexed before sections existed (user_version 0, big transcripts
    already in file_cache) gets re-parsed by the background sync; a request
    thread must not parse anything inline."""
    sid = "dddddddd-0000-0000-0000-000000000021"
    path = fts_env["projects"] / "long-repo" / f"{sid}.jsonl"
    _write_claude_jsonl(path, sid, _long_session_turns(120, 60, "okapi"))
    monkeypatch.setattr(session_fts, "_SECTION_MIGRATE_MIN_SIZE", 1000)
    session_fts.search_sessions("okapi", force_refresh=True)
    conn = session_fts._get_connection()
    # Simulate the pre-section DB state.
    conn.execute("DELETE FROM ssec")
    conn.execute("DELETE FROM ssec_map")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()

    started = []
    real_parse = session_fts._parse_file
    monkeypatch.setattr(session_fts, "_start_background_sync", lambda: started.append(1))
    parse_calls = []
    monkeypatch.setattr(session_fts, "_parse_file", lambda a: parse_calls.append(a))
    session_fts._last_sync_ts = 0.0
    session_fts.search_sessions("okapi")
    assert started and not parse_calls

    monkeypatch.setattr(session_fts, "_parse_file", real_parse)
    session_fts._sync_index(conn, force=True)  # what the background worker runs
    # Both migrations share one `PRAGMA user_version` counter and chain in
    # the same force=True sync, so a fully-migrated DB lands on the higher
    # (Kimi sid) version, not just the section one.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == session_fts.KIMI_SID_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM ssec_map WHERE sid = ?", (sid,)).fetchone()[0] > 0


def _write_kimi_wire(kimi_root: Path, bare_sid: str, prompt: str, reply: str, cwd: str = "/Users/test/kimi-proj"):
    """<kimi_root>/<project>/session_<uuid>/agents/main/wire.jsonl, matching
    the real ~/.kimi-code/sessions/*/session_<uuid> layout parse_kimi()
    expects (glob is kimi_root.glob('*/*/agents/main/wire.jsonl'))."""
    session_dir = kimi_root / "proj" / f"session_{bare_sid}"
    wire_dir = session_dir / "agents" / "main"
    wire_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "state.json").write_text(json.dumps({"cwd": cwd}), encoding="utf-8")
    lines = [
        json.dumps({"type": "turn.prompt", "time": 1000, "input": [{"type": "text", "text": prompt}]}),
        json.dumps({
            "type": "context.append_loop_event",
            "time": 1001,
            "event": {"type": "content.part", "part": {"type": "text", "text": reply}},
        }),
    ]
    (wire_dir / "wire.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_dir


def test_search_sessions_finds_kimi_session_normalizes_sid(fts_env):
    """MEMORY-18: Kimi's on-disk dir is `session_<uuid>`, but search_sessions()
    must return the bare UUID like every other harness."""
    bare_sid = "99999999-1111-2222-3333-444444444444"
    _write_kimi_wire(fts_env["kimi"], bare_sid, "debug the narwhal pipeline", "fixed the narwhal race condition")

    results = session_fts.search_sessions("narwhal race condition")
    assert len(results) > 0
    assert results[0]["session_id"] == bare_sid
    assert not results[0]["session_id"].startswith("session_")


def test_kimi_sid_migration_rekeys_existing_rows(fts_env):
    """A DB indexed before MEMORY-18 (user_version 1, Kimi rows keyed by the
    old `session_<uuid>` sid) gets re-parsed under the bare-UUID sid, and the
    stale prefixed row is gone."""
    bare_sid = "88888888-1111-2222-3333-444444444444"
    prefixed_sid = f"session_{bare_sid}"
    session_dir = _write_kimi_wire(fts_env["kimi"], bare_sid, "quokka migration test", "quokka migration done")
    wire_path = session_dir / "agents" / "main" / "wire.jsonl"

    conn = session_fts._get_connection()
    session_fts._init_db(conn)
    st = os.stat(wire_path)
    # Simulate a pre-MEMORY-18 DB: indexed under the old prefixed sid, section
    # migration already done (user_version 1).
    conn.execute(
        "INSERT INTO file_cache (path, sid, mtime, size, indexed, cwd, engine) VALUES (?, ?, ?, ?, 1, ?, ?)",
        (str(wire_path), prefixed_sid, st.st_mtime, st.st_size, "/Users/test/kimi-proj", "kimi"),
    )
    conn.execute(
        "INSERT INTO sdoc VALUES (?, ?, ?, ?, ?, ?, ?)",
        (prefixed_sid, "old title", "quokka migration test", "", "", "", ""),
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()

    session_fts._last_sync_ts = 0.0
    session_fts._sync_index(conn, force=True)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == session_fts.KIMI_SID_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM sdoc WHERE sid = ?", (prefixed_sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sdoc WHERE sid = ?", (bare_sid,)).fetchone()[0] == 1
    results = session_fts.search_sessions("quokka migration")
    assert results[0]["session_id"] == bare_sid


def test_candidate_files_default_window_covers_a_year_not_45_days(fts_env, monkeypatch):
    """MEMORY-19: a real hand-verified question's answer session (Codex, 68
    days old) was silently unindexable under the old 45-day default -- no
    ranking fix can recover a candidate _candidate_files() never returns.
    The default must cover at least ~68 days (measured) and still respect
    *some* cutoff (not literally unbounded) at ~a year+1."""
    monkeypatch.delenv("CCC_SESSION_FTS_DAYS", raising=False)

    recent_sid = "11111111-0000-0000-0000-000000000001"
    old_sid = "22222222-0000-0000-0000-000000000002"
    ancient_sid = "33333333-0000-0000-0000-000000000003"
    for sid, days_ago in ((recent_sid, 1), (old_sid, 68), (ancient_sid, 400)):
        f = fts_env["projects"] / "repo" / f"{sid}.jsonl"
        _write_claude_jsonl(f, sid, [
            {"type": "user", "cwd": "/repo", "message": {"role": "user", "content": "windowtest marker"}},
        ])
        ts = time.time() - days_ago * 86400
        os.utime(f, (ts, ts))

    found = {(eng, path) for eng, path, _mt, _sz in session_fts._candidate_files()}
    found_sids = {Path(p).stem for _eng, p in found}
    assert recent_sid in found_sids, "a 1-day-old session must always be a candidate"
    assert old_sid in found_sids, "a 68-day-old session regressed past the old 45-day cutoff"
    assert ancient_sid not in found_sids, "the window must still have *some* bound, not be unbounded"
