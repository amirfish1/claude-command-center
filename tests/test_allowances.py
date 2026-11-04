import json
import pytest
from ccc_server import allowances as a


def test_manual_persistence_validation_and_expiration(tmp_path, monkeypatch):
    ledger = tmp_path / 'allowances.json'
    monkeypatch.setattr(a, '_path', lambda: ledger)
    ledger.write_text('[]')
    entry = dict(account='Secondary', provider='Codex', kind='Coding',
                 remaining=50, unit='%', deadline_type='reset',
                 deadline='2030-01-01T09:00:00+00:00')
    a.mutate(entry)
    result = a.payload(now=1_800_000_000)
    row = result['rows'][0]
    assert row['remaining'] == 50 and row['status'] == 'available'
    assert result['recommended_id'] == row['id']
    assert a.payload(now=2_000_000_000)['rows'][0]['status'] == 'needs_update'
    assert a.payload(now=2_000_000_000)['recommended_id'] is None
    for bad in (101, -1, float('nan'), True):
        with pytest.raises(ValueError):
            a.mutate(dict(entry, remaining=bad))
    with pytest.raises(ValueError):
        a.mutate(dict(entry, deadline='2030-01-01T09:00:00'))
    a.mutate({'id': row['id'], 'delete': True})
    assert json.loads(ledger.read_text()) == []


def test_recommendation_uses_deadline_and_blocks_exhausted_account(tmp_path, monkeypatch):
    entries = [dict(id='a', account='Large', provider='Codex', source='codex', codex_home=str(tmp_path)),
               dict(id='b', account='Small', provider='Codex', source='manual', remaining=50,
                    deadline_type='reset', deadline='2030-01-01T09:00:00Z'),
               dict(id='c', account='Unknown', provider='Claude', source='manual', remaining=None,
                    deadline_type='expiration', deadline='2029-01-01T00:00:00Z')]
    monkeypatch.setattr(a, '_load', lambda: entries)
    monkeypatch.setattr(a, '_CACHE', {})
    monkeypatch.setattr(a, '_read_codex', lambda home: dict(
        weekly=dict(pct=96, resets_at='2030-01-02T09:00:00Z'),
        session=dict(pct=100, resets_at='2029-12-31T09:00:00Z'), fetched_at='2028-01-01T00:00:00Z'))
    result = a.payload(now=1_800_000_000)
    assert result['recommended_id'] == 'b'
    assert {r['status'] for r in result['rows'] if r['id'] == 'a'} == {'blocked', 'empty'}


def test_duplicate_homes_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(a, '_path', lambda: tmp_path / 'ledger.json')
    monkeypatch.setattr(a, '_load', lambda: [dict(id='a', source='codex', codex_home=str(tmp_path))])
    with pytest.raises(ValueError, match='already connected'):
        a.mutate(dict(account='Duplicate', provider='Codex', source='codex', codex_home=str(tmp_path)))


def test_signin_preserves_manual_until_success_and_uses_isolated_home(tmp_path, monkeypatch):
    import server
    import queue
    import threading
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(a, '_path', lambda: tmp_path / 'ledger.json')
    monkeypatch.setattr(a, '_LOGINS', {})
    original = dict(id='second', account='Secondary', source='manual', provider='Codex',
                    remaining=50, unit='%', deadline_type='reset', deadline='2030-01-01T00:00:00Z')
    (tmp_path / 'ledger.json').write_text(json.dumps([original]))
    release = threading.Event()
    waiting = threading.Event()
    homes = []

    def protocol(home, action):
        homes.append(home)
        messages = queue.Queue()
        def request(ident, method, params):
            assert method == 'account/login/start'
            assert params == {'type': 'chatgptDeviceCode'}
            waiting.set()
            release.wait(3)
            messages.put({'method': 'account/login/completed',
                          'params': {'loginId': 'test', 'success': True}})
            return {'result': {'verificationUrl': 'https://auth.openai.com/codex/device',
                               'userCode': 'TEST-0000', 'loginId': 'test'}}
        return action(request, messages)
    monkeypatch.setattr(a, '_with_codex', protocol)
    monkeypatch.setattr(a, '_read_codex', lambda home: {'identity': 'person@example.com'})
    result = a.login_start({'id': 'second', 'account': 'Secondary'})
    assert waiting.wait(2)
    assert json.loads((tmp_path / 'ledger.json').read_text()) == [original]
    release.set()
    for thread in threading.enumerate():
        if thread.name == 'allowance-sign-in':
            thread.join(3)
    status = a.login_status({'session_id': result['session_id']})
    assert status['status'] == 'connected'
    assert status['user_code'] is None
    saved = json.loads((tmp_path / 'ledger.json').read_text())[0]
    assert saved['id'] == 'second' and saved['source'] == 'codex'
    assert saved['codex_home'] == homes[0]
    assert str(tmp_path / 'allowance-accounts') in homes[0]


def test_signin_cancel_keeps_manual_entry(tmp_path, monkeypatch):
    import server
    import queue
    import threading
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(a, '_path', lambda: tmp_path / 'ledger.json')
    monkeypatch.setattr(a, '_LOGINS', {})
    (tmp_path / 'ledger.json').write_text('[]')
    def protocol(home, action):
        def request(*args):
            return {'result': {'verificationUrl': 'https://auth.openai.com/codex/device',
                               'userCode': 'TEST-0000', 'loginId': 'test'}}
        return action(request, queue.Queue())
    monkeypatch.setattr(a, '_with_codex', protocol)
    result = a.login_start({'account': 'Secondary'})
    a.login_status({'session_id': result['session_id']}, cancel=True)
    for thread in threading.enumerate():
        if thread.name == 'allowance-sign-in':
            thread.join(3)
    assert a.login_status({'session_id': result['session_id']})['status'] == 'cancelled'
    assert json.loads((tmp_path / 'ledger.json').read_text()) == []
