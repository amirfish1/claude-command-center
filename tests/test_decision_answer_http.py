"""Actual HTTP handlers with disposable CCC state and explicit source transport."""
import http.client
import json
import threading
import uuid
from http.server import ThreadingHTTPServer

import pytest

from ccc_server.decision_answer_api import OPT_IN
from ccc_server.decision_answer_http import answer_payload
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sources import apply_owner_export
from tests.test_decision_delivery import Adapter
from tests.test_decision_sources import receipt, row


@pytest.fixture
def http_api(tmp_path, monkeypatch):
    import server
    from ccc_server import decision_answer_http, decision_inbox
    monkeypatch.delenv(OPT_IN, raising=False)
    monkeypatch.setattr(decision_inbox, '_state_root', lambda: tmp_path)
    adapter, source = Adapter(), receipt([row()])
    original = decision_answer_http.answer_payload
    monkeypatch.setattr(decision_answer_http, 'answer_payload',
                        lambda *a, **kw: original(*a, **kw, export_source=lambda: source, adapter=adapter))

    class Handler(server.CommandCenterHandler):
        def _check_proxy_secret(self):
            return True

        def _phone_pin_blocked(self, path):
            return False

        def log_message(self, *args):
            pass

    listener = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()

    def call(method, suffix='', body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=10)
        try:
            connection.request(method, '/api/decision-inbox/answers' + suffix, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield call, tmp_path, adapter, source
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=5)


def test_disabled_factory_does_not_initialize_or_contact_dependencies(tmp_path):
    for action in ('record', 'send', 'reconcile', 'status'):
        result, status = answer_payload(action, b'{}', state_root=tmp_path, environ={},
            export_source=lambda: pytest.fail('disabled must not refresh'))
        assert status == 503 and result['error'] == 'answers_disabled'
    assert list(tmp_path.iterdir()) == []


def test_disabled_http_and_origin_body_limits(http_api):
    call, root, adapter, _ = http_api
    for method, suffix in [('GET', '?answer_id=' + str(uuid.uuid4())),
                           ('POST', ''), ('POST', '/send'), ('POST', '/reconcile')]:
        status, result = call(method, suffix, '{}' if method == 'POST' else None)
        assert status == 503 and result['error'] == 'answers_disabled'
    assert call('POST', '', '{}', Origin='https://foreign.example')[0] == 403
    assert call('POST', '', 'x' * (128 * 1024 + 1))[0] == 413
    assert call('POST', '', '')[0] == 413
    assert not (root / 'decision-inbox').exists() and adapter.calls == 0


def test_enabled_http_records_gets_sends_and_replays_without_automatic_dispatch(http_api, monkeypatch):
    call, root, adapter, source = http_api
    monkeypatch.setenv(OPT_IN, '1')
    registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3')
    request = apply_owner_export(source, registry=registry, cards={})['requests'][0]
    payload = dict(request_id=request['request_id'], expected_revision=request['revision'],
                   text='Region A', idempotency_key='browser-answer')
    status, pending = call('POST', '', json.dumps(payload))
    assert status == 200 and pending['state'] == 'pending' and adapter.calls == 0
    status, fetched = call('GET', '?answer_id=' + pending['answer_id'])
    assert status == 200 and fetched == pending and adapter.calls == 0
    body = json.dumps({'answer_id': pending['answer_id']})
    status, accepted = call('POST', '/send', body)
    assert status == 200 and accepted['state'] == 'accepted' and adapter.calls == 1
    assert accepted['work_completed'] is False
    for suffix in ('/send', '/reconcile'):
        assert call('POST', suffix, body)[1] == accepted
    source['items'] = []
    source['store_version']['revision'] += 1
    status, replay = call('POST', '', json.dumps(payload))
    assert status == 200 and replay['replay'] and replay['answer_id'] == accepted['answer_id']
    assert replay['state'] == 'accepted' and adapter.calls == 1
    assert call('POST', '', json.dumps(dict(payload, text='Changed answer')))[0] == 409
    assert call('POST', '', json.dumps(dict(payload, actor='someone')))[0] == 400
    assert call('POST', '', '{"request_id":1,"request_id":2}')[0] == 400
    assert call('GET', '?answer_id=invalid')[0] == 400
    assert call('GET', '?answer_id=' + str(uuid.uuid4()))[0] == 404
