"""Durable titles for explicit continuation chains, independent of UI rows.

The local ship index owns this small metadata table, separate from reparsable
session_meta rows. Ordinary spawn/report-to parent links are never consulted.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager, nullcontext
import functools
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from types import MappingProxyType

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
_ARCHIVE_TOPIC_SNAPSHOT = (None, None, None)
_ARCHIVE_TOPIC_CONNECTION = (None, None)
_NATIVE_TITLE_STATE = {'data': None, 'generation': 0}
_SCOPED_TOPIC_SPLIT_MIN = 2000
_SCOPED_TOPIC_ORIGIN_LIMIT = 4096
_TRACKED_TABLES = {
    'continuation_topics': ('sid', 'origin', 'root_sid', 'topic_title', 'user_named'),
    'continuation_topic_meta': ('id', 'revision'),
    'session_meta': ('sid', 'continuation_origin'),
}


def _nonintegral_topic_value(value):
    raise ValueError(f'Inexact SQLite JSON value: {value}')


_SCOPED_TOPIC_DECODER = json.JSONDecoder(
    parse_float=_nonintegral_topic_value, parse_constant=_nonintegral_topic_value)


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


def _precise_topic_token(conn, db_path, broad, *, pinned=False):
    if not pinned:
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
    if not pinned:
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


def _db_state_token(conn=None, *, pinned=False):
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
    return _precise_topic_token(conn, db_path, broad, pinned=pinned)


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


def _row_title(row, names, sid):
    return names.get(sid) or row.get('display_name') or row.get('custom_title') or row.get('ai_title')


def _external_titles():
    from ccc_server.log_parse import _archive_topic_metadata_titles

    # Scoped sidebar reads use the same title projection as All. Detached
    # readers still use only the metadata they have already loaded.
    return _archive_topic_metadata_titles(_project_archive_metadata_titles, include_detached=False)


def _external_title(titles, sid):
    if title := titles.get(sid):
        return title
    native = getattr(_core, '_codex_titles_cache', {}).get('data', {}).get(sid, {})
    return native.get('title') or native.get('agent_nickname')


def _native_title(sid):
    lookup = getattr(_core, '_codex_thread_row', None)
    native = (lookup(sid) if lookup else None) or {}
    return native.get('title') or native.get('agent_nickname')


def _archive_native_titles(roots):
    if len(roots) <= 1:
        return {sid: _native_title(sid) for sid in roots}
    # Resolve the bounded set in one read per account. Use the same account
    # precedence and deleted-thread filtering as the individual thread lookup.
    rows = _core._codex_fetch_threads(
        columns=('id', 'title', 'agent_nickname'), exact_ids=tuple(sorted(roots)),
    )
    titles = {row['id']: row.get('title') or row.get('agent_nickname') for row in rows}
    return {sid: titles.get(sid) for sid in roots}


class UnavailableArchiveProjection(Exception):
    """A title source or topic mutation needs the ordinary full reader."""


class StaleArchiveTopicCut(UnavailableArchiveProjection):
    """A tracked topic input changed before the projection could be saved."""


def _archive_read_version(conn, *, pinned=False):
    token = _db_state_token(conn, pinned=pinned)
    if token is not None and token[0] == "topics-v1":
        # The revision is scoped to tracked topic data. The archive captures
        # external titles separately and compares inside and outside a write.
        return token[:5]
    return conn.total_changes, conn.execute("PRAGMA data_version").fetchone()[0]


def archive_connection():
    """Reuse the topic database connection under the caller's archive lock."""
    global _ARCHIVE_TOPIC_CONNECTION
    database = Path(_sg._get_db_path()).resolve()

    def identity():
        stat = database.stat()
        return database, stat.st_dev, stat.st_ino

    current = identity()
    previous, conn = _ARCHIVE_TOPIC_CONNECTION
    if current == previous:
        return _connection(conn)
    _ARCHIVE_TOPIC_CONNECTION = (None, None)
    if conn is not None:
        conn.close()
    # The ship index owns creation and journaling. A missing or contended
    # database leaves the guarded All response on its ordinary fallback.
    conn = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True,
                           check_same_thread=False, timeout=0.05)
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        _connection(conn)
        if identity() != current:
            raise OSError("Topic database replaced while opening the archive connection")
    except BaseException:
        conn.close()
        raise
    _ARCHIVE_TOPIC_CONNECTION = current, conn
    return conn


