"""Source acceptance and explicit consumption are separate durable events."""
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from ccc_server.decision_registry import DecisionRegistry, _json
from ccc_server.decision_sessions import SessionQuestions, SessionQuestionConflict, session_payload
from ccc_server.decision_session_answers import SessionAnswerAdapter, consume_payload


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def setup(tmp_path):
    registry = DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')
    sessions = SessionQuestions(registry)
    event = dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                 generation=uid(), title='Choose region', body='A or B?')
    local = sessions.apply(event)
    promoted = sessions.apply(dict(event_id=uid(), operation='promote', session_id=event['session_id'],
                                  request_id=local['request_id'], expected_revision=local['revision'],
                                  reason='Explicit promotion'))
    row = dict(answer_id=uid(), request_id=promoted['request_id'], idempotency_key='answer-key',
               payload=dict(answer=dict(request_id=promoted['request_id'],
                   expected_revision=promoted['revision'], text='A', actor='owner'),
                   source=promoted['content'], created_at='frozen timestamp'))
    return registry, sessions, SessionAnswerAdapter(registry), row


def local_event(row, operation='answer_local'):
    answer, source = row['payload']['answer'], row['payload']['source']
    event = dict(event_id=uid(), operation=operation, request_id=row['request_id'],
                 session_id=source['session_id'], expected_revision=answer['expected_revision'])
    if operation == 'answer_local':
        event.update(text='B', source_message_id='local-message')
    elif operation == 'edit':
        event.update(title='Changed title', body='A, B or C?')
    else:
        event['reason'] = 'Withdrawn locally'
    return event


def consume_event(row):
    source = row['payload']['source']
    return dict(event_id=uid(), session_id=source['session_id'], answer_id=row['answer_id'],
                request_id=row['request_id'], question_id=source['question_id'],
                generation=source['generation'], source_message_id='session-input-1')


def test_acceptance_frozen_replay_lookup_and_session_poll(setup, tmp_path):
    registry, sessions, adapter, row = setup
    assert adapter.lookup(row) == dict(status='not_found', answer_id=row['answer_id'], authoritative=True)
    receipt = adapter.submit(row)
    assert receipt['status'] == 'accepted' and receipt['acceptance'] == 'source_inbox'
    assert receipt['session_consumed'] is False and receipt['work_completed'] is False
    assert registry.list_requests()[0]['status'] == 'answered'
    restored = SessionAnswerAdapter(DecisionRegistry(registry.db_path))
    assert restored.lookup(row) == restored.submit(row) == receipt
    response, status = session_payload(row['payload']['source']['session_id'], state_root=tmp_path)
    assert status == 200 and response['remote_answers_supported'] is True
    assert response['remote_answers'][0]['receipt'] == receipt
    assert response['remote_answers'][0]['answer']['text'] == 'A'
    assert response['remote_answers'][0]['consumption'] is None
    assert adapter.poll(uid()) == []
    assert [e['operation'] for e in sessions.events(row['request_id'])] == [
        'create', 'promote', 'answer_remote_accepted']
    with pytest.raises(SessionQuestionConflict):
        sessions.apply(local_event(row))


@pytest.mark.parametrize('field', ['session_id', 'question_id', 'generation', 'body', 'source_revision', 'visibility'])
def test_exact_source_snapshot_required_and_rejection_sticky(setup, field):
    _, _, adapter, row = setup
    stale = copy.deepcopy(row)
    stale['payload']['source'][field] = uid() if field.endswith('_id') or field == 'generation' else 'changed'
    receipt = adapter.submit(stale)
    assert receipt['status'] == 'rejected'
    assert adapter.lookup(stale) == adapter.submit(stale) == receipt
    with pytest.raises(SessionQuestionConflict):
        adapter.submit(row)


