"""Sidebar search finds a session from a fragment of its id, at any age."""

import pathlib
import sqlite3

import pytest

from ccc_server import recent_search, session_fts

SID = "ab59b381-0e83-4a56-bfbb-2ccb6732602b"
OTHER = "11112222-3333-4444-5555-666677778888"
ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def index(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE file_cache (path, sid, mtime, size, indexed, cwd, engine)")
    conn.execute("INSERT INTO file_cache VALUES ('/t/a.jsonl', ?, 1000, 1, 1, '/repo', 'claude')", (SID,))
    conn.execute("INSERT INTO file_cache VALUES ('/t/b.jsonl', ?, 2000, 1, 1, '/repo', 'codex')", (OTHER,))
    monkeypatch.setattr(session_fts, "_get_connection", lambda: conn)
    monkeypatch.setattr(session_fts, "search_sessions_enriched", lambda *a, **k: [
        {"session_id": OTHER, "_source": "recall"}, {"session_id": SID, "_source": "recall"}])
    return conn


@pytest.mark.parametrize("query", ["ab59", "b381-0e83", "AB59B3", "b3810e83", SID])
def test_fragment_finds_the_session(index, query):
    hits = session_fts.session_id_hits(query)
    assert [h["session_id"] for h in hits] == [SID]
    assert hits[0]["_source"] == "id" and hits[0]["cwd"] == "/repo"


@pytest.mark.parametrize("query", ["ab5", "zzzz", "ab59%", "%%%%", "____", ""])
def test_non_id_queries_match_nothing(index, query):
    assert session_fts.session_id_hits(query) == []


def test_prefix_beats_a_newer_substring_match(index):
    index.execute("INSERT INTO file_cache VALUES ('/t/c.jsonl', 'ffffab59-0000', 9000, 1, 1, '', 'claude')")
    assert [h["session_id"] for h in session_fts.session_id_hits("ab59")] == [SID, "ffffab59-0000"]


def test_unindexed_rows_stay_hidden(index):
    index.execute("UPDATE file_cache SET indexed = 0")
    assert session_fts.session_id_hits("ab59") == []


def test_sidebar_gets_id_hits_first_without_a_duplicate(index):
    rows = recent_search.search_recent_sessions("ab59", match_ids=True)["results"]
    assert [(r["session_id"], r["_source"]) for r in rows] == [(SID, "id"), (OTHER, "recall")]


def test_other_callers_do_not_get_id_hits(index):
    rows = recent_search.search_recent_sessions("ab59")["results"]
    assert {r["_source"] for r in rows} == {"recall"}


def test_client_checks_the_id_before_any_filter_and_ranks_it_top():
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function filterConversations(q)"):]
    assert body.index("_sessionIdSearchRank(c, idNeedle)") < body.index("Recent-only filter")
    assert "if (source === 'id') return 4;" in app
