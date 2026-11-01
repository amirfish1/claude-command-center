"""Broker tests: persisted native stores, strict transports and dynamic families.

Only the opt-in real Devin test executes an installed agent/model. Other
cases exercise adapter contracts and local persisted fixtures, not paid CLIs.
"""
import collections
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
from types import SimpleNamespace

import pytest
import server
from ccc_server import takeover_sessions as broker


@pytest.fixture
def core(monkeypatch, tmp_path):
    fake = SimpleNamespace(
        _ENGINE_CURATED_MODELS=server._ENGINE_CURATED_MODELS,
        _ACP_HARNESSES={}, _ACP_WORKER_HARNESSES=(), _spawned_sessions=[],
        _spawn_registry_entry_for_session=lambda *args: {},
        _get_session_override=lambda sid: {},
        _hermes_active_session_status=lambda sid: {},
        session_live_status=lambda *args: {},
        _poll_spawn_entry=lambda entry: entry['proc'].poll(),
        _detect_session_engine=lambda sid: 'fixture-engine',
        repo_log_dir=lambda cwd: tmp_path / 'logs',
    )
    monkeypatch.setattr(broker, '_core', fake)
    monkeypatch.setattr(broker, '_ADAPTERS', {})
    monkeypatch.setattr(broker, '_RUNNING', {})
    return fake


def reader(tmp_path, events=None, **metadata):
    events = events if events is not None else [
        {'type': 'user_text', 'text': 'start', 'line': 1},
        {'type': 'assistant', 'text': 'finished\n<session-state>done</session-state>', 'line': 2},
    ]
    return lambda sid, nonce: (events, {'source': str(tmp_path / 'transcript.jsonl'),
                                      'cwd': str(tmp_path), **metadata}, None)


@pytest.mark.parametrize('engine', broker.engine_names() + ['custom-engine'])
def test_every_discovered_family_uses_common_observation(core, tmp_path, engine):
    broker.register_adapter(broker.Adapter(engine, read=reader(tmp_path)))
    result = broker.observe('exact-session', engine)
    assert result['engine'] == engine and result['session_id'] == 'exact-session'
    assert result['schema_version'] == 1 and result['state'] == 'idle'
    assert result['repo_path'] == str(tmp_path) and result['cursor'] == '2'
    assert result['completed_text'].startswith('finished')
    assert isinstance(result['observed_at'], float)
    assert isinstance(result['activity_at'], (float, int))
    assert result['receipt_confirmed'] is False


def test_unknown_engine_pauses_instead_of_rejecting_enrollment(core):
    result = broker.observe('exact-session', 'new-engine')
    assert result['state'] == 'unknown'
    assert result['engine'] == 'new-engine'
    assert 'not supported' not in str(result['error'])


@pytest.mark.parametrize('loaded', [False, True])
def test_native_settings_win_over_acp_facade_defaults(core, tmp_path, monkeypatch, loaded):
    broker.register_adapter(broker.Adapter('fixture', read=reader(
        tmp_path, settings={'model': 'native-model', 'respect_workspace_trust': False})))
    monkeypatch.setattr(broker, '_snapshot', lambda *args: {
        'loaded': loaded, 'status': 'idle', 'model': 'ambient-model',
        'config_options': [{'category': 'model', 'currentValue': 'ambient-option'},
                           {'id': 'respect_workspace_trust', 'currentValue': True},
                           {'id': 'unpersisted_option', 'currentValue': 'current-value'}],
    })
    result = broker.observe('exact-session', 'fixture')
    assert result['settings']['model'] == 'native-model'
    assert result['settings']['respect_workspace_trust'] is False
    assert ('unpersisted_option' in result['settings']) is loaded


def test_current_acp_model_fills_missing_native_setting(core, tmp_path, monkeypatch):
    broker.register_adapter(broker.Adapter('fixture', read=reader(tmp_path)))
    monkeypatch.setattr(broker, '_snapshot', lambda *args: {
        'loaded': True, 'status': 'idle', 'model': 'current-model', 'config_options': []})
    assert broker.observe('exact-session', 'fixture')['settings']['model'] == 'current-model'


