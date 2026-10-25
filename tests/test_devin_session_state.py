"""Devin must preserve authored controls beyond the sidebar preview limit."""
import importlib
import json
import sqlite3


def test_devin_prompt_reminder_roundtrips_visible_text():
    server = importlib.import_module('server')
    text = 'Please check the build.'
    wrapped = server._devin_session_state_prompt(text)
    assert server._CCC_SESSION_STATE_SYSTEM_PROMPT in wrapped
    assert server._strip_ccc_session_state_instruction(wrapped) == text
    assert server._devin_session_state_prompt(wrapped) == wrapped


def test_devin_fields_parse_footer_before_truncating_preview(monkeypatch):
    server = importlib.import_module('server')
    from ccc_server import devin
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE message_nodes (row_id INTEGER, session_id TEXT, chat_message TEXT)')
    answer = 'Long evidence. ' * 250 + '\n<session-state>\nDID: Checked the build.\nINSIGHT: Signing remains separate.\nNEXT_STEP_USER: Answer [Q7:destination].\nNEXT_ACTION: null\n</session-state>'
    con.execute('INSERT INTO message_nodes VALUES (1, ?, ?)', ('sample',json.dumps({'role':'assistant','content':answer})))
    monkeypatch.setattr(devin,'_devin_cli_first_prompts_from_history',lambda *_: {'sample':'Check the build'})
    fields=devin._devin_cli_row_fields_for_session(con,'sample',{})
    assert len(fields['last_assistant_text']) == 2000
    assert fields['session_state']['next_step_user'] == 'Answer [Q7:destination].'
    con.close()


def test_devin_acp_sends_reporting_instruction_with_original_retry_text(monkeypatch):
    server=importlib.import_module('server')
    monkeypatch.setenv('CCC_CONTROL_PLANE_ENGINES','0')
    sid='reporting-reminder-test'
    captured={}
    def send(harness,method,params,**kwargs):
        captured.update(params)
        entry={}
        kwargs['on_registered'](11,entry)
        return 11
    monkeypatch.setattr(server,'_acp_ensure_session_loaded',lambda *_: None)
    monkeypatch.setattr(server,'_acp_request_async',send)
    monkeypatch.setattr(server,'_acp_emit_event_unlocked',lambda *a,**kw: None)
    try:
        result=server._acp_prompt('devin',sid,'Check the build.')
        assert result['ok']
        assert server._CCC_SESSION_STATE_SYSTEM_PROMPT in captured['prompt'][0]['text']
        assert server._acp_session('devin',sid)['active_turn']['prompt']=='Check the build.'
    finally:
        server._ACP_SESSION_STATE.get('devin',{}).pop(sid,None)


def test_devin_delivery_proof_accepts_wrapped_prompt(monkeypatch,tmp_path):
    server=importlib.import_module('server')
    db=tmp_path/'sessions.db'
    con=sqlite3.connect(db)
    con.execute('CREATE TABLE prompt_history (session_id TEXT, content TEXT, timestamp INTEGER)')
    con.execute('INSERT INTO prompt_history VALUES (?,?,?)',('example',server._devin_session_state_prompt('Check build.'),100))
    con.commit();con.close()
    monkeypatch.setenv('CCC_DEVIN_DB',str(db))
    assert server._devin_cli_prompt_history_count('example','Check build.',99)==1


def test_acp_row_overlay_updates_cached_db_rows_without_mutating_them(monkeypatch,tmp_path):
    import server
    from ccc_server import devin
    path=tmp_path/'sample.jsonl'
    monkeypatch.setattr(server,'_acp_transcript_path',lambda *_:path)
    row={'session_id':'devincli-sample','modified':100,'last_assistant_text':'Old DB answer','session_state':None}
    monkeypatch.setattr(devin,'_find_devin_cli_db_conversations',lambda *a,**kw:[row])
    answer='Build succeeded.\n<session-state>\nDID: Built.\nINSIGHT: Compiles.\nNEXT_STEP_USER: None\nNEXT_ACTION: null\n</session-state>'
    path.write_text(json.dumps({'type':'assistant','ts':200,'blocks':[{'kind':'text','text':answer}]})+'\n')
    current=devin.find_devin_cli_conversations()[0]
    assert current['session_state']['did']=='Built.'
    assert current['modified']==200
    assert row['last_assistant_text']=='Old DB answer'
    # New real request clears the prior footer even though SQLite has not flushed.
    with path.open('a') as f:f.write(json.dumps({'type':'user_text','ts':201,'text':'Do more'})+'\n')
    current=devin.find_devin_cli_conversations()[0]
    assert current['session_state'] is None
    assert current['last_assistant_text']==''
    # A newer DB request must also defeat an older ACP answer.
    row['modified']=202
    assert devin.find_devin_cli_conversations()[0] is row
