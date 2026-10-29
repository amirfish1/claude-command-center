"""Regression test for CCC-615: title/name matches must outrank incidental
content mentions in history search.

A session whose FIRST user message (the display-title proxy) contains the
query term is about that topic and belongs on the first page — even when
hundreds of other sessions merely mention the term in passing. Before the
boost, a session titled "fix the twilio campaign" ranked #340 for 'twilio'
because BM25 scored only per-row snippets with no title signal.

Also covers the synthetic-injection guard: harness-injected user rows
(`<recommended_plugins>…`) must not shadow the real first user message.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pytest

import _history_index.search as history_search
from ccc_server import history_search as _ccc_history_search
from ccc_server import session_fts


def _build_index(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            uuid TEXT, session_id TEXT, type TEXT, role TEXT,
            cwd TEXT, project_dir TEXT, git_branch TEXT,
            timestamp TEXT, ts_unix REAL, model TEXT, slug TEXT,
            source_file TEXT, source_line INTEGER, content TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        """
    )
    rows = []
    mid = 0

    def add(session, ts, content, type_="user"):
        nonlocal mid
        mid += 1
        rows.append((
            mid, f"uuid-{mid}", session, type_, type_,
            "/Users/x/dev/proj", "proj", "main",
            "2026-07-16T10:00:00Z", 1784000000.0 + ts, "model-x", None,
            "transcript.jsonl", mid, content,
        ))

    # The titled session: first user message is about the topic, later turns
    # are not. A synthetic harness injection precedes the real first message
    # and must NOT shadow it.
    add("sess-title", 1, "<recommended_plugins> synthetic harness block")
    add("sess-title", 2, "I need help fixing the zephyr campaign for joyce")
    add("sess-title", 3, "looks good, ship it", "assistant")
    add("sess-title", 4, "done", "assistant")

    # Filler sessions: their TITLES are off-topic, but their later assistant
    # turns mention the term densely — raw bm25 outranks the titled session's
    # single topical row without the boost.
    for s in range(30):
        add(f"sess-filler-{s}", 10 + s * 10, "how do I center a div in css")
        for t in range(5):
            add(
                f"sess-filler-{s}", 10 + s * 10 + t + 1,
                f"zephyr mention number {t} zephyr zephyr padding content row",
                "assistant",
            )

    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
        [(r[0], r[14]) for r in rows],
    )
    con.commit()
    con.close()


class TestTitleBoost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "index.db"
        _build_index(db_path)
        self.con = sqlite3.connect(str(db_path))
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_titled_session_ranks_first_page(self):
        res = history_search.search(self.con, "zephyr", limit=5)
        self.assertTrue(res, "no results at all")
        titled = [r for r in res if r["session_id"] == "sess-title"]
        self.assertTrue(titled, "titled session missing from the first page")
        first = titled[0]
        self.assertIn("zephyr", first["snippet"])
        self.assertIn("campaign", first["snippet"])
        self.assertLess(
            res.index(first), 5,
            "titled session should be promoted to the first page",
        )

    def test_injection_does_not_shadow_real_title(self):
        res = history_search.search(self.con, "zephyr", limit=5)
        titled = [r for r in res if r["session_id"] == "sess-title"]
        self.assertTrue(titled)
        self.assertNotIn("recommended_plugins", titled[0]["snippet"])

    def test_content_hits_still_present(self):
        res = history_search.search(self.con, "zephyr", limit=10)
        fillers = [r for r in res if r["session_id"].startswith("sess-filler")]
        self.assertTrue(fillers, "incidental content hits should follow titles")


if __name__ == "__main__":
    unittest.main()


@pytest.fixture
def _sfts_title_env(tmp_path, monkeypatch):
    """MEMO-FIX-19: search_conversation_history no longer reads the vendored
    claude-index db (server._HISTORY_INDEX_PATH) at all -- it delegates to
    session_fts. Isolated env mirroring tests/test_session_fts.py's fts_env."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(tmp_path / "session_fts.sqlite"))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(tmp_path / "codex-empty"))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_DEVIN_TRANSCRIPTS_ROOT", str(tmp_path / "devin-empty"))
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")
    if hasattr(session_fts._tls, "conn") and session_fts._tls.conn:
        session_fts._tls.conn.close()
        session_fts._tls.conn = None
    session_fts._last_sync_ts = 0.0
    return projects_dir


def _write_titled_session(projects_dir, sid, custom_title, user_text, repo="proj"):
    d = projects_dir / repo
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    lines = [json.dumps({"type": "custom-title", "customTitle": custom_title})]
    lines.append(json.dumps({
        "type": "user", "cwd": str(d),
        "message": {"role": "user", "content": user_text},
    }))
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_search_conversation_history_also_boosts_titles(_sfts_title_env):
    """CCC-615, re-verified against the session_fts-backed path (MEMO-FIX-19):
    a session whose TITLE is about "zephyr" must outrank sessions where
    "zephyr" only appears buried, densely repeated, in assistant chatter --
    session_fts's BM25_WEIGHTS gives the title column 15x the weight of body
    text, so this should hold with no extra boost logic needed."""
    projects_dir = _sfts_title_env
    _write_titled_session(
        projects_dir, "sess-title-0001",
        "fix the zephyr campaign for joyce",
        "I need help fixing the zephyr campaign for joyce",
    )
    for s in range(15):
        sid = f"sess-filler-{s:04d}"
        d = projects_dir / "proj"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        lines = [json.dumps({
            "type": "user", "cwd": str(d),
            "message": {"role": "user", "content": "how do I center a div in css"},
        })]
        for t in range(5):
            lines.append(json.dumps({
                "type": "assistant", "cwd": str(d),
                "message": {"role": "assistant", "content": [{
                    "type": "text",
                    "text": f"zephyr mention number {t} zephyr zephyr padding content row",
                }]},
            }))
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = _ccc_history_search.search_conversation_history("zephyr", limit=5)
    res = out.get("results") or []
    assert res, "no results at all"
    session_ids = [r["session_id"] for r in res]
    assert "sess-title-0001" in session_ids, (
        "titled session missing from the first page: "
        f"got {session_ids}"
    )
    assert session_ids.index("sess-title-0001") == 0, (
        "titled session should rank ABOVE incidental content-only hits, "
        f"got order {session_ids}"
    )
