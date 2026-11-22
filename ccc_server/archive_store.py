# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Per-row archive persistence; short windows never decode cold row bodies.

The database format is independent of the transcript row-shaping version.
Transactions retain newer variants and update only changed row bodies. The
small JSON snapshot remains a fallback, not the canonical historical store.
"""
from concurrent.futures import Future
import hashlib
import copy
from contextlib import contextmanager
import json
import math
import marshal
import os
from pathlib import Path
import sqlite3
import shutil
import sys
import tempfile
import threading
import time
from types import MappingProxyType

from .bounded_cache import ByteBudgetCache

HOT_SECONDS = 7 * 86400

# The peer inventory never consumes transcript/parser detail. Publish its
# projection in the same transaction as each canonical row, so polling peers
# does not repeatedly decode the complete historical payload.
INVENTORY_FIELDS = (
    'id', 'session_id', 'engine', 'source', 'jsonl_path', 'mtime', 'timestamp',
    'session_cwd', 'cwd', 'effective_branch', 'branch', 'is_live', 'model',
    'parent_session_id', 'claude_auth_failed', 'display_name', 'first_message',
)
INVENTORY_VERSION = '1:'
_INVENTORY_CACHE = ByteBudgetCache(max_bytes=64 << 20, max_entries=2)
_INVENTORY_FLIGHT_LOCK = threading.Lock()
_INVENTORY_FLIGHTS = {}

# The attention poll needs classification inputs, not parser/tool histories.
# Keep the projection transactionally paired with the canonical row digest.
ATTENTION_VERSION = '1:'
_ATTENTION_CACHE = ByteBudgetCache(max_bytes=192 << 20, max_entries=32768)
ATTENTION_FIELDS = (
    'archived', 'backlog_type', 'display_name', 'engine', 'first_message',
    'folder_label', 'folder_path', 'gh_in_progress', 'gh_state', 'has_commit',
    'has_edit', 'has_push', 'id', 'is_live', 'issue_labels', 'issue_number',
    'jsonl_path', 'last_assistant_text', 'last_event_type', 'linked_issue',
    'modified', 'mtime', 'needs_approval', 'needs_approval_message', 'pending_file',
    'pending_tool', 'question_preamble', 'question_text', 'question_waiting',
    'session_cwd', 'session_id', 'session_state', 'sidecar_in_flight',
    'sidecar_status', 'source', 'stale_tool_age_s', 'stale_tool_call',
    'subagent_in_flight_count', 'tail_issue_number', 'verified',
)


def attention_row(row):
    return {name: row[name] for name in ATTENTION_FIELDS if name in row}


def attention_candidate(row):
    """Keep rows that can produce a card in any scope of the attention feed."""
    # Keep this superset in sync with server._classify_attention. Lifecycle
    # flags can change after publication, so they cannot exclude a candidate.
    return bool(row.get('is_live') or row.get('source') == 'backlog'
                or row.get('stale_tool_call') or row.get('has_push')
                or row.get('has_edit') or row.get('has_commit'))


def inventory_row(row):
    result = {name: row[name] for name in INVENTORY_FIELDS if name in row}
    if isinstance(result.get('first_message'), str):
        result['first_message'] = result['first_message'][:160]
    return result


def activity(row):
    newest = 0.0
    for key in ('modified', 'mtime', 'last_interacted', 'last_activity', 'last_mtime',
                'archived_at', 'closed_at', 'started_at'):
        try:
            stamp = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(stamp):
            newest = max(newest, stamp / 1000 if stamp > 100000000000 else stamp)
    return newest


def identity(row, index=0):
    if isinstance(row, _RowIndexEntry):
        return row.rid
    sid = row.get('session_id') or row.get('id')
    return json.dumps([row.get('engine'), row.get('source'), sid or f'#{index}',
                       row.get('jsonl_path')], separators=(',', ':'))


def sticky(row, *, stamp=None):
    # Preserve the existing sidebar exceptions, including imported Hermes
    # history. Unknown timestamps cannot safely be classified as old.
    return bool(row.get('pinned') or row.get('is_live')
                or not (activity(row) if stamp is None else stamp)
                or (row.get('engine') or row.get('source') or '').lower() == 'hermes')


def hot_rows(rows, now=None):
    cutoff = (time.time() if now is None else now) - HOT_SECONDS
    by_sid = {str(r.get('session_id') or r.get('id') or ''): r for r in rows}
    # Unique session IDs already distinguish the rows. Only archives with
    # ambiguous or missing IDs need to serialize their compound identities.
    unique_sids = len(by_sid) == len(rows) and '' not in by_sid
    indexed = (by_sid.items() if unique_sids else
               [(identity(row, index), row) for index, row in enumerate(rows)])
    keep = set()
    for rid, row in indexed:
        stamp = activity(row)
        if sticky(row, stamp=stamp) or stamp >= cutoff:
            keep.add(rid)
    pending = [row for rid, row in indexed if rid in keep]
    for row in pending:
        parent_sid = str(row.get('parent_session_id') or row.get('hermes_parent_session_id') or '')
        parent = by_sid.get(parent_sid)
        if parent:
            parent_id = parent_sid if unique_sids else identity(parent)
            if parent_id not in keep:
                keep.add(parent_id)
                pending.append(parent)
    return [row for rid, row in indexed if rid in keep]


def database_path(cache_file):
    return Path(cache_file).with_suffix('.sqlite3')


def _connect(cache_file, *, write=False, shared_reader=False):
    target = database_path(cache_file)
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(target, 0o600)
        con = sqlite3.connect(target, timeout=0.5)
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
            CREATE TABLE IF NOT EXISTS variants (
                key TEXT PRIMARY KEY, cached_at REAL NOT NULL, metadata TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rows (
                variant TEXT NOT NULL, identity TEXT NOT NULL, sid TEXT NOT NULL,
                parent TEXT NOT NULL, position INTEGER NOT NULL,
                activity REAL NOT NULL, sticky INTEGER NOT NULL,
                digest TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (variant, identity)
            );
            CREATE INDEX IF NOT EXISTS rows_activity ON rows(variant, activity);
            CREATE INDEX IF NOT EXISTS rows_sid ON rows(variant, sid);
            CREATE INDEX IF NOT EXISTS rows_position ON rows(variant, position);
            CREATE TABLE IF NOT EXISTS session_inventory (
                variant TEXT NOT NULL, identity TEXT NOT NULL,
                digest TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (variant, identity)
            );
            CREATE TABLE IF NOT EXISTS session_attention (
                variant TEXT NOT NULL, identity TEXT NOT NULL,
                digest TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (variant, identity)
            );
            CREATE TABLE IF NOT EXISTS attention_variants (
                key TEXT PRIMARY KEY, cached_at REAL NOT NULL,
                schema_version INTEGER, build_id TEXT, version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS row_projection_versions (
                variant TEXT PRIMARY KEY, cached_at REAL NOT NULL,
                inventory TEXT NOT NULL, attention TEXT NOT NULL
            );
        ''')
        return con
    # Read-only connections never create an empty store that hides the JSON
    # fallback if a previous migration was interrupted.
    return sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True,
                           timeout=0.5, check_same_thread=not shared_reader)


def save(cache_file, snapshot, *, complete=False, sidebar_key=None, sidebar_pages=None,
         base_entry=None):
    """Publish variants atomically. Return the count of changed row bodies.

    Only complete builder snapshots may remove absent rows. Hot snapshots
    and stale workers cannot erase cold history or a newer publication.
    With a base entry, publish only against that generation and return whether
    the publication succeeded.
    """
    changed = 0
    con = _connect(cache_file, write=True)
    try:
        from ccc_server import archive_preview, archive_sidebar_pages
        if sidebar_key is not None:
            archive_preview.setup(con)
        if sidebar_pages is not None:
            archive_sidebar_pages.setup(con)
        con.execute('BEGIN IMMEDIATE')
        from ccc_server.archive_snapshot import _entries
        for key, entry in _entries(snapshot).items():
            stamp = float(entry.get('cached_at') or 0)
            previous = con.execute('SELECT cached_at,metadata FROM variants WHERE key=?', (key,)).fetchone()
            if base_entry is not None and (
                    previous is None or previous[0] != base_entry.get("cached_at")):
                return False
            old_version = json.loads(previous[1]).get('schema_version') if previous else None
            new_version = snapshot.get('schema_version')
            downgrade = isinstance(old_version, int) and isinstance(new_version, int) and old_version > new_version
            if downgrade or (previous and previous[0] >= stamp):
                if base_entry is not None:
                    return False
                continue
            existing = (base_entry or {}).get("_row_index")
            if existing is None:
                existing = {rid: (digest, position) for rid, digest, position in con.execute(
                    'SELECT identity,digest,position FROM rows WHERE variant=?', (key,))}
            projections_current = base_entry is not None and con.execute("""
                SELECT 1 FROM row_projection_versions
                WHERE variant=? AND cached_at=? AND inventory=? AND attention=?
            """, (key, previous[0], INVENTORY_VERSION, ATTENTION_VERSION)).fetchone()
            if projections_current:
                inventory = attention = None
            else:
                inventory = dict(con.execute(
                    'SELECT identity,digest FROM session_inventory WHERE variant=?', (key,)))
                attention = dict(con.execute(
                    'SELECT identity,digest FROM session_attention WHERE variant=?', (key,)))
            preview_current = base_entry is not None and key == sidebar_key and con.execute("""
                SELECT 1 FROM sidebar_preview_generation WHERE variant=? AND cached_at=? AND version=?
            """, (key, previous[0], archive_preview.VERSION)).fetchone()
            preview_ids = archive_preview.indexed(con, key) if key == sidebar_key and not preview_current else None
            previous_hot = None
            previous_changed, inserted = set(), set()
            if base_entry is not None and complete and key == sidebar_key and sidebar_pages is not None:
                previous_hot = archive_sidebar_pages.hot_basis(con, key, previous[0], sidebar_pages[0])
            base_rows = {identity(row, index): row
                         for index, row in enumerate((base_entry or {}).get('conversations', ()))}
            seen = set()
            for index, row in enumerate(entry.get('conversations') or []):
                if not isinstance(row, dict):
                    continue
                rid = identity(row, index)
                seen.add(rid)
                if rid in existing and rid in base_rows and (
                        row is base_rows[rid] or row == base_rows[rid]):
                    digest, position = existing[rid]
                    if ((projections_current or (inventory.get(rid) == INVENTORY_VERSION + digest
                                                 and attention.get(rid) == ATTENTION_VERSION + digest))
                            and (preview_ids is None or rid in preview_ids)):
                        if position != index:
                            con.execute('UPDATE rows SET position=? WHERE variant=? AND identity=?',
                                        (index, key, rid))
                        continue
                    if isinstance(row, _RowIndexEntry):
                        body, = con.execute('SELECT body FROM rows WHERE variant=? AND identity=?',
                                            (key, rid)).fetchone()
                        row = json.loads(body)
                body = json.dumps(row, separators=(',', ':'), sort_keys=True)
                digest = hashlib.sha256(body.encode()).hexdigest()
                same_body = existing.get(rid, (None,))[0] == digest
                attention_digest = ATTENTION_VERSION + digest
                if (not same_body if projections_current else attention.get(rid) != attention_digest):
                    projected = json.dumps(attention_row(row), separators=(',', ':'), sort_keys=True)
                    con.execute('INSERT OR REPLACE INTO session_attention VALUES(?,?,?,?)',
                                (key, rid, attention_digest, projected))
                inventory_digest = INVENTORY_VERSION + digest
                if (not same_body if projections_current else inventory.get(rid) != inventory_digest):
                    projected = json.dumps(inventory_row(row), separators=(',', ':'), sort_keys=True)
                    con.execute('INSERT OR REPLACE INTO session_inventory VALUES(?,?,?,?)',
                                (key, rid, inventory_digest, projected))
                if same_body:
                    if existing[rid][1] != index:
                        con.execute('UPDATE rows SET position=? WHERE variant=? AND identity=?', (index, key, rid))
                    if preview_ids is not None and rid not in preview_ids:
                        archive_preview.publish(con, key, rid)
                    continue
                row_activity = activity(row)
                if len(inserted) >= archive_sidebar_pages.MAX_INCREMENTAL_CHANGES:
                    previous_hot = None
                if previous_hot is not None and rid in existing:
                    old_rowid, = con.execute(
                        'SELECT rowid FROM rows WHERE variant=? AND identity=?', (key, rid),
                    ).fetchone()
                    previous_changed.add(old_rowid)
                write = con.execute('INSERT OR REPLACE INTO rows VALUES(?,?,?,?,?,?,?,?,?)', (
                    key, rid, str(row.get('session_id') or row.get('id') or ''),
                    str(row.get('parent_session_id') or row.get('hermes_parent_session_id') or ''),
                    index, row_activity, int(sticky(row, stamp=row_activity)), digest, body,
                ))
                if previous_hot is not None:
                    inserted.add(write.lastrowid)
                if key == sidebar_key:
                    archive_preview.publish(con, key, rid, row)
                changed += 1
            if complete:
                removed = existing.keys() - seen
                if len(removed) + len(inserted) > archive_sidebar_pages.MAX_INCREMENTAL_CHANGES:
                    previous_hot = None
                if previous_hot is not None:
                    for rid in removed:
                        old_rowid, = con.execute(
                            'SELECT rowid FROM rows WHERE variant=? AND identity=?', (key, rid),
                        ).fetchone()
                        previous_changed.add(old_rowid)
                con.executemany('DELETE FROM rows WHERE variant=? AND identity=?',
                                ((key, rid) for rid in removed))
                inventory_keys = existing.keys() if projections_current else inventory.keys()
                attention_keys = existing.keys() if projections_current else attention.keys()
                con.executemany('DELETE FROM session_inventory WHERE variant=? AND identity=?',
                                ((key, rid) for rid in inventory_keys - seen))
                con.executemany('DELETE FROM session_attention WHERE variant=? AND identity=?',
                                ((key, rid) for rid in attention_keys - seen))
                con.execute('INSERT OR REPLACE INTO row_projection_versions VALUES(?,?,?,?)',
                            (key, stamp, INVENTORY_VERSION, ATTENTION_VERSION))
            metadata = {k: v for k, v in snapshot.items() if k != 'entries'}
            metadata['entry'] = {k: v for k, v in entry.items() if k != 'conversations'}
            con.execute('INSERT OR REPLACE INTO variants VALUES(?,?,?)',
                        (key, stamp, json.dumps(metadata, separators=(',', ':'))))
            con.execute('INSERT OR REPLACE INTO attention_variants VALUES(?,?,?,?,?)',
                        (key, stamp, new_version, snapshot.get('build_id'), ATTENTION_VERSION))
            if key == sidebar_key:
                archive_preview.finish_publication(con, key, stamp)
                if complete and sidebar_pages is not None:
                    version, project = sidebar_pages
                    if base_entry is None:
                        hot = {identity(row) for row in hot_rows(entry.get('conversations') or [], now=stamp)}
                        archive_sidebar_pages.publish(con, key, stamp, version, hot, project)
                    else:
                        hot_rows_by_id = dict(con.execute(
                            _HOT_ROW_QUERY.format(columns="r.identity,r.rowid"), _hot_params(key, stamp)))
                        archive_sidebar_pages.publish(
                            con, key, stamp, version, hot_rows_by_id, project,
                            hot_rowids=set(hot_rows_by_id.values()), previous_hot=previous_hot,
                            previous_changed=previous_changed, inserted=inserted,
                        )
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return True if base_entry is not None else changed


class _AttentionReader:
    """Keep one read-only connection so data_version can guard the row order."""

    def __init__(self):
        self.lock = threading.Lock()
        self.conn = None
        self.identity = None
        self.manifests = {False: object(), True: object()}

    def _close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.identity = None
        for manifest in self.manifests.values():
            _ATTENTION_CACHE.pop(manifest)

    def read(self, cache_file, key, schema_version, build_id, *, candidates_only=False):
        with self.lock:
            target = database_path(cache_file).resolve()
            try:
                stat = target.stat()
            except FileNotFoundError:
                self._close()
                return None
            identity = (str(target), stat.st_dev, stat.st_ino)
            if identity != self.identity:
                self._close()
                self.conn = _connect(cache_file, shared_reader=True)
                self.identity = identity

            conn = self.conn
            try:
                # Do not leave a read transaction open between calls. A
                # committed write from any writer, even one that leaves the
                # snapshot header untouched, invalidates this manifest.
                version = conn.execute('PRAGMA data_version').fetchone()[0]
                guard = (version, key, schema_version, build_id, ATTENTION_VERSION)
                manifest = self.manifests[candidates_only]
                cached = _ATTENTION_CACHE.get(manifest)
                if cached is not None and cached[0] == guard:
                    _, timestamp, keys, positions = cached
                    values = _ATTENTION_CACHE.get_many(keys)
                    if all(value is not None for value in values):
                        return timestamp, keys, values, positions

                with conn:
                    conn.execute('BEGIN')
                    snapshot = _read_attention(conn, key, (*identity, key), schema_version, build_id)
                # A publication between the version read and the transaction
                # leaves this memo tagged with an obsolete version. The next
                # reader will validate again rather than replaying stale rows.
                if snapshot is None:
                    return None
                timestamp, keys, values = snapshot
                positions = None
                if candidates_only:
                    # An inactive archive row can have a live override at the
                    # same position. Keep every position without copying every
                    # historical row for each feed refresh.
                    positions = MappingProxyType({
                        row_key[-2]: i for i, row_key in enumerate(keys)
                    })
                    selected = []
                    for row_key, value in zip(keys, values):
                        row, nested = value
                        # Legacy non-scalar flags can also be truthy.
                        if attention_candidate(row) or (
                                nested is not None and attention_candidate(marshal.loads(nested))):
                            selected.append((row_key, value))
                    keys = tuple(row_key for row_key, _ in selected)
                    values = [value for _, value in selected]
                if len(keys) < _ATTENTION_CACHE.max_entries:
                    weight = 512 + sum(128 + 4 * (len(k[-2]) + len(k[-1])) for k in keys)
                    if positions is not None:
                        weight += sum(128 + 4 * len(rid) for rid in positions)
                    _ATTENTION_CACHE.put(manifest, (guard, timestamp, keys, positions), weight)
                return timestamp, keys, values, positions
            except sqlite3.Error:
                self._close()
                raise


_ATTENTION_READER = _AttentionReader()


def _read_attention(conn, key, namespace, schema_version, build_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='attention_variants'").fetchone():
        return None
    header = conn.execute('SELECT cached_at,schema_version,build_id,version '
                          'FROM attention_variants WHERE key=?', (key,)).fetchone()
    if not header or header[1:] != (schema_version, build_id, ATTENTION_VERSION):
        return None
    current = conn.execute('SELECT cached_at FROM variants WHERE key=?', (key,)).fetchone()
    if not current or current[0] != header[0]:
        return None
    # Validate and collect order in one index traversal. Missing projections
    # must invalidate warm decoded rows rather than shortening the feed.
    records = conn.execute('''SELECT r.identity,a.digest,a.digest=?||r.digest
        FROM rows r LEFT JOIN session_attention a
        ON a.variant=r.variant AND a.identity=r.identity
        WHERE r.variant=? ORDER BY r.position''', (ATTENTION_VERSION, key)).fetchall()
    if any(not valid for _, _, valid in records):
        return None
    keys = tuple((*namespace, rid, digest) for rid, digest, _ in records)
    values = _ATTENTION_CACHE.get_many(keys)
    missing = [row_key[-2] for row_key, value in zip(keys, values) if value is None]
    if missing:
        # The bodies and their validation share the same read transaction.
        bodies = conn.execute('''SELECT identity,digest,body FROM session_attention
            WHERE variant=? AND identity IN (SELECT value FROM json_each(?))''',
                              (key, json.dumps(missing)))
        loaded = {}
        for rid, digest, body in bodies:
            row = json.loads(body)
            nested = {name: item for name, item in row.items() if isinstance(item, (dict, list))}
            # The marshal bytes come only from JSON decoded here and stay
            # in memory. Placeholders preserve field order on replay.
            encoded = marshal.dumps(nested, 2) if nested else None
            for name in nested:
                row[name] = None
            value = (row, encoded)
            loaded[rid] = value
            _ATTENTION_CACHE.put((*namespace, rid, digest), value, 4 * (len(rid) + len(body)))
        values = [loaded[row_key[-2]] if value is None else value
                  for row_key, value in zip(keys, values)]
    return header[0], keys, values


def load_attention(cache_file, key, *, schema_version, build_id, candidates_only=False):
    """Load classifier inputs, with the original positions for a sparse read."""
    snapshot = _ATTENTION_READER.read(
        cache_file, key, schema_version, build_id, candidates_only=candidates_only)
    if snapshot is None:
        return None
    timestamp, keys, values, positions = snapshot
    decoded = []
    for row_key, (row, nested) in zip(keys, values):
        # Callers can modify their own copy, including structured state.
        result = row.copy()
        if nested is not None:
            result.update(marshal.loads(nested))
        decoded.append((row_key[-2], result))
    result = {'cached_at': timestamp, 'rows': decoded}
    if positions is not None:
        result['positions'] = positions
    return result


def _read_inventory_generation(con, key, cache_key, activity_key):
    missing = con.execute('''SELECT 1 FROM rows r LEFT JOIN session_inventory i
        ON i.variant=r.variant AND i.identity=r.identity
        WHERE r.variant=? AND (i.digest IS NULL OR i.digest!=?||r.digest) LIMIT 1''',
                          (key, INVENTORY_VERSION)).fetchone()
    if missing:
        return None
    bodies = con.execute('''SELECT i.identity,i.body FROM rows r JOIN session_inventory i
        ON i.variant=r.variant AND i.identity=r.identity
        WHERE r.variant=? ORDER BY r.position''', (key,)).fetchall()
    positions = {rid: index for index, (rid, _) in enumerate(bodies)}
    weight = sum(len(rid) + len(body) for rid, body in bodies) * 4
    packed = activity_key is not None and weight > _INVENTORY_CACHE.max_bytes
    if packed:
        # A decoded generation can outgrow the cache even though peer polls
        # only need a small slice. Keep the original JSON with its exact
        # order, and decode eligible rows on demand. Account for the strings,
        # tuples and position map under the same cache limit.
        bodies.sort(key=lambda item: activity_key(json.loads(item[1])), reverse=True)
        ordered = bodies
        weight = sum(sys.getsizeof(rid) + sys.getsizeof(body) + 192 for rid, body in bodies)
    else:
        ordered = [(rid, json.loads(body)) for rid, body in bodies]
        if activity_key is not None:
            ordered.sort(key=lambda item: activity_key(item[1]), reverse=True)
    cached = (tuple(ordered), positions, packed)
    _INVENTORY_CACHE.put(cache_key, cached, weight)
    return cached


def _inventory_generation(con, key, cache_key, activity_key):
    # Each caller has already identified the generation in its read
    # transaction. Share only that projection, before applying caller filters.
    with _INVENTORY_FLIGHT_LOCK:
        cached = _INVENTORY_CACHE.get(cache_key)
        if cached is not None:
            return cached
        flight = _INVENTORY_FLIGHTS.get(cache_key)
        owner = flight is None
        if owner:
            flight = _INVENTORY_FLIGHTS[cache_key] = Future()
    if not owner:
        return flight.result()
    try:
        cached = _read_inventory_generation(con, key, cache_key, activity_key)
    except BaseException as exc:
        flight.set_exception(exc)
        raise
    else:
        flight.set_result(cached)
        return cached
    finally:
        with _INVENTORY_FLIGHT_LOCK:
            del _INVENTORY_FLIGHTS[cache_key]


def load_inventory(cache_file, key, *, schema_version, build_id=None,
                   limit=None, activity_key=None, skip_identities=(), deleted_codex=()):
    """A complete, transactionally current peer projection, or None.

    Older stores and partial upgrades use the compatibility history path until
    a normal archive publication has filled every projection. A digest check
    prevents a stale projection from masquerading as the canonical row.
    """
    if not database_path(cache_file).exists():
        return None
    con = _connect(cache_file)
    try:
        con.execute('BEGIN')
        metadata = con.execute('SELECT cached_at,metadata FROM variants WHERE key=?', (key,)).fetchone()
        if not metadata:
            return None
        meta = json.loads(metadata[1])
        if (meta.get('schema_version') != schema_version
                or meta.get('build_id') != (str(schema_version) if build_id is None else build_id)):
            return None
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='session_inventory'").fetchone():
            return None
        stat = database_path(cache_file).stat()
        cache_key = (str(database_path(cache_file)), stat.st_dev, stat.st_ino,
                     key, metadata[0], schema_version, build_id, INVENTORY_VERSION, activity_key)
        cached = _inventory_generation(con, key, cache_key, activity_key)
        if cached is None:
            return None
        # Callers may enrich rows; no mutable cache member escapes. Ordinary
        # inventory fields are scalar, but preserve unusual nested model data.
        import copy
        ordered, positions, packed = cached
        identities, rows = [], []
        for identity, stored_row in ordered:
            if identity in skip_identities:
                continue
            row = json.loads(stored_row) if packed else stored_row
            if (row.get('engine') == 'codex'
                    and str(row.get('session_id') or row.get('id') or '') in deleted_codex):
                continue
            identities.append(identity)
            rows.append(row if packed else {
                name: copy.deepcopy(value) if isinstance(value, (dict, list)) else value
                for name, value in row.items()})
            if limit is not None and len(rows) >= limit:
                break
        positions = {rid: positions[rid] for rid in (*identities, *skip_identities) if rid in positions}
        return {'cached_at': metadata[0], 'identities': tuple(identities),
                'positions': positions, 'conversations': rows}
    finally:
        con.close()


def load_head(cache_file, key, limit, *, schema_version, build_id, deleted_codex=()):
    """Decode only a capped query's first eligible canonical history rows.

    The public sessions API applies its limit after lifecycle deletion, in
    stored order. Filtering those deletions in the same read transaction keeps
    the cap exact without deserializing unrelated historical bodies.
    """
    if not database_path(cache_file).exists():
        return None
    con = _connect(cache_file)
    try:
        con.execute('BEGIN')
        metadata = con.execute('SELECT cached_at,metadata FROM variants WHERE key=?', (key,)).fetchone()
        if not metadata:
            return None
        meta = json.loads(metadata[1])
        if meta.get('schema_version') != schema_version or meta.get('build_id') != build_id:
            return None
        sql = 'SELECT body FROM rows WHERE variant=?'
        params = [key]
        if deleted_codex:
            sql += ''' AND (sid NOT IN (SELECT value FROM json_each(?))
                       OR COALESCE(json_extract(body,'$.engine'),'')!='codex')'''
            params.append(json.dumps(sorted(deleted_codex)))
        sql += ' ORDER BY position LIMIT ?'
        params.append(limit)
        bodies = con.execute(sql, params).fetchall()
        return {'cached_at': metadata[0], 'conversations': [json.loads(body) for body, in bodies]}
    finally:
        con.close()


def _decode_full_batch(bodies):
    if (bodies and sum(map(len, bodies)) <= 2 * 1024 * 1024
            and all(isinstance(body, str) and body.startswith('{') and body.endswith('}')
                    for body in bodies)):
        try:
            decoded = json.loads('[' + ','.join(bodies) + ']')
            if len(decoded) == len(bodies):
                return decoded
        except (ValueError, RecursionError):
            pass
    # Retain per-record decoding for oversized, legacy or malformed
    # bodies so corrupt records still take the normal fallback path.
    return [json.loads(body) for body in bodies]


def _decode_full_bodies(cursor):
    """Decode ordered archive bodies in bounded batches."""
    rows = []
    while batch := cursor.fetchmany(192):
        rows.extend(_decode_full_batch([body for (body,) in batch]))
    return rows


_SIDEBAR_HISTORY_MAX_BYTES = 256 << 20
_SIDEBAR_HISTORY_REUSE_S = 20


class SidebarHistoryProjection:
    """Static sidebar fields belonging to one borrowed history layout."""

    max_bytes = 64 << 20
    source_row_bytes = 128

    def __init__(self, layout):
        # Strong references make identity checks safe even if the reader's
        # expiry timer replaces the layout during projection.
        self.sources = {id(row): row for _, row in layout if row is not None}
        self.rows = None
        self.full_response_served = False


def _recover_sidebar_history(conn, key, missing, version):
    """Recover skipped bodies after a small change to the live overrides."""
    if len(missing) > 32:
        return None
    marks = ','.join('?' for _ in missing)
    records = conn.execute(
        f'SELECT identity, body FROM rows WHERE variant=? AND identity IN ({marks})',
        (key, *missing)).fetchall()
    # A publication between the memo check and this SELECT could otherwise
    # mix different revisions of the same ordered history.
    if (len(records) != len(missing)
            or conn.execute('PRAGMA data_version').fetchone()[0] != version):
        return None
    rows = _decode_full_batch([body for _, body in records])
    if not all(isinstance(row, dict) for row in rows):
        return None
    return dict(zip((rid for rid, _ in records), rows))


class _SidebarHistoryReader:
    """Keep one decoded cold layout for overlapping sidebar loads and reloads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.conn = None
        self.identity = None
        self.memo = None
        self.pages = {}
        self.expiry = None
        self.expires_at = 0.0
        self.packed_poll_renewed = False

    def _clear(self):
        self.memo = None
        self.pages = {}
        self.packed_poll_renewed = False
        if self.expiry is not None:
            self.expiry.cancel()
            self.expiry = None

    def _close(self):
        self._clear()
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.identity = None

    def _expire(self, marker):
        with self.lock:
            if self.memo is not None and self.memo[0] is marker:
                self._close()

    def _arm_expiry(self):
        if self.expiry is not None:
            self.expiry.cancel()
        marker = object()
        self.memo = (marker, *self.memo[1:])
        self.expires_at = time.monotonic() + _SIDEBAR_HISTORY_REUSE_S
        self.expiry = threading.Timer(_SIDEBAR_HISTORY_REUSE_S, self._expire, (marker,))
        self.expiry.daemon = True
        self.expiry.start()

    def publish_projection(self, projection, rows):
        with self.lock:
            if (self.memo is None or self.memo[-1] is not projection or projection.rows is not None
                    or time.monotonic() >= self.expires_at):
                return
            projection.rows = rows
            # The first projection can consume most of the original lease.
            # Renew once so an immediate reload can reuse that work. Reads
            # still check SQLite's data_version and the publication header.
            self._arm_expiry()

    def renew_after_full_response(self, projection):
        with self.lock:
            if (self.memo is None or self.memo[-1] is not projection
                    or projection.full_response_served or time.monotonic() >= self.expires_at):
                return
            # Encoding and sending a full list can use much of the lease.
            # Give its saved reload one interval without turning polls into
            # an indefinitely retained decoded archive.
            projection.full_response_served = True
            self._arm_expiry()

    def read(self, cache_file, key, current, schema_version, build_id, min_cached_at,
             *, copy_history=True, packed_version=None, check_client=None):
        with self.lock:
            if check_client is not None:
                check_client()
            target = database_path(cache_file).resolve()
            try:
                stat = target.stat()
            except FileNotFoundError:
                self._close()
                return None
            identity = (target, stat.st_dev, stat.st_ino)
            if identity != self.identity:
                self._close()
                self.conn = _connect(cache_file, shared_reader=True)
                self.identity = identity
            conn = self.conn
            try:
                # data_version is comparable only on this connection. It
                # detects all intervening commits, even with the same header.
                version = conn.execute('PRAGMA data_version').fetchone()[0]
                # Writable shallow copies can retain pointers to nested
                # values. Keep them separate from borrowed, read-only rows.
                guard = (version, key, schema_version, build_id, copy_history, packed_version)
                pages = {}
                if self.memo is not None:
                    _, previous, entry, layout, overridden, _ = self.memo
                    if (previous == guard
                            and float(entry.get('cached_at') or 0) >= min_cached_at):
                        recovered = {}
                        if not overridden <= current.keys():
                            recovered = _recover_sidebar_history(
                                conn, key, overridden - current.keys(), version)
                        if recovered is not None:
                            rows = []
                            for rid, stored in layout:
                                live = current.pop(rid, None)
                                if live is None:
                                    if stored is None:
                                        stored = recovered[rid]
                                    live = stored.copy() if copy_history else stored
                                rows.append(live)
                            rows.extend(current.values())
                            if self.pages and not self.packed_poll_renewed:
                                remaining = self.expires_at - time.monotonic()
                                if 0 < remaining <= _SIDEBAR_HISTORY_REUSE_S / 2:
                                    # One late reuse can cover the next active
                                    # sidebar poll without repeated renewals
                                    # retaining this layout indefinitely.
                                    self.packed_poll_renewed = True
                                    self._arm_expiry()
                            return dict(entry, conversations=rows)
                    if (packed_version is not None and previous[1:] == guard[1:]
                            and time.monotonic() < self.expires_at):
                        pages = self.pages
                    self._clear()

                with conn:
                    loaded = _load_override_entry(
                        conn, key, current, schema_version, build_id, min_cached_at,
                        capture_layout=True, copy_history=copy_history, packed_version=packed_version,
                        page_cache=pages, check_client=check_client)
                if loaded is None:
                    self._close()
                    return None
                entry, rows, layout = loaded
                if layout is not None:
                    overridden = {rid for rid, stored in layout if stored is None}
                    projection = None
                    if (not copy_history and len(layout) * SidebarHistoryProjection.source_row_bytes
                            < SidebarHistoryProjection.max_bytes):
                        projection = SidebarHistoryProjection(layout)
                    self.memo = (None, guard, entry, layout, overridden, projection)
                    self.pages = pages
                    self._arm_expiry()
                else:
                    self._close()
                return dict(entry, conversations=rows)
            except Exception:
                # A stopped or failed read must release its connection and
                # any memo after the transaction rolls back.
                self._close()
                raise


