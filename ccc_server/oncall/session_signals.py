# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Conservative idle-turn questions, with bounded, persistent transcript caching."""
import calendar
import hashlib
import json
import re
import time
from pathlib import Path

# A session must sit idle this long before its closing question is read. It
# keeps a person who is at the keyboard from being messaged about every turn,
# and it means one transcript lookup per idle period, not one per tick.
GRACE_S = 120

# CCC's own session footer names the step that needs the person, when any.
_FOOTER_STEP = re.compile(r"^NEXT_STEP_USER[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
_FOOTER_ACTION = re.compile(r"^NEXT_ACTION[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
_NO_STEP = re.compile(r"^(?:$|(?:none|nothing|no|neither|n/?a|null|nil|not (?:needed|required|applicable))\b)", re.I)
_ASK = re.compile(r"\b(?:shall I|should I|should we|do you want|would you like|can you|could you|which .{0,80}(?:prefer|want|choose)|what do you (?:think|prefer))\b", re.I)


def _choices(footer):
    """Up to twelve (label, reply) answer choices from the footer's NEXT_ACTION.

    Anything malformed, empty or longer than twelve yields no choices.
    """
    found = _FOOTER_ACTION.findall(footer)
    try:
        data = json.loads(found[-1]) if found else None
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not 1 <= len(data) <= 12:
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            return []
        label, reply = item.get('label'), item.get('reply')
        if not (isinstance(label, str) and isinstance(reply, str)
                and label.strip() and reply.strip()):
            return []
        out.append([' '.join(label.split())[:24], reply.strip()[:400]])
    return out


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


def context(store, session_id, transcript):
    """Bounded head/tail read, persisted by path, mtime and size across restarts."""
    if not transcript:
        return {}
    path = Path(transcript)
    try:
        stat = path.stat()
        stamp = [str(path), stat.st_mtime_ns, stat.st_size]
        key = 'context-cache:' + session_id
        cached = store.kv_get(key) or {}
        if cached.get('stamp') == stamp:
            return cached.get('context') or {}
        with path.open('rb') as stream:
            head = stream.read(65536).splitlines()
            start = max(0, stat.st_size - 65536)
            stream.seek(start)
            if start:
                stream.readline()
            tail = stream.read(65536).splitlines()
    except OSError:
        return {}
    first, last, structured, structured_id = '', '', None, ''
    for lines, is_head in ((head, True), (tail, False)):
        for line in lines:
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict):
                continue
            payload = row.get('payload') or {}
            message = payload if row.get('type') == 'response_item' else row.get('message') or {}
            if not isinstance(message, dict) or not isinstance(payload, dict):
                continue
            role = message.get('role') or row.get('type')
            content = message.get('content') or []
            text = content if isinstance(content, str) else '\n'.join(
                str(c.get('text') or '') for c in content
                if isinstance(c, dict) and c.get('type') in ('text', 'input_text', 'output_text')) if isinstance(content, list) else ''
            if row.get('type') == 'event_msg' and payload.get('type') == 'user_message':
                role, text = 'user', str(payload.get('message') or '')
            if is_head:
                if role == 'user' and text and not first:
                    first = ' '.join(text.split())[:160]
                continue
            if role == 'user':
                # Tool results do not start a new human turn.
                if text:
                    last, structured = '', None
            elif role == 'assistant' and text:
                last, structured = text, None
            if payload.get('type') == 'function_call_output' and payload.get('call_id') == structured_id:
                structured = None
            blocks = content if isinstance(content, list) else []
            for block in blocks + ([payload] if row.get('type') == 'response_item' else []):
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_result' and block.get('tool_use_id') == structured_id:
                    structured = None
                if str(block.get('name') or '').split('.')[-1] not in ('AskUserQuestion', 'request_user_input'):
                    continue
                data = block.get('input') or block.get('arguments') or {}
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        continue
                if isinstance(data, dict):
                    structured = structured_question(data)
                    structured_id = block.get('id') or block.get('call_id') or ''
    result = {'first': first, 'last': last[-12000:], 'structured': structured, 'mtime': stat.st_mtime}
    store.kv_set(key, {'stamp': stamp, 'context': result})
    return result


def structured_question(data):
    from .clarity import choices
    questions = data.get('questions') or []
    if not isinstance(questions, list) or not questions:
        return None
    bodies, options = [], []
    for q in questions:
        if not isinstance(q, dict) or not isinstance(q.get('question'), str):
            return None
        bodies.append(q['question'])
        opts = choices(q.get('options'))
        if len(questions) == 1 and not q.get('multiSelect'):
            options = opts
            for i, opt in enumerate(q.get('options') or [], 1):
                if isinstance(opt, dict) and opt.get('description'):
                    bodies.append(f"{i}. {opt.get('label', '')} — {opt['description']}")
        elif opts:
            bodies.extend(f'{i}. {name}' for i, (name, _) in enumerate(opts, 1))
    meta = {'choices': options} if options else {}
    if len(questions) != 1 or any(q.get('multiSelect') for q in questions):
        meta = {'at_keyboard': True}
    return {'body': '\n'.join(bodies), 'meta': meta}


def question(store, session_id, transcript, since=0, notify_idle=False):
    info = context(store, session_id, transcript)
    if since and info.get('mtime', 0) < since:
        return None
    if info.get('structured'):
        return structured_signal(info['structured'])
    return from_message(info.get('last', ''), notify_idle=notify_idle)


def structured_signal(signal):
    """Use the same stable incident identity for native and transcript questions."""
    digest = hashlib.sha256(json.dumps(signal, sort_keys=True).encode()).hexdigest()[:24]
    return {'kind': 'plain_question', 'stamp': digest, **signal}


def from_message(last, turn='', notify_idle=False):
    """Same conservative parser for a hook's final message and a transcript."""
    # Ignore machine footers and quoted/code examples. Only a closing paragraph
    # explicitly addressed to the owner qualifies, not every question mark.
    prose, _, footer = last.partition('<session-state>')
    prose = prose.strip()
    tail = prose.rsplit('\n\n', 1)[-1].strip()
    from .clarity import numbered
    numbered_choices = numbered(prose)
    if numbered_choices:
        tail = prose
    steps = _FOOTER_STEP.findall(footer)
    step = steps[-1].strip() if steps else ''
    signal = None
    if not _NO_STEP.match(step):
        tail = ((prose[-1200:] + '\n\n' + step) if prose and step not in prose else step)[:1800]
    elif steps or not ((tail.endswith('?') or numbered_choices) and len(tail) <= 1800 and (_ASK.search(tail) or numbered_choices)
                       and not any(x in tail for x in ('```', '> ', '`'))):
        # A footer that says nothing is needed outranks a question mark above it.
        tail = ''
    if not tail and notify_idle and last.strip():
        tail = 'Turn finished. ' + (prose or 'Open the session for details.')[:1000]
    if tail:
        # Keyed on the question text alone: the same session repeating an
        # unchanged question (a stale footer) must not alert again each turn.
        digest = hashlib.sha256(tail.encode()).hexdigest()[:24]
        choices = (_choices(footer) if not _NO_STEP.match(step) else []) or numbered_choices
        signal = {'kind': 'plain_question', 'stamp': digest, 'body': tail,
                  'meta': {'choices': choices} if choices else {}}
    return signal


def observe(eng, current):
    """Raise and clear plain-question and permission alerts from session states.

    A permission row is probed on every call (rare, and already in memory).
    An idle row is probed once, after it has stayed idle for GRACE_S.
    """
    if not eng.active() or eng.deps is None:
        return
    from . import alerts
    with eng._lock:
        now = eng.clock()
        enabled = (eng.config().get("alerts") or {}).get("decisions", True)
        if not enabled:
            return
        idle = getattr(eng, "_idle_seen", None)
        if idle is None:
            idle = eng._idle_seen = {}
        prompts, candidates = set(), set()
        missing = getattr(eng, '_permission_missing', {})
        eng._permission_missing = missing

        from .clarity import label, unreadable

        def raise_signal(sid, signal):
            key = f"session:{sid}:{signal['kind']}:{signal['stamp']}"
            # The same words asked again after the owner answered are a new
            # question; one that only cleared unanswered is the same incident.
            base, n = key, 0
            while (signal['kind'] == 'plain_question' and (prior := eng.store.alert_by_key(key))
                   and prior['state'] == 'answered'):
                n += 1
                key = f"{base}:{n}"
            if enabled:
                alerts.raise_event(eng.store, kind=signal['kind'], key=key, session_id=sid,
                                   title=label(eng.deps, sid), body=signal['body'],
                                   needs_reply=not (signal['meta'] or {}).get('at_keyboard'),
                                   meta=signal['meta'], now=now)
            return key

        from .agent_wait import active_sessions
        hook_sessions = active_sessions(eng.store)
        for sid, row in (current or {}).items():
            if sid in hook_sessions:
                continue
            if row.get('needs_approval'):
                signal = eng.deps.permission(sid)
                if not signal:
                    if sid not in missing:
                        missing[sid] = now
                        continue
                    signal = {'kind': 'permission', 'stamp': 'unreadable:' + str(missing[sid]),
                              'body': unreadable('permission prompt'), 'meta': {'at_keyboard': True}}
                else:
                    missing.pop(sid, None)
                prompts.add(raise_signal(sid, signal))
            elif row.get('state') in ('idle', 'waiting') and not row.get('question_waiting'):
                candidates.add(sid)
                seen = idle.setdefault(sid, {'at': now, 'done': False})
                if seen['done'] or not enabled or now - seen['at'] < GRACE_S:
                    continue
                seen['done'] = True
                signal = eng.deps.question(sid, eng.store, _epoch(eng.store.kv_get("enabled_at")))
                if signal:
                    raise_signal(sid, signal)
        for sid in list(missing):
            if not (current or {}).get(sid, {}).get('needs_approval'):
                del missing[sid]
        for sid in [s for s in idle if s not in candidates]:
            del idle[sid]
        for alert in eng.store.alerts(state='open'):
            if (alert.get('meta') or {}).get('hook_wait'):
                continue  # the waiting hook owns expiry and collection
            gone = ((alert['kind'] == 'permission' and alert['key'] not in prompts)
                    or (alert['kind'] == 'plain_question'
                        and alert.get('session_id') not in candidates))
            if gone:
                eng.store.alert_close(alert['id'], 'cleared', now=now)
