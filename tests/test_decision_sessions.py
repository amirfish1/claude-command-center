"""Explicit lifecycle events never enroll ordinary transcript questions."""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from ccc_server.decision_delivery import AnswerConflict, AnswerOutbox
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sessions import SessionQuestionConflict, SessionQuestions, event_payload, session_payload


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def setup(tmp_path):
    registry = DecisionRegistry(tmp_path / 'requests.sqlite3')
    sessions = SessionQuestions(registry)
    create = dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                  generation=uid(), title='Choose a deployment region', body='Region A or B?',
                  aliases=['[Q12:deployment-region]'])
    return registry, sessions, create


def follow(create, request, operation, **fields):
    return dict(event_id=uid(), operation=operation, session_id=create['session_id'],
                request_id=request['request_id'], expected_revision=request['revision'], **fields)


def global_open(registry):
    return [r for r in registry.list_requests() if r.get('visibility') == 'global' and r['status'] == 'open']


def test_local_question_and_local_answer_contribute_zero_global_requests(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    assert request['visibility'] == 'local'
    assert global_open(registry) == []
    answer = follow(create, request, 'answer_local', text='Region A', source_message_id='input-message-1')
    resolved = sessions.apply(answer)
    assert resolved['status'] == 'answered'
    assert resolved['answer_state'] == 'recorded'
    assert resolved['delivery_state'] == 'local'
    assert resolved['content']['local_answer']['source_message_id'] == 'input-message-1'
    assert global_open(registry) == []
    assert [e['operation'] for e in sessions.events(request['request_id'])] == ['create', 'answer_local']


def test_promoted_question_keeps_identity_aliases_and_local_answer_resolves_it(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    promoted = sessions.apply(follow(create, request, 'promote', reason='Owner requested escalation'))
    assert promoted['request_id'] == request['request_id']
    assert promoted['visibility'] == 'global'
    assert promoted['aliases'] == request['aliases']
    assert '[Q12:deployment-region]' in promoted['aliases']
    assert [r['request_id'] for r in global_open(registry)] == [request['request_id']]
    sessions.apply(follow(create, promoted, 'answer_local', text='Region B', source_message_id='input-message-2'))
    assert global_open(registry) == []
    # Reporting a source-local answer never enables remote transport.
    with pytest.raises(AnswerConflict):
        AnswerOutbox(registry).record(promoted['request_id'], expected_revision=promoted['revision'],
                                     text='Region A', idempotency_key='remote-key')


def test_edit_preserves_id_and_alias_and_stale_promotion_is_rejected(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    edited = sessions.apply(follow(create, request, 'edit', title='Region choice', body='A, B, or C?'))
    assert edited['request_id'] == request['request_id']
    assert edited['revision'] == request['revision'] + 1
    assert edited['aliases'] == request['aliases']
    with pytest.raises(SessionQuestionConflict):
        sessions.apply(follow(create, request, 'promote', reason='Stale view'))
    assert global_open(registry) == []


def test_event_replay_is_exact_durable_and_cannot_overwrite_a_later_answer(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    answer = follow(create, request, 'answer_local', text='Region A', source_message_id='input-message-3')
    resolved = sessions.apply(answer)
    restored = SessionQuestions(DecisionRegistry(registry.db_path))
    assert restored.apply(create) == request
    assert restored.apply(answer) == resolved
    assert registry.list_requests()[0]['status'] == 'answered'
    with pytest.raises(SessionQuestionConflict):
        restored.apply(dict(answer, text='Region B'))
    assert len(restored.events(request['request_id'])) == 2


def test_new_episode_requires_explicit_resolution_and_never_reuses_identity(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    recurrence = dict(create, event_id=uid(), generation=uid())
    with pytest.raises(SessionQuestionConflict):
        sessions.apply(recurrence)
    sessions.apply(follow(create, request, 'withdraw', reason='Question replaced'))
    new = sessions.apply(recurrence)
    assert new['request_id'] != request['request_id']
    assert new['generation'] != request['generation']
    assert new['body'] == request['body']
    assert len(registry.list_requests()) == 2


def test_same_q_alias_in_different_sessions_is_not_a_merge_key(setup):
    registry, sessions, create = setup
    first = sessions.apply(create)
    second = sessions.apply(dict(create, event_id=uid(), session_id=uid()))
    assert first['aliases'] == second['aliases']
    assert first['request_id'] != second['request_id']
    assert len(registry.list_requests()) == 2


def test_concurrent_duplicate_events_and_conflicting_answers_are_atomic(setup):
    registry, sessions, create = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        requests = list(pool.map(lambda _: sessions.apply(copy.deepcopy(create)), range(16)))
    assert len({r['request_id'] for r in requests}) == 1
    request = requests[0]
    options = [follow(create, request, 'answer_local', text=text, source_message_id=uid())
               for text in ['Region A', 'Region B']]
    def apply(event):
        try:
            return sessions.apply(event)
        except SessionQuestionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, options))
    assert sum(r is not None for r in results) == 1
    assert len(sessions.events(request['request_id'])) == 2
    assert len(registry.list_requests()) == 1


def test_cross_session_or_legacy_request_events_fail_closed(setup):
    registry, sessions, create = setup
    request = sessions.apply(create)
    with pytest.raises(SessionQuestionConflict):
        sessions.apply(dict(follow(create, request, 'promote', reason='Escalate'), session_id=uid()))
    legacy = registry.import_cards({'card-1': {'title': 'Old card', 'status': 'open'}})[0]
    with pytest.raises(SessionQuestionConflict):
        sessions.apply(follow(create, legacy, 'answer_local', text='Yes', source_message_id='input-message-4'))
    assert len(sessions.events(request['request_id'])) == 1


@pytest.mark.parametrize('changes', [
    {'visibility': 'global'}, {'generation': ''}, {'session_id': '../../elsewhere'},
    {'aliases': ['Q12']}, {'aliases': ['[Q12:deployment-region]'] * 21},
    {'body': ''}, {'title': 'a' * 501}, {'event_id': 'not-a-uuid'}, {'operation': 'answer_remote'},
])
def test_invalid_create_events_do_not_create_requests(setup, changes):
    registry, sessions, create = setup
    with pytest.raises(ValueError):
        sessions.apply(dict(create, **changes))
    assert registry.list_requests() == []


def test_facade_returns_conflicts_and_unavailability_without_source_actions(tmp_path):
    create = dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                  generation=uid(), title='Choose a region', body='A or B?')
    result, status = event_payload(create, state_root=tmp_path)
    assert status == 200 and result['input_sent'] is False
    invalid, status = event_payload(dict(create, title='Changed'), state_root=tmp_path)
    assert status == 409 and invalid['ok'] is False
    invalid, status = event_payload([], state_root=tmp_path)
    assert status == 400
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('file')
    invalid, status = event_payload(create, state_root=blocked)
    assert status == 503


def test_session_read_only_projection_is_explicit_scoped_and_reports_corrupt_store(tmp_path):
    session = uid()
    value, status = session_payload(session, state_root=tmp_path)
    assert status == 200 and value['requests'] == []
    assert not (tmp_path / 'decision-inbox').exists()
    event = dict(event_id=uid(), operation='create', session_id=session, question_id=uid(),
                 generation=uid(), title='Choose region', body='A or B?')
    recorded, _ = event_payload(event, state_root=tmp_path)
    event_payload(dict(event, event_id=uid(), session_id=uid()), state_root=tmp_path)
    value, status = session_payload(session, state_root=tmp_path)
    assert status == 200 and value['remote_answers_supported'] is True
    assert value['requests'] == [recorded['request']]
    assert session_payload('invalid', state_root=tmp_path)[1] == 400
    (tmp_path / 'decision-inbox' / 'requests.sqlite3').write_bytes(b'not sqlite')
    value, status = session_payload(session, state_root=tmp_path)
    assert status == 503 and value['requests'] is None


def test_http_routes_enforce_origin_limits_and_lifecycle_with_disposable_state(tmp_path, monkeypatch):
    import http.client
    import json
    import threading
    from http.server import ThreadingHTTPServer
    import server
    from ccc_server import decision_inbox

    monkeypatch.setattr(decision_inbox, '_state_root', lambda: tmp_path)

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

    def call(method, endpoint, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=10)
        try:
            connection.request(method, endpoint, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        event = dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                     generation=uid(), title='Choose region', body='A or B?')
        endpoint = '/api/decision-inbox/session-events'
        assert call('POST', endpoint, json.dumps(event), Origin='https://foreign.example')[0] == 403
        assert not (tmp_path / 'decision-inbox').exists()
        assert call('POST', endpoint, 'x' * (128 * 1024 + 1))[0] == 413
        assert call('POST', endpoint, '{invalid')[0] == 400
        status, created = call('POST', endpoint, json.dumps(event))
        assert status == 200 and created['input_sent'] is False
        status, scoped = call('GET', '/api/decision-inbox/session-questions?session_id=' + event['session_id'])
        assert status == 200 and scoped['requests'] == [created['request']]
        promote = follow(event, created['request'], 'promote', reason='Explicit escalation')
        status, promoted = call('POST', endpoint, json.dumps(promote))
        assert status == 200 and promoted['request']['visibility'] == 'global'
        assert call('POST', endpoint, json.dumps(dict(promote, reason='Changed payload')))[0] == 409
        answer = follow(event, promoted['request'], 'answer_local', text='A', source_message_id='message-1')
        status, answered = call('POST', endpoint, json.dumps(answer))
        assert status == 200 and answered['request']['status'] == 'answered'
        assert call('POST', endpoint, json.dumps(event))[1] == created
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=5)
