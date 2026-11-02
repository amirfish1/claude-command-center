"""Typed producer links require exact provenance and preserve old references."""
import copy
import sqlite3

import pytest

from ccc_server import decision_inbox as di
from ccc_server.decision_registry import DecisionRegistry
from ccc_server.decision_source_reports import SourceReportConflict, bind_source_request


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(di, '_state_root', lambda: tmp_path)
    registry = DecisionRegistry(tmp_path / 'decision-inbox' / 'requests.sqlite3')
    store_id = '00000000-0000-4000-8000-000000000001'
    snapshot = dict(store_id=store_id, blocked_at='episode-1', block_question='  Region A or B?\n',
                    wait_kind='owner', wait={'on': 'owner', 'block_at': 'episode-1', 'revision': 'revision-1'})
    source = dict(adapter='watchtower-block', origin_id='EXAMPLE-1', generation='episode-1',
                  kind='question', wait_kind='owner', visibility='global', title='Deployment',
                  body='Region A or B?', source_revision='revision-1', source_snapshot=snapshot)
    row = registry.observe_source('watchtower-block', {'backend': 'file', 'store_id': store_id},
                                  [source], version={'revision': 1, 'snapshot_sha256': '0' * 64})[0]
    typed = {key: copy.deepcopy(source[key]) for key in ['adapter', 'origin_id', 'generation',
                                                       'source_revision', 'source_snapshot']}
    typed.update(version=1, delivery_owner='routine')
    payload = dict(source='routine', source_id='sample:question:revision-1', title='[Q12:region] Choose region',
                   detail='Why this choice is needed', tags=['EXAMPLE-1'], source_request=typed,
                   options=[{'label': 'A', 'action': {'kind': 'human', 'prompt': 'Choose A'}}])
    return registry, source, row, typed, payload


def test_binding_uses_canonical_id_and_does_not_write_or_modify_the_producer(setup):
    registry, source, row, typed, payload = setup
    before = registry.db_path.read_bytes()
    original = copy.deepcopy(typed)
    linked = bind_source_request(typed, database=registry.db_path)
    assert linked['request_id'] == row['request_id']
    assert linked['request_revision'] == row['revision']
    assert linked['source_snapshot']['block_question'] == '  Region A or B?\n'
    assert typed == original
    assert registry.db_path.read_bytes() == before
    assert registry.list_requests() == [row]


def test_ingest_retains_exact_binding_and_existing_card_numbers_are_stable(setup):
    registry, _, row, typed, payload = setup
    cards = {'dc_old': dict(id='dc_old', source_id='routine:old', title='Old', status='open',
                           decision_number=12, created_at='2020-01-01T00:00:00Z')}
    result = di.decision_inbox_ingest(payload, cards=cards, persist=False)
    assert result['ok']
    card = cards[result['card_id']]
    assert card['source']['source_request']['request_id'] == row['request_id']
    assert card['source']['source_request']['source_snapshot'] == typed['source_snapshot']
    assert card['source']['tags'] == ['EXAMPLE-1']
    assert card['title'].startswith('[Q12:region]')
    repeated = di.decision_inbox_ingest(payload, cards=cards, persist=False)
    assert repeated['card_id'] == card['id'] and repeated['deduped']
    assert cards['dc_old']['decision_number'] == 12
    assert len(cards) == 2
    assert registry.list_requests() == [row]


@pytest.mark.parametrize('changes', [
    {'version': True}, {'adapter': 'session-question'}, {'origin_id': 'EXAMPLE-2'},
    {'generation': 'episode-2'}, {'source_revision': 'stale'}, {'request_id': 'wrong'},
    {'request_revision': True}, {'request_revision': 99}, {'delivery_owner': 'both'}, {'extra': 'ignored?'},
])
def test_bad_links_never_create_a_card(setup, changes):
    _, _, _, typed, payload = setup
    cards = {}
    result = di.decision_inbox_ingest(dict(payload, source_request=dict(typed, **changes)),
                                      cards=cards, persist=False)
    assert result['ok'] is False and result['code'] == 'source_request_conflict'
    assert cards == {}


@pytest.mark.parametrize('field,value', [
    ('store_id', '00000000-0000-4000-8000-000000000002'),
    ('block_question', 'Region A or B?'), ('blocked_at', 'episode-2'),
    ('wait_kind', 'external'), ('wait', None),
])
def test_exact_snapshot_including_whitespace_and_wait_revision_is_required(setup, field, value):
    registry, _, _, typed, _ = setup
    typed['source_snapshot'][field] = value
    with pytest.raises(SourceReportConflict):
        bind_source_request(typed, database=registry.db_path)