def test_stale_revision_and_request_uuid_reject(setup):
    _, _, adapter, row = setup
    row['payload']['answer']['expected_revision'] += 1
    assert adapter.submit(row)['status'] == 'rejected'
    row = copy.deepcopy(row)
    row.update(answer_id=uid(), idempotency_key='other-key', request_id=uid())
    row['payload']['answer']['request_id'] = row['request_id']
    assert adapter.submit(row)['status'] == 'rejected'


@pytest.mark.parametrize('operation', ['answer_local', 'withdraw', 'edit'])
@pytest.mark.parametrize('provisional', [False, True])
def test_concurrent_local_change_and_global_acceptance_has_single_winner(setup, operation, provisional):
    registry, sessions, adapter, row = setup
    if provisional:
        with registry._transaction() as db:
            db.execute('CREATE TABLE answers (answer_id TEXT, request_id TEXT, idempotency_key TEXT, payload TEXT, state TEXT)')
            db.execute('INSERT INTO answers VALUES (?,?,?,?,?)',
                       (row['answer_id'], row['request_id'], row['idempotency_key'], _json(row['payload']), 'sending'))
            db.execute("UPDATE requests SET answer_state='recorded', delivery_state='sending'")
    def local():
        try:
            return sessions.apply(local_event(row, operation))
        except SessionQuestionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(local)
        second = pool.submit(adapter.submit, row)
        local_result, receipt = first.result(), second.result()
    assert (local_result is None) == (receipt['status'] == 'accepted')
    assert len(adapter.poll(row['payload']['source']['session_id'])) == (local_result is None)
    assert adapter.lookup(row) == receipt


@pytest.mark.parametrize('operation', ['answer_local', 'withdraw', 'edit'])
def test_local_change_before_source_always_rejects(setup, operation):
    _, sessions, adapter, row = setup
    sessions.apply(local_event(row, operation))
    assert adapter.submit(row)['status'] == 'rejected'


def test_ordinary_local_question_is_never_remotely_answerable(setup):
    registry, sessions, adapter, row = setup
    source = row['payload']['source']
    request = sessions.apply(dict(event_id=uid(), operation='create', session_id=uid(), question_id=uid(),
                                 generation=uid(), title=source['title'], body=source['body']))
    row.update(request_id=request['request_id'])
    row['payload']['source'] = request['content']
    row['payload']['answer'].update(request_id=request['request_id'], expected_revision=request['revision'])
    assert adapter.submit(row)['status'] == 'rejected'
    assert [r for r in registry.list_requests() if r['request_id'] == request['request_id']][0]['status'] == 'open'


def test_duplicate_remote_submits_and_idempotency_conflicts(setup):
    registry, _, adapter, row = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: adapter.submit(copy.deepcopy(row)), range(16)))
    assert all(receipt == receipts[0] for receipt in receipts)
    for mutation in ('text', 'answer_id', 'idempotency_key', 'created_at'):
        other = copy.deepcopy(row)
        if mutation == 'text':
            other['payload']['answer']['text'] = 'B'
        elif mutation == 'created_at':
            other['payload']['created_at'] = 'different frozen payload'
        else:
            other[mutation] = uid()
        with pytest.raises(SessionQuestionConflict):
            adapter.submit(other)
        with pytest.raises(SessionQuestionConflict):
            adapter.lookup(other)
    other = copy.deepcopy(row)
    other.update(answer_id=uid(), idempotency_key='another')
    assert adapter.submit(other)['status'] == 'rejected'
    assert registry.list_requests()[0]['delivery_state'] == 'accepted'


def test_pending_recorded_requires_exact_durable_outbox_answer(setup):
    registry, _, adapter, row = setup
    with registry._transaction() as db:
        db.execute("UPDATE requests SET answer_state='recorded', delivery_state='pending'")
    assert adapter.submit(row)['status'] == 'rejected'


