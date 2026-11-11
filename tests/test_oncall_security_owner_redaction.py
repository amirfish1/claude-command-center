from ccc_server.oncall import alerts
from ccc_server.oncall.store import Receipt, Store


def test_owner_alerts_and_acknowledgements_mask_secrets(tmp_path, monkeypatch):
    from ccc_server import vault
    monkeypatch.setattr(vault, 'vault_list_entries', lambda: [{'name': 'synthetic'}])
    monkeypatch.setattr(vault, 'vault_get_value', lambda name: 'SYNTHETIC-OWNER-SECRET')
    class Channel:
        id, owner = 'fixture', 'owner@example.test'
        def __init__(self): self.sent = []
        def send(self, message):
            self.sent.append(message)
            return Receipt(True)
    store, channel = Store(tmp_path / 'oncall'), Channel()
    try:
        alert, _ = store.alert_upsert('secret', kind='question', title='SYNTHETIC-OWNER-SECRET',
                                     body='Value: SYNTHETIC-OWNER-SECRET')
        assert alerts.send_alert(store, [channel], alert)
        assert alerts.say(store, channel, 'SYNTHETIC-OWNER-SECRET').ok
        assert len(channel.sent) == 2
        assert all('SYNTHETIC-OWNER-SECRET' not in str(message) for message in channel.sent)
        assert all('[secret redacted]' in message.text for message in channel.sent)
    finally:
        store.close()
