"""Pure historical-card annotations; no imports of stores or action handlers.

A ticket reference is a navigation relationship, never question identity.
Only a successful authoritative owner-source envelope can establish currency.
See docs/decision-legacy-reconciliation.md for the caller's trust boundary.
"""
from collections import Counter
from collections.abc import Mapping
import json
import re
import uuid


LEGACY_STATUSES = ('open', 'snoozed', 'decided', 'dismissed', 'expired', 'superseded')
_TICKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*-\d+\Z', re.ASCII)
_QUESTION = re.compile(r'\[Q\d+:[a-z0-9-]+\]', re.ASCII)


def _ticket(value):
    # Whole structured fields only. Never search free text, URLs or Q aliases.
    return (isinstance(value, str) and bool(_TICKET.fullmatch(value))
            and value.rsplit('-', 1)[0].upper() not in ('D', 'Q'))


def ticket_refs(card):
    """Extract exact ticket refs from source.ref and individual source.tags."""
    source = card.get('source')
    if not isinstance(source, dict):
        return []
    tags = source.get('tags')
    values = list(tags) if isinstance(tags, list) else []
    values.append(source.get('ref'))
    return sorted({value for value in values if _ticket(value)})


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _content(row, key):
    content = row.get('content')
    return row.get(key, content.get(key) if isinstance(content, dict) else None)


def _snapshot(row):
    value = _content(row, 'source_snapshot')
    return value if isinstance(value, dict) else {}


def _identified(row, store_id):
    return (isinstance(row, dict) and row.get('adapter') == 'watchtower-block'
            and _ticket(row.get('origin_id')) and _uuid(row.get('request_id'))
            and isinstance(row.get('generation'), str) and bool(row['generation'])
            and type(row.get('revision')) is int and row['revision'] > 0
            and _snapshot(row).get('store_id') == store_id
            and _snapshot(row).get('blocked_at') == row['generation']
            and isinstance(row.get('status'), str) and bool(row['status'])
            and isinstance(row.get('answer_state'), str) and bool(row['answer_state'])
            and isinstance(_content(row, 'source_revision'), str)
            and bool(_content(row, 'source_revision')))


def _eligible(row):
    return (row.get('status') == 'open' and _content(row, 'kind') == 'question'
            and _content(row, 'wait_kind') == 'owner'
            and _content(row, 'visibility') == 'global'
            and (row.get('answer_state') == 'none' or row.get('delivery_state') == 'rejected'))


def _binding_check(binding, rows, owners):
    """Verify frozen identity, content and owner; missing evidence is unknown."""
    if not isinstance(binding, dict):
        return 'invalid', ['typed_binding_not_object']
    if (type(binding.get('version')) is not int or binding.get('version') != 1
            or binding.get('adapter') != 'watchtower-block'
            or not _uuid(binding.get('request_id'))
            or type(binding.get('request_revision')) is not int
            or binding['request_revision'] < 1
            or binding.get('delivery_owner') not in ('routine', 'ccc-outbox')
            or not _ticket(binding.get('origin_id'))
            or not isinstance(binding.get('generation'), str) or not binding['generation']
            or not isinstance(binding.get('source_revision'), str) or not binding['source_revision']
            or not isinstance(binding.get('source_snapshot'), dict)):
        return 'invalid', ['invalid_typed_identity']
    matches = [r for r in rows if r.get('request_id') == binding['request_id']]
    if len(matches) != 1:
        return 'unavailable', ['canonical_uuid_not_uniquely_available']
    row = matches[0]
    mismatches = [key for key in ('adapter', 'origin_id', 'generation') if row.get(key) != binding[key]]
    if row.get('revision') != binding['request_revision']:
        mismatches.append('request_revision')
    for key in ('source_revision', 'source_snapshot'):
        if _content(row, key) != binding[key]:
            mismatches.append(key)
    owner = owners.get(binding['request_id'], row.get('delivery_owner'))
    if owner is not None and owner != binding['delivery_owner']:
        mismatches.append('delivery_owner')
    if mismatches:
        return 'stale', mismatches
    if owner is None:
        return 'unavailable', ['canonical_delivery_owner_unavailable']
    return 'verified', []


