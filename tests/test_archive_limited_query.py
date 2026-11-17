"""An existing sessions cap must bound cold decoding without changing rows."""
import json
import pytest
import server
from ccc_server import archive_store
from tests.test_archive_inventory import OPTS


@pytest.fixture
def archive(tmp_path, monkeypatch):
    target = tmp_path/'archive.json'
    key = server._archive_response_cache_key(**OPTS)
    rows = [dict(session_id=f'row-{i}', source='codex', engine='codex',
                 display_name=f'Title {i}', mtime=100+i, first_message='request',
                 parser_detail={'unused': 'x'*10000}) for i in range(1200)]
    snapshot = {'schema_version':server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
                'build_id':server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
                'entries':{key:{'cached_at':100,'conversations':rows}},'signature_maps':{}}
    archive_store.save(target,snapshot,complete=True)
    hot = [dict(rows[1], display_name='Live rename', is_live=True),
           dict(session_id='new', source='claude')]
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {key:{'cached_at':100}})
    monkeypatch.setattr(server, '_archive_serve_hot_rows_versioned', lambda *a,**kw:(hot,True,1))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda:{'row-0','row-2'})
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda:({'row-3'},set()))
    monkeypatch.setattr(server, '_load_session_lane_overrides', lambda:{'row-4':'other'})
    expected = [dict(row) for row in rows]
    expected[1] = dict(hot[0]); expected.append(dict(hot[1]))
    expected = server._overlay_conversation_lifecycle_flags(expected)
    return target,key,rows,expected


@pytest.mark.parametrize('limit',[1,3,200,1000])
def test_limit_precedes_cold_decoding_and_retains_canonical_payload(archive, monkeypatch, limit):
    target,key,rows,expected = archive
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:pytest.fail('uncapped history'))
    loads = json.loads
    decoded = []
    def record(body):
        value = loads(body)
        if isinstance(value,dict) and 'parser_detail' in value:
            decoded.append(value['session_id'])
        return value
    monkeypatch.setattr(archive_store.json,'loads',record)
    qs = {'limit':[str(limit)],'fields':['session_id,display_name,state,archived,all_lane_override']}
    result,cached = server._archive_session_query_rows_cached(OPTS, qs)
    assert cached
    assert server._apply_session_query_params(result,qs) == server._apply_session_query_params(expected,qs)
    assert len(decoded) == limit
    assert not {'row-0','row-2'} & set(decoded)


@pytest.mark.parametrize('query',[{}, {'limit':['-1']}, {'limit':['1001']}, {'limit':['bad']},
    {'limit':['3'],'since':['123']}, {'limit':['3'],'state':['working']}, {'limit':['3'],'engine':['codex']}])
def test_complex_or_unbounded_query_keeps_compatibility_path(monkeypatch, query):
    expected = ([{'session_id':'legacy'}],True)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:expected)
    assert server._archive_session_query_rows_cached(OPTS,query) == expected


def test_empty_limit_does_not_open_archive(monkeypatch):
    monkeypatch.setattr(archive_store, 'load_head', lambda *a,**kw:pytest.fail('history opened'))
    assert server._archive_session_query_rows_cached(OPTS,{'limit':['0']}) == ([],True)


def test_short_history_appends_new_hot_rows_and_legacy_schema_falls_back(archive, monkeypatch):
    target,key,rows,expected = archive
    current = archive_store.load(target)
    current['entries'][key]['cached_at'] = 101
    current['entries'][key]['conversations'] = rows[:5]
    archive_store.save(target,current,complete=True)
    result,_ = server._archive_session_query_rows_cached(OPTS,{'limit':['20']})
    assert [r['session_id'] for r in result] == ['row-1','row-3','row-4','new']
    current['schema_version'] += 1
    current['entries'][key]['cached_at'] = 102
    archive_store.save(target,current,complete=True)
    legacy = ([{'session_id':'legacy'}],True)
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:legacy)
    assert server._archive_session_query_rows_cached(OPTS,{'limit':['20']}) == legacy


def test_sessions_endpoint_uses_capped_reader_and_keeps_compatibility_alias(archive, monkeypatch):
    from tests.test_static_asset_caching import _get
    _,_,_,expected = archive
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a:pytest.fail('uncapped endpoint read'))
    monkeypatch.setattr(server, '_archive_spawned_coalesced', lambda:[])
    monkeypatch.setattr(server, '_attach_usage_limit_resume_fields', lambda rows:None)
    status,_,body = _get(server, '/api/sessions?all=1&limit=3&fields=session_id,display_name,state')
    payload = json.loads(body)
    assert status == 200
    qs = {'limit':['3'],'fields':['session_id,display_name,state']}
    assert payload['sessions'] == server._apply_session_query_params(expected,qs)
    assert payload['conversations'] == payload['sessions']
    assert payload['count'] == 3 and payload['spawned'] == []
    _,_,body = _get(server, '/api/sessions?all=1&compact=1&limit=3&fields=session_id,display_name,state')
    assert 'conversations' not in json.loads(body)
