import copy
import json
import socket
import struct
import threading
import tempfile
from pathlib import Path
from unittest import mock
import pytest
from ccc_server import codex_desktop as desktop


def state():
    return {'id':'task','cwd':'/repo','title':'Task','turns':[], 'requests':[],
            'turnHistory':{'history':{'isComplete':True,'entitiesByKey':{
                'turn:one':{'turnId':'one','status':'completed','items':[{'id':'answer','type':'agentMessage','text':'First'}]},
                'turn:two':{'turnId':'two','status':'inProgress','items':[], 'params':{'input':[{'type':'text','text':'Second'}]}},
            },'islands':[{'entries':[{'key':'turn:one','value':'turn:one'},{'key':'turn:two','value':'turn:two'}]}]}}}


def test_canonical_history_preserves_order_and_synthesizes_missing_user_item():
    result=desktop.normalize_thread(state())
    assert [t['id'] for t in result['turns']]==['one','two']
    assert result['turns'][1]['items'][0]['content'][0]['text']=='Second'
    assert result['historyComplete']


def test_patch_batch_is_atomic_and_supports_array_insert_remove():
    original={'items':['one','three'],'nested':{'value':1}}
    result=desktop.apply_patches(original,[{'op':'add','path':['items',1],'value':'two'},
        {'op':'replace','path':['nested','value'],'value':2},{'op':'remove','path':['items',0]}])
    assert result=={'items':['two','three'],'nested':{'value':2}}
    assert original=={'items':['one','three'],'nested':{'value':1}}
    with pytest.raises((ValueError,KeyError,IndexError)):
        desktop.apply_patches(original,[{'op':'remove','path':['items',99]}])
    assert original['items']==['one','three']


def test_stream_rejects_foreign_owner_and_revision_gap():
    client=desktop.DesktopClient();client.owners['task']='owner'
    def event(change,owner='owner'):
        client._message({'type':'broadcast','method':'thread-stream-state-changed','version':11,
            'sourceClientId':owner,'params':{'hostId':'local','conversationId':'task','change':change}})
    event({'type':'snapshot','revision':1,'conversationState':state()},'stranger')
    assert not client.states
    event({'type':'snapshot','revision':1,'conversationState':state()})
    event({'type':'patches','revision':2,'baseRevision':1,'patches':[{'op':'replace','path':['title'],'value':'Updated'}]})
    assert client.states['task'][1]['title']=='Updated'
    event({'type':'patches','revision':4,'baseRevision':3,'patches':[]})
    assert 'task' not in client.states


@pytest.fixture
def peer(tmp_path):
    short=tempfile.TemporaryDirectory(prefix='ccc-ipc-',dir='/tmp')
    target=Path(short.name)/'ipc.sock';listener=socket.socket(socket.AF_UNIX);listener.bind(str(target));listener.listen(1)
    requests=[];halt=threading.Event()
    def serve():
        connection,_=listener.accept()
        def exact(size):
            data=b''
            while len(data)<size:
                chunk=connection.recv(size-len(data))
                if not chunk:raise EOFError()
                data+=chunk
            return data
        def send(value):
            data=json.dumps(value).encode();connection.sendall(struct.pack('<I',len(data))+data)
        try:
            while not halt.is_set():
                incoming=json.loads(exact(struct.unpack('<I',exact(4))[0]));requests.append(incoming)
                if incoming['type']=='request':
                    method=incoming['method']
                    result={'clientId':'ccc-client'} if method=='initialize' else {}
                    if method=='thread-follower-start-turn':result={'result':{'id':'new','status':'inProgress'}}
                    if method=='thread-follower-steer-turn':result={'result':{'turnId':'two'}}
                    send({'type':'response','requestId':incoming['requestId'],'method':method,'resultType':'success',
                          'handledByClientId':'ccc-client' if method=='initialize' else 'owner','result':result})
                elif incoming.get('method')=='thread-stream-following-changed' and incoming['params']['following']:
                    send({'type':'broadcast','method':'thread-stream-state-changed','version':11,'sourceClientId':'owner',
                          'params':{'hostId':'local','conversationId':'task','change':{'type':'snapshot','revision':1,'conversationState':state()}}})
        except (EOFError,OSError):pass
        finally:connection.close()
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    client=desktop.DesktopClient()
    with mock.patch.object(desktop,'endpoint',return_value=target):
        yield client,requests
        if client.sock:client._disconnect(client.sock)
    halt.set();listener.close();thread.join(timeout=1);short.cleanup()