_SIDEBAR_HISTORY_READER = _SidebarHistoryReader()


def sidebar_pages_available(cache_file, key, version):
    """Select the packed source flight only when both sidebar readers are ready."""
    if not database_path(cache_file).exists():
        return False
    try:
        con = _connect(cache_file)
        try:
            return _sidebar_pages_current(con, key, version)
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return False


def _sidebar_pages_current(con, key, version):
    return con.execute("""
        SELECT 1 FROM variants v
        JOIN sidebar_page_generation b ON b.variant=v.key AND b.cached_at=v.cached_at
        JOIN sidebar_preview_generation p ON p.variant=v.key AND p.cached_at=v.cached_at
        WHERE v.key=? AND b.version=?
    """, (key, version)).fetchone() is not None


def backfill_sidebar_pages(cache_file, key, version, project):
    """Build the derived indexes for an unchanged, already published archive."""
    from ccc_server import archive_preview, archive_sidebar_pages

    if not database_path(cache_file).exists() or sidebar_pages_available(cache_file, key, version):
        return False
    con = _connect(cache_file, write=True)
    try:
        archive_preview.setup(con)
        archive_sidebar_pages.setup(con)
        with con:
            con.execute("BEGIN IMMEDIATE")
            stored = con.execute("SELECT cached_at FROM variants WHERE key=?", (key,)).fetchone()
            if stored is None or _sidebar_pages_current(con, key, version):
                return False
            stamp, = stored
            indexed = archive_preview.indexed(con, key)
            rows = []
            for rid, body in con.execute(
                "SELECT identity,body FROM rows WHERE variant=? ORDER BY position,rowid", (key,),
            ):
                row = json.loads(body)
                rows.append(row)
                if rid not in indexed:
                    archive_preview.publish(con, key, rid, row)
            archive_preview.finish_publication(con, key, stamp)
            hot = {identity(row) for row in hot_rows(rows, now=stamp)}
            archive_sidebar_pages.publish(con, key, stamp, version, hot, project)
            return _sidebar_pages_current(con, key, version)
    finally:
        con.close()


