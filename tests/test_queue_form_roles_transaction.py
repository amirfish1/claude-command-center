"""Full-form roles and fields share one temporary native publication."""
import json
from types import SimpleNamespace
from pathlib import Path
import pytest
import server
from ccc_server import settings_transactions as tx
from tests.test_settings_transactions import api  # Inert WT helper; all state paths temporary.


@pytest.fixture
def roles(api, monkeypatch):
    request, base, effects = api
    native = server._wt_config
    native.ROLE_KEYS = ('planner','plan_reviewer','verifier','assessor')
    native._models = SimpleNamespace(ENGINES=('claude','codex'))
    native.canonical_model = lambda engine, model: 'claude-sonnet-5' if engine=='claude' and model=='sonnet-5' else model
    native.is_approved_model = lambda engine, model: model in {'claude':['claude-sonnet-5'],'codex':['gpt-5']}[engine]
    def forbidden(*a, **kw):pytest.fail('No role setters, runtime resolver or external audit')
    native.set_role=forbidden;native.engine=forbidden;native._record_audit=forbidden
    # Path mismatch also forbids calling the optional audit helper.
    native.CONFIG_FILE=base/'unused-audit-source.json'
    path=base/'queue-config.json'
    path.write_text(json.dumps({'SAMPLE':{'engine':'claude','desired_workers':2,'planner_engine':'claude',
        'planner_model':'claude-sonnet-5','verifier_engine':'codex','verifier_model':'gpt-5',
        'planner_future':{'opaque':'keep'},'future':{'opaque':True}}}))
    return request,base,effects,native


def test_valid_complete_save_roles_and_fields_single_replace_preserves_metadata(roles,monkeypatch):
    request,base,effects,native=roles;path=base/'queue-config.json';calls=[];original=tx.os.replace
    def replace(src,dst):calls.append(Path(dst));return original(src,dst)
    monkeypatch.setattr(tx.os,'replace',replace)
    code,result=request('/api/queue/config',{'queue':'SAMPLE','engine':'codex','workers':4,'roles':{
        'planner':{'engine':'','model':'gpt-5'},'assessor':{'engine':'claude','model':'sonnet-5'}}})
    assert code==200,result
    assert calls.count(path)==1
    saved=json.loads(path.read_text())['SAMPLE']
    assert saved['desired_workers']==4 and saved['engine']=='codex'
    assert 'planner_engine' not in saved and saved['planner_model']=='gpt-5'
    assert saved['assessor_engine']=='claude' and saved['assessor_model']=='claude-sonnet-5'
    assert saved['verifier_engine']=='codex' and saved['verifier_model']=='gpt-5'
    assert saved['planner_future']=={'opaque':'keep'} and saved['future']=={'opaque':True}


@pytest.mark.parametrize('late',[{'engine':'codex','model':'not-approved'}, {'engine':'unknown','model':''},
                                {'engine':'claude','model':'line\nbreak'}, {'model':'gpt-5'}, {'engine':'claude','model':'gpt-5'}])
def test_late_invalid_role_leaves_all_native_bytes_unchanged(roles,late):
    request,base,effects,native=roles;path=base/'queue-config.json';before=path.read_bytes()
    code,result=request('/api/queue/config',{'queue':'SAMPLE','workers':9,'roles':{
        'planner':{'engine':'codex','model':'gpt-5'},'verifier':late}})
    assert code==400,result
    assert path.read_bytes()==before and effects==[]
    assert not (base/'configuration-history.json').exists()
    assert not (base/'queue-config.audit.json').exists()


def test_roles_replace_failure_preserves_queue_and_roles(roles,monkeypatch):
    request,base,effects,native=roles;path=base/'queue-config.json';before=path.read_bytes();original=tx.os.replace
    def fail(src,dst):
        if Path(dst)==path:raise OSError('fixture replace failure')
        return original(src,dst)
    monkeypatch.setattr(tx.os,'replace',fail)
    code,result=request('/api/queue/config',{'queue':'SAMPLE','workers':7,'roles':{'planner':{'engine':'codex','model':'gpt-5'}}})
    assert code==500 and path.read_bytes()==before and effects==[]
    assert not list(base.glob('*.tmp'))


def test_clear_role_and_old_client_preservation(roles):
    request,base,effects,native=roles
    assert request('/api/queue/config',{'queue':'SAMPLE','roles':{'planner':{'engine':'','model':''}}})[0]==200
    saved=json.loads((base/'queue-config.json').read_text())['SAMPLE']
    assert 'planner_engine' not in saved and 'planner_model' not in saved and saved['planner_future']=={'opaque':'keep'}
    assert request('/api/queue/config',{'queue':'SAMPLE','workers':3})[0]==200
    assert json.loads((base/'queue-config.json').read_text())['SAMPLE']['verifier_model']=='gpt-5'


def test_role_default_source_conflict_no_write_and_unresolved_no_guess(roles):
    request,base,effects,native=roles;path=base/'queue-config.json';before=path.read_bytes()
    def conflict(engine,model):
        (base/'spawn-defaults.json').write_text('{"worker_engine":"codex"}')
        return model
    native.canonical_model=conflict
    code,result=request('/api/queue/config',{'queue':'SAMPLE','roles':{'planner':{'engine':'','model':'claude-sonnet-5'}}})
    assert code==409,result
    assert path.read_bytes()==before
    native.canonical_model=lambda e,m:m
    (base/'spawn-defaults.json').write_text('{}')
    code,result=request('/api/queue/config',{'queue':'SAMPLE','roles':{'planner':{'engine':'','model':'gpt-5'}}})
    assert code==400 and path.read_bytes()==before


def test_nonempty_roles_unavailable_rejects_whole_save_but_empty_compatible(roles,monkeypatch):
    request,base,effects,native=roles;path=base/'queue-config.json';before=path.read_bytes()
    monkeypatch.setattr(server,'_WT_CONFIG_AVAILABLE',False)
    assert request('/api/queue/config',{'queue':'SAMPLE','workers':6,'roles':{'planner':{'engine':'','model':''}}})[0]==400
    assert path.read_bytes()==before
    assert request('/api/queue/config',{'queue':'SAMPLE','roles':{}})[0]==200


def test_explicit_role_engine_does_not_depend_on_unrelated_broken_worker_defaults(roles):
    request,base,effects,native=roles
    (base/'spawn-defaults.json').write_text('{')
    code,result=request('/api/queue/config',{'queue':'SAMPLE','roles':{'planner':{'engine':'codex','model':'gpt-5'}}})
    assert code==200,result
    assert json.loads((base/'queue-config.json').read_text())['SAMPLE']['planner_model']=='gpt-5'
