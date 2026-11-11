# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
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
_NO_STEP = re.compile(r"^(?:$|(?:none|nothing|no|neither|n/?a|null|nil|not (?:needed|required|applicable))\b)", re.I)
_ASK = re.compile(r"\b(?:shall I|should I|should we|do you want|would you like|can you|could you|which .{0,80}(?:prefer|want|choose)|what do you (?:think|prefer))\b", re.I)


def _epoch(iso):
    try:
        return calendar.timegm(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


def question(store, session_id, transcript, since=0, notify_idle=False):
    """The closing question of an idle transcript, or None.

    ``since`` (epoch seconds) drops a transcript last written before On-call
    was turned on, so turning it on never replays old questions.
    """
    if not transcript:
        return None
    path = Path(transcript)
    try:
        stat = path.stat()
        if since and stat.st_mtime < since:
            return None
        stamp = [str(path), stat.st_mtime_ns, stat.st_size, notify_idle]
        key = 'question-cache:' + session_id
        cached = store.kv_get(key) or {}
        if cached.get('stamp') == stamp:
            return cached.get('signal')
        with path.open('rb') as stream:
            start = max(0, stat.st_size - 65536)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.read(65536).splitlines()
    except OSError:
        return None
    last, turn = '', ''
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict):
            continue
        payload = row.get('payload') or {}
        message = row.get('message') or {}
        if row.get('type') == 'response_item':
            message = payload
        role = message.get('role') or row.get('type')
        if role == 'user':
            last = ''
        elif role == 'assistant':
            content = message.get('content') or []
            last = content if isinstance(content, str) else '\n'.join(
                str(c.get('text') or '') for c in content if c.get('type') in ('text', 'output_text'))
            turn = str(row.get('uuid') or row.get('timestamp') or '')
        elif row.get('type') == 'event_msg' and payload.get('type') == 'user_message':
            last = ''
    # Ignore machine footers and quoted/code examples. Only a closing paragraph
    # explicitly addressed to the owner qualifies, not every question mark.
    prose, _, footer = last.partition('<session-state>')
    prose = prose.strip()
    tail = prose.rsplit('\n\n', 1)[-1].strip()
    steps = _FOOTER_STEP.findall(footer)
    step = steps[-1].strip() if steps else ''
    signal = None
    if not _NO_STEP.match(step):
        tail = step[:1200]
    elif steps or not (tail.endswith('?') and len(tail) <= 1200 and _ASK.search(tail)
                       and not any(x in tail for x in ('```', '> ', '`'))):
        # A footer that says nothing is needed outranks a question mark above it.
        tail = ''
    if not tail and notify_idle and last.strip():
        tail = 'Turn finished. ' + (prose or 'Open the session for details.')[:1000]
    if tail:
        digest = hashlib.sha256((turn + last).encode()).hexdigest()[:24]
        signal = {'kind': 'plain_question', 'stamp': digest, 'body': tail, 'meta': {}}
    store.kv_set(key, {'stamp': stamp, 'signal': signal})
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
        idle = getattr(eng, "_idle_seen", None)
        if idle is None:
            idle = eng._idle_seen = {}
        prompts, candidates = set(), set()

        def raise_signal(sid, signal):
            key = f"session:{sid}:{signal['kind']}:{signal['stamp']}"
            if enabled:
                alerts.raise_event(eng.store, kind=signal['kind'], key=key, session_id=sid,
                                   title='Agent ' + sid[:8], body=signal['body'],
                                   meta=signal['meta'], now=now)
            return key

        for sid, row in (current or {}).items():
            if row.get('needs_approval'):
                signal = eng.deps.permission(sid)
                if signal:
                    prompts.add(raise_signal(sid, signal))
            elif row.get('state') == 'idle' and not row.get('question_waiting'):
                candidates.add(sid)
                seen = idle.setdefault(sid, {'at': now, 'done': False})
                if seen['done'] or not enabled or now - seen['at'] < GRACE_S:
                    continue
                seen['done'] = True
                signal = eng.deps.question(sid, eng.store, _epoch(eng.store.kv_get("enabled_at")))
                if signal:
                    raise_signal(sid, signal)
        for sid in [s for s in idle if s not in candidates]:
            del idle[sid]
        for alert in eng.store.alerts(state='open'):
            gone = ((alert['kind'] == 'permission' and alert['key'] not in prompts)
                    or (alert['kind'] == 'plain_question'
                        and alert.get('session_id') not in candidates))
            if gone:
                eng.store.alert_close(alert['id'], 'cleared', now=now)