@contextmanager
def sidebar_preview_view(cache_file, key, *, schema_version, build_id):
    """Expose only a complete indexed generation and its read transaction."""
    from ccc_server import archive_preview

    if not database_path(cache_file).exists():
        yield None
        return
    con = _connect(cache_file)
    try:
        con.execute("BEGIN")
        stored = con.execute("""
            SELECT v.metadata, v.cached_at FROM variants v
            JOIN sidebar_preview_generation p ON p.variant=v.key AND p.cached_at=v.cached_at
            WHERE v.key=? AND p.version=?
        """, (key, archive_preview.VERSION)).fetchone()
        if not stored:
            yield None
            return
        metadata = json.loads(stored[0])
        if metadata.get("schema_version") != schema_version or metadata.get("build_id") != build_id:
            yield None
            return
        yield con, stored[1]
    finally:
        con.close()


def _load_override_entry(con, key, current, schema_version, build_id, min_cached_at,
                         *, capture_layout=False, copy_history=True, packed_version=None,
                         page_cache=None, check_client=None, session_ids=None):
    con.execute('BEGIN')
    header = con.execute('SELECT metadata FROM variants WHERE key=?', (key,)).fetchone()
    if not header:
        return None
    metadata = json.loads(header[0])
    entry = metadata['entry']
    if not (metadata.get('schema_version') == schema_version and metadata.get('build_id') == build_id
            and float(entry.get('cached_at') or 0) >= min_cached_at):
        return None

    if packed_version is not None and page_cache is not None and capture_layout and not copy_history:
        from ccc_server import archive_sidebar_pages

        packed = archive_sidebar_pages.read(
            con, key, float(entry.get('cached_at') or 0), packed_version, current,
            _SIDEBAR_HISTORY_MAX_BYTES, page_cache, check_client=check_client)
        if packed is not None:
            return entry, *packed
        page_cache.clear()

    # The body and identity are published together. A NULL here marks an
    # override, since stored bodies are non-null. Keep the database's
    # ordering even when the live snapshot ranks those rows differently.
    params = [json.dumps(list(current)), key]
    source = 'FROM rows WHERE variant=?'
    if session_ids is not None:
        source = ('FROM rows INDEXED BY rows_sid WHERE variant=?'
                  ' AND sid IN (SELECT value FROM json_each(?))')
        params.append(json.dumps(list(session_ids)))
    records = con.execute('''SELECT identity,
        CASE WHEN identity IN (SELECT value FROM json_each(?)) THEN NULL ELSE body END
        ''' + source + ' ORDER BY position, rowid', params)
    rows = []
    layout = [] if capture_layout else None
    weight = 0
    while batch := records.fetchmany(192):
        if check_client is not None:
            check_client()
        bodies = [body for _, body in batch if body is not None]
        decoded = iter(_decode_full_batch(bodies))
        if layout is not None:
            # Retain ordered identities as well as decoded rows, so new
            # overrides can be merged without re-reading the whole variant.
            weight += (4 * sum(map(len, bodies)) + 224 * len(batch)
                       + 4 * sum(len(rid) for rid, _ in batch))
            if weight > _SIDEBAR_HISTORY_MAX_BYTES:
                layout = None
        if layout is None:
            rows.extend(current.pop(rid) if body is None else next(decoded) for rid, body in batch)
            continue
        for rid, body in batch:
            if body is None:
                rows.append(current.pop(rid))
                if layout is not None:
                    layout.append((rid, None))
            else:
                row = next(decoded)
                if not isinstance(row, dict):
                    layout = None
                if layout is not None:
                    layout.append((rid, row))
                    if copy_history:
                        row = row.copy()
                rows.append(row)
    rows.extend(current.values())
    return entry, rows, layout


