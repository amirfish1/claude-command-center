"""Portable host transfer through disposable native HTTP APIs and real journal."""
import copy
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import pytest
import server
from ccc_server import settings_profiles as profiles, configuration_history as history
from tests.test_configuration_history import state, api  # isolated native server fixtures


def text(values,**metadata):
    return json.dumps({'format':profiles.FORMAT,'version':1,'scope':'machine','values':values,**metadata})


def client(api):
    request,store=api
    return lambda body=None:request('/api/settings/profiles',body)


def preview(api,values,chosen=None):
    call=client(api);data={'action':'preview_import','profile':text(values),'fields':list(values) if chosen is None else chosen}
    code,view=call(data);assert code==200,view
    return data,view


def apply(api,data,view):
    return client(api)({**data,'action':'apply_import','preview_token':view['preview_token']})


def test_registry_and_selected_export_are_readonly_and_redacted(api):
    request,store=api;call=client(api)
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['models']['codex']='not-a-secret';current['worker_fallback']['path']='/private/example';server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    before=server.SPAWN_DEFAULTS_FILE.read_bytes();code,registry=call();assert code==200
    fields={f['key']:f for f in registry['fields']}
    assert not fields['models.codex']['available'] and not fields['worker_fallback']['available']
    assert '/private/example' not in json.dumps(registry) and 'not-a-secret' not in json.dumps(registry)
    code,result=call({'action':'export','expected_revision':registry['revision'],'fields':['engine','auto_compact_k','models.claude']})
    assert code==200
    assert result['profile']=={'format':profiles.FORMAT,'version':1,'scope':'machine','values':{'auto_compact_k':250,'engine':'claude','models.claude':'auto'}}
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not store.path.exists()


def test_export_excludes_linked_materialized_fields_and_absent_defaults(api):
    call=client(api);current=history.read(server.SPAWN_DEFAULTS_FILE);current['worker_inherit_session']=True;current.pop('auto_compact_k');server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    code,view=call();assert code==200
    fields={f['key']:f for f in view['fields']}
    assert not fields['worker_model']['available'] and fields['worker_inherit_session']['available']
    assert not fields['auto_compact_k']['available']
    assert client(api)({'action':'export','expected_revision':view['revision'],'fields':['auto_compact_k']})[0]==400


