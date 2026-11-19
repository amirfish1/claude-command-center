"""Durable titles for explicit continuation chains, independent of UI rows.

The local ship index owns this small metadata table, separate from reparsable
session_meta rows. Ordinary spawn/report-to parent links are never consulted.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import functools
import json
import os
from pathlib import Path
import re
import sqlite3
import threading

from ccc_server import core as _core
from ccc_server import ship_graph as _sg
from ccc_server.sqlite_batch import fetch_rows as _sqlite_fetch_rows

MAX_HOPS = 64
_NATIVE_ID_RE = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
_ORIG_GET_CONNECTION = _sg._get_connection
_CACHE_LOCK = threading.RLock()
_INITIALIZED_CONNS: OrderedDict[int, tuple[sqlite3.Connection, str]] = OrderedDict()
_CONN_CHANGES: dict[int, int] = {}
_CACHE_GEN = 0
_TOPIC_CACHE: dict[str, object] = {'token': None, 'entries': {}}
_REVISION_CACHE: dict[str, object] = {'token': None, 'value': 0}
_PRECISE_TOKENS = OrderedDict()
_NATIVE_TITLE_STATE = {'data': None, 'generation': 0}
_TRACKED_TABLES = {
    'continuation_topics': ('sid', 'origin', 'root_sid', 'topic_title', 'user_named'),
    'continuation_topic_meta': ('id', 'revision'),
    'session_meta': ('sid', 'continuation_origin'),
}


def _install_topic_tracking(conn):
    """Install transactional change counters without waiting for a writer.

    Readers keep the conservative database-wide token until every trigger
    exists. Never commit a caller's transaction to install this optimization.
    """
    names = [f'ccc_topic_cache_{table}_{event}' for table in _TRACKED_TABLES
             for event in ('insert', 'update', 'delete')]
    marks = ','.join('?' for _ in names)
    if conn.execute(f'SELECT count(*) FROM sqlite_master WHERE type=\'trigger\' '
                    f'AND name IN ({marks})', names).fetchone()[0] == len(names):
        return
    if conn.in_transaction:
        return
    timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
    try:
        conn.execute('PRAGMA busy_timeout=0')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS continuation_topic_cache_state '
                     '(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
        conn.execute('INSERT OR IGNORE INTO continuation_topic_cache_state VALUES (1, 0)')
        for table, columns in _TRACKED_TABLES.items():
            for event in ('insert', 'update', 'delete'):
                when = ''
                if event == 'update':
                    when = ' WHEN ' + ' OR '.join(
                        f'NEW.{column} IS NOT OLD.{column}' for column in columns)
                elif table == 'session_meta':
                    alias = 'NEW' if event == 'insert' else 'OLD'
                    when = f" WHEN coalesce({alias}.continuation_origin, '') != ''"
                conn.execute(f'CREATE TRIGGER IF NOT EXISTS ccc_topic_cache_{table}_{event} '
                             f'AFTER {event.upper()} ON {table}{when} BEGIN '
                             'UPDATE continuation_topic_cache_state SET revision=revision+1 '
                             'WHERE id=1; END')
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
        # Older/incomplete schemas and contended writers retain broad tokens.
    finally:
        conn.execute(f'PRAGMA busy_timeout={timeout}')


def _precise_topic_token(conn, db_path, broad):
    with _CACHE_LOCK:
        if broad in _PRECISE_TOKENS:
            _PRECISE_TOKENS.move_to_end(broad)
            return _PRECISE_TOKENS[broad]
    reader = None
    try:
        if conn is None:
            reader = sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', uri=True, timeout=0)
            conn = reader
        names = [f'ccc_topic_cache_{table}_{event}' for table in _TRACKED_TABLES
                 for event in ('insert', 'update', 'delete')]
        marks = ','.join('?' for _ in names)
        row = conn.execute(
            'SELECT revision, (SELECT count(*) FROM sqlite_master WHERE type=\'trigger\' '
            f'AND name IN ({marks})), (SELECT schema_version FROM pragma_schema_version) '
            'FROM continuation_topic_cache_state WHERE id=1', names).fetchone()
        if row is None or row[1] != len(names):
            return broad
        # Keep the main-file signature: checkpoint/restore/replacement may
        # invalidate conservatively, while unrelated WAL appends do not.
        identity = broad[1][0] if db_path else broad[0]
        token = ('topics-v1', broad[0], identity, row[0], row[2], broad[3], broad[-1])
    except sqlite3.Error:
        return broad
    finally:
        if reader is not None:
            reader.close()
    with _CACHE_LOCK:
        _PRECISE_TOKENS[broad] = token
        _PRECISE_TOKENS.move_to_end(broad)
        while len(_PRECISE_TOKENS) > 128:
            _PRECISE_TOKENS.popitem(last=False)
    return token


def _topic(title):
    return ' '.join(str(title or '').split()).split(':', 1)[0].strip()[:240]


@functools.lru_cache(maxsize=32768)
def _metadata_session_id(filename):
    # Paths stay stable while metadata titles change. Avoid parsing every
    # filename again when a new session makes the topic projection miss.
    # The first pass also covers a large archive: ordinary POSIX filenames
    # need only the final component and suffix, without allocating a Path.
    if isinstance(filename, str) and os.name == 'posix':
        name = filename.rsplit('/', 1)[-1]
        if name and not name.endswith('.'):
            dot = name.rfind('.')
            return name[:dot] if 0 < dot < len(name) - 1 else name
    return Path(filename).stem


def _file_stat_sig(db_path):
    if not db_path:
        return None
    sig = []
    for path in (db_path, f'{db_path}-wal', f'{db_path}-journal'):
        try:
            st = os.stat(path)
            sig.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((0, 0, 0))
    return tuple(sig)


def _external_title_cache_sig():
    conv_meta = getattr(_core, '_conv_meta_cache', None)
    codex_cache = getattr(_core, '_codex_titles_cache', None)
    codex_data = codex_cache.get('data') if isinstance(codex_cache, dict) else None
    codex_lookup = getattr(_core, '_codex_thread_row', None)
    # Native snapshots publish a new dictionary every refresh, including when
    # only prompts or activity changed. Topic fallback reads just these two
    # title fields; compare them once per published snapshot, not per row.
    with _CACHE_LOCK:
        previous = _NATIVE_TITLE_STATE['data']
        if previous is not codex_data:
            changed = (
                not isinstance(previous, dict) or not isinstance(codex_data, dict)
                or len(previous) != len(codex_data)
                or any(not isinstance(meta, dict)
                       or not isinstance(previous.get(sid), dict)
                       or previous[sid].get('title') != meta.get('title')
                       or previous[sid].get('agent_nickname') != meta.get('agent_nickname')
                       for sid, meta in codex_data.items())
            )
            if changed:
                _NATIVE_TITLE_STATE['generation'] += 1
            _NATIVE_TITLE_STATE['data'] = codex_data
        native_generation = _NATIVE_TITLE_STATE['generation']
    return (
        id(conv_meta),
        len(conv_meta) if isinstance(conv_meta, dict) else 0,
        native_generation,
        len(codex_data) if isinstance(codex_data, dict) else 0,
        id(codex_lookup),
    )


def _db_state_token(conn=None):
    global _CACHE_GEN
    if conn is None:
        if _sg._get_connection is not _ORIG_GET_CONNECTION:
            conn = _sg._get_connection()
        else:
            conn = getattr(_sg._tls, 'conn', None)
            if conn is None:
                db_path = os.path.abspath(str(_sg._get_db_path()))
                file_sig = _file_stat_sig(db_path)
                if file_sig is None or file_sig[0] == (0, 0, 0):
                    return None
                with _CACHE_LOCK:
                    gen = _CACHE_GEN
                broad = (db_path, file_sig, gen, (), _external_title_cache_sig())
                return _precise_topic_token(None, db_path, broad)
    conn_id = id(conn)
    with _CACHE_LOCK:
        entry = _INITIALIZED_CONNS.get(conn_id)
        if entry is None or entry[0] is not conn:
            return None
        db_path = entry[1]
        changes = conn.total_changes
        prev_changes = _CONN_CHANGES.get(conn_id)
        if prev_changes != changes:
            _CONN_CHANGES[conn_id] = changes
            _CACHE_GEN += 1
        gen = _CACHE_GEN
    file_sig = _file_stat_sig(db_path) if db_path else (conn_id, changes)
    tx_sig = (conn_id, changes) if conn.in_transaction else ()
    broad = (db_path or conn_id, file_sig, gen, tx_sig, _external_title_cache_sig())
    return _precise_topic_token(conn, db_path, broad)


def _connection(conn=None):
    conn = conn if conn is not None else _sg._get_connection()
    conn_id = id(conn)
    with _CACHE_LOCK:
        entry = _INITIALIZED_CONNS.get(conn_id)
        if entry is not None and entry[0] is conn:
            _INITIALIZED_CONNS.move_to_end(conn_id)
            return conn
    db_row = conn.execute('PRAGMA database_list').fetchone()
    raw_path = (db_row[2] if db_row and len(db_row) > 2 else '') or ''
    db_path = os.path.abspath(raw_path) if raw_path else ''
    has_tables = (
        conn.execute('PRAGMA table_info(continuation_topics)').fetchone() is not None
        and conn.execute('PRAGMA table_info(continuation_topic_meta)').fetchone() is not None
    )
    if not has_tables:
        conn.execute('''CREATE TABLE IF NOT EXISTS continuation_topics (
            sid TEXT PRIMARY KEY, origin TEXT NOT NULL DEFAULT '',
            root_sid TEXT NOT NULL DEFAULT '', topic_title TEXT NOT NULL DEFAULT '',
            user_named INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('CREATE TABLE IF NOT EXISTS continuation_topic_meta (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
    _install_topic_tracking(conn)
    with _CACHE_LOCK:
        for old_id, (old_conn, _) in list(_INITIALIZED_CONNS.items()):
            try:
                _ = old_conn.in_transaction
            except sqlite3.ProgrammingError:
                _INITIALIZED_CONNS.pop(old_id, None)
                _CONN_CHANGES.pop(old_id, None)
        _INITIALIZED_CONNS[conn_id] = (conn, db_path)
        _INITIALIZED_CONNS.move_to_end(conn_id)
        _CONN_CHANGES[conn_id] = conn.total_changes
        while len(_INITIALIZED_CONNS) > 128:
            old_id, _ = _INITIALIZED_CONNS.popitem(last=False)
            _CONN_CHANGES.pop(old_id, None)
    return conn


def revision():
    """Indexed cross-process cache token; changes only when topic data changes."""
    token = _db_state_token(None)
    if token is not None:
        with _CACHE_LOCK:
            if _REVISION_CACHE.get('token') == token:
                return _REVISION_CACHE['value']
    conn = _connection()
    row = conn.execute('SELECT revision FROM continuation_topic_meta WHERE id=1').fetchone()
    val = row[0] if row else 0
    token = _db_state_token(conn)
    if token is not None:
        with _CACHE_LOCK:
            _REVISION_CACHE['token'] = token
            _REVISION_CACHE['value'] = val
    return val


@contextmanager
def _transaction(conn, *, wait=True):
    # Dashboard and worker may snapshot or rename the same root concurrently.
    # Acquire the writer lock before the first read, rather than letting a
    # stale read overwrite a peer's explicit rename during a later upsert.
    if conn.in_transaction:
        yield
    else:
        timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
        try:
            if not wait:
                conn.execute('PRAGMA busy_timeout=0')
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                yield
        finally:
            if not wait:
                conn.execute(f'PRAGMA busy_timeout={timeout}')


def _sid(row):
    return str(row.get('session_id') or row.get('id') or '')


def _origin(row):
    return str(row.get('continued_from_session_id') or row.get('continuation_origin') or '')


class _Batch:
    def __init__(self, conn, rows, names, *, reuse_cached=False):
        self.conn = conn
        self.rows = {_sid(row): row for row in rows if _sid(row)}
        self.names = names
        self.records = {}
        self.resolved = {}
        self.indexed_origins = {}
        self.cached_titles = None
        self.titles = {}
        self.pending = {}
        self.allow_native_lookup = True
        self.capture_root_titles = False
        self.ancestors = set()
        if reuse_cached:
            token = _db_state_token(conn)
            with _CACHE_LOCK:
                previous = _TOPIC_CACHE.get('token')
                if token is not None and previous is not None and previous[:-1] == token[:-1]:
                    # A new row misses the projected cache without changing
                    # established database records. Resolve the whole batch
                    # normally, but fetch only records not already read under
                    # this database token. External titles remain live inputs.
                    self.records = dict(_TOPIC_CACHE.get('records') or {})
                    self.indexed_origins = dict(_TOPIC_CACHE.get('indexed_origins') or {})
        self._prefetch([sid for sid in self.rows if sid not in self.records])

    def _prefetch(self, sids, chunk=500):
        # Read both indexed inputs in one bounded result. Separate reads of
        # the same IDs double the SQLite/GIL handoffs on a busy dashboard.
        # Start from the requested IDs so missing topic/index rows retain
        # the same defaults as established standalone sessions.
        for i in range(0, len(sids), chunk):
            part = sids[i:i + chunk]
            wanted = 'WITH wanted(sid) AS (VALUES ' + ','.join('(?)' for _ in part) + ') '
            topic_sql = '''SELECT w.sid AS sid, coalesce(t.origin, '') AS origin,
                coalesce(t.root_sid, '') AS root_sid,
                coalesce(t.topic_title, '') AS topic_title,
                CASE WHEN t.sid IS NULL THEN 0 ELSE t.user_named END AS user_named,
                {indexed} AS continuation_origin
                FROM wanted w LEFT JOIN continuation_topics t ON t.sid=w.sid'''
            columns = ('sid', 'origin', 'root_sid', 'topic_title', 'user_named',
                       'continuation_origin')
            try:
                found = _sqlite_fetch_rows(
                    self.conn,
                    wanted + topic_sql.format(indexed="coalesce(m.continuation_origin, '')")
                    + ' LEFT JOIN session_meta m ON m.sid=w.sid', part, columns)
            except sqlite3.OperationalError as exc:
                if str(exc) != 'no such table: session_meta':
                    raise
                found = _sqlite_fetch_rows(
                    self.conn, wanted + topic_sql.format(indexed="''"), part, columns)
            for sid, origin, root, title, named, indexed in found:
                self.records.setdefault(sid, (origin, root, title, named))
                self.indexed_origins.setdefault(sid, indexed)

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
                    self.cached_titles[_metadata_session_id(filename)] = (
                        meta.get('custom_title') or meta.get('agent_name') or meta.get('ai_title'))
        cached = self.cached_titles.get(sid)
        if not cached:
            # Native Codex titles have their own already-batched snapshot.
            # Consult it in memory; never fetch all native threads per row.
            native = getattr(_core, '_codex_titles_cache', {}).get('data', {}).get(sid, {})
            cached = native.get('title') or native.get('agent_nickname')
        if (not cached and self.allow_native_lookup
                and (self.capture_root_titles or sid in self.ancestors)
                and _NATIVE_ID_RE.fullmatch(sid)):
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
            self.pending[sid] = value
            self.records[sid] = value

    def flush(self):
        global _CACHE_GEN
        if not self.pending:
            return
        items = list(self.pending.items())
        revisions = 0
        upsert = '''INSERT INTO continuation_topics VALUES (?,?,?,?,?)
            ON CONFLICT(sid) DO UPDATE SET origin=excluded.origin,
            root_sid=excluded.root_sid, topic_title=excluded.topic_title,
            user_named=excluded.user_named'''
        # A cold archive can establish thousands of roots. Keep both reads
        # and writes bounded, without a Python/GIL handoff for each row.
        for offset in range(0, len(items), 500):
            chunk = items[offset:offset + 500]
            marks = ','.join('?' for _ in chunk)
            previous = dict((row[0], row[1:]) for row in _sqlite_fetch_rows(
                self.conn,
                'SELECT sid, root_sid, topic_title, user_named FROM continuation_topics '
                f'WHERE sid IN ({marks})', [sid for sid, _ in chunk],
                ('sid', 'root_sid', 'topic_title', 'user_named')))
            # New rows already change the archive snapshot. Established root
            # corrections invalidate cached projections in other processes.
            revisions += sum(sid in previous and previous[sid] != value[1:]
                             for sid, value in chunk)
            values = [(sid, *value) for sid, value in chunk]
            try:
                self.conn.execute('''INSERT INTO continuation_topics
                    SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'),
                           json_extract(value, '$[2]'), json_extract(value, '$[3]'),
                           json_extract(value, '$[4]')
                    FROM json_each(?) WHERE 1
                    ON CONFLICT(sid) DO UPDATE SET origin=excluded.origin,
                    root_sid=excluded.root_sid, topic_title=excluded.topic_title,
                    user_named=excluded.user_named''', (json.dumps(values),))
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if not (('no such function:' in message or 'no such table:' in message)
                        and 'json_' in message):
                    raise
                self.conn.executemany(upsert, values)
        if revisions:
            self.conn.execute('''INSERT INTO continuation_topic_meta VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET revision=revision+excluded.revision''',
                (revisions,))
        with _CACHE_LOCK:
            _CACHE_GEN += 1
            _CONN_CHANGES[id(self.conn)] = self.conn.total_changes
            _TOPIC_CACHE['token'] = None
            _TOPIC_CACHE['entries'] = {}
            _REVISION_CACHE['token'] = None

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
                if chain:
                    self.ancestors.add(current)
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


def _project_topics(batch, rows):
    for row in rows:
        sid = _sid(row)
        if not sid:
            continue
        root, title = batch.resolve(sid)
        origin = batch.record(sid)[0]
        if origin and root:
            root_record = batch.record(root)
            batch.ancestors.add(root)
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


def _try_attach_from_cache(rows, conn, names, diagnostics=None):
    def miss(reason, changed=()):
        if diagnostics is not None:
            diagnostics.update(hit=False, reason=reason)
            if changed:
                diagnostics['changed'] = changed
        return False

    token = _db_state_token(conn)
    if token is None:
        return miss('no-state')
    with _CACHE_LOCK:
        previous = _TOPIC_CACHE.get('token')
        # External title caches are fallback inputs only. Persisted titles
        # win during normal resolution, so their metadata churn must not
        # discard established topics. Untitled entries recheck fallbacks below.
        if previous != token and (previous is None or previous[:-1] != token[:-1]):
            return miss('first-observed' if previous is None else 'database')
        entries = _TOPIC_CACHE.get('entries') or {}
        if not entries:
            return miss('empty')
        resolved_names = None
        external_titles = None
        staged = []
        for item in rows:
            sid = _sid(item)
            if not sid:
                continue
            entry = entries.get(sid)
            if entry is None:
                return miss('new-session')
            accepted_origins, has_persisted_title, root, topic_title = entry
            if _origin(item) not in accepted_origins:
                return miss('origin-changed')
            if not has_persisted_title:
                if resolved_names is None:
                    resolved_names = _core._load_session_name_overrides() if names is None else names
                candidate = (
                    resolved_names.get(sid)
                    or item.get('display_name')
                    or item.get('custom_title')
                    or item.get('ai_title')
                )
                if not candidate:
                    # Match _Batch._title without native I/O: these cached
                    # standalone rows have no ancestor/root-capture lookup.
                    # Copy items before scanning the concurrently filled map.
                    if external_titles is None:
                        external_titles = {
                            _metadata_session_id(filename): (meta.get('custom_title')
                                                  or meta.get('agent_name')
                                                  or meta.get('ai_title'))
                            for filename, meta in list(
                                getattr(_core, '_conv_meta_cache', {}).items())
                            if isinstance(meta, dict)
                        }
                    native = getattr(_core, '_codex_titles_cache', {}).get('data', {}).get(sid, {})
                    candidate = (external_titles.get(sid) or native.get('title')
                                 or native.get('agent_nickname'))
                if _topic(candidate):
                    return miss('title-discovered')
            staged.append((item, root, topic_title))
    for item, root, topic_title in staged:
        if root:
            item['continuation_topic_title'] = topic_title
            item['continuation_topic_root_session_id'] = root
            item['continuation_topic_session_id'] = root
        else:
            item.pop('continuation_topic_title', None)
            item.pop('continuation_topic_root_session_id', None)
            item.pop('continuation_topic_session_id', None)
    if diagnostics is not None:
        diagnostics.update(hit=True, reason='hit')
    return True


def _store_cache(conn, batch, rows):
    token = _db_state_token(conn)
    if token is None:
        return
    with _CACHE_LOCK:
        if _TOPIC_CACHE.get('token') != token:
            _TOPIC_CACHE['token'] = token
            _TOPIC_CACHE['entries'] = {}
        _TOPIC_CACHE['records'] = dict(batch.records)
        _TOPIC_CACHE['indexed_origins'] = dict(batch.indexed_origins)
        entries = _TOPIC_CACHE['entries']
        for item in rows:
            sid = _sid(item)
            if not sid:
                continue
            origin, root, persisted_title, _ = batch.record(sid)
            raw_origin = _origin(item)
            # A missing source edge falls back to this persisted edge during
            # resolution. Readers that omit it and readers that repeat it
            # therefore have the same topic, under the same database token.
            # Keep conflicting/unresolved explicit edges separate.
            accepted_origins = ((('', origin) if origin else ('',))
                                if raw_origin in ('', origin) else (raw_origin,))
            if origin and root:
                topic_title = item.get('continuation_topic_title') or ''
                if not topic_title:
                    entries.pop(sid, None)
                    continue
                entries[sid] = (accepted_origins, True, root, topic_title)
            else:
                entries[sid] = (accepted_origins, bool(persisted_title), '', '')


def attach_topics(rows, *, conn=None, names=None, wait_for_writer=False, diagnostics=None):
    """Project topics without waiting behind the ship index's bulk writer.

    Archive materialization is opportunistic: a busy writer leaves the computed
    titles available and persistence is retried on the next load. Explicit
    spawn snapshots/edges wait for durable storage instead.
    """
    if not rows:
        return
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return
    if not wait_for_writer and _try_attach_from_cache(rows, conn, names, diagnostics):
        return
    conn = _connection(conn)
    names = _core._load_session_name_overrides() if names is None else names
    read_version = (conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0])
    batch = _Batch(conn, rows, names, reuse_cached=not wait_for_writer)
    batch.capture_root_titles = wait_for_writer
    # Native title reads may perform I/O. Never do them while holding the
    # shared index's writer lock, including on a cold archive projection.
    _project_topics(batch, rows)
    if not batch.pending:
        if not wait_for_writer:
            _store_cache(conn, batch, rows)
        return
    flushed = False
    try:
        with _transaction(conn, wait=wait_for_writer):
            # Check after acquiring the writer lock: data_version catches peer
            # commits and total_changes catches writes on this connection.
            # An unchanged database needs neither another bulk read nor a
            # second resolution pass. A concurrent rename still wins through
            # the fresh batch, reusing only the earlier external title reads.
            if read_version == (conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0]):
                current = batch
            else:
                current = _Batch(conn, rows, names)
                current.allow_native_lookup = False
                current.titles = batch.titles
                current.cached_titles = batch.cached_titles
                _project_topics(current, rows)
            current.flush()
            flushed = True
    except sqlite3.OperationalError as exc:
        if wait_for_writer or getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_BUSY:
            raise
    if flushed and not wait_for_writer:
        _store_cache(conn, current, rows)


def record_continuation(origin, sid, *, title='', conn=None):
    """Persist the explicit edge before a new continuation can disappear."""
    if not origin or not sid or origin == sid:
        return
    conn = _connection(conn)
    rows = [{'session_id': origin, 'display_name': title},
            {'session_id': sid, 'continued_from_session_id': origin}]
    attach_topics(rows, conn=conn, wait_for_writer=True)


def rename_topic(sid, title, *, conn=None):
    """Record an explicit user rename; a continuation rename stays local."""
    if not sid or not title:
        return
    conn = _connection(conn)
    rows = [{'session_id': sid, 'display_name': title}]
    prepared = _Batch(conn, rows, {})
    prepared.resolve(sid)
    with _transaction(conn):
        batch = _Batch(conn, rows, {})
        batch.allow_native_lookup = False
        batch.titles = prepared.titles
        batch.cached_titles = prepared.cached_titles
        root, _ = batch.resolve(sid)
        if root == sid:
            batch.put(sid, '', sid, _topic(title), 1)
        batch.flush()


def track_spawn(fn):
    """Cover UI, CLI, auto-resume, and worker-routed engine spawn entry points."""
    @functools.wraps(fn)
    def wrapped(prompt, *args, **kwargs):
        match = _sg.CONTINUATION_ORIGIN_RE.search(str(prompt or ''))
        if match:
            try:
                attach_topics([{'session_id': match.group(1)}], wait_for_writer=True)
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
