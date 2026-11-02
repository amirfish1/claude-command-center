"""Global coverage keeps local questions and unproven aliases out of counts."""
import uuid

from ccc_server.decision_delivery import AnswerOutbox
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_requests import read_requests, requests_payload
from ccc_server.decision_sessions import SessionQuestions
from ccc_server.decision_sources import apply_owner_export
from tests.test_decision_sources import receipt, row


def setup(tmp_path):
    registry = DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')
    apply_owner_export(receipt([row(body='  Choose region?\n')]), registry=registry, cards={})
    return registry


def test_read_only_snapshot_exact_body_and_ids(tmp_path):
    registry = setup(tmp_path)
    before = registry.db_path.read_bytes()
    request = read_requests(registry.db_path)[0]
    assert request['request_id'] == registry.list_requests()[0]['request_id']
    assert request['body'] == '  Choose region?\n'
    assert request['needs_answer'] and not request['can_answer']
    assert request['origin_id'] == 'EXAMPLE-1'
    assert registry.db_path.read_bytes() == before


def test_only_bound_sources_and_explicit_promotions_are_global(tmp_path):
    registry = setup(tmp_path)
    source_id = read_requests(registry.db_path)[0]['request_id']
    registry.import_cards({'old': {'title': '[Q12:region]', 'decision_number': 12}})
    registry.upsert_requests([dict(adapter='watchtower-block', origin_id='EXAMPLE-2', generation='old',
                                  kind='question', visibility='global', body='Display cache only')])
    session = SessionQuestions(registry)
    event = dict(event_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), operation='create',
                 question_id=str(uuid.uuid4()), generation=str(uuid.uuid4()),
                 title='Local question', body='Region?', aliases=['[Q12:region]'])
    local = session.apply(event)
    assert len(read_requests(registry.db_path)) == 1
    promoted = session.apply(dict(event_id=str(uuid.uuid4()), session_id=event['session_id'],
        operation='promote', request_id=local['request_id'], expected_revision=local['revision'], reason='Owner away'))
    rows = read_requests(registry.db_path)
    assert len(rows) == 2 and {r['request_id'] for r in rows} == {local['request_id'], source_id}
    assert '[Q12:region]' in next(r for r in rows if r['request_id'] == promoted['request_id'])['aliases']
    session.apply(dict(event_id=str(uuid.uuid4()), session_id=event['session_id'], operation='answer_local',
        request_id=promoted['request_id'], expected_revision=promoted['revision'], text='A', source_message_id='message-1'))
    assert sum(r['needs_answer'] for r in read_requests(registry.db_path)) == 1


def test_recorded_answer_is_delivery_not_another_owner_question(tmp_path):
    registry = setup(tmp_path)
    request = read_requests(registry.db_path)[0]
    AnswerOutbox(registry).record(request['request_id'], expected_revision=request['revision'], text='A', idempotency_key='answer-1')
    pending = read_requests(registry.db_path)[0]
    assert not pending['needs_answer'] and pending['delivery_state'] == 'pending'
    assert pending['delivery_owner'] == 'ccc-outbox'


def test_answer_affordance_requires_exact_opt_in_and_current_source(tmp_path, monkeypatch):
    registry = setup(tmp_path)
    current = dict(ok=True, source=dict(authoritative=True, complete=True))
    for value in ('', 'true', '0', '01'):
        monkeypatch.setenv('CCC_DECISION_ANSWERS_ENABLED', value)
        result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: current)
        assert not result['answers_enabled'] and not result['requests'][0]['can_answer']
    monkeypatch.setenv('CCC_DECISION_ANSWERS_ENABLED', '1')
    result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: current)
    assert result['answers_enabled'] and result['requests'][0]['can_answer']
    assert result['requests'][0]['answer'] is None
    unavailable = dict(ok=False, source=dict(authoritative=False, complete=False))
    result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: unavailable)
    assert result['answers_enabled'] and not result['requests'][0]['can_answer']
    assert 'Check source coverage' in result['requests'][0]['answer_unavailable_reason']
    with registry._transaction() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='answers'").fetchone()


