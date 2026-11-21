"""Peer polls retain current hot overlays without materializing full history."""
import pytest
import server
from ccc_server import archive_store

OPTS = dict(include_prs=False, resolve_pr_states=False,
            resolve_effective=False, resolve_worktree_dirty=False)


def test_inventory_merges_hot_rows_and_filters_deleted_codex(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    key = server._archive_response_cache_key(**OPTS)
    cold = [dict(session_id='older', engine='codex', source='codex', jsonl_path='/tmp/old.jsonl',
                 display_name='Old title', parser_data=['unused'] * 500),
            dict(session_id='deleted', engine='codex', source='codex'),
            dict(session_id='history', source='claude', display_name='Historical title')]
    data = {'schema_version':server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
            'build_id':server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
            'entries':{key:{'cached_at':100,'conversations':cold}},'signature_maps':{}}
    archive_store.save(target, data, complete=True)
    hot = [dict(cold[0], display_name='Current title'), dict(session_id='new', source='claude')]
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {key:{'cached_at':100}})
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned', lambda *a,**kw:(hot,True,1))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda:{'deleted'})
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:pytest.fail('full cold history read'))
    result, cached = server._archive_inventory_rows_cached(OPTS)
    assert cached
    assert [row['session_id'] for row in result] == ['older','history','new']
    assert result[0]['display_name'] == 'Current title'
    assert result[1] == archive_store.inventory_row(cold[2])
    # Match the existing all-history preference for newer in-memory publication.
    server._ARCHIVE_RESPONSE_CACHE[key]['cached_at'] = 101
    assert server._archive_inventory_rows_cached(OPTS)[0] == hot


def test_inventory_legacy_store_uses_existing_compatibility_path(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', tmp_path/'missing.json')
    expected = ([{'session_id':'legacy'}],True)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda opts:expected)
    assert server._archive_inventory_rows_cached(OPTS) == expected


@pytest.mark.parametrize('limit', [1, 3, 200, 1000])
def test_bounded_inventory_preserves_peer_payload_and_stable_ties(tmp_path, monkeypatch, limit):
    from ccc_server import fleet
    target = tmp_path / 'archive.json'
    key = server._archive_response_cache_key(**OPTS)
    cold = [dict(session_id=f'row-{i}', source='codex', engine='codex',
                 mtime=(i % 11) * 100, timestamp='2024-01-01T00:00:00Z',
                 first_message='message' * 60, display_name=f'Title {i}') for i in range(1200)]
    cold[0].update(mtime=None, timestamp='2026-01-01T00:00:00+02:00')
    data = {'schema_version':server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
            'build_id':server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
            'entries':{key:{'cached_at':100,'conversations':cold}},'signature_maps':{}}
    archive_store.save(target, data, complete=True)
    hot = [dict(cold[19], display_name='Renamed live session', is_live=True),
           dict(cold[5], mtime=1_900_000_000),
           dict(session_id='new', source='claude', mtime=1_900_000_001)]
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {key:{'cached_at':100}})
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned', lambda *a,**kw:(hot,True,1))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda:{'row-9'})
    monkeypatch.setattr(fleet, '_federation_lease_owners', lambda:{'row-19':'elsewhere'})
    monkeypatch.setattr(fleet.federation, 'load_repo_map', lambda:{})
    method = server._archive_inventory_rows_cached
    expected_rows = [dict(row) for row in cold]
    expected_rows[19] = hot[0]
    expected_rows[5] = hot[1]
    expected_rows.append(hot[2])
    expected_rows = [r for r in expected_rows if r['session_id'] != 'row-9']
    monkeypatch.setattr(server, '_archive_inventory_rows_cached', lambda *a,**kw:(expected_rows,True))
    expected = fleet._federation_sessions_inventory(limit)['sessions']
    monkeypatch.setattr(server, '_archive_inventory_rows_cached', method)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:pytest.fail('full history materialized'))
    assert fleet._federation_sessions_inventory(limit)['sessions'] == expected


def test_oversized_inventory_reuses_packed_rows_until_next_publication(tmp_path, monkeypatch):
    from ccc_server import fleet
    from ccc_server.bounded_cache import ByteBudgetCache

    target = tmp_path / 'archive.json'
    key = 'base'
    rows = [
        {'session_id': sid, 'engine': 'codex', 'mtime': mtime,
         'display_name': 'x' * 400, 'model': {'nested': ['value']}}
        for sid, mtime in [('first', 20), ('live', 40), ('second', 20), ('deleted', 30)]
    ]
    snapshot = {'schema_version': 1, 'build_id': '1',
                'entries': {key: {'cached_at': 1, 'conversations': rows}}}
    archive_store.save(target, snapshot, complete=True)
    monkeypatch.setattr(archive_store, '_INVENTORY_CACHE', ByteBudgetCache(4096, 2))
    read_generation = archive_store._read_inventory_generation
    reads = []

    def tracked_read(*args):
        reads.append(1)
        return read_generation(*args)

    monkeypatch.setattr(archive_store, '_read_inventory_generation', tracked_read)
    options = dict(schema_version=1, limit=2, activity_key=fleet._federation_row_activity,
                   skip_identities={archive_store.identity(rows[1])}, deleted_codex={'deleted'})
    first = archive_store.load_inventory(target, key, **options)
    assert [row['session_id'] for row in first['conversations']] == ['first', 'second']
    first['conversations'][0]['model']['nested'].append('local edit')
    repeat = archive_store.load_inventory(target, key, **options)
    assert repeat['conversations'][0]['model'] == {'nested': ['value']}
    assert repeat['positions'] == first['positions']
    assert len(reads) == 1

    snapshot['entries'][key]['cached_at'] = 2
    rows[2]['mtime'] = 25
    archive_store.save(target, snapshot, complete=True)
    updated = archive_store.load_inventory(target, key, **options)
    assert [row['session_id'] for row in updated['conversations']] == ['second', 'first']
    assert updated['cached_at'] == 2
    assert len(reads) == 2
