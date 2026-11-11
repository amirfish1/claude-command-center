from ccc_server.oncall import replies
from ccc_server.oncall.store import Inbound
from tests.test_oncall_engine import rig


def test_unknown_number_does_not_fall_back_to_only_alert(rig):
    a, _ = rig.eng.store.alert_upsert('one', kind='question', title='one', body='', session_id='test')
    rig.eng.store.alert_mark_sent(a['id'])
    rig.msg('tg', '999999 allow', '4242')
    rig.tick()
    assert not rig.deps.injected


def test_thread_and_explicit_number_must_agree(rig):
    a, _ = rig.eng.store.alert_upsert('one', kind='question', title='one', body='', session_id='test')
    rig.msg('mail', '999999 allow', 'owner@example.com', thread=a['token'])
    rig.tick()
    assert not rig.deps.injected
