"""Answer delivery contracts against isolated databases and explicit adapters."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from ccc_server.decision_delivery import AnswerConflict, AnswerOutbox
from ccc_server.decision_registry import DecisionRegistry


@pytest.fixture
def setup(tmp_path):
    registry = DecisionRegistry(tmp_path / 'requests.sqlite3')
    source = dict(adapter='watchtower-block', origin_id='EXAMPLE-1', generation='episode-1',
                  source_revision='revision-1', kind='question', wait_kind='owner',
                  body='Choose region', title='Deployment', visibility='global')
    request = registry.upsert_requests([source])[0]
    return registry, AnswerOutbox(registry), request, source


def save(outbox, request, **overrides):
    values = dict(expected_revision=request['revision'], text='Region A',
                  idempotency_key='request-key')
    values.update(overrides)
    return outbox.record(request['request_id'], **values)


def test_record_is_atomic_idempotent_and_source_observations_preserve_it(setup):
    registry, outbox, request, source = setup
    answer = save(outbox, request)
    assert answer['state'] == 'pending'
    assert save(outbox, request) == answer
    assert AnswerOutbox(DecisionRegistry(registry.db_path)).get(answer['answer_id']) == answer
    observed = registry.upsert_requests([source])[0]
    assert observed['answer_state'] == 'recorded'
    assert observed['delivery_state'] == 'pending'
    assert observed['revision'] == request['revision']
    with pytest.raises(AnswerConflict):
        save(outbox, request, text='Region B')
    with pytest.raises(AnswerConflict):
        save(outbox, request, idempotency_key='another-key')


def test_stale_or_unsupported_answers_are_rejected_before_recording(setup):
    registry, outbox, request, source = setup
    registry.upsert_requests([dict(source, body='Choose another region')])
    with pytest.raises(AnswerConflict):
        save(outbox, request)
    for changes in ({'visibility': 'local'}, {'adapter': 'legacy-card'},
                    {'wait_kind': 'external'}, {'kind': 'review'}):
        row = registry.upsert_requests([dict(source, origin_id=str(changes), **changes)])[0]
        with pytest.raises(AnswerConflict):
            save(outbox, row)
    assert registry.list_requests()[0]['answer_state'] == 'none'


class Adapter:
    conditional_idempotent = True

    def __init__(self):
        self.calls = 0
        self.receipts = {}

    def submit(self, row):
        self.calls += 1
        receipt = dict(status='accepted', answer_id=row['answer_id'], work_completed=False)
        self.receipts[row['answer_id']] = receipt
        return receipt

    def lookup(self, row):
        return self.receipts.get(row['answer_id'], dict(status='not_found', authoritative=True))


def test_concurrent_duplicate_submits_and_dispatch_have_one_owner(setup):
    registry, outbox, request, source = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: save(outbox, request), range(16)))
    assert len({r['answer_id'] for r in records}) == 1
    answer_id = records[0]['answer_id']
    adapter = Adapter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: outbox.dispatch(answer_id, adapter), range(16)))
    assert adapter.calls == 1
    assert outbox.get(answer_id)['state'] == 'accepted'
    observed = registry.upsert_requests([source])[0]
    assert observed['delivery_state'] == 'accepted'
    assert observed['status'] == 'open'  # Answer acceptance never completes work.


def test_lost_receipt_reconciles_without_repeating_side_effect(setup):
    registry, outbox, request, _ = setup
    answer_id = save(outbox, request)['answer_id']

    class LostResponse(Adapter):
        def submit(self, row):
            super().submit(row)
            raise TimeoutError('Response lost after source accepted')

    adapter = LostResponse()
    assert outbox.dispatch(answer_id, adapter)['state'] == 'unknown'
    assert outbox.dispatch(answer_id, adapter)['state'] == 'unknown'
    assert adapter.calls == 1
    assert outbox.reconcile(answer_id, adapter)['state'] == 'accepted'
    assert adapter.calls == 1


def test_interrupted_send_requires_receipt_lookup_and_safe_adapter(setup):
    _, outbox, request, _ = setup
    answer_id = save(outbox, request)['answer_id']
    with pytest.raises(ValueError):
        outbox.dispatch(answer_id, object())
    assert outbox.get(answer_id)['state'] == 'pending'
    outbox._claim(answer_id)  # Simulate process death before submit.
    adapter = Adapter()
    assert outbox.reconcile(answer_id, adapter)['state'] == 'pending'
    assert adapter.calls == 0
    assert outbox.dispatch(answer_id, adapter)['state'] == 'accepted'


def test_wrong_receipt_or_offline_source_never_claims_delivery(setup):
    _, outbox, request, _ = setup
    answer_id = save(outbox, request)['answer_id']

    class WrongReceipt(Adapter):
        def submit(self, row):
            return dict(status='accepted', answer_id='another-answer')

        def lookup(self, row):
            raise ConnectionError('Offline')

    adapter = WrongReceipt()
    assert outbox.dispatch(answer_id, adapter)['state'] == 'unknown'
    assert outbox.reconcile(answer_id, adapter)['state'] == 'unknown'


def test_stale_source_rejection_is_not_acceptance(setup):
    registry, outbox, request, source = setup
    answer_id = save(outbox, request)['answer_id']

    class Stale(Adapter):
        def submit(self, row):
            return dict(status='rejected', answer_id=row['answer_id'], reason='source_changed')

    result = outbox.dispatch(answer_id, Stale())
    assert result['state'] == 'rejected'
    assert registry.list_requests()[0]['answer_state'] == 'recorded'
    assert registry.list_requests()[0]['delivery_state'] == 'rejected'
    changed = registry.upsert_requests([dict(source, body='Updated question')])[0]
    replacement = save(outbox, changed, text='Updated answer', idempotency_key='new-key')
    assert replacement['answer_id'] != answer_id
    assert outbox.get(answer_id)['state'] == 'rejected'
