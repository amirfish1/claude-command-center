import pytest
from tests.test_oncall_channels import web, twilio
from ccc_server.oncall.store import Store, Outbound


def test_whatsapp_fallback_crash_does_not_resend_customer(tmp_path, web, monkeypatch):
    store = Store(tmp_path)
    channel = twilio(web, store, ctype='whatsapp', template_sid='test-template')
    web.reply(201, {'sid': 'original', 'status': 'queued'})
    assert channel.send(Outbound(text='Customer question', to='+15550102')).ok
    web.replies.clear()
    web.reply(200, {'status': 'failed', 'error_code': 63016})
    accepted = []
    def crash(to, text):
        accepted.append(text)
        raise SystemExit('accepted then killed')
    monkeypatch.setattr(channel, '_template', crash)
    with pytest.raises(SystemExit):
        channel.poll()
    store.close()
    store = Store(tmp_path)
    channel.kv = store
    channel.poll()
    assert accepted == ['Customer question']
    notice = store.alert_by_key('whatsapp-template-problem:tw:original')
    assert notice and 'Customer question' in notice['body']
    store.close()
