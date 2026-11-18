"""Observed turn links in the session graph, indexed only on explicit demand.

No prompt text is retained. A recorded input is a wake observation, not proof
that a new process was launched. Usage is measured tokens, never a dollar bill.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import threading

from ccc_server import core as _core
from ccc_server import session_driver as driver

_scan_cache = {}
_scan_lock = threading.RLock()
_journal_cache = {}


def index_messages(graph, sessions):
    with _scan_lock:
        return _index_messages(graph, sessions)


def _index_messages(graph, sessions):
    """Read existing delivery journals; a send is never counted as a wake."""
    import sender_envelope
    records = {}
    for base in (sender_envelope._ccc_state(), sender_envelope._wt_state()):
        for path in (base / 'attribution').glob('????-??.jsonl'):
            stat = path.stat()
            key = (str(graph._path), str(path))
            state = _journal_cache.get(key)
            token = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if not state or state['token'][0] != stat.st_ino or stat.st_size < state['offset']:
                state = {'offset': 0, 'token': token, 'records': {}}
            with path.open('rb') as handle:
                handle.seek(state['offset'])
                while True:
                    line = handle.readline()
                    if not line or not line.endswith(b'\n'):
                        break
                    state['offset'] = handle.tell()
                    try:
                        row = json.loads(line)
                        attr = row['attr']
                        author = attr['author']
                        receiver = (attr.get('delivered_recipient') or attr.get('intended_recipient') or {}).get('id')
                        if not receiver or not attr.get('message_id'):
                            continue
                        ident = 'message:' + attr['message_id'] + ':' + str(row.get('event') or '') + ':' + str(row.get('transport_id') or '')
                        state['records'][ident] = {'id': ident, 'type': 'message', 'receiver': receiver,
                            'sender': author.get('id') or '', 'sender_kind': author.get('kind') or 'unknown',
                            'sender_name': author.get('name_at_send') or '', 'at': driver.epoch(row.get('at')),
                            'message_id': attr['message_id'], 'status': row.get('event') or 'unknown',
                            'tokens': None, 'basis': attr.get('basis')}
                    except (ValueError, KeyError, TypeError):
                        continue
            state['token'] = token
            _journal_cache[key] = state
            records.update({k: e for k, e in state['records'].items() if e['receiver'] in sessions})
    graph.record_activity(list(records.values()))


def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(str(block.get('text') or '') for block in content or []
                     if isinstance(block, dict) and block.get('type') in ('text', 'input_text'))


def wake_event(sid, text, timestamp, event, key):
    """Shared driver semantics, enriched with durable envelope identity."""
    who = driver.classify(text, event)
    at = driver.epoch(timestamp)
    if not who or not math.isfinite(at) or at <= 0:
        return None
    import sender_envelope
    peer = event.get('peer') or {}
    attr = event.get('attribution') or sender_envelope.from_text(text)
    if not attr and peer.get('msg_id'):
        attr = sender_envelope.lookup(message_id=peer['msg_id'], recipient=sid)
    if isinstance(attr, dict):
        author = attr.get('author') or {}
        # Service body markers override legacy unknown envelopes, as they do
        # in the sidebar; a known author supplies its durable session id.
        if author.get('kind') not in ('unknown', None):
            who = {**who, 'id': author.get('id') or '',
                   'name': author.get('name_at_send') or who.get('name', '')}
    message_id = (attr or {}).get('message_id') or peer.get('msg_id') or ''
    clean = sender_envelope.strip_header(text).lstrip()
    cause = {'human': 'owner-input', 'session': 'session-message',
             'background': 'task-notification', 'service': 'service-input'}.get(who['kind'], 'unknown')
    if clean.startswith('[WATCHTOWER] A new comment was added to your claimed ticket '):
        cause = 'ticket-comment'
    ident = str(who.get('id') or '')
    if not ident and who['kind'] == 'session':
        # Legacy transport headers used a session UUID as the display name.
        import re
        if re.fullmatch(r'[0-9a-fA-F-]{36}', who.get('name', '')):
            ident = who['name']
    return {'id': sid + ':' + str(message_id or key), 'type': 'wake', 'version': driver.VERSION,
            'receiver': sid, 'sender': ident, 'sender_kind': who['kind'],
            'sender_name': who.get('name') or '', 'basis': who.get('basis'),
            'at': at, 'message_id': message_id or None,
            'cause': cause,
            'tokens': None, 'usage_status': 'unobserved'}


def index_transcript(graph, sid, path):
    """Incremental native/ACP scan; unchanged files incur no reads."""
    path = __import__('pathlib').Path(path)
    stat = path.stat()
    cache_key = (str(graph._path), sid, str(path))
    with _scan_lock:
        state = _scan_cache.get(cache_key)
        token = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if state and state['token'] == token:
            return
        if not state or state['token'][0] != token[0] or stat.st_size <= state['offset']:
            state = {'offset': 0, 'token': token, 'current': None, 'events': {},
                     'claude_usage': {}, 'totals': None, 'last_prompt': None}
        changed = {}
        with path.open('rb') as handle:
            handle.seek(state['offset'])
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b'\n'):
                    break
                state['offset'] = handle.tell()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get('type')
                payload = event.get('payload') or {}
                text = None
                native = event
                if kind == 'user':
                    text = _text((event.get('message') or {}).get('content'))
                elif kind == 'user_text':
                    text = event.get('text') or ''
                elif kind == 'attachment':
                    queued = _core._extract_queued_command_prompt(event)
                    if queued:
                        text = queued.get('text') or ''
                        native = {**event, 'peer': queued.get('peer'),
                                  'origin': (event.get('attachment') or {}).get('origin')}
                elif kind == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
                    text = _text(payload.get('content'))
                elif kind == 'event_msg' and payload.get('type') == 'user_message':
                    text = payload.get('message') or ''
                if text:
                    at = event.get('timestamp') or event.get('ts')
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    prompt = (digest, driver.epoch(at))
                    # Some Codex versions mirror response_item as event_msg.
                    previous = state['last_prompt']
                    duplicate = (previous and previous[0] == digest and
                                 abs(previous[1] - prompt[1]) < 1 and previous[2] != kind)
                    if not duplicate:
                        wake = wake_event(sid, text, at, native, event.get('uuid') or hashlib.sha256((str(at) + digest).encode()).hexdigest())
                        if wake:
                            # Re-delivery of one envelope is one observed wake.
                            key = wake['id']
                            state['current'] = key
                            state['events'].setdefault(key, wake)
                            changed[key] = state['events'][key]
                            state['last_prompt'] = (*prompt, kind)
                usage = None
                if kind == 'assistant':
                    msg = event.get('message') or {}
                    raw = msg.get('usage') or {}
                    if raw:
                        # Streaming updates for one API request replace its
                        # prior counters instead of billing every content block.
                        request = msg.get('id') or event.get('requestId') or event.get('uuid') or offset
                        totals = {key: max(0, int(raw.get(key) or 0)) for key in
                                  ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')}
                        previous = state['claude_usage'].get(request, {})
                        usage = {'input': max(0, totals['input_tokens'] - previous.get('input_tokens', 0)),
                                 'output': max(0, totals['output_tokens'] - previous.get('output_tokens', 0)),
                                 'cache_read': max(0, totals['cache_read_input_tokens'] - previous.get('cache_read_input_tokens', 0)),
                                 'cache_write': max(0, totals['cache_creation_input_tokens'] - previous.get('cache_creation_input_tokens', 0))}
                        state['claude_usage'][request] = totals
                elif kind == 'event_msg' and payload.get('type') == 'token_count':
                    delta, totals = _core._codex_usage_delta_from_event(event, state['totals'])
                    state['totals'] = totals
                    if delta:
                        usage = {'input': max(0, delta['input_tokens'] - delta['cached_input_tokens']),
                                 'output': delta['output_tokens'], 'cache_read': delta['cached_input_tokens'], 'cache_write': 0}
                current = state['events'].get(state['current'])
                if usage and current:
                    current['tokens'] = {key: (current['tokens'] or {}).get(key, 0) + value
                                         for key, value in usage.items()}
                    current['usage_status'] = 'measured'
                    changed[current['id']] = current
        state['token'] = token
        _scan_cache[cache_key] = state
        if changed:
            graph.record_activity(list(changed.values()))


def ticket_activity(ref, *, after=0, before=0):
    from ccc_server import idea_tickets
    data = idea_tickets.load_state()
    if ref not in data['tickets']:
        return {'ok': True, 'ref': ref, 'sessions': [], 'events': [], 'gaps': []}
    graph = idea_tickets._graph()
    if data.get('schema') != 2:
        return {'ok': False, 'error': 'Session activity will be available after the pending update. Existing ticket history remains available.'}
    sessions = data['tickets'][ref]['sessions']
    gaps = []
    for sid in sessions:
        path = idea_tickets._session_transcript_path(sid)
        if not path and _core._is_devin_cli_session(sid):
            candidate = _core._acp_transcript_path('devin', _core._devin_cli_raw_id(sid))
            if candidate.is_file():
                path = candidate
        if not path:
            gaps.append({'session_id': sid, 'reason': 'Transcript unavailable'})
            continue
        try:
            index_transcript(graph, sid, path)
        except (OSError, ValueError) as exc:
            gaps.append({'session_id': sid, 'reason': type(exc).__name__})
    index_messages(graph, set(sessions))
    records = sorted((e for e in graph.activity_snapshot(set(sessions))
                     if e['at'] >= after and (not before or e['at'] < before)), key=lambda e: e['at'])
    events = [e for e in records if e['type'] == 'wake']
    messages = [e for e in records if e['type'] == 'message']
    summaries = []
    for sid in sessions:
        own = [e for e in events if e['receiver'] == sid]
        hours, senders, tokens = Counter(), Counter(), Counter()
        for event in own:
            hour = datetime.fromtimestamp(event['at'], timezone.utc).strftime('%Y-%m-%dT%H:00:00Z')
            hours[hour] += 1
            senders[(event['sender_kind'], event['sender'], event['sender_name'])] += 1
            tokens.update(event.get('tokens') or {})
        summaries.append({'session_id': sid, 'wakes': len(own), 'hours': dict(hours),
                          'drivers': [{'kind': k, 'id': i, 'name': n, 'count': count}
                                      for (k, i, n), count in senders.most_common()],
                          'tokens': dict(tokens) if tokens else None,
                          'unmeasured_turns': sum(e.get('tokens') is None for e in own),
                          'latest': own[-1] if own else None})
    return {'ok': True, 'ref': ref, 'sessions': summaries, 'events': events, 'messages': messages, 'gaps': gaps,
            'links': {sid: link for sid, link in graph.lineage_snapshot().items() if sid in sessions}}
