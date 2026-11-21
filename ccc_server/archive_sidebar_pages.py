# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Optional packed list projections of published cold archive rows."""

from collections import defaultdict
import hashlib
import json
import zlib

from ccc_server.archive_list_replay import pack_row_layouts


_BUCKETS = 128
_PAGE_MAX_BYTES = 4 << 20


class StoredRow(dict):
    """A cold sidebar projection with the canonical lane fallback."""

    __slots__ = ("cwd",)

    def __init__(self, fields, values, cwd):
        super().__init__(zip(fields, values))
        self.cwd = cwd

    def copy(self):
        return StoredRow(self.keys(), self.values(), self.cwd)


def setup(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sidebar_page_generation (
            variant TEXT PRIMARY KEY, cached_at REAL NOT NULL, version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sidebar_pages (
            variant TEXT NOT NULL, bucket INTEGER NOT NULL, version TEXT NOT NULL,
            fingerprint BLOB NOT NULL, payload BLOB NOT NULL,
            PRIMARY KEY(variant, bucket)
        ) WITHOUT ROWID;
        CREATE TRIGGER IF NOT EXISTS sidebar_page_insert
        AFTER INSERT ON rows BEGIN
            DELETE FROM sidebar_page_generation WHERE variant=NEW.variant;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_page_update
        AFTER UPDATE OF variant, identity, body, digest ON rows BEGIN
            DELETE FROM sidebar_page_generation WHERE variant=OLD.variant OR variant=NEW.variant;
            DELETE FROM sidebar_pages WHERE variant=OLD.variant AND bucket=OLD.rowid%128;
        END;
        CREATE TRIGGER IF NOT EXISTS sidebar_page_delete
        AFTER DELETE ON rows BEGIN
            DELETE FROM sidebar_page_generation WHERE variant=OLD.variant;
        END;
    """)


def _digest(records):
    return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).digest()


def _build_bucket(con, group, project):
    rowids = [rowid for _, rowid, _ in group]
    metadata = []
    projections = []
    fields_by_shape = {}
    for identity, body in _stored_bodies(con, rowids):
        row = json.loads(body)
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id") or row.get("id")
        if not isinstance(sid, str) or not sid or sid.startswith("#"):
            continue
        cwd = row.get("cwd")
        # The full list replaces these scalar values from live sidecars.
        row.update(archived=False, trashed=False, all_lane_override="")
        try:
            output = project(row, fields_by_shape)
        except (TypeError, ValueError, OverflowError):
            continue
        projections.append(output)
        metadata.append((identity, cwd))
    layouts, packed = pack_row_layouts(projections)
    entries = [(identity, layout, values, cwd)
               for (layout, values), (identity, cwd) in zip(packed, metadata)]
    data = json.dumps((layouts, entries), separators=(",", ":")).encode()
    return zlib.compress(data, 1) if len(data) <= _PAGE_MAX_BYTES else None


def _stored_bodies(con, rowids):
    for start in range(0, len(rowids), 256):
        batch = rowids[start:start + 256]
        marks = ",".join("?" for _ in batch)
        yield from con.execute(
            f"SELECT identity,body FROM rows WHERE rowid IN ({marks}) ORDER BY rowid", batch)


def publish(con, key, cached_at, version, hot_ids, project):
    """Repack only buckets whose canonical cold members changed."""
    groups = defaultdict(list)
    for rid, rowid, digest in con.execute("""
        SELECT identity,rowid,digest FROM rows WHERE variant=? ORDER BY rowid
    """, (key,)):
        if rid not in hot_ids:
            groups[rowid % _BUCKETS].append((rid, rowid, digest))

    previous = dict(con.execute(
        "SELECT bucket,fingerprint FROM sidebar_pages WHERE variant=? AND version=?",
        (key, version)))
    for bucket, group in groups.items():
        fingerprint = _digest(group)
        if previous.get(bucket) == fingerprint:
            continue
        payload = _build_bucket(con, group, project)
        if payload is None:
            con.execute("DELETE FROM sidebar_page_generation WHERE variant=?", (key,))
            return
        con.execute("INSERT OR REPLACE INTO sidebar_pages VALUES (?,?,?,?,?)",
                    (key, bucket, version, fingerprint, payload))
    for bucket in range(_BUCKETS):
        if bucket not in groups:
            con.execute("DELETE FROM sidebar_pages WHERE variant=? AND bucket=?", (key, bucket))
    con.execute("INSERT OR REPLACE INTO sidebar_page_generation VALUES (?,?,?)",
                (key, cached_at, version))


def _decode_page(payload):
    decoder = zlib.decompressobj()
    data = decoder.decompress(payload, _PAGE_MAX_BYTES + 1)
    if not decoder.eof or len(data) > _PAGE_MAX_BYTES:
        raise ValueError("invalid sidebar page size")
    layouts, entries = json.loads(data)
    rows = {rid: StoredRow(layouts[shape], fields, cwd)
            for rid, shape, fields, cwd in entries}
    return len(data) * 4 + len(entries) * 224 + len(payload), rows


def read(con, key, cached_at, version, current, max_bytes, page_cache):
    """Load a current projection, borrowing read-only rows from recent pages."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='sidebar_page_generation'").fetchone():
        return None
    if not con.execute("""
        SELECT 1 FROM sidebar_page_generation WHERE variant=? AND cached_at=? AND version=?
    """, (key, cached_at, version)).fetchone():
        return None

    decoded, pages = {}, {}
    weight = 0
    try:
        for payload, in con.execute(
            "SELECT payload FROM sidebar_pages WHERE variant=? AND version=?", (key, version),
        ):
            # A peer publication can invalidate the ordered layout while
            # leaving most pages byte-identical. Validate the new generation
            # and order in this transaction even when reusing decoded rows.
            page = page_cache.get(payload)
            if page is None:
                page = _decode_page(payload)
            size, page_rows = page
            weight += size
            if weight > max_bytes:
                return None
            pages[payload] = page
            decoded.update(page_rows)
    except (IndexError, TypeError, ValueError, zlib.error):
        return None

    ordered = list(con.execute("SELECT identity FROM rows WHERE variant=? ORDER BY position,rowid", (key,)))
    missing = [rid for rid, in ordered if rid not in current and rid not in decoded]
    if len(missing) > 32:
        return None
    if missing:
        marks = ",".join("?" for _ in missing)
        for rid, body in con.execute(
            f"SELECT identity,body FROM rows WHERE variant=? AND identity IN ({marks})",
            (key, *missing),
        ):
            decoded[rid] = json.loads(body)
    layout = []
    rows = []
    for rid, in ordered:
        hot = current.pop(rid, None)
        stored = None if hot is not None else decoded[rid]
        layout.append((rid, stored))
        rows.append(hot if hot is not None else stored)
    rows.extend(current.values())
    page_cache.clear()
    page_cache.update(pages)
    return rows, layout