@pytest.mark.parametrize('role,synthetic,confirmed', [
    ('assistant', False, False), ('user_text', True, False),
    ('user_text', False, True), ('tool_result', False, False),
])
def test_receipt_requires_persisted_user_input(core, tmp_path, role, synthetic, confirmed):
    broker.register_adapter(broker.Adapter('fixture', read=reader(tmp_path, [
        {'type': role, 'text': 'exact nonce-123', 'line': 17, 'synthetic': synthetic}])))
    result = broker.observe('exact-session', 'fixture', 'nonce-123')
    assert result['receipt_confirmed'] is confirmed
    if confirmed:
        assert result['receipt_evidence'] == {
            'session_id': 'exact-session', 'nonce': 'nonce-123', 'role': 'user',
            'persisted': True, 'source': str(tmp_path / 'transcript.jsonl'),
            'path': str(tmp_path / 'transcript.jsonl'), 'line': 17,
        }


def test_acceptance_sidecar_is_never_receipt(core, tmp_path):
    broker.register_adapter(broker.Adapter('fixture', read=reader(tmp_path, [
        {'type': 'user_text', 'text': 'nonce-123', 'line': 1}], acceptance_only=True)))
    assert broker.observe('exact-session', 'fixture', 'nonce-123')['receipt_confirmed'] is False


def test_wrong_adapter_evidence_does_not_confirm(core, tmp_path):
    def read(sid, nonce):
        return [], {'source': 'fixture'}, broker._evidence('other-session', nonce, 'fixture', 1)
    broker.register_adapter(broker.Adapter('fixture', read=read))
    assert broker.observe('exact-session', 'fixture', 'nonce-123')['receipt_confirmed'] is False


@pytest.mark.parametrize('state', ['busy', 'unknown'])
def test_uncertain_state_never_calls_sender(core, tmp_path, state):
    def fail(*args):
        pytest.fail('sender must not run')
    broker.register_adapter(broker.Adapter('fixture', read=reader(tmp_path, state=state), send=fail))
    result = broker.deliver('exact-session', 'prompt', engine='fixture')
    assert result['code'] == 'session_' + state
    assert not result['ok'] and not result['queued']


def test_devin_pk_read_and_native_prompt_history_proof(core, tmp_path):
    db = tmp_path / 'sessions.db'
    con = sqlite3.connect(db)
    con.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,working_directory TEXT,model TEXT,agent_mode TEXT,last_activity_at INTEGER);
CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY,session_id TEXT,chat_message TEXT,created_at INTEGER);
CREATE TABLE prompt_history(session_id TEXT,content TEXT,is_shell INTEGER,timestamp INTEGER);''')
    con.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', ('native-slug', str(tmp_path), 'saved-model', 'bypass', 1700000000))
    con.execute('INSERT INTO message_nodes VALUES (?,?,?,?)', (1, 'native-slug', json.dumps({'role': 'assistant', 'content': 'complete', 'message_id': 'm1'}), 1700000000))
    con.execute('INSERT INTO prompt_history VALUES (?,?,?,?)', ('other', 'nonce-123', 0, 1))
    con.commit(); con.close()
    def connect(db):
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        return con
    core._devin_cli_db_paths = lambda: [db]
    core._devin_cli_connect = connect
    core._devin_cli_parse_message_row = server._devin_cli_parse_message_row
    core._devin_cli_session_live = lambda sid: False
    result = broker.observe('devincli-native-slug', 'devin', 'nonce-123')
    assert result['state'] == 'idle' and result['receipt_confirmed'] is False
    assert result['settings']['model'] == 'saved-model'
    assert result['settings']['permission_mode'] == 'dangerous'
    con = connect(db)
    con.execute('INSERT INTO prompt_history VALUES (?,?,?,?)', ('native-slug', 'nonce-123', 0, 2))
    con.commit(); con.close()
    result = broker.observe('devincli-native-slug', 'devin', 'nonce-123')
    assert result['receipt_confirmed'] and result['receipt_evidence']['source'].endswith('#prompt_history')


@pytest.mark.parametrize('engine', ['devin', 'kimi', 'grok', 'glm'])
def test_existing_acp_exact_identity_and_no_retry_payload(core, tmp_path, engine):
    sent = []
    transport = SimpleNamespace(alive=lambda: True, send_json=sent.append)
    conn = {'transport': transport, 'next_id': 1}
    sid = 'devincli-native-slug' if engine == 'devin' else 'native-slug'
    raw = broker._native_sid(engine, sid)
    state = {'status': 'idle', 'loaded_conn': id(conn), 'turn_seq': 0, 'deltas': [], 'events': collections.deque(), 'next_line': 1}
    core._ACP_LOCK = threading.Condition(threading.RLock())
    core._ACP_CONNS = {engine: conn}
    core._ACP_SESSION_STATE = {engine: {raw: state}}
    core._ACP_PENDING = {}
    result = broker._deliver_acp(engine, sid, 'nonce-123')
    assert result['ok'] and not result['queued'] and not result['receipt_confirmed']
    assert result['session_id'] == sid and sent[0]['params']['sessionId'] == raw
    assert state['active_turn']['prompt'] == '' and state['active_turn']['no_queue']
    assert broker._deliver_acp(engine, sid, 'second')['code'] == 'session_busy'
    assert len(sent) == 1


def test_acp_generation_mismatch_refuses_without_send(core):
    def fail(*args):
        pytest.fail('must not send on replaced connection')
    conn = {'transport': SimpleNamespace(alive=lambda: True, send_json=fail), 'next_id': 1}
    core._ACP_LOCK = threading.Condition()
    core._ACP_CONNS = {'kimi': conn}
    core._ACP_SESSION_STATE = {'kimi': {'native-slug': {'status': 'idle', 'loaded_conn': -1}}}
    core._ACP_PENDING = {}
    assert broker._deliver_acp('kimi', 'native-slug', 'text')['code'] == 'scoped_transport_unavailable'


def test_acp_write_error_retains_uncertain_state_without_retry(core):
    def fail(*args):
        raise BrokenPipeError()
    conn = {'transport': SimpleNamespace(alive=lambda: True, send_json=fail), 'next_id': 1}
    state = {'status': 'idle', 'loaded_conn': id(conn)}
    core._ACP_LOCK = threading.Condition()
    core._ACP_CONNS = {'kimi': conn}
    core._ACP_SESSION_STATE = {'kimi': {'native-slug': state}}
    core._ACP_PENDING = {}
    result = broker._deliver_acp('kimi', 'native-slug', 'text')
    assert result['uncertain'] and result['code'] == 'delivery_unknown'
    assert state['active_turn']['prompt'] == '' and state['status'] == 'active'


def test_dedicated_worker_operation_bypasses_dispatch_ledger(monkeypatch):
    from worker_engines import EngineHost
    host = object.__new__(EngineHost)
    host._legacy = lambda: server
    called = []
    monkeypatch.setattr(broker, 'deliver', lambda sid, text, **kw: called.append((sid, text, kw)) or {'ok': True, 'queued': False})
    result = host.query({'engine': 'devin', 'operation': 'takeover_deliver',
                         'args': {'session_id': 'devincli-native-slug', 'text': 'hello'}})
    assert result['ok'] and not result['queued']
    assert called == [('devincli-native-slug', 'hello', {'engine': 'devin', 'local': True})]


@pytest.mark.parametrize('engine', ['claude', 'cursor', 'hermes', 'opencode', 'kilo', 'kimi', 'grok', 'gemini', 'copilot', 'devin'])
def test_native_command_preserves_exact_id_and_no_approval_defaults(core, engine, tmp_path):
    setattr(core, '_resolve_' + engine + '_bin', lambda: {'available': True, 'bin': '/fixture/agent'})
    sid = 'devincli-native-slug' if engine == 'devin' else 'native-slug'
    cmd = broker.native_command(engine, sid, 'hello', {'model': 'saved-model'}, str(tmp_path))
    assert broker._native_sid(engine, sid) in cmd
    assert 'hello' in cmd and 'saved-model' in cmd
    assert not set(cmd) & {'--auto', '--yolo', '--dangerously-skip-permissions', '--respect-workspace-trust'}
    if engine == 'kimi':
        assert '--print' not in cmd  # installed Kimi has --prompt, not --print


@pytest.mark.skipif(os.environ.get('CCC_TEST_REAL_DEVIN') != '1' or not shutil.which('devin'), reason='opt-in native model call')
def test_real_isolated_devin_same_session_resume(tmp_path, monkeypatch):
    """Two real turns, exact native identity, receipt from native SQLite."""
    binary = shutil.which('devin')
    token = 'takeover-proof-' + tmp_path.name
    prompt = 'Do not use any tools. Reply with exactly TAKEOVER_BOOTSTRAP.'
    bootstrap = subprocess.run([binary, '--model', 'swe-2-medium', '--permission-mode', 'auto',
                                '--respect-workspace-trust', 'false', '-p', prompt],
                               cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert bootstrap.returncode == 0, (bootstrap.stdout + bootstrap.stderr)[-1000:]
    raw = None
    for db in server._devin_cli_db_paths():
        con = server._devin_cli_connect(db)
        if con:
            try:
                row = con.execute('SELECT id FROM sessions WHERE working_directory=? ORDER BY created_at DESC LIMIT 1', (str(tmp_path),)).fetchone()
                if row:
                    raw = row[0]
                    break
            finally:
                con.close()
    assert raw
    sid = 'devincli-' + raw
    original_override = server._get_session_override
    monkeypatch.setattr(server, '_get_session_override', lambda current:
                        {'respect_workspace_trust': False} if current == sid else original_override(current))
    before = broker.observe(sid, 'devin')
    assert before['state'] == 'idle', before['error']
    assert before['settings']['model'] == 'swe-2-medium'
    # Keep this disposable session's explicit trust setting; do not mutate
    # the machine config or silently relax trust for other sessions.
    result = broker.deliver(sid, 'Do not use any tools. Reply with exactly ' + token, 'devin')
    assert result['same_session'] and not result['queued']
    proc = broker._RUNNING[sid]
    proc.wait(timeout=120)
    after = broker.observe(sid, 'devin', token)
    assert after['receipt_confirmed'], Path(result['log']).read_text()[-1500:]
    assert token in after['completed_text']
    assert after['settings']['model'] == before['settings']['model']
    assert after['settings']['permission_mode'] == before['settings']['permission_mode']


@pytest.mark.parametrize('engine', ['opencode', 'kilo'])
def test_native_parts_store_identity_timestamp_and_receipt(core, tmp_path, engine):
    db = tmp_path / (engine + '.db')
    con = sqlite3.connect(db)
    con.executescript('''CREATE TABLE session(id TEXT PRIMARY KEY,directory TEXT,time_updated INTEGER);
