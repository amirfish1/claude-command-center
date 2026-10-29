# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unit tests for the Hermes messages_fts search channel (multi-machine S9):
ccc_server.hermes.search_hermes_messages()/hermes_meta_for_sids(), and their
wiring into ccc_server.session_fts.search_sessions()/search_sessions_enriched()
as a third ranking channel alongside FTS and the optional embeddings channel.
Closes TODO(hermes-search) in ccc_server/hermes.py.
"""

import sqlite3

import pytest

import server
from ccc_server import hermes, session_fts


def _write_hermes_db(path, messages, sessions=None):
    """Build a minimal Hermes state.db: `sessions` + `messages` + a real FTS5
    `messages_fts` table whose rowid matches messages.id, mirroring the real
    ~/.hermes/state.db schema (verified against a live install: messages_fts
    is a plain single-column fts5 table Hermes populates with explicit
    rowid=messages.id inserts, not a contentless/external-content table)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                cwd TEXT,
                updated_at REAL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                timestamp REAL NOT NULL
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        """)
        for sid, cwd, updated_at in (sessions or []):
            con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (sid, cwd, updated_at))
        for mid, sid, role, content, ts in messages:
            con.execute(
                "INSERT INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (mid, sid, role, content, ts),
            )
            con.execute("INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", (mid, content))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def hermes_env(tmp_path, monkeypatch):
    db = tmp_path / "hermes" / "state.db"
    monkeypatch.setattr(server, "HERMES_HOME", tmp_path / "hermes")
    monkeypatch.setattr(server, "HERMES_STATE_DB", db)
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "hermes" / "profiles")
    hermes._HERMES_ID_CACHE.update(key=None, ids=set())
    hermes._core._HERMES_DB_INDEX.update(key=None, by_session={})
    return db


def test_search_hermes_messages_finds_and_ranks_by_bm25(hermes_env):
    _write_hermes_db(
        hermes_env,
        messages=[
            (1, "sess-a", "user", "the frobnicator needs recalibration urgently", 1000.0),
            (2, "sess-b", "assistant", "unrelated weather chat", 1001.0),
            (3, "sess-c", "user", "frobnicator", 1002.0),
        ],
        sessions=[("sess-a", "/home/hermes", 1000.0), ("sess-c", "", 1002.0)],
    )

    hits = hermes.search_hermes_messages("frobnicator")

    sids = [h["session_id"] for h in hits]
    assert "sess-a" in sids
    assert "sess-c" in sids
    assert "sess-b" not in sids
    for h in hits:
        assert h["score"] <= 0  # bm25() is negative-is-better in sqlite


def test_search_hermes_messages_empty_query_and_no_db_degrade_to_empty(hermes_env):
    assert hermes.search_hermes_messages("") == []
    assert hermes.search_hermes_messages("anything") == []  # no db written yet


def test_search_hermes_messages_skips_corrupt_db_without_raising(hermes_env, monkeypatch):
    hermes_env.parent.mkdir(parents=True, exist_ok=True)
    hermes_env.write_bytes(b"not a sqlite file")
    assert hermes.search_hermes_messages("frobnicator") == []


def test_hermes_meta_for_sids_is_bounded_by_requested_sids(hermes_env):
    _write_hermes_db(
        hermes_env,
        messages=[(1, "sess-a", "user", "hello", 500.0)],
        sessions=[("sess-a", "/home/hermes/work", 500.0), ("sess-other", "/tmp", 999.0)],
    )

    meta = hermes.hermes_meta_for_sids(["sess-a", "sess-missing"])

    assert meta["sess-a"] == {"path": "", "cwd": "/home/hermes/work", "engine": "hermes", "mtime": 500.0}
    assert "sess-other" not in meta  # never requested, never returned
    assert "sess-missing" not in meta  # requested but absent -- no fabricated row


def test_hermes_meta_for_sids_empty_input(hermes_env):
    assert hermes.hermes_meta_for_sids([]) == {}


@pytest.fixture
def fts_env(tmp_path, monkeypatch, hermes_env):
    """session_fts isolation (mirrors tests/test_session_fts.py's fixture)
    plus the hermes_env fixture above, so search_sessions() only ever sees
    the fake Hermes db this file writes."""
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    projects_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)

    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(tmp_path / "session_fts.sqlite"))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_DEVIN_TRANSCRIPTS_ROOT", str(tmp_path / "devin-empty"))
    monkeypatch.setenv("CCC_HARVESTED_ROOT", str(tmp_path / "harvested-empty"))
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")

    if hasattr(session_fts._tls, "conn") and session_fts._tls.conn:
        session_fts._tls.conn.close()
        session_fts._tls.conn = None
    session_fts._last_sync_ts = 0.0
    session_fts._ollama_state["ts"] = 0.0
    session_fts._ollama_state["ok"] = False
    session_fts._vec_cache["sids"] = []
    session_fts._vec_cache["vecs"] = []
    session_fts._vec_cache["sig"] = None


def test_search_sessions_includes_hermes_only_sid_via_rrf(fts_env, hermes_env):
    """A Hermes session with no sdoc/file_cache row at all (CCC never parses
    Hermes transcripts into sdoc) must still surface in search_sessions()
    once Hermes' own messages_fts matches it -- the RRF channel wiring in
    ccc_server/session_fts.py's search_sessions()."""
    _write_hermes_db(
        hermes_env,
        messages=[(1, "hermes-sess-1", "user", "zorblatt telemetry spike", 2000.0)],
        sessions=[("hermes-sess-1", "", 2000.0)],
    )

    results = session_fts.search_sessions("zorblatt telemetry")

    assert any(r["session_id"] == "hermes-sess-1" for r in results)


def test_search_sessions_enriched_labels_hermes_hit_and_has_snippet(fts_env, hermes_env):
    _write_hermes_db(
        hermes_env,
        messages=[(1, "hermes-sess-2", "user", "quixotic pipeline throughput regression", 3000.0)],
        sessions=[("hermes-sess-2", "/home/hermes/repo", 3000.0)],
    )

    rows = session_fts.search_sessions_enriched("quixotic pipeline")

    hit = next((r for r in rows if r["session_id"] == "hermes-sess-2"), None)
    assert hit is not None
    assert hit["type"] == "hermes"
    assert hit["cwd"] == "/home/hermes/repo"
    assert hit["snippet"]  # falls back to Hermes' own messages_fts snippet()


def test_search_sessions_with_no_hermes_db_is_unaffected(fts_env, hermes_env):
    # No state.db written: the channel must degrade to empty, not raise.
    assert session_fts.search_sessions("anything at all") == []