def _archive_topic_rows(conn, max_rows):
    columns = ("sid", "origin", "root_sid", "topic_title", "user_named")
    query = "SELECT sid,origin,root_sid,topic_title,user_named FROM continuation_topics LIMIT ?"
    params = (max_rows + 1,)
    # Pack each bounded column once to skip per-row JSON arrays and escaping.
    expected_types = ("text", "text", "text", "text", "integer")
    wrong_type = " OR ".join(
        f"typeof({column})!='{kind}'" for column, kind in zip(columns, expected_types)
    )
    fields = ", ".join(
        f"CASE WHEN max({wrong_type})=0 THEN group_concat({column}, char(0)) END"
        for column in columns
    )
    count, *packed = conn.execute(f"SELECT count(*), {fields} FROM ({query})", params).fetchone()
    if count > max_rows:
        return None
    if not count:
        return ()
    # Legacy scalars and embedded delimiters need the typed reader.
    if packed[0] is not None:
        values = [column.split("\0") for column in packed]
        if all(len(column) == count for column in values):
            return zip(*values[:4], map(int, values[4]))
    return _sqlite_fetch_rows(conn, query, params, columns)


def _archive_topic_records(conn, max_rows, *, timing=nullcontext):
    with timing("topic_rows"):
        records = _archive_topic_rows(conn, max_rows)
    if records is None:
        return None
    with timing("indexed_origins"):
        # Two indexable ranges skip the wide rows with empty origins. Keep
        # the scan bounded before restoring row order. OFFSET 0 prevents
        # SQLite from flattening the two queries into a rowid table scan.
        indexed = _sqlite_fetch_rows(
            conn, "SELECT sid,continuation_origin FROM ("
            "SELECT rowid,sid,continuation_origin FROM session_meta "
            "WHERE continuation_origin>'' OR continuation_origin<'' LIMIT ? OFFSET 0"
            ") ORDER BY rowid",
            (max_rows + 1,), ("sid", "continuation_origin"),
        )
    if len(indexed) > max_rows:
        return None
    with timing("build_edges"):
        by_sid, linked_roots, edges = {}, set(), []
        is_native_id = _NATIVE_ID_RE.fullmatch
        for sid, origin, root, title, named in records:
            by_sid[sid] = (origin, root, title, named)
            if origin:
                if origin != sid:
                    edges.append((sid, origin))
                if root and is_native_id(root):
                    linked_roots.add(root)
            if root and root != sid:
                edges.append((sid, root))
        edges.extend(indexed)
        return (MappingProxyType(by_sid), MappingProxyType(dict(indexed)),
                frozenset(linked_roots), tuple(edges))


def _archive_fallback_titles():
    from ccc_server.log_parse import _archive_topic_metadata_titles

    fallback = dict(_archive_topic_metadata_titles(_project_archive_metadata_titles))
    codex_cache = getattr(_core, '_codex_titles_cache', {}).get('data') or {}
    for sid, value in codex_cache.items():
        if isinstance(value, dict) and (name := value.get('title') or value.get('agent_nickname')):
            fallback.setdefault(sid, name)
    return fallback


def _project_archive_metadata_titles(items):
    titles = {
        _metadata_session_id(filename): (meta.get('custom_title')
                                         or meta.get('agent_name') or meta.get('ai_title'))
        for filename, meta in items if isinstance(meta, dict)
    }
    # Resolve duplicate session IDs before dropping empty titles. A later
    # transcript can shadow an earlier title and expose the native fallback.
    return {sid: title for sid, title in titles.items() if title}


