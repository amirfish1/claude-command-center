"""Owner-send and held-report budgets, using isolated SQLite stores."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from ccc_server.oncall import alerts, customers, replies
from ccc_server.oncall.store import Inbound, Receipt, Store

class Channel:
    def __init__(self, name, receipt=None):
        self.id, self.sent, self.receipt = name, [], receipt or Receipt(True)
    def send(self, message):
        self.sent.append(message)
        return self.receipt

def test_channels_share_cap_and_queued_decisions_resume(tmp_path):
    store = Store(tmp_path)
    a, b = Channel('a'), Channel('b')
    cfg = {'channels': [{'id': 'a'}, {'id': 'b'}], 'hourly_cap': 1}
    for n in range(2):
        alerts.raise_event(store, kind='decision', key=str(n), title='Decision', now=10000)
    assert alerts.deliver(cfg, store, {'a': a, 'b': b}, now=10000) == 0
    assert len(a.sent) == 1 and not b.sent
    assert len(store.unsent_alerts()) == 2
    assert alerts.deliver(cfg, store, {'a': a, 'b': b}, now=13601) == 1
    assert len(a.sent) == 1 and len(b.sent) == 1
    assert '1 other alert(s) waiting.' in b.sent[0].text
    assert len(store.unsent_alerts()) == 1
    store.close()

def test_acknowledgements_and_test_messages_use_budget(tmp_path):
    store = Store(tmp_path)
    channel = Channel('a')
    cfg, entry = {'hourly_cap': 1}, {'id': 'a', 'type': 'ntfy'}
    replies.handle(cfg, store, {'a': channel}, None, entry, Inbound('a', '1', '', 'status'), now=10000)
    replies.handle(cfg, store, {'a': channel}, None, entry, Inbound('a', '2', '', 'status'), now=10000)
    assert len(channel.sent) == 1
    assert not alerts.say(store, channel, 'test', now=10000, cap=1).ok
    alerts.raise_event(store, kind='decision', key='next', title='Next', now=10000)
    assert alerts.say(store, channel, 'status', now=13601, cap=1).ok
    assert '1 other alert(s) waiting.' in channel.sent[-1].text
    store.close()

def test_failed_send_does_not_spend_budget_and_uncertain_does(tmp_path):
    store = Store(tmp_path)
    failed, good = Channel('bad', Receipt(False, error='rejected')), Channel('good')
    assert not alerts.say(store, failed, 'x', now=10000, cap=1).ok
    assert alerts.say(store, good, 'x', now=10000, cap=1).ok
    assert not alerts.say(store, good, 'x', now=10000, cap=1).ok
    store.close()
    store = Store(tmp_path / 'crash')
    class Crash(Channel):
        def send(self, message):
            raise SystemExit()
    with pytest.raises(SystemExit):
        alerts.say(store, Crash('crash'), 'x', now=10000, cap=1)
    assert not alerts.say(store, good, 'x', now=10000, cap=1).ok
    store.close()

def test_parallel_owner_claims_cannot_exceed_cap(tmp_path):
    stores = [Store(tmp_path), Store(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda st: st.owner_delivery_claim(None, 'a', '', 10000, 1), stores))
    assert sum(claim is not None for claim in claims) == 1
    for store in stores:
        store.close()

def test_held_limit_counts_dropped_filed_and_source_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(customers, 'HELD_PER_DAY', 2)
    store = Store(tmp_path)
    cfg = {'customers': {'hold_unknown': True}}
    first = Inbound('mail', '1', 'first@example.test', 'One')
    assert customers.intake(cfg, store, None, first, now=10000).startswith('held report')
    customers.drop_report(store, store.reports()[0]['id'], now=10001)
    finding = SimpleNamespace(hold=True, sender='another', reporter={}, fingerprint='f',
                              title='Two', text='Two', url='', severity='')
    customers.file_finding(cfg, store, None, {'id': 'source'}, finding, now=10002)
    store.report_update(store.reports()[0]['id'], state='filed')
    third = Inbound('mail', '3', 'third@example.test', 'Three')
    assert customers.intake(cfg, store, None, third, now=10003) == 'ignored: too many unknown reports today'
    assert len(store.reports()) == 2
    assert customers.intake(cfg, store, None, third, now=96401).startswith('held report')
    store.close()

def test_duplicate_at_limit_and_parallel_intake(tmp_path):
    stores = [Store(tmp_path), Store(tmp_path)]
    def create(pair):
        store, key = pair
        return store.report_upsert('channel:a', key, title='x', text='x', now=10000,
                                   initially_held=True, held_limit=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, zip(stores, ['a', 'b'])))
    assert sum(row is not None for row, created in results) == 1
    accepted = next(row for row, created in results if row)
    duplicate, created = stores[0].report_upsert('channel:a', accepted['fingerprint'], title='x', text='x',
                                                 now=10000, initially_held=True, held_limit=1)
    assert duplicate['id'] == accepted['id'] and not created
    for store in stores:
        store.close()