def test_routine_owner_is_fenced_and_explicit_session_promotion_supports_replies(tmp_path):
    registry = setup(tmp_path)
    source = read_requests(registry.db_path)[0]
    with registry._transaction() as db:
        db.execute('INSERT INTO metadata VALUES (?,?)', ('delivery_owner:' + source['request_id'], 'routine'))
    sessions = SessionQuestions(registry)
    session_id = str(uuid.uuid4())
    local = sessions.apply(dict(event_id=str(uuid.uuid4()), session_id=session_id, operation='create',
        question_id=str(uuid.uuid4()), generation=str(uuid.uuid4()), title='Choose a region', body='Region?'))
    sessions.apply(dict(event_id=str(uuid.uuid4()), session_id=session_id, operation='promote',
        request_id=local['request_id'], expected_revision=local['revision'], reason='Awaiting owner'))
    rows = read_requests(registry.db_path, answers_enabled=True, source_current=True)
    assert len(rows) == 2
    assert next(r for r in rows if r['adapter'] == 'session-question')['can_answer']
    assert not next(r for r in rows if r['adapter'] == 'watchtower-block')['can_answer']
    assert 'reporting routine' in next(r for r in rows if r['adapter'] == 'watchtower-block')['answer_unavailable_reason']
    assert next(r for r in rows if r['adapter'] == 'session-question')['answer_unavailable_reason'] == ''


def test_saved_answer_is_visible_after_reload_without_delivery_or_writes(tmp_path):
    registry = setup(tmp_path)
    request = read_requests(registry.db_path)[0]
    outbox = AnswerOutbox(registry)
    answer = outbox.record(request['request_id'], expected_revision=request['revision'],
                           text='  Region A\nExact answer.  ', idempotency_key='saved-for-reload')
    before = registry.db_path.read_bytes()
    for enabled, current in ((False, True), (True, False), (True, True)):
        row = read_requests(registry.db_path, answers_enabled=enabled, source_current=current)[0]
        assert not row['can_answer']
        assert row['answer'] == dict(answer_id=answer['answer_id'], state='pending',
            text='  Region A\nExact answer.  ', expected_revision=request['revision'])
    assert registry.db_path.read_bytes() == before
    assert outbox.get(answer['answer_id'])['state'] == 'pending'


def test_failed_refresh_retains_questions_but_never_claims_zero(tmp_path):
    registry = setup(tmp_path)
    def unavailable(**kwargs):
        return dict(ok=False, error='Offline', source=dict(status='unavailable', complete=False, authoritative=False))
    result = requests_payload(state_root=tmp_path, source_loader=unavailable)
    assert result['ok'] and len(result['requests']) == 1
    assert result['needs_answer_count'] is None and not result['source_current']
    assert result['error'] == 'Offline'
    assert read_requests(registry.db_path)[0]['status'] == 'open'


def test_complete_empty_withdraws_without_claiming_answer_delivery(tmp_path):
    registry = setup(tmp_path)
    source = apply_owner_export(receipt([], revision=2), registry=registry, cards={})
    result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: source)
    assert result['needs_answer_count'] == 0 and result['source_current']
    assert result['requests'][0]['status'] == 'withdrawn'
    assert result['requests'][0]['answer_state'] == 'none'


def test_incomplete_question_is_visible_as_a_coverage_issue_without_inventing_identity(tmp_path):
    registry = setup(tmp_path)
    before = registry.list_requests()
    source = apply_owner_export(receipt([row('EXAMPLE-2', generation='')], revision=2), registry=registry, cards={})
    result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: source)
    assert result['needs_answer_count'] is None and not result['source_current']
    assert result['coverage_issues'][0]['origin_id'] == 'EXAMPLE-2'
    assert 'request_id' not in result['coverage_issues'][0]
    assert registry.list_requests() == before


def test_missing_or_corrupt_registry_is_not_created_or_reported_empty(tmp_path):
    path = tmp_path / 'requests.sqlite3'
    assert read_requests(path) == [] and not path.exists()
    path = tmp_path / 'decision-inbox' / 'requests.sqlite3'
    path.parent.mkdir()
    path.write_text('broken')
    source = dict(ok=False, source=dict(status='unavailable', complete=False))
    result = requests_payload(state_root=tmp_path, source_loader=lambda **kw: source)
    assert not result['ok'] and result['requests'] is None and result['needs_answer_count'] is None


def test_http_read_model_uses_actual_handler_and_preserves_unavailable_semantics(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    import server
    from ccc_server import decision_inbox, decision_sources
    registry = setup(tmp_path)
    monkeypatch.setattr(decision_inbox, '_state_root', lambda: tmp_path)
    monkeypatch.setattr(decision_sources, 'owner_source_payload', lambda **kw:
                        dict(ok=False, source=dict(status='unavailable', complete=False)))

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
    before = registry.db_path.read_bytes()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{listener.server_port}/api/decision-inbox/requests') as response:
            result = json.load(response)
            assert response.status == 200 and result['ok']
        assert len(result['requests']) == 1 and result['needs_answer_count'] is None
        assert registry.db_path.read_bytes() == before
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=5)
