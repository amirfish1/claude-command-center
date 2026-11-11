"""Every production poller must commit its inbox before its provider cursor."""
import pytest
from ccc_server.oncall import sources
from ccc_server.oncall.store import Store
from tests.test_oncall_channels import (box, web, make, EMAIL_CFG, mail_bytes, telegram,
                                  update, ntfy, ndjson, twilio, twilio_message)


@pytest.mark.parametrize('transport', ['email', 'telegram', 'ntfy', 'sms', 'whatsapp'])
@pytest.mark.parametrize('after_commit', [False, True])
def test_crash_between_poll_and_dispatch(tmp_path, monkeypatch, box, web, transport, after_commit):
    store = Store(tmp_path)
    if transport == 'email':
        channel = make(EMAIL_CFG, store)
        channel.poll()
        box.add(mail_bytes('durable reply', Message_ID='<durable@example.test>'))
    elif transport == 'telegram':
        channel = telegram(web, store)
        web.reply(200, {'ok': True, 'result': []})
        channel.poll()
        web.replies.clear()
        web.reply(200, {'ok': True, 'result': [update(42, 'durable reply')]})
    elif transport == 'ntfy':
        channel = ntfy(web, store)
        web.reply(200, b'')
        channel.poll()
        web.replies.clear()
        web.reply(200, ndjson({'id': 'new', 'time': 1700000060, 'event': 'message', 'message': 'durable reply'}))
    else:
        channel = twilio(web, store, ctype=transport)
        web.reply(200, {'messages': []})
        channel.poll()
        web.replies.clear()
        web.reply(200, {'messages': [twilio_message('local-proof', 'durable reply')]})
    before = store._all('SELECT * FROM kv ORDER BY key')
    original = store.stage_poll
    def crash(messages, updates):
        if after_commit:
            original(messages, updates)
        raise SystemExit('crash before dispatch')
    monkeypatch.setattr(store, 'stage_poll', crash)
    with pytest.raises(SystemExit):
        channel.poll()
    if not after_commit:
        assert store._all('SELECT * FROM kv ORDER BY key') == before
    store.close()
    reopened = Store(tmp_path)
    channel.kv = reopened
    if not after_commit:
        channel.poll()
    pending = reopened.polled_messages()
    assert len(pending) == 1 and pending[0][1].text == 'durable reply'
    # Restart consumption uses the ordinary inbound dedupe record.
    assert reopened.inbound_claim(pending[0][1])
    reopened.inbound_handled(pending[0][1], 'handled')
    reopened.polled_handled(pending[0][0])
    assert not reopened.inbound_claim(pending[0][1])
    reopened.close()


def test_github_cursor_waits_for_durable_callback(tmp_path):
    store = Store(tmp_path)
    def http(method, url, **kwargs):
        import json
        payload = ({'login': 'posting-account'} if url.endswith('/user') else [{
            'id': 42, 'body': 'reply', 'user': {'login': 'reporter', 'type': 'User'},
            'issue_url': 'https://api.github.com/repos/example/project/issues/1'}])
        return 200, json.dumps(payload).encode()
    source = sources.GitHubSource({'id': 'gh', 'type': 'github', 'repo': 'example/project',
                                   'token_vault': 'test'}, kv=store, secret=lambda _: 'test', http=http)
    source.receive_replies(lambda msg: None)  # baseline only
    before = source._get('comments')
    def killed(msg):
        assert store.inbound_claim(msg)
        raise SystemExit('after durable claim, before cursor')
    with pytest.raises(SystemExit):
        source.receive_replies(killed)
    assert source._get('comments') == before
    seen = []
    source.receive_replies(lambda msg: seen.append(store.inbound_claim(msg)))
    assert seen == [False]
    store.park_unhandled_inbound()
    assert store.alerts()[0]['kind'] == 'reply_problem'
    store.close()
