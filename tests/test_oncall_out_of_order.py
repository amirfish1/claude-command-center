from tests.test_oncall_channels import web, ntfy, ndjson
from ccc_server.oncall.store import Store


def test_ntfy_older_reply_after_newer_is_not_lost(tmp_path, web):
    store = Store(tmp_path)
    channel = ntfy(web, store)
    web.reply(200, b'')
    channel.poll()
    web.replies.clear()
    web.reply(200, ndjson({'id': 'newer', 'time': 1700000100, 'event': 'message', 'message': 'second'}))
    assert [m.text for m in channel.poll()] == ['second']
    web.replies.clear()
    web.reply(200, ndjson({'id': 'older', 'time': 1700000050, 'event': 'message', 'message': 'first'},
                          {'id': 'newer', 'time': 1700000100, 'event': 'message', 'message': 'second'}))
    assert [m.text for m in channel.poll()] == ['first']
    assert len(store.polled_messages()) == 2
    assert channel.poll() == []
    store.close()