def test_precise_preview_successful_native_apply_and_history_undo(api):
    request,store=api;before=server.SPAWN_DEFAULTS_FILE.read_bytes();data,view=preview(api,{'auto_compact_k':310})
    assert not store.path.exists() and server.SPAWN_DEFAULTS_FILE.read_bytes()==before
    assert view['changes']==[{'key':'auto_compact_k','label':'Session compaction (K tokens)','before':{'present':True,'value':250,'local_metadata_preserved':False},'after':{'present':True,'value':310,'local_metadata_preserved':False},'selected':True}]
    assert view['undo_supported']
    code,result=apply(api,data,view);assert code==200 and result['applied'] and result['configuration_history']['state']=='recorded'
    entry=request()[1]['entries'][0];undo=store.preview(entry['id'])[0];assert store.undo(undo)['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==250
    assert history.read(server.SPAWN_DEFAULTS_FILE)['future_policy']['credential']=='not-a-secret'
    assert 'not-a-secret' not in store.path.read_text()


def test_shared_models_merge_selected_engine_without_copying_the_whole_map(api):
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['models']['future']={'credential':'not-a-secret'};current['models']['codex']='not-a-secret';server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    data,view=preview(api,{'models.claude':'default'});assert 'not-a-secret' not in json.dumps(view)
    assert not view['undo_supported']  # whole native model-map snapshot would contain unsupported values
    assert apply(api,data,view)[0]==200
    changed=history.read(server.SPAWN_DEFAULTS_FILE);assert changed['models']['claude']=='default' and changed['models']['future']==current['models']['future'] and changed['models']['codex']=='not-a-secret'


def test_profiles_preserve_root_and_row_metadata_but_never_export_it(api):
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['worker_fallback']={'enabled':False,'guard':'not-a-secret','models':[{'engine':'codex','model':'auto','effort':'low','path':'/private/example'}]};current['model_profiles']={'fast':{'guard':'not-a-secret','models':[{'engine':'claude','model':'auto','effort':'low','path':'/private/example'}]},'future':{'credential':'not-a-secret'}};server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    values={'worker_fallback':{'enabled':True,'models':[{'engine':'codex','model':'default','effort':'high'}]},'model_profiles.fast':{'models':[{'engine':'claude','model':'auto','effort':'high'}]}}
    data,view=preview(api,values);assert '/private/example' not in json.dumps(view) and 'not-a-secret' not in json.dumps(view)
    assert all(c['before'].get('local_metadata_preserved') for c in view['changes'])
    assert apply(api,data,view)[0]==200
    saved=history.read(server.SPAWN_DEFAULTS_FILE);assert saved['worker_fallback']['guard']=='not-a-secret' and saved['worker_fallback']['models'][0]['path']=='/private/example'
    assert saved['model_profiles']['fast']['models'][0]['path']=='/private/example' and saved['model_profiles']['future']==current['model_profiles']['future']


def test_import_refuses_to_drop_unmatched_local_route_metadata(api):
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['model_profiles']={'fast':{'models':[{'engine':'claude','model':'auto','effort':'low','credential':'not-a-secret'}]}};server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    code,result=client(api)({'action':'preview_import','profile':text({'model_profiles.fast':{'models':[{'engine':'claude','model':'default','effort':'high'}]}}),'fields':['model_profiles.fast']})
    assert code==400 and 'metadata' in result['error'] and 'not-a-secret' not in json.dumps(result)
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not api[1].path.exists()


def test_opt_in_uses_local_retained_worker_snapshot_and_previews_dependencies(api):
    request,store=api;data,view=preview(api,{'worker_inherit_session':True,'auto_compact_k':320})
    keys={r['key'] for r in view['changes']};assert {'worker_inherit_session','worker_engine','worker_auto_compact_k','worker_overrides'}<=keys
    assert apply(api,data,view)[0]==200
    current=history.read(server.SPAWN_DEFAULTS_FILE);assert current['worker_engine']=='claude' and current['worker_overrides']['worker_engine']=='codex'
    code,result=client(api)({'action':'preview_import','profile':text({'worker_engine':'codex'}),'fields':['worker_engine']});assert code==400 and 'follow session' in result['error']


def test_engine_visibility_dependency_is_in_actual_preview(api):
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['disabled_engines']=['codex'];server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    _,view=preview(api,{'engine':'codex','reasoning_effort':'high'})
    row=next(d for d in view['changes'] if d['key']=='disabled_engines');assert row['before']['value']==['codex'] and row['after']['value']==[] and not row['selected']


@pytest.mark.parametrize('values', [{'auto_compact_k':True},{'worker_inherit_session':1},{'engine':'/private/example'},{'worker_model':'not-a-secret'},{'model_profiles.fast':{'models':[],'credential':'not-a-secret'}},{'reasoning_effort':'unexpected'}])
def test_unsupported_selected_values_reject_without_writes(api,values):
    before=server.SPAWN_DEFAULTS_FILE.read_bytes();call=client(api)
    code,inspection=call({'action':'inspect','profile':text(values)});assert code==200 and inspection['fields'][0]['portable'] is False
    assert 'not-a-secret' not in json.dumps(inspection)
    assert call({'action':'preview_import','profile':text(values),'fields':list(values)})[0]==400
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not api[1].path.exists()


def test_explicit_omission_is_recorded_in_preview_and_apply_only_selected_fields(api):
    data,view=preview(api,{'auto_compact_k':315,'worker_model':'not-a-secret'},['auto_compact_k'])
    assert view['omitted']==['worker_model'] and apply(api,data,view)[0]==200
    assert history.read(server.SPAWN_DEFAULTS_FILE)['worker_model']=='auto'


@pytest.mark.parametrize('raw', [
    '{', '[]', '{"format":"ccc-operational-preferences","version":1,"scope":"machine","values":{"engine":"claude","engine":"codex"}}',
    '{"format":"ccc-operational-preferences","version":1,"scope":"machine","values":{"auto_compact_k":NaN}}',
    text({'engine':'claude'},version=2),text({'engine':'claude'},version=True),text({'engine':'claude'},scope='queue'),
    text({'engine':'claude'},path='/private/example'),text({'unknown':'not-a-secret'}),
    json.dumps({'format':'ccc-preferences','version':1,'values':{'theme':'dark'}}),
    ' '* (profiles.MAX_BYTES+1),
])
def test_malformed_duplicate_version_unknown_schema_and_oversize_are_rejected(api,raw):
    before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    code,result=client(api)({'action':'inspect','profile':raw});assert code==400
    assert 'not-a-secret' not in json.dumps(result) and '/private/example' not in json.dumps(result)
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not api[1].path.exists()


def test_outer_request_duplicate_fields_and_oversize_rejected(api):
    request,store=api
    # Native fixture exposes only a structured client; use the bound HTTP URL
    # captured by urllib to verify the raw envelope decoder too.
    payload={'action':'inspect','profile':text({'engine':'claude'})}
    original=urllib.request.Request
    def duplicate(url,data=None,**kw):
        return original(url,data=b'{"action":"inspect","action":"export"}',**kw)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(urllib.request,'Request',duplicate)
        assert client(api)(payload)[0]==400
    def oversized(url,data=None,**kw):
        # The handler rejects Content-Length before reading the body. Sending
        # a large body races its early response and can break the client pipe.
        req=original(url,data=b'',**kw)
        req.add_header('Content-Length',str(profiles.MAX_REQUEST+1))
        return req
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(urllib.request,'Request',oversized)
        code,result=client(api)(payload)
        assert code==400 and 'size limit' in result['error']
    assert not store.path.exists()


@pytest.mark.parametrize('change',['machine','queue','policy','selection','profile'])
def test_stale_sources_selection_or_profile_reject_apply(api,change,monkeypatch):
    data,view=preview(api,{'auto_compact_k':320,'worker_auto_compact_k':330})
    if change in ('machine','queue'):
        path=server.SPAWN_DEFAULTS_FILE if change=='machine' else server._wt_config_path();value=history.read(path);value['external']='not-a-secret';path.write_text(json.dumps(value))
    elif change=='policy':server.MODEL_POLICY_FILE.write_text('{"blocked_models":["default"]}')
    elif change=='selection':data['fields']=['auto_compact_k']
    else:data['profile']=text({'auto_compact_k':340,'worker_auto_compact_k':330})
    before=server.SPAWN_DEFAULTS_FILE.read_bytes();assert apply(api,data,view)[0]==409
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and not api[1].path.exists()


def test_current_model_and_effort_policy_native_validation(api,monkeypatch):
    monkeypatch.setattr(server,'_model_policy_blocks',lambda model:model=='default')
    code,_=client(api)({'action':'preview_import','profile':text({'models.claude':'default'}),'fields':['models.claude']});assert code==400
    code,_=client(api)({'action':'preview_import','profile':text({'engine':'codex','reasoning_effort':'max'}),'fields':['engine','reasoning_effort']});assert code==400
    assert not api[1].path.exists()


def test_policy_change_during_intent_fsync_is_rechecked_at_native_publication(api,monkeypatch):
    data,view=preview(api,{'auto_compact_k':320});original=history.atomic
    def during_intent(path,value):
        original(path,value)
        if value['pending'] is not None:server.MODEL_POLICY_FILE.write_text('{"blocked_models":["default"]}')
    monkeypatch.setattr(history,'atomic',during_intent);before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    assert apply(api,data,view)[0]==409
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before and api[1].snapshot()['entries']==[]


def test_history_and_config_write_failure_and_noop_are_truthful(api,monkeypatch):
    data,view=preview(api,{'auto_compact_k':250});assert view['changes']==[]
    assert not apply(api,data,view)[1]['applied'] and not api[1].path.exists()
    data,view=preview(api,{'auto_compact_k':320});before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    monkeypatch.setattr(history,'atomic',lambda *a:(_ for _ in ()).throw(OSError('fixture')))
    assert apply(api,data,view)[0]==503 and server.SPAWN_DEFAULTS_FILE.read_bytes()==before


def test_concurrent_profile_apply_has_at_most_one_success(api):
    data,view=preview(api,{'auto_compact_k':320})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:apply(api,data,view)[0],range(2)))
    assert sorted(results)==[200,409] and len(api[1].snapshot()['entries'])==1


