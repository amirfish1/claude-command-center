"""Attention polling preserves full-history behavior without parser bodies."""
import ast
import inspect
import json
import sqlite3

import pytest

import server
from ccc_server import archive_store as store

OPTS = dict(include_prs=False, resolve_pr_states=False,
            resolve_effective=False, resolve_worktree_dirty=False)


def setup_archive(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    key = server._archive_response_cache_key(**OPTS)
    now = server.time.time()
    cold = [
        dict(session_id='history', source='backlog', backlog_type='github',
             issue_number=7, issue_labels=['needs-attention'], modified=now - 30 * 86400),
        dict(session_id='live', engine='codex', is_live=True, modified=now,
             question_waiting=True, question_text='Choose a folder?',
             session_state={'did': 'Prepared choices'}),
        dict(session_id='deleted', engine='codex', has_edit=True, linked_issue=9),
        dict(session_id='old-tool', engine='codex', stale_tool_call=True,
             stale_tool_age_s=100, modified=now - 30 * 86400),
    ]
    for row in cold:
        row['parser_data'] = [{'large': 'unused history ' * 2000}]
    snapshot = dict(schema_version=server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                    build_id=server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
                    entries={key: dict(cached_at=100, conversations=cold)}, signature_maps={})
    store.save(target, snapshot, complete=True)
    hot = [dict(cold[1], question_text='Current question?'),
           dict(session_id='new', is_live=True, needs_approval=True)]
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {key: {'cached_at': 100}})
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned', lambda *a, **kw: (hot, True, 1))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: {'deleted'})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: (set(), set()))
    monkeypatch.setattr(server, '_apply_session_lane_overrides', lambda rows: None)
    monkeypatch.setattr(server, '_attention_read_turns', lambda *a, **kw: [])
    return target, key, cold, hot, snapshot


@pytest.mark.parametrize('recent', [True, False])
def test_exact_feed_parity_and_no_canonical_body_decode(tmp_path, monkeypatch, recent):
    target, key, cold, hot, _ = setup_archive(tmp_path, monkeypatch)
    expected_rows = [cold[0], hot[0], cold[3], hot[1]]
    method = server._attention_archive_rows_cached
    monkeypatch.setattr(server, '_attention_archive_rows_cached', lambda opts: (expected_rows, True))
    expected = server.compute_attention_feed(recent_only=recent)
    monkeypatch.setattr(server, '_attention_archive_rows_cached', method)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a: pytest.fail('full history read'))
    loads = json.loads

    def decode(body):
        assert 'unused history' not in body
        return loads(body)

    monkeypatch.setattr(store.json, 'loads', decode)
    assert server.compute_attention_feed(recent_only=recent) == expected
    assert 'old-tool' in {item['session_id'] for item in expected['items']}
    assert ('history' in {item['session_id'] for item in expected['items']}) == (not recent)


def test_publication_reorder_delete_and_nested_mutation(tmp_path, monkeypatch):
    target, key, cold, hot, snapshot = setup_archive(tmp_path, monkeypatch)
    load = lambda: store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                        build_id=snapshot['build_id'])
    first = load()
    first['rows'][1][1]['session_state']['did'] = 'mutated'
    assert load()['rows'][1][1]['session_state']['did'] == 'Prepared choices'
    snapshot['entries'][key].update(cached_at=101, conversations=list(reversed(cold[:2])))
    store.save(target, snapshot, complete=True)
    assert [row['session_id'] for _, row in load()['rows']] == ['live', 'history']
    server._ARCHIVE_RESPONSE_CACHE[key]['cached_at'] = 102
    assert server._attention_archive_rows_cached(OPTS)[0] == hot


@pytest.mark.parametrize('damage', ['missing', 'digest', 'stamp', 'version'])
def test_incomplete_or_older_writer_falls_back(tmp_path, monkeypatch, damage):
    target, key, _, _, _ = setup_archive(tmp_path, monkeypatch)
    with sqlite3.connect(store.database_path(target)) as con:
        if damage == 'missing':
            con.execute('DROP TABLE attention_variants')
        elif damage == 'digest':
            con.execute("UPDATE session_attention SET digest='stale'")
        elif damage == 'stamp':
            con.execute('UPDATE variants SET cached_at=200')
        else:
            con.execute("UPDATE attention_variants SET version='old'")
    expected = ([{'session_id': 'compatibility'}], True)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda opts: expected)
    assert server._attention_archive_rows_cached(OPTS) == expected


