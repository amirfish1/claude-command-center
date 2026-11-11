from datetime import datetime, timezone
from ccc_server.oncall import retention
from ccc_server.oncall.store import Store, Receipt, Inbound

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()
OLD = datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp()


def test_retention_preserves_waiting_and_52_weeks(tmp_path):
    store = Store(tmp_path)
    old, _ = store.alert_upsert('closed', kind='decision', title='', body='', now=OLD)
    store.alert_close(old['id'], 'answered', now=OLD)
    waiting, _ = store.alert_upsert('waiting', kind='decision', title='', body='', now=OLD)
    recent, _ = store.alert_upsert('52weeks', kind='decision', title='', body='', now=NOW - 364*86400)
    store.alert_close(recent['id'], 'answered', now=NOW - 364*86400)
    store.delivery_add(waiting['id'], 'local', '', Receipt(ok=True), now=OLD)
    store.delivery_add(old['id'], 'local', '', Receipt(ok=True), now=OLD)
    for handled in ('handled', '', 'needs_review'):
        msg = Inbound(channel='local', external_id=handled, sender='', text='', received_at=OLD)
        store.inbound_claim(msg)
        store.inbound_handled(msg, handled)
    for state in ('pending', 'uncertain', 'failed', 'dropped', 'sent'):
        store.customer_message_add(ref='', kind='tell', channel='local', address='', text='', state=state, now=OLD)
    removed = retention.prune(store, NOW)
    assert removed['alerts'] == 1 and removed['deliveries'] == 1
    assert removed['inbound'] == 1 and removed['customer_messages'] == 2
    assert store.alert(waiting['id']) and store.alert(recent['id'])
    assert {m['state'] for m in store.customer_messages()} == {'pending', 'uncertain', 'failed'}
    store.close()


def test_retention_budget_does_not_scan_thirteen_months(tmp_path):
    store = Store(tmp_path)
    with store._db:
        store._db.executemany("INSERT INTO alerts(key,kind,token,state,created_at,closed_at) "
                              "VALUES(?, 'fixed', '', 'answered', ?, ?)",
                              [(str(i), NOW - i, NOW - i) for i in range(100000)])
    steps = []
    store._db.set_progress_handler(lambda: steps.append(1) or 0, 100)
    removed = retention.prune(store, NOW)
    assert not any(removed.values())
    assert len(steps) < 20, 'Retention scanned retained history'
    store._db.set_progress_handler(None, 0)
    store.close()


def test_retention_batches_and_rotates_past_protected_deliveries(tmp_path):
    store = Store(tmp_path)
    waiting, _ = store.alert_upsert('waiting', kind='decision', title='', body='', now=OLD)
    for _ in range(150):
        store.delivery_add(waiting['id'], 'local', '', Receipt(ok=True), now=OLD)
    for _ in range(150):
        store.delivery_add(None, 'local', '', Receipt(ok=True), now=OLD)
    results = [retention.prune(store, NOW)['deliveries'] for _ in range(4)]
    assert max(results) <= 100 and sum(results) == 150
    assert len(store.deliveries_since(0)) == 150
    store.close()
