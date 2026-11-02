"""Real controller/outbox/source/readmodel integration with no engine transport."""
import json
import uuid

import pytest

from ccc_server.decision_answer_api import DecisionAnswerAPI
from ccc_server.decision_answer_http import AnswerRouter, answer_payload
from ccc_server.decision_delivery import AnswerOutbox
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_requests import read_requests
from ccc_server.decision_session_answers import SessionAnswers
from ccc_server.decision_sessions import SessionQuestions, session_payload, event_payload
from ccc_server.decision_session_wake import wake_session_answer


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def flow(tmp_path):
    registry = DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')
    sessions = SessionQuestions(registry)
    event = dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                 generation=uid(), title='Choose region', body=' A or B?\n', aliases=[])
    local = sessions.apply(event)
    assert read_requests(registry.db_path, answers_enabled=True) == []
    request = sessions.apply(dict(event_id=uid(), operation='promote', session_id=event['session_id'],
        request_id=local['request_id'], expected_revision=local['revision'], reason='Owner asked for inbox'))
    payload = dict(request_id=request['request_id'], expected_revision=request['revision'],
                   text='  Use A\n', idempotency_key='stable-input')
    return registry, sessions, event, request, payload


def test_http_composition_records_routes_accepts_and_consumes_without_wt(flow, tmp_path):
    registry, sessions, event, request, payload = flow
    wake = []
    enabled = {'CCC_DECISION_ANSWERS_ENABLED': '1'}
    def forbidden():
        pytest.fail('A session answer must not export WatchTower')
    pending, status = answer_payload('record', json.dumps(payload), state_root=tmp_path,
        environ=enabled, export_source=forbidden)
    assert status == 200 and pending['state'] == 'pending'
    accepted, status = answer_payload('send', json.dumps({'answer_id': pending['answer_id']}),
        state_root=tmp_path, environ=enabled, session_wake=lambda ids: wake.append(ids) or True)
    assert status == 200 and accepted['state'] == 'accepted'
    assert accepted['receipt']['acceptance'] == 'source_inbox'
    assert accepted['receipt']['session_consumed'] is False
    assert len(wake) == 1 and set(wake[0]) == {'session_id', 'request_id', 'answer_id'}
    saved = registry.list_requests()[0]
    assert saved['status'] == 'answered' and saved['delivery_state'] == 'accepted'
    source, _ = session_payload(event['session_id'], state_root=tmp_path)
    item = source['remote_answers'][0]
    assert item['answer']['text'] == payload['text'] and item['consumption'] is None
    consume = dict(operation='consume', event_id=uid(), session_id=event['session_id'],
        answer_id=pending['answer_id'], request_id=request['request_id'],
        question_id=event['question_id'], generation=event['generation'], source_message_id='actual-message')
    acknowledgment, code = event_payload(consume, state_root=tmp_path)
    assert code == 200 and acknowledgment['consumption']['session_consumed'] is True
    assert event_payload(consume, state_root=tmp_path)[0] == acknowledgment
    assert read_requests(registry.db_path, answers_enabled=True)[0]['answer']['session_consumed'] is True
    replay, _ = answer_payload('record', json.dumps(payload), state_root=tmp_path,
                               environ=enabled, export_source=forbidden)
    assert replay['answer_id'] == pending['answer_id'] and replay['state'] == 'accepted'
    assert len(wake) == 1


def test_local_answer_after_save_wins_without_stale_delivery_overwrite(flow):
    registry, sessions, event, request, payload = flow
    adapter = SessionAnswers(registry)
    api = DecisionAnswerAPI(registry, adapter=adapter, environ={'CCC_DECISION_ANSWERS_ENABLED': '1'})
    pending = api.record(json.dumps(payload))
    sessions.apply(dict(event_id=uid(), operation='answer_local', session_id=event['session_id'],
        request_id=request['request_id'], expected_revision=request['revision'],
        text='B locally', source_message_id='local-response'))
    result = api.act('send', json.dumps({'answer_id': pending['answer_id']}))
    assert result['state'] == 'rejected'
    saved = registry.list_requests()[0]
    assert saved['delivery_state'] == 'local' and saved['content']['local_answer']['text'] == 'B locally'
    assert adapter.poll(event['session_id']) == []


def test_lost_accepted_response_reconciles_without_duplicate_wake(flow):
    registry, sessions, event, request, payload = flow
    wake = []
    real = SessionAnswers(registry, notify=lambda ids: wake.append(ids) or True)
    class Lost:
        conditional_idempotent = True
        def submit(self, row):
            real.submit(row)
            raise OSError('lost response')
        lookup = staticmethod(real.lookup)
    api = DecisionAnswerAPI(registry, adapter=Lost(), environ={'CCC_DECISION_ANSWERS_ENABLED': '1'})
    pending = api.record(json.dumps(payload))
    body = json.dumps({'answer_id': pending['answer_id']})
    assert api.act('send', body)['state'] == 'unknown'
    assert api.act('reconcile', body)['state'] == 'accepted'
    assert api.act('send', body)['state'] == 'accepted'
    assert len(real.poll(event['session_id'])) == len(wake) == 1


def test_wake_is_ids_only_scoped_and_never_treated_as_consumption():
    ids = dict(session_id=uid(), answer_id=uid(), request_id=uid())
    calls = []
    def inject(sid, text, **options):
        calls.append((sid, text, options))
        return dict(ok=True, session_id=sid, same_session=True, queued=False)
    assert wake_session_answer(ids, inject=inject)
    sid, text, options = calls[0]
    assert sid == ids['session_id'] and ids['answer_id'] in text
    assert options['same_session'] is options['no_queue'] is options['skip_wt'] is True
    assert 'consume' in text and options['idempotency_key'].endswith(ids['answer_id'])
    assert not wake_session_answer(ids, inject=lambda *a, **kw: {'ok': True, 'queued': True})
