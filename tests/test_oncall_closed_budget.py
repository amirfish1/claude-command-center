from ccc_server.oncall import customers
from ccc_server.oncall.store import Store


def test_closed_collection_rotates_with_one_listing(tmp_path):
    store = Store(tmp_path)
    for n in range(1001):
        row, _ = store.report_upsert('test', str(n), title='Report', text='', state='filed', queue='DEMO')
        store.report_update(row['id'], ticket_ref=f'DEMO-{n}')
    class Queue:
        calls = 0
        def ls(self, queue, status, limit, *, offset):
            self.calls += 1
            assert (queue, status, limit) == ('DEMO', 'closed', 100)
            return [{'ref': f'DEMO-{n}', 'status': 'closed', 'resolution': {'summary': 'Closed'}}
                    for n in range(offset, min(offset + limit, 1001))]
        def find(self, ref):
            raise AssertionError('Per-ticket lookup is forbidden')
    queue = Queue()
    for _ in range(11):
        before = queue.calls
        customers.collect_closed({'queue': 'DEMO'}, store, {}, queue)
        assert queue.calls - before == 1
    assert store.report_by_ref('DEMO-1000')['state'] == 'closed'
    assert store.alert_by_key("fixed:DEMO-1000")
    store.close()


def test_closed_batch_does_not_scan_retired_reports(tmp_path):
    store = Store(tmp_path)
    with store._db:
        store._db.executemany("INSERT INTO reports(source,fingerprint,state,created_at,updated_at) "
                              "VALUES('test', ?, 'closed', 1, 1)", [(str(n),) for n in range(100000)])
    steps = []
    store._db.set_progress_handler(lambda: steps.append(1) or 0, 100)
    assert store.filed_queues() == []
    assert len(steps) < 10
    store._db.set_progress_handler(None, 0)
    store.close()


def test_no_listing_without_filed_reports(tmp_path):
    store = Store(tmp_path)
    class Queue:
        def ls(self, *args, **kwargs):
            raise AssertionError('An empty On-call backlog must not list queue history')
    assert customers.collect_closed({'queue': 'DEMO'}, store, {}, Queue()) == 0
    store.close()