def load_with_overrides(cache_file, key, current_rows, *, schema_version, build_id, min_cached_at,
                        reuse_sidebar_history=False, copy_history=True, packed_version=None,
                        check_client=None, session_ids=None):
    """Merge current rows at their stored positions without decoding superseded bodies.

    Older schemas and identities derived from a row's position still use the
    caller's ordinary full load and merge.
    With copy_history=False the sidebar reader may borrow stored dictionaries.
    The caller must treat them as read-only, including during lifecycle overlays.
    A scoped read merges only the requested sessions and bypasses the full memo.
    """
    current = {}
    for row in current_rows:
        sid = row.get('session_id') or row.get('id')
        if not isinstance(sid, str) or not sid or sid.startswith('#'):
            return None
        if session_ids is None or sid in session_ids:
            current[identity(row)] = row
    if not database_path(cache_file).exists():
        return None
    if reuse_sidebar_history and session_ids is None:
        return _SIDEBAR_HISTORY_READER.read(
            cache_file, key, current, schema_version, build_id, min_cached_at,
            copy_history=copy_history, packed_version=packed_version, check_client=check_client)
    con = _connect(cache_file)
    try:
        loaded = _load_override_entry(
            con, key, current, schema_version, build_id, min_cached_at,
            check_client=check_client, session_ids=session_ids)
        return dict(loaded[0], conversations=loaded[1]) if loaded is not None else None
    finally:
        con.close()


