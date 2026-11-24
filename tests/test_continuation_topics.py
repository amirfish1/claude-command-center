"""Durable continuation prefixes without loaded ancestors or parent guessing."""
import sqlite3
from collections import OrderedDict

import pytest

import server
from ccc_server import continuation_topics as topics
from ccc_server import usage_limit


@pytest.fixture
def store(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / 'index.sqlite3')
    conn.execute('CREATE TABLE session_meta (sid TEXT PRIMARY KEY, continuation_origin TEXT)')
    monkeypatch.setattr(topics._sg, '_get_connection', lambda: conn)
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {})
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    yield conn
    conn.close()


def row(sid, title='', origin=''):
    return {'session_id': sid, 'display_name': title, 'continued_from_session_id': origin}


def test_named_root_projection_preserves_explicit_and_indexed_origins(store):
    topics.attach_topics([row('root', 'Project'), row('explicit', 'Old explicit'),
                          row('indexed', 'Old indexed')], wait_for_writer=True, names={})
    store.execute("INSERT INTO session_meta VALUES ('indexed', 'root')")
    store.commit()
    standalone = dict(row('root'), continuation_topic_title='Stale',
                      continuation_topic_root_session_id='old',
                      continuation_topic_session_id='old')
    first = row('explicit')
    last = row('explicit', origin='root')
    indexed = row('indexed')

    topics.attach_topics([standalone, first, last, indexed], wait_for_writer=True, names={})

    assert standalone == row('root')
    for continuation in (first, last, indexed):
        assert continuation['continuation_topic_title'] == 'Project'
        assert continuation['continuation_topic_root_session_id'] == 'root'
    assert store.execute(
        "SELECT origin, root_sid, topic_title FROM continuation_topics WHERE sid='root'"
    ).fetchone() == ('', 'root', 'Project')


def test_repeated_metadata_fallback_reuses_paths_but_reads_changed_titles(store, monkeypatch):
    metadata = {'/archive/untitled.jsonl': {'ai_title': ''},
                '/archive/other.jsonl': {'ai_title': 'Other'}}
    monkeypatch.setattr(server, '_conv_meta_cache', metadata)
    topics._metadata_session_id.cache_clear()
    topics.attach_topics([row('untitled')], names={})
    topics.attach_topics([row('untitled')], names={})
    assert topics._metadata_session_id.cache_info().misses == len(metadata)

    # Caching the filename must not freeze the mutable metadata fallback.
    metadata['/archive/untitled.jsonl']['ai_title'] = 'Discovered topic: task'
    topics.attach_topics([row('untitled')], names={})
    topics.record_continuation('untitled', 'child')
    child = row('child')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'Discovered topic'
    assert topics._metadata_session_id.cache_info().misses == len(metadata)


@pytest.mark.parametrize('filename', [
    '/archive/session.jsonl', 'session', '.session', '..session',
    '.session.jsonl', 'session.', 'session..', 'session.part.jsonl',
    '/archive//session.jsonl', '/archive/../session.jsonl',
    '/archive/session.jsonl/', '/archive/.', '/archive/..',
    '', '.', '..', '/', '//', 'folder/שלום.jsonl',
])
def test_metadata_session_identity_matches_pathlib(filename):
    from pathlib import Path

    assert topics._metadata_session_id(filename) == Path(filename).stem


def test_metadata_session_identity_accepts_path_objects():
    from pathlib import Path

    filename = Path('/archive/session.part.jsonl')
    assert topics._metadata_session_id(filename) == filename.stem


def test_multihop_survives_missing_ancestors_and_database_reopen(store):
    root = row('root-session', 'PROJECT DESIGN: first task')
    child = row('child-session', 'current task: detail', 'root-session')
    topics.attach_topics([child, root], names={})
    topics.record_continuation('child-session', 'third-session')
    store.execute('DELETE FROM session_meta')
    store.commit()
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    fresh = sqlite3.connect(filename)
    latest = row('third-session', 'generated replacement: more detail')
    topics.attach_topics([latest], conn=fresh, names={})
    assert latest['continuation_topic_title'] == 'PROJECT DESIGN'
    assert latest['continuation_topic_root_session_id'] == 'root-session'
    assert latest['continuation_topic_session_id'] == 'root-session'
    fresh.close()


def test_archived_unloaded_root_uses_index_and_cached_metadata(store, monkeypatch):
    store.executemany('INSERT INTO session_meta VALUES (?,?)', [
        ('child-session', 'root-session'), ('third-session', 'child-session')])
    monkeypatch.setattr(server, '_conv_meta_cache', {
        'root-session.jsonl': {'custom_title': 'RESEARCH: initial task'}})
    latest = row('third-session', 'auto title')
    topics.attach_topics([latest], names={})
    assert latest['continuation_topic_title'] == 'RESEARCH'
    assert latest['continuation_topic_root_session_id'] == 'root-session'
    assert latest['continuation_topic_session_id'] == 'root-session'


def test_root_user_rename_wins_over_snapshots_and_auto_titles(store, monkeypatch):
    topics.attach_topics([row('root-session', 'OLD TOPIC: first')], names={})
    topics.record_continuation('root-session', 'child-session')
    monkeypatch.setattr(server, 'find_session_cwd', lambda sid: None)
    monkeypatch.setattr(server, 'session_live_status', lambda *args: {'live': False})
    monkeypatch.setattr(server, '_find_session_jsonl', lambda sid: None)
    monkeypatch.setattr(server, '_sync_codex_thread_title', lambda *args: None)
    from ccc_server import session_graph
    monkeypatch.setattr(session_graph, '_save_session_name_override', lambda *args: {})
    assert session_graph.rename_session('root-session', 'NEW TOPIC: next task')['ok']
    assert session_graph.rename_session('root-session', 'Generated task', source='auto')['ok']
    latest = row('child-session', 'Generated child: suffix')
    topics.attach_topics([latest], names={'root-session': 'Generated task'})
    assert latest['continuation_topic_title'] == 'NEW TOPIC'
    topics.record_continuation('child-session', 'third-session')
    newest = row('third-session', 'NEW TOPIC: child: generated: nested')
    topics.attach_topics([newest], names={})
    assert newest['continuation_topic_title'] == 'NEW TOPIC'


def test_worker_parent_is_not_a_continuation_and_child_rename_is_local(store):
    worker = row('worker-session', 'Worker: task')
    worker['parent_session_id'] = 'root-session'
    topics.attach_topics([row('root-session', 'TOPIC: task'), worker], names={})
    assert 'continuation_topic_title' not in worker
    assert 'continuation_topic_session_id' not in worker
    topics.record_continuation('root-session', 'child-session')
    topics.rename_topic('child-session', 'LOCAL: rename')
    child = row('child-session')
    topics.attach_topics([child], names={'child-session': 'LOCAL: rename'})
    assert child['continuation_topic_title'] == 'TOPIC'


