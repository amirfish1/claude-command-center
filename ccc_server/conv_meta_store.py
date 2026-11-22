# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Path-keyed persistence for rebuildable transcript metadata."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def database_path(legacy):
    legacy = Path(legacy)
    return legacy.with_suffix(".sqlite3" if legacy.suffix != ".sqlite3" else ".rows.sqlite3")


def open_reader(legacy):
    return sqlite3.connect(database_path(legacy).resolve().as_uri() + "?mode=ro",
                           uri=True, timeout=0.5, check_same_thread=False)


def ready(conn, version):
    return conn.execute("SELECT version FROM initialized WHERE version=?", (version,)).fetchone()


def decode(body):
    meta = json.loads(body)
    if isinstance(meta.get("cache_key"), list):
        meta["cache_key"] = tuple(meta["cache_key"])
    return meta


def get(conn, version, transcript_path):
    found = conn.execute("SELECT body FROM metadata WHERE version=? AND path=?",
                         (version, transcript_path)).fetchone()
    return decode(found[0]) if found else None


def lineage_ready(conn):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lineage'"
    ).fetchone() is not None


def get_lineage(conn, version, transcript_path):
    found = conn.execute("SELECT body FROM lineage WHERE version=? AND path=?",
                         (version, transcript_path)).fetchone()
    return json.loads(found[0]) if found else None


def load(conn, version):
    return {path: decode(body) for path, body in conn.execute(
        "SELECT path,body FROM metadata WHERE version=?", (version,))}


def save(legacy, version, entries, *, migration=False, legacy_lineage=None):
    database = database_path(legacy)
    database.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(database, os.O_CREAT | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(database, timeout=1)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS initialized (version INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS metadata (
                version INTEGER NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (version, path)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS lineage (
                version INTEGER NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (version, path)
            ) WITHOUT ROWID;
        """)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if migration and ready(conn, version):
                return
            conn.executemany("""
                INSERT OR REPLACE INTO metadata VALUES(?,?,?)
            """, ((version, path, json.dumps(meta, separators=(",", ":")))
                  for path, meta in entries.items() if meta is not None))
            conn.executemany("DELETE FROM metadata WHERE version=? AND path=?",
                             ((version, path) for path, meta in entries.items() if meta is None))
            conn.executemany("INSERT OR IGNORE INTO lineage VALUES(?,?,?)", (
                (version, path, json.dumps(head, separators=(",", ":")))
                for path, head in (legacy_lineage or {}).items()
            ))
            conn.executemany("INSERT OR REPLACE INTO lineage VALUES(?,?,?)", (
                (version, path, json.dumps(meta["lineage_head"], separators=(",", ":")))
                for path, meta in entries.items() if meta and isinstance(meta.get("lineage_head"), dict)
            ))
            conn.executemany("DELETE FROM lineage WHERE version=? AND path=?",
                             ((version, path) for path, meta in entries.items() if meta is None))
            conn.execute("INSERT OR IGNORE INTO initialized VALUES(?)", (version,))
