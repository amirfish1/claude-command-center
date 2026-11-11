"""Voice escalation and SMS replies through a loopback Twilio protocol server."""
import email.utils
import xml.etree.ElementTree as ET
import pytest
from ccc_server.oncall import alerts, channels, calls
from ccc_server.oncall.engine import Engine
from tests.test_oncall_channels import web  # loopback HTTP fixture
from tests.test_oncall_engine import FakeWT, Deps


def setup(tmp_path, web):
    now = [1800000000.0]
    def build(cfg, **kw):
        return channels.build(cfg, secret=lambda _: 'test-token-not-real', clock=lambda: now[0], **kw)
    e = Engine(tmp_path, wt=FakeWT(), deps=Deps(), flag_on=lambda: True,
               clock=lambda: now[0], build_channel=build)
    cfg = e.config()
    cfg.update(enabled=True, ack=False, channels=[
        {'id': 'sms', 'type': 'sms', 'account_sid': 'test-account', 'auth_token_vault': 'test-token',
         'from': '+15550100', 'owner': '+15550101', 'api_base': web.base},
        {'id': 'call', 'type': 'call', 'sms_channel': 'sms', 'after_minutes': 1},
        {'id': 'backup', 'type': 'sms', 'account_sid': 'test-account', 'auth_token_vault': 'test-token',
         'from': '+15550100', 'owner': '+15550102', 'api_base': web.base}],
        escalation=[{'channels': ['sms'], 'after': 0}, {'channels': ['call'], 'after': 60},
                    {'channels': ['backup'], 'after': 120}])
    e.save_config(cfg)
    web.reply(201, {'sid': 'test-message'})
    return e, now


def raise_alert(e, key='incident', kind='stopped', **kw):
    return e.store.alert_upsert(key, kind=kind, title='Build agent',
        body='The task stopped. See https://example.com/ TASK-123 12345678-abcd.',
        session_id='test-session', now=e.clock(), **kw)[0]


def send(e):
    alerts.deliver(e.config(), e.store, e.channels(), now=e.clock())


def requests(web):
    return [r for r in web.requests if r['path'].endswith('/Calls.json')]


def test_call_and_sms_answer_once(tmp_path, web):
    e, now = setup(tmp_path, web)
    a = raise_alert(e)
    sms = e.channels()['sms']
    web.replies.clear(); web.reply(200, {'messages': []})
    assert sms.poll() == []  # establish the existing SMS cursor
    web.replies.clear(); web.reply(201, {'sid': 'test-sent'})
    send(e)
    now[0] += 60
    web.replies.clear(); web.reply(201, {'sid': 'test-call'})
    send(e)
    assert len(requests(web)) == 1
    form = web.form()
    assert form['To'] == ['+15550101'] and form['From'] == ['+15550100']
    root = ET.fromstring(form['Twiml'][0])
    speech = root.find('Say').text
    assert 'Build agent' in speech and 'Option 1. continue.' in speech
    assert all(x not in speech for x in ('https:', 'TASK-123', '12345678-abcd', '#1'))
    assert root.find('Gather') is None and root.find('Hangup') is not None
    web.replies.clear(); web.reply(200, {'messages': [{'sid': 'test-reply', 'direction': 'inbound',
        'from': '+15550101', 'body': f"{a['id']} continue", 'date_sent': email.utils.formatdate(now[0], usegmt=True)}]})
    messages = sms.poll()
    assert len(messages) == 1
    e.inbound('sms', messages[0]); e.inbound('sms', messages[0])
    assert e.deps.injected == [('test-session', 'Continue where you left off.')]
    now[0] += 120
    send(e)
    assert len(requests(web)) == 1
    assert e.store.alert(a['id'])['state'] == 'answered'


@pytest.mark.parametrize('status, payload', [(201, {'sid':'test-call', 'status':'busy'}),
    (201, {'sid':'test-call', 'status':'no-answer'}), (500, {}), (400, {})])
def test_failure_next_step_no_retry(tmp_path, web, status, payload):
    e, now = setup(tmp_path, web); raise_alert(e); send(e)
    now[0] += 60
    web.replies.clear(); web.reply(status, payload)
    send(e)
    now[0] += 60
    web.replies.clear(); web.reply(201, {'sid':'test-backup'})
    send(e); send(e)
    assert len(requests(web)) == 1
    assert e.store.delivery_latest(1, 'backup')['ok']