def test_cycles_and_depth_are_bounded(store):
    store.executemany('INSERT INTO session_meta VALUES (?,?)', [
        ('cycle-one', 'cycle-two'), ('cycle-two', 'cycle-one')])
    rows = [row('cycle-one'), row('cycle-two')]
    topics.attach_topics(rows, names={})
    assert all('continuation_topic_title' not in item for item in rows)
    store.executemany('INSERT INTO session_meta VALUES (?,?)', [
        (f'chain-{i}', f'chain-{i+1}') for i in range(topics.MAX_HOPS + 5)])
    queries = []
    store.set_trace_callback(queries.append)
    topics.attach_topics([row('chain-0')], names={})
    reads = [q for q in queries if q.startswith('SELECT continuation_origin')]
    assert len(reads) <= topics.MAX_HOPS
    assert all('WHERE sid=' in q for q in reads)


def test_late_indexed_edge_repairs_initially_unknown_ancestor(store):
    topics.attach_topics([row('child-session', 'Temporary task')], names={})
    store.execute('INSERT INTO session_meta VALUES (?,?)', ('child-session', 'root-session'))
    latest = row('child-session')
    topics.attach_topics([latest], names={'root-session': 'STABLE TOPIC: original'})
    assert latest['continuation_topic_title'] == 'STABLE TOPIC'


def test_generated_titles_never_replace_root_and_suffix_does_not_stack(store):
    topics.attach_topics([row('root-session', 'TOPIC: old: detail')], names={})
    topics.attach_topics([row('root-session', 'Generated task')], names={})
    topics.record_continuation('root-session', 'child-session')
    child = row('child-session', 'TOPIC: new: detail')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'TOPIC'


def test_spawn_wrapper_snapshots_full_prompt_but_never_guesses_from_parent(store, monkeypatch):
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {'root-session': 'TOPIC: first'})
    @topics.track_spawn
    def spawn(prompt, **kwargs):
        return {'ok': True, 'session_id': 'child-session'}
    spawn('preamble' * 100 + '\nOrigin session id: root-session\n')
    child = row('child-session')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'TOPIC'
    @topics.track_spawn
    def worker(prompt, **kwargs):
        return {'ok': True, 'session_id': 'worker-session'}
    worker('ordinary task', parent_session_id='root-session')
    item = row('worker-session')
    topics.attach_topics([item], names={})
    assert 'continuation_topic_title' not in item


def test_shared_metadata_pass_runs_without_usage_resume(store, monkeypatch):
    monkeypatch.setattr(server, '_load_usage_limit_resumes', lambda: {})
    rows = [row('child-session', 'task', 'root-session')]
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {'root-session': 'TOPIC: first'})
    usage_limit._attach_usage_limit_resume_fields(rows)
    assert rows[0]['continuation_topic_title'] == 'TOPIC'


def test_fresh_index_without_session_meta_is_supported(tmp_path):
    conn = sqlite3.connect(tmp_path / 'fresh.sqlite3')
    topics.attach_topics([row('root-session', 'TOPIC: first')], conn=conn, names={})
    topics.record_continuation('root-session', 'child-session', conn=conn)
    child = row('child-session')
    topics.attach_topics([child], conn=conn, names={})
    assert child['continuation_topic_title'] == 'TOPIC'
    conn.close()


@pytest.mark.parametrize('engine_name', ['spawn_session', 'spawn_session_codex', 'spawn_session_kimi'])
def test_real_engine_entry_points_preserve_topic_on_worker_routed_result(store, monkeypatch, engine_name):
    from ccc_server import engines
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {'root-session': 'TOPIC: first'})
    monkeypatch.setattr(server, '_control_plane_engine_call',
                        lambda *args, **kwargs: {'ok': True, 'session_id': 'child-session'})
    monkeypatch.setattr(server, '_take_control_plane_action_id', lambda: None)
    # Claude routes through a compatibility adapter; its delegate still uses
    # the real worker call stub above, without invoking any external CLI.
    result = getattr(engines, engine_name)('Origin session id: root-session\nContinue.')
    assert result['ok']
    child = row('child-session')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'TOPIC'


def test_metadata_cache_is_scanned_at_most_once_per_batch(store, monkeypatch):
    class Cache(dict):
        scans = 0
        def items(self):
            self.scans += 1
            return super().items()
    cache = Cache({'root-session.jsonl': {'custom_title': 'TOPIC: initial'}})
    monkeypatch.setattr(server, '_conv_meta_cache', cache)
    rows = [row(f'child-{i}', 'Generated task', 'root-session') for i in range(100)]
    queries = []
    store.set_trace_callback(queries.append)
    topics.attach_topics(rows, names={})
    assert cache.scans == 1
    assert all(item['continuation_topic_title'] == 'TOPIC' for item in rows)
    selects = [query for query in queries if query.startswith('SELECT ')]
    # Session reads stay indexed; tracking also reads one counter and the
    # fixed set of trigger names from SQLite's small schema catalog.
    assert all(' WHERE sid=' in query or ' WHERE sid IN (' in query
               or 'FROM wanted w LEFT JOIN continuation_topics t ON t.sid=w.sid' in query
               or query.startswith("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN (")
               or query.startswith("SELECT revision, (SELECT count(*) FROM sqlite_master WHERE type='trigger' ")
               for query in selects)
    for query in selects:
        if 'FROM wanted w LEFT JOIN' in query:
            plan = [item[3] for item in store.execute('EXPLAIN QUERY PLAN ' + query)]
            assert any('SEARCH t USING INDEX' in step for step in plan)
            assert any('SEARCH m USING INDEX' in step for step in plan)
    # CCC-142: a steady-state load prefetches rows in chunks instead of
    # issuing 2-3 point queries per row.
    queries.clear()
    topics.attach_topics(rows, names={})
    assert len([q for q in queries if q.startswith('SELECT ')]) < 10


def test_missing_root_title_can_be_filled_later(store):
    topics.record_continuation('root-session', 'child-session')
    child = row('child-session')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == ''
    topics.attach_topics([child], names={'root-session': 'RECOVERED: task'})
    assert child['continuation_topic_title'] == 'RECOVERED'


def test_cli_continuation_uses_resolved_source_title_when_no_rows_are_loaded(store, monkeypatch):
    from ccc_server import continuation
    monkeypatch.setattr(server, 'spawn_session_codex',
                        lambda *args, **kwargs: {'ok': True, 'session_id': 'child-session'})
    context = dict(session_id='root-session', latest='root-session', title='TOPIC: current task',
                   engine='codex', model=None, effort=None, report_to=None, cwd='',
                   context_tokens=0, idle_seconds=0, capture_path=None, transcript_path=None)
    result = continuation._spawn_continuation('root-session', _context=context, rebind_chain=False)
    assert result['ok']
    child = row('child-session', 'Generated title')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'TOPIC'


