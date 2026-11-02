"""Coverage is diagnostic: it never equates ticket tags with question identity."""
import copy
import json
import os
import subprocess
from pathlib import Path

from ccc_server.decision_coverage import project, snapshot, wait_kind

ROOT = Path(__file__).resolve().parent.parent


def ticket(**kwargs):
    return dict(ref='EXAMPLE-1', status='open', needs_input=True,
                blocked_at='2026-01-01T00:00:00Z', block_question='Choose a region', **kwargs)


def test_ownership_matches_existing_queue_ui():
    rows = [ticket(), ticket(wait={'on': 'external', 'block_at': '2026-01-01T00:00:00Z'}),
            ticket(wait={'on': 'worker', 'block_at': 'old'}),
            ticket(wait={'on': 'dependency', 'block_at': '2026-01-01T00:00:00Z'}),
            ticket(duplicate_of='EXAMPLE-2'), ticket(block_kind='awaiting-client')]
    script = "const q=require('./static/queue-attention.js');process.stdout.write(JSON.stringify(JSON.parse(require('fs').readFileSync(0,'utf8')).map(x=>q.project(x).key)))"
    actual = json.loads(subprocess.check_output(['node', '-e', script], input=json.dumps(rows).encode(), cwd=ROOT))
    assert [wait_kind(row) for row in rows] == actual


def test_projection_keeps_generations_and_legacy_links_separate():
    row = ticket(title='Deployment')
    card = {'id': 'dc_example', 'decision_number': 12, 'title': '[Q13:region] Choose',
            'status': 'decided', 'source': {'tags': ['EXAMPLE-1']}}
    before = copy.deepcopy((row, card))
    out = project([row], {'dc_example': card})
    request = out['requests'][0]
    assert request['generation'] == row['blocked_at']
    assert request['request_id'] is None  # No fabricated canonical merge with old card.
    assert request['possible_legacy_links'][0]['relationship'] == 'same_ticket_only'
    assert request['possible_legacy_links'][0]['aliases'] == ['dc_example', 'D-12', '[Q13:region]']
    assert out['counts']['owner_questions'] == 1
    assert (row, card) == before
    row.pop('blocked_at')
    assert project([row], {})['requests'][0]['identity_status'] == 'missing_generation'


def test_reviews_and_bad_sources_are_explicit():
    human = {'ref': 'EXAMPLE-1', 'status': 'in_review', 'gate_pending': 'review',
             'history': [{'event': 'close', 'at': '2026-01-01'}]}
    verifier = dict(human, ref='EXAMPLE-2', gate_pending='verify')
    missing = dict(ticket(), block_question='')
    out = project([human, verifier, missing], {})
    assert out['counts']['human_reviews'] == 1
    assert out['requests'][0]['generation'] == '2026-01-01'
    assert out['excluded'] == {'verifier': 1}
    assert out['source_errors'][0]['reason'] == 'missing_ref_or_question'
    assert project([], {})['requests'] == []  # No transcript/Q-index fallback.


def test_snapshots_never_claim_authoritative_empty_coverage(tmp_path):
    target = tmp_path / 'snapshot.json'
    assert snapshot(target)[1]['status'] == 'unavailable'
    target.write_text(json.dumps({'items': []}))
    os.utime(target, (100, 100))
    assert snapshot(target, now=200)[1]['status'] == 'stale'
    assert snapshot(target, now=101)[1]['status'] == 'cached'
    assert not snapshot(target, now=101)[1]['complete']
    target.write_text('broken')
    assert snapshot(target)[1]['status'] == 'unavailable'


def test_malformed_optional_fields_do_not_hide_other_requests():
    human = {'ref': 'EXAMPLE-2', 'status': 'in_review', 'gate_pending': 'review',
             'resolution': 'old summary', 'history': {'old': 'format'}}
    out = project([None, human, ticket()], {
        'dc_example': {'id': 'dc_example', 'source': {'tags': 123}}})
    assert len(out['requests']) == 2
    assert out['counts']['missing_generation'] == 1
    assert out['source_errors'] == [{'reason': 'invalid_ticket'}]


def test_endpoint_shadow_is_stable_and_never_rewrites_source_files(tmp_path):
    from ccc_server.decision_coverage import coverage_payload
    folder = tmp_path / 'decision-inbox'
    folder.mkdir()
    cards = {'dc_example': {'id': 'dc_example', 'decision_number': 12,
             'title': '[Q13:region] Choose', 'status': 'decided',
             'source': {'tags': ['EXAMPLE-1']},
             'options': [{'action': {'kind': 'spawn', 'prompt': 'Never execute'}}]}}
    card_file = folder / 'cards.json'
    card_file.write_text(json.dumps(cards))
    snap = tmp_path / 'wt_items_snapshot.json'
    snap.write_text(json.dumps({'items': [ticket(title='Region')]}))
    before = (card_file.read_bytes(), snap.read_bytes())
    first = coverage_payload(state_root=tmp_path)
    second = coverage_payload(state_root=tmp_path)
    assert first['requests'][0]['request_id'] == second['requests'][0]['request_id']
    assert first['installation_id'] == second['installation_id']
    assert not first['llm_used'] and first['mode'] == 'shadow'
    from ccc_server.decision_registry import DecisionRegistry
    registry = DecisionRegistry(folder / 'requests.sqlite3')
    rows = registry.list_requests()
    assert len(rows) == 2 and all(r['revision'] == 1 for r in rows)
    legacy = next(r for r in rows if r['adapter'] == 'legacy-card')
    assert legacy['delivery_state'] == 'unknown'
    assert {'dc_example', 'D-12', '[Q13:region]'} <= set(legacy['aliases'])
    assert (card_file.read_bytes(), snap.read_bytes()) == before
    changed = ticket(title='Region')
    changed['blocked_at'] = '2026-02-01T00:00:00Z'
    snap.write_text(json.dumps({'items': [changed]}))
    assert coverage_payload(state_root=tmp_path)['requests'][0]['request_id'] != first['requests'][0]['request_id']