def test_source_ownership_change_and_rationale_are_not_admitted(setup):
    registry, source, _, typed, _ = setup
    for changes in ({'kind': 'rationale'}, {'visibility': 'local'}, {'wait_kind': 'external'},
                    {'status': 'withdrawn'}):
        registry.upsert_requests([dict(source, **changes)])
        with pytest.raises(SourceReportConflict):
            bind_source_request(typed, database=registry.db_path)


def test_new_source_metadata_cannot_silently_rebind_an_old_card(setup):
    _, _, _, typed, payload = setup
    cards = {}
    untyped = {key: value for key, value in payload.items() if key != 'source_request'}
    first = di.decision_inbox_ingest(untyped, cards=cards, persist=False)
    before = copy.deepcopy(cards)
    attempted = di.decision_inbox_ingest(payload, cards=cards, persist=False)
    assert attempted['ok'] is False and attempted['code'] == 'source_request_conflict'
    assert cards == before and cards[first['card_id']]['source'].get('source_request') is None


def test_frozen_binding_and_delivery_owner_cannot_change_on_repost(setup):
    _, _, _, typed, payload = setup
    cards = {}
    first = di.decision_inbox_ingest(payload, cards=cards, persist=False)
    before = copy.deepcopy(cards)
    changed = dict(payload, source_request=dict(typed, delivery_owner='ccc-outbox'))
    assert di.decision_inbox_ingest(changed, cards=cards, persist=False)['ok'] is False
    assert cards == before
    assert di.decision_inbox_ingest(dict(payload, source_id='new-producer-revision',
                                       source_request=dict(typed, delivery_owner='ccc-outbox')),
                                  cards=cards, persist=False)['ok']
    assert first['card_id'] in cards


def test_outbox_owned_card_cannot_trigger_a_legacy_action(setup):
    _, _, _, typed, payload = setup
    cards = {}
    result = di.decision_inbox_ingest(dict(payload, source_request=dict(typed, delivery_owner='ccc-outbox')),
                                     cards=cards, persist=False)
    before = copy.deepcopy(cards)
    assert di.decision_inbox_decide(result['card_id'], 0, cards=cards, persist=False)['ok'] is False
    assert cards == before


def test_source_options_cannot_bypass_conditional_delivery_or_change_on_repost(setup):
    _, _, _, _, payload = setup
    cards = {}
    direct = dict(payload, options=[{'label': 'Send', 'action': {'kind': 'inject',
                      'prompt': 'Answer now', 'session_id': 'session-1'}}])
    assert di.decision_inbox_ingest(direct, cards=cards, persist=False)['ok'] is False
    assert cards == {}
    assert di.decision_inbox_ingest(payload, cards=cards, persist=False)['ok']
    before = copy.deepcopy(cards)
    changed = dict(payload, options=[{'label': 'B', 'action': {'kind': 'human', 'prompt': 'Choose B'}}])
    assert di.decision_inbox_ingest(changed, cards=cards, persist=False)['ok'] is False
    assert cards == before


def test_date_family_heuristic_does_not_resolve_a_source_question(setup):
    _, _, _, _, payload = setup
    cards = {}
    first = di.decision_inbox_ingest(dict(payload, source_id='daily:2020-01-01'), cards=cards, persist=False)
    assert di.decision_inbox_ingest(dict(payload, source_id='daily:2020-01-02'), cards=cards, persist=False)['ok']
    assert cards[first['card_id']]['status'] == 'open'
    untyped = {key: value for key, value in payload.items() if key != 'source_request'}
    assert di.decision_inbox_ingest(dict(untyped, source_id='daily:2020-01-03'), cards=cards, persist=False)['ok']
    assert cards[first['card_id']]['status'] == 'open'


def test_missing_registry_is_unavailable_and_does_not_initialize_it(tmp_path, monkeypatch):
    monkeypatch.setattr(di, '_state_root', lambda: tmp_path)
    typed = dict(version=1, adapter='watchtower-block', origin_id='EXAMPLE-1', generation='episode-1',
                 source_revision='revision-1', delivery_owner='routine', source_snapshot={
                     'store_id': '00000000-0000-4000-8000-000000000001', 'blocked_at': 'episode-1',
                     'block_question': 'Choose region', 'wait_kind': 'owner', 'wait': None})
    with pytest.raises(sqlite3.Error):
        bind_source_request(typed)
    result = di.decision_inbox_ingest(dict(title='Choose region', source_id='test', source_request=typed,
                                        options=[{'label': 'A', 'action': {'kind': 'human', 'prompt': 'A'}}]),
                                    cards={}, persist=False)
    assert result['ok'] is False and result['code'] == 'source_request_unavailable'
    assert not (tmp_path / 'decision-inbox').exists()