def test_consume_is_explicit_scoped_durable_idempotent_and_never_completion(setup, tmp_path):
    registry, sessions, adapter, row = setup
    event = consume_event(row)
    with pytest.raises(SessionQuestionConflict):
        adapter.consume(event)
    receipt = adapter.submit(row)
    for key in ('session_id', 'question_id', 'generation', 'request_id', 'answer_id'):
        with pytest.raises(SessionQuestionConflict):
            adapter.consume(dict(event, **{key: uid()}))
    result, status = consume_payload(event, state_root=tmp_path)
    assert status == 200 and result['consumption']['session_consumed'] is True
    assert result['work_completed'] is False
    restored = SessionAnswerAdapter(DecisionRegistry(registry.db_path))
    assert restored.consume(event) == result['consumption']
    assert restored.consume(dict(event, event_id=uid(), source_message_id='another-message')) == result['consumption']
    assert restored.lookup(row) == receipt  # acceptance receipt remains frozen
    assert restored.poll(event['session_id'])[0]['consumption'] == result['consumption']
    assert consume_payload(dict(event, source_message_id='changed'), state_root=tmp_path)[1] == 409
    assert consume_payload({}, state_root=tmp_path)[1] == 400
    assert [e['operation'] for e in sessions.events(row['request_id'])].count('answer_remote_consumed') == 2


def test_concurrent_consumption_is_sticky(setup):
    _, _, adapter, row = setup
    adapter.submit(row)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: adapter.consume(dict(consume_event(row), event_id=uid())), range(16)))
    assert all(result == results[0] for result in results)


def test_notification_uncertainty_never_changes_acceptance_or_consumes(setup):
    _, _, adapter, row = setup
    session_id = row['payload']['source']['session_id']
    with pytest.raises(SessionQuestionConflict):
        adapter.notify(row['answer_id'], session_id, lambda ids: True)
    receipt = adapter.submit(row)
    called = []
    def wake(ids):
        called.append(ids)
        return True
    assert adapter.notify(row['answer_id'], session_id, wake)['notification'] == 'notified'
    assert called == [dict(answer_id=row['answer_id'], session_id=session_id, request_id=row['request_id'])]
    def failed(ids):
        raise TimeoutError('wake response lost')
    assert adapter.notify(row['answer_id'], session_id, failed)['notification'] == 'notified'
    assert adapter.lookup(row) == receipt
    assert adapter.poll(session_id)[0]['consumption'] is None


def test_actual_outbox_dispatch_and_lost_response_reconciliation(setup):
    from ccc_server.decision_delivery import AnswerOutbox
    registry, _, adapter, row = setup
    outbox = AnswerOutbox(registry)
    # Parent owns enabling record() for this source. Seed its documented
    # frozen row to exercise the existing claim/finish/reconcile boundary.
    with registry._transaction() as db:
        db.execute('INSERT INTO answers VALUES (?,?,?,?,?,NULL,NULL)',
                   (row['answer_id'], row['request_id'], row['idempotency_key'],
                    _json(row['payload']), 'pending'))
        db.execute("UPDATE requests SET answer_state='recorded', delivery_state='pending'")
    class LostResponse:
        conditional_idempotent = True
        def submit(self, frozen):
            adapter.submit(frozen)
            raise TimeoutError('after commit')
        def lookup(self, frozen):
            return adapter.lookup(frozen)
    interrupted = outbox.dispatch(row['answer_id'], LostResponse())
    assert interrupted['state'] == 'unknown'
    assert registry.list_requests()[0]['status'] == 'answered'
    accepted = outbox.reconcile(row['answer_id'], adapter)
    assert accepted['state'] == 'accepted'
    assert accepted['receipt']['acceptance'] == 'source_inbox'
    assert len(adapter.poll(row['payload']['source']['session_id'])) == 1


def test_source_requires_explicit_promotion_audit_not_global_flag_alone(setup):
    registry, _, adapter, row = setup
    with registry._transaction() as db:
        db.execute("DELETE FROM session_question_events WHERE operation='promote'")
    assert adapter.submit(row)['status'] == 'rejected'


