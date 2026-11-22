# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Optional packed list projections of published cold archive rows."""

from collections import defaultdict
import hashlib
import json
import sqlite3
import zlib

from ccc_server.archive_list_replay import pack_row_layouts
from ccc_server.sqlite_batch import fetch_rows


_BUCKETS = 128
MAX_INCREMENTAL_CHANGES = _BUCKETS
_PAGE_MAX_BYTES = 4 << 20
_ORDER_BATCH_ITEMS = 32768
_MAX_UNPACKED_ROWS = 1024
_UNPACKED_QUERY_ROWS = 64
_UNPACKED_BATCH_BODY_LIMIT = 32 << 10


class StoredRow(dict):
    """A cold sidebar projection with the canonical lane fallback."""

    __slots__ = ("cwd",)

    def __init__(self, fields, values, cwd):
        super().__init__(zip(fields, values))
        self.cwd = cwd

    def copy(self):
        # Copy the dict directly. Lifecycle overlays may update thousands of
        # packed rows, and rebuilding each one from zipped fields is costly.
        clone = StoredRow.__new__(StoredRow)
        clone.update(self)
        clone.cwd = self.cwd
        return clone


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
        CREATE TABLE IF NOT EXISTS sidebar_page_hot_basis (
            variant TEXT PRIMARY KEY, cached_at REAL NOT NULL,
            version TEXT NOT NULL, rowids TEXT NOT NULL
        );
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
        # Live sidecars still decide the final values. Keep the last
        # published values so matching rows can be borrowed without copying.
        row.setdefault("archived", False)
        row.setdefault("trashed", False)
        row.setdefault("all_lane_override", "")
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


def hot_basis(con, key, cached_at, version):
    """Read the hot membership paired with the previously published pages."""
    record = con.execute("""
        SELECT b.rowids FROM sidebar_page_hot_basis b
        JOIN sidebar_page_generation g USING (variant, cached_at, version)
        WHERE b.variant=? AND b.cached_at=? AND b.version=?
    """, (key, cached_at, version)).fetchone()
    if not record:
        return None
    try:
        return set(json.loads(record[0]))
    except (TypeError, ValueError):
        return None


def publish(con, key, cached_at, version, hot_ids, project, *,
            hot_rowids=None, previous_hot=None, previous_changed=(), inserted=()):
    """Repack buckets affected by changed bodies or hot membership."""
    buckets = range(_BUCKETS)
    params = (key,)
    bucket_filter = ""
    if previous_hot is not None:
        # Replaced hot rows often acquire a new rowid. Neither end of a hot
        # replacement was in a cold page, so it does not dirty a bucket.
        dirty = (previous_hot ^ hot_rowids).difference(previous_changed, inserted)
        dirty.update(set(previous_changed) - previous_hot)
        dirty.update(set(inserted) - hot_rowids)
        buckets = sorted({rowid % _BUCKETS for rowid in dirty})
        if buckets:
            marks = ",".join("?" for _ in buckets)
            bucket_filter = f" AND rowid%{_BUCKETS} IN ({marks})"
            params = (key, *buckets)

    groups = defaultdict(list)
    if buckets:
        for rid, rowid, digest in con.execute(
            "SELECT identity,rowid,digest FROM rows WHERE variant=?"
            + bucket_filter + " ORDER BY rowid", params,
        ):
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
    for bucket in buckets:
        if bucket not in groups:
            con.execute("DELETE FROM sidebar_pages WHERE variant=? AND bucket=?", (key, bucket))
    if hot_rowids is None:
        con.execute("DELETE FROM sidebar_page_hot_basis WHERE variant=?", (key,))
    else:
        con.execute("INSERT OR REPLACE INTO sidebar_page_hot_basis VALUES (?,?,?,?)",
                    (key, cached_at, version, json.dumps(sorted(hot_rowids), separators=(",", ":"))))
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


def _ordered_identities(con, key):
    sql = "SELECT identity FROM rows WHERE variant=? ORDER BY position,rowid"
    # Moving the canonical order out of SQLite one row at a time yields the
    # GIL at each step. Read it in one bounded result under the same snapshot.
    try:
        encoded, = con.execute(
            f"SELECT json_group_array(identity) FROM ({sql} LIMIT ?)",
            (key, _ORDER_BATCH_ITEMS + 1),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if str(exc) != "no such function: json_group_array":
            raise
    else:
        identities = json.loads(encoded)
        if len(identities) <= _ORDER_BATCH_ITEMS:
            return identities
    return [rid for rid, in con.execute(sql, (key,))]


def _unpacked_bodies(con, key, missing, check_client):
    for start in range(0, len(missing), _UNPACKED_QUERY_ROWS):
        if check_client is not None:
            check_client()
        batch = missing[start:start + _UNPACKED_QUERY_ROWS]
        marks = ",".join("?" for _ in batch)
        # Batch the small bodies to avoid releasing the GIL at every SQLite
        # row. Read large bodies individually to bound the aggregate buffer.
        records = fetch_rows(
            con, f"""SELECT identity, CASE
                WHEN length(CAST(body AS BLOB)) <= ? THEN body END AS body
                FROM rows WHERE variant=? AND identity IN ({marks})""",
            (_UNPACKED_BATCH_BODY_LIMIT, key, *batch), ("identity", "body"))
        for rid, body in records:
            if body is None:
                if check_client is not None:
                    check_client()
                body, = con.execute(
                    "SELECT body FROM rows WHERE variant=? AND identity=?", (key, rid)
                ).fetchone()
            yield rid, body


def read(con, key, cached_at, version, current, max_bytes, page_cache, *, check_client=None):
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
            if check_client is not None:
                check_client()
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

    ordered = _ordered_identities(con, key)
    missing = [rid for rid in ordered if rid not in current and rid not in decoded]
    # A stale publication after restart can have hundreds of formerly hot
    # rows. Recover them within the byte budget without unpacking the rest
    # of the archive or exceeding SQLite's older parameter limit.
    if len(missing) > _MAX_UNPACKED_ROWS:
        return None
    for rid, body in _unpacked_bodies(con, key, missing, check_client):
        weight += 4 * len(body) + 224 + 4 * len(rid)
        if weight > max_bytes:
            return None
        decoded[rid] = json.loads(body)
    layout = []
    rows = []
    for rid in ordered:
        hot = current.pop(rid, None)
        stored = None if hot is not None else decoded[rid]
        layout.append((rid, stored))
        rows.append(hot if hot is not None else stored)
    rows.extend(current.values())
    page_cache.clear()
    page_cache.update(pages)
    return rows, layout
