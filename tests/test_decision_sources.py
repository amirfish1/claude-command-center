"""Authoritative source admission and withdrawal, with generic isolated data."""
import copy
import hashlib
import json
import uuid

import pytest

from ccc_server.decision_coverage import coverage_payload
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_sources import REQUIRED_CAPABILITIES, apply_owner_export, owner_source_payload

SCOPE = '00000000-0000-4000-8000-000000000001'


def row(ref='EXAMPLE-1', generation='episode-1', body='Choose region', **extra):
    wait = extra.pop('expected_wait', None)
    digest = hashlib.sha256(json.dumps([generation, body, 'owner', wait], sort_keys=True).encode()).hexdigest()
    return dict(origin_id=ref, generation=generation, body=body, wait_kind='owner',
                title='Deployment', status='in_progress', duplicate_of='', block_kind='input',
                expected_blocked_at=generation, expected_block_question=body,
                expected_wait_kind='owner', expected_wait=wait, expected_store_id=SCOPE,
                source_revision=digest, **extra)


def receipt(rows, revision=1):
    return dict(schema_version=1, adapter='watchtower-block', authoritative=True, complete=True,
                scope={'backend': 'file', 'store_id': SCOPE},
                store_version={'revision': revision, 'snapshot_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()},
                capabilities=sorted(REQUIRED_CAPABILITIES), items=rows)


@pytest.fixture
def registry(tmp_path):
    return DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')


def apply(registry, rows, revision=1):
    return apply_owner_export(receipt(rows, revision), registry=registry, cards={})


def test_parallel_refreshes_each_export_and_apply_in_order(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import time
    from ccc_server import decision_sources as sources
    calls, active = [], []
    original = sources.apply_owner_export
    def runner(*args, **kwargs):
        assert not active
        seq = len(calls) + 1
        calls.append(seq)
        active.append(seq)
        time.sleep(0.01)
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt([row()], seq)))
    def applying(data, **kwargs):
        assert active == [data['store_version']['revision']]
        time.sleep(0.01)
        result = original(data, **kwargs)
        active.clear()
        return result
    monkeypatch.setattr(sources, 'apply_owner_export', applying)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: owner_source_payload(
            state_root=tmp_path, executable='example-wt', runner=runner), range(8)))
    assert all(r['ok'] for r in results)
    assert calls == list(range(1, 9))
    assert len({r['requests'][0]['request_id'] for r in results}) == 1


def test_busy_refresh_never_claims_current_or_empty_coverage(monkeypatch):
    from ccc_server import decision_sources as sources
    class Busy:
        def acquire(self, *, timeout):
            assert timeout == 5
            return False
    monkeypatch.setattr(sources, '_owner_refresh_lock', Busy())
    result = owner_source_payload(runner=lambda *a, **kw: pytest.fail('busy refresh exported'))
    assert result['ok'] is False and result['requests'] is None
    assert result['source']['authoritative'] is False and result['source']['complete'] is False


def test_identity_edits_generations_and_scope_binding(registry):
    first = apply(registry, [row()])['requests'][0]
    repeat = apply(registry, [row()])['requests'][0]
    assert first['request_id'] == repeat['request_id'] and first['revision'] == repeat['revision']
    assert registry.source_scope('watchtower-block')['scope']['store_id'] == SCOPE
    edit = apply(registry, [row(body='Choose another region')], 2)['requests'][0]
    assert edit['request_id'] == first['request_id'] and edit['revision'] > first['revision']
    new = apply(registry, [row(generation='episode-2')], 3)['requests'][0]
    assert new['request_id'] != first['request_id']
    old = next(r for r in registry.list_requests() if r['request_id'] == first['request_id'])
    assert old['status'] == 'withdrawn' and old['answer_state'] == 'none'


