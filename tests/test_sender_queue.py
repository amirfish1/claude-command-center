"""Envelope survives the compatible queue sidecar, including slash commands."""
import server
import sender_envelope as attribution
from ccc_server import pending_inputs


def test_slash_entry_reloads_attribution_from_sidecar(monkeypatch):
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT','1')
    attr=attribution.service('ccc:control-plane','recipient')
    entry=attribution.during(attr,lambda: pending_inputs._PendingInputEntry('/compact'))
    assert str(entry)=='/compact'
    metadata=[{'id':entry.pending_id,'fingerprint':pending_inputs._pending_text_fingerprint(entry),'attr':entry.attr}]
    decoded,_=pending_inputs._decode_pending_queue([str(entry)],metadata,sidecar_version=2)
    assert decoded[0].attr['message_id']==attr['message_id']


def test_federation_preserves_id_and_records_peer_separately(monkeypatch):
    from ccc_server import fleet
    import federation
    a=attribution.make('recipient',who=attribution.author('session','sender','Name','codex',node='remote-node'),basis='runtime')
    requests={};calls=[]
    monkeypatch.setattr(federation,'check_and_record_request',lambda rid: requests.setdefault(rid,None))
    monkeypatch.setattr(federation,'record_request_result',lambda rid,result: requests.update({rid:{'result':result}}))
    monkeypatch.setattr(fleet,'_federation_self_api',lambda method,path,**kw: calls.append(kw['body']) or {'ok':True})
    env={'action':'inject','hops':2,'req_id':'per-hop-one','args':{'session_id':'recipient','text':'notice','attr':a,'idempotency_key':a['message_id']}}
    result,status=fleet._federation_execute_route(env,peer={'node_id':'paired-node'})
    assert status==200 and result['ok']
    received=calls[0]['attr']
    assert received['message_id']==a['message_id'] and received['author']==a['author']
    assert received['basis']=='claimed'
    assert received['evidence']['peer_node']=='paired-node'
    assert received['relay'][-1]['peer_node']=='paired-node'
    assert attribution.request_attr({'attr':received},'recipient')['evidence']['source']=='federation'
    env['req_id']='per-hop-two'
    assert fleet._federation_execute_route(env,peer={'node_id':'paired-node'})[0]['duplicate']
    assert len(calls)==1


def test_ask_wt_fallback_uses_env_carrier_without_new_flags(monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT','1')
    monkeypatch.setattr(server,'_wt_messaging_enabled',lambda:True)
    monkeypatch.setattr(server,'_wt_cli_available',lambda:True)
    monkeypatch.setattr(server,'_wt_cli_path',lambda:'old-wt')
    attr=attribution.service('ccc:control-plane','recipient')
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(stdout=json.dumps({'ok':True,'answer':'reply'}))
    monkeypatch.setattr(server.subprocess,'run',run)
    result=server._try_wt_ask_for_headless_delivery('recipient',attribution.carry('question',attr,app='ccc'),1000)
    args,kwargs=calls[0]
    assert '--attr-json' not in args
    assert json.loads(kwargs['env'][attribution.ENV])['message_id']==attr['message_id']
    assert 'CLAUDE_CODE_SESSION_ID' not in kwargs['env']
    assert result['ok'] and result['attribution_limitation']


def test_attribution_carrier_hidden_from_original_ask_and_titles(monkeypatch):
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT','1')
    attr=attribution.service('ccc:control-plane','recipient')
    carried=attribution.carry('Actual original ask',attr,app='ccc')
    event={'type':'user','message':{'content':carried}}
    assert server._extract_user_prompt_text(event)=='Actual original ask'
    assert server._codex_display_name({},first_message=carried)=='Actual original ask'
    assert server._codex_display_name({},title=carried)=='Actual original ask'
    parsed=server._parse_conversation_event(event,1)
    assert parsed['text']=='Actual original ask'
    assert parsed['attribution']['message_id']==attr['message_id']
    assert carried.startswith(attribution.HEADER)


def test_attributed_queue_row_matches_visible_text(monkeypatch):
    # CCC-233: queued rows carry the attribution header, but the browser shows,
    # steers and cancels the header-free text. Steer reported "queued message
    # no longer exists" and the queued card rendered twice.
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT','1')
    sid='ccc-233-attributed-queue'
    carried=attribution.carry('just answer the ticket',attribution.service('ccc:test',sid),app='ccc')
    assert carried.startswith(attribution.HEADER)
    monkeypatch.setattr(server,'_mutate_pending_inputs',lambda sids,fn:{'ok':True,'value':fn()})
    monkeypatch.setitem(server._pending_resume_queue,sid,[pending_inputs._PendingInputEntry(carried)])
    try:
        events=pending_inputs._get_queued_events_for_session(sid)
        assert [e['text'] for e in events if e.get('pending')]==['just answer the ticket']
        claim=pending_inputs._claim_matching_pending_input(sid,'just answer the ticket')
        assert claim and str(claim['item'])==carried
    finally:
        server._pending_resume_queue.pop(sid,None)
