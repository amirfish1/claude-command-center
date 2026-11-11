"""Synthetic-only tests for On-call's outgoing secret boundary."""
from unittest.mock import patch

import pytest

from ccc_server.oncall import customers, secret_filter, sources, store
from ccc_server.oncall.store import Receipt


@pytest.fixture(autouse=True)
def synthetic_vault():
    with patch('ccc_server.vault.vault_list_entries', return_value=[{'name': 'test'}]), \
         patch('ccc_server.vault.vault_get_value', return_value='synthetic-private-value-XYZ'):
        yield


class WT:
    def __init__(self):
        self.added = []
        self.comments = []
    def add(self, queue, **kw):
        self.added.append(kw)
        return 'TEST-1'
    def comment(self, ref, text):
        self.comments.append(text)


class Channel:
    id = 'mail'
    def __init__(self):
        self.sent = []
    def send(self, message):
        self.sent.append(message)
        return Receipt(ok=True)


def test_mask_and_fail_closed():
    text, hit = secret_filter.mask('before synthetic-private-value-XYZ after')
    assert hit and text == 'before [secret redacted] after'
    # Assemble the fake shape so no realistic token appears in source.
    token = 'ghp_' + 'A' * 30
    assert secret_filter.mask(token) == ('[secret redacted]', True)
    with patch('ccc_server.vault.vault_get_value', return_value=None), pytest.raises(ValueError):
        secret_filter.mask('safe')


def test_monitoring_secret_waits_for_owner_then_files_masked(tmp_path):
    db, wt = store.Store(tmp_path), WT()
    finding = sources.Finding('fingerprint', 'problem', 'synthetic-private-value-XYZ')
    cfg = {'queue': 'TEST', 'customers': {'hold_unknown': False}}
    assert customers.file_finding(cfg, db, wt, {'id': 'monitor', 'type': 'http'}, finding) is None
    assert not wt.added
    alert = db.alerts(state='open')[0]
    assert 'synthetic-private-value-XYZ' not in str(alert)
    assert '[secret redacted]' in alert['body']
    report = db.report(alert['meta']['report_id'])
    customers.retry_unfiled(cfg, db, wt)
    assert not wt.added
    customers.file_report(cfg, db, wt, report)
    assert 'synthetic-private-value-XYZ' not in str(wt.added)
    assert '[secret redacted]' in wt.added[0]['text']
    db.close()


def test_message_held_masked_and_approved_replay(tmp_path):
    db, wt, chan = store.Store(tmp_path), WT(), Channel()
    report, _ = db.report_upsert('test', 'fp', title='Problem', text='help', state='filed',
                                 reporter_channel='mail', reporter_address='person@example.com')
    db.report_update(report['id'], ticket_ref='TEST-1')
    cfg = {'customers': {'approve_sends': False}}
    result = customers.request_message(cfg, db, {'mail': chan}, wt, ref='TEST-1', kind='tell',
                                       text='Value synthetic-private-value-XYZ')
    assert result['state'] == 'pending' and not chan.sent
    alert = db.alert(result['alert'])
    assert 'synthetic-private-value-XYZ' not in str(alert)
    assert customers.send_message(db, {'mail': chan}, wt, result['message']).ok
    assert chan.sent[0].text == 'Value [secret redacted]'
    assert 'synthetic-private-value-XYZ' not in str(wt.comments)
    db.close()


def test_owner_reword_cannot_silently_send_secret(tmp_path):
    db, wt, chan = store.Store(tmp_path), WT(), Channel()
    message = db.customer_message_add(ref='TEST-1', kind='tell', channel='mail', address='person@example.com',
                                      name='', text='safe', subject='Re: report', thread='', state='pending')
    receipt = customers.send_message(db, {'mail': chan}, wt, message['id'], text='synthetic-private-value-XYZ')
    assert not receipt.ok and not chan.sent
    assert db.customer_message(message['id'])['state'] == 'pending'
    db.close()


def test_ticket_policy_matches_channel():
    cfg = {'customers': {'approve_sends': False}}
    assert 'send immediately' in customers.report_ticket_text({'reporter_channel': 'mail'}, cfg)
    assert 'owner approves' in customers.report_ticket_text({'reporter_channel': 'github:repo'}, cfg)


def test_owner_reword_can_retry_after_secret_refusal(tmp_path):
    from ccc_server.oncall import replies
    db, wt, chan = store.Store(tmp_path), WT(), Channel()
    message = db.customer_message_add(ref='TEST-1', kind='tell', channel='mail', address='person@example.com',
                                      name='', text='safe', subject='Re: report', thread='', state='pending')
    alert = {'id': 1, 'kind': 'send', 'ref': 'TEST-1', 'meta': {'message_id': message['id']}}
    note, reply = replies._apply({}, db, {'mail': chan}, wt, alert, 'synthetic-private-value-XYZ', None, 0)
    assert note == 'unclear' and 'Nothing was sent' in reply and not chan.sent
    assert 'synthetic-private-value-XYZ' not in reply
    db.close()


def test_cli_off_requires_one_explicit_confirmation():
    from ccc_server.oncall import cli
    class Client:
        def __init__(self): self.patches = []
        def must(self, name, body): self.patches.append((name, body)); return {}
    client, asked, lines = Client(), [], []
    io = cli.IO(ask=lambda prompt: asked.append(prompt) or 'yes', out=lines.append, interactive=True)
    args = cli.build_parser().parse_args(['approve-sends', 'off'])
    assert cli._run(args, client, io) == 0
    assert len(asked) == 1 and client.patches == [('config', {'customers': {'approve_sends': False}})]
