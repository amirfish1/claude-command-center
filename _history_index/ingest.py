"""Walk Claude Code and Codex transcript directories and index JSONL sessions.

We treat both runtimes uniformly downstream — every searchable message lands
in the same `messages` table. The two source roots:

- Claude Code: `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl` (top
  level) plus nested `subagents/agent-*.jsonl` for spawned subagents.
- Codex:      `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-<iso>-<uuid>.jsonl`.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from . import parse

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# Codex filenames embed the session UUID as the trailing dash-delimited group:
# rollout-2026-05-02T15-11-24-019deabf-13f9-7611-bc08-9873057cd8b7.jsonl
_CODEX_FILENAME_UUID_RE = re.compile(
    r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)


def iso_to_unix(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def find_jsonl_files(root: Path = CLAUDE_PROJECTS_DIR) -> Iterator[tuple[str, str]]:
    """Yield (jsonl_path, project_dir_name) tuples from the Claude Code store."""
    if not root.exists():
        return
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.rglob("*.jsonl")):
            yield str(jsonl), project_dir.name


def find_codex_jsonl_files(
    root: Path = CODEX_SESSIONS_DIR,
) -> Iterator[tuple[str, str]]:
    """Yield (jsonl_path, session_id) tuples from the Codex store. Session id
    is parsed from the filename — Codex embeds it as the last dash-delimited
    UUID group. Falls back to the bare stem if the filename isn't conventional.
    """
    if not root.exists():
        return
    for jsonl in sorted(root.rglob("*.jsonl")):
        m = _CODEX_FILENAME_UUID_RE.search(jsonl.name)
        session_id = m.group(1) if m else jsonl.stem
        yield str(jsonl), session_id


def _count_lines(path: str) -> int:
    with open(path, "rb") as fh:
        return sum(1 for _ in fh)


_INSERT_SQL = """
    INSERT INTO messages
        (uuid, session_id, parent_uuid, type, role, cwd, project_dir,
         git_branch, timestamp, ts_unix, version, slug, model,
         source_file, source_line, content)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _ingest_one_file(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    path: str,
    record_iter: Callable[[str, int], Iterator[dict]],
    label: str,
    stats: dict,
    verbose: bool,
) -> None:
    """Per-file ingest: skip if unchanged since last run; otherwise resume from
    `lines_indexed` and insert any new records. Shared by both Claude Code
    and Codex walkers; differences are isolated to `record_iter`.
    """
    st = os.stat(path)
    row = cur.execute(
        "SELECT lines_indexed, size, mtime FROM files WHERE path=?", (path,)
    ).fetchone()
    if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
        return

    start_line = row["lines_indexed"] if row else 0
    new_rows = 0
    try:
        cur.execute("BEGIN")
        for rec in record_iter(path, start_line):
            if rec.get("uuid") is None:
                continue
            ts_unix = iso_to_unix(rec.get("timestamp"))
            try:
                cur.execute(
                    _INSERT_SQL,
                    (
                        rec["uuid"],
                        rec["session_id"],
                        rec["parent_uuid"],
                        rec["type"],
                        rec["role"],
                        rec["cwd"],
                        rec["project_dir"],
                        rec["git_branch"],
                        rec["timestamp"],
                        ts_unix,
                        rec["version"],
                        rec["slug"],
                        rec["model"],
                        rec["source_file"],
                        rec["source_line"],
                        rec["content"],
                    ),
                )
                new_rows += 1
            except sqlite3.IntegrityError:
                pass  # duplicate uuid — already indexed in a prior run

        total_lines = _count_lines(path)
        cur.execute(
            """INSERT INTO files (path, size, mtime, lines_indexed, last_indexed_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 size=excluded.size, mtime=excluded.mtime,
                 lines_indexed=excluded.lines_indexed,
                 last_indexed_at=excluded.last_indexed_at""",
            (path, st.st_size, st.st_mtime, total_lines, time.time()),
        )
        conn.commit()
        stats["files_updated"] += 1
        stats["rows_inserted"] += new_rows
        if verbose and new_rows:
            print(f"[+{new_rows:5d}] {label}")
    except Exception as e:
        conn.rollback()
        if verbose:
            print(f"[ERROR] {path}: {e}")


def ingest(
    conn: sqlite3.Connection,
    root: Path = CLAUDE_PROJECTS_DIR,
    codex_root: Path = CODEX_SESSIONS_DIR,
    verbose: bool = True,
) -> dict:
    """Incrementally ingest both Claude Code and Codex transcripts. Returns
    stats aggregated across both sources.
    """
    stats = {"files_seen": 0, "files_updated": 0, "rows_inserted": 0}
    cur = conn.cursor()

    # --- Claude Code ---
    for path, project_dir in find_jsonl_files(root):
        stats["files_seen"] += 1

        def _claude_iter(p: str, sl: int, _pd: str = project_dir) -> Iterator[dict]:
            return parse.iter_records(p, _pd, sl)

        _ingest_one_file(
            conn,
            cur,
            path,
            _claude_iter,
            f"{project_dir}/{Path(path).name}",
            stats,
            verbose,
        )

    # --- Codex ---
    for path, session_id in find_codex_jsonl_files(codex_root):
        stats["files_seen"] += 1

        def _codex_iter(p: str, sl: int, _sid: str = session_id) -> Iterator[dict]:
            return parse.iter_records_codex(p, _sid, sl)

        _ingest_one_file(
            conn,
            cur,
            path,
            _codex_iter,
            f"codex/{Path(path).name}",
            stats,
            verbose,
        )

    # --- Refresh sessions aggregate ---
    cur.execute(
        """
        INSERT OR REPLACE INTO sessions
            (session_id, cwd, project_dir, first_ts, last_ts, message_count, slug)
        SELECT
            session_id,
            MAX(cwd),
            MAX(project_dir),
            MIN(timestamp),
            MAX(timestamp),
            COUNT(*),
            MAX(slug)
        FROM messages
        WHERE session_id IS NOT NULL
        GROUP BY session_id
        """
    )
    conn.commit()
    return stats
