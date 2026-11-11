"""Bounded, single-use phone replies for synchronous CLI hooks.

Mailboxes live only in this server process. A restart, expiry or cancellation
never falls through to another session transport. No permission rules change.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

from . import alerts

MAX_WAIT = 540
MAX_PENDING = 128
SID = re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
_INIT_LOCK = threading.Lock()


class Mailboxes:
    def __init__(self):
        self.lock = threading.RLock()
        self.rows = {}


def boxes(store):
    with _INIT_LOCK:
        if not hasattr(store, '_agent_mailboxes'):
            # A server restart loses every synchronous HTTP waiter. Old phone
            # alerts must not be replayed or redirected through native injection.
            for alert in store.alerts(state='open'):
                if (alert.get('meta') or {}).get('hook_wait'):
                    store.alert_close(alert['id'], 'cleared')
            store._agent_mailboxes = Mailboxes()
        return store._agent_mailboxes


def _finish(store, row, state):
    row['state'] = state
    row['event'].set()
    store.alert_close(row['alert_id'], 'answered' if state == 'collected' else 'cleared',
                      row.get('answer', '') if state == 'collected' else '')


def sweep(store):
    box = boxes(store)
    with box.lock:
        now = time.monotonic()
        for key, row in list(box.rows.items()):
            if now >= row['deadline']:
                if row['state'] in ('waiting', 'answered'):
                    _finish(store, row, 'expired')
                del box.rows[key]


def register(eng, body):
    sid, kind = body.get('session_id'), body.get('event')
    if not isinstance(sid, str) or not SID.fullmatch(sid) or kind not in ('Stop', 'PermissionRequest'):
        raise ValueError('Invalid hook session or event.')
    text = body.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 1800:
        raise ValueError('Hook text must be 1 to 1800 characters.')
    seconds = max(1, min(MAX_WAIT, int(body.get('seconds', 180))))
    with eng._lock:
        if not eng.active() or eng.store.kv_get('paused'):
            raise ValueError('On-call is off or paused.')
        if not (eng.config().get('alerts') or {}).get('decisions', True):
            raise ValueError('Decision alerts are off.')
        box = boxes(eng.store)
        with box.lock:
            sweep(eng.store)
            if len(box.rows) >= MAX_PENDING:
                raise ValueError('Too many waiting hooks.')
            # Never replace a permission hook or guess which simultaneous call
            # a session-level answer refers to. Each prompt gets its own ID.
            key, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            from .clarity import label
            from .session_signals import from_message
            parsed = from_message(text) if kind == 'Stop' else None
            alert, _ = alerts.raise_event(eng.store,
                kind='permission' if kind == 'PermissionRequest' else 'plain_question',
                key='hook:' + key, session_id=sid, title=label(eng.deps, sid), body=text,
                meta={**((parsed or {}).get('meta') or {}), 'hook_wait': key}, now=eng.clock())
            if kind == 'Stop':
                eng.store.kv_set('hook-last:' + sid, hashlib.sha256(text.encode()).hexdigest())
            box.rows[key] = {'sid': sid, 'kind': kind, 'digest': hashlib.sha256(token.encode()).digest(),
                             'event': threading.Event(), 'deadline': time.monotonic() + seconds,
                             'state': 'waiting', 'answer': '', 'alert_id': alert['id'], 'polling': False}
            return {'wait_id': key, 'token': token, 'alert_id': alert['id']}


def collect(eng, body):
    store, box = eng.store, boxes(eng.store)
    with box.lock:
        row = box.rows.get(body.get('wait_id'))
        token = str(body.get('token') or '')
        if (not row or row['sid'] != body.get('session_id') or
                not hmac.compare_digest(row['digest'], hashlib.sha256(token.encode()).digest())):
            raise ValueError('This reply does not belong to that waiting hook.')
        if row['polling'] or row['state'] not in ('waiting', 'answered'):
            raise ValueError('This hook reply was already collected or canceled.')
        row['polling'] = True
        remaining = max(0, row['deadline'] - time.monotonic())
    # No engine/box lock is held here. Other sessions and the channel loop run.
    row['event'].wait(remaining)
    with box.lock:
        if row['state'] == 'answered' and time.monotonic() < row['deadline']:
            _finish(store, row, 'collected')
            return {'answer': row['answer'], 'event': row['kind']}
        if row['state'] == 'waiting':
            _finish(store, row, 'expired')
        return {'answer': None, 'state': row['state']}


def cancel(eng, body):
    sid = body.get('session_id')
    if not isinstance(sid, str) or not SID.fullmatch(sid):
        raise ValueError('Invalid hook session.')
    box = boxes(eng.store)
    with box.lock:
        if not body.get('wait_id'):
            eng.store.kv_set('hook-last:' + sid, '')
        for key, row in box.rows.items():
            if row['sid'] == sid and (not body.get('wait_id') or body['wait_id'] == key):
                if row['state'] in ('waiting', 'answered'):
                    _finish(eng.store, row, 'canceled')
    return {}


def answer(store, alert, text):
    box = boxes(store)
    with box.lock:
        row = box.rows.get(alert['meta']['hook_wait'])
        if (not row or row['sid'] != alert['session_id'] or row['alert_id'] != alert['id']
                or row['state'] != 'waiting' or time.monotonic() >= row['deadline']):
            return False
        row['answer'] = text[:8000]
        row['state'] = 'answered'
        row['event'].set()
        return True


def active_sessions(store):
    box = boxes(store)
    with box.lock:
        now = time.monotonic()
        return {row['sid'] for row in box.rows.values()
                if row['state'] in ('waiting', 'answered') and now < row['deadline']}


def handled(store, sid, signal):
    """A duplicate native Stop marker must not re-alert the hook-owned turn."""
    return bool(signal) and store.kv_get('hook-last:' + sid) == hashlib.sha256(
        signal['body'].encode()).hexdigest()
