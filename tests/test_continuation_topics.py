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
    assert all(' WHERE sid=' in query for query in queries if query.startswith('SELECT '))


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
    server._archive_delta_ctx_remember('before', 'scope', 'all', {}, {}, {}, 1)
    server._archive_delta_ctx_remember('after', 'scope', 'all', {}, {}, {}, 2)
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
