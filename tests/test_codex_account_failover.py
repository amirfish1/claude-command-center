"""A Codex account that refuses the model must not strand the session."""
import json
import time
from datetime import datetime, timezone

import pytest

import server
from ccc_server import codex_account_failover as failover
from ccc_server import codex_accounts as accounts
from ccc_server import continuation, notify, wake_guard

MODEL = 'gpt-test-big'
REFUSAL = f"The '{MODEL}' model is not supported when using Codex with a ChatGPT account."


def _stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _event(kind, at, **fields):
    return json.dumps({'timestamp': _stamp(at), 'type': 'event_msg', 'payload': {'type': kind, **fields}})


def _user(text, at):
    return _event('item_completed', at, item={'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]})


def _refused(path, text='do the work', turn='turn-1', at=None):
    at = time.time() if at is None else at
    lines = [_event('task_started', at, turn_id=turn), _user(text, at),
             _event('task_complete', at, turn_id=turn, last_agent_message=None,
                    error={'message': REFUSAL, 'codex_error_info': 'model_not_found'})]
    with path.open('a') as stream:
        stream.write('\n'.join(lines) + '\n')


def _catalog(home, *slugs):
    (home / 'models_cache.json').write_text(json.dumps({'models': [{'slug': slug} for slug in slugs]}))


@pytest.fixture
def world(tmp_path, monkeypatch):
    primary, secondary = tmp_path / 'primary', tmp_path / 'secondary'
    shared = primary / 'sessions'
    shared.mkdir(parents=True)
    secondary.mkdir()
    (secondary / 'sessions').symlink_to(shared)
    rows = [dict(id='primary', account='Primary', source='codex', codex_home=str(primary)),
            dict(id='secondary', account='Secondary', source='codex', codex_home=str(secondary))]
    monkeypatch.setattr(accounts, '_root', lambda: tmp_path / 'runtime')
    monkeypatch.setattr(accounts, '_NEGATIVE', {})
    monkeypatch.setattr(accounts, '_CATALOGS', {})
    monkeypatch.setattr(accounts, 'configured', lambda: rows)
    monkeypatch.setenv('CODEX_HOME', str(primary))
    monkeypatch.delenv('CCC_CODEX_ACCOUNT_KEY', raising=False)
    monkeypatch.delenv(wake_guard.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(failover._core, 'COMMAND_CENTER_STATE_DIR', tmp_path / 'state', raising=False)
    (tmp_path / 'state').mkdir()
    sent, spawned, told = [], [], []
    monkeypatch.setattr(failover._core, '_inject_text_into_session',
                        lambda sid, text, **kw: sent.append((sid, text, accounts.session_account(sid))) or {'ok': True},
                        raising=False)
    monkeypatch.setattr(continuation, 'spawn_continuation',
                        lambda sid, **kw: spawned.append((sid, kw)) or {'ok': True, 'new_session_id': 'new-session'})
    monkeypatch.setattr(notify, 'post', lambda data, **kw: told.append(data))
    monkeypatch.setattr(failover._core, '_load_spawn_defaults', lambda: {'engine': 'codex'}, raising=False)
    monkeypatch.setattr(failover, '_pending', {})
    _catalog(primary, MODEL, 'gpt-small')
    _catalog(secondary, 'gpt-small')
    return dict(primary=primary, secondary=secondary, rollout=shared / 'rollout-sid.jsonl',
                sent=sent, spawned=spawned, told=told)


def _on_secondary(world, sid='sid'):
    accounts.bind(sid, accounts.account_for_home(world['secondary']))


def _pass(world, sid='sid'):
    failover.note(sid, world['rollout'])
    failover.run_pending()


def test_refused_turn_moves_to_serving_account_and_resends(world):
    _on_secondary(world)
    _refused(world['rollout'])
    _pass(world)
    # The resend already routes to the machine login.
    assert world['sent'] == [('sid', 'do the work', None)]
    assert accounts.session_account('sid') is None
    assert 'switched account' in world['told'][0]['title']
    assert world['spawned'] == []


def test_moved_thread_is_not_rediscovered_from_its_old_home(world):
    import sqlite3
    _on_secondary(world)
    with sqlite3.connect(world['secondary'] / 'state_5.sqlite') as con:
        con.execute('CREATE TABLE threads (id TEXT)')
        con.execute("INSERT INTO threads VALUES ('sid')")
    accounts.rebind('sid', None)
    assert accounts.session_account('sid') is None


def test_one_retry_per_failed_turn(world):
    _on_secondary(world)
    _refused(world['rollout'])
    _pass(world)
    _on_secondary(world)
    failover._pending['sid'] = dict(failover.detect(world['rollout']), path=str(world['rollout']))
    failover.run_pending()
    assert len(world['sent']) == 1


def test_no_serving_account_continues_on_fallback_engine(world):
    _catalog(world['primary'], 'gpt-small')
    _on_secondary(world)
    _refused(world['rollout'])
    _pass(world)
    assert world['sent'] == []
    assert world['spawned'] == [('sid', {'prompt': 'do the work', 'engine': 'claude'})]
    assert accounts.session_account('sid')['home'] == str(world['secondary'])


def test_home_that_cannot_see_the_rollout_is_not_a_target(world):
    (world['secondary'] / 'sessions').unlink()
    (world['secondary'] / 'sessions').mkdir()
    _catalog(world['secondary'], MODEL)
    _catalog(world['primary'], 'gpt-small')
    _refused(world['rollout'])
    _pass(world)
    assert world['sent'] == []
    assert len(world['spawned']) == 1


def test_wake_guard_refusal_moves_but_does_not_resend(world, monkeypatch):
    monkeypatch.setenv(wake_guard.KILL_SWITCH_ENV, '0')
    _on_secondary(world)
    _refused(world['rollout'])
    _pass(world)
    assert world['sent'] == []
    assert accounts.session_account('sid') is None
    assert world['told'][0]['kind'] == 'needs_input'


def test_later_turn_clears_the_refusal(world):
    _refused(world['rollout'])
    with world['rollout'].open('a') as stream:
        stream.write(_event('task_started', time.time(), turn_id='turn-2') + '\n')
    assert failover.detect(world['rollout']) is None


def test_old_refusal_is_ignored(world):
    _refused(world['rollout'], at=time.time() - failover.MAX_AGE_S - 60)
    failover.note('sid', world['rollout'])
    assert failover._pending == {}


def test_usage_limit_is_not_this_lane(world):
    at = time.time()
    world['rollout'].write_text(_event('task_complete', at, turn_id='t', last_agent_message=None,
                                       error={'message': "You've hit your usage limit.",
                                              'codex_error_info': 'usage_limit_exceeded'}) + '\n')
    assert failover.detect(world['rollout']) is None


def _saved_model(monkeypatch, model):
    monkeypatch.setattr(accounts, 'query_account', lambda *a: {'ok': True, 'active': False}, raising=True)
    monkeypatch.setattr(failover._core, '_get_session_override', lambda sid: {'model': model}, raising=False)
    monkeypatch.setattr(failover._core, '_log_activity', lambda *a: None, raising=False)


def test_send_moves_a_session_its_account_would_reject(world, monkeypatch):
    _saved_model(monkeypatch, MODEL)
    _on_secondary(world)
    # None hands the send to the machine login's worker.
    assert accounts.route('inject', {'session_id': 'sid', 'text': 'hi'}) is None
    assert accounts.session_account('sid') is None


def test_send_stays_when_the_account_offers_the_model(world, monkeypatch):
    _saved_model(monkeypatch, 'gpt-small')
    _on_secondary(world)
    assert accounts.route('inject', {'session_id': 'sid', 'text': 'hi'})['ok']
    assert accounts.session_account('sid')['home'] == str(world['secondary'])


def test_send_never_moves_a_running_turn(world, monkeypatch):
    _saved_model(monkeypatch, MODEL)
    monkeypatch.setattr(accounts, 'query_account', lambda *a: {'ok': True, 'active': True})
    _on_secondary(world)
    accounts.route('inject', {'session_id': 'sid', 'text': 'hi'})
    assert accounts.session_account('sid')['home'] == str(world['secondary'])


def test_spawn_skips_a_saved_account_that_lost_the_model(world):
    assert accounts.spawn_account({'codex_account_id': 'secondary', 'model': MODEL}) is None


def test_spawn_keeps_an_account_that_offers_the_model(world):
    chosen = accounts.spawn_account({'codex_account_id': 'secondary', 'model': 'gpt-small'})
    assert chosen['home'] == str(world['secondary'])


def test_spawn_keeps_the_choice_when_the_catalog_is_unreadable(world):
    (world['secondary'] / 'models_cache.json').unlink()
    chosen = accounts.spawn_account({'codex_account_id': 'secondary', 'model': MODEL})
    assert chosen['home'] == str(world['secondary'])


def test_cross_engine_continuation_drops_the_codex_model(monkeypatch):
    calls = {}
    ctx = dict(session_id='s', latest='s', title='t', cwd='/tmp', engine='codex', transcript_path=None,
               context_tokens=0, idle_seconds=0, model=MODEL, effort='high', report_to=None)
    monkeypatch.setattr(continuation, 'build_continuation_prompt', lambda prompt, c: prompt)
    monkeypatch.setattr(continuation._core, 'spawn_session',
                        lambda prompt, **kw: calls.update(kw) or {'session_id': None}, raising=False)
    continuation._spawn_continuation('s', prompt='go', engine='claude', _context=ctx)
    assert calls['model'] is None
    assert calls['reasoning_effort'] == ''
