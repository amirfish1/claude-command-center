import pytest
from ccc_server.oncall import alerts
from ccc_server.oncall.store import Store, Receipt


@pytest.mark.parametrize('crash', [True, False])
def test_owner_send_restart_does_not_repeat(tmp_path, crash):
    store = Store(tmp_path)
    alert, _ = store.alert_upsert('one', kind='permission', title='Choose', body='')
    class Channel:
        id = 'local'
        calls = 0
        def send(self, msg):
            self.calls += 1
            if crash:
                raise SystemExit()
            return Receipt(ok=True)
    channel = Channel()
    if crash:
        with pytest.raises(SystemExit):
            alerts.send_alert(store, [channel], alert)
    else:
        assert alerts.send_alert(store, [channel], alert)
    store.close()
    store = Store(tmp_path)
    assert alerts.send_alert(store, [channel], alert) is (not crash)
    assert channel.calls == 1
    store.close()
