# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Transactionally indexed selection of the recent All-sidebar rows."""

from collections import defaultdict
import heapq
import json
import math
import sqlite3
import zlib


VERSION = 1
_RECENT_ROWS = 500
_MAX_BATCHED_EDGES = 8192
_MAX_EDGE_BYTES = 512
_MAX_PACKED_ROWS = 32768
_MAX_PACKED_BYTES = 8 << 20
_ORDERED_ROWS = """
    SELECT identity,sid,activity,pinned,working,codex
    FROM sidebar_preview_rows WHERE variant=? ORDER BY position,row_order
"""


def activity(row):
    """Sidebar activity includes ticket moves as well as transcript stamps."""
    newest = 0
    for key in ("modified", "mtime", "last_interacted", "last_activity", "last_mtime",
                "archived_at", "closed_at", "started_at"):
        value = (row or {}).get(key)
        if not value:
            continue
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(ts) and ts > 0:
            if ts > 100000000000:
                ts /= 1000
            if ts > newest:
                newest = ts
    try:
        moved_at = float(((row or {}).get("idea_ticket") or {}).get("moved_at") or 0)
        if math.isfinite(moved_at):
            newest = max(newest, moved_at)
    except (TypeError, ValueError):
        pass
    return newest


def _sid(row):
    return str(row.get("session_id") or row.get("id") or "")


def parent_ids(row, parents):
    return {str(value) for value in (
        row.get("parent_session_id"), row.get("hermes_parent_session_id"),
        row.get("continued_from_session_id"), row.get("hermes_continued_from"),
        parents.get(_sid(row)),
    ) if isinstance(value, str) and value}


def _working(row):
    return row.get("state") == "working" or row.get("codex_state") == "working"


def setup(con):
    """Create separate metadata tables so old canonical readers still work."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sidebar_preview_generation (
            variant TEXT PRIMARY KEY, cached_at REAL NOT NULL, version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sidebar_preview_rows (
            variant TEXT NOT NULL, identity TEXT NOT NULL, position INTEGER NOT NULL,
            row_order INTEGER NOT NULL, sid TEXT NOT NULL, activity REAL NOT NULL,
            pinned INTEGER NOT NULL, working INTEGER NOT NULL, codex INTEGER NOT NULL,
            PRIMARY KEY(variant, identity)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS sidebar_preview_order
            ON sidebar_preview_rows(variant, position, row_order);
        CREATE TABLE IF NOT EXISTS sidebar_preview_parents (
            variant TEXT NOT NULL, identity TEXT NOT NULL, parent TEXT NOT NULL,
            PRIMARY KEY(variant, identity, parent)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS sidebar_preview_parent_lookup
            ON sidebar_preview_parents(variant, parent, identity);
        CREATE TABLE IF NOT EXISTS sidebar_preview_packed (
            variant TEXT PRIMARY KEY, cached_at REAL NOT NULL, version INTEGER NOT NULL,
            body BLOB
        );
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_packed_insert
        AFTER INSERT ON sidebar_preview_rows BEGIN
            DELETE FROM sidebar_preview_packed WHERE variant=NEW.variant;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_packed_update
        AFTER UPDATE ON sidebar_preview_rows BEGIN
            DELETE FROM sidebar_preview_packed WHERE variant=OLD.variant OR variant=NEW.variant;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_packed_delete
        AFTER DELETE ON sidebar_preview_rows BEGIN
            DELETE FROM sidebar_preview_packed WHERE variant=OLD.variant;
        END;

        CREATE TRIGGER IF NOT EXISTS sidebar_preview_insert
        AFTER INSERT ON rows BEGIN
            DELETE FROM sidebar_preview_generation WHERE variant=NEW.variant;
            DELETE FROM sidebar_preview_rows WHERE variant=NEW.variant AND identity=NEW.identity;
            DELETE FROM sidebar_preview_parents WHERE variant=NEW.variant AND identity=NEW.identity;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_update
        AFTER UPDATE OF body, digest, sid, identity, variant ON rows BEGIN
            DELETE FROM sidebar_preview_generation WHERE variant=OLD.variant OR variant=NEW.variant;
            DELETE FROM sidebar_preview_rows
                WHERE (variant=OLD.variant AND identity=OLD.identity)
                   OR (variant=NEW.variant AND identity=NEW.identity);
            DELETE FROM sidebar_preview_parents
                WHERE (variant=OLD.variant AND identity=OLD.identity)
                   OR (variant=NEW.variant AND identity=NEW.identity);
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_move
        AFTER UPDATE OF position ON rows BEGIN
            UPDATE sidebar_preview_rows SET position=NEW.position
            WHERE variant=NEW.variant AND identity=NEW.identity;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_delete
        AFTER DELETE ON rows BEGIN
            DELETE FROM sidebar_preview_generation WHERE variant=OLD.variant;
            DELETE FROM sidebar_preview_rows WHERE variant=OLD.variant AND identity=OLD.identity;
            DELETE FROM sidebar_preview_parents WHERE variant=OLD.variant AND identity=OLD.identity;
        END;
    """)


