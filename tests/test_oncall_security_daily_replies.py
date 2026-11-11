"""Direct replies have a daily budget without discarding decision alerts."""
from ccc_server.oncall import alerts
from ccc_server.oncall.store import Store
from tests.test_oncall_security_caps import Channel


def test_daily_replies_span_channels_and_leave_decisions_deliverable(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, 'DIRECT_PER_DAY', 2)
    store = Store(tmp_path)
    a, b = Channel('a'), Channel('b')
    try:
        assert alerts.say(store, a, 'ack', now=10000).ok
        assert alerts.say(store, b, 'status', now=14000).ok
        assert not alerts.say(store, a, 'ack', now=18000).ok
        alert, _ = alerts.raise_event(store, kind='decision', key='hard-stop', title='Needs you', now=18000)
        assert alerts.send_alert(store, [a], alert, now=18000)
        assert alerts.say(store, b, 'ack', now=96401).ok
        assert len(a.sent) == 2 and len(b.sent) == 2
    finally:
        store.close()
