# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Per-row archive persistence; short windows never decode cold row bodies.

The database format is independent of the transcript row-shaping version.
Transactions retain newer variants and update only changed row bodies. The
small JSON snapshot remains a fallback, not the canonical historical store.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import time

HOT_SECONDS = 7 * 86400


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
            seen = set()
            for index, row in enumerate(entry.get('conversations') or []):
                if not isinstance(row, dict):
                    continue
                rid = identity(row, index)
                seen.add(rid)
                body = json.dumps(row, separators=(',', ':'), sort_keys=True)
                digest = hashlib.sha256(body.encode()).hexdigest()
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
            metadata = {k: v for k, v in snapshot.items() if k != 'entries'}
            metadata['entry'] = {k: v for k, v in entry.items() if k != 'conversations'}
            con.execute('INSERT OR REPLACE INTO variants VALUES(?,?,?)',
                        (key, stamp, json.dumps(metadata, separators=(',', ':'))))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return changed


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