def test_real_framing_discovery_snapshot_and_owner_targeted_start(peer):
    client,requests=peer
    result=client.rpc('thread/read',{'threadId':'task','includeTurns':True})
    assert len(result['thread']['turns'])==2
    assert len(client.rpc('thread/turns/list',{'threadId':'task'})['data'])==2
    client.states['task'][1]['turnHistory']['history']['entitiesByKey']['turn:two']['status']='completed'
    result=client.rpc('turn/start',{'threadId':'task','input':[{'type':'text','text':'Hello'}]})
    assert result['turn']['id']=='new'
    wire=next(r for r in requests if r.get('method')=='thread-follower-start-turn')
    assert wire['targetClientId']=='owner' and wire['version']==2
    assert wire['params']['turnStart']['request']['input'][0]['text']=='Hello'


def test_stale_active_turn_is_never_sent(peer):
    client,requests=peer
    with pytest.raises(ValueError,match='active desktop turn changed'):
        client.rpc('turn/interrupt',{'threadId':'task','turnId':'old'})
    assert not any(r.get('method')=='thread-follower-interrupt-turn' for r in requests)
    with pytest.raises(ValueError,match='not exposed'):
        client.rpc('command/exec',{'command':['anything']})


def test_owner_change_renews_epoch_before_returning_snapshot(peer):
    client,_=peer
    client.snapshot('task');before=client.epoch
    client.owners['task']='former-owner';client.seen_at['task']=0
    client.snapshot('task')
    assert client.epoch!=before


def test_approval_reply_targets_only_current_pending_request(peer):
    client,requests=peer
    client.snapshot('task')
    client.states['task'][1]['requests']=[{'id':7,'method':'item/tool/requestUserInput','params':{'questions':[]}}]
    client.answer('task',{'id':7,'result':{'answers':{}}})
    wire=next(r for r in requests if r.get('method')=='thread-follower-submit-user-input')
    assert wire['targetClientId']=='owner'
    assert wire['params']=={'conversationId':'task','requestId':7,'response':{'answers':{}}}
    with pytest.raises(ValueError,match='no longer pending'):
        client.answer('task',{'id':'7','result':{'answers':{}}})


def test_native_catalog_does_not_advertise_unexposed_desktop_operations():
    import server
    from ccc_server import codex_client, codex_capabilities
    raw={'methods':[{'method':'turn/start','available':True},{'method':'command/exec','available':True}]}
    with mock.patch.object(codex_client,'_client_desktop_mode',return_value=True), mock.patch.object(codex_capabilities,'get_codex_catalog',return_value=raw):
        catalog=codex_client._client_catalog()
    assert catalog['connection_kind']=='desktop-ipc'
    assert catalog['methods'][0]['available']
    assert not catalog['methods'][1]['available']
    assert raw['methods'][1]['available']


def test_existing_composer_routes_to_desktop_without_native_resume(peer):
    import server
    from ccc_server import codex_client as client
    transport,_=peer
    transport.snapshot('task')
    transport.states['task'][1]['turnHistory']['history']['entitiesByKey']['turn:two']['status']='completed'
    with mock.patch.object(desktop,'DESKTOP',transport), \
         mock.patch.object(client,'_client_desktop_mode',return_value=True), \
         mock.patch.object(client,'_client_rpc',return_value={'ok':True,'generation':'g'}), \
         mock.patch.object(client,'_client_operation',return_value={'ok':True,'result':{'turn':{'id':'new'}}}) as send:
        result=client.resume_desktop_conversation('task','Follow up',cwd='/repo',model='model',action_id='short')
    assert result['confirmed'] and result['via']=='codex-desktop' and result['turn_id']=='new'
    body=send.call_args.args[0]
    assert body['context']=={'thread_id':'task','repo_path':'/repo'}
    assert body['params']['input']==[{'type':'text','text':'Follow up'}]
    assert len(body['action_id'])==64


def test_existing_composer_busy_desktop_returns_queue_without_a_second_send(peer):
    from ccc_server import codex_client as client
    transport,_=peer
    with mock.patch.object(desktop,'DESKTOP',transport), \
         mock.patch.object(client,'_client_desktop_mode',return_value=True), \
         mock.patch.object(client,'_client_operation') as send:
        result=client.resume_desktop_conversation('task','Next',cwd='/repo')
    assert result['fallback']=='queue'
    send.assert_not_called()