def indexed(con, key):
    return {identity for identity, in con.execute(
        "SELECT identity FROM sidebar_preview_rows WHERE variant=?", (key,))}


def publish(con, key, identity, row=None):
    if row is None:
        body, = con.execute("SELECT body FROM rows WHERE variant=? AND identity=?",
                            (key, identity)).fetchone()
        row = json.loads(body)
    if not isinstance(row, dict):
        return
    sid = row.get("session_id") or row.get("id")
    if not isinstance(sid, str) or not sid:
        return
    try:
        stamp = activity(row)
    except (AttributeError, OverflowError):
        return
    con.execute("""
        INSERT INTO sidebar_preview_rows
        SELECT variant,identity,position,rowid,sid,?,?,?,? FROM rows
        WHERE variant=? AND identity=?
    """, (stamp, bool(row.get("pinned")), _working(row), row.get("engine") == "codex", key, identity))
    con.executemany("INSERT INTO sidebar_preview_parents VALUES (?,?,?)",
                    [(key, identity, parent) for parent in parent_ids(row, {})])


def finish_publication(con, key, cached_at):
    # An incremental/old writer may leave existing cold rows unindexed. The
    # ordinary list read remains available until the index covers that variant.
    counts = con.execute("""
        SELECT (SELECT COUNT(*) FROM rows WHERE variant=?),
               (SELECT COUNT(*) FROM sidebar_preview_rows WHERE variant=?)
    """, (key, key)).fetchone()
    if counts[0] == counts[1]:
        con.execute("INSERT OR REPLACE INTO sidebar_preview_generation VALUES (?,?,?)",
                    (key, cached_at, VERSION))
        _publish_packed(con, key, cached_at, counts[0])


def _publish_packed(con, key, cached_at, count):
    # The index itself is the source of truth. Its mutation triggers only
    # invalidate the packed copy when a new publication changes index rows.
    if con.execute("""
        UPDATE sidebar_preview_packed SET cached_at=? WHERE variant=? AND version=?
    """, (cached_at, key, VERSION)).rowcount:
        return
    body = _pack_rows(con, key) if count <= _MAX_PACKED_ROWS else None
    # Remember oversized generations so the detached worker does not retry
    # unchanged input on every background refresh.
    con.execute("INSERT OR REPLACE INTO sidebar_preview_packed VALUES (?,?,?,?)",
                (key, cached_at, VERSION, body))


def _pack_rows(con, key):
    rows = []
    text_chars = 0
    for row in con.execute(_ORDERED_ROWS, (key,)):
        text_chars += len(row[0]) + len(row[1])
        if text_chars > _MAX_PACKED_BYTES:
            return None
        rows.append(row)
    # Encode SQLite REAL values through Python's round-trip float serializer.
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    return zlib.compress(body, 1) if len(body) <= _MAX_PACKED_BYTES else None


def packed_current(con, key):
    # This check also accepts the oversized-generation marker.
    return con.execute("""
        SELECT 1 FROM sidebar_preview_packed JOIN sidebar_preview_generation
        USING (variant,cached_at,version) WHERE variant=? AND version=?
    """, (key, VERSION)).fetchone() is not None