CREATE TABLE message(id TEXT PRIMARY KEY,session_id TEXT,data TEXT,time_created INTEGER);
CREATE TABLE part(message_id TEXT,data TEXT,time_created INTEGER);''')
    con.execute('INSERT INTO session VALUES (?,?,?)', ('native-slug', str(tmp_path), 1700000002000))
    for mid, sid, role, text in [('m0', 'other', 'user', 'wrong-nonce'),
                                ('m1', 'native-slug', 'user', 'right-nonce'),
                                ('m2', 'native-slug', 'assistant', 'complete')]:
        data = {'role': role, 'time': {'created': 1700000001000, 'completed': 1700000002000},
                'providerID': 'fixture-provider', 'modelID': 'saved-model'}
        con.execute('INSERT INTO message VALUES (?,?,?,?)', (mid, sid, json.dumps(data), 1700000001000))
        con.execute('INSERT INTO part VALUES (?,?,?)', (mid, json.dumps({'type': 'text', 'text': text}), 1700000001000))
    con.commit(); con.close()
    def connect():
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row; return con
    setattr(core, '_' + engine + '_connect', connect)
    setattr(core, '_' + engine + '_db_path', lambda: db)
    result = broker.observe('native-slug', engine, 'right-nonce')
    assert result['state'] == 'idle' and result['activity_at'] == 1700000002.0
    assert result['completed_text'] == 'complete'
    assert result['settings']['model'] == 'fixture-provider/saved-model'
    assert result['receipt_confirmed'] and result['receipt_evidence']['session_id'] == 'native-slug'
    assert broker.observe('native-slug', engine, 'wrong-nonce')['receipt_confirmed'] is False


def test_native_hermes_reader_does_not_follow_lineage(core, tmp_path):
    db = tmp_path / 'state.db'
    con = sqlite3.connect(db)
    con.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT,model TEXT,last_activity_at REAL,parent_session_id TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);''')
    con.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', ('native-slug', str(tmp_path), 'saved-model', 1700000002.0, 'parent'))
    con.executemany('INSERT INTO messages VALUES (?,?,?,?,?)', [
        (1, 'parent', 'user', 'wrong-nonce', 1700000000),
        (2, 'native-slug', 'user', 'right-nonce', 1700000001),
        (3, 'native-slug', 'assistant', 'complete', 1700000002),
    ])
    con.commit(); con.close()
    def connect(db):
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row; return con
    core._hermes_db_for_session = lambda sid: db
    core._hermes_connect = connect
    core._parse_hermes_message = server._parse_hermes_message
    result = broker.observe('native-slug', 'hermes', 'right-nonce')
    assert result['state'] == 'idle' and result['activity_at'] == 1700000002.0
    assert result['completed_text'] == 'complete' and result['receipt_confirmed']
    assert broker.observe('native-slug', 'hermes', 'wrong-nonce')['receipt_confirmed'] is False