@pytest.mark.parametrize('reply',[
    {'ok':True,'via':'codex-desktop','accepted':True,'confirmed':True,'turn_id':'new'},
    {'ok':False,'via':'codex-desktop','error':'timed out','uncertain':True},
])
def test_existing_resume_entry_never_falls_through_after_desktop_delivery(tmp_path, reply):
    import server
    from ccc_server import queue_events, codex_client
    with mock.patch.object(server,'_resolve_codex_bin',return_value={'available':True,'bin':'unused'}), \
         mock.patch.object(server,'_spawned_sessions',[]), \
         mock.patch.object(server,'_codex_thread_row',return_value={'cwd':str(tmp_path),'model':'test-model'}), \
         mock.patch.object(server,'_spawn_registry_entry_for_session',return_value={}), \
         mock.patch.object(server,'_get_session_override',return_value=None), \
         mock.patch.object(server,'_model_policy_blocks',return_value=False), \
         mock.patch.object(server,'_resume_ledger_append'), \
         mock.patch.object(codex_client,'resume_desktop_conversation',return_value=reply) as desktop_send, \
         mock.patch.object(server,'_codex_resume_or_steer_via_app_server',side_effect=AssertionError('second transport')):
        result=queue_events.resume_session_codex('task','Follow up',_native_delivery=True,_from_queue=True)
    assert result==reply
    desktop_send.assert_called_once()


def test_confirmed_blocked_codex_override_resumes_without_reprompt(tmp_path):
    """A policy warning accepted at spawn remains valid for that session."""
    import server
    from ccc_server import queue_events, codex_client

    def validate(model, *, require_available=False, confirm_blocked=False):
        assert model == "gpt-6-astra"
        assert require_available is True
        assert confirm_blocked is True
        return model, None

    reply = {"ok": True, "via": "codex-desktop", "accepted": True}
    with mock.patch.object(server, "_resolve_codex_bin", return_value={"available": True, "bin": "unused"}), \
         mock.patch.object(server, "_spawned_sessions", []), \
         mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(tmp_path), "model": "gpt-5.6-terra"}), \
         mock.patch.object(server, "_spawn_registry_entry_for_session", return_value={}), \
         mock.patch.object(server, "_get_session_override", return_value={"model": "gpt-6-astra", "policy_confirmed": True}), \
         mock.patch.object(server, "_validate_codex_model", side_effect=validate), \
         mock.patch.object(server, "_resume_ledger_append"), \
         mock.patch.object(codex_client, "resume_desktop_conversation", return_value=reply) as desktop_send:
        result = queue_events.resume_session_codex("task", "Follow up", _native_delivery=True)

    assert result == reply
    assert desktop_send.call_args.kwargs["model"] == "gpt-6-astra"


def test_desktop_steer_targets_owner_with_desktop_composer_payload(peer):
    client,requests=peer
    client.snapshot('task')
    receipt=client.steer('task','Hold on, check tests first',cwd='/repo',client_message_id='idem-1')
    assert receipt=={'turn_id':'two','owner':'owner'}
    wire=next(r for r in requests if r.get('method')=='thread-follower-steer-turn')
    assert wire['targetClientId']=='owner' and wire['version']==1
    params=wire['params']
    assert params['conversationId']=='task'
    assert params['clientUserMessageId']=='idem-1'
    assert params['input']==[{'type':'text','text':'Hold on, check tests first','text_elements':[]}]
    restore=params['restoreMessage']
    assert restore['text']=='Hold on, check tests first'
    assert restore['cwd']=='/repo'
    assert restore['context']['prompt']=='Hold on, check tests first'
    assert restore['context']['workspaceRoots']==['/repo']
    assert params['toolOutput'] is None and params['attachments']==[]


def test_desktop_steer_requires_an_active_turn(peer):
    client,requests=peer
    client.snapshot('task')
    client.states['task'][1]['turnHistory']['history']['entitiesByKey']['turn:two']['status']='completed'
    with pytest.raises(ValueError,match='no active desktop turn'):
        client.steer('task','too late',cwd='/repo')
    assert not any(r.get('method')=='thread-follower-steer-turn' for r in requests)


