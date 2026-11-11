from tests.test_oncall_engine import rig

def test_email_reply_id_without_token_cannot_answer(rig):
    a, _ = rig.eng.store.alert_upsert('question', kind='question', title='question', body='', session_id='test')
    from ccc_server.oncall.store import Receipt
    rig.eng.store.delivery_add(a['id'], 'mail', 'owner@example.com', Receipt(True, '<known-id>'))
    rig.msg('mail', 'allow', 'owner@example.com', reply_to='<known-id>', thread='forged')
    rig.tick()
    assert not rig.deps.injected


def test_closed_email_token_cannot_pause(rig):
    a, _ = rig.eng.store.alert_upsert('question', kind='question', title='question', body='')
    rig.eng.store.alert_close(a['id'], 'answered')
    rig.msg('mail', 'pause', 'owner@example.com', thread=a['token'])
    rig.tick()
    assert not rig.eng.store.kv_get('paused', False)
