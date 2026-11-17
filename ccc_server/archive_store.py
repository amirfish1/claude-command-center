# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Per-row archive persistence; short windows never decode cold row bodies.

The database format is independent of the transcript row-shaping version.
Transactions retain newer variants and update only changed row bodies. The
small JSON snapshot remains a fallback, not the canonical historical store.
"""
import hashlib
import copy
import json
import math
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import time

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


def _connect(cache_file, *, write=False):
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
    return sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.5)


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


def load_attention(cache_file, key, *, schema_version, build_id):
    """Complete classifier inputs, or None until a normal publication upgrades.

    Read the tiny header instead of the canonical metadata's corpus stat maps.
    The digest join detects an older writer updating rows without projections.
    """
    if not database_path(cache_file).exists():
        return None
    con = _connect(cache_file)
    try:
        con.execute('BEGIN')
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='attention_variants'").fetchone():
            return None
        header = con.execute('SELECT cached_at,schema_version,build_id,version '
                             'FROM attention_variants WHERE key=?', (key,)).fetchone()
        if not header or header[1:] != (schema_version, build_id, ATTENTION_VERSION):
            return None
        current = con.execute('SELECT cached_at FROM variants WHERE key=?', (key,)).fetchone()
        if not current or current[0] != header[0]:
            return None
        missing = con.execute('''SELECT 1 FROM rows r LEFT JOIN session_attention a
            ON a.variant=r.variant AND a.identity=r.identity
            WHERE r.variant=? AND (a.digest IS NULL OR a.digest!=?||r.digest) LIMIT 1''',
                              (key, ATTENTION_VERSION)).fetchone()
        if missing:
            return None
        stat = database_path(cache_file).stat()
        namespace = (str(database_path(cache_file).resolve()), stat.st_dev, stat.st_ino, key)
        signatures = con.execute('''SELECT a.identity,a.digest FROM rows r JOIN session_attention a
            ON a.variant=r.variant AND a.identity=r.identity
            WHERE r.variant=? ORDER BY r.position''', (key,)).fetchall()
        cached, missing = {}, []
        for rid, digest in signatures:
            row = _ATTENTION_CACHE.get((*namespace, rid, digest))
            if row is None:
                missing.append(rid)
            else:
                cached[rid] = row
        # Read bodies only for new/changed rows. Validation, order and these
        # bodies share one transaction, including across concurrent writers.
        if missing:
            bodies = con.execute('''SELECT identity,digest,body FROM session_attention
                WHERE variant=? AND identity IN (SELECT value FROM json_each(?))''',
                                 (key, json.dumps(missing)))
            for rid, digest, body in bodies:
                row = json.loads(body)
                value = (row, tuple(name for name, item in row.items()
                                    if isinstance(item, (dict, list))))
                cached[rid] = value
                # Account for decoded containers and keys as well as strings.
                # Oversized rows remain readable without being retained.
                _ATTENTION_CACHE.put((*namespace, rid, digest), value, 4 * (len(rid) + len(body)))
        decoded = []
        for rid, _ in signatures:
            row, nested = cached[rid]
            # Lifecycle overlays and consumer enrichment must never mutate
            # cached inputs, including nested structured state and labels.
            result = row.copy()
            for name in nested:
                result[name] = copy.deepcopy(row[name])
            decoded.append((rid, result))
        return {'cached_at': header[0], 'rows': decoded}
    finally:
        con.close()


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
        cached = _INVENTORY_CACHE.get(cache_key)
        if cached is None:
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
            _INVENTORY_CACHE.put(cache_key, cached,
                                 sum(len(rid) + len(body) for rid, body in bodies) * 4)
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


def lookup(cache_file, key, sids, *, schema_version=None):
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
        rows = [json.loads(raw) for (raw,) in con.execute(query, (key, *sids, key, key))]
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
