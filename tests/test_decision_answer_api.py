"""Controller-only tests: isolated registries and optional temporary CLI stores."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from ccc_server.decision_answer_api import (
    AnswerAPIError, DecisionAnswerAPI, MAX_BODY_BYTES, OPT_IN, parse_request,
)
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sources import REQUIRED_CAPABILITIES, apply_owner_export
from tests.test_decision_delivery import Adapter
from tests.test_decision_watchtower import integrated, source_item  # noqa: F401


@pytest.fixture
def api_setup(tmp_path):
    registry = DecisionRegistry(tmp_path / 'requests.sqlite3')
    store_id = str(uuid.uuid4())
    generation, body, wait = 'episode-1', ' Choose a color? \n', None
    digest = hashlib.sha256(json.dumps([generation, body, 'owner', wait], sort_keys=True).encode()).hexdigest()
    export = dict(schema_version=1, adapter='watchtower-block', authoritative=True, complete=True,
                  capabilities=sorted(REQUIRED_CAPABILITIES), scope=dict(backend='file', store_id=store_id),
                  store_version=dict(revision=1, snapshot_sha256='0' * 64),
                  items=[dict(origin_id='EXAMPLE-1', generation=generation, body=body,
                              expected_block_question=body, expected_blocked_at=generation,
                              expected_wait_kind='owner', wait_kind='owner', expected_store_id=store_id,
                              expected_wait=wait, source_revision=digest)])
    row = apply_owner_export(export, registry=registry, cards={})['requests'][0]
    adapter = Adapter()
    api = DecisionAnswerAPI(registry, export_source=lambda: export, adapter=adapter, environ={OPT_IN: '1'})
    data = dict(request_id=row['request_id'], expected_revision=row['revision'], text='Blue',
                idempotency_key='canonical-key')
    return api, data, export, adapter


def record(api, data):
    return api.record(json.dumps(data))


def act(api, action, answer):
    return api.act(action, json.dumps({'answer_id': answer['answer_id']}))


def error(code, call):
    with pytest.raises(AnswerAPIError) as caught:
        call()
    assert caught.value.code == code
    return caught.value.status


def test_disabled_never_initializes_outbox_records_refreshes_or_contacts(api_setup):
    api, data, _, adapter = api_setup
    for value in (None, 'true', 'yes', 1, '0'):
        disabled = DecisionAnswerAPI(api.registry, environ={OPT_IN: value},
                                     export_source=lambda: pytest.fail('refresh'), adapter=adapter)
        assert disabled.outbox is None
        assert error('answers_disabled', lambda: record(disabled, data)) == 503
        error('answers_disabled', lambda: disabled.status(str(uuid.uuid4())))
        error('answers_disabled', lambda: disabled.act('send', '{}'))
    assert api.registry.list_requests()[0]['answer_state'] == 'none'
    assert adapter.calls == 0


@pytest.mark.parametrize('change', [
    {'request_id': 'EXAMPLE-1'}, {'request_id': str(uuid.uuid4()).upper()},
    {'expected_revision': True}, {'expected_revision': 0}, {'expected_revision': -1},
    {'expected_revision': 1.0}, {'expected_revision': 2**63}, {'text': ''}, {'text': ' '},
    {'text': 'x' * 24001}, {'text': '\ud800'}, {'text': 2}, {'idempotency_key': ''},
    {'idempotency_key': 'x' * 201}, {'actor': 'admin'}, {'source_snapshot': {}},
    {'routing': {}}, {'session_id': 'override'}, {'by_kind': 'worker'},
])
def test_strict_record_validation(api_setup, change):
    api, data, _, _ = api_setup
    error('invalid_request', lambda: record(api, dict(data, **change)))
    assert api.outbox.get_by_key(data['idempotency_key']) is None


@pytest.mark.parametrize('body', ['[]', 'null', '{}', '{', '{"x":1,"x":2}',
                                   '{"x":NaN}', b'\xff', '[' * 2000])
def test_malformed_json(body):
    error('invalid_request', lambda: parse_request(body))


def test_body_bounds_and_action_validation(api_setup):
    assert error('body_too_large', lambda: parse_request(' ' * (MAX_BODY_BYTES + 1))) == 413
    api, _, _, _ = api_setup
    for body in ('{}', '{"answer_id":"invalid"}', json.dumps({'answer_id': str(uuid.uuid4()), 'text': 'override'})):
        error('invalid_request', lambda: api.act('send', body))
    error('invalid_action', lambda: api.act('retry', '{}'))
    assert error('answer_not_found', lambda: api.status(str(uuid.uuid4()))) == 404


def test_record_and_status_never_dispatch_and_actor_is_frozen(api_setup):
    api, data, _, adapter = api_setup
    result = record(api, data)
    assert result['state'] == 'pending' and result['answer']['actor'] == 'owner'
    api.status(result['answer_id'])
    assert adapter.calls == 0
    assert record(api, data)['answer_id'] == result['answer_id']
    error('answer_conflict', lambda: record(api, dict(data, text='Red')))
    error('answer_conflict', lambda: record(api, dict(data, idempotency_key='other')))


def test_replay_survives_withdrawal_and_offline_refresh_and_never_resends(api_setup):
    api, data, export, adapter = api_setup
    answer = record(api, data)
    accepted = act(api, 'send', answer)
    assert accepted['state'] == 'accepted' and accepted['work_completed'] is False
    empty = dict(export, items=[], store_version=dict(revision=2, snapshot_sha256='1' * 64))
    apply_owner_export(empty, registry=api.registry, cards={})
    api.export_source = lambda: pytest.fail('Replay must not refresh')
    assert record(api, data)['state'] == 'accepted'
    assert act(api, 'send', answer)['receipt'] == accepted['receipt']
    assert act(api, 'reconcile', answer)['state'] == 'accepted'
    assert adapter.calls == 1


@pytest.mark.parametrize('bad', [None, {}, {'complete': False}])
def test_failed_refresh_does_not_record_or_withdraw(api_setup, bad):
    api, data, _, _ = api_setup
    api.export_source = lambda: bad
    error('source_unavailable', lambda: record(api, data))
    assert api.registry.list_requests()[0]['status'] == 'open'
    assert api.outbox.get_by_key(data['idempotency_key']) is None


def test_current_revision_required_after_refresh(api_setup):
    api, data, export, _ = api_setup
    export['items'][0]['title'] = 'Changed title'
    export['store_version'] = dict(revision=2, snapshot_sha256='1' * 64)
    error('answer_conflict', lambda: record(api, data))
    data['expected_revision'] = api.registry.list_requests()[0]['revision']
    assert record(api, data)['state'] == 'pending'


def test_routine_owner_cannot_be_stolen(api_setup):
    api, data, _, _ = api_setup
    with api.registry._transaction() as db:
        db.execute('INSERT INTO metadata VALUES (?,?)', ('delivery_owner:' + data['request_id'], 'routine'))
    error('answer_conflict', lambda: record(api, data))
    assert api.outbox.get_by_key(data['idempotency_key']) is None


def test_unsupported_sources_never_fall_back(api_setup):
    api, data, _, _ = api_setup
    for changes in ({'adapter': 'legacy-card'}, {'adapter': 'session'}, {'visibility': 'local'},
                    {'kind': 'review'}, {'wait_kind': 'rationale'}):
        source = dict(api.registry.list_requests()[0]['content'], **changes)
        row = api.registry.upsert_requests([source])[0]
        error('answer_conflict', lambda: record(api, dict(data, request_id=row['request_id'],
                                                        expected_revision=row['revision'])))


def test_concurrent_record_send_and_reconcile_are_single_owner(api_setup):
    api, data, _, adapter = api_setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda _: record(api, data), range(16)))
    assert len({a['answer_id'] for a in answers}) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: act(api, 'send', answers[0]), range(16)))
    assert adapter.calls == 1
    assert api.status(answers[0]['answer_id'])['state'] == 'accepted'


def test_unknown_requires_receipt_first_then_explicit_frozen_retry(api_setup):
    api, data, _, adapter = api_setup
    answer = record(api, data)
    payloads = []
    original = adapter.submit
    def fail_before_source(row):
        payloads.append(row['payload'])
        raise TimeoutError()
    adapter.submit = fail_before_source
    assert act(api, 'send', answer)['state'] == 'unknown'
    assert record(api, data)['state'] == 'unknown'
    assert act(api, 'send', answer)['state'] == 'unknown'
    assert len(payloads) == 1
    assert act(api, 'reconcile', answer)['state'] == 'pending'
    assert len(payloads) == 1
    def recover(row):
        assert row['payload'] == payloads[0]
        assert row['answer_id'] == answer['answer_id']
        return original(row)
    adapter.submit = recover
    assert act(api, 'send', answer)['state'] == 'accepted'


def real_api(integrated):
    outbox, answer, adapter, *_ = integrated
    api = DecisionAnswerAPI(outbox.registry, adapter=adapter, environ={OPT_IN: '1'})
    return api, answer, adapter


@pytest.mark.parametrize('changed', [
    {'blocked_at': 'episode-2'}, {'block_question': 'Choose a color?'},
    {'wait': {'on': 'owner', 'block_at': 'episode-1', 'revision': 'wait-2'}},
    {'wait': {'on': 'external', 'block_at': 'episode-1'}},
])
def test_real_controller_stale_source_cas(integrated, changed):
    api, answer, _ = real_api(integrated)
    integrated[4](changed)
    before = source_item(integrated[3])
    assert act(api, 'send', answer)['state'] == 'rejected'
    assert source_item(integrated[3]) == before


def test_real_controller_lost_response_and_wrong_store(integrated):
    api, answer, adapter = real_api(integrated)
    submit = adapter.submit
    def lost(row):
        submit(row)
        raise TimeoutError()
    adapter.submit = lost
    assert act(api, 'send', answer)['state'] == 'unknown'
    other = integrated[5]()
    before = source_item(other)
    assert act(api, 'reconcile', answer)['state'] == 'unknown'
    assert source_item(other) == before


def test_real_controller_acceptance_is_sticky_after_loss(integrated):
    api, answer, adapter = real_api(integrated)
    submit = adapter.submit
    def lost(row):
        submit(row)
        raise TimeoutError()
    adapter.submit = lost
    assert act(api, 'send', answer)['state'] == 'unknown'
    assert act(api, 'reconcile', answer)['state'] == 'accepted'
    replay = dict(answer['payload']['answer'], idempotency_key=answer['idempotency_key'])
    replay.pop('actor')
    assert record(api, replay)['state'] == 'accepted'
    assert act(api, 'send', answer)['state'] == 'accepted'
    assert sum(e['event'] == 'answer' for e in source_item(integrated[3])['history']) == 1


def test_sending_replay_and_get_do_not_reconcile(api_setup):
    api, data, _, adapter = api_setup
    answer = record(api, data)
    api.outbox._claim(answer['answer_id'])  # Simulate an interrupted process.
    adapter.lookup = lambda _: pytest.fail('Status/replay/send must not lookup receipts')
    assert api.status(answer['answer_id'])['state'] == 'sending'
    assert record(api, data)['state'] == 'sending'
    assert act(api, 'send', answer)['state'] == 'sending'
    assert adapter.calls == 0


def test_non_authoritative_absence_never_requeues(api_setup):
    api, data, _, adapter = api_setup
    answer = record(api, data)
    api.outbox._claim(answer['answer_id'])
    adapter.lookup = lambda _: dict(status='not_found', authoritative=False)
    assert act(api, 'reconcile', answer)['state'] == 'sending'
    assert adapter.calls == 0


def test_real_controller_wrong_store_before_send(integrated):
    api, answer, _ = real_api(integrated)
    other = integrated[5]()
    before = source_item(other)
    assert act(api, 'send', answer)['state'] == 'rejected'
    assert source_item(other) == before


def test_source_failure_after_answer_does_not_hide_pending_replay(api_setup):
    api, data, _, _ = api_setup
    answer = record(api, data)
    def unavailable():
        raise ConnectionError('offline')
    api.export_source = unavailable
    assert record(api, data)['answer_id'] == answer['answer_id']
    error('source_unavailable', lambda: record(api, dict(data, idempotency_key='new-key')))
    assert api.outbox.get_by_key('new-key') is None


def test_real_controller_new_record_refresh_and_send(integrated, tmp_path):
    _, _, adapter, store, *_ = integrated
    registry = DecisionRegistry(tmp_path / 'controller.sqlite3')
    def export_source():
        code, receipt = adapter._call(['owner-blocks-export', '--json'])
        assert code == 0
        return receipt
    row = apply_owner_export(export_source(), registry=registry, cards={})['requests'][0]
    api = DecisionAnswerAPI(registry, export_source=export_source, adapter=adapter, environ={OPT_IN: '1'})
    data = dict(request_id=row['request_id'], expected_revision=row['revision'],
                text='Green', idempotency_key='controller-new-answer')
    before = source_item(store)
    answer = record(api, data)
    assert answer['state'] == 'pending'
    assert source_item(store) == before
    assert act(api, 'send', answer)['state'] == 'accepted'
    assert sum(e['event'] == 'answer' for e in source_item(store)['history']) == 1


def test_registry_failure_has_distinct_error(api_setup, monkeypatch):
    import sqlite3
    api, data, _, _ = api_setup
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('busy')
    monkeypatch.setattr(api.outbox, 'get_by_key', unavailable)
    assert error('registry_unavailable', lambda: record(api, data)) == 503
    monkeypatch.setattr(api.outbox, 'get', unavailable)
    error('registry_unavailable', lambda: api.status(str(uuid.uuid4())))