def sidebar_history_projection():
    """Get the prepared-field cache for the current read-only SQLite memo."""
    memo = _SIDEBAR_HISTORY_READER.memo
    return memo[-1] if memo is not None else None


def publish_sidebar_history_projection(projection, rows):
    _SIDEBAR_HISTORY_READER.publish_projection(projection, rows)


def renew_sidebar_history_after_full_response(projection):
    _SIDEBAR_HISTORY_READER.renew_after_full_response(projection)


_HOT_ROW_QUERY = """WITH RECURSIVE selected(identity,sid,parent) AS (
    SELECT identity,sid,parent FROM rows
    WHERE variant=? AND (activity>=? OR sticky=1)
    UNION
    SELECT r.identity,r.sid,r.parent FROM selected s CROSS JOIN rows r
    WHERE r.variant=? AND r.sid=s.parent AND s.parent!=''
) SELECT {columns} FROM selected s CROSS JOIN rows r
  WHERE r.variant=? AND r.identity=s.identity ORDER BY r.position
"""


def _hot_params(key, now=None):
    cutoff = (time.time() if now is None else now) - HOT_SECONDS
    return key, cutoff, key, key


class _RowIndexEntry(dict):
    """Minimal row for merging a refresh without decoding historical bodies."""

    def __init__(self, rid, sid):
        super().__init__(session_id=sid)
        self.rid = rid

    def get(self, key, default=None):
        # With no deletions or engine refresh, the merge only needs the ID.
        # Decode the compound identity on demand for the other merge paths.
        if key in ('engine', 'source', 'jsonl_path') and key not in self:
            engine, source, _, jsonl_path = json.loads(self.rid)
            self.update(engine=engine, source=source, jsonl_path=jsonl_path)
        return super().get(key, default)