def reconcile_legacy_cards(cards, source_payload=None, *, canonical_requests=None, delivery_owners=None):
    """Return detached JSON annotations for ALL retained cards, without I/O.

    cards: id->card mapping or list of cards (never a visibility-filtered list).
    source_payload: decision_sources.owner_source_payload() result, whose
      requests are the complete current source observation, not registry history.
    canonical_requests: optional registry rows for exact typed-binding checks;
      must be from the same observation. Does not add current candidates.
    delivery_owners: optional UUID->owner map from that registry observation.
    Inputs must be JSON values; invalid/nonfinite JSON raises before any work.
    """
    # Detach at the boundary; nothing returned can mutate the caller's cards.
    inputs = json.loads(json.dumps([cards, source_payload, canonical_requests, delivery_owners],
                                  allow_nan=False))
    cards, payload, canonical, owners = inputs
    owners = owners if isinstance(owners, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    source = payload.get('source')
    source = source if isinstance(source, dict) else {}
    scope = source.get('scope')
    scope = scope if isinstance(scope, dict) else {}
    store_id = scope.get('store_id')
    observed = payload.get('requests')
    authoritative = (payload.get('ok') is True and payload.get('mode') == 'authoritative_read_only'
                     and source.get('status') == 'current' and source.get('complete') is True
                     and source.get('authoritative') is True and scope.get('backend') == 'file'
                     and _uuid(store_id) and isinstance(observed, list)
                     and not payload.get('source_errors'))
    observed = observed if isinstance(observed, list) else []
    # Incomplete/malformed identities or duplicate UUIDs invalidate the entire
    # comparison. Never turn a dropped row into evidence of withdrawal.
    complete = bool(authoritative and all(_identified(r, store_id) for r in observed)
                    and len({r['request_id'] for r in observed}) == len(observed))
    canonical = canonical if isinstance(canonical, list) else observed
    canonical = [r for r in canonical if isinstance(r, dict)]
    candidates = [r for r in observed if _eligible(r)] if complete else []
    if not isinstance(cards, (dict, list)):
        raise ValueError('Retained cards must be an id mapping or list')
    entries = cards.items() if isinstance(cards, Mapping) else (
        (c.get('id') if isinstance(c, dict) else None, c) for c in cards)
    results = []
    for card_key, card in entries:
        if not isinstance(card, dict):
            raise ValueError('Every retained card must be an object')
        source_card = card.get('source')
        source_card = source_card if isinstance(source_card, dict) else {}
        typed = 'source_request' in source_card
        binding = source_card.get('source_request')
        refs = ticket_refs(card)
        supported_binding = (isinstance(binding, dict)
                             and binding.get('adapter') == 'watchtower-block')
        binding_store = binding.get('source_snapshot') if isinstance(binding, dict) else None
        foreign_store = (isinstance(binding_store, dict) and binding_store.get('store_id')
                         and binding_store['store_id'] != store_id)
        if supported_binding and _ticket(binding.get('origin_id')):
            refs = sorted(set(refs + [binding['origin_id']]))
        linked = [r for r in candidates if r['origin_id'] in refs]
        state, issues = _binding_check(binding, canonical, owners) if typed else ('untyped', [])
        if not refs and not typed:
            classification = 'unlinked/no-ticket'
        elif not complete or not refs or foreign_store or (typed and isinstance(binding, dict)
                                         and binding.get('adapter') not in (None, 'watchtower-block')):
            classification = 'source-unavailable'
        elif linked:
            classification = 'current-source-candidate'
        else:
            classification = 'no-longer-waiting'
        if classification == 'source-unavailable':
            linked = []
        # Typed mismatch describes a frozen binding, independently of whether
        # this ticket has a newer source episode waiting for a fresh answer.
        navigation = [dict(request_id=r['request_id'], revision=r['revision'],
                           origin_id=r['origin_id'], generation=r['generation'],
                           relationship='same_ticket_only', requires_reconfirmation=True)
                      for r in linked]
        guarded = bool(refs or typed)
        results.append(dict(card_key=card_key, card_id=card.get('id'), card=card,
            aliases=_aliases(card_key, card), ticket_refs=refs, classification=classification,
            binding={'state': state, 'issues': issues}, current_questions=navigation,
            legacy_answer_state='decided' if card.get('status') == 'decided' else 'unknown',
            legacy_delivery_state='unknown',
            action={'kind': 'open_current_question' if navigation else 'none',
                    'request_ids': [r['request_id'] for r in linked],
                    'legacy_action_policy': 'hold' if guarded else 'unchanged',
                    'requires_reconfirmation': bool(navigation), 'replay_allowed': False}))
    statuses = Counter(str(r['card'].get('status', '<missing>')) for r in results)
    counts = Counter(r['classification'] for r in results)
    return dict(version=1, mode='read_only', source_complete=complete,
                coverage={'status': 'complete' if complete else 'unavailable',
                          'adapter': 'watchtower-block', 'scope': scope,
                          'reason': '' if complete else 'No successful complete authoritative owner observation.'},
                cards=results, counts={'total': len(results), 'by_status': dict(statuses),
                                      'by_classification': dict(counts)},
                replay_allowed=False)


def _aliases(card_key, card):
    values = [card_key, card.get('id')]
    for key in ('aliases', 'cardrefs'):
        aliases = card.get(key)
        if isinstance(aliases, list):
            values.extend(aliases)
    for key in ('cardref', 'q_alias', 'question_id', 'decision_ref'):
        values.append(card.get(key))
    number = card.get('decision_number')
    if type(number) is int and number > 0:
        values.extend([f'D{number}', f'D-{number}'])
    values.extend(_QUESTION.findall(str(card.get('title') or '')))
    source = card.get('source')
    if isinstance(source, dict) and isinstance(source.get('tags'), list):
        values.extend(v for v in source['tags'] if isinstance(v, str) and _QUESTION.fullmatch(v))
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v))