def test_native_claude_file_parser_and_receipt(core, tmp_path):
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'user', 'timestamp': '2023-11-14T22:13:21Z', 'message': {'role': 'user', 'content': 'right-nonce'}},
        {'type': 'assistant', 'timestamp': '2023-11-14T22:13:22Z', 'message': {'id': 'm1', 'role': 'assistant', 'content': [{'type': 'text', 'text': 'complete'}]}},
    ]) + '\n')
    core._resolve_conversation_path = lambda sid: transcript
    core._parse_conversation_event = server._parse_conversation_event
    result = broker.observe('native-slug', 'claude', 'right-nonce')
    assert result['state'] == 'idle' and result['activity_at'] == 1700000002.0
    assert result['completed_text'] == 'complete' and result['receipt_confirmed']
    assert result['receipt_evidence']['source'] == str(transcript)


def test_native_kimi_wire_completion_and_input_proof(core, tmp_path):
    directory = tmp_path / 'native-slug'
    wire = directory / 'agents' / 'main' / 'wire.jsonl'
    wire.parent.mkdir(parents=True)
    wire.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'config.update', 'modelAlias': 'saved-model'},
        {'type': 'turn.prompt', 'input': [{'type': 'text', 'text': 'right-nonce'}]},
        {'type': 'context.append_loop_event', 'event': {'type': 'content.part', 'part': {'type': 'text', 'text': 'complete'}}},
        {'type': 'context.append_loop_event', 'event': {'type': 'step.end', 'finishReason': 'end_turn'}},
    ]) + '\n')
    core._kimi_session_index = lambda: {'native-slug': {'session_dir': str(directory), 'work_dir': str(tmp_path)}}
    result = broker.observe('native-slug', 'kimi', 'right-nonce')
    assert result['state'] == 'idle' and result['completed_text'] == 'complete'
    assert result['receipt_confirmed'] and result['settings']['model'] == 'saved-model'


def test_acp_remote_busy_finalizer_cannot_queue_takeover_intent(core, tmp_path, monkeypatch):
    from ccc_server import acp
    conn = {'transport': SimpleNamespace(alive=lambda: True, send_json=lambda payload: None), 'next_id': 1}
    state = {'status': 'idle', 'loaded_conn': id(conn), 'events': [], 'delta_seq': 0, 'deltas': []}
    core._ACP_LOCK = threading.Condition(threading.RLock())
    core._ACP_CONNS = {'kimi': conn}
    core._ACP_SESSION_STATE = {'kimi': {'native-slug': state}}
    core._ACP_PENDING = {}
    broker._deliver_acp('kimi', 'native-slug', 'must never requeue')
    core._acp_session = lambda *args, **kwargs: state
    core._acp_remote_turn_busy_error = lambda message: True
    core._kimi_wire_turn_usage_since = lambda *args: None
    core._acp_wire_path = lambda *args: tmp_path / 'wire.jsonl'
    core._acp_flush_turn_text_unlocked = lambda *args, **kwargs: None
    core._acp_emit_event_unlocked = lambda *args, **kwargs: None
    def no_queue(*args, **kwargs):
        pytest.fail('strict takeover payload must never enter busy retry queue')
    core._queue_kimi_remote_busy_retry = no_queue
    monkeypatch.setattr(acp, '_core', core)
    monkeypatch.setattr(acp, '_acp_emit_delta_unlocked', lambda *args: None)
    acp._acp_finalize_turn('kimi', 'native-slug', {'error': {'message': 'busy'}}, core._ACP_PENDING['kimi'][1])
    assert state['status'] == 'idle' and state['active_turn'] is None


