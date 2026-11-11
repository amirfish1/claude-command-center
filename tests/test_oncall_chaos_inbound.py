"""Interrupted replies stay visible without repeating external side effects."""
import pytest
from ccc_server.oncall.store import Store, Inbound


@pytest.mark.parametrize('outcome', [None, 'error: TimeoutError', '#1 failed', 'reply for DEMO-1 failed'])
def test_unconfirmed_reply_is_parked_once(tmp_path, outcome):
    store = Store(tmp_path)
    msg = Inbound(channel='local', external_id='duplicate', sender='owner@example.test', text='yes')
    assert store.inbound_claim(msg)
    if outcome:
        store.inbound_handled(msg, outcome)
    store.close()
    store = Store(tmp_path)
    store.park_unhandled_inbound()
    store.park_unhandled_inbound()
    assert not store.inbound_claim(msg)
    assert len(store.alerts()) == 1
    assert store.alerts()[0]['kind'] == 'reply_problem'
    assert 'yes' in store.alerts()[0]['body']
    store.close()
