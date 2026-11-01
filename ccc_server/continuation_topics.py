"""Durable titles for explicit continuation chains, independent of UI rows.

The local ship index owns this small metadata table, separate from reparsable
session_meta rows. Ordinary spawn/report-to parent links are never consulted.
"""
from __future__ import annotations

from contextlib import contextmanager
import functools
from pathlib import Path
import re
import sqlite3

from ccc_server import core as _core
from ccc_server import ship_graph as _sg

MAX_HOPS = 64
_NATIVE_ID_RE = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')


def _topic(title):
    return ' '.join(str(title or '').split()).split(':', 1)[0].strip()[:240]


def _connection(conn=None):
    conn = conn if conn is not None else _sg._get_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS continuation_topics (
        sid TEXT PRIMARY KEY, origin TEXT NOT NULL DEFAULT '',
        root_sid TEXT NOT NULL DEFAULT '', topic_title TEXT NOT NULL DEFAULT '',
        user_named INTEGER NOT NULL DEFAULT 0)''')
    conn.execute('CREATE TABLE IF NOT EXISTS continuation_topic_meta (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
    return conn


def revision():
    """Indexed cross-process cache token; changes only when topic data changes."""
    conn = _connection()
    row = conn.execute('SELECT revision FROM continuation_topic_meta WHERE id=1').fetchone()
    return row[0] if row else 0


@contextmanager
def _transaction(conn):
    # Dashboard and worker may snapshot or rename the same root concurrently.
    # Acquire the writer lock before the first read, rather than letting a
    # stale read overwrite a peer's explicit rename during a later upsert.
    if conn.in_transaction:
        yield
    else:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            yield


def _sid(row):
    return str(row.get('session_id') or row.get('id') or '')


def _origin(row):
    return str(row.get('continued_from_session_id') or row.get('continuation_origin') or '')


class _Batch:
    def __init__(self, conn, rows, names):
        self.conn = conn
        self.rows = {_sid(row): row for row in rows if _sid(row)}
        self.names = names
        self.records = {}
        self.resolved = {}
        self.indexed_origins = {}
        self.cached_titles = None
        self.titles = {}

    def record(self, sid):
        if sid not in self.records:
            row = self.conn.execute(
                'SELECT origin, root_sid, topic_title, user_named FROM continuation_topics WHERE sid=?',
                (sid,)).fetchone()
            self.records[sid] = row or ('', '', '', 0)
        return self.records[sid]

    def title(self, sid):
        if sid not in self.titles:
            self.titles[sid] = self._title(sid)
        return self.titles[sid]

    def _title(self, sid):
        row = self.rows.get(sid, {})
        title = self.names.get(sid) or row.get('display_name') or row.get('custom_title') or row.get('ai_title')
        if title:
            return _topic(title)
        # One in-memory pass per batch, only if an unloaded root needs a title.
        # Never walk directories, load transcripts, or invoke a session search.
        if self.cached_titles is None:
            self.cached_titles = {}
            for filename, meta in list(getattr(_core, '_conv_meta_cache', {}).items()):
                if isinstance(meta, dict):
                    self.cached_titles[Path(filename).stem] = (
                        meta.get('custom_title') or meta.get('agent_name') or meta.get('ai_title'))
        cached = self.cached_titles.get(sid)
        if not cached:
            # Native Codex titles have their own already-batched snapshot.
            # Consult it in memory; never fetch all native threads per row.
            native = getattr(_core, '_codex_titles_cache', {}).get('data', {}).get(sid, {})
            cached = native.get('title') or native.get('agent_nickname')
        if not cached and _NATIVE_ID_RE.fullmatch(sid):
            # On a fresh process an archived Codex root may not be in either
            # in-memory cache. This is an indexed id=? lookup, once per root,
            # not the full native-session enumeration used by title refresh.
            lookup = getattr(_core, '_codex_thread_row', None)
            native = (lookup(sid) if lookup else None) or {}
            cached = native.get('title') or native.get('agent_nickname')
        return _topic(cached)

    def put(self, sid, origin, root, title, user_named=0):
        value = (origin, root, title, user_named)
        previous = self.record(sid)
        if previous != value:
            self.conn.execute('''INSERT INTO continuation_topics VALUES (?,?,?,?,?)
                ON CONFLICT(sid) DO UPDATE SET origin=excluded.origin,
                root_sid=excluded.root_sid, topic_title=excluded.topic_title,
                user_named=excluded.user_named''', (sid, *value))
            # New rows already change the archive snapshot. Invalidate cached
            # projections when an established root/title is corrected or renamed.
            if previous != ('', '', '', 0) and previous[1:] != value[1:]:
                self.conn.execute('INSERT INTO continuation_topic_meta VALUES (1, 1) ON CONFLICT(id) DO UPDATE SET revision=revision+1')
            self.records[sid] = value

    def indexed_origin(self, sid):
        if sid not in self.indexed_origins:
            try:
                row = self.conn.execute(
                    'SELECT continuation_origin FROM session_meta WHERE sid=?', (sid,)).fetchone()
            except sqlite3.OperationalError as exc:
                if str(exc) != 'no such table: session_meta':
                    raise
                row = None
            self.indexed_origins[sid] = (row[0] if row else '') or ''
        return self.indexed_origins[sid]

    def resolve(self, sid):
        if sid in self.resolved:
            return self.resolved[sid]
        chain, seen = [], set()
        current = sid
        for _ in range(MAX_HOPS):
            if current in seen:
                return ('', '')  # malformed cycles must not establish a topic
            seen.add(current)
            origin, root, title, user_named = self.record(current)
            explicit = _origin(self.rows.get(current, {}))
            if current in self.resolved:
                result = self.resolved[current]
                break
            # Once established, root identity outlives the ancestry rows and
            # keeps even years-long chains O(1). Unmaterialized/legacy chains
            # still take the bounded walk below. A late-discovered root edge
            # invalidates the shortcut so initially incomplete history heals.
            if origin and root and root != current and (not explicit or explicit == origin):
                canonical = self.record(root)
                if (canonical[1] == root and not canonical[0]
                        and not _origin(self.rows.get(root, {})) and not self.indexed_origin(root)):
                    result = (root, canonical[2] or title)
                    break
            origin = explicit or origin or self.indexed_origin(current)
            if not origin:
                title = title or self.title(current)
                if not title and chain:
                    title = self.record(chain[-1][0])[2]
                self.put(current, '', current, title, user_named)
                result = (current, title)
                break
            chain.append((current, origin))
            current = origin
        else:
            return ('', '')
        root, title = result
        for member, origin in reversed(chain):
            # Do not promote an intermediate task's suffix to the root topic.
            self.put(member, origin, root, title)
            self.resolved[member] = result
        self.resolved[sid] = result
        return result


def attach_topics(rows, *, conn=None, names=None):
    """Add continuation_topic_title and root identity to explicit continuations.

    Empty title means the root title is not known yet. Existing metadata is
    retried on later loads; generated child titles never change the topic.
    Work is indexed and bounded per distinct ancestor; no transcript scans.
    """
    if not rows:
        return
    conn = _connection(conn)
    names = _core._load_session_name_overrides() if names is None else names
    rows = [row for row in rows if isinstance(row, dict)]
    batch = _Batch(conn, rows, names)
    with _transaction(conn):
        for row in rows:
            sid = _sid(row)
            if not sid:
                continue
            root, title = batch.resolve(sid)
            origin = batch.record(sid)[0]
            if origin and root:
                # A root initially missing its title can become known later.
                root_record = batch.record(root)
                updated = root_record[2] or batch.title(root)
                if updated and updated != root_record[2]:
                    batch.put(root, '', root, updated, root_record[3])
                row['continuation_topic_title'] = updated or title
                row['continuation_topic_root_session_id'] = root
                row['continuation_topic_session_id'] = root
            else:
                row.pop('continuation_topic_title', None)
                row.pop('continuation_topic_root_session_id', None)
                row.pop('continuation_topic_session_id', None)


def record_continuation(origin, sid, *, title='', conn=None):
    """Persist the explicit edge before a new continuation can disappear."""
    if not origin or not sid or origin == sid:
        return
    conn = _connection(conn)
    rows = [{'session_id': origin, 'display_name': title},
            {'session_id': sid, 'continued_from_session_id': origin}]
    attach_topics(rows, conn=conn)


def rename_topic(sid, title, *, conn=None):
    """Record an explicit user rename; a continuation rename stays local."""
    if not sid or not title:
        return
    conn = _connection(conn)
    batch = _Batch(conn, [], {})
    with _transaction(conn):
        root, _ = batch.resolve(sid)
        if root == sid:
            batch.put(sid, '', sid, _topic(title), 1)


def track_spawn(fn):
    """Cover UI, CLI, auto-resume, and worker-routed engine spawn entry points."""
    @functools.wraps(fn)
    def wrapped(prompt, *args, **kwargs):
        match = _sg.CONTINUATION_ORIGIN_RE.search(str(prompt or ''))
        if match:
            try:
                attach_topics([{'session_id': match.group(1)}])
            except (OSError, sqlite3.Error) as exc:
                print(f'[continuation-topics] could not snapshot source topic: {exc}')
        result = fn(prompt, *args, **kwargs)
        if match and isinstance(result, dict) and result.get('ok') and result.get('session_id'):
            try:
                record_continuation(match.group(1), result['session_id'])
            except (OSError, sqlite3.Error) as exc:
                print(f'[continuation-topics] could not persist spawn topic: {exc}')
        return result
    return wrapped
