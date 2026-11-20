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
import json
import math
import marshal
import os
from pathlib import Path
import sqlite3
import shutil
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
    sid = row.get('session_id') or row.get('id')
    return json.dumps([row.get('engine'), row.get('source'), sid or f'#{index}',
                       row.get('jsonl_path')], separators=(',', ':'))


def sticky(row):
    # Preserve the existing sidebar exceptions, including imported Hermes
    # history. Unknown timestamps cannot safely be classified as old.
    return bool(row.get('pinned') or row.get('is_live') or not activity(row)
                or (row.get('engine') or row.get('source') or '').lower() == 'hermes')


def hot_rows(rows, now=None):
    cutoff = (time.time() if now is None else now) - HOT_SECONDS
    by_sid = {str(r.get('session_id') or r.get('id') or ''): r for r in rows}
    keep = {identity(r, i) for i, r in enumerate(rows) if sticky(r) or activity(r) >= cutoff}
    pending = [r for i, r in enumerate(rows) if identity(r, i) in keep]
    for row in pending:
        parent = by_sid.get(str(row.get('parent_session_id') or row.get('hermes_parent_session_id') or ''))
        if parent and identity(parent) not in keep:
            keep.add(identity(parent))
            pending.append(parent)
    return [r for i, r in enumerate(rows) if identity(r, i) in keep]


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
        ''')
        return con
    # Read-only connections never create an empty store that hides the JSON
    # fallback if a previous migration was interrupted.
    return sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True,
                           timeout=0.5, check_same_thread=not shared_reader)


def save(cache_file, snapshot, *, complete=False):
    """Publish variants atomically. Return the count of changed row bodies.

    Only complete builder snapshots may remove absent rows. Hot snapshots
    and stale workers cannot erase cold history or a newer publication.
    """
    changed = 0
    con = _connect(cache_file, write=True)
    try:
        con.execute('BEGIN IMMEDIATE')
        from ccc_server.archive_snapshot import _entries
        for key, entry in _entries(snapshot).items():
            stamp = float(entry.get('cached_at') or 0)
            previous = con.execute('SELECT cached_at,metadata FROM variants WHERE key=?', (key,)).fetchone()
            old_version = json.loads(previous[1]).get('schema_version') if previous else None
            new_version = snapshot.get('schema_version')
            downgrade = isinstance(old_version, int) and isinstance(new_version, int) and old_version > new_version
            if downgrade or (previous and previous[0] >= stamp):
                continue
            existing = {rid: (digest, position) for rid, digest, position in con.execute(
                'SELECT identity,digest,position FROM rows WHERE variant=?', (key,))}
            inventory = {rid: digest for rid, digest in con.execute(
                'SELECT identity,digest FROM session_inventory WHERE variant=?', (key,))}
            attention = {rid: digest for rid, digest in con.execute(
                'SELECT identity,digest FROM session_attention WHERE variant=?', (key,))}
            seen = set()
            for index, row in enumerate(entry.get('conversations') or []):
                if not isinstance(row, dict):
                    continue
                rid = identity(row, index)
                seen.add(rid)
                body = json.dumps(row, separators=(',', ':'), sort_keys=True)
                digest = hashlib.sha256(body.encode()).hexdigest()
                attention_digest = ATTENTION_VERSION + digest
                if attention.get(rid) != attention_digest:
                    projected = json.dumps(attention_row(row), separators=(',', ':'), sort_keys=True)
                    con.execute('INSERT OR REPLACE INTO session_attention VALUES(?,?,?,?)',
                                (key, rid, attention_digest, projected))
                inventory_digest = INVENTORY_VERSION + digest
                if inventory.get(rid) != inventory_digest:
                    projected = json.dumps(inventory_row(row), separators=(',', ':'), sort_keys=True)
                    con.execute('INSERT OR REPLACE INTO session_inventory VALUES(?,?,?,?)',
                                (key, rid, inventory_digest, projected))
                if existing.get(rid, (None,))[0] == digest:
                    if existing[rid][1] != index:
                        con.execute('UPDATE rows SET position=? WHERE variant=? AND identity=?', (index, key, rid))
                    continue
                con.execute('INSERT OR REPLACE INTO rows VALUES(?,?,?,?,?,?,?,?,?)', (
                    key, rid, str(row.get('session_id') or row.get('id') or ''),
                    str(row.get('parent_session_id') or row.get('hermes_parent_session_id') or ''),
                    index, activity(row), int(sticky(row)), digest, body,
                ))
                changed += 1
            if complete:
                con.executemany('DELETE FROM rows WHERE variant=? AND identity=?',
                                ((key, rid) for rid in existing.keys() - seen))
                con.executemany('DELETE FROM session_inventory WHERE variant=? AND identity=?',
                                ((key, rid) for rid in inventory.keys() - seen))
                con.executemany('DELETE FROM session_attention WHERE variant=? AND identity=?',
                                ((key, rid) for rid in attention.keys() - seen))
            metadata = {k: v for k, v in snapshot.items() if k != 'entries'}
            metadata['entry'] = {k: v for k, v in entry.items() if k != 'conversations'}
            con.execute('INSERT OR REPLACE INTO variants VALUES(?,?,?)',
                        (key, stamp, json.dumps(metadata, separators=(',', ':'))))
            con.execute('INSERT OR REPLACE INTO attention_variants VALUES(?,?,?,?,?)',
                        (key, stamp, new_version, snapshot.get('build_id'), ATTENTION_VERSION))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return changed


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
    decoded = [(rid, json.loads(body)) for rid, body in bodies]
    positions = {rid: index for index, (rid, _) in enumerate(decoded)}
    if activity_key is not None:
        decoded.sort(key=lambda item: activity_key(item[1]), reverse=True)
    cached = (tuple(decoded), positions)
    _INVENTORY_CACHE.put(cache_key, cached, sum(len(rid) + len(body) for rid, body in bodies) * 4)
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
        identities, rows = [], []
        for identity, row in cached[0]:
            if identity in skip_identities or (row.get('engine') == 'codex'
                    and str(row.get('session_id') or row.get('id') or '') in deleted_codex):
                continue
            identities.append(identity)
            rows.append({name: copy.deepcopy(value) if isinstance(value, (dict, list)) else value
                         for name, value in row.items()})
            if limit is not None and len(rows) >= limit:
                break
        positions = {rid: cached[1][rid] for rid in (*identities, *skip_identities) if rid in cached[1]}
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


def load(cache_file, *, hot=False, key=None, now=None):
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
            if hot:
                cutoff = (time.time() if now is None else now) - HOT_SECONDS
                query = '''WITH RECURSIVE selected(identity,sid,parent) AS (
                    SELECT identity,sid,parent FROM rows
                    WHERE variant=? AND (activity>=? OR sticky=1)
                    UNION
                    SELECT r.identity,r.sid,r.parent FROM selected s CROSS JOIN rows r
                    WHERE r.variant=? AND r.sid=s.parent AND s.parent!=''
                ) SELECT r.body FROM selected s CROSS JOIN rows r
                  WHERE r.variant=? AND r.identity=s.identity ORDER BY r.position'''
                bodies = con.execute(query, (variant, cutoff, variant, variant))
            else:
                bodies = con.execute('SELECT body FROM rows WHERE variant=? ORDER BY position', (variant,))
            result['entries'][variant] = dict(entry, conversations=[json.loads(row[0]) for row in bodies])
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
