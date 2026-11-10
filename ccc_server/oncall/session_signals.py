# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Conservative idle-turn questions, with bounded, persistent transcript caching."""
import hashlib
import json
import re
from pathlib import Path

_ASK = re.compile(r"\b(?:shall I|should I|should we|do you want|would you like|can you|could you|which .{0,80}(?:prefer|want|choose)|what do you (?:think|prefer))\b", re.I)


def question(store, session_id, transcript):
    if not transcript:
        return None
    path = Path(transcript)
    try:
        stat = path.stat()
        stamp = [str(path), stat.st_mtime_ns, stat.st_size]
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
    prose = last.split('<session-state>', 1)[0].strip()
    tail = prose.rsplit('\n\n', 1)[-1].strip()
    signal = None
    if (tail.endswith('?') and len(tail) <= 1200 and _ASK.search(tail)
            and not any(x in tail for x in ('```', '> ', '`'))):
        digest = hashlib.sha256((turn + last).encode()).hexdigest()[:24]
        signal = {'kind': 'plain_question', 'stamp': digest, 'body': tail, 'meta': {}}
    store.kv_set(key, {'stamp': stamp, 'signal': signal})
    return signal


def observe(eng, current):
    """Only live idle/approval candidates reach the dependency adapters."""
    if not eng.active() or eng.deps is None:
        return
    with eng._lock:
        live = set()
        enabled = (eng.config().get("alerts") or {}).get("decisions", True)
        for sid, row in (current or {}).items():
            signal = None
            if row.get('needs_approval'):
                signal = eng.deps.permission(sid)
            elif row.get('state') == 'idle' and not row.get('question_waiting'):
                signal = eng.deps.question(sid, eng.store)
            if not signal:
                continue
            kind = signal['kind']
            key = f"session:{sid}:{kind}:{signal['stamp']}"
            live.add(key)
            if not enabled:
                continue
            from . import alerts
            alerts.raise_event(eng.store, kind=kind, key=key, session_id=sid,
                               title='Agent ' + sid[:8], body=signal['body'],
                               meta=signal['meta'], now=eng.clock())
        for alert in eng.store.alerts(state='open'):
            if alert['kind'] in ('plain_question', 'permission') and alert['key'] not in live:
                eng.store.alert_close(alert['id'], 'cleared', now=eng.clock())
