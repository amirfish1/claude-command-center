"""Owner close for one exact decision request; never wakes a worker.

A WatchTower question closes at the source through `close-conditional`, which
rejects anything but the exact question the owner saw. A session question has
no source close, so it is only hidden in CCC and its session is left alone.
Disabled requests initialize nothing. A lost response is retried with the same
key, which replays the source receipt instead of closing again.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccc_server.decision_answer_api import OPT_IN

REASON = 'No longer relevant'
FIELDS = {'request_id', 'expected_revision', 'idempotency_key'}
# Status for each source refusal. All are definite: nothing was closed.
REFUSALS = {'conflict': 409, 'invalid_request': 400, 'unsupported': 501}


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _parse(body):
    try:
        request = json.loads(body)
    except (ValueError, TypeError):
        return None
    if (not isinstance(request, dict) or set(request) != FIELDS
            or not _uuid(request['request_id']) or not _uuid(request['idempotency_key'])
            or type(request['expected_revision']) is not int or request['expected_revision'] < 1):
        return None
    return request


def _result(record, marker, *, state='closed'):
    scope = 'source' if record['adapter'] == 'watchtower-block' else 'ccc'
    return dict(ok=True, request_id=record['request_id'], origin_id=record['origin_id'],
                adapter=record['adapter'], state=state, scope=scope, work_completed=False,
                closed_at=(marker or {}).get('closed_at'))


def _refusal(record, request):
    """Why an open request may not close now, or None."""
    if record['revision'] != request['expected_revision']:
        return 'conflict', 409
    if record['answer_state'] != 'none' and record['delivery_state'] != 'rejected':
        return 'answer_in_progress', 409
    return None


def _close(registry, adapter, request):
    record = registry.get_request(request['request_id'])
    if record is None:
        return {'ok': False, 'error': 'not_found'}, 404
    if (record['adapter'] not in ('watchtower-block', 'session-question')
            or record['content'].get('kind') != 'question'
            or record['content'].get('visibility') != 'global'):
        return {'ok': False, 'error': 'unsupported'}, 501
    marker = registry.closed_marker(record['request_id'])
    if marker:
        if marker.get('idempotency_key') == request['idempotency_key']:
            return _result(record, marker), 200
        return {'ok': False, 'error': 'conflict'}, 409
    now = datetime.now(timezone.utc).isoformat()
    marker = dict(idempotency_key=request['idempotency_key'], closed_at=now)
    if record['adapter'] == 'session-question':
        if record['status'] != 'open':
            return {'ok': False, 'error': 'conflict'}, 409
        refused = _refusal(record, request)
        if refused:
            return {'ok': False, 'error': refused[0]}, refused[1]
        if not registry.mark_closed(record['request_id'], marker,
                                    expected_revision=request['expected_revision']):
            return {'ok': False, 'error': 'conflict'}, 409
        return _result(record, marker), 200
    # A row the source no longer reports may be this key's own lost close, so
    # it still goes to the source, which replays that receipt or refuses.
    if record['status'] == 'open':
        refused = _refusal(record, request)
        if refused:
            return {'ok': False, 'error': refused[0]}, refused[1]
        if registry.delivery_owner(record['request_id']) not in (None, 'ccc-outbox'):
            return {'ok': False, 'error': 'routine_owned'}, 409
    if adapter is None:
        from ccc_server.decision_watchtower import WatchTowerCloses
        from ccc_server.watchtower_msg import _wt_cli_path
        executable = _wt_cli_path()
        if not executable:
            return {'ok': False, 'error': 'source_unavailable'}, 503
        adapter = WatchTowerCloses(executable)
    outcome = adapter.close(record, request['idempotency_key'], REASON)
    if outcome['status'] == 'rejected':
        return {'ok': False, 'error': outcome['reason']}, REFUSALS.get(outcome['reason'], 409)
    if outcome['status'] != 'accepted':
        return _result(record, None, state='unknown'), 200
    registry.mark_closed(record['request_id'], marker)
    return _result(record, marker), 200


def close_payload(body, *, state_root=None, environ=None, adapter=None):
    from ccc_server import decision_inbox
    from ccc_server.decision_answer_settings import answer_environment
    root = Path(state_root) if state_root else decision_inbox._state_root()
    if answer_environment(root, environ).get(OPT_IN) != '1':
        return {'ok': False, 'error': 'answers_disabled'}, 503
    request = _parse(body)
    if request is None:
        return {'ok': False, 'error': 'invalid_request'}, 400
    try:
        from ccc_server.decision_registry import DecisionRegistry
        registry = DecisionRegistry(root / 'decision-inbox' / 'requests.sqlite3', timeout=5)
        return _close(registry, adapter, request)
    except ValueError:
        return {'ok': False, 'error': 'unsupported'}, 501
    except (OSError, sqlite3.Error):
        return {'ok': False, 'error': 'registry_unavailable'}, 503