def archive_projection_inputs(conn, *, max_rows, max_native_roots=32, diagnostics=None):
    """Read topic records and external titles needed to guard an archive layer."""
    global _ARCHIVE_TOPIC_SNAPSHOT

    def unavailable(reason):
        if diagnostics is not None:
            diagnostics["topics_unavailable"] = reason
        return None

    @contextmanager
    def timing(phase, group="topics_phases"):
        if diagnostics is None:
            yield
            return
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            yield
        finally:
            wall_ms = (time.perf_counter() - wall) * 1000
            cpu_ms = (time.thread_time() - cpu) * 1000
            totals = diagnostics.setdefault(group, {}).setdefault(
                phase, {"wall_ms": 0.0, "cpu_ms": 0.0})
            totals["wall_ms"] = round(totals["wall_ms"] + wall_ms, 1)
            totals["cpu_ms"] = round(totals["cpu_ms"] + cpu_ms, 1)

    if conn.in_transaction:
        return unavailable("already_in_transaction")
    try:
        with timing("fallback_titles"):
            fallback = _archive_fallback_titles()
        for attempt in range(2):
            # A replaced database can leave an older reader on its previous
            # inode. Reuse only on the connection that read the cut.
            owner, version, snapshot = _ARCHIVE_TOPIC_SNAPSHOT
            with timing("revision"):
                reused = owner is conn and _archive_read_version(conn) == version
            if not reused:
                conn.execute("BEGIN")
                try:
                    # The revision SELECT establishes the same SQLite
                    # snapshot that supplies records. A commit before it
                    # is in the cut and need not force a second bulk read.
                    with timing("revision"):
                        version = _archive_read_version(conn, pinned=True)
                    with timing("records"):
                        if diagnostics is not None:
                            diagnostics["topics_records_tid"] = threading.get_native_id()
                        snapshot = _archive_topic_records(
                            conn, max_rows,
                            timing=functools.partial(timing, group="topics_records_phases"),
                        )
                finally:
                    conn.rollback()
            if snapshot is None:
                return unavailable("row_limit")
            by_sid, indexed, linked_roots, edges = snapshot
            if max(len(by_sid), len(indexed)) > max_rows:
                return unavailable("row_limit")
            native_roots = {sid for sid in linked_roots
                            if not fallback.get(sid) and not by_sid.get(sid, ('', '', '', 0))[2]}
            if len(native_roots) > max_native_roots:
                return unavailable("native_root_limit")
            with timing("native_titles"):
                native = _archive_native_titles(native_roots)
            with timing("revision"):
                current = _archive_read_version(conn)
            if current == version:
                _ARCHIVE_TOPIC_SNAPSHOT = conn, version, snapshot
                if diagnostics is not None:
                    diagnostics["topics_snapshot_reused"] = reused
                    if attempt:
                        diagnostics["topics_retried"] = True
                return (by_sid, indexed, fallback, native), edges, version
        return unavailable("changed_during_read")
    except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError) as exc:
        return unavailable(type(exc).__name__)