def test_worker_and_dashboard_writes_preserve_user_rename(store):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    topics.attach_topics([row('root-session', 'OLD TOPIC: first')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    barrier = threading.Barrier(2)
    def spawn():
        conn = sqlite3.connect(filename)
        barrier.wait(timeout=5)
        for i in range(10):
            topics.record_continuation('root-session', f'child-{i}', title='OLD TOPIC: task', conn=conn)
        conn.close()
    def rename():
        conn = sqlite3.connect(filename)
        barrier.wait(timeout=5)
        topics.rename_topic('root-session', 'NEW TOPIC: next task', conn=conn)
        conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        spawned, renamed = pool.submit(spawn), pool.submit(rename)
        spawned.result(timeout=10)
        renamed.result(timeout=10)
    rows = [row(f'child-{i}') for i in range(10)]
    topics.attach_topics(rows, names={})
    assert all(item['continuation_topic_title'] == 'NEW TOPIC' for item in rows)


def test_durable_chain_outlives_legacy_walk_bound(store):
    topics.attach_topics([row('root-session', 'TOPIC: first')], names={})
    previous = 'root-session'
    for i in range(topics.MAX_HOPS + 20):
        sid = f'child-{i}'
        topics.record_continuation(previous, sid)
        previous = sid
    latest = row(previous, 'Generated latest task')
    queries = []
    store.set_trace_callback(queries.append)
    topics.attach_topics([latest], names={})
    assert latest['continuation_topic_title'] == 'TOPIC'
    assert latest['continuation_topic_root_session_id'] == 'root-session'
    assert latest['continuation_topic_session_id'] == 'root-session'
    assert len([q for q in queries if q.startswith('SELECT ')]) <= 4


def test_cold_native_archived_root_title_uses_one_exact_lookup(store, monkeypatch):
    root = '00000000-0000-4000-8000-000000000001'
    calls = []
    def native_title(sid):
        calls.append(sid)
        return {'id': sid, 'title': 'NATIVE TOPIC: old task'}
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    monkeypatch.setattr(server, '_codex_thread_row', native_title)
    rows = [row(f'child-{i}', 'Generated child', root) for i in range(20)]
    # Unrelated untitled native rows must not trigger one disk lookup each.
    rows.extend(row(f'00000000-0000-4000-8000-{i:012d}') for i in range(2, 32))
    topics.attach_topics(rows, names={})
    assert calls == [root]
    assert all(item['continuation_topic_title'] == 'NATIVE TOPIC' for item in rows[:20])


def test_revision_invalidates_sidebar_caches_only_on_changes(store):
    before = topics.revision()
    topics.attach_topics([row('root-session', 'TOPIC')], names={})
    established = topics.revision()
    assert established == before
    topics.attach_topics([row('root-session', 'Generated task')], names={})
    assert topics.revision() == established
    topics.record_continuation('root-session', 'child-session')
    continued = topics.revision()
    assert continued == established
    topics.rename_topic('root-session', 'RENAMED')
    assert topics.revision() > continued


def test_fresh_sidebar_projection_resolves_topic_after_live_names(store, monkeypatch):
    topics.record_continuation('root-session', 'child-session')
    monkeypatch.setattr(server, '_archive_list_title_overlays', lambda: {
        'child-session': {'task_title': 'Current task'}})
    monkeypatch.setattr(server, '_load_session_name_overrides', lambda: {'root-session': 'PROJECT: original'})
    rows = server._archive_list_project_rows([row('child-session', 'Current task')])
    assert rows[0]['continuation_topic_title'] == 'PROJECT'
    assert rows[0]['continuation_topic_session_id'] == 'root-session'
    assert rows[0]['task_title'] == 'Current task'


def test_topic_revision_rejects_delta_even_when_renamed_root_is_unloaded(monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_DELTA_CTX_HIST', OrderedDict())
    monkeypatch.setattr(server._session_graph, 'parent_snapshot', lambda: {})
    server._archive_delta_ctx_remember('before', 'scope', 'all', '{}', '{}', '{}', 1)
    server._archive_delta_ctx_remember('after', 'scope', 'all', '{}', '{}', '{}', 2)
    assert server._archive_delta_ctx_changed_sids('before', 'after') is None


def test_archive_topics_remain_readable_while_index_writer_is_busy(store):
    store.execute('PRAGMA journal_mode=WAL')
    topics.record_continuation('root-session', 'child-session', title='TOPIC', conn=store)
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    writer = sqlite3.connect(filename)
    store.execute('PRAGMA busy_timeout=50')
    writer.execute('BEGIN IMMEDIATE')
    try:
        rows = [row('child-session'), row('new-child', origin='root-session')]
        topics.attach_topics(rows, conn=store, names={})
        assert [item['continuation_topic_title'] for item in rows] == ['TOPIC', 'TOPIC']
        assert not store.in_transaction
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            topics.record_continuation('root-session', 'durable-child', conn=store)
        assert store.execute('PRAGMA busy_timeout').fetchone()[0] == 50
    finally:
        writer.rollback()
        writer.close()
    topics.attach_topics(rows, conn=store, names={})
    assert store.execute('SELECT root_sid FROM continuation_topics WHERE sid=?',
                         ('new-child',)).fetchone() == ('root-session',)


def test_native_title_lookup_releases_writer_and_rechecks_peer_rename(store, monkeypatch):
    root = '00000000-0000-4000-8000-000000000099'
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    peer = sqlite3.connect(filename, timeout=0.05)
    topics.attach_topics([row(root, 'OLD TOPIC')], conn=store, names={})
    # Missing title requires native lookup, concurrent with a user rename.
    store.execute('UPDATE continuation_topics SET topic_title=? WHERE sid=?', ('', root))
    store.commit()
    def native_title(sid):
        assert not store.in_transaction
        topics.rename_topic(sid, 'USER TOPIC', conn=peer)
        return {'title': 'STALE GENERATED TOPIC'}
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    monkeypatch.setattr(server, '_codex_thread_row', native_title)
    child = row('new-child', origin=root)
    try:
        topics.attach_topics([child], conn=store, names={})
        assert child['continuation_topic_title'] == 'USER TOPIC'
        assert store.execute('SELECT topic_title, user_named FROM continuation_topics WHERE sid=?',
                             (root,)).fetchone() == ('USER TOPIC', 1)
    finally:
        peer.close()


def test_connection_skips_ddl_and_cached_attach_topics_invalidates_on_mutations(store):
    store.execute('PRAGMA journal_mode=WAL')
    topics.attach_topics([row('root-session', 'INITIAL TOPIC: first'),
                          row('standalone-session', 'STANDALONE: task')], names={})
    topics.record_continuation('root-session', 'child-session')
    # First read-only projection warms the in-memory cache.
    warm_rows = [row('child-session'), row('standalone-session', 'STANDALONE: task')]
    topics.attach_topics(warm_rows, names={})
    assert warm_rows[0]['continuation_topic_title'] == 'INITIAL TOPIC'
    assert 'continuation_topic_title' not in warm_rows[1]

    filename = store.execute('PRAGMA database_list').fetchone()[2]
    writer = sqlite3.connect(filename)
    reader = sqlite3.connect(filename, timeout=0.05)
    queries = []
    store.set_trace_callback(queries.append)
    reader_queries = []
    reader.set_trace_callback(reader_queries.append)
    writer.execute('BEGIN IMMEDIATE')
    try:
        # _connection() on an already-initialized connection issues zero SQL;
        # on a fresh connection to an initialized DB it avoids CREATE TABLE DDL.
        assert topics._connection(store) is store
        assert queries == []
        assert topics._connection(reader) is reader
        assert not any('CREATE TABLE' in q for q in reader_queries)
        assert topics.revision() >= 0
        assert queries == ['SELECT revision FROM continuation_topic_meta WHERE id=1']
        queries.clear()
        assert topics.revision() >= 0
        assert queries == []
        cached_rows = [row('child-session'), row('standalone-session', 'STANDALONE: task')]
        topics.attach_topics(cached_rows, names={})
        assert cached_rows[0]['continuation_topic_title'] == 'INITIAL TOPIC'
        assert 'continuation_topic_title' not in cached_rows[1]
        assert queries == []
    finally:
        writer.rollback()
        writer.close()
        reader.close()

    # rename_topic invalidates the cache immediately.
    topics.rename_topic('root-session', 'RENAMED TOPIC: updated')
    renamed_rows = [row('child-session')]
    topics.attach_topics(renamed_rows, names={})
    assert renamed_rows[0]['continuation_topic_title'] == 'RENAMED TOPIC'

    # Subsequent calls hit the refreshed cache with zero SQL queries.
    queries.clear()
    again_rows = [row('child-session')]
    topics.attach_topics(again_rows, names={})
    assert again_rows[0]['continuation_topic_title'] == 'RENAMED TOPIC'
    assert queries == []

    # record_continuation also invalidates the cache immediately for new edges.
    topics.record_continuation('child-session', 'grandchild-session')
    chain_rows = [row('child-session'), row('grandchild-session')]
    topics.attach_topics(chain_rows, names={})
    assert [item['continuation_topic_title'] for item in chain_rows] == ['RENAMED TOPIC', 'RENAMED TOPIC']


@pytest.mark.parametrize('variable_limit', [None, 1200])
def test_topic_prefetch_has_one_python_result_per_chunk(store, variable_limit):
    sids = [f'session-{i}' for i in range(3200)]
    store.executemany('INSERT INTO session_meta VALUES (?,?)', [(sid, '') for sid in sids])
    topics._connection(store)
    callbacks = []

    def factory(cursor, result):
        callbacks.append(result)
        return result

    getlimit = getattr(store, 'getlimit', None)
    if variable_limit is not None:
        if getlimit is None:
            pytest.skip('SQLite variable limits are unavailable')
        store.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)
    chunk = min(3000, getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)) if getlimit else 500
    checkpoints = []
    store.row_factory = factory
    batch = topics._Batch(
        store, [row(sid, 'Standalone') for sid in sids], {},
        check_client=lambda: checkpoints.append(len(callbacks)),
    )
    assert len(batch.records) == len(sids)
    assert len(batch.indexed_origins) == len(sids)
    assert len(callbacks) == (len(sids) + chunk - 1) // chunk
    assert checkpoints == list(range(len(callbacks)))


def test_topic_prefetch_preserves_sparse_and_missing_index_rows(store):
    topics._connection(store)
    store.execute('INSERT INTO continuation_topics VALUES (?,?,?,?,?)',
                  ('child', 'root', 'root', 'TOPIC', 0))
    store.execute('INSERT INTO session_meta VALUES (?,?)', ('index-only', 'root'))
    batch = topics._Batch(store, [row(sid) for sid in ('child', 'index-only', 'absent')], {})
    assert batch.records == {'child': ('root', 'root', 'TOPIC', 0),
                             'index-only': ('', '', '', 0), 'absent': ('', '', '', 0)}
    assert batch.indexed_origins == {'child': '', 'index-only': 'root', 'absent': ''}
    store.execute('DROP TABLE session_meta')
    batch = topics._Batch(store, [row('child'), row('absent')], {})
    assert batch.records['child'] == ('root', 'root', 'TOPIC', 0)
    assert batch.records['absent'] == ('', '', '', 0)
    assert batch.indexed_origins == {'child': '', 'absent': ''}


@pytest.mark.parametrize('fallback', [None, 'old_schema', 'dense', 'real', 'blob'])
def test_large_topic_prefetch_preserves_exact_results(store, monkeypatch, fallback):
    topics._connection(store)
    if fallback != 'old_schema':
        store.execute('CREATE INDEX idx_session_meta_continuation_origin '
                      'ON session_meta(continuation_origin)')
    store.executemany('INSERT INTO session_meta VALUES (?,?)',
                      [('indexed', 'root'), ('empty', ''), ('null', None),
                       ('unselected', 'other-root')])
    if fallback == 'blob':
        store.execute('UPDATE session_meta SET continuation_origin=? WHERE sid=?',
                      (b'legacy-root', 'indexed'))
    store.execute('INSERT INTO continuation_topics VALUES (?,?,?,?,?)',
                  ('child', 'root', 'root', 'TOPIC',
                   0.12345678901234567 if fallback == 'real' else 0))
    rows = [row(sid) for sid in ('child', 'indexed', 'empty', 'null', 'absent')]
    expected = topics._Batch(store, rows, {})
    monkeypatch.setattr(topics, '_SCOPED_TOPIC_SPLIT_MIN', 1)
    if fallback == 'dense':
        monkeypatch.setattr(topics, '_SCOPED_TOPIC_ORIGIN_LIMIT', 1)
    legacy_calls = []
    original = topics._sqlite_fetch_rows

    def legacy(*args):
        legacy_calls.append(True)
        return original(*args)

    monkeypatch.setattr(topics, '_sqlite_fetch_rows', legacy)
    actual = topics._Batch(store, rows, {})
    assert actual.records == expected.records
    assert actual.indexed_origins == expected.indexed_origins
    assert legacy_calls == ([True] if fallback else [])


def test_topic_cache_survives_unrelated_peer_writes_and_noop_updates(store, monkeypatch):
    store.execute('PRAGMA journal_mode=WAL')
    topics.attach_topics([row('root', 'TOPIC: task'), row('child', origin='root')], names={})
    topics.attach_topics([row('child')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    before = topics._db_state_token(store)
    assert before[0] == 'topics-v1'
    with sqlite3.connect(filename) as peer:
        peer.execute('INSERT INTO session_meta VALUES (?, ?)', ('unrelated', ''))
        peer.execute('UPDATE continuation_topics SET topic_title=topic_title')
    assert topics._db_state_token(store) == before

    def unexpected(*args, **kwargs):
        raise AssertionError('unchanged topics must not be resolved again')

    monkeypatch.setattr(topics, '_Batch', unexpected)
    fresh = row('child')
    topics.attach_topics([fresh], names={})
    assert fresh['continuation_topic_title'] == 'TOPIC'


@pytest.mark.parametrize('table,insert,update,delete', [
    ('continuation_topics',
     "INSERT INTO continuation_topics VALUES ('tracked', '', 'tracked', 'TITLE', 0)",
     "UPDATE continuation_topics SET sid='moved' WHERE sid='tracked'",
     "DELETE FROM continuation_topics WHERE sid='moved'"),
    ('continuation_topic_meta',
     'INSERT INTO continuation_topic_meta VALUES (99, 10)',
     'UPDATE continuation_topic_meta SET id=100 WHERE id=99',
     'DELETE FROM continuation_topic_meta WHERE id=100'),
    ('session_meta',
     "INSERT INTO session_meta VALUES ('tracked', 'root')",
     "UPDATE session_meta SET sid='moved' WHERE sid='tracked'",
     "DELETE FROM session_meta WHERE sid='moved'"),
])
def test_precise_topic_token_tracks_peer_identity_insert_update_delete(store, table, insert, update, delete):
    topics._connection(store)
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    previous = topics._db_state_token(store)
    assert previous[0] == 'topics-v1'
    with sqlite3.connect(filename) as peer:
        for sql in (insert, update, delete):
            peer.execute(sql)
            peer.commit()
            current = topics._db_state_token(store)
            assert current != previous, (table, sql)
            previous = current


def test_topic_cache_uses_live_indexed_origin_after_peer_update(store):
    topics.attach_topics([row('root', 'ROOT: task'), row('other', 'OTHER: task'),
                          row('child', origin='root')], names={})
    store.execute('INSERT INTO session_meta VALUES (?, ?)', ('child', 'root'))
    store.commit()
    topics.attach_topics([row('child')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    with sqlite3.connect(filename) as peer:
        peer.execute("UPDATE continuation_topics SET origin='other', root_sid='other' WHERE sid='child'")
        peer.execute("UPDATE session_meta SET continuation_origin='other' WHERE sid='child'")
    child = row('child')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'OTHER'
    assert child['continuation_topic_root_session_id'] == 'other'


def test_precise_token_handles_uncommitted_changes_rollback_and_missing_trigger(store):
    topics._connection(store)
    baseline = topics._db_state_token(store)
    assert baseline[0] == 'topics-v1'
    store.execute("INSERT INTO session_meta VALUES ('child', 'root')")
    assert topics._db_state_token(store) != baseline
    store.rollback()
    assert topics._db_state_token(store) == baseline
    store.execute('DROP TRIGGER ccc_topic_cache_session_meta_update')
    store.commit()
    assert topics._db_state_token(store)[0] != 'topics-v1'


def test_tracking_install_never_waits_for_writer_or_commits_caller(store):
    topics._connection(store)
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    for name, in store.execute("SELECT name FROM sqlite_master WHERE name LIKE 'ccc_topic_cache_%'").fetchall():
        store.execute('DROP TRIGGER ' + name)
    store.execute('DROP TABLE continuation_topic_cache_state')
    store.commit()
    with sqlite3.connect(filename) as peer:
        peer.execute('BEGIN IMMEDIATE')
        topics._install_topic_tracking(store)
        assert topics._db_state_token(store)[0] != 'topics-v1'
    store.execute("INSERT INTO session_meta VALUES ('pending', '')")
    topics._install_topic_tracking(store)
    assert store.in_transaction
    store.rollback()
    assert store.execute("SELECT count(*) FROM session_meta WHERE sid='pending'").fetchone()[0] == 0


def test_precise_token_without_thread_connection_reuses_peer_revision(store, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    store.execute('PRAGMA journal_mode=WAL')
    topics._connection(store)
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    monkeypatch.setattr(topics._sg, '_get_connection', topics._ORIG_GET_CONNECTION)
    monkeypatch.setattr(topics._sg, '_tls', SimpleNamespace())
    monkeypatch.setattr(topics._sg, '_get_db_path', lambda: Path(filename))
    before = topics._db_state_token()
    assert before[0] == 'topics-v1'
    with sqlite3.connect(filename) as peer:
        peer.execute("INSERT INTO session_meta VALUES ('unrelated', '')")
    assert topics._db_state_token() == before
    with sqlite3.connect(filename) as peer:
        peer.execute("UPDATE session_meta SET continuation_origin='root' WHERE sid='unrelated'")
    assert topics._db_state_token() != before


def test_topic_cache_reuses_projection_with_preopened_ship_index(store, monkeypatch):
    from contextlib import closing
    from types import SimpleNamespace

    store.execute('PRAGMA journal_mode=WAL')
    topics.attach_topics([row('root', 'ROOT: task'), row('child', origin='root')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    monkeypatch.setattr(topics._sg, '_get_connection', topics._ORIG_GET_CONNECTION)

    with closing(sqlite3.connect(filename)) as reader:
        reader.execute('SELECT sid FROM session_meta').fetchall()
        monkeypatch.setattr(topics._sg, '_tls', SimpleNamespace(conn=reader))
        child = row('child')
        diagnostics = {}
        topics.attach_topics([child], names={}, diagnostics=diagnostics)
        assert diagnostics['hit'] and diagnostics['reason'] == 'hit'
        assert child['continuation_topic_title'] == 'ROOT'
        assert reader.total_changes == 0

    with closing(sqlite3.connect(filename)) as reader:
        monkeypatch.setattr(topics._sg, '_tls', SimpleNamespace(conn=reader))
        new_child = row('new-child', origin='child')
        diagnostics = {}
        topics.attach_topics([row('child'), new_child], names={}, diagnostics=diagnostics)
        assert diagnostics['hit'] is False
        assert diagnostics['reason'] == 'new-session'
        assert diagnostics['miss_detail']['missing_rows'] == 1
        assert new_child['continuation_topic_root_session_id'] == 'root'
        assert reader.execute(
            'SELECT origin, root_sid FROM continuation_topics WHERE sid=?',
            ('new-child',),
        ).fetchone() == ('child', 'root')


def test_topic_cache_discovers_new_indexed_origin_after_peer_write(store):
    topics.attach_topics([row('root', 'ROOT: task'), row('standalone', 'CHILD: task')], names={})
    store.execute("INSERT INTO session_meta VALUES ('standalone', '')")
    store.commit()
    topics.attach_topics([row('standalone')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    with sqlite3.connect(filename) as peer:
        peer.execute("UPDATE session_meta SET continuation_origin='root' WHERE sid='standalone'")
    child = row('standalone')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'ROOT'
    assert child['continuation_topic_root_session_id'] == 'root'


def test_topic_cache_observes_direct_peer_root_rename(store):
    topics.attach_topics([row('root', 'BEFORE: task'), row('child', origin='root')], names={})
    topics.attach_topics([row('child')], names={})
    filename = store.execute('PRAGMA database_list').fetchone()[2]
    with sqlite3.connect(filename) as peer:
        peer.execute("UPDATE continuation_topics SET topic_title='AFTER' WHERE sid='root'")
    child = row('child')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'AFTER'


def test_precise_token_invalidates_when_schema_changes(store):
    store.execute('PRAGMA journal_mode=WAL')
    topics._connection(store)
    before = topics._db_state_token(store)
    store.execute('CREATE TABLE unrelated_schema_change (value)')
    store.commit()
    assert topics._db_state_token(store) != before


def test_topic_cache_ignores_equivalent_native_title_snapshot_replacement(store, monkeypatch):
    data = {'native': {'title': 'NATIVE', 'agent_nickname': 'Helper', 'first_user_message': 'old'}}
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': data})
    topics.attach_topics([row('root', 'ROOT: task'), row('child', origin='root')], names={})
    topics.attach_topics([row('child')], names={})
    before = topics._db_state_token(store)
    server._codex_titles_cache['data'] = {
        'native': {'title': 'NATIVE', 'agent_nickname': 'Helper', 'first_user_message': 'new',
                   'agent_role': 'reviewer', 'agent_path': 'different'},
    }
    assert topics._db_state_token(store) == before

    def unexpected(*args, **kwargs):
        raise AssertionError('equivalent native titles must not reproject topics')

    monkeypatch.setattr(topics, '_Batch', unexpected)
    child = row('child')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'ROOT'


@pytest.mark.parametrize('native', [
    {'title': 'DISCOVERED: task', 'agent_nickname': ''},
    {'title': '', 'agent_nickname': 'DISCOVERED: task'},
])
def test_topic_cache_discovers_native_title_or_nickname_after_empty_snapshot(store, monkeypatch, native):
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    topics.attach_topics([row('root')], names={})
    topics.attach_topics([row('root')], names={})
    before = topics._db_state_token(store)
    server._codex_titles_cache['data'] = {'root': native}
    assert topics._db_state_token(store) != before
    topics.attach_topics([row('root')], names={})
    assert store.execute("SELECT topic_title FROM continuation_topics WHERE sid='root'").fetchone()[0] == 'DISCOVERED'
    child = row('child', origin='root')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'DISCOVERED'
    assert store.execute("SELECT topic_title FROM continuation_topics WHERE sid='root'").fetchone()[0] == 'DISCOVERED'


@pytest.mark.parametrize('replacement', [
    {'native': {'title': 'AFTER', 'agent_nickname': 'Helper'}},
    {'native': {'title': 'BEFORE', 'agent_nickname': 'Changed'}},
    {'different': {'title': 'BEFORE', 'agent_nickname': 'Helper'}},
    {},
    {'native': None},
])
def test_external_title_signature_keeps_real_native_changes(monkeypatch, replacement):
    monkeypatch.setattr(server, '_codex_titles_cache', {
        'data': {'native': {'title': 'BEFORE', 'agent_nickname': 'Helper'}},
    })
    before = topics._external_title_cache_sig()
    server._codex_titles_cache['data'] = replacement
    assert topics._external_title_cache_sig() != before


def test_external_title_signature_compares_only_once_per_native_snapshot(monkeypatch):
    class Snapshot(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    monkeypatch.setattr(server, '_codex_titles_cache', {
        'data': {'native': {'title': 'SAME', 'agent_nickname': ''}},
    })
    before = topics._external_title_cache_sig()
    replacement = Snapshot({'native': {'title': 'SAME', 'agent_nickname': ''}})
    server._codex_titles_cache['data'] = replacement
    for _ in range(20):
        assert topics._external_title_cache_sig() == before
    assert replacement.scans == 1


def test_topic_cache_reuses_persisted_titles_across_metadata_churn(store, monkeypatch):
    metadata = {'unrelated.jsonl': {'ai_title': 'Unrelated'}}
    monkeypatch.setattr(server, '_conv_meta_cache', metadata)
    topics.attach_topics([row('root', 'ROOT'), row('child', origin='root'), row('untitled')], names={})
    metadata['new-session.jsonl'] = {'ai_title': 'New session'}
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {'other': {'title': 'Changed'}}})

    def unexpected(*args, **kwargs):
        raise AssertionError('unrelated fallback titles must not rebuild established topics')

    monkeypatch.setattr(topics, '_Batch', unexpected)
    items = [row('child', origin='root'), row('untitled')]
    detail = {}
    topics.attach_topics(items, names={}, diagnostics=detail)
    assert detail['hit'] and detail['reason'] == 'hit'
    assert items[0]['continuation_topic_title'] == 'ROOT'
    assert 'continuation_topic_title' not in items[1]


@pytest.mark.parametrize('field', ['custom_title', 'agent_name', 'ai_title'])
def test_topic_cache_observes_in_place_fallback_title_discovery(store, monkeypatch, field):
    metadata = {'root.jsonl': {}}
    monkeypatch.setattr(server, '_conv_meta_cache', metadata)
    topics.attach_topics([row('root')], names={})
    # The map's identity and count stay fixed when a parser discovers a title.
    metadata['root.jsonl'][field] = 'DISCOVERED: detail'
    topics.attach_topics([row('root')], names={})
    child = row('child', origin='root')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'DISCOVERED'


def test_topic_cache_keeps_persisted_title_when_fallback_changes(store, monkeypatch):
    metadata = {'root.jsonl': {'ai_title': 'BEFORE'}}
    monkeypatch.setattr(server, '_conv_meta_cache', metadata)
    topics.attach_topics([row('root'), row('child', origin='root')], names={})
    metadata['root.jsonl']['ai_title'] = 'AFTER'
    metadata['unrelated.jsonl'] = {}
    child = row('child', origin='root')
    topics.attach_topics([child], names={})
    assert child['continuation_topic_title'] == 'BEFORE'


def test_archive_projection_inputs_batch_native_titles(store, monkeypatch):
    roots = [f'{digit}1111111-1111-4111-8111-111111111111' for digit in '123']
    for i, root in enumerate(roots):
        topics.attach_topics([row(root), row(f'child-{i}', origin=root)], names={})
    monkeypatch.setattr(topics, '_archive_fallback_titles', lambda: {})
    calls = []

    def fetch(*, columns, exact_ids):
        calls.append((columns, exact_ids))
        return [
            {'id': roots[0], 'title': ' Native title ', 'agent_nickname': 'Ignored'},
            {'id': roots[1], 'title': '', 'agent_nickname': 'Nickname'},
        ]

    monkeypatch.setattr(server, '_codex_fetch_threads', fetch)
    inputs, _, _ = topics.archive_projection_inputs(store, max_rows=20)

    assert calls == [(('id', 'title', 'agent_nickname'), tuple(roots))]
    assert inputs[3] == dict(zip(roots, (' Native title ', 'Nickname', None)))


def test_archive_projection_inputs_capture_topic_and_native_title_sources(store, monkeypatch):
    root = '11111111-1111-4111-8111-111111111111'
    topics.attach_topics([row(root), row('child', origin=root)], names={})
    store.execute("INSERT INTO session_meta VALUES ('indexed', ?)", (root,))
    store.commit()
    monkeypatch.setattr(server, '_conv_meta_cache', {'/sessions/solo.jsonl': {'ai_title': 'External: agenda'}})
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {'cached': {'agent_nickname': 'Cached'}}})
    monkeypatch.setattr(topics, '_native_title', lambda sid: 'Native found' if sid == root else None)

    inputs, edges, version = topics.archive_projection_inputs(store, max_rows=20)
    persisted, indexed, fallback, native = inputs
    assert persisted['child'][:2] == (root, root)
    assert indexed == {'indexed': root}
    assert fallback == {'solo': 'External: agenda', 'cached': 'Cached'}
    assert native == {root: 'Native found'}
    assert ('child', root) in edges and ('indexed', root) in edges
    assert topics.archive_projection_inputs(store, max_rows=20, max_native_roots=0) is None
    assert topics.archive_projection_inputs(store, max_rows=1) is None
    rows = [row('child', origin=root), row('solo')]
    topics.attach_archive_topics(rows, conn=store, names={}, inputs=inputs, version=version)
    assert rows[0]['continuation_topic_title'] == 'Native found'
    assert store.execute(
        "SELECT topic_title FROM continuation_topics WHERE sid='solo'"
    ).fetchone() == ('External',)


@pytest.mark.parametrize("title, named", [
    ('Normal "title"\n with unicode é', -9223372036854775808),
    ("Embedded\0separator", 1),
    (b"\xfflegacy blob", "legacy flag"),
])
def test_archive_topic_batch_preserves_types_and_order(store, title, named):
    topics._connection(store)
    root = '11111111-1111-4111-8111-111111111111'
    store.executemany("INSERT INTO continuation_topics VALUES (?,?,?,?,?)", [
        ('child', root, root, title, named),
        ('self', 'self', 'self', '', 0),
    ])
    store.execute("INSERT INTO session_meta VALUES (?,?)", ('indexed', root))

    by_sid, indexed, roots, edges = topics._archive_topic_records(store, 2)
    assert list(by_sid.items()) == [
        ('child', (root, root, title, named)),
        ('self', ('self', 'self', '', 0)),
    ]
    assert indexed == {'indexed': root}
    assert roots == {root}
    assert edges == (('child', root), ('child', root), ('indexed', root))
    assert topics._archive_topic_records(store, 1) is None


def test_archive_indexed_origins_keep_scan_order_and_legacy_values(store):
    topics._connection(store)
    store.execute("CREATE INDEX idx_session_meta_continuation_origin ON session_meta(continuation_origin)")
    store.executemany("INSERT INTO session_meta VALUES (?, ?)", [
        ('first', 'z'), ('empty', ''), ('second', 'a'), ('missing', None), ('legacy', b'root'),
    ])

    _, indexed, _, edges = topics._archive_topic_records(store, 3)

    assert list(indexed.items()) == list(edges) == [('first', 'z'), ('second', 'a'), ('legacy', b'root')]
    assert topics._archive_topic_records(store, 2) is None


def test_archive_title_snapshot_reuses_metadata_until_title_changes(monkeypatch):
    from ccc_server.log_parse import _ConvMetaCache

    first, duplicate = '/old/shared.jsonl', '/new/shared.jsonl'
    cache = _ConvMetaCache({first: {'custom_title': 'First'}})
    monkeypatch.setattr(server, '_conv_meta_cache', cache)
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {'shared': {'title': 'Native'}}})

    assert topics._external_titles() == {'shared': 'First'}
    snapshot = cache.title_snapshot
    assert topics._archive_fallback_titles() == {'shared': 'First'}
    cache[first] = {'custom_title': 'First', 'mtime': 42}
    assert topics._external_titles() is snapshot
    assert topics._archive_fallback_titles() == {'shared': 'First'}
    assert cache.title_snapshot is snapshot

    cache[first] = {'custom_title': 'Renamed', 'mtime': 42}
    assert topics._external_titles() == {'shared': 'Renamed'}
    assert topics._archive_fallback_titles() == {'shared': 'Renamed'}
    assert snapshot == {'shared': 'First'}

    cache[duplicate] = {'mtime': 43}
    assert topics._external_title(topics._external_titles(), 'shared') == 'Native'
    assert topics._archive_fallback_titles() == {'shared': 'Native'}
    cache[first] = {'custom_title': 'Shadowed'}
    assert topics._archive_fallback_titles() == {'shared': 'Native'}
    cache.pop(duplicate)
    assert topics._archive_fallback_titles() == {'shared': 'Shadowed'}
    cache.update({first: {'agent_name': 'Agent', 'ai_title': 'Automatic'}})
    assert topics._archive_fallback_titles() == {'shared': 'Agent'}
    cache.clear()
    assert topics._archive_fallback_titles() == {'shared': 'Native'}


def test_archive_projection_reuses_committed_records_but_refreshes_titles(store, monkeypatch):
    from contextlib import closing

    root = '11111111-1111-4111-8111-111111111111'
    topics.attach_topics([row(root), row('child', origin=root)], names={})
    metadata = {'/sessions/solo.jsonl': {'ai_title': 'First external title'}}
    monkeypatch.setattr(server, '_conv_meta_cache', metadata)
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    monkeypatch.setattr(topics, '_native_title', lambda sid: 'First native title')
    cold = {}
    first, first_edges, version = topics.archive_projection_inputs(
        store, max_rows=20, diagnostics=cold)
    assert set(cold["topics_phases"]) == {"fallback_titles", "revision", "records", "native_titles"}
    assert set(cold["topics_records_phases"]) == {"topic_rows", "indexed_origins", "build_edges"}
    assert cold["topics_records_tid"] > 0

    metadata['/sessions/solo.jsonl']['ai_title'] = 'Second external title'
    monkeypatch.setattr(topics, '_native_title', lambda sid: 'Second native title')
    warm = {}
    second, second_edges, repeat_version = topics.archive_projection_inputs(
        store, max_rows=20, diagnostics=warm)
    assert set(warm["topics_phases"]) == {"fallback_titles", "revision", "native_titles"}
    assert "topics_records_phases" not in warm
    assert "topics_records_tid" not in warm
    for phases in (cold["topics_phases"], cold["topics_records_phases"], warm["topics_phases"]):
        for phase in phases.values():
            assert phase["wall_ms"] >= 0
            assert phase["cpu_ms"] >= 0
    assert second[0] is first[0]
    assert second_edges is first_edges
    assert repeat_version == version
    assert second[2:] == ({'solo': 'Second external title'}, {root: 'Second native title'})
    assert topics.archive_projection_inputs(store, max_rows=1) is None

    filename = store.execute("PRAGMA database_list").fetchone()[2]
    with closing(sqlite3.connect(filename)) as peer, peer:
        peer.execute("UPDATE continuation_topics SET topic_title='Saved title' WHERE sid=?", (root,))
        peer.execute("INSERT INTO session_meta VALUES ('new-child', ?)", (root,))
    updated, edges, new_version = topics.archive_projection_inputs(store, max_rows=20)
    assert new_version != version
    assert updated[0][root][2] == 'Saved title'
    assert updated[1] == {'new-child': root}
    assert updated[3] == {}
    assert ('new-child', root) in edges


def test_archive_topic_connection_reuses_records_across_request_threads_and_reopens(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from pathlib import Path

    database = Path(store.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(topics._sg, '_get_db_path', lambda: database)
    monkeypatch.setattr(topics, '_ARCHIVE_TOPIC_CONNECTION', (None, None))
    monkeypatch.setattr(topics, '_ARCHIVE_TOPIC_SNAPSHOT', (None, None, None))
    with store:
        store.execute("INSERT INTO session_meta VALUES ('child', 'first')")

    def capture():
        details = {}
        conn = topics.archive_connection()
        inputs, _, _ = topics.archive_projection_inputs(conn, max_rows=20, diagnostics=details)
        return conn, inputs[1], details["topics_snapshot_reused"]

    try:
        # Each executor owns a different request thread. The server's archive
        # lock serializes their use of the shared connection.
        with ThreadPoolExecutor(max_workers=1) as first, ThreadPoolExecutor(max_workers=1) as second:
            conn, indexed, reused = first.submit(capture).result()
            assert indexed == {'child': 'first'}
            assert not reused
            assert second.submit(capture).result() == (conn, indexed, True)
            with store:
                store.execute("UPDATE session_meta SET continuation_origin='renamed'")
            assert second.submit(capture).result() == (conn, {'child': 'renamed'}, False)

            replacement = database.with_name('replacement.sqlite3')
            with closing(sqlite3.connect(replacement)) as new_database, new_database:
                new_database.execute('CREATE TABLE session_meta (sid TEXT, continuation_origin TEXT)')
                new_database.execute("INSERT INTO session_meta VALUES ('child', 'replacement')")
            replacement.replace(database)
            new_conn, indexed, reused = first.submit(capture).result()
            assert new_conn is not conn
            assert indexed == {'child': 'replacement'}
            assert not reused
    finally:
        if topics._ARCHIVE_TOPIC_CONNECTION[1] is not None:
            topics._ARCHIVE_TOPIC_CONNECTION[1].close()


def test_archive_projection_retries_when_topic_sources_change_during_capture(store, monkeypatch):
    root = '11111111-1111-4111-8111-111111111111'
    topics.attach_topics([row(root), row('child', origin=root)], names={})
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    lookups = []

    def native_title(sid):
        lookups.append(sid)
        if len(lookups) == 1:
            store.execute("INSERT INTO session_meta VALUES ('new-indexed-child', ?)", (root,))
            store.commit()
        return 'Native title'

    monkeypatch.setattr(topics, '_native_title', native_title)
    inputs, edges, version = topics.archive_projection_inputs(store, max_rows=20)
    assert lookups == [root, root]
    assert inputs[1] == {'new-indexed-child': root}
    assert ('new-indexed-child', root) in edges
    assert version == topics._archive_read_version(store)


def test_archive_projection_includes_peer_commit_before_snapshot_without_retry(store, monkeypatch):
    from contextlib import closing

    store.execute('PRAGMA journal_mode=WAL')
    topics._connection(store)
    reads = []
    fetch = topics._archive_topic_records

    def record_read(conn, max_rows, **kwargs):
        reads.append(max_rows)
        return fetch(conn, max_rows, **kwargs)

    monkeypatch.setattr(topics, '_archive_topic_records', record_read)
    filename = store.execute("PRAGMA database_list").fetchone()[2]
    with closing(sqlite3.connect(filename)) as peer:
        def commit_before_snapshot(sql):
            if sql == 'BEGIN' and not peer.total_changes:
                with peer:
                    peer.execute("INSERT INTO session_meta VALUES ('child', 'root')")

        store.set_trace_callback(commit_before_snapshot)
        try:
            details = {}
            inputs, edges, version = topics.archive_projection_inputs(
                store, max_rows=20, diagnostics=details)
        finally:
            store.set_trace_callback(None)

    assert reads == [20]
    assert inputs[1] == {'child': 'root'}
    assert ('child', 'root') in edges
    assert version == topics._archive_read_version(store)
    assert 'topics_retried' not in details
    assert not store.in_transaction


def test_archive_projection_ignores_writes_outside_tracked_topics(store, monkeypatch):
    root = '11111111-1111-4111-8111-111111111111'
    store.execute('PRAGMA journal_mode=WAL')
    topics.attach_topics([row(root), row('child', origin=root)], names={})
    store.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    store.commit()
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    lookups = []
    filename = store.execute("PRAGMA database_list").fetchone()[2]

    def native_title(sid):
        lookups.append(sid)
        with sqlite3.connect(filename) as peer:
            peer.execute("INSERT INTO unrelated DEFAULT VALUES")
        return 'Native title'

    monkeypatch.setattr(topics, '_native_title', native_title)
    inputs, _, version = topics.archive_projection_inputs(store, max_rows=20)
    assert lookups == [root]
    assert inputs[-1] == {root: 'Native title'}
    assert version == topics._archive_read_version(store)


def test_archive_projection_collects_unloaded_detached_metadata_titles(store, tmp_path, monkeypatch):
    from contextlib import closing

    from ccc_server import conv_meta_store, log_parse

    metadata = tmp_path / 'conversation-meta.json'
    version = log_parse._CONV_META_SCHEMA_VERSION
    conv_meta_store.save(metadata, version, {
        'alpha.jsonl': {'ai_title': 'Alpha title'},
        'beta.jsonl': {'custom_title': 'Beta title', 'ai_title': 'Lower priority'},
        'legacy.jsonl': {'agent_name': 'Legacy title', 'elapsed': float('nan')},
    })
    topics._connection(store)
    monkeypatch.setattr(server, '_codex_titles_cache', {'data': {}})
    with closing(conv_meta_store.open_reader(metadata)) as connection:
        detached = log_parse._DetachedConvMetaCache(connection)
        detached['alpha.jsonl'] = {'agent_name': 'Updated title in this process'}
        monkeypatch.setattr(server, '_conv_meta_cache', detached)
        assert topics._external_titles() == {'alpha': 'Updated title in this process'}
        inputs, _, _ = topics.archive_projection_inputs(store, max_rows=20)
        assert inputs[2] == {
            'alpha': 'Updated title in this process',
            'beta': 'Beta title',
            'legacy': 'Legacy title',
        }
        assert set(detached) == {'alpha.jsonl'}
