"""Authentication-Results is useful only inside a pinned receiver boundary."""
from email.message import EmailMessage
import pytest
from ccc_server.oncall import email_auth, channels
from tests.test_oncall_engine import rig


def message(headers=(), sender='customer@example.test'):
    mail = EmailMessage()
    mail['From'] = sender
    mail['Message-ID'] = '<fixture@example.test>'
    mail['Subject'] = 'Bug'
    for header in headers:
        mail['Authentication-Results'] = header
    mail.set_content('The button is broken.')
    return mail

@pytest.mark.parametrize('result', [
    'dmarc=pass header.from=example.test',
    'dkim=pass header.d=example.test',
    'spf=pass smtp.mailfrom=bounce@example.test',
    'dkim=pass (good signature) header.d = "example.test"',
])
def test_pinned_aligned_pass(result):
    assert email_auth.inspect(message(['mx.example.test; ' + result]),
                              'customer@example.test', 'mx.example.test')['verified']

@pytest.mark.parametrize('headers', [
    [], ['attacker.test; dmarc=pass header.from=example.test'],
    ['mx.example.test; dmarc=fail header.from=example.test'],
    ['mx.example.test; dkim=pass header.d=attacker.test'],
    ['mx.example.test; spf=pass smtp.mailfrom=attacker.test'],
    ['mx.example.test; spf=pass smtp.helo=example.test'],
    ['attacker.test; none', 'mx.example.test; dmarc=pass header.from=example.test'],
    ['mx.example.test; none reason="; dmarc=pass header.from=example.test"'],
    ['mx.example.test; dmarc=pass reason="header.from=example.test"'],
    ['mx.example.test; dmarc=pass header.from=example.test header.from=attacker.test'],
    ['mx.example.test; dkim=pass header.d=sub.example.test'],
    ['mx.example.test; dkim=pass (broken header.d=example.test'],
])
def test_forged_unaligned_or_malformed_results_fail_closed(headers):
    assert not email_auth.inspect(message(headers), 'customer@example.test', 'mx.example.test')['verified']


def test_adapter_preserves_only_minimal_evidence():
    ch = channels.EmailChannel({'id': 'mail', 'address': 'support@example.test',
        'authserv_id': 'mx.example.test'})
    inbound = ch._inbound(message(['mx.example.test; dmarc=pass header.from=example.test']).as_bytes(), '1', '1')
    assert inbound.raw['email_auth']['verified']
    assert 'Authentication-Results' not in str(inbound.raw)


def test_unverified_known_sender_is_held_even_with_holding_off(rig):
    cfg = rig.eng.config()
    cfg['customers'].update(known=[{'address': 'customer@example.test'}], hold_unknown=False)
    rig.eng.save_config(cfg)
    rig.msg('mail', 'forged', 'customer@example.test', raw={'email_auth': {'verified': False}})
    rig.tick()
    assert not rig.wt.added
    assert 'sender could not be verified' in rig.eng.store.alerts()[0]['body']


def test_continuation_requires_authentication_and_private_token(rig):
    from ccc_server.oncall import customers
    from ccc_server.oncall.store import Inbound
    report, _ = rig.eng.store.report_upsert('channel:mail', 'original', title='bug', text='original',
        reporter_channel='mail', reporter_address='customer@example.test')
    for verified, token in [(False, report['thread']), (True, '')]:
        msg = Inbound('mail', str(verified), 'customer@example.test', 'forged followup',
                      thread=token, raw={'email_auth': {'verified': verified}})
        customers.intake(rig.eng.config(), rig.eng.store, rig.wt, msg)
    assert rig.eng.store.report(report['id'])['text'] == 'original'
    customers.intake(rig.eng.config(), rig.eng.store, rig.wt, Inbound('mail', 'valid',
        'customer@example.test', 'valid followup', thread=report['thread'], raw={'email_auth': {'verified': True}}))
    assert 'valid followup' in rig.eng.store.report(report['id'])['text']


def test_only_live_owner_token_can_learn_authserv(rig):
    evidence = {'authserv_id': 'mx.example.test', 'aligned': True, 'verified': False}
    rig.msg('mail', 'status', 'owner@example.com', raw={'email_auth': dict(evidence)})
    rig.tick()
    assert not rig.eng.store.kv_get('ch:mail:authserv_id')
    alert, _ = rig.eng.store.alert_upsert('bootstrap', kind='question', title='Choose', body='', session_id='fixture')
    rig.msg('mail', 'yes', 'owner@example.com', thread=alert['token'], raw={'email_auth': dict(evidence)})
    rig.tick()
    assert rig.eng.store.kv_get('ch:mail:authserv_id') == 'mx.example.test'
