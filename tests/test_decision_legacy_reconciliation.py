"""Read-only historical reports never promote ticket matches to answers."""
import copy
import json

import pytest

from ccc_server.decision_legacy_reconciliation import (
    LEGACY_STATUSES, reconcile_legacy_cards, ticket_refs,
)

STORE = '00000000-0000-4000-8000-000000000001'
UUID = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'


def request(**changes):
    row = dict(request_id=UUID, revision=2, adapter='watchtower-block', origin_id='EXAMPLE-1',
               generation='episode-2', source_revision='revision-2', kind='question',
               wait_kind='owner', visibility='global', status='open', answer_state='none',
               delivery_state='none', delivery_owner='ccc-outbox',
               source_snapshot=dict(store_id=STORE, blocked_at='episode-2',
                                    block_question='Current question?', wait_kind='owner', wait=None))
    row.update(changes)
    return row


def receipt(rows=None):
    return dict(ok=True, mode='authoritative_read_only', requests=[request()] if rows is None else rows,
                source=dict(status='current', complete=True, authoritative=True,
                            scope=dict(backend='file', store_id=STORE)))


def card(**changes):
    row = dict(id='dc_old', decision_number=14, title='[Q21:old-choice] Old question',
               status='open', context='Original\n text', source=dict(source='routine', tags=['EXAMPLE-1']),
               options=[dict(label='Old answer', action=dict(kind='human', prompt='Original answer'))])
    row.update(changes)
    return row


def typed(row):
    return dict(version=1, request_revision=row['revision'], delivery_owner=row['delivery_owner'],
                **{k: copy.deepcopy(row[k]) for k in ('request_id', 'adapter', 'origin_id',
                    'generation', 'source_revision', 'source_snapshot')})


def one(c=None, p=None, **kwargs):
    return reconcile_legacy_cards({'storage-key': card() if c is None else c}, p, **kwargs)['cards'][0]


@pytest.mark.parametrize('value', ['[Q620:question]', 'Q-620', 'D-14', 'Q620', 'D14',
    ' EXAMPLE-1', 'EXAMPLE-1 ', 'see EXAMPLE-1', 'https://example.test/EXAMPLE-1',
    'EXAMPLE-1\n', '[EXAMPLE-1]', 'EXAMPLE-1 / EXAMPLE-2', 123, None])
def test_strict_reference_fields_never_parse_question_tags_or_embedded_ticket(value):
    c = card(title='EXAMPLE-1', context='EXAMPLE-2', source_id='EXAMPLE-3',
             source=dict(tags=[value], ref=value))
    assert ticket_refs(c) == []
    assert one(c, receipt())['classification'] == 'unlinked/no-ticket'


def test_tags_can_contain_questions_and_multiple_exact_ticket_refs():
    c = card(source=dict(ref='EXAMPLE-2', tags=['[Q620:question]', 'EXAMPLE-1', 'EXAMPLE-1']))
    assert ticket_refs(c) == ['EXAMPLE-1', 'EXAMPLE-2']
    assert '[Q620:question]' in one(c)['aliases']


def test_no_source_is_uncertain_not_withdrawn_and_never_replays():
    r = one()
    assert r['classification'] == 'source-unavailable'
    assert r['current_questions'] == []
    assert r['action'] == dict(kind='none', request_ids=[], legacy_action_policy='hold',
                              requires_reconfirmation=False, replay_allowed=False)


@pytest.mark.parametrize('status', LEGACY_STATUSES + ('future-status',))
def test_all_retained_statuses_link_to_current_question_without_changing_history(status):
    c = card(status=status, cardref='report:old', cardrefs=['report:older'],
             decided=dict(option=0, label='Old answer', result={'ok': True, 'effect': 'injected'}))
    before = copy.deepcopy(c)
    r = one(c, receipt())
    assert r['classification'] == 'current-source-candidate'
    assert r['current_questions'] == [dict(request_id=UUID, revision=2, origin_id='EXAMPLE-1',
        generation='episode-2', relationship='same_ticket_only', requires_reconfirmation=True)]
    assert r['card'] == c == before
    assert r['card_key'] == 'storage-key'
    assert {'D14', 'D-14', '[Q21:old-choice]', 'report:old', 'report:older'} <= set(r['aliases'])
    assert r['legacy_delivery_state'] == 'unknown'
    assert r['legacy_answer_state'] == ('decided' if status == 'decided' else 'unknown')
    assert r['action']['replay_allowed'] is False


