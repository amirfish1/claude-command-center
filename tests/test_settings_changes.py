"""Disposable native GET, allowlisted projections, no probe or migration."""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import server
from ccc_server import settings_changes as changes


@pytest.fixture
def state(tmp_path,monkeypatch):
    defaults=tmp_path/'spawn-defaults.json';queues=tmp_path/'queue-config.json'
    monkeypatch.setattr(server,'SPAWN_DEFAULTS_FILE',defaults)
    monkeypatch.setattr(server,'_wt_config_path',lambda:queues)
    def forbidden(*a,**kw):pytest.fail('No secrets, catalog, probes, migrations or writes')
    for name in ('_load_spawn_defaults','_factory_spawn_defaults','_write_spawn_defaults_file','_settings_launch_overview','_wt_read_config','byok_get_key','byok_list_profiles','build_system_services','build_ccc_doctor','_worker_fallback_catalog_models','_model_catalog_allows_model'):
        monkeypatch.setattr(server,name,forbidden)
    monkeypatch.setattr(server.subprocess,'run',forbidden);monkeypatch.setattr(server.subprocess,'Popen',forbidden)
    return defaults,queues


def by_key(view,scope):
    return {r['key']:r for g in view['groups'] if g['scope']==scope for r in g['rows']}


def test_empty_is_absent_and_never_seeds_or_probes(state):
    defaults,queues=state;view=changes.snapshot(server)
    assert all(g['status']=='absent' and g['rows']==[] for g in view['groups'])
    assert not defaults.exists() and not queues.exists()
    assert len(view['browser_registry'])==20
    assert 'reader_scale' in view['browser_registry']


def test_saved_same_as_baseline_is_explicit_not_an_effective_change(state):
    defaults,_=state;defaults.write_text(json.dumps({'engine':'claude','auto_compact_k':250,'worker_inherit_session':False}))
    before=defaults.read_bytes();rows=by_key(changes.snapshot(server),'machine')
    assert all(r['explicit'] and r['changed'] is False for r in rows.values())
    assert defaults.read_bytes()==before


def test_linked_materialized_fields_and_retained_overrides_stay_distinct(state):
    defaults,_=state;defaults.write_text(json.dumps({'engine':'codex','worker_engine':'codex','worker_model':'auto','worker_inherit_session':True,'worker_overrides':{'worker_engine':'claude','worker_model':'default','worker_auto_compact_k':250,'future':'not-a-secret'}}))
    rows=by_key(changes.snapshot(server),'machine')
    assert rows['worker_engine']['activity']=='inherited'
    assert rows['worker_engine']['effective']=='codex'
    assert rows['retained.worker_engine']['stored']=='claude'
    assert rows['retained.worker_engine']['effective']=='codex'
    assert rows['retained.worker_engine']['activity']=='shadowed'
    assert 'retained.future' not in rows


def test_fallback_off_and_claude_profiles_retain_separate_edit_contexts(state):
    defaults,_=state;defaults.write_text(json.dumps({'worker_fallback':{'enabled':False,'models':[{'engine':'codex','model':'auto','effort':'high','future':'not-a-secret'}]},'model_profiles':{'fast':{'models':[{'engine':'claude','model':'auto','effort':'low'}]},'future':{'token':'not-a-secret'}}}))
    rows=by_key(changes.snapshot(server),'machine')
    fallback=rows['worker_fallback.worker_fallback.models'];assert fallback['activity']=='shadowed' and fallback['effective']==[]
    assert fallback['stored'][0]['engine']=='codex' and fallback['edit']['context']=='worker_fallback'
    assert rows['model_profiles.fast.models']['edit']['context']=='fast'
    assert 'not-a-secret' not in json.dumps(rows)


def test_manual_queue_pins_and_explicit_off_are_not_ignored(state):
    defaults,queues=state;defaults.write_text(json.dumps({'worker_engine':'codex','worker_model':'auto','worker_reasoning_effort':'high','worker_headroom_dispatch':True,'worker_fallback':{'enabled':True,'models':[]}}))
    queues.write_text(json.dumps({'MANUAL':{'auto_drain':False,'engine':'claude','model':'','effort':'low','fallback_to_default_worker':False,'headroom_dispatch':False,'repo_path':'/private/fixture','secret':'not-a-secret'},'INHERITED':{'auto_drain':True},'ARCHIVED':{'archived':True,'engine':'codex'}}))
    rows=by_key(changes.snapshot(server),'queue')
    assert len(rows)==5
    assert rows['MANUAL.fallback_to_default_worker']['effective'] is False
    assert rows['MANUAL.fallback_to_default_worker']['baseline'] is True
    assert 'manual' in rows['MANUAL.engine']['explanation']
    assert rows['MANUAL.model']['activity']=='inherited'
    assert rows['MANUAL.headroom_dispatch']['changed'] is True
    assert '/private/fixture' not in json.dumps(rows) and 'not-a-secret' not in json.dumps(rows)


