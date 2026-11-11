"""Faults at the customer transport boundary, using the real durable store."""
import pytest
from ccc_server.oncall import customers
from ccc_server.oncall.store import Store, Receipt


def test_crash_after_accept_does_not_repeat(tmp_path):
    store = Store(tmp_path)
    message = store.customer_message_add(ref='', kind='tell', channel='local',
                                         address='reporter@example.test', text='Fixed', state='pending')
    class Channel:
        id = 'local'
        calls = 0
        def send(self, outbound):
            self.calls += 1
            raise SystemExit('crash after provider acceptance')
    channel = Channel()
    with pytest.raises(SystemExit):
        customers.send_message(store, {'local': channel}, None, message['id'])
    store.close()
    store = Store(tmp_path)
    assert not customers.send_message(store, {'local': channel}, None, message['id']).ok
    assert channel.calls == 1
    customers.collect_delivery_problems(store)
    customers.collect_delivery_problems(store)
    assert len(store.alerts()) == 1
    assert store.alerts()[0]['kind'] == 'delivery_problem'
    store.close()


def test_failed_send_and_removed_channel_are_visible(tmp_path):
    store = Store(tmp_path)
    class Channel:
        id = 'local'
        def send(self, outbound):
            return Receipt(ok=False, error='timeout after write')
    for channels in ({'local': Channel()}, {}):
        message = store.customer_message_add(ref='', kind='tell', channel='local',
                         address='reporter@example.test', text='Fixed', state='pending')
        assert not customers.send_message(store, channels, None, message['id']).ok
        assert not customers.send_message(store, channels, None, message['id']).ok
    customers.collect_delivery_problems(store)
    assert len(store.alerts()) == 2
    store.close()
