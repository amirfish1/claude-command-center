"""Answer a GitHub-backed owner wait from the decision queue.

These waits are read through from the ticket cache and never enter the answer
registry, so there is no conditional contract. Safety comes from re-reading the
ticket immediately before answering: the answer is only written while the
ticket still asks the exact question the owner saw.
"""
import json
import subprocess
import uuid

from ccc_server.decision_requests import github_requests


def _still_waiting(ticket, row):
    return (isinstance(ticket, dict) and ticket.get('needs_input') is True
            and github_requests([ticket]) != []
            and str(ticket.get('blocked_at') or '') == row['generation']
            and str(ticket.get('block_question') or '').strip() == row['body'])


def answer_github_wait(body, tickets, *, executable, runner=subprocess.run):
    """Return ``(payload, status)``, or None when ``body`` targets another source."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    request_id = data.get('request_id')
    row = next((r for r in github_requests(tickets) if r['request_id'] == request_id), None)
    if row is None:
        return None
    text, key = data.get('text'), data.get('idempotency_key')
    if (not isinstance(text, str) or not text.strip() or not isinstance(key, str) or not key
            or data.get('expected_revision') != row['revision']):
        return {'ok': False, 'error': 'invalid_request'}, 400
    if not executable:
        return {'ok': False, 'error': 'source_unavailable'}, 503
    ref = row['origin_id']
    try:
        found = runner([executable, 'find', ref, '--json'],
                       capture_output=True, text=True, timeout=30)
        ticket = json.loads(found.stdout) if found.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ok': False, 'error': 'source_unavailable'}, 503
    if not _still_waiting(ticket, row):
        return {'ok': False, 'error': 'conflict'}, 409
    try:
        answered = runner([executable, 'answer', '--by', 'human', '--', ref, text],
                          capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        answered = None
    if answered is None or answered.returncode != 0:
        # The answer may be written even when session delivery failed.
        return {'ok': False, 'error': 'delivery_unknown: check the ticket; do not resend'}, 502
    answer_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'ccc-github-answer:{request_id}:{key}'))
    return {'ok': True, 'request_id': request_id, 'answer_id': answer_id, 'state': 'accepted',
            'origin_id': ref,
            'answer': {'request_id': request_id, 'text': text,
                       'expected_revision': row['revision']}}, 200