def test_unknown_models_paths_metadata_and_unsafe_queue_identifiers_are_hidden(state):
    defaults,queues=state;defaults.write_text(json.dumps({'engine':'/private/fixture','worker_model':'not-a-secret','models':{'codex':'https://account.example.test/private'},'credentials':'not-a-secret','path':'/private/fixture'}))
    queues.write_text(json.dumps({'/private/fixture':{'engine':'codex'},'SAFE':{'model':'sk-ant-test-XXXX'}}))
    text=json.dumps(changes.snapshot(server))
    for secret in ('/private/fixture','not-a-secret','https://account.example.test','sk-ant-test-XXXX'):assert secret not in text
    assert 'unregistered value hidden' in text


@pytest.mark.parametrize('which', ['machine','queue'])
@pytest.mark.parametrize('raw',['{','[]','null'])
def test_malformed_is_unavailable_not_empty_default(state,which,raw):
    defaults,queues=state;(defaults if which=='machine' else queues).write_text(raw)
    group=next(g for g in changes.snapshot(server)['groups'] if g['scope']==which)
    assert group['status']=='unavailable' and group['rows']==[]


def test_unreadable_machine_does_not_invent_queue_baselines(state,monkeypatch):
    defaults,queues=state;queues.write_text('{"MANUAL":{"headroom_dispatch":false}}')
    native=Path.read_text
    def read(path,*a,**kw):
        if path==defaults:raise PermissionError('fixture path must not leak')
        return native(path,*a,**kw)
    monkeypatch.setattr(Path,'read_text',read)
    row=by_key(changes.snapshot(server),'queue')['MANUAL.headroom_dispatch']
    assert row['baseline_known'] is False and row['changed'] is None and row['activity']=='unknown'


def test_stale_snapshot_rejected_without_writes(state,monkeypatch):
    defaults,queues=state;defaults.write_text('{"engine":"claude"}');queues.write_text('{}')
    native=changes.machine_rows
    def mutate(*args):
        result=native(*args);defaults.write_text('{"engine":"codex"}');return result
    monkeypatch.setattr(changes,'machine_rows',mutate)
    with pytest.raises(RuntimeError):changes.snapshot(server)
    assert defaults.read_text()=='{"engine":"codex"}'


def test_actual_get_keeps_store_bytes_and_exposes_only_contract(state):
    defaults,queues=state;defaults.write_text('{"auto_compact_k":310,"opaque":"not-a-secret"}');queues.write_text('{"MANUAL":{"fallback_to_default_worker":false}}')
    before=(defaults.read_bytes(),queues.read_bytes())
    class Handler(server.CommandCenterHandler):
        def log_message(self,*a):pass
        def _phone_pin_blocked(self,path):return False
        def _check_proxy_secret(self):return True
    httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_address[1]}/api/settings/changes') as response:view=json.loads(response.read())
        assert set(view)=={'version','observed_at','revision','groups','browser_registry','coverage'}
        assert by_key(view,'machine')['auto_compact_k']['effective']==310
        assert 'not-a-secret' not in json.dumps(view)
        assert before==(defaults.read_bytes(),queues.read_bytes())
    finally:httpd.shutdown();httpd.server_close();thread.join(5)


def test_stale_materialized_link_is_not_claimed_to_match_session(state):
    defaults,_=state
    defaults.write_text(json.dumps({'engine':'codex','worker_engine':'claude','worker_inherit_session':True}))
    item=by_key(changes.snapshot(server),'machine')['worker_engine']
    assert item['effective']=='claude' and item['activity']=='unknown'
    assert 'differs from the saved session source' in item['explanation']


def test_managed_profile_later_routes_follow_shared_fallback_switch(state):
    defaults,_=state
    routes=[{'engine':'claude','model':'auto','effort':'low'},{'engine':'claude','model':'default','effort':'high'}]
    defaults.write_text(json.dumps({'worker_fallback':{'enabled':False,'models':[]},'model_profiles':{'fast':{'models':routes}}}))
    item=by_key(changes.snapshot(server),'machine')['model_profiles.fast.models']
    assert len(item['stored'])==2 and len(item['effective'])==1
    assert 'shadowed' in item['explanation']


def test_invalid_supported_values_are_unknown_not_confirmed_changes(state):
    defaults,_=state
    defaults.write_text(json.dumps({'auto_compact_k':'not-a-secret','worker_overrides':[]}))
    rows=by_key(changes.snapshot(server),'machine')
    assert rows['auto_compact_k']['changed'] is None
    assert rows['auto_compact_k']['stored_changed'] is None
    assert rows['retained']['activity']=='unknown'
