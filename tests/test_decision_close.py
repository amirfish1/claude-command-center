"""Owner close: exact-question WatchTower closes and local session hides."""
import json
import sqlite3
import subprocess
import uuid

import pytest

from ccc_server.decision_answer_api import OPT_IN
from ccc_server.decision_close import REASON, close_payload
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_requests import read_requests
from ccc_server.decision_sources import apply_owner_export
from ccc_server.decision_watchtower import WatchTowerCloses
from tests.test_decision_sources import SCOPE, receipt, row

ENABLED = {OPT_IN: '1'}


class Source:
    def __init__(self, status='accepted', reason=None):
        self.status, self.reason, self.calls = status, reason, []

    def close(self, record, key, reason):
        self.calls.append((record['origin_id'], key, reason))
        return dict(status=self.status, reason=self.reason)


@pytest.fixture
def db(tmp_path):
    return tmp_path / 'decision-inbox' / 'requests.sqlite3'


@pytest.fixture
def registry(db):
    registry = DecisionRegistry(db)
    apply_owner_export(receipt([row()]), registry=registry, cards={})
    return registry


def only(registry):
    return next(r for r in registry.list_requests() if r['adapter'] == 'watchtower-block')


def session_row(registry):
    return registry.upsert_requests([dict(adapter='session-question', origin_id='s1', generation='q1',
                                          kind='question', visibility='global', title='Q', body='Why?')])[0]


def body(record, key=None, revision=None):
    return json.dumps(dict(request_id=record['request_id'], idempotency_key=key or str(uuid.uuid4()),
                           expected_revision=revision or record['revision']))


def call(tmp_path, payload, adapter=None, environ=ENABLED):
    return close_payload(payload, state_root=tmp_path, environ=environ, adapter=adapter)


def test_disabled_touches_nothing(tmp_path):
    result, status = call(tmp_path, b'{}', environ={})
    assert (status, result['error']) == (503, 'answers_disabled')
    assert not (tmp_path / 'decision-inbox').exists()


@pytest.mark.parametrize('payload', [b'', b'[]', b'{}', b'{"request_id":"x","expected_revision":1,"idempotency_key":"y"}'])
def test_invalid_body(tmp_path, payload):
    assert call(tmp_path, payload)[1] == 400


def test_extra_field_refused(tmp_path, registry):
    record = only(registry)
    payload = dict(json.loads(body(record)), force=True)
    assert call(tmp_path, json.dumps(payload))[1] == 400


def test_watchtower_close_marks_row_and_survives_refresh(tmp_path, registry, db):
    record, source, key = only(registry), Source(), str(uuid.uuid4())
    result, status = call(tmp_path, body(record, key), source)
    assert status == 200 and result['state'] == 'closed' and result['scope'] == 'source'
    assert result['work_completed'] is False
    assert source.calls == [(record['origin_id'], key, REASON)]
    apply_owner_export(receipt([row()], revision=2), registry=registry, cards={})
    view = read_requests(db, answers_enabled=True, source_current=True)[0]
    assert (view['status'], view['needs_answer'], view['can_close']) == ('closed', False, False)


def test_replay_same_key_does_not_call_source_again(tmp_path, registry):
    record, source, key = only(registry), Source(), str(uuid.uuid4())
    first = call(tmp_path, body(record, key), source)
    assert call(tmp_path, body(record, key), source) == first
    assert len(source.calls) == 1


def test_other_key_after_close_is_conflict(tmp_path, registry):
    record, source = only(registry), Source()
    call(tmp_path, body(record), source)
    assert call(tmp_path, body(record), source)[1] == 409
    assert len(source.calls) == 1


def test_stale_revision_never_reaches_source(tmp_path, registry):
    record, source = only(registry), Source()
    result, status = call(tmp_path, body(record, revision=record['revision'] + 1), source)
    assert (status, result['error']) == (409, 'conflict') and source.calls == []
    assert registry.closed_marker(record['request_id']) is None


def test_source_conflict_leaves_row_open(tmp_path, registry):
    record = only(registry)
    result, status = call(tmp_path, body(record), Source('rejected', 'conflict'))
    assert (status, result['error']) == (409, 'conflict')
    assert registry.closed_marker(record['request_id']) is None


def test_unknown_outcome_is_not_reported_closed(tmp_path, registry):
    record = only(registry)
    result, status = call(tmp_path, body(record), Source('unknown'))
    assert (status, result['state']) == (200, 'unknown')
    assert registry.closed_marker(record['request_id']) is None


