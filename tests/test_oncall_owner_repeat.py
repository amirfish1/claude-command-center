from ccc_server.oncall import alerts, customers
from ccc_server.oncall.store import Store, Receipt


def test_owner_unknown_retries_once_with_warning(tmp_path):
    store = Store(tmp_path)
    alert, _ = store.alert_upsert('owner', kind='decision', title='Choose', body='')
    class Channel:
        id = 'local'
        sent = []
        def send(self, msg):
            self.sent.append(msg.text)
            return Receipt(ok=False, uncertain=True)
    chan = Channel()
    for now in (1000, 1001, 1119, 1120, 1240, 9000):
        alerts.send_alert(store, [chan], alert, now=now)
    assert len(chan.sent) == 2
    assert chan.sent[1].startswith('This may be a repeat.\n')
    store.close()


def test_customer_unknown_never_repeats(tmp_path):
    store = Store(tmp_path)
    msg = store.customer_message_add(ref='', kind='tell', channel='local',
                                    address='customer@example.test', text='Fixed', state='pending')
    class Channel:
        id = 'local'
        calls = 0
        def send(self, msg):
            self.calls += 1
            return Receipt(ok=False, uncertain=True, error='Unknown outcome')
    chan = Channel()
    for now in (1000, 1120, 9000):
        customers.send_message(store, {'local': chan}, None, msg['id'], now=now)
    customers.collect_delivery_problems(store, now=9000)
    assert chan.calls == 1
    assert store.alert_by_key('delivery-problem:' + str(msg['id']))
    store.close()


def test_pre_upgrade_uncertain_owner_attempt_gets_one_retry(tmp_path):
    store = Store(tmp_path)
    alert, _ = store.alert_upsert('legacy', kind='decision', title='Choose', body='')
    store.delivery_add(alert['id'], 'local', '', Receipt(ok=False,
        error='Delivery interrupted; outcome unknown. No automatic retry.'), now=1000)
    class Channel:
        id = 'local'
        sent = []
        def send(self, msg):
            self.sent.append(msg.text)
            return Receipt(ok=True)
    channel = Channel()
    assert alerts.send_alert(store, [channel], alert, now=1120)
    assert len(channel.sent) == 1 and channel.sent[0].startswith('This may be a repeat.')
    store.close()
