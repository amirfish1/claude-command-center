"""Account selection must never change the default login or another session."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from ccc_server import codex_accounts as accounts


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    primary = tmp_path / 'primary'
    secondary = tmp_path / 'secondary'
    primary.mkdir()
    secondary.mkdir()
    monkeypatch.setattr(accounts, '_root', lambda: root)
    monkeypatch.setattr(accounts, '_NEGATIVE', {})
    monkeypatch.setenv('CODEX_HOME', str(primary))
    monkeypatch.delenv('CCC_CODEX_ACCOUNT_KEY', raising=False)
    rows = [dict(id='primary', account='Primary', source='codex', codex_home=str(primary)),
            dict(id='secondary', account='Secondary', source='codex', codex_home=str(secondary))]
    monkeypatch.setattr(accounts, 'configured', lambda: rows)
    return primary, secondary, rows


def test_selection_binds_followups_and_inherits_children(profiles, monkeypatch):
    primary, secondary, rows = profiles
    calls = []

    def query(account, operation, args):
        calls.append((account, operation, args))
        if operation == 'rpc':
            return {'ok': True, 'response': {'result': {'account': {'type': 'chatgpt'}}}}
        return {'ok': True, 'session_id': 'second-session'}

    monkeypatch.setattr(accounts, 'query_account', query)
    before = dict(os.environ)
    result = accounts.route('spawn', {'codex_account_id': 'secondary', 'prompt': 'hello'})
    assert result['codex_account'] == 'Secondary'
    assert accounts.session_account('second-session')['home'] == str(secondary)
    assert 'codex_account_id' not in calls[-1][2]
    assert accounts.route('resume', {'session_id': 'second-session', 'text': 'followup'})['ok']
    assert calls[-1][0]['home'] == str(secondary)
    assert accounts.route('client', {'action': 'respond', 'data': {'context': {'thread_id': 'second-session'}}})['ok']
    assert calls[-1][0]['home'] == str(secondary)
    accounts.route('spawn', {'parent_session_id': 'second-session', 'prompt': 'child'})
    assert calls[-1][0]['home'] == str(secondary)
    assert accounts.route('spawn', {'codex_account_id': 'primary', 'prompt': 'default'}) is None
    assert accounts.route('resume', {'session_id': 'original-session'}) is None
    assert accounts.route('spawn', {'codex_account_id': '__current__', 'parent_session_id': 'second-session'}) is None
    assert dict(os.environ) == before
    # Removing the allowance does not reroute sessions already bound to it.
    rows.clear()
    assert accounts.route('resume', {'session_id': 'second-session'})['ok']
    assert calls[-1][0]['home'] == str(secondary)


def test_unknown_failed_and_logged_out_accounts_do_not_fallback(profiles, monkeypatch):
    assert accounts.route('spawn', {'codex_account_id': 'missing'})['ok'] is False
    monkeypatch.setattr(accounts, 'query_account', lambda *args: {'ok': False, 'error': 'offline'})
    assert accounts.route('spawn', {'codex_account_id': 'secondary'})['ok'] is False
    account = accounts._register(profiles[1], 'Secondary')
    accounts.bind('bound', account)
    assert accounts.route('resume', {'session_id': 'bound'})['ok'] is False
    assert accounts.route('client', {'action': 'handover', 'data': {'context': {'thread_id': 'bound'}}})['ok'] is False
    monkeypatch.setenv('CCC_CODEX_ACCOUNT_KEY', account['key'])
    assert accounts.route('resume', {'session_id': 'bound'}) is None


def test_bindings_immutable_and_native_children_discovered(profiles):
    primary, secondary, _ = profiles
    account = accounts._register(secondary, 'Secondary')
    accounts.bind('session', account)
    with pytest.raises(ValueError, match='different'):
        accounts.bind('session', accounts._register(primary, 'Primary'))
    with sqlite3.connect(secondary / 'state_5.sqlite') as con:
        con.execute('CREATE TABLE threads (id TEXT PRIMARY KEY)')
        con.execute("INSERT INTO threads VALUES ('native-child')")
    assert accounts.session_account('native-child')['home'] == str(secondary)
    assert 'home' not in json.dumps(accounts.public_payload('native-child'))


def test_thread_indexes_merge_and_apply_limit_globally(profiles, monkeypatch):
    import server
    from ccc_server import codex
    primary, secondary, _ = profiles
    for home, sid, stamp in ((primary, 'older', 10), (secondary, 'newer', 20)):
        with sqlite3.connect(home / 'state_5.sqlite') as con:
            con.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER, created_at INTEGER)')
            con.execute('INSERT INTO threads VALUES (?, ?, ?)', (sid, stamp, stamp))
    monkeypatch.setattr(server, 'CODEX_STATE_DB', primary / 'state_5.sqlite')
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: set())
    monkeypatch.setattr(codex, '_CODEX_THREADS_CACHE', {})
    assert [r['id'] for r in codex._codex_fetch_threads()] == ['newer', 'older']
    assert [r['id'] for r in codex._codex_fetch_threads(limit=1)] == ['newer']


def test_worker_failure_cannot_send_selected_work_to_default(profiles, monkeypatch):
    import server
    monkeypatch.delenv('CCC_WORKER_PROCESS', raising=False)
    monkeypatch.setattr(server, '_control_plane_routes_engines', lambda: True)
    monkeypatch.setattr(server, '_control_plane_request', lambda *a, **k: {'ok': False, 'available': False})
    reply = server._control_plane_engine_call('codex', 'spawn', {'codex_account_id': 'secondary'})
    assert reply['ok'] is False and reply['code'] == 'codex_account_unavailable'
    bound = accounts._register(profiles[1], 'Secondary')
    accounts.bind('bound', bound)
    reply = server._control_plane_engine_call('codex', 'resume', {'session_id': 'bound'})
    assert reply['ok'] is False
    assert server._control_plane_engine_call('codex', 'resume', {'session_id': 'original'}) is None
    assert not accounts.dedupe_matches({'codex_account_id': 'secondary'}, 'original')
    assert accounts.dedupe_matches({'codex_account_id': 'secondary'}, 'bound')


def test_account_backend_retirement_refuses_active_work(monkeypatch):
    import threading
    from types import SimpleNamespace
    from ccc_worker import WorkerRuntime
    ledger = SimpleNamespace(recover_orphaned_running=lambda epoch: [])
    runtime = WorkerRuntime(ledger=ledger, token='test-worker-token')
    monkeypatch.setenv('CCC_CODEX_ACCOUNT_KEY', 'test-account')
    closed = []
    state = {'status': 'active', 'active_turn_id': 'turn'}
    runtime._engine_host = SimpleNamespace(_module=SimpleNamespace(
        _CODEX_APP_SERVER_LOCK=threading.Lock(),
        _CODEX_APP_SERVER_THREAD_STATE={'thread': state},
        _codex_app_server_shutdown=lambda: closed.append(True)))
    assert runtime.dispatch('account.retire', {})['code'] == 'account_busy'
    assert not closed
    state.clear()
    runtime._account_requests = 1
    assert runtime.dispatch('account.retire', {})['code'] == 'account_busy'
    runtime._account_requests = 0
    assert runtime.dispatch('account.retire', {})['ok']
    assert closed == [True]
    assert runtime.dispatch('engine.query', {})['code'] == 'account_retiring'
