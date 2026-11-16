"""Turn initiator metadata collected by existing transcript parsers, without I/O.

This is recorded attribution, not authentication. Unknown machine senders never
become owner input just because a legacy envelope lacks a durable identity.
"""
from datetime import datetime, timezone
import json
import math
import re

from ccc_server.transcript_bookkeeping import is_compaction_bookkeeping

VERSION = 1
_HEADER = re.compile(r'^\[sender: (.*?) \(([^)]+)\)(?: ·|\])')
_PEER = re.compile(r'^<cross-session-message\b[^>]*\bfrom-name=["\']([^"\']+)["\']')
_SKIP = ('<system-reminder', '<user-prompt-submit-hook', '<local-command-',
         '<turn_aborted>', '[Request interrupted',
         '# AGENTS.md instructions', '<environment_context>',
         'This session is being continued from a previous conversation',
         'Stop hook feedback', 'Base directory for this skill:')


def epoch(value):
    try:
        if isinstance(value, (int, float)):
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def classify(text, event=None):
    event = event or {}
    t = str(text or '').strip()
    if not t or is_compaction_bookkeeping(event) or event.get('isCompactSummary') or t.startswith(_SKIP):
        return None
    # A continuation bootstrap embeds the original injected task after its
    # instructions. Attribute that task rather than the generated preamble.
    if t.startswith('You continue the work of session ') and '\nTask: ' in t:
        t = t.split('\nTask: ', 1)[1].lstrip()
    # Claude wraps peer messages; the author envelope inside takes precedence
    # over the transport's from-name (which may be a relay).
    peer_info = event.get('peer') or {}
    origin_info = event.get('origin') or {}
    peer = str(peer_info.get('name') or peer_info.get('from') or '') if isinstance(peer_info, dict) else ''
    if not peer and isinstance(origin_info, dict) and origin_info.get('kind') == 'peer':
        peer = str(origin_info.get('from_name') or origin_info.get('name') or origin_info.get('from') or 'unknown')
    if t.startswith('Another Claude session sent a message:'):
        t = t.split('\n', 1)[-1].lstrip()
    match = _PEER.match(t)
    if match:
        peer = match[1]
        t = t.split('>', 1)[-1].lstrip()
    author = event.get('attribution', {}).get('author') if isinstance(event.get('attribution'), dict) else None
    if not author and t.startswith('[Message attribution v1: '):
        try:
            author = json.loads(t.split('\n', 1)[0][len('[Message attribution v1: '):-1]).get('author')
        except (ValueError, TypeError, AttributeError):
            pass
        t = t.partition('\n')[2].lstrip()
    header = _HEADER.match(t)
    if header:
        author = {'kind': header[2], 'name_at_send': header[1]}
        t = t.partition('\n')[2].lstrip()
    # Legacy service deliveries sometimes carry an "unknown" envelope. The
    # explicit body marker still establishes service, never human, provenance.
    if t.lower().startswith(('[watchtower]', '[watchtower:', '[takeover]', '<<autonomous-loop')):
        return {'kind': 'service', 'name': 'WatchTower' if 'watchtower' in t[:20].lower() else 'Routine', 'basis': 'marker'}
    if t.startswith('<task-notification') or event.get('turnOrigin') == 'task_notification':
        return {'kind': 'background', 'name': 'Background task', 'basis': 'marker'}
    if isinstance(author, dict):
        kind = str(author.get('kind') or 'unknown').lower()
        kind = 'human' if kind in ('human', 'user', 'owner') else kind
        return {'kind': kind if kind in ('human', 'session', 'service') else 'unknown',
                'id': str(author.get('id') or '')[:160],
                'name': str(author.get('name_at_send') or author.get('id') or '')[:160], 'basis': 'envelope'}
    if peer:
        return {'kind': 'service' if peer.lower().startswith(('wt:', 'wt-', 'watchtower')) else 'session', 'name': peer[:160], 'basis': 'peer'}
    origin = event.get('turnOrigin')
    if event.get('wakeupSource') or origin == 'scheduled':
        return {'kind': 'service', 'name': str(event.get('wakeupSource') or 'Routine')[:160], 'basis': 'origin'}
    if event.get('isMeta') and origin not in ('peer', 'system'):
        return None
    if origin in ('peer', 'system') or event.get('isSidechain'):
        return {'kind': 'unknown', 'name': '', 'basis': 'origin'}
    return {'kind': 'human', 'name': '', 'basis': 'typed'}


def record(meta, text, timestamp, event=None):
    """Consume one user text from the owning transcript only; never descendants."""
    meta['driver_version'] = VERSION
    driver = classify(text, event)
    ts = epoch(timestamp)
    if not driver or not math.isfinite(ts) or ts <= 0:
        return
    if driver['kind'] == 'human':
        meta['last_owner_input_at'] = max(ts, meta.get('last_owner_input_at') or 0)
    if ts >= (meta.get('turn_driver') or {}).get('at', 0):
        meta['turn_driver'] = {**driver, 'at': ts}


def fields(meta):
    return {'turn_driver': meta.get('turn_driver'),
            'last_owner_input_at': meta.get('last_owner_input_at') or 0}