class _Batch:
    def __init__(self, conn, rows, names, *, reuse_cached=False, rows_by_sid=None,
                 miss_detail=None, check_client=None, archive_inputs=None):
        self.conn = conn
        self.check_client = check_client
        self.rows = (rows_by_sid if rows_by_sid is not None else
                     {sid: row for row in rows if (sid := _sid(row))})
        self.names = names
        self.records = {}
        self.resolved = {}
        self.indexed_origins = {}
        self.cached_titles = None
        self.titles = {}
        self.pending = {}
        self.original_records = {}
        self.allow_native_lookup = True
        self.capture_root_titles = False
        self.ancestors = set()
        self.read_token = None
        self.archive_inputs = archive_inputs
        if archive_inputs is not None:
            records, origins, _, _ = archive_inputs
            self.records = dict(records)
            self.indexed_origins = origins
            return
        if reuse_cached:
            token = _db_state_token(conn)
            if not conn.in_transaction:
                self.read_token = token
            with _CACHE_LOCK:
                previous = _TOPIC_CACHE.get('token')
                if token is not None and previous is not None and previous[:-1] == token[:-1]:
                    # A new row misses the projected cache without changing
                    # established database records. Resolve the whole batch
                    # normally, but fetch only records not already read under
                    # this database token. External titles remain live inputs.
                    self.records = dict(_TOPIC_CACHE.get('records') or {})
                    self.indexed_origins = dict(_TOPIC_CACHE.get('indexed_origins') or {})
        missing = [sid for sid in self.rows if sid not in self.records]
        if miss_detail is not None:
            miss_detail['prefetch_rows'] = miss_detail.get('prefetch_rows', 0) + len(missing)
        self._prefetch(missing)
        if reuse_cached:
            self._prefetch_related()

    def _prefetch_related(self):
        # The scoped batch can omit ancestors and saved roots. Fetch their
        # topic and index records together before the resolver encounters
        # them one at a time. Deeper or changed chains use its normal lookup.
        related = {}
        for sid, row in self.rows.items():
            origin, root, _, _ = self.records[sid]
            explicit = _origin(row)
            parent = explicit or origin or self.indexed_origins.get(sid)
            if parent and parent not in self.records:
                related[parent] = None
            if (origin and root and root != sid and (not explicit or explicit == origin)
                    and root not in self.records):
                related[root] = None
        if related:
            self._prefetch(list(related))

    def _prefetch(self, sids):
        # Read both indexed inputs in one bounded result. Separate reads of
        # the same IDs double the SQLite/GIL handoffs on a busy dashboard.
        # Start from the requested IDs so missing topic/index rows retain
        # the same defaults as established standalone sessions.
        # Larger batches reduce SQLite/GIL handoffs on a cold sidebar load.
        # Keep a bound for cancellation and honor the connection's SQL limit.
        getlimit = getattr(self.conn, 'getlimit', None)
        chunk = min(3000, getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)) if getlimit else 500
        for i in range(0, len(sids), chunk):
            if self.check_client is not None:
                self.check_client()
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
            if len(part) >= _SCOPED_TOPIC_SPLIT_MIN:
                split = self._prefetch_sparse_origins(wanted, topic_sql, part)
                if split is not None:
                    records, origins = split
                    for sid, origin, root, title, named in records:
                        self.records.setdefault(sid, (origin, root, title, named))
                        self.indexed_origins.setdefault(sid, origins.get(sid, ''))
                    continue
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

    def _prefetch_sparse_origins(self, wanted, topic_sql, sids):
        # On large scopes, fetch sparse indexed origins once instead of
        # probing the wide session_meta table for every session. Keep both
        # aggregates in one SQLite statement so they see the same snapshot.
        topic_rows = topic_sql.format(indexed="''")
        query = wanted + f"""
            SELECT (SELECT json_group_array(json_array(sid, origin, root_sid, topic_title, user_named))
                    FROM ({topic_rows})),
                   (SELECT json_group_array(json_array(sid, continuation_origin))
                    FROM (
                        SELECT sid, continuation_origin FROM session_meta
                        INDEXED BY idx_session_meta_continuation_origin
                        WHERE continuation_origin > '' OR continuation_origin < ''
                        LIMIT {_SCOPED_TOPIC_ORIGIN_LIMIT + 1} OFFSET 0
                    ))
        """
        try:
            packed_topics, packed_origins = self.conn.execute(query, sids).fetchone()
            origins = _SCOPED_TOPIC_DECODER.decode(packed_origins)
            if len(origins) <= _SCOPED_TOPIC_ORIGIN_LIMIT:
                return _SCOPED_TOPIC_DECODER.decode(packed_topics), dict(origins)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if not (message in ('no such table: session_meta',
                                'no such index: idx_session_meta_continuation_origin')
                    or ('no such function:' in message and 'json_' in message)
                    or 'json cannot hold blob values' in message):
                raise
        except ValueError:
            # Legacy REAL values must use the exact typed read.
            pass
        return None

    def record(self, sid):
        if self.archive_inputs is not None:
            return self.records.get(sid, ('', '', '', 0))
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
        title = _row_title(self.rows.get(sid, {}), self.names, sid)
        if title:
            return _topic(title)
        if self.archive_inputs is not None:
            _, _, cached_titles, native_titles = self.archive_inputs
            if cached := cached_titles.get(sid):
                return _topic(cached)
            if sid in self.ancestors and _NATIVE_ID_RE.fullmatch(sid):
                if sid not in native_titles:
                    raise UnavailableArchiveProjection()
                return _topic(native_titles[sid])
            return ''
        # One in-memory pass per batch, only if an unloaded root needs a title.
        # Never walk directories, load transcripts, or invoke a session search.
        if self.cached_titles is None:
            self.cached_titles = _external_titles()
        cached = _external_title(self.cached_titles, sid)
        if (not cached and self.allow_native_lookup
                and (self.capture_root_titles or sid in self.ancestors)
                and _NATIVE_ID_RE.fullmatch(sid)):
            # On a fresh process an archived Codex root may not be in either
            # in-memory cache. This is an indexed id=? lookup, once per root,
            # not the full native-session enumeration used by title refresh.
            cached = _native_title(sid)
        return _topic(cached)

    def put(self, sid, origin, root, title, user_named=0):
        value = (origin, root, title, user_named)
        previous = self.record(sid)
        if previous != value:
            if self.read_token is not None:
                self.original_records.setdefault(sid, previous)
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
        if self.archive_inputs is not None:
            return self.indexed_origins.get(sid, '')
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
        origin, root, title, user_named = self.record(sid)
        if not (origin or _origin(self.rows.get(sid, {})) or self.indexed_origin(sid)):
            # A standalone session does not need cycle detection or an
            # ancestry walk. It can still discover a title or become a root.
            current_title = title or self.title(sid)
            if root != sid or current_title != title:
                self.put(sid, '', sid, current_title, user_named)
            result = (sid, current_title)
            self.resolved[sid] = result
            return result
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
    check_client = batch.check_client
    for index, row in enumerate(rows):
        if check_client is not None and index % 256 == 0:
            check_client()
        sid = _sid(row)
        if not sid:
            continue
        record = batch.records.get(sid)
        if (record and record[1] == sid and record[2] and not record[0]
                and not _origin(batch.rows[sid]) and not batch.indexed_origin(sid)):
            # A named canonical root with no origin needs no projected topic
            # or write. Check the same last row per ID that the resolver uses.
            origin = ''
        else:
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