def _ordered_rows(con, key):
    # One SQLite step avoids giving up the GIL once per index entry while a
    # simultaneous complete-history request projects archive rows.
    try:
        record = con.execute("""
            SELECT body FROM sidebar_preview_packed JOIN sidebar_preview_generation
            USING (variant,cached_at,version)
            WHERE variant=? AND version=? AND length(body)<=?
        """, (key, VERSION, _MAX_PACKED_BYTES)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: sidebar_preview_packed" not in str(exc):
            raise
        record = None
    if record:
        try:
            decoder = zlib.decompressobj()
            body = decoder.decompress(record[0], _MAX_PACKED_BYTES + 1)
            if decoder.eof and len(body) <= _MAX_PACKED_BYTES:
                rows = json.loads(body)
                if isinstance(rows, list) and len(rows) <= _MAX_PACKED_ROWS:
                    return rows
        except (ValueError, zlib.error):
            pass
    return con.execute(_ORDERED_ROWS, (key,))


class Preview:
    """A view of one consistent canonical generation plus current hot rows."""

    def __init__(self, con, key, overrides, deleted_codex, overlay_cold):
        self.con = con
        self.key = key
        self.overlay_cold = overlay_cold
        self.items = []
        self.sources = {}
        self.cold_positions = {}
        # Use the same persisted order as the canonical override merge.
        for identity, sid, stamp, pinned, working, codex in _ordered_rows(con, key):
            live = overrides.pop(identity, None)
            if live is not None:
                self.append([live], deleted_codex=deleted_codex)
            elif not codex or sid not in deleted_codex:
                offset = len(self.items)
                self.items.append((sid, stamp, pinned, working, identity))
                self.cold_positions[identity] = offset
        self.append(overrides.values(), deleted_codex=deleted_codex)

    def append(self, rows, *, deleted_codex=()):
        for row in rows:
            sid = _sid(row)
            if row.get("engine") == "codex" and sid in deleted_codex:
                continue
            self.sources[len(self.items)] = row
            self.items.append((sid, activity(row), bool(row.get("pinned")), _working(row), None))

    def _row(self, offset):
        row = self.sources.get(offset)
        if row is None:
            identity = self.items[offset][-1]
            body, = self.con.execute("SELECT body FROM rows WHERE variant=? AND identity=?",
                                     (self.key, identity)).fetchone()
            row = self.sources[offset] = self.overlay_cold(json.loads(body))
        return row

    def _indexed_children(self):
        # A bounded read avoids stepping SQLite once per selected session,
        # including each newly discovered family member. For larger graphs
        # retain the indexed lookup only for the parents we actually visit.
        packed, = self.con.execute("""
            SELECT json_group_array(CASE
                WHEN length(CAST(parent AS BLOB)) + length(CAST(identity AS BLOB)) <= ?
                THEN json_array(parent, identity) END)
            FROM (SELECT parent,identity FROM sidebar_preview_parents WHERE variant=? LIMIT ?)
        """, (_MAX_EDGE_BYTES, self.key, _MAX_BATCHED_EDGES + 1)).fetchone()
        edges = json.loads(packed)
        if len(edges) > _MAX_BATCHED_EDGES or None in edges:
            return None
        children = defaultdict(list)
        for parent, identity in edges:
            offset = self.cold_positions.get(identity)
            if offset is not None:
                children[parent].append(offset)
        return children

    def select(self, parents, ideas):
        recent = set(heapq.nlargest(_RECENT_ROWS, range(len(self.items)), key=lambda i: self.items[i][1]))
        children = defaultdict(list)
        positions = {}
        historical = self.cold_positions
        indexed_children = self._indexed_children()
        for offset, (sid, _, pinned, _, identity) in enumerate(self.items):
            positions[sid] = offset
            if pinned:
                recent.add(offset)
            if identity is None:
                for parent in parent_ids(self.sources[offset], parents):
                    children[parent].append(offset)
            elif (parent := parents.get(sid)) and isinstance(parent, str):
                children[parent].append(offset)
        selected = sorted(recent)
        seen = {self.items[offset][0] for offset in selected}
        cursor = 0
        while cursor < len(selected):
            offset = selected[cursor]
            cursor += 1
            row = self._row(offset)
            sid = _sid(row)
            relatives = [positions[parent] for parent in parent_ids(row, parents) if parent in positions]
            if indexed_children is None:
                cold_children = (historical[child] for child, in self.con.execute("""
                    SELECT identity FROM sidebar_preview_parents WHERE variant=? AND parent=?
                """, (self.key, sid)) if child in historical)
            else:
                cold_children = indexed_children.get(sid, ())
            relatives.extend(sorted(set(children[sid]).union(cold_children)))
            for member in relatives:
                member_id = self.items[member][0]
                if member_id in seen:
                    continue
                entry = self._row(member)
                ticket = ideas.get(member_id) or entry.get("idea_ticket") or {}
                if not entry.get("trashed") and not entry.get("explicitly_archived", ticket.get("explicitly_archived")):
                    seen.add(member_id)
                    selected.append(member)
        return [self._row(offset) for offset in selected]

    def counts(self):
        working = {}
        active = 0
        for sid, _, _, is_working, _ in self.items:
            active += is_working
            if sid:
                working.setdefault(sid, is_working)
        return active, working