def load(cache_file, *, hot=False, key=None, now=None, row_index=False, reuse_engines=None):
    """Read all metadata but only requested row bodies, with ancestor context."""
    if not database_path(cache_file).exists():
        return None
    con = _connect(cache_file)
    try:
        con.execute('BEGIN')
        variants = con.execute('SELECT key,metadata FROM variants' + (' WHERE key=?' if key else ''),
                               (key,) if key else ()).fetchall()
        if not variants:
            return None
        result = {'entries': {}, 'signature_maps': {}}
        for variant, raw_metadata in variants:
            meta = json.loads(raw_metadata)
            entry = meta.pop('entry')
            previous_version = result.get('schema_version')
            previous_build = result.get('build_id')
            result.update({k: v for k, v in meta.items() if k != 'signature_maps'})
            if (isinstance(previous_version, int) and isinstance(meta.get('schema_version'), int)
                    and previous_version != meta['schema_version']
                    and previous_build == str(previous_version)
                    and meta.get('build_id') == str(meta['schema_version'])):
                result['schema_version'] = min(previous_version, meta['schema_version'])
                result['build_id'] = str(result['schema_version'])
            result['signature_maps'].update(meta.get('signature_maps') or {})
            if row_index:
                records = con.execute("""
                    SELECT identity,sid,digest,position FROM rows WHERE variant=? ORDER BY position
                """, (variant,))
                index = {}
                rows = []
                for rid, sid, digest, position in records:
                    rows.append(_RowIndexEntry(rid, sid))
                    index[rid] = (digest, position)
                entry['_row_index'] = index
            elif hot:
                bodies = con.execute(_HOT_ROW_QUERY.format(columns="r.body"), _hot_params(variant, now))
                rows = _decode_full_bodies(bodies)
            elif reuse_engines:
                predicates = " OR ".join("identity LIKE ?" for _ in reuse_engines)
                prefixes = ["[" + json.dumps(engine) + ",%" for engine in reuse_engines]
                bodies = con.execute(f"""
                    SELECT body FROM rows WHERE variant=? AND ({predicates})
                    ORDER BY position
                """, (variant, *prefixes))
                rows = _decode_full_bodies(bodies)
            else:
                bodies = con.execute('SELECT body FROM rows WHERE variant=? ORDER BY position', (variant,))
                rows = _decode_full_bodies(bodies)
            result['entries'][variant] = dict(entry, conversations=rows)
        return result
    finally:
        con.close()