def attach_archive_topics(rows, *, conn, names, inputs, version, check_client=None, persist=True):
    """Resolve a checked archive scope using the authoritative captured titles."""
    batch = _Batch(conn, rows, names, check_client=check_client, archive_inputs=inputs)
    _project_topics(batch, rows)
    if check_client is not None:
        check_client()
    if not persist or not batch.pending:
        return
    try:
        with _transaction(conn, wait=False):
            if _archive_read_version(conn) != version:
                raise StaleArchiveTopicCut()
            batch.flush()
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_BUSY:
            raise


def _try_attach_from_cache(rows, conn, names, diagnostics=None, *, rows_by_sid=None,
                           check_client=None):
    if diagnostics is not None:
        diagnostics.pop('miss_detail', None)

    def miss(reason, changed=()):
        if diagnostics is not None:
            diagnostics.update(hit=False, reason=reason)
            if changed:
                diagnostics['changed'] = changed
        return False

    def miss_new_session(entries):
        if diagnostics is not None:
            # A preview can seed only a small fraction of a full request.
            # Count the gap before the fallback replaces those cache entries.
            diagnostics['miss_detail'] = {
                'cached_entries': len(entries),
                'missing_rows': sum(1 for item in rows if (sid := _sid(item)) and sid not in entries),
            }
        return miss('new-session')

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
        if rows_by_sid is not None and not (rows_by_sid.keys() <= entries.keys()):
            return miss_new_session(entries)
        resolved_names = None
        external_titles = None
        staged = []
        untitled_roots = set()
        for index, item in enumerate(rows):
            if check_client is not None and index % 256 == 0:
                check_client()
            sid = _sid(item)
            if not sid:
                continue
            entry = entries.get(sid)
            if entry is None:
                return miss_new_session(entries)
            accepted_origins, has_persisted_title, root, topic_title = entry
            if _origin(item) not in accepted_origins:
                return miss('origin-changed')
            if root and not topic_title:
                untitled_roots.add(root)
            elif not has_persisted_title:
                if resolved_names is None:
                    resolved_names = _core._load_session_name_overrides() if names is None else names
                candidate = _row_title(item, resolved_names, sid)
                if not candidate:
                    # Match _Batch._title without native I/O: these cached
                    # standalone rows have no ancestor/root-capture lookup.
                    # Request one consistent view of the concurrently filled map.
                    if external_titles is None:
                        external_titles = _external_titles()
                    candidate = _external_title(external_titles, sid)
                if _topic(candidate):
                    return miss('title-discovered')
            staged.append((item, root, topic_title))
    if untitled_roots:
        # A titleless continuation still has a canonical root. Validate the
        # root's current inputs, including the indexed native lookup outside
        # the cache lock, before applying the cached empty title.
        root_rows = {sid: item for item in rows if (sid := _sid(item)) in untitled_roots}
        if resolved_names is None:
            resolved_names = _core._load_session_name_overrides() if names is None else names
        for index, sid in enumerate(untitled_roots):
            if check_client is not None and index % 256 == 0:
                check_client()
            root_row = root_rows.get(sid, {})
            if _origin(root_row):
                return miss('origin-changed')
            candidate = _row_title(root_row, resolved_names, sid)
            if not candidate:
                if external_titles is None:
                    external_titles = _external_titles()
                candidate = _external_title(external_titles, sid)
                if not candidate and _NATIVE_ID_RE.fullmatch(sid):
                    candidate = _native_title(sid)
            if _topic(candidate):
                return miss('title-discovered')
        if _db_state_token(conn) != token:
            return miss('database')
    if check_client is not None:
        check_client()
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


