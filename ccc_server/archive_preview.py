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
_PACKED_PAGE_SHIFT = 9
_PACKED_PAGE_SIZE = 1 << _PACKED_PAGE_SHIFT
_MAX_MEMO_PAGES = 128
_packed_page_memo = {}
_ORDERED_ROWS = """
    SELECT identity,sid,activity,pinned,working,codex
    FROM sidebar_preview_rows WHERE variant=? ORDER BY position,row_order
"""


def ticket_activity(ticket):
    try:
        moved_at = float((ticket or {}).get("moved_at") or 0)
    except (TypeError, ValueError):
        return 0
    return moved_at if math.isfinite(moved_at) and moved_at > 0 else 0


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
    ticket = (row or {}).get("idea_ticket")
    return max(newest, ticket_activity(ticket)) if ticket else newest


def _sid(row):
    return str(row.get("session_id") or row.get("id") or "")


def parent_ids(row, parents, *, sid=None):
    parent = row.get("parent_session_id")
    hermes_parent = row.get("hermes_parent_session_id")
    origin = row.get("continued_from_session_id")
    hermes_origin = row.get("hermes_continued_from")
    graph_parent = parents.get(_sid(row) if sid is None else sid)
    if not (parent or hermes_parent or origin or hermes_origin or graph_parent):
        return ()
    return {str(value) for value in (parent, hermes_parent, origin, hermes_origin, graph_parent)
            if isinstance(value, str) and value}


def _working(row):
    return row.get("state") == "working" or row.get("codex_state") == "working"


