"""Regression test: CCC's vendored `_history_index` ingester (the one driven
by IndexerManager.maybe_ingest on a 120s timer — see manager.py) must purge
CCC scratch one-shot sessions (auto-titler, queue-brief, Ask spawns) after
every ingest, the same way the canonical `claude-index ingest`
(dev/tools/indexing) already does.

Before this fix, `_history_index/ingest.py` had no purge step at all, so any
scratch session file created since the last canonical purge got re-indexed
by this vendored copy and stayed — quietly undoing the canonical purge every
two minutes and polluting "where did I work on X" search results with
self-referential scratch noise.
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

import _history_index.db as history_db
import _history_index.ingest as history_ingest


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _claude_session(path: Path, sid: str, cwd: str, text: str) -> None:
    _jsonl(path, [
        {"uuid": f"{sid}-u1", "sessionId": sid, "type": "user", "cwd": cwd,
         "timestamp": "2026-09-03T12:00:00Z", "message": {"role": "user", "content": text}},
        {"uuid": f"{sid}-a1", "sessionId": sid, "type": "assistant", "cwd": cwd,
         "timestamp": "2026-09-03T12:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Sure, doing " + text}]}},
    ])


class ScratchPurgeTest(unittest.TestCase):
    def test_ingest_purges_scratch_sessions(self, tmp_path=None):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        projects = tmp / "projects"
        _claude_session(projects / "-Users-x-Apps-ccc" / "real.jsonl", "real-1",
                        "/Users/x/Apps/ccc", "rebuild the ask tab")
        _claude_session(projects / "-Users-x--claude-command-center-scratch" / "scratch.jsonl",
                        "scratch-1", "/Users/x/.claude/command-center/scratch",
                        "auto-title this session about the ask tab")

        conn = history_db.connect(tmp / "index.db")
        stats = history_ingest.ingest(conn, root=projects, codex_root=tmp / "nocodex", verbose=False)

        self.assertEqual(stats["scratch_purged"]["messages"], 2)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='scratch-1'").fetchone()[0], 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='scratch-1'").fetchone()[0], 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='real-1'").fetchone()[0], 1)

        # Simulate the 120s-timer re-run: no new files, nothing should
        # reappear (the `files` row for the scratch file is kept on purpose
        # so it's skipped, not re-added).
        stats2 = history_ingest.ingest(conn, root=projects, codex_root=tmp / "nocodex", verbose=False)
        self.assertEqual(stats2["rows_inserted"], 0)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='scratch-1'").fetchone()[0], 0)

    def test_purge_scratch_keeps_files_row(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        conn = history_db.connect(tmp / "index.db")
        conn.execute(
            "INSERT INTO messages (uuid, session_id, cwd, project_dir, timestamp, content) "
            "VALUES ('u1', 's1', '/Users/x/.claude/command-center/scratch', 'proj', 't', 'hi')")
        conn.execute(
            "INSERT INTO sessions (session_id, cwd, project_dir) VALUES "
            "('s1', '/Users/x/.claude/command-center/scratch', 'proj')")
        conn.execute("INSERT INTO files (path, size, mtime, lines_indexed) VALUES ('f1', 10, 1.0, 2)")
        conn.commit()
        stats = history_ingest.purge_scratch(conn)
        self.assertEqual(stats, {"messages": 1, "sessions": 1})
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM files WHERE path='f1'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