def test_historical_withdrawn_episode_never_masks_new_question_on_same_ticket():
    old = request(request_id=OTHER, generation='episode-1', status='withdrawn',
                  source_snapshot=dict(store_id=STORE, blocked_at='episode-1'))
    r = one(card(status='decided'), receipt([old, request()]))
    assert [r['request_id'] for r in r['current_questions']] == [UUID]
    assert r['binding']['state'] == 'untyped'
    assert one(p=receipt([old]))['classification'] == 'no-longer-waiting'
    assert one(p=receipt([]))['classification'] == 'no-longer-waiting'


def test_multiple_open_episodes_remain_separate_candidates_no_forced_merge():
    other = request(request_id=OTHER, generation='episode-3',
                    source_snapshot=dict(store_id=STORE, blocked_at='episode-3'))
    result = one(p=receipt([request(), other]))
    assert result['action']['request_ids'] == [UUID, OTHER]
    assert all(r['relationship'] == 'same_ticket_only' for r in result['current_questions'])


@pytest.mark.parametrize('change', [dict(status='withdrawn'), dict(status='answered'),
    dict(answer_state='recorded'), dict(wait_kind='external'), dict(kind='rationale'),
    dict(visibility='local')])
def test_only_current_open_eligible_owner_questions_are_candidates(change):
    assert one(p=receipt([request(**change)]))['current_questions'] == []


@pytest.mark.parametrize('mutation', ['failed', 'partial', 'stale', 'cached', 'nonauthoritative',
                                     'missing_requests', 'errors', 'bad_identity', 'duplicate_uuid', 'shadow'])
def test_failed_or_partial_receipts_never_invent_currency_or_withdrawal(mutation):
    p = receipt()
    if mutation == 'failed':
        p['ok'] = False
    elif mutation == 'partial':
        p['source']['complete'] = False
    elif mutation in ('stale', 'cached'):
        p['source']['status'] = mutation
    elif mutation == 'nonauthoritative':
        p['source']['authoritative'] = False
    elif mutation == 'missing_requests':
        p['requests'] = None
    elif mutation == 'errors':
        p['source_errors'] = [{'reason': 'missing_generation'}]
    elif mutation == 'bad_identity':
        p['requests'][0]['request_id'] = 'not-a-uuid'
    elif mutation == 'duplicate_uuid':
        p['requests'].append(copy.deepcopy(p['requests'][0]))
    elif mutation == 'shadow':
        p['mode'] = 'shadow'
    result = reconcile_legacy_cards([card()], p)
    assert result['source_complete'] is False
    assert result['cards'][0]['classification'] == 'source-unavailable'
    assert result['cards'][0]['current_questions'] == []


def test_typed_binding_verifies_exact_identity_snapshot_revision_and_owner():
    row = request()
    c = card(source=dict(source='routine', source_request=typed(row)))
    result = one(c, receipt())
    assert result['binding'] == dict(state='verified', issues=[])
    assert result['action']['legacy_action_policy'] == 'hold'
    assert result['action']['replay_allowed'] is False


@pytest.mark.parametrize('key,value', [('generation', 'episode-old'), ('request_revision', 1),
    ('source_revision', 'old'), ('delivery_owner', 'routine'), ('origin_id', 'EXAMPLE-2'),
    ('source_snapshot', {'store_id': STORE})])
def test_stale_typed_binding_cannot_be_repaired_by_ticket_match(key, value):
    binding = typed(request())
    binding[key] = value
    c = card(source=dict(tags=['EXAMPLE-1'], source_request=binding))
    r = one(c, receipt())
    assert r['binding']['state'] == 'stale'
    assert key in r['binding']['issues']
    assert r['classification'] == 'current-source-candidate'
    assert r['action']['requires_reconfirmation']
    assert r['card']['source']['source_request'] == binding


def test_old_typed_uuid_keeps_its_identity_when_new_episode_exists():
    old = request(request_id=OTHER, status='withdrawn')
    binding = typed(old)
    binding['delivery_owner'] = 'routine'
    old['delivery_owner'] = 'routine'
    c = card(source=dict(source_request=binding))
    r = one(c, receipt(), canonical_requests=[old, request()])
    assert r['binding']['state'] == 'verified'
    assert r['action']['request_ids'] == [UUID]
    assert r['current_questions'][0]['relationship'] == 'same_ticket_only'