def test_projection_covers_classifier_and_feed_inputs():
    # A new classifier input must be added to the persisted projection, too.
    for function in (server._classify_attention, server._detect_soft_block,
                     server._awaits_human_decision, server.compute_attention_feed):
        for node in ast.walk(ast.parse(inspect.getsource(function))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'c'
                    and node.func.attr == 'get' and node.args
                    and isinstance(node.args[0], ast.Constant)):
                assert node.args[0].value in store.ATTENTION_FIELDS


def test_unchanged_poll_reuses_decode_but_observes_changed_rows(tmp_path, monkeypatch):
    target, key, cold, _, snapshot = setup_archive(tmp_path, monkeypatch)
    load = lambda: store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                        build_id=snapshot['build_id'])
    expected = load()
    original = json.loads
    decoded = []

    def decode(body):
        decoded.append(body)
        return original(body)

    monkeypatch.setattr(store.json, 'loads', decode)
    assert load() == expected
    assert decoded == []
    cold[1]['question_text'] = 'Updated question'
    cold[1]['session_state']['did'] = 'Updated state'
    snapshot['entries'][key]['cached_at'] += 1
    store.save(target, snapshot, complete=True)
    decoded.clear()
    result = load()
    assert len(decoded) == 1
    assert result['rows'][1][1]['question_text'] == 'Updated question'
    assert result['rows'][1][1]['session_state']['did'] == 'Updated state'


@pytest.mark.parametrize('damage', ['digest', 'stamp', 'version'])
def test_warm_decode_cache_does_not_hide_invalid_projection(tmp_path, monkeypatch, damage):
    target, key, _, _, snapshot = setup_archive(tmp_path, monkeypatch)
    load = lambda: store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                        build_id=snapshot['build_id'])
    assert load() is not None
    with sqlite3.connect(store.database_path(target)) as con:
        if damage == 'digest':
            con.execute("UPDATE session_attention SET digest='stale'")
        elif damage == 'stamp':
            con.execute('UPDATE variants SET cached_at=200')
        else:
            con.execute("UPDATE attention_variants SET version='old'")
    assert load() is None


def test_attention_decode_cache_budget_keeps_oversized_rows_readable(tmp_path, monkeypatch):
    target, key, _, _, snapshot = setup_archive(tmp_path, monkeypatch)
    cache = store.ByteBudgetCache(max_bytes=1, max_entries=1)
    monkeypatch.setattr(store, '_ATTENTION_CACHE', cache)
    first = store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                 build_id=snapshot['build_id'])
    second = store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                  build_id=snapshot['build_id'])
    assert first == second
    assert len(cache) == cache.bytes == 0


def test_nested_json_round_trip_preserves_order_and_isolates_each_read(tmp_path, monkeypatch):
    target, key, cold, _, snapshot = setup_archive(tmp_path, monkeypatch)
    cold[1]['session_state'] = {
        'text': 'Unicode: é 🙂',
        'choices': [{'values': [None, False, 1.5, 2 ** 80], 'empty': {}}, []],
    }
    cold[1]['issue_labels'] = ['needs-attention', 'example']
    snapshot['entries'][key]['cached_at'] += 1
    store.save(target, snapshot, complete=True)
    load = lambda: store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                        build_id=snapshot['build_id'])
    expected = json.dumps(store.attention_row(cold[1]), sort_keys=True)
    first = load()['rows'][1][1]
    assert json.dumps(first) == expected
    first['session_state']['choices'][0]['values'].append('changed')
    first['session_state']['choices'][1].append({'changed': True})
    first['issue_labels'].clear()
    first['question_text'] = 'Changed by consumer'
    assert json.dumps(load()['rows'][1][1]) == expected


def test_missing_projection_invalidates_warm_decoded_rows(tmp_path, monkeypatch):
    target, key, _, _, snapshot = setup_archive(tmp_path, monkeypatch)
    load = lambda: store.load_attention(target, key, schema_version=snapshot['schema_version'],
                                        build_id=snapshot['build_id'])
    assert load() is not None
    with sqlite3.connect(store.database_path(target)) as con:
        con.execute('DELETE FROM session_attention WHERE variant=? AND identity=('
                    'SELECT identity FROM rows WHERE variant=? ORDER BY position LIMIT 1)',
                    (key, key))
    assert load() is None