def _store_cache(conn, batch, rows, *, miss_detail=None, read_token=None):
    token = _db_state_token(conn)
    if token is None:
        return
    # A peer may change a prefetched ancestor before projection finishes.
    # Publish read-only results only under the topic revision they read.
    if read_token is not None and read_token[:-1] != token[:-1]:
        return
    with _CACHE_LOCK:
        previous = _TOPIC_CACHE.get('token')
        # Like a cache read, keep entries under the same database revision
        # when only fallback inputs change. Reads recheck entries without
        # persisted titles against the current inputs.
        if previous is None or previous[:-1] != token[:-1]:
            _TOPIC_CACHE['entries'] = {}
        _TOPIC_CACHE['token'] = token
        _TOPIC_CACHE['records'] = dict(batch.records)
        _TOPIC_CACHE['indexed_origins'] = dict(batch.indexed_origins)
        entries = _TOPIC_CACHE['entries']
        omitted_rows = 0
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
                    canonical = batch.record(root)
                    if (origin != root or canonical[:3] != ('', root, '')
                            or batch.indexed_origin(root) or _origin(batch.rows.get(root, {}))):
                        entries.pop(sid, None)
                        omitted_rows += 1
                        continue
                entries[sid] = (accepted_origins, True, root, topic_title)
            else:
                entries[sid] = (accepted_origins, bool(persisted_title), '', '')
        if miss_detail is not None:
            miss_detail['omitted_rows'] = omitted_rows


def _store_busy_writer_inputs(conn, batch, read_version):
    # Reuse committed inputs while the writer is occupied. The next request
    # can resolve the current titles and retry its writes.
    token = batch.read_token
    if token is None:
        return
    try:
        version = conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0]
    except sqlite3.OperationalError:
        return
    if read_version != version:
        return
    current = _db_state_token(conn)
    if current is None or current[:-1] != token[:-1]:
        return
    with _CACHE_LOCK:
        previous = _TOPIC_CACHE.get('token')
        if previous is None or previous[:-1] != current[:-1]:
            _TOPIC_CACHE.update(token=current, entries={}, records={}, indexed_origins={})
        _TOPIC_CACHE['records'].update(batch.records | batch.original_records)
        _TOPIC_CACHE['indexed_origins'].update(batch.indexed_origins)