def test_withdrawn_row_replays_lost_close_at_source(tmp_path, registry):
    record, source, key = only(registry), Source(), str(uuid.uuid4())
    apply_owner_export(receipt([], revision=2), registry=registry, cards={})
    assert only(registry)['status'] == 'withdrawn'
    result, status = call(tmp_path, body(record, key), source)
    assert status == 200 and result['state'] == 'closed' and len(source.calls) == 1


@pytest.mark.parametrize('answer,delivery,allowed', [('recorded', 'pending', False), ('recorded', 'unknown', False),
                                                     ('recorded', 'accepted', False), ('recorded', 'rejected', True)])
def test_saved_answer_blocks_close_until_rejected(tmp_path, registry, db, answer, delivery, allowed):
    record, source = only(registry), Source()
    connection = sqlite3.connect(db)
    connection.execute('UPDATE requests SET answer_state=?, delivery_state=? WHERE request_id=?',
                       (answer, delivery, record['request_id']))
    connection.commit(); connection.close()
    result, status = call(tmp_path, body(record), source)
    assert (status == 200) is allowed
    if not allowed:
        assert result['error'] == 'answer_in_progress' and source.calls == []


def test_routine_owned_question_refused(tmp_path, registry, db):
    record, source = only(registry), Source()
    connection = sqlite3.connect(db)
    connection.execute('INSERT INTO metadata VALUES (?,?)', ('delivery_owner:' + record['request_id'], 'routine'))
    connection.commit(); connection.close()
    assert call(tmp_path, body(record), source)[0]['error'] == 'routine_owned' and source.calls == []
    assert read_requests(db, answers_enabled=True, source_current=True)[0]['can_close'] is False


def test_can_close_needs_current_source_and_enabled_answers(registry, db):
    assert read_requests(db, answers_enabled=True, source_current=True)[0]['can_close'] is True
    assert read_requests(db, answers_enabled=False, source_current=True)[0]['can_close'] is False
    assert read_requests(db, answers_enabled=True, source_current=False)[0]['can_close'] is False


def test_session_question_is_only_hidden_locally(tmp_path, registry, db):
    record, source, key = session_row(registry), Source(), str(uuid.uuid4())
    result, status = call(tmp_path, body(record, key), source)
    assert status == 200 and result['scope'] == 'ccc' and source.calls == []
    session_row(registry)  # The session re-reports its question.
    view = next(r for r in read_requests(db, answers_enabled=True, source_current=True)
                if r['adapter'] == 'session-question')
    assert view['status'] == 'closed' and view['needs_answer'] is False
    assert only(registry)['status'] == 'open'
    assert call(tmp_path, body(record, key), source)[1] == 200


def test_session_stale_revision_refused(tmp_path, registry):
    record = session_row(registry)
    assert call(tmp_path, body(record, revision=record['revision'] + 1), Source())[1] == 409


def test_legacy_card_unsupported(tmp_path, registry):
    record = registry.import_cards({'card': dict(title='x')})[0]
    assert call(tmp_path, body(record), Source())[1] == 501


def adapter_outcome(registry, *, code=0, stdout='', mutate=None):
    record, calls = only(registry), []
    expected = WatchTowerCloses._request(record, 'key', REASON)
    if stdout is None:
        stdout = json.dumps(dict(version=1, accepted=True, ref=record['origin_id'], idempotency_key='key',
                                 request=dict(expected, **(mutate or {}))))

    def runner(args, **_):
        calls.append(args)
        return subprocess.CompletedProcess(args, code, stdout, '')
    return WatchTowerCloses('wt', runner=runner).close(record, 'key', REASON), calls, expected


def test_adapter_sends_exact_snapshot_and_validates_receipt(registry):
    outcome, calls, expected = adapter_outcome(registry, stdout=None)
    assert outcome['status'] == 'accepted'
    assert calls[0][:3] == ['wt', 'close-conditional', 'EXAMPLE-1']
    assert json.loads(calls[0][4]) == expected
    assert expected['expected_store_id'] == SCOPE and expected['by'] == 'owner'


def test_adapter_receipt_for_other_payload_is_unknown(registry):
    assert adapter_outcome(registry, stdout=None, mutate={'reason': 'other'})[0]['status'] == 'unknown'


def test_adapter_refusals(registry):
    assert adapter_outcome(registry, code=2, stdout='{"error":"conflict"}')[0] == dict(status='rejected', reason='conflict')
    assert adapter_outcome(registry, code=2, stdout='')[0] == dict(status='rejected', reason='unsupported')
    assert adapter_outcome(registry, code=1, stdout='boom')[0] == dict(status='unknown')
