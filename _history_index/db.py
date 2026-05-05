"""SQLite schema and connection helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path.home() / ".claude-index" / "index.db"


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec extension if available. Returns True if loaded."""
    try:
        import sqlite_vec  # type: ignore
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE,
    session_id TEXT,
    parent_uuid TEXT,
    type TEXT,
    role TEXT,
    cwd TEXT,
    project_dir TEXT,
    git_branch TEXT,
    timestamp TEXT,
    ts_unix REAL,
    version TEXT,
    slug TEXT,
    model TEXT,
    source_file TEXT,
    source_line INTEGER,
    content TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts_unix);
CREATE INDEX IF NOT EXISTS idx_messages_cwd ON messages(cwd);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    cwd UNINDEXED,
    project_dir UNINDEXED,
    git_branch UNINDEXED,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, cwd, project_dir, git_branch)
    VALUES (new.id, new.content, new.cwd, new.project_dir, new.git_branch);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, cwd, project_dir, git_branch)
    VALUES('delete', old.id, old.content, old.cwd, old.project_dir, old.git_branch);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, cwd, project_dir, git_branch)
    VALUES('delete', old.id, old.content, old.cwd, old.project_dir, old.git_branch);
    INSERT INTO messages_fts(rowid, content, cwd, project_dir, git_branch)
    VALUES (new.id, new.content, new.cwd, new.project_dir, new.git_branch);
END;

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER,
    mtime REAL,
    lines_indexed INTEGER NOT NULL DEFAULT 0,
    last_indexed_at REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT,
    project_dir TEXT,
    first_ts TEXT,
    last_ts TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    custom_title TEXT,
    slug TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    _try_load_vec(conn)
    return conn


def has_vec_extension(conn: sqlite3.Connection) -> bool:
    """Whether sqlite-vec is loaded on this connection."""
    try:
        conn.execute("SELECT vec_version()").fetchone()
        return True
    except sqlite3.OperationalError:
        return False


def ensure_vec_schema(conn: sqlite3.Connection, dim: int) -> None:
    """Create the vec0 virtual table for embeddings if missing.

    Also creates a small bookkeeping table for embedding state, so we can
    incrementally backfill without re-embedding what we already have.
    """
    if not has_vec_extension(conn):
        raise RuntimeError(
            "sqlite-vec extension not loaded. Install with `pip install sqlite-vec`."
        )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_vec USING vec0(
            embedding float[{dim}]
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings_state (
            message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            embedded_at REAL NOT NULL
        )
        """
    )
    conn.commit()