@contextmanager
def _topic_phase(phases, name):
    if phases is None:
        yield
        return
    wall, cpu = time.perf_counter(), time.thread_time()
    try:
        yield
    finally:
        phases[name] = {
            'wall_ms': round((time.perf_counter() - wall) * 1000, 1),
            'cpu_ms': round((time.thread_time() - cpu) * 1000, 1),
        }


def attach_topics(rows, *, conn=None, names=None, wait_for_writer=False, diagnostics=None,
                  check_client=None):
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
    phases = {} if diagnostics is not None else None
    if diagnostics is not None:
        diagnostics['phases'] = phases
    rows_by_sid = None
    with _topic_phase(phases, 'register_connection'):
        if conn is None and not wait_for_writer and _sg._get_connection is _ORIG_GET_CONNECTION:
            thread_conn = getattr(_sg._tls, 'conn', None)
            if thread_conn is not None:
                with _CACHE_LOCK:
                    initialized = _INITIALIZED_CONNS.get(id(thread_conn))
                if initialized is None or initialized[0] is not thread_conn:
                    # The dashboard may already have opened the ship index.
                    # Register it and share the ID map if new rows miss.
                    rows_by_sid = {sid: row for row in rows if (sid := _sid(row))}
                    conn = _connection(thread_conn)
    with _topic_phase(phases, 'cache_lookup'):
        if not wait_for_writer and _try_attach_from_cache(
                rows, conn, names, diagnostics, rows_by_sid=rows_by_sid, check_client=check_client):
            return
    with _topic_phase(phases, 'open_and_version'):
        conn = _connection(conn)
        names = _core._load_session_name_overrides() if names is None else names
        read_version = (conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0])
    miss_detail = (diagnostics.get('miss_detail') if diagnostics is not None
                   and diagnostics.get('reason') == 'new-session' else None)
    with _topic_phase(phases, 'read_inputs'):
        batch = _Batch(conn, rows, names, reuse_cached=not wait_for_writer, rows_by_sid=rows_by_sid,
                       miss_detail=miss_detail, check_client=check_client)
        batch.capture_root_titles = wait_for_writer
    # Native title reads may perform I/O. Never do them while holding the
    # shared index's writer lock, including on a cold archive projection.
    with _topic_phase(phases, 'resolve'):
        _project_topics(batch, rows)
    # Cancellation is safe during reads and request-local projection. Once
    # persistence starts, finish the transaction and cache publication.
    if check_client is not None:
        check_client()
    if not batch.pending:
        if not wait_for_writer:
            with _topic_phase(phases, 'publish_cache'):
                _store_cache(conn, batch, rows, miss_detail=miss_detail, read_token=batch.read_token)
        return
    flushed = False
    try:
        with _topic_phase(phases, 'persist'), _transaction(conn, wait=wait_for_writer):
            # Check after acquiring the writer lock: data_version catches peer
            # commits and total_changes catches writes on this connection.
            # An unchanged database needs neither another bulk read nor a
            # second resolution pass. A concurrent rename still wins through
            # the fresh batch, reusing only the earlier external title reads.
            if read_version == (conn.total_changes, conn.execute('PRAGMA data_version').fetchone()[0]):
                current = batch
            else:
                if miss_detail is not None:
                    miss_detail['writer_retry'] = True
                current = _Batch(conn, rows, names, miss_detail=miss_detail)
                current.allow_native_lookup = False
                current.titles = batch.titles
                current.cached_titles = batch.cached_titles
                _project_topics(current, rows)
            current.flush()
            flushed = True
    except sqlite3.OperationalError as exc:
        if wait_for_writer or getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_BUSY:
            raise
        if miss_detail is not None:
            miss_detail['writer_busy'] = True
        with _topic_phase(phases, 'publish_cache'):
            _store_busy_writer_inputs(conn, batch, read_version)
    if flushed and not wait_for_writer:
        with _topic_phase(phases, 'publish_cache'):
            _store_cache(conn, current, rows, miss_detail=miss_detail)


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
