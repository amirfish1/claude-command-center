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
import sqlite3
import tempfile
import unittest
from pathlib import Path

import _history_index.search as history_search


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

    def test_server_lexical_path_also_boosts_titles(self):
        """The /api/search-history default (lexical) path gets the same boost —
        it has its own BM25 SQL, separate from the vendored search."""
        import server
        orig_path = server._HISTORY_INDEX_PATH
        orig_conn = server._history_conn
        try:
            server._HISTORY_INDEX_PATH = Path(self.con.execute(
                "PRAGMA database_list").fetchone()[2])
            server._history_conn = None
            out = server.search_conversation_history("zephyr", limit=5)
            res = out.get("results") or []
            titled = [r for r in res if r.get("session_id") == "sess-title"]
            self.assertTrue(titled, "titled session missing via server lexical path")
            self.assertLess(res.index(titled[0]), 5)
        finally:
            server._HISTORY_INDEX_PATH = orig_path
            server._history_conn = orig_conn


if __name__ == "__main__":
    unittest.main()
