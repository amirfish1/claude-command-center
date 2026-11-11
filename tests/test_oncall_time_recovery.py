import time
from ccc_server.oncall import alerts
from ccc_server.oncall.store import Store, Receipt


class Channel:
    id = 'local'
    def __init__(self):
        self.sent = []
    def send(self, msg):
        self.sent.append(msg.text)
        return Receipt(ok=True)


def test_backward_clock_does_not_reset_or_freeze_hourly_cap(tmp_path):
    store = Store(tmp_path)
    channel = Channel()
    cfg = {'channels': [{'id': 'local'}], 'hourly_cap': 1}
    for n in range(2):
        store.alert_upsert(str(n), kind='permission', title='Choose', body='')
    assert alerts.deliver(cfg, store, {'local': channel}, now=100000) == 1
    # Roll back a day. Do not grant a second immediate send or hold for a day.
    assert alerts.deliver(cfg, store, {'local': channel}, now=13600) == 0
    assert alerts.deliver(cfg, store, {'local': channel}, now=17199) == 0
    assert alerts.deliver(cfg, store, {'local': channel}, now=17201) == 1
    assert len(channel.sent) == 2
    store.close()


def test_sleep_past_quiet_hours_releases_one_alert(tmp_path):
    store = Store(tmp_path)
    channel = Channel()
    cfg = {'channels': [{'id': 'local'}], 'quiet_hours': {'start': '22:00', 'end': '07:00'}}
    at = time.mktime((2026, 11, 10, 23, 30, 0, 0, 0, -1))
    store.alert_upsert('hard-stop', kind='permission', title='Choose', body='')
    assert alerts.deliver(cfg, store, {'local': channel}, now=at) == 0
    assert alerts.deliver(cfg, store, {'local': channel}, now=at + 8*3600) == 1
    assert alerts.deliver(cfg, store, {'local': channel}, now=at + 8*3600 + 3) == 0
    assert len(channel.sent) == 1
    store.close()