def test_old_generation_rejection_survives_new_episode(setup):
    _, sessions, adapter, row = setup
    sessions.apply(local_event(row, 'withdraw'))
    source = row['payload']['source']
    newer = sessions.apply(dict(event_id=uid(), operation='create', session_id=source['session_id'],
                               question_id=source['question_id'], generation=uid(),
                               title=source['title'], body=source['body']))
    assert newer['request_id'] != row['request_id']
    assert adapter.submit(row)['status'] == 'rejected'


@pytest.mark.parametrize('operation', ['answer_local', 'withdraw', 'edit'])
def test_recorded_outbox_intent_does_not_lock_out_local_changes(setup, operation):
    registry, sessions, adapter, row = setup
    with registry._transaction() as db:
        db.execute("UPDATE requests SET answer_state='recorded', delivery_state='sending'")
    changed = sessions.apply(local_event(row, operation))
    assert changed['revision'] == row['payload']['answer']['expected_revision'] + 1
    assert adapter.submit(row)['status'] == 'rejected'
    if operation == 'answer_local':
        assert registry.list_requests()[0]['delivery_state'] == 'local'


def test_accepted_receipt_stays_frozen_after_next_question_episode(setup):
    _, sessions, adapter, row = setup
    receipt = adapter.submit(row)
    source = row['payload']['source']
    newer = sessions.apply(dict(event_id=uid(), operation='create', session_id=source['session_id'],
                               question_id=source['question_id'], generation=uid(),
                               title=source['title'], body=source['body']))
    assert newer['request_id'] != row['request_id']
    assert adapter.lookup(row) == adapter.submit(row) == receipt
    assert adapter.consume(consume_event(row))['session_consumed'] is True


def test_preferred_adapter_optional_advisory_notify_and_exact_existing_payload(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    row['payload'].pop('created_at')
    calls = []
    def wake(ids):
        calls.append(ids)
        raise TimeoutError('after wake')
    adapter = SessionAnswers(registry, notify=wake)
    receipt = adapter.submit(row)
    assert receipt['status'] == 'accepted'
    assert adapter.last_notification['notification'] == 'uncertain'
    assert calls == [dict(session_id=receipt['session_id'], request_id=row['request_id'], answer_id=row['answer_id'])]
    assert adapter.lookup(row) == adapter.submit(row) == receipt
    assert adapter.poll(receipt['session_id'])[0]['consumption'] is None
    assert len(calls) == 1  # replay cannot repeat the advisory side effect
    with pytest.raises(ValueError):
        SessionAnswers(registry, notify='not callable')


def test_consume_operation_through_existing_event_facade(setup, tmp_path):
    from ccc_server.decision_sessions import event_payload
    _, sessions, adapter, row = setup
    event = dict(consume_event(row), operation='consume')
    assert event_payload(event, state_root=tmp_path)[1] == 409
    adapter.submit(row)
    response, status = event_payload(event, state_root=tmp_path)
    assert status == 200 and response['consumption']['session_consumed'] is True
    assert response['input_sent'] is False and response['work_completed'] is False
    assert sessions.apply(event) == response['consumption']
    assert event_payload(event, state_root=tmp_path) == (response, 200)
    assert event_payload(dict(event, session_id=uid()), state_root=tmp_path)[1] == 409
    assert event_payload(dict(event, event_id=uid(), answer_id='invalid'), state_root=tmp_path)[1] == 400
    assert event_payload(dict(event, event_id=uid(), expected_revision=2), state_root=tmp_path)[1] == 400
    audit = sessions.events(row['request_id'])[-1]
    assert audit['operation'] == 'answer_remote_consumed'
    assert audit['payload'] == event


def test_notification_infrastructure_failure_cannot_hide_acceptance(setup, monkeypatch):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    adapter = SessionAnswers(registry, notify=lambda ids: True)
    def unavailable(*args):
        raise OSError('advisory infrastructure unavailable')
    monkeypatch.setattr(adapter, 'notify', unavailable)
    receipt = adapter.submit(row)
    assert receipt['status'] == 'accepted'
    assert adapter.last_notification['notification'] == 'uncertain'
    assert adapter.lookup(row) == receipt


def test_notification_journal_concurrent_submit_and_explicit_notify_exactly_once(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    calls = []
    adapter = SessionAnswers(registry, notify=lambda ids: calls.append(ids) or True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: adapter.submit(copy.deepcopy(row)), range(24)))
    assert len(calls) == 1
    assert set(calls[0]) == {'session_id', 'request_id', 'answer_id'}
    assert all(r == receipts[0] for r in receipts)
    assert receipts[0]['consumed'] is False
    assert receipts[0]['routing']['state'] == 'source_inbox'
    assert receipts[0]['notification']['state'] == 'pending'  # acceptance-time snapshot
    state = adapter.notification_state(row['answer_id'], calls[0]['session_id'])
    assert state['journal_state'] == state['notification'] == 'notified'
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids))
    assert restored.submit(row) == receipts[0]
    assert restored.notify(row['answer_id'], calls[0]['session_id'], lambda ids: calls.append(ids)) == state
    assert len(calls) == 1
    assert restored.poll(calls[0]['session_id'])[0]['notification'] == state