@pytest.mark.parametrize('engine', ['pi', 'droid'])
def test_native_session_jsonl_store_and_documented_resume(core, tmp_path, monkeypatch, engine):
    root = tmp_path / engine
    folder = root / 'sessions' / 'workspace'
    folder.mkdir(parents=True)
    if engine == 'pi':
        monkeypatch.setenv('PI_CODING_AGENT_SESSION_DIR', str(root / 'sessions'))
        name = '2026-01-01T00-00-00Z_native-slug.jsonl'
    else:
        monkeypatch.setenv('FACTORY_HOME', str(root))
        name = 'native-slug.jsonl'
    transcript = folder / name
    transcript.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'session', 'id': 'native-slug', 'cwd': str(tmp_path)},
        {'type': 'message', 'id': 'm1', 'message': {'role': 'user', 'content': 'right-nonce'}},
        {'type': 'message', 'id': 'm2', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'complete'}], 'model': 'saved-model', 'stopReason': 'stop'}},
    ]) + '\n')
    monkeypatch.setattr(broker, '_STORE_INDEX', {})
    result = broker.observe('native-slug', engine, 'right-nonce')
    assert result['state'] == 'idle' and result['completed_text'] == 'complete'
    assert result['receipt_confirmed'] and result['activity_at'] > 0
    setattr(core, '_resolve_' + engine + '_bin', lambda: {'available': True, 'bin': '/fixture/agent'})
    cmd = broker.native_command(engine, 'native-slug', 'next', {'model': 'saved-model', 'reasoning_effort': 'high'}, str(tmp_path))
    if engine == 'pi':
        assert cmd[:3] == ['/fixture/agent', '--session', str(transcript)]
        assert '--thinking' in cmd
    else:
        assert cmd[:4] == ['/fixture/agent', 'exec', '--session-id', 'native-slug']
        assert '--reasoning-effort' in cmd
    assert '--fork' not in cmd and '--auto' not in cmd
    assert broker.observe('other-session', engine, 'right-nonce')['receipt_confirmed'] is False


def test_native_copilot_reader_uses_exact_persisted_events(core, tmp_path):
    transcript = tmp_path / 'events.jsonl'
    transcript.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'session.start', 'data': {'context': {'cwd': str(tmp_path)}}},
        {'type': 'user.message', 'data': {'content': 'right-nonce'}, 'timestamp': 1700000001000},
        {'type': 'assistant.message', 'data': {'content': 'complete', 'model': 'saved-model'}, 'timestamp': 1700000002000},
    ]) + '\n')
    core._copilot_events_path = lambda sid: transcript
    core._copilot_event_text = server._copilot_event_text
    result = broker.observe('native-slug', 'copilot', 'right-nonce')
    assert result['state'] == 'idle' and result['activity_at'] == 1700000002.0
    assert result['receipt_confirmed'] and result['completed_text'] == 'complete'
    assert result['settings']['model'] == 'saved-model' and result['cwd'] == str(tmp_path)