def test_caps_quiet_and_answered(tmp_path, web):
    e, now = setup(tmp_path, web)
    cfg = e.config(); cfg['channels'][1]['hourly_cap'] = 1; e.save_config(cfg)
    a = raise_alert(e); raise_alert(e, 'second'); send(e)
    now[0] += 60; send(e)
    assert len(requests(web)) == 1
    e.store.alert_close(a['id'], 'answered', now=now[0])
    send(e); assert len(requests(web)) == 1


@pytest.mark.parametrize('override, expected', [('false', 0), ('true', 1)])
def test_quiet_override(tmp_path, web, override, expected):
    e, now = setup(tmp_path, web); raise_alert(e); send(e)
    cfg = e.config(); cfg['quiet_hours'] = {'start':'00:00', 'end':'23:59'}
    cfg['channels'][1]['during_quiet'] = override; e.save_config(cfg)
    now[0] += 60; send(e)
    assert len(requests(web)) == expected


def test_answer_before_call_and_excluded_kind(tmp_path, web):
    e, now = setup(tmp_path, web)
    a = raise_alert(e); raise_alert(e, 'other', kind='question'); send(e)
    e.store.alert_close(a['id'], 'answered', now=now[0])
    now[0] += 60; send(e)
    assert not requests(web)


def test_no_earlier_sms_no_call_and_general_test_skips(tmp_path, web):
    e, now = setup(tmp_path, web); raise_alert(e)
    now[0] += 60  # enroll now, not at creation
    send(e)
    assert not requests(web)
    e.test(); assert not requests(web)
    e.test(call=True); assert len(requests(web)) == 1


def test_stuck_can_escalate_and_no_plan_never_calls(tmp_path, web):
    e, now = setup(tmp_path, web); raise_alert(e, kind='stuck', needs_reply=False)
    send(e); now[0] += 60; send(e)
    assert len(requests(web)) == 1
    cfg = e.config(); cfg['escalation'] = []; e.save_config(cfg)
    raise_alert(e, 'new'); send(e)
    assert len(requests(web)) == 1


def test_invalid_call_settings(tmp_path, web):
    e, _ = setup(tmp_path, web)
    for key, value in [('sms_channel','missing'), ('hourly_cap',0), ('after_minutes',0), ('during_quiet','maybe')]:
        cfg = e.config(); cfg['channels'][1][key] = value
        with pytest.raises(ValueError):
            e.save_config(cfg)


def test_ci_ticket_decision_calls_and_answers_once(tmp_path, web):
    e, now = setup(tmp_path, web)
    cfg = e.config(); cfg['queue'] = 'TEST'; cfg['sources'] = [{'id':'builds', 'type':'ci'}]
    e.save_config(cfg)
    ref = e.wt.add('TEST', title='Failed build', text='Build failed')
    e.wt.park(ref, 'Retry the build?')
    report, _ = e.store.report_upsert('source:builds', 'test-run', title='Failed build', text='Build failed', now=now[0])
    e.store._run('UPDATE reports SET ticket_ref=? WHERE id=?', (ref, report['id']))
    alerts.collect_decisions(cfg, e.store, e.wt, now=now[0])
    a = e.store.alerts(state='open')[0]
    assert a['meta']['call_kind'] == 'ci_failed'
    send(e); now[0] += 60; send(e)
    assert len(requests(web)) == 1
    from ccc_server.oncall.store import Inbound
    msg = Inbound('sms', 'test-answer', '+15550101', f"{a['id']} retry")
    e.inbound('sms', msg); e.inbound('sms', msg)
    assert e.wt.answers == [(ref, 'retry')]


def test_urgent_still_waits_and_requires_successful_sms(tmp_path, web):
    e, now = setup(tmp_path, web)
    raise_alert(e, meta={'skip_wait':True})
    web.replies.clear(); web.reply(400, {})
    send(e); now[0] += 60; send(e)
    assert not requests(web)


def test_preview_off_never_sends(tmp_path, web):
    e, now = setup(tmp_path, web); raise_alert(e)
    e.flag_on = lambda: False
    e.tick()
    assert not web.requests


def test_cli_call_is_explicit():
    from ccc_server.oncall.cli import build_parser
    parser = build_parser()
    assert parser.parse_args(['test', '--call']).call
    assert not parser.parse_args(['test']).call
    assert parser.parse_args(['channel', 'add', 'call', '--set', 'sms_channel=sms']).type == 'call'


def test_unknown_answer_outcome_does_not_ring(tmp_path, web):
    e, now = setup(tmp_path, web); a = raise_alert(e); send(e)
    e.store.reply_action_claim(a['id'])
    now[0] += 60; send(e)
    assert not requests(web)
