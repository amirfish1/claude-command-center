"""Escalation scheduling, authority, crash recovery and bounded query work."""
from ccc_server.oncall import escalation as es, alerts, replies
from ccc_server.oncall.engine import Engine
from ccc_server.oncall.store import Inbound, Receipt
from tests.test_oncall_engine import FakeChannel, FakeWT, Deps


def setup(tmp_path):
    now = [1800000000.0]
    eng = Engine(tmp_path, wt=FakeWT(), deps=Deps(), clock=lambda: now[0],
                 flag_on=lambda: True, build_channel=FakeChannel)
    cfg = eng.config()
    cfg.update(enabled=True, channels=[{'id': c, 'type': 'ntfy'} for c in ('a', 'b', 'c')],
               people=[{'id': 'backup', 'channels': ['b']}, {'id': 'third', 'channels': ['c']}],
               escalation=[{'person': p, 'channels': [c], 'after': n * 60}
                           for n, (p, c) in enumerate((('owner', 'a'), ('backup', 'b'), ('third', 'c')))])
    eng.save_config(cfg)
    return eng, now


def raised(eng, **kw):
    return eng.store.alert_upsert('fixture', kind='permission', title='Choose', body='Allow?', session_id='test', now=eng.clock(), **kw)[0]


def send(eng):
    return alerts.deliver(eng.config(), eng.store, eng.channels(), now=eng.clock())


def test_steps_restart_and_clock(tmp_path):
    e, clock = setup(tmp_path)
    a = raised(e)
    send(e)
    assert len(e.channels()['a'].sent) == 1
    clock[0] += 59
    send(e)
    assert not e.channels()['b'].sent
    e.store.close()
    e._store = None
    clock[0] += 1
    send(e)
    assert len(e.channels()['b'].sent) == 1
    clock[0] -= 1000
    send(e)
    assert not e.channels()['c'].sent
    clock[0] += 60
    send(e)
    assert len(e.channels()['c'].sent) == 1
    send(e)
    assert len(e.channels()['a'].sent) == 1


def test_reply_stops_and_notifies(tmp_path):
    e, clock = setup(tmp_path)
    e.deps.approve = lambda *args: (True, '')
    a = raised(e)
    send(e)
    clock[0] += 60
    send(e)
    assert e.inbound('b', Inbound('b', 'answer-1', '', 'allow', thread=a['token']))
    assert e.store.alert(a['id'])['state'] == 'answered'
    clock[0] += 60
    send(e)
    assert not e.channels()['c'].sent
    assert e.channels()['a'].sent[-1].text.endswith('handled by backup.')
    e.inbound('a', Inbound('a', 'answer-2', '', 'deny', thread=a['token']))
    assert 'already handled' in e.channels()['a'].sent[-1].text
    send(e)
    assert len([m for m in e.channels()['a'].sent if 'handled by' in m.text]) == 1


def test_permissions_and_info(tmp_path):
    e, clock = setup(tmp_path)
    assert not es.authorized(e.config(), 'b', {'kind': 'send'})
    assert es.authorized(e.config(), 'b', {'kind': 'decision'})
    raised(e, needs_reply=False)
    send(e)
    assert not e.store._all('SELECT * FROM escalation_jobs')


def test_uncertain_pause_cap_quiet(tmp_path):
    e, clock = setup(tmp_path)
    raised(e)
    e.channels()['a'].send = lambda msg: Receipt(False, uncertain=True)
    send(e)
    assert e.store._one('SELECT state FROM escalation_sends')['state'] == 'uncertain'
    e.store.kv_set('paused', True)
    clock[0] += 60
    send(e)
    assert not e.channels()['b'].sent
    e.store.kv_set('paused', False)
    cfg = e.config()
    cfg['hourly_cap'] = 1
    e.save_config(cfg)
    send(e)
    assert not e.channels()['b'].sent
    cfg['hourly_cap'] = 12
    cfg['people'][0]['quiet_hours'] = {'start': '00:00', 'end': '23:59'}
    e.save_config(cfg)
    send(e)
    assert not e.channels()['b'].sent


def test_rota_validation(tmp_path):
    e, clock = setup(tmp_path)
    cfg = e.config()
    cfg['rota'] = {'mode': 'weekly', 'people': ['owner', 'backup'], 'time': '09:00'}
    assert es.who(cfg, clock[0]) != es.who(cfg, clock[0]+604800)
    cfg['rota']['override'] = {'person': 'third', 'until': clock[0]+100}
    assert es.who(cfg, clock[0]) == 'third'
    cfg['people'][1]['channels'] = ['b']
    assert es.validate(cfg)


def test_tick_history_budget(tmp_path):
    e, clock = setup(tmp_path)
    with e.store._db:
        e.store._db.executemany('INSERT INTO escalation_jobs VALUES(?,?,0,?,?,1)',
                               ((i, '[]', 0, 0) for i in range(1, 10001)))
    calls = [0]
    def progress():
        calls[0] += 1
        return 0
    e.store._db.set_progress_handler(progress, 100)
    send(e)
    e.store._db.set_progress_handler(None, 0)
    assert calls[0] < 100, calls