def test_aider_restores_bound_history_without_acceptance_receipt(core, tmp_path):
    transcript = tmp_path / 'native-session.jsonl'
    rows = [
        {'type': 'aider_session_meta', 'cwd': str(tmp_path), 'model': 'saved-model'},
        {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'first\nsecond'}]}},
        {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'finished'}]}},
    ]
    transcript.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
    core._aider_session_path = lambda sid: transcript
    core._aider_read_records = lambda source: rows
    core._parse_conversation_event = server._parse_conversation_event
    core._resolve_aider_bin = lambda: {'available': True, 'bin': '/fixture/aider'}
    cmd = broker.native_command('aider', 'native-session', 'next', {'model': 'saved-model'}, str(tmp_path))
    assert '--restore-chat-history' in cmd and '--message' in cmd
    history = Path(cmd[cmd.index('--chat-history-file') + 1])
    assert '#### first  \n#### second' in history.read_text()
    assert 'finished' in history.read_text()
    # Neither broker bootstrap history nor acceptance transcript is native
    # role-proven receipt evidence. Markdown assistant echoes are ambiguous.
    history.write_text(history.read_text() + '\n#### right-nonce\n\n')
    rows.insert(1, {'type': 'user', 'message': {'role': 'user', 'content': 'right-nonce'}})
    result = broker.observe('native-session', 'aider', 'right-nonce')
    assert result['state'] == 'idle' and result['settings']['model'] == 'saved-model'
    assert result['receipt_confirmed'] is False


def test_antigravity_idle_stream_overrides_live_process(core, tmp_path, monkeypatch):
    from ccc_server import antigravity_stream
    stream = SimpleNamespace(alive=lambda: True, busy=False)
    monkeypatch.setattr(antigravity_stream, '_PROCS', {'native-session': stream})
    core._spawned_sessions = [{'session_id': 'native-session', 'engine': 'antigravity',
                               'proc': SimpleNamespace(poll=lambda: None)}]
    broker.register_adapter(broker.Adapter('antigravity', read=reader(tmp_path)))
    monkeypatch.setattr(antigravity_stream, 'send', lambda sid, text: {'ok': True, 'via': 'antigravity-stream'})
    assert broker.observe('native-session', 'antigravity')['state'] == 'idle'
    result = broker.deliver('native-session', 'next', 'antigravity')
    assert result['ok'] and result['same_session'] and not result['queued']
    assert not result['receipt_confirmed']
    stream.busy = True
    assert broker.deliver('native-session', 'next', 'antigravity')['code'] == 'session_busy'


def test_imported_copilotchat_uses_existing_native_parser(core, tmp_path):
    from ccc_server import engines
    transcript = tmp_path / 'native-session.json'
    transcript.write_text(json.dumps({'requests': [
        {'timestamp': 1700000002000, 'message': {'text': 'right-nonce'},
         'response': [{'kind': 'markdownContent', 'content': {'value': 'finished'}}]},
    ], 'cwd': str(tmp_path)}))
    core.conversation_transcript_path = lambda sid: transcript
    core._parse_copilotchat_conversation = lambda sid, after_line: {
        'events': engines._copilotchat_events_from_file(transcript)[0]}
    result = broker.observe('native-session', 'copilotchat', 'right-nonce')
    assert result['completed_text'] == 'finished' and result['receipt_confirmed']
    assert result['activity_at'] > 0 and result['repo_path'] == str(tmp_path)


def test_native_grok_db_read_is_exact_bounded_and_retains_model(core, tmp_path):
    db = tmp_path / 'grok.db'
    con = sqlite3.connect(db)
    con.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT,model TEXT,updated_at INTEGER);'
                      'CREATE TABLE messages(session_id TEXT,seq INTEGER,role TEXT,content TEXT,created_at INTEGER);')
    con.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('native-session', str(tmp_path), 'saved-model', 1700000002))
    con.executemany('INSERT INTO messages VALUES(?,?,?,?,?)', [
        ('native-session', 1, 'user', 'right-nonce', 1700000001),
        ('native-session', 2, 'assistant', 'finished', 1700000002),
        ('other-session', 3, 'user', 'wrong-nonce', 1700000003),
    ])
    con.commit()
    con.close()
    def connect():
        result = sqlite3.connect(db)
        result.row_factory = sqlite3.Row
        return result
    core._grok_db_connect = connect
    core._grok_db_path = lambda: db
    core._grok_db_message_text = lambda text: text
    result = broker.observe('native-session', 'grok', 'right-nonce')
    assert result['state'] == 'idle' and result['completed_text'] == 'finished'
    assert result['receipt_confirmed'] and result['settings']['model'] == 'saved-model'
    assert result['repo_path'] == str(tmp_path) and result['activity_at'] == 1700000002
    assert not broker.observe('native-session', 'grok', 'wrong-nonce')['receipt_confirmed']