def test_pending_after_commit_crash_replay_is_inert_and_explicit_claim_recovers(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, adapter, row = setup
    receipt = adapter.submit(row)  # source committed, process crashed before claiming wake
    calls = []
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids) or True)
    assert restored.submit(row) == receipt
    assert calls == []
    assert restored.last_notification['journal_state'] == 'pending'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: restored.notify(row['answer_id'], receipt['session_id'],
                                                          lambda ids: calls.append(ids) or True), range(16)))
    assert len(calls) == 1
    assert all(r['journal_state'] in ('uncertain', 'notified') for r in results)
    assert restored.notification_state(row['answer_id'], receipt['session_id'])['notification'] == 'notified'


def test_claimed_crash_and_lost_transport_response_never_resend(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, adapter, row = setup
    receipt = adapter.submit(row)
    ids, claimed = adapter._claim_notification(row['answer_id'], receipt['session_id'])
    assert claimed['journal_state'] == 'uncertain' and claimed['notification'] == 'uncertain'
    assert ids['request_id'] == row['request_id']
    calls = []
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids))
    assert restored.submit(row) == receipt
    assert restored.notify(row['answer_id'], receipt['session_id'], lambda ids: calls.append(ids)) == claimed
    assert calls == []
    assert restored.consume(consume_event(row))['consumed'] is True
    assert restored.lookup(row) == receipt


def test_side_effect_then_crash_before_journal_finish_never_repeats(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    calls = []
    def crashed(ids):
        calls.append(ids)
        raise SystemExit('process died after side effect')
    adapter = SessionAnswers(registry, notify=crashed)
    with pytest.raises(SystemExit):
        adapter.submit(row)
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids))
    assert restored.submit(row)['status'] == 'accepted'
    assert restored.last_notification['journal_state'] == 'uncertain'
    assert restored.notify(row['answer_id'], calls[0]['session_id'], lambda ids: calls.append(ids))['notification'] == 'uncertain'
    assert len(calls) == 1


def test_consumed_before_notify_suppresses_transport_and_wrong_session_cannot_claim(setup):
    _, _, adapter, row = setup
    receipt = adapter.submit(row)
    with pytest.raises(SessionQuestionConflict):
        adapter.notify(row['answer_id'], uid(), lambda ids: True)
    adapter.consume(consume_event(row))
    calls = []
    state = adapter.notify(row['answer_id'], receipt['session_id'], lambda ids: calls.append(ids))
    assert state['notification'] == state['journal_state'] == 'suppressed'
    assert state['session_consumed'] is True
    assert calls == []