def test_native_configuration_failure_does_not_offer_undo(api,monkeypatch):
    data,view=preview(api,{'auto_compact_k':325});before=server.SPAWN_DEFAULTS_FILE.read_bytes()
    monkeypatch.setattr(server,'_write_spawn_defaults_file',lambda *a:(_ for _ in ()).throw(OSError('fixture')))
    assert apply(api,data,view)[0]==503
    assert server.SPAWN_DEFAULTS_FILE.read_bytes()==before
    assert api[1].snapshot()['entries']==[]


def test_finalization_failure_reports_saved_and_history_recovers_undo(api,monkeypatch):
    data,view=preview(api,{'auto_compact_k':325});original=history.atomic
    def fail_final(path,value):
        if value['pending'] is None:raise OSError('fixture')
        original(path,value)
    monkeypatch.setattr(history,'atomic',fail_final)
    code,result=apply(api,data,view)
    assert code==200 and result['applied'] and result['configuration_history']['state']=='pending'
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==325
    snapshot=api[1].snapshot();assert snapshot['pending_status']=='saved' and len(snapshot['entries'])==1
    monkeypatch.setattr(history,'atomic',original)
    undo=api[1].preview(snapshot['entries'][0]['id'])[0]
    assert api[1].undo(undo)['applied']
    assert history.read(server.SPAWN_DEFAULTS_FILE)['auto_compact_k']==250


def test_registry_unavailable_is_not_empty_and_stale_export_refuses(api):
    call=client(api);_,registry=call()
    current=history.read(server.SPAWN_DEFAULTS_FILE);current['external']='not-a-secret'
    server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(current))
    assert call({'action':'export','fields':['engine'],'expected_revision':registry['revision']})[0]==409
    server.SPAWN_DEFAULTS_FILE.write_text('{')
    code,result=call();assert code==503 and 'fields' not in result and 'not-a-secret' not in json.dumps(result)
    assert not api[1].path.exists()
