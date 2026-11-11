import pytest
from ccc_server.oncall import replies
from ccc_server.oncall.store import Store, Inbound


def test_crash_after_permission_applied_different_reply_id(tmp_path):
    store = Store(tmp_path)
    alert, _ = store.alert_upsert('approval', kind='permission', title='Tool', body='', session_id='fixture')
    class Deps:
        calls = 0
        def approve(self, *args):
            self.calls += 1
            raise SystemExit('killed after approval applied')
    deps = Deps()
    cfg, entry = {'ack': False}, {'id': 'local', 'type': 'command'}
    msg = Inbound(channel='local', external_id='first', sender='owner', text=f"{alert['id']} allow")
    with pytest.raises(SystemExit):
        replies.handle(cfg, store, {}, None, entry, msg, deps=deps)
    store.close()
    store = Store(tmp_path)
    msg.external_id = 'second'
    assert replies.handle(cfg, store, {}, None, entry, msg, deps=deps).endswith('failed')
    assert deps.calls == 1
    store.close()