def test_quiet_first_does_not_block_backup(tmp_path):
    e, clock = setup(tmp_path)
    cfg = e.config()
    cfg['quiet_hours'] = {'start': '00:00', 'end': '23:59'}
    cfg['people'][0]['quiet_hours'] = {'start': '00:00', 'end': '00:00'}
    e.save_config(cfg)
    raised(e)
    send(e)
    assert not e.channels()['a'].sent
    clock[0] += 60
    send(e)
    assert len(e.channels()['b'].sent) == 1


def test_repeated_channel_and_urgent(tmp_path):
    e, clock = setup(tmp_path)
    cfg = e.config()
    cfg['escalation'] = [{'person': 'owner', 'channels': ['a'], 'after': t} for t in (60, 120)]
    e.save_config(cfg)
    raised(e, meta={'skip_wait': True})
    send(e)
    assert len(e.channels()['a'].sent) == 1
    clock[0] += 3
    send(e)
    assert len(e.channels()['a'].sent) == 2


def test_cli_config_roundtrip(tmp_path):
    from ccc_server.oncall.escalation_cli import command
    e, clock = setup(tmp_path)
    assert command(e, {'group': 'escalate', 'action': 'list'})['ok']
    assert command(e, {'group': 'rota', 'action': 'override', 'name': 'backup'})['ok']
    assert e.status()['oncall_now'] == 'backup'
    assert command(e, {'group': 'escalate', 'action': 'rm', 'name': '3'})['ok']
    assert len(e.config()['escalation']) == 2


def test_closed_notices_survive_reopen(tmp_path):
    e, clock = setup(tmp_path)
    e.deps.approve = lambda *args: (True, '')
    a = raised(e)
    send(e)
    clock[0] += 60
    send(e)
    e.inbound('b', Inbound('b', 'answer', '', 'allow', thread=a['token']))
    e.store.close()
    e._store = None
    send(e)
    assert 'handled by backup' in e.channels()['a'].sent[-1].text


def test_preview_never_calls_transport(tmp_path):
    from ccc_server.oncall.escalation_preview import preview
    e, clock = setup(tmp_path)
    cfg = e.config()
    for c in cfg['channels']:
        c.update(topic=c['id'], server='http://127.0.0.1:1')
    e.save_config(cfg)
    e._build_channel = None
    e._chan_cache = (None, {})
    channels = e.channels()
    for chan in channels.values():
        chan.send = lambda msg: (_ for _ in ()).throw(AssertionError('Network send in preview'))
    result = preview(e)
    assert result['ok'] and [m['at_seconds'] for m in result['messages']] == [0, 60, 120]
    assert not e.store.alerts()


def test_customer_send_answer_refused(tmp_path):
    e, clock = setup(tmp_path)
    a = e.store.alert_upsert('send', kind='send', title='Send?', body='Text', now=e.clock())[0]
    assert e.inbound('b', Inbound('b', 'send-answer', '', 'yes', thread=a['token']))
    assert e.store.alert(a['id'])['state'] == 'open'
    assert 'first owner' in e.channels()['b'].sent[-1].text


def test_notices_respect_quiet_and_pause(tmp_path):
    e, clock = setup(tmp_path)
    e.deps.approve = lambda *args: (True, '')
    a = raised(e)
    send(e)
    clock[0] += 60
    send(e)
    e.inbound('b', Inbound('b', 'answer', '', 'allow', thread=a['token']))
    e.store.kv_set('paused', True)
    send(e)
    assert len(e.channels()['a'].sent) == 1
    e.store.kv_set('paused', False)
    cfg = e.config()
    cfg['quiet_hours'] = {'start': '00:00', 'end': '23:59'}
    e.save_config(cfg)
    send(e)
    assert len(e.channels()['a'].sent) == 1
    cfg['quiet_hours'] = None
    e.save_config(cfg)
    clock[0] += 3
    send(e)
    assert len(e.channels()['a'].sent) == 2


def test_quiet_customer_approval_keeps_waiting_for_owner(tmp_path):
    e, clock = setup(tmp_path)
    cfg = e.config()
    cfg['quiet_hours'] = {'start': '00:00', 'end': '23:59'}
    e.save_config(cfg)
    a = e.store.alert_upsert('send', kind='send', title='Send?', body='Text', now=e.clock())[0]
    send(e)
    clock[0] += 120
    send(e)
    assert e.store._one('SELECT done FROM escalation_jobs')['done'] == 0
    assert not e.channels()['b'].sent
    cfg['quiet_hours'] = None
    e.save_config(cfg)
    clock[0] += 3
    send(e)
    assert len(e.channels()['a'].sent) == 1


def test_urgent_command_brings_next_step_forward(tmp_path):
    from ccc_server.oncall.escalation_cli import command
    e, clock = setup(tmp_path)
    a = raised(e)
    send(e)
    command(e, {'group': 'escalate', 'action': 'urgent', 'name': str(a['id'])})
    clock[0] += 3
    send(e)
    assert len(e.channels()['b'].sent) == 1
