"""Shadow coverage of owner requests. Never executes actions or reads transcripts.

Ticket references on legacy cards are possible links, not canonical identity.
Only an explicit source episode can establish a stable source request identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from ccc_server import wt_review


def _last_event(item, names):
    history = item.get('history') or []
    if not isinstance(history, list):
        return ''
    for event in reversed(history):
        if isinstance(event, dict) and event.get('event') in names:
            return str(event.get('id') or event.get('at') or '')
    return ''


def wait_kind(item):
    """Match the queue attention ownership rules, including stale wait records."""
    if item.get('status') == 'closed':
        return 'closed'
    if item.get('status') == 'in_review':
        return 'review' if wt_review.needs_review(item) else 'verifier'
    if item.get('duplicate_of'):
        return 'dependency'
    if item.get('needs_input'):
        if item.get('block_kind') == 'rationale':
            return 'rationale'
        wait = item.get('wait') or {}
        if (isinstance(wait, dict) and wait.get('on') in ('owner', 'external', 'dependency', 'worker')
                and str(wait.get('block_at') or '') == str(item.get('blocked_at') or '')):
            return wait['on']
        return 'external' if item.get('block_kind') == 'awaiting-client' else 'owner'
    return 'operational'


def project(items, cards):
    requests, excluded, errors = [], {}, []
    by_ref = {}
    for card in cards.values():
        source = card.get('source') or {}
        if not isinstance(source, dict):
            continue
        tags = source.get('tags') or []
        refs = list(tags) if isinstance(tags, list) else []
        if source.get('ref'):
            refs.append(source['ref'])
        for ref in set(str(r) for r in refs):
            by_ref.setdefault(ref, []).append(card)
    for item in items:
        if not isinstance(item, dict):
            errors.append({'reason': 'invalid_ticket'})
            continue
        kind = wait_kind(item)
        if kind not in ('owner', 'rationale', 'review'):
            excluded[kind] = excluded.get(kind, 0) + 1
            continue
        ref = str(item.get('ref') or '').strip()
        body = str(item.get('block_question') or '').strip()
        if not ref or (kind != 'review' and not body):
            errors.append({'ref': ref, 'reason': 'missing_ref_or_question'})
            continue
        if kind == 'review':
            generation = _last_event(item, ('close', 'submit', 'in_review')) or str(item.get('closed_at') or '')
            resolution = item.get('resolution')
            resolution = resolution if isinstance(resolution, dict) else {}
            body = str(resolution.get('summary') or item.get('title') or '')
            adapter = 'watchtower-review'
            # Gate-stage changes within an episode revise the same request.
            revision_data = [body, item.get('gate_pending'), item.get('accept')]
        else:
            generation = str(item.get('blocked_at') or '') or _last_event(item, ('block',))
            adapter = 'watchtower-block'
            revision_data = [body, kind]
        possible = []
        for card in by_ref.get(ref, []):
            aliases = [str(card.get('id') or '')]
            if card.get('decision_number'):
                aliases.append('D-' + str(card['decision_number']))
            aliases.extend(re.findall(r'\[Q\d+:[a-z0-9-]+\]', str(card.get('title') or '')))
            possible.append({'card_id': card.get('id'), 'status': card.get('status'),
                             'aliases': aliases, 'relationship': 'same_ticket_only'})
        request = {'adapter': adapter, 'origin_id': ref, 'generation': generation,
                   'kind': 'review' if kind == 'review' else 'question',
                   'wait_kind': kind, 'title': str(item.get('title') or ref), 'body': body,
                   'visibility': 'global', 'source_revision': hashlib.sha256(
                       json.dumps(revision_data, sort_keys=True).encode()).hexdigest(),
                   'identity_status': 'source_episode' if generation else 'missing_generation',
                   'possible_legacy_links': possible, 'request_id': None}
        if kind == 'owner':
            wait = item.get('wait')
            current = (isinstance(wait, dict) and wait.get('on') in ('owner', 'external', 'dependency', 'worker')
                       and str(wait.get('block_at') or '') == str(item.get('blocked_at') or ''))
            request['source_snapshot'] = {
                'blocked_at': str(item.get('blocked_at') or ''),
                'block_question': str(item.get('block_question') or ''),
                'wait_kind': kind, 'wait': copy.deepcopy(wait) if current else None}
        requests.append(request)
    requests.sort(key=lambda r: (r['adapter'], r['origin_id']))
    return {'requests': requests, 'excluded': excluded, 'source_errors': errors,
            'counts': {'owner_questions': sum(r['wait_kind'] == 'owner' for r in requests),
                       'rationale_approvals': sum(r['wait_kind'] == 'rationale' for r in requests),
                       'human_reviews': sum(r['wait_kind'] == 'review' for r in requests),
                       'with_possible_legacy_links': sum(bool(r['possible_legacy_links']) for r in requests),
                       'without_legacy_links': sum(not r['possible_legacy_links'] for r in requests),
                       'missing_generation': sum(not r['generation'] for r in requests)}}


def snapshot(path, now=None, max_age=30):
    """Read local snapshot only. Stale/absent is never a complete empty source."""
    now = time.time() if now is None else now
    path = Path(path)
    try:
        stat = path.stat()
        if stat.st_size > 64 * 1024 * 1024:
            raise ValueError('snapshot too large')
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('invalid snapshot')
        age = max(0, now - stat.st_mtime)
        return data['items'], {'status': 'cached' if age <= max_age else 'stale',
                               'age_s': age, 'complete': False,
                               'reason': 'Display snapshot is not an authoritative full-source receipt.'}
    except (OSError, ValueError) as error:
        return [], {'status': 'unavailable', 'complete': False, 'reason': str(error)}


def coverage_payload(*, state_root=None, snapshot_path=None, registry_path=None):
    from ccc_server import decision_inbox
    from ccc_server.decision_registry import DecisionRegistry
    root = Path(state_root) if state_root else decision_inbox._state_root()
    cards_file = root / 'decision-inbox' / 'cards.json'
    cards = decision_inbox.load_cards(cards_file)
    items, source = snapshot(snapshot_path or root / 'wt_items_snapshot.json')
    result = project(items, cards)
    stable = [r for r in result['requests'] if r['generation']]
    fields = ('adapter', 'origin_id', 'generation', 'kind', 'wait_kind', 'title', 'body', 'visibility', 'source_revision', 'source_snapshot')
    try:
        registry = DecisionRegistry(registry_path or root / 'decision-inbox' / 'requests.sqlite3', timeout=1)
        registry.import_cards(cards, only_if_changed=True)
        if registry.source_scope('watchtower-block'):
            # After authoritative binding, a display cache must not replace
            # scoped source content or reopen a withdrawn request.
            saved = registry.find_source_requests('watchtower-block',
                [(r['origin_id'], r['generation']) for r in stable if r['adapter'] == 'watchtower-block'])
            reviews = [r for r in stable if r['adapter'] != 'watchtower-block']
            saved += registry.upsert_requests([{key: r[key] for key in fields if key in r} for r in reviews])
        else:
            saved = registry.upsert_requests([{key: r[key] for key in fields if key in r} for r in stable])
    except sqlite3.Error:
        # Coverage data remains useful even when shadow identity persistence
        # is busy. Do not turn contention into an empty or authoritative feed.
        return dict(result, ok=False, mode='shadow', llm_used=False, source=source,
                    registry={'status': 'unavailable', 'retryable': True},
                    error='Shadow registry unavailable; source comparison is still shown.')
    records = {(r['adapter'], r['origin_id'], r['generation']): r for r in saved}
    for request in stable:
        record = records.get((request['adapter'], request['origin_id'], request['generation']))
        if not record:
            request['identity_status'] = 'awaiting_authoritative_source'
            continue
        for field in ('request_id', 'revision', 'answer_state', 'delivery_state', 'status'):
            request[field] = record[field]
    return dict(result, ok=True, mode='shadow', llm_used=False, source=source,
                installation_id=registry.installation_id,
                limitations=['Legacy ticket links do not establish the same question episode.',
                             'Local interactive questions are not ingested.',
                             'No answers are delivered and no existing inbox counts change.'])