def test_initialized_episode_provenance_is_preserved_without_invented_history(registry):
    from ccc_server.decision_requests import read_requests
    source = row(generation='2026-01-15T18:00:00.123456Z')
    source['episode_provenance'] = dict(version=1, kind='legacy-owner-wait-initialization',
        initialized_at=source['generation'], historical_blocked_at=None,
        historical_block_time='unknown', idempotency_key='migration-key')
    first = apply(registry, [source])['requests'][0]
    displayed = read_requests(registry.db_path)[0]
    assert displayed['episode_provenance'] == source['episode_provenance']
    assert displayed['observed_at'] == source['generation']
    assert displayed['body'] == source['expected_block_question']
    assert apply(registry, [source])['requests'][0]['request_id'] == first['request_id']
    before = registry.list_requests()
    source['episode_provenance']['historical_blocked_at'] = 'invented historical date'
    with pytest.raises(ValueError, match='provenance'):
        apply(registry, [source], 2)
    assert registry.list_requests() == before


def test_absence_withdraws_only_previously_bound_source_rows(registry):
    apply(registry, [row()])
    registry.import_cards({'dc_example': {'status': 'open', 'title': 'Legacy'}})
    registry.upsert_requests([dict(adapter='watchtower-block', origin_id='EXAMPLE-2', generation='old'),
                              dict(adapter='watchtower-review', origin_id='EXAMPLE-3', generation='old')])
    assert apply(registry, [], 2)['requests'] == []
    records = registry.list_requests()
    assert next(r for r in records if r['origin_id'] == 'EXAMPLE-1')['status'] == 'withdrawn'
    assert all(r['status'] == 'open' for r in records if r['origin_id'] != 'EXAMPLE-1')


@pytest.mark.parametrize('mutation', ['failed', 'scope', 'older', 'digest', 'duplicate'])
def test_bad_or_other_source_never_retires_records(registry, mutation):
    apply(registry, [row()], 2)
    before = registry.list_requests()
    value = receipt([row()], 3)
    if mutation == 'failed':
        value['complete'] = False
    elif mutation == 'scope':
        new_id = str(uuid.uuid4())
        value['scope']['store_id'] = new_id
        value['items'][0]['expected_store_id'] = new_id
    elif mutation == 'older':
        value['store_version']['revision'] = 1
    elif mutation == 'digest':
        value['items'][0]['source_revision'] = 'bad'
    else:
        value['items'] *= 2
    with pytest.raises(ValueError):
        apply_owner_export(value, registry=registry, cards={})
    assert registry.list_requests() == before


def test_owner_distinctions_stay_structured_and_rationale_cannot_be_answered(registry):
    rows = [row(), row('EXAMPLE-2'), row('EXAMPLE-3'), row('EXAMPLE-4')]
    rows[1]['block_kind'] = 'rationale'
    rows[2]['duplicate_of'] = 'EXAMPLE-1'
    rows[3]['status'] = 'in_review'
    out = apply(registry, rows)
    assert out['counts']['owner_questions'] == 1
    assert out['counts']['rationale_approvals'] == 1
    assert out['excluded'] == {'dependency': 1, 'review_outside_scope': 1}
    assert len(out['requests']) == 2


def test_display_cache_cannot_overwrite_scoped_content_or_reopen_withdrawn_rows(registry, tmp_path):
    source = row()
    first = apply(registry, [source])['requests'][0]
    apply(registry, [], 2)
    snapshot = dict(ref=source['origin_id'], status='open', needs_input=True,
                    blocked_at=source['generation'], block_question='Stale cached question')
    (tmp_path / 'wt_items_snapshot.json').write_text(json.dumps({'items': [snapshot]}))
    result = coverage_payload(state_root=tmp_path)
    assert result['requests'][0]['request_id'] == first['request_id']
    assert result['requests'][0]['status'] == 'withdrawn'
    saved = next(r for r in registry.list_requests() if r['request_id'] == first['request_id'])
    assert saved['body'] == 'Choose region'
    assert saved['content']['source_snapshot']['store_id'] == SCOPE


def test_incomplete_source_identity_reports_data_without_withdrawal(registry):
    apply(registry, [row()])
    before = copy.deepcopy(registry.list_requests())
    result = apply(registry, [row(generation='')], 2)
    assert result['ok'] is False
    assert result['counts']['missing_generation'] == 1
    assert registry.list_requests() == before


def test_unavailable_provider_is_not_an_empty_success_or_a_registry_write(tmp_path):
    result = owner_source_payload(state_root=tmp_path, executable='')
    assert result['ok'] is False and result['requests'] is None
    assert not result['source']['complete']
    assert not (tmp_path / 'decision-inbox').exists()