def test_pre_journal_receipt_migration_defaults_uncertain_and_never_reroutes(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, adapter, row = setup
    receipt = adapter.submit(row)
    with registry._transaction() as db:
        db.execute('DELETE FROM session_answer_notifications')
    calls = []
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids))
    assert restored.submit(row) == receipt
    state = restored.notify(row['answer_id'], receipt['session_id'], lambda ids: calls.append(ids))
    assert state['notification'] == 'uncertain'
    assert calls == []


def test_consumption_racing_wake_finish_keeps_live_journal_acknowledgment(setup):
    import threading
    _, _, adapter, row = setup
    receipt = adapter.submit(row)
    started, resume = threading.Event(), threading.Event()
    def wake(ids):
        started.set()
        assert resume.wait(5)
        return True
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(adapter.notify, row['answer_id'], receipt['session_id'], wake)
        assert started.wait(5)
        adapter.consume(consume_event(row))
        resume.set()
        result = future.result()
    assert result['notification'] == 'notified'
    assert result['consumed'] is result['session_consumed'] is True
    assert adapter.notification_state(row['answer_id'], receipt['session_id']) == result
    assert adapter.lookup(row)['consumed'] is False  # immutable acceptance-time receipt


def test_notification_is_durably_uncertain_before_callback_and_consumption_is_display_only(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    calls = []
    def wake(ids):
        state = SessionAnswers(DecisionRegistry(registry.db_path)).notification_state(
            ids['answer_id'], ids['session_id'])
        assert state['journal_state'] == state['notification'] == 'uncertain'
        assert state['attempt_id'] is not None
        calls.append(ids)
        return False
    adapter = SessionAnswers(registry, notify=wake)
    receipt = adapter.submit(row)
    before = adapter.notification_state(row['answer_id'], receipt['session_id'])
    assert before['journal_state'] == 'uncertain'
    adapter.consume(consume_event(row))
    after = adapter.notification_state(row['answer_id'], receipt['session_id'])
    assert after['consumed'] is True
    assert after['journal_state'] == before['journal_state']
    assert after['notification'] == before['notification']
    assert after['attempt_id'] == before['attempt_id']
    assert adapter.submit(row) == adapter.lookup(row) == receipt
    assert len(calls) == 1


def test_legacy_claim_journal_normalizes_uncertainty_without_resend(setup):
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, adapter, row = setup
    receipt = adapter.submit(row)
    ids, state = adapter._claim_notification(row['answer_id'], receipt['session_id'])
    state.update(notification='unknown', journal_state='claimed')
    with registry._transaction() as db:
        db.execute("UPDATE session_answer_notifications SET state='claimed',result=? WHERE answer_id=?",
                   (_json(state), row['answer_id']))
    calls = []
    restored = SessionAnswers(DecisionRegistry(registry.db_path), notify=lambda ids: calls.append(ids))
    restored.submit(row)
    assert restored.last_notification['notification'] == 'uncertain'
    assert restored.last_notification['attempt_id'] == state['attempt_id']
    assert calls == []


def _database_snapshot(database):
    import sqlite3
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        rows = tuple(db.iterdump())
    return database.read_bytes(), rows


def test_missing_answer_tables_constructor_and_all_read_paths_are_strictly_read_only(setup, tmp_path, monkeypatch):
    import sqlite3
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, _, row = setup
    # Preserve the existing non-WAL format too: constructing a writer registry
    # for GET would change the database header even without inserting rows.
    with sqlite3.connect(registry.db_path) as db:
        db.execute('PRAGMA journal_mode=DELETE')
    before = _database_snapshot(registry.db_path)
    actual_connect = sqlite3.connect
    connections = []
    def read_only_connect(database, *args, **kwargs):
        assert isinstance(database, str) and database.endswith('?mode=ro')
        assert kwargs.get('uri') is True
        connections.append(database)
        return actual_connect(database, *args, **kwargs)
    def forbidden_writer(*args, **kwargs):
        raise AssertionError('Read path initialized a writer')
    monkeypatch.setattr(sqlite3, 'connect', read_only_connect)
    monkeypatch.setattr(registry, '_transaction', forbidden_writer)
    monkeypatch.setattr(DecisionRegistry, '__init__', forbidden_writer)
    adapter = SessionAnswers(registry, notify=lambda ids: pytest.fail('read attempted wake'))
    assert adapter.lookup(row)['status'] == 'not_found'
    assert adapter.poll(row['payload']['source']['session_id']) == []
    with pytest.raises(SessionQuestionConflict):
        adapter.notification_state(row['answer_id'], row['payload']['source']['session_id'])
    response, status = session_payload(row['payload']['source']['session_id'], state_root=tmp_path)
    assert status == 200 and response['remote_answers'] == []
    assert response['requests'][0]['request_id'] == row['request_id']
    assert len(connections) >= 5
    monkeypatch.setattr(sqlite3, 'connect', actual_connect)
    assert _database_snapshot(registry.db_path) == before


@pytest.mark.parametrize('legacy', ['missing_table', 'missing_row', 'claimed', 'unknown'])
def test_older_journals_have_virtual_uncertainty_without_read_migration(setup, tmp_path, monkeypatch, legacy):
    import sqlite3
    from ccc_server.decision_session_answers import SessionAnswers
    registry, _, adapter, row = setup
    receipt = adapter.submit(row)
    if legacy in ('claimed', 'unknown'):
        adapter._claim_notification(row['answer_id'], receipt['session_id'])
    with registry._transaction() as db:
        if legacy == 'missing_table':
            db.execute('DROP TABLE session_answer_notifications')
        elif legacy == 'missing_row':
            db.execute('DELETE FROM session_answer_notifications')
        else:
            old = json.loads(db.execute('SELECT result FROM session_answer_notifications').fetchone()[0])
            old.update(notification=legacy, journal_state=legacy)
            db.execute('UPDATE session_answer_notifications SET state=?,result=?', (legacy, _json(old)))
    with sqlite3.connect(registry.db_path) as db:
        db.execute('PRAGMA journal_mode=DELETE')
    before = _database_snapshot(registry.db_path)
    def forbidden_writer(*args, **kwargs):
        raise AssertionError('Read path attempted migration')
    monkeypatch.setattr(registry, '_transaction', forbidden_writer)
    monkeypatch.setattr(DecisionRegistry, '__init__', forbidden_writer)
    restored = SessionAnswers(registry, notify=lambda ids: pytest.fail('read attempted wake'))
    assert restored.lookup(row) == receipt
    notification = restored.notification_state(row['answer_id'], receipt['session_id'])
    assert notification['notification'] == notification['journal_state'] == 'uncertain'
    assert notification['virtual'] is True
    assert restored.poll(receipt['session_id'])[0]['notification'] == notification
    response, status = session_payload(receipt['session_id'], state_root=tmp_path)
    assert status == 200 and response['remote_answers'][0]['notification'] == notification
    assert _database_snapshot(registry.db_path) == before


def test_missing_database_read_paths_create_no_files(tmp_path):
    from types import SimpleNamespace
    from ccc_server.decision_session_answers import SessionAnswers
    database = tmp_path / 'absent' / 'requests.sqlite3'
    adapter = SessionAnswers(SimpleNamespace(db_path=database, timeout=1))
    session = uid()
    row = dict(answer_id=uid(), request_id=uid(), idempotency_key='key', payload={})
    row['payload'] = dict(answer=dict(request_id=row['request_id'], expected_revision=1, text='A', actor='owner'),
                          source=dict(session_id=session, question_id=uid(), generation=uid(), body='A or B?'))
    assert adapter.lookup(row)['status'] == 'not_found'
    assert adapter.poll(session) == []
    with pytest.raises(SessionQuestionConflict):
        adapter.notification_state(row['answer_id'], session)
    assert not database.parent.exists()