def setup(con):
    """Create separate metadata tables so old canonical readers still work."""
    con.executescript(f"""
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
        CREATE TABLE IF NOT EXISTS sidebar_preview_pack_manifest (
            variant TEXT PRIMARY KEY, row_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sidebar_preview_pack_pages (
            variant TEXT NOT NULL, page INTEGER NOT NULL, body BLOB,
            PRIMARY KEY(variant, page)
        ) WITHOUT ROWID;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_page_insert
        AFTER INSERT ON sidebar_preview_rows
        WHEN EXISTS (SELECT 1 FROM sidebar_preview_pack_manifest WHERE variant=NEW.variant)
        BEGIN
            INSERT INTO sidebar_preview_pack_pages VALUES
                (NEW.variant, NEW.position >> {_PACKED_PAGE_SHIFT}, NULL)
                ON CONFLICT(variant, page) DO UPDATE SET body=NULL;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_page_update
        AFTER UPDATE ON sidebar_preview_rows BEGIN
            INSERT INTO sidebar_preview_pack_pages
                SELECT OLD.variant, OLD.position >> {_PACKED_PAGE_SHIFT}, NULL
                WHERE EXISTS (SELECT 1 FROM sidebar_preview_pack_manifest WHERE variant=OLD.variant)
                UNION
                SELECT NEW.variant, NEW.position >> {_PACKED_PAGE_SHIFT}, NULL
                WHERE EXISTS (SELECT 1 FROM sidebar_preview_pack_manifest WHERE variant=NEW.variant)
                ON CONFLICT(variant, page) DO UPDATE SET body=NULL;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_preview_page_delete
        AFTER DELETE ON sidebar_preview_rows
        WHEN EXISTS (SELECT 1 FROM sidebar_preview_pack_manifest WHERE variant=OLD.variant)
        BEGIN
            INSERT INTO sidebar_preview_pack_pages VALUES
                (OLD.variant, OLD.position >> {_PACKED_PAGE_SHIFT}, NULL)
                ON CONFLICT(variant, page) DO UPDATE SET body=NULL;
        END;
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
    if count > _MAX_PACKED_ROWS:
        con.execute("DELETE FROM sidebar_preview_pack_manifest WHERE variant=?", (key,))
        con.execute("DELETE FROM sidebar_preview_pack_pages WHERE variant=?", (key,))
    else:
        initialized = con.execute(
            "SELECT 1 FROM sidebar_preview_pack_manifest WHERE variant=?", (key,),
        ).fetchone()
        if initialized:
            pages = con.execute("""
                SELECT page FROM sidebar_preview_pack_pages WHERE variant=? AND body IS NULL
            """, (key,)).fetchall()
        else:
            pages = con.execute("""
                SELECT DISTINCT position >> ? FROM sidebar_preview_rows WHERE variant=?
            """, (_PACKED_PAGE_SHIFT, key)).fetchall()
        for page, in pages:
            body = _pack_page(con, key, page)
            if body is None:
                con.execute("DELETE FROM sidebar_preview_pack_pages WHERE variant=? AND page=?",
                            (key, page))
            else:
                con.execute("INSERT OR REPLACE INTO sidebar_preview_pack_pages VALUES (?,?,?)",
                            (key, page, body))
        con.execute("INSERT OR REPLACE INTO sidebar_preview_pack_manifest VALUES (?,?)",
                    (key, count))
    # Old readers use the row index when the single-blob body is absent.
    con.execute("INSERT OR REPLACE INTO sidebar_preview_packed VALUES (?,?,?,?)",
                (key, cached_at, VERSION, None))


def _pack_page(con, key, page):
    rows = []
    text_chars = 0
    start = page * _PACKED_PAGE_SIZE
    for row in con.execute("""
        SELECT identity,sid,activity,pinned,working,codex
        FROM sidebar_preview_rows WHERE variant=? AND position BETWEEN ? AND ?
        ORDER BY position,row_order
    """, (key, start, start + _PACKED_PAGE_SIZE - 1)):
        text_chars += len(row[0]) + len(row[1])
        if text_chars > _MAX_PACKED_BYTES:
            return b""
        rows.append(row)
    if not rows:
        return None
    # Encode SQLite REAL values through Python's round-trip float serializer.
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    return zlib.compress(body, 1) if len(body) <= _MAX_PACKED_BYTES else b""


def packed_current(con, key):
    # This check also accepts the oversized-generation marker.
    return con.execute("""
        SELECT 1 FROM sidebar_preview_packed JOIN sidebar_preview_generation
        USING (variant,cached_at,version) WHERE variant=? AND version=?
    """, (key, VERSION)).fetchone() is not None


def _decode_packed(body, limit):
    if not body:
        raise ValueError("missing packed preview body")
    decoder = zlib.decompressobj()
    data = decoder.decompress(body, limit + 1)
    if not decoder.eof or len(data) > limit:
        raise ValueError("invalid packed preview size")
    rows = json.loads(data)
    if not isinstance(rows, list):
        raise ValueError("invalid packed preview rows")
    return rows, len(data)


def _read_packed_rows(con, key):
    global _packed_page_memo

    marker = con.execute("""
        SELECT body FROM sidebar_preview_packed JOIN sidebar_preview_generation
        USING (variant,cached_at,version)
        WHERE variant=? AND version=? AND (body IS NULL OR length(body)<=?)
    """, (key, VERSION, _MAX_PACKED_BYTES)).fetchone()
    if not marker:
        return None
    if marker[0] is not None:
        rows, _ = _decode_packed(marker[0], _MAX_PACKED_BYTES)
        return rows if len(rows) <= _MAX_PACKED_ROWS else None
    manifest = con.execute(
        "SELECT row_count FROM sidebar_preview_pack_manifest WHERE variant=?", (key,),
    ).fetchone()
    if not manifest or not 0 <= manifest[0] <= _MAX_PACKED_ROWS:
        return None
    rows = []
    remaining = _MAX_PACKED_BYTES
    previous = _packed_page_memo
    memo = {}
    memo_bytes = 0
    for page_id, body in con.execute("""
        SELECT page, CASE WHEN length(body)<=? THEN body END
        FROM sidebar_preview_pack_pages WHERE variant=? ORDER BY page
    """, (_MAX_PACKED_BYTES, key)):
        entry = previous.get(page_id)
        # Read the current compressed bytes from the pinned transaction.
        # A peer can publish a changed page under the same cached_at.
        if entry is None or entry[0] != body:
            decoded, size = _decode_packed(body, remaining)
            entry = body, tuple(map(tuple, decoded)), size
        _, page, size = entry
        if size > remaining or len(rows) + len(page) > manifest[0]:
            return None
        rows.extend(page)
        remaining -= size
        if len(memo) < _MAX_MEMO_PAGES and memo_bytes + len(body) <= _MAX_PACKED_BYTES:
            memo[page_id] = entry
            memo_bytes += len(body)
    if len(rows) != manifest[0]:
        return None
    _packed_page_memo = memo
    return rows


def _ordered_rows(con, key):
    # The read transaction guards the generation, pages, and fallback index.
    try:
        rows = _read_packed_rows(con, key)
        if rows is not None:
            return rows
    except (TypeError, ValueError, RecursionError, zlib.error):
        pass
    except sqlite3.OperationalError as exc:
        if str(exc) not in (
            "no such table: sidebar_preview_packed",
            "no such table: sidebar_preview_pack_manifest",
            "no such table: sidebar_preview_pack_pages",
        ):
            raise
    return con.execute(_ORDERED_ROWS, (key,))


class Preview:
    """A view of one consistent canonical generation plus current hot rows."""

    def __init__(self, con, key, overrides, deleted_codex, overlay_row):
        self.con = con
        self.key = key
        self.overlay_row = overlay_row
        self.resolved = {}
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
        self.indexed_count = len(self.items)

    def append(self, rows, *, deleted_codex=()):
        for row in rows:
            sid = _sid(row)
            if row.get("engine") == "codex" and sid in deleted_codex:
                continue
            self.sources[len(self.items)] = row
            self.items.append((sid, activity(row), bool(row.get("pinned")), _working(row), None))

    def _row(self, offset):
        resolved = self.resolved.get(offset)
        if resolved is not None:
            return resolved
        row = self.sources.get(offset)
        if row is None:
            identity = self.items[offset][-1]
            body, = self.con.execute("SELECT body FROM rows WHERE variant=? AND identity=?",
                                     (self.key, identity)).fetchone()
            row = json.loads(body)
        # Selection only needs fresh lifecycle flags for rows it inspects.
        # Engine overlays appended after the index already have their flags.
        if offset < self.indexed_count:
            row = self.overlay_row(row)
        self.resolved[offset] = row
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
