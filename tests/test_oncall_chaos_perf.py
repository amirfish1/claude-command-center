"""SQL work budgets use SQLite VM steps rather than machine-dependent timings."""
from ccc_server.oncall.store import Store, Receipt


def test_unsent_query_skips_month_of_history(tmp_path):
    store = Store(tmp_path)
    old, _ = store.alert_upsert('old', kind='permission', title='Waiting', body='')
    with store._db:
        store._db.executemany("INSERT INTO alerts(key,kind,token,created_at,state,sent_at) "
                              "VALUES(?, 'fixed', '', 1, 'open', 1)",
                              [(str(i),) for i in range(10000)])
    steps = []
    store._db.set_progress_handler(lambda: steps.append(1) or 0, 100)
    assert [a['id'] for a in store.unsent_alerts()] == [old['id']]
    assert len(steps) < 20, 'Unsent query scanned historical alerts'
    store._db.set_progress_handler(None, 0)
    store.close()


def test_delivery_budget_and_batch_rotation(tmp_path):
    store = Store(tmp_path)
    for i in range(110):
        store.alert_upsert(str(i), kind='permission', title='Waiting', body='')
    first = {a['id'] for a in store.unsent_alerts()}
    second = {a['id'] for a in store.unsent_alerts()}
    third = {a['id'] for a in store.unsent_alerts()}
    assert len(first | second | third) == 110
    for i in range(1000):
        store.delivery_add(i + 1, 'local', '', Receipt(ok=True), now=i)
    assert len(store.recent_alert_ids(0, 12)) == 12
    store.close()
