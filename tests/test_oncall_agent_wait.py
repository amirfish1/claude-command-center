"""Mailboxes must never answer another session or outlive their hook."""
import concurrent.futures
import time

import pytest

from ccc_server.oncall import agent_wait, replies
from ccc_server.oncall.engine import Engine


@pytest.fixture
def eng(tmp_path):
    engine = Engine(tmp_path, flag_on=lambda: True)
    cfg = engine.config()
    cfg.update(enabled=True, queue='TEST')
    engine.save_config(cfg)
    return engine


def register(eng, sid='session-one', event='Stop', seconds=30):
    row = agent_wait.register(eng, {'session_id': sid, 'event': event,
                                  'seconds': seconds, 'text': 'Should I use blue?'})
    return dict(row, session_id=sid)


def test_two_sessions_wrong_claim_and_single_use(eng):
    a, b = register(eng), register(eng, 'session-two')
    with pytest.raises(ValueError):
        agent_wait.collect(eng, dict(a, session_id='session-two'))
    with pytest.raises(ValueError):
        agent_wait.collect(eng, dict(a, token=b['token']))
    with concurrent.futures.ThreadPoolExecutor() as pool:
        pending = pool.submit(agent_wait.collect, eng, a)
        assert agent_wait.answer(eng.store, eng.store.alert(b['alert_id']), 'green')
        assert not pending.done()
        assert agent_wait.answer(eng.store, eng.store.alert(a['alert_id']), 'blue')
        assert pending.result(timeout=2)['answer'] == 'blue'
    assert agent_wait.collect(eng, b)['answer'] == 'green'
    with pytest.raises(ValueError):
        agent_wait.collect(eng, a)
    assert not agent_wait.answer(eng.store, eng.store.alert(a['alert_id']), 'again')


def test_cancel_and_timeout(eng):
    a = register(eng)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(agent_wait.collect, eng, a)
        agent_wait.cancel(eng, a)
        try:
            assert future.result(timeout=2)['answer'] is None
        except ValueError:
            pass  # cancel won the race before collect started
    b = register(eng, seconds=1)
    start = time.monotonic()
    assert agent_wait.collect(eng, b)['answer'] is None
    assert time.monotonic() - start < 2
    assert eng.store.alert(b['alert_id'])['state'] == 'cleared'
    assert not agent_wait.answer(eng.store, eng.store.alert(b['alert_id']), 'late')


@pytest.mark.parametrize('text', ['allow', 'deny'])
def test_permission_uses_mailbox_not_native_transport(eng, text):
    a = register(eng, event='PermissionRequest')
    alert = eng.store.alert(a['alert_id'])
    assert replies._apply({}, eng.store, {}, None, alert, 'yes', None, time.time())[0] == 'unclear'
    assert replies._apply({}, eng.store, {}, None, alert, text, None, time.time())[0] == 'queued for hook'
    assert agent_wait.collect(eng, a)['answer'] == text


def test_off_and_bounded(eng):
    cfg = eng.config()
    cfg['enabled'] = False
    eng.save_config(cfg)
    with pytest.raises(ValueError):
        register(eng)
    cfg['enabled'] = True
    eng.save_config(cfg)
    a = register(eng, seconds=99999)
    row = agent_wait.boxes(eng.store).rows[a['wait_id']]
    assert row['deadline'] - time.monotonic() <= 540
    agent_wait.cancel(eng, a)


def test_observer_does_not_clear_hook_alert(eng):
    from ccc_server.oncall.session_signals import observe
    eng.deps = object()
    a = register(eng)
    observe(eng, {})
    assert eng.store.alert(a['alert_id'])['state'] == 'open'
    agent_wait.cancel(eng, a)


def test_restart_clears_orphans_without_replaying(eng):
    a = register(eng)
    restarted = Engine(eng.root, flag_on=lambda: True)
    agent_wait.sweep(restarted.store)
    assert restarted.store.alert(a['alert_id'])['state'] == 'cleared'
    with pytest.raises(ValueError):
        agent_wait.collect(restarted, a)


def test_native_hooks_cannot_raise_a_duplicate_while_plugin_waits(eng):
    from ccc_server.oncall.session_signals import observe
    class Deps:
        def question(self, *args):
            raise AssertionError('Native observer read a plugin-owned question')
        def permission(self, *args):
            raise AssertionError('Native observer read a plugin-owned permission')
    eng.deps = Deps()
    register(eng, 'question-session')
    register(eng, 'permission-session', event='PermissionRequest')
    observe(eng, {'question-session': {'state': 'idle'},
                  'permission-session': {'needs_approval': True}})


def test_completed_wait_suppresses_same_question_until_new_prompt(eng):
    a = register(eng)
    assert agent_wait.answer(eng.store, eng.store.alert(a['alert_id']), 'blue')
    assert agent_wait.collect(eng, a)['answer'] == 'blue'
    assert agent_wait.handled(eng.store, 'session-one', {'body': 'Should I use blue?'})
    assert not agent_wait.handled(eng.store, 'session-one', {'body': 'Should I use green?'})
    agent_wait.cancel(eng, {'session_id': 'session-one'})
    assert not agent_wait.handled(eng.store, 'session-one', {'body': 'Should I use blue?'})
