from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ccc_server.oncall import retention
from ccc_server.oncall.store import Store, Receipt, Inbound

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()
OLD = datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp()


def test_upgrades_constant_partial_indexes_for_newer_sqlite(tmp_path):
    store = Store(tmp_path)
    with store._db:
        for table, age in (('deliveries', 'sent_at'), ('log', 'at')):
            store._db.execute(f'DROP INDEX retention_{table}')
            store._db.execute(f'CREATE INDEX retention_{table} ON {table}({age},id) WHERE 1=1')
        retention.initialize(store._db)
        retention.initialize(store._db)  # Reopening an upgraded store is safe.
    store.delivery_add(None, 'local', '', Receipt(ok=True), now=OLD)
    store._run("INSERT INTO log(at,kind,detail) VALUES(?, 'test', '')", (OLD,))
    for table in ('deliveries', 'log'):
        indexes = store._db.execute(f'PRAGMA index_list({table})').fetchall()
        assert next(row[4] for row in indexes if row[1] == 'retention_' + table) == 0
    # Genuine partial indexes still protect waiting work and bound scans.
    assert next(row[4] for row in store._db.execute('PRAGMA index_list(alerts)')
                if row[1] == 'retention_alerts') == 1
    removed = retention.prune(store, NOW)
    assert removed['deliveries'] == removed['log'] == 1
    store.close()


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


def test_existing_index_upgrade_serializes_store_connections(tmp_path):
    store = Store(tmp_path)
    with store._db:
        store._db.execute('DROP INDEX retention_deliveries')
        store._db.execute('CREATE INDEX retention_deliveries ON deliveries(sent_at,id) WHERE 1=1')
    store.close()

    def reopen(_):
        reader = Store(tmp_path)
        try:
            return retention.prune(reader, NOW)
        finally:
            reader.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(not any(result.values()) for result in pool.map(reopen, range(8)))


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


def test_engine_tick_budget_on_thirteen_month_store(tmp_path):
    from ccc_server.oncall.engine import Engine
    class Queue:
        calls = 0
        def blocked(self, queue):
            self.calls += 1
            return []
        def ls(self, *args, **kwargs):
            self.calls += 1
            return []
    queue = Queue()
    eng = Engine(tmp_path, wt=queue, clock=lambda: NOW, flag_on=lambda: True)
    cfg = eng.config()
    cfg.update(enabled=True, queue='DEMO')
    eng.save_config(cfg)
    with eng.store._db:
        eng.store._db.executemany("INSERT INTO alerts(key,kind,token,state,created_at,closed_at) "
                                  "VALUES(?, 'fixed', '', 'answered', ?, ?)",
                                  [(str(n), NOW-n*340, NOW-n*340) for n in range(100000)])
    with eng.store._db:
        history = [(str(n), NOW-n*3300) for n in range(10000)]
        eng.store._db.executemany("INSERT INTO reports(source,fingerprint,state,created_at,updated_at) "
                                  "VALUES('history', ?, 'closed', ?, ?)", [(key, at, at) for key, at in history])
        eng.store._db.executemany("INSERT INTO inbound(channel,external_id,received_at,handled) "
                                  "VALUES('history', ?, ?, 'handled')", history)
        eng.store._db.executemany("INSERT INTO customer_messages(kind,channel,address,text,state,created_at) "
                                  "VALUES('tell', 'history', '', '', 'sent', ?)", [(at,) for _, at in history])
        eng.store._db.executemany("INSERT INTO deliveries(channel,ok,sent_at) VALUES('history',1,?)",
                                  [(at,) for _, at in history])
        eng.store._db.executemany("INSERT INTO log(at,kind,detail) VALUES(?, 'history', '')",
                                  [(at,) for _, at in history])
    steps = []
    eng.store._db.set_progress_handler(lambda: steps.append(1) or 0, 100)
    eng.tick()
    assert len(steps) < 300, 'Tick scanned historical rows instead of bounded batches'
    assert queue.calls <= 2
    eng.store._db.set_progress_handler(None, 0)
    eng.store.close()