@pytest.mark.parametrize('binding', [None, {}, dict(request_id='bad'),
                                    dict(typed(request()), request_revision=True)])
def test_malformed_typed_binding_is_guarded(binding):
    r = one(card(source=dict(source_request=binding)), receipt())
    assert r['binding']['state'] == 'invalid'
    assert r['action']['legacy_action_policy'] == 'hold'


def test_owner_requires_canonical_evidence_not_report_claim():
    row = request()
    binding = typed(row)
    del row['delivery_owner']
    c = card(source=dict(source_request=binding))
    assert one(c, receipt([row]))['binding']['state'] == 'unavailable'
    assert one(c, receipt([row]), delivery_owners={UUID: 'ccc-outbox'})['binding']['state'] == 'verified'
    assert one(c, receipt([row]), delivery_owners={UUID: 'routine'})['binding']['state'] == 'stale'


def test_registry_content_shape_supported_for_exact_binding():
    row = request()
    content = {k: row.pop(k) for k in ('source_revision', 'source_snapshot', 'kind', 'wait_kind', 'visibility')}
    row['content'] = content
    c = card(source=dict(source_request=typed(request())))
    assert one(c, receipt([row]))['binding']['state'] == 'verified'


def test_complete_inventory_unrelated_cards_and_json_detachment():
    cards = {s: card(id='dc_' + s, status=s) for s in LEGACY_STATUSES}
    cards['unrelated'] = card(id='dc_unrelated', status='custom', source=dict(source='governor'),
                              title='A routine alert')
    cards['missing'] = card(id='dc_missing', source={})
    del cards['missing']['status']
    p = receipt()
    before = copy.deepcopy([cards, p])
    result = reconcile_legacy_cards(cards, p)
    assert [cards, p] == before
    assert result['counts']['total'] == 8
    assert result['counts']['by_status'] == dict.fromkeys((*LEGACY_STATUSES, 'custom', '<missing>'), 1)
    unrelated = next(r for r in result['cards'] if r['card_id'] == 'dc_unrelated')
    assert unrelated['classification'] == 'unlinked/no-ticket'
    assert unrelated['action']['legacy_action_policy'] == 'unchanged'
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    result['cards'][0]['card']['options'][0]['label'] = 'mutated'
    assert cards == before[0]


def test_empty_complete_coverage_differs_from_unavailable():
    assert reconcile_legacy_cards({}, receipt([]))['coverage']['status'] == 'complete'
    assert reconcile_legacy_cards({}, None)['coverage']['status'] == 'unavailable'


def test_nonfinite_or_non_json_input_fails_without_coercion():
    with pytest.raises(ValueError):
        reconcile_legacy_cards([card(context=float('nan'))])
    with pytest.raises(TypeError):
        reconcile_legacy_cards([card(context=object())])


def test_foreign_store_or_adapter_never_establishes_current_ticket_relationship():
    binding = typed(request())
    binding['source_snapshot']['store_id'] = OTHER
    c = card(source=dict(source_request=binding, tags=['EXAMPLE-1']))
    r = one(c, receipt())
    assert r['classification'] == 'source-unavailable'
    assert r['binding']['state'] == 'stale'
    assert r['current_questions'] == []
    binding = dict(typed(request()), adapter='session-question')
    r = one(card(source=dict(source_request=binding, tags=['EXAMPLE-1'])), receipt())
    assert r['classification'] == 'source-unavailable'
    assert r['current_questions'] == []


def test_no_io_or_source_refresh_is_needed(monkeypatch):
    import builtins
    import socket
    import sqlite3
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError('Reconciliation attempted I/O')

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', forbidden)
        patch.setattr(socket, 'socket', forbidden)
        patch.setattr(sqlite3, 'connect', forbidden)
        patch.setattr(subprocess, 'run', forbidden)
        result = reconcile_legacy_cards([card()], receipt())
    assert result['cards'][0]['classification'] == 'current-source-candidate'


@pytest.mark.parametrize('cards', [None, 'bad', [None], [False], [42]])
def test_invalid_card_collection_cannot_silently_reduce_coverage(cards):
    with pytest.raises(ValueError):
        reconcile_legacy_cards(cards, receipt())
