"""Validate authoritative owner exports before changing CCC's source projection.

Only CCC's registry changes. Source export/receipt commands are read-only; no
answers, scans, models or worker actions run here. Remote and review sources
are explicitly outside this local-file contract.
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path

from ccc_server.decision_coverage import project


REQUIRED_CAPABILITIES = {'conditional-owner-answer-v1', 'answer-receipt-v1',
                         'owner-blocks-export-v1', 'store-identity-v1'}


def validate_export(receipt):
    capabilities = receipt.get('capabilities') if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1
            or receipt.get('adapter') != 'watchtower-block'
            or receipt.get('authoritative') is not True or receipt.get('complete') is not True
            or not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities)
            or not REQUIRED_CAPABILITIES <= set(capabilities)):
        raise ValueError('A complete conditional owner source receipt is required')
    scope = receipt.get('scope') or {}
    identity = scope.get('store_id') if isinstance(scope, dict) else None
    if (not isinstance(scope, dict) or scope.get('backend') != 'file' or not isinstance(identity, str)
            or str(uuid.UUID(identity)) != identity):
        raise ValueError('A durable local store UUID is required')
    if not isinstance(receipt.get('items'), list):
        raise ValueError('Full source items required')
    version = receipt.get('store_version') or {}
    if (not isinstance(version, dict) or not isinstance(version.get('snapshot_sha256'), str)
            or not re.fullmatch('[a-f0-9]{64}', version['snapshot_sha256'])):
        raise ValueError('Source snapshot digest required')
    items, origins = [], set()
    for row in receipt['items']:
        if (not isinstance(row, dict) or not isinstance(row.get('origin_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*-\d+', row['origin_id'])
                or row['origin_id'] in origins or not isinstance(row.get('generation'), str)
                or not isinstance(row.get('body'), str)
                or row.get('body') != row.get('expected_block_question')
                or row.get('generation') != row.get('expected_blocked_at')
                or row.get('expected_wait_kind') != 'owner' or row.get('wait_kind') != 'owner'
                or row.get('expected_store_id') != identity or 'expected_wait' not in row):
            raise ValueError('Invalid source question identity or exact snapshot')
        origins.add(row['origin_id'])
        wait = row['expected_wait']
        if wait is not None and (not isinstance(wait, dict) or wait.get('on') != 'owner'
                                 or str(wait.get('block_at') or '') != row['generation']):
            raise ValueError('Invalid current owner wait record')
        digest = hashlib.sha256(json.dumps([row['generation'], row['body'], 'owner', wait],
                                          sort_keys=True).encode()).hexdigest()
        if row.get('source_revision') != digest:
            raise ValueError('Source question revision mismatch')
        items.append(dict(ref=row['origin_id'], title=row.get('title') or row['origin_id'],
                          status=row.get('status') or 'open', duplicate_of=row.get('duplicate_of'),
                          needs_input=True, blocked_at=row['generation'], block_question=row['body'],
                          wait=wait, block_kind=row.get('block_kind') or ''))
    return scope, version, items


def apply_owner_export(receipt, *, registry, cards):
    scope, version, items = validate_export(receipt)
    reviews = sum(item['status'] == 'in_review' for item in items)
    result = project([item for item in items if item['status'] != 'in_review'], cards)
    if reviews:
        result['excluded']['review_outside_scope'] = reviews
    by_origin = {r['origin_id']: r for r in receipt['items']}
    records = []
    for request in result['requests']:
        row = by_origin[request['origin_id']]
        request['source_revision'] = row['source_revision']
        request['source_snapshot'] = dict(store_id=scope['store_id'], blocked_at=row['generation'],
            block_question=row['body'], wait_kind='owner', wait=row['expected_wait'])
        fields = ('adapter', 'origin_id', 'generation', 'kind', 'wait_kind', 'title', 'body',
                  'visibility', 'source_revision', 'source_snapshot')
        records.append({key: request[key] for key in fields})
    if result['source_errors'] or result['counts']['missing_generation']:
        return dict(result, ok=False, mode='authoritative_read_only',
                    source=dict(receipt, items=None), error='Source questions have incomplete identities; registry unchanged.')
    saved = registry.observe_source('watchtower-block', scope, records, version=version)
    rows = {r['origin_id']: r for r in saved}
    for request in result['requests']:
        record = rows[request['origin_id']]
        for field in ('request_id', 'revision', 'status', 'answer_state', 'delivery_state'):
            request[field] = record[field]
    return dict(result, ok=True, mode='authoritative_read_only', llm_used=False,
                installation_id=registry.installation_id,
                source={'status': 'current', 'complete': True, 'authoritative': True,
                        'scope': scope, 'store_version': version},
                limitations=['This receipt covers local file-backed owner waits only.',
                             'Remote providers and review gates require separate adapters.',
                             'Rationale approvals are read-only; local interactive questions are excluded.'])


def owner_source_payload(*, state_root=None, executable=None, runner=None):
    from ccc_server import decision_inbox
    from ccc_server.decision_registry import DecisionRegistry
    if executable is None:
        from ccc_server.watchtower_msg import _wt_cli_path
        executable = _wt_cli_path()
    unavailable = dict(ok=False, mode='authoritative_read_only', llm_used=False, requests=None,
                       source={'status': 'unavailable', 'complete': False, 'authoritative': False})
    if not executable:
        return dict(unavailable, error='WatchTower conditional source is unavailable')
    try:
        process = (runner or subprocess.run)([executable, 'owner-blocks-export', '--json'],
                                            capture_output=True, text=True, timeout=10)
        receipt = json.loads(process.stdout)
        if process.returncode:
            return dict(unavailable, error='WatchTower did not return an authoritative source receipt')
        validate_export(receipt)  # Refuse bad receipts before touching the registry.
        root = Path(state_root) if state_root else decision_inbox._state_root()
        registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3', timeout=1)
        cards = decision_inbox.load_cards(root / 'decision-inbox' / 'cards.json')
        return apply_owner_export(receipt, registry=registry, cards=cards)
    except (OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        return dict(unavailable, error=str(error))