def lookup(cache_file, key, sids, *, schema_version=None, include_ancestors=True):
    """Fetch newly pinned/moved sessions and their ancestors without a cold scan."""
    sids = sorted(set(str(sid) for sid in sids if sid))
    if not sids or not database_path(cache_file).exists():
        return []
    con = _connect(cache_file)
    try:
        con.execute('BEGIN')
        placeholders = ','.join('?' for _ in sids)
        query = f"""WITH RECURSIVE selected(identity,sid,parent) AS (
            SELECT identity,sid,parent FROM rows WHERE variant=? AND sid IN ({placeholders})
            UNION SELECT r.identity,r.sid,r.parent FROM selected s CROSS JOIN rows r
                  WHERE r.variant=? AND r.sid=s.parent AND s.parent!=''
        ) SELECT r.body FROM selected s CROSS JOIN rows r
          WHERE r.variant=? AND r.identity=s.identity ORDER BY r.position"""
        if include_ancestors:
            bodies = con.execute(query, (key, *sids, key, key))
        else:
            # Sorting a few matching rows is cheaper than walking the whole
            # variant in position order to find them.
            bodies = con.execute(
                f'SELECT body FROM rows INDEXED BY rows_sid '
                f'WHERE variant=? AND sid IN ({placeholders}) ORDER BY position',
                (key, *sids))
        rows = [json.loads(raw) for (raw,) in bodies]
        if schema_version is not None:
            meta = con.execute('SELECT metadata FROM variants WHERE key=?', (key,)).fetchone()
            if meta:
                from ccc_server.archive_snapshot import upgrade_driver_metadata
                snapshot = json.loads(meta[0])
                snapshot['entries'] = {key: dict(snapshot.pop('entry'), conversations=rows)}
                upgraded = upgrade_driver_metadata(snapshot, schema_version, str(schema_version))
                rows = upgraded['entries'][key]['conversations']
        return rows
    finally:
        con.close()


def backup_legacy(cache_file):
    """Keep one complete pre-migration JSON snapshot for a source rollback."""
    from ccc_server.archive_snapshot import _writer_lock
    cache_file = Path(cache_file)
    backup = cache_file.with_suffix('.legacy.json')
    if backup.exists() or not cache_file.exists():
        return
    with _writer_lock(backup, 0.5) as acquired:
        if not acquired or backup.exists():
            return
        fd, temporary = tempfile.mkstemp(prefix='.archive-legacy-', dir=backup.parent)
        try:
            with os.fdopen(fd, 'wb') as output, cache_file.open('rb') as source:
                shutil.copyfileobj(source, output)
            os.replace(temporary, backup)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