def test_desktop_steer_reports_confirmed_receipt_under_the_steer_contract(peer):
    from ccc_server import codex_client as client
    transport,_=peer
    transport.snapshot('task')
    with mock.patch.object(desktop,'DESKTOP',transport), \
         mock.patch.object(client,'_client_desktop_mode',return_value=True):
        result=client.resume_desktop_conversation('task','Hold on',cwd='/repo',steer=True,action_id='claim-7')
    assert result['ok'] and result['confirmed'] and result['accepted']
    assert result['via']=='codex-steer' and result['transport']=='codex-desktop'
    assert result['turn_id']=='two'
    wire=next(r for r in _ if r.get('method')=='thread-follower-steer-turn')
    import hashlib
    assert wire['params']['clientUserMessageId']==hashlib.sha256(b'steer:claim-7').hexdigest()


def test_desktop_steer_without_active_turn_uses_the_queue_preserving_code(peer):
    from ccc_server import codex_client as client
    transport,_=peer
    transport.snapshot('task')
    transport.states['task'][1]['turnHistory']['history']['entitiesByKey']['turn:two']['status']='completed'
    with mock.patch.object(desktop,'DESKTOP',transport), \
         mock.patch.object(client,'_client_desktop_mode',return_value=True):
        result=client.resume_desktop_conversation('task','Hold on',cwd='/repo',steer=True)
    assert result['ok'] is False
    assert result['code']=='codex_no_active_turn'
    assert result['via']=='codex-steer' and result['transport']=='codex-desktop'
    assert not any(r.get('method')=='thread-follower-steer-turn' for r in _)


@pytest.mark.parametrize('failure,code',[
    (TimeoutError('Desktop request timed out; it will not be retried'),'desktop_steer_uncertain'),
    (ValueError('Desktop task owner changed'),'desktop_owner_changed'),
    (ValueError('Desktop: Cannot steer conversation task because its active turn already ended'),'codex_no_active_turn'),
])
def test_desktop_steer_failure_surfaces_actual_cause_without_retry(peer, failure, code):
    from ccc_server import codex_client as client
    transport,_=peer
    transport.snapshot('task')
    with mock.patch.object(desktop,'DESKTOP',transport), \
         mock.patch.object(client,'_client_desktop_mode',return_value=True), \
         mock.patch.object(transport,'steer',side_effect=failure) as steer:
        result=client.resume_desktop_conversation('task','Hold on',cwd='/repo',steer=True)
    steer.assert_called_once()
    assert result['ok'] is False and result['code']==code
    assert result['via']=='codex-steer' and result['transport']=='codex-desktop'
    assert str(failure) in result['error']
    if code=='desktop_steer_uncertain':
        assert result['uncertain'] and result['ambiguous']
    assert not any(r.get('method')=='thread-follower-steer-turn' for r in _)


@pytest.mark.parametrize('reply',[
    {'ok':True,'via':'codex-steer','transport':'codex-desktop','accepted':True,'confirmed':True,'turn_id':'two'},
    {'ok':False,'via':'codex-steer','transport':'codex-desktop','code':'desktop_steer_uncertain','uncertain':True,'ambiguous':True,'error':'timed out'},
])
def test_desktop_steer_never_falls_through_to_a_second_transport(tmp_path, reply):
    import server
    from ccc_server import queue_events, codex_client
    with mock.patch.object(server,'_resolve_codex_bin',return_value={'available':True,'bin':'unused'}), \
         mock.patch.object(server,'_spawned_sessions',[]), \
         mock.patch.object(server,'_codex_thread_row',return_value={'cwd':str(tmp_path),'model':'test-model'}), \
         mock.patch.object(server,'_spawn_registry_entry_for_session',return_value={}), \
         mock.patch.object(server,'_get_session_override',return_value=None), \
         mock.patch.object(server,'_model_policy_blocks',return_value=False), \
         mock.patch.object(server,'_resume_ledger_append'), \
         mock.patch.object(codex_client,'resume_desktop_conversation',return_value=reply) as desktop_send, \
         mock.patch.object(server,'_codex_steer_via_app_server',side_effect=AssertionError('second transport')):
        result=queue_events.resume_session_codex('task','Hold on',steer=True,_native_delivery=True)
    assert result==reply
    desktop_send.assert_called_once()