def test_generic_acp_family_reads_persisted_events_without_acceptance_proof(core, tmp_path):
    source = tmp_path / 'glm.jsonl'
    source.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'user_text', 'text': 'right-nonce', 'line': 1},
        {'type': 'assistant', 'text': 'finished', 'line': 2},
    ]) + '\n')
    core._ACP_HARNESSES = {'glm': {}}
    core._acp_session_snapshot = lambda *args: {}
    core._acp_transcript_path = lambda engine, sid: source
    result = broker.observe('native-session', 'glm', 'right-nonce')
    assert result['completed_text'] == 'finished' and result['activity_at'] > 0, result
    assert not result['receipt_confirmed']  # Local ACP acceptance is not native input proof.


def test_observe_uses_one_request_local_owner_snapshot(core, tmp_path):
    calls = []
    core._ACP_HARNESSES = {'devin': {}}
    def snapshot(engine, sid):
        calls.append((engine, sid))
        return {'loaded': True, 'status': 'idle', 'model': 'owner-model'}
    core._acp_session_snapshot = snapshot
    broker.register_adapter(broker.Adapter('devin', read=reader(tmp_path)))
    result = broker.observe('devincli-native-session', 'devin')
    assert calls == [('devin', 'native-session')]
    assert result['state'] == 'idle' and result['settings']['model'] == 'owner-model'
    broker.observe('devincli-native-session', 'devin')
    assert len(calls) == 2  # Fresh owner state on the next observation.


def test_worker_snapshot_has_bounded_read_budget_and_failure_pauses(core, tmp_path, monkeypatch):
    import control_plane
    core._ACP_HARNESSES = {'devin': {}}
    core._control_plane_routes_engines = lambda: True
    core._CONTROL_PLANE_ENGINE_CLIENT = SimpleNamespace(path=tmp_path / 'worker.sock', token_file=tmp_path / 'token')
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] == 1.0
        def request(self, method, args):
            calls.append((method, args))
            return {'ok': False, 'available': True, 'ambiguous': True, 'error': 'timed out'}
    monkeypatch.setattr(control_plane, 'ControlPlaneClient', Client)
    broker.register_adapter(broker.Adapter('devin', read=reader(tmp_path)))
    result = broker.observe('devincli-native-session', 'devin')
    assert result['state'] == 'unknown' and result['error'] == 'timed out'
    assert calls == [('engine.query', {'engine': 'devin', 'operation': 'snapshot', 'args': {'session_id': 'native-session'}})]
    assert broker.deliver('devincli-native-session', 'no retry', 'devin')['code'] == 'session_unknown'


def test_stalled_worker_socket_observation_returns_within_wt_budget(core, tmp_path):
    import socket
    import tempfile
    import time
    core._ACP_HARNESSES = {'devin': {}}
    core._control_plane_routes_engines = lambda: True
    broker.register_adapter(broker.Adapter('devin', read=reader(tmp_path)))
    with tempfile.TemporaryDirectory(prefix='ccc-observe-') as directory:
        endpoint = Path(directory) / 'worker.sock'
        token = Path(directory) / 'token'
        token.write_text('test-token')
        core._CONTROL_PLANE_ENGINE_CLIENT = SimpleNamespace(path=endpoint, token_file=token)
        release = threading.Event()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen(1)
            def stall():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(65536)
                    release.wait(3)
            thread = threading.Thread(target=stall, daemon=True)
            thread.start()
            start = time.monotonic()
            try:
                result = broker.observe('devincli-native-session', 'devin')
            finally:
                release.set()
                thread.join(timeout=2)
            assert time.monotonic() - start < 3
            assert result['state'] == 'unknown' and 'timed out' in result['error']


def test_owner_timeout_on_delivery_recheck_never_falls_back_to_native(core, monkeypatch):
    core._ACP_HARNESSES = {'devin': {}}
    monkeypatch.setattr(broker, 'observe', lambda *args: {'engine': 'devin', 'state': 'idle'})
    monkeypatch.setattr(broker, '_snapshot', lambda *args: {'status': 'unknown', 'observation_error': 'timed out'})
    result = broker.deliver('devincli-native-session', 'must not start native writer', 'devin')
    assert result['code'] == 'session_unknown' and not result['queued']
