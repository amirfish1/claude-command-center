"""Durable, bounded escalation. Channel ownership is also the reply authority."""
import json
import re
from datetime import datetime, timezone

from . import alerts, calls
from .store import Outbound, Receipt

BATCH = 50
KINDS = {'decision', 'permission', 'question', 'plain_question', 'stopped', 'report', 'send', 'fixed'}


def validate(cfg):
    def require(condition):
        if not condition:
            raise ValueError("Invalid escalation settings")

    try:
        people = cfg.get('people', [])
        steps = cfg.get('escalation', [])
        require(isinstance(people, list) and len(people) <= 32)
        require(isinstance(steps, list) and len(steps) <= 32)
        ids, assigned = set(), set()
        channels = {c['id'] for c in cfg.get('channels', [])}
        for p in people:
            require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}', p['id']) and p['id'] not in ids | {'owner', 'oncall'})
            ids.add(p['id'])
            require(p['channels'] and set(p['channels']) <= channels)
            require(not assigned.intersection(p['channels']))
            assigned.update(p['channels'])
            require(set(p.get('may_answer', ['decision', 'permission'])) <= KINDS)
            q = p.get('quiet_hours')
            if q:
                require(all(re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', q[k]) for k in ('start', 'end')))
        last = -1
        for s in steps:
            require(isinstance(s['after'], (int, float)) and 0 <= s['after'] <= 604800 and s['after'] >= last)
            last = s['after']
            require(s.get('person', 'owner') in ids | {'owner', 'oncall'})
            require(s['channels'] and set(s['channels']) <= channels)
            if s.get('person', 'owner') not in ('oncall', 'owner'):
                p = next(p for p in people if p['id'] == s['person'])
                require(set(s['channels']) <= set(p['channels']))
            if s.get('person', 'owner') == 'owner':
                require(not assigned.intersection(s['channels']))
        rota = cfg.get('rota') or {}
        require(set(rota.get('people', [])) <= ids | {'owner'})
        require(len(set(rota.get('people', []))) == len(rota.get('people', [])))
        require(rota.get('mode', 'weekly') in ('weekly', 'hours'))
        require(0 <= int(rota.get('weekday', 0)) <= 6)
        require(re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', rota.get('time', '09:00')))
        occupied = set()
        for h in rota.get('hours', []):
            require(h['person'] in ids | {'owner'} and 0 <= h['start'] < h['end'] <= 24)
            slots = set(range(h['start'], h['end']))
            require(not occupied.intersection(slots))
            occupied.update(slots)
        override = rota.get('override')
        if override:
            require(override['person'] in ids | {'owner'} and float(override['until']) > 0)
    except (AssertionError, KeyError, TypeError, ValueError):
        return ['Invalid escalation, person or rota settings. Check channel ownership, times and answer permissions.']
    return []


def who(cfg, now):
    rota = cfg.get('rota') or {}
    override = rota.get('override') or {}
    if override.get('until', 0) > now:
        return override['person']
    dt = datetime.fromtimestamp(now, timezone.utc)
    if rota.get('mode') == 'hours':
        return next((h['person'] for h in rota.get('hours', []) if h['start'] <= dt.hour < h['end']), 'owner')
    names = rota.get('people', [])
    hh, mm = map(int, rota.get('time', '09:00').split(':'))
    # Monday 1970-01-05 anchors an unambiguous weekly rotation in UTC.
    anchor = 345600 + int(rota.get('weekday', 0)) * 86400 + hh * 3600 + mm * 60
    return names[int((now - anchor) // 604800) % len(names)] if names else 'owner'


def person(cfg, cid):
    return next((p for p in cfg.get('people', []) if cid in p['channels']), None)


def authorized(cfg, cid, alert):
    p = person(cfg, cid)
    return p is None or alert['kind'] in p.get('may_answer', ['decision', 'permission'])


def init(store):
    store._db.executescript('''
    CREATE TABLE IF NOT EXISTS oncall_call_attempts (at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS oncall_call_time ON oncall_call_attempts(at);
    CREATE TABLE IF NOT EXISTS escalation_jobs (
      alert_id INTEGER PRIMARY KEY, plan TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0,
      due REAL NOT NULL, origin REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS escalation_due ON escalation_jobs(done,due,alert_id);
    CREATE TABLE IF NOT EXISTS escalation_sends (
      alert_id INTEGER NOT NULL, step INTEGER NOT NULL, channel TEXT NOT NULL,
      state TEXT NOT NULL, at REAL NOT NULL,
      PRIMARY KEY(alert_id,step,channel));
    CREATE INDEX IF NOT EXISTS escalation_send_time ON escalation_sends(at);
    CREATE TABLE IF NOT EXISTS escalation_notices (
      alert_id INTEGER NOT NULL, channel TEXT NOT NULL, name TEXT NOT NULL, due REAL NOT NULL,
      PRIMARY KEY(alert_id,channel));
    CREATE INDEX IF NOT EXISTS escalation_notice_due ON escalation_notices(due);
    ''')


def enroll(cfg, store, pending, now):
    for a in pending:
        if not calls.escalates(cfg, a):
            continue
        plan = []
        for step in cfg.get('escalation', []):
            step = dict(step)
            name = step.get('person', 'owner')
            if name == 'oncall':
                name = who(cfg, now)
                p = next((p for p in cfg.get('people', []) if p['id'] == name), None)
                step['channels'] = p['channels'] if p else step['channels']
            step['person'] = name
            step['channels'] = [cid for cid in step['channels'] if authorized(cfg, cid, a)]
            if step['channels']:
                plan.append(step)
        if not plan and cfg.get('escalation'):
            primary = [c['id'] for c in cfg.get('channels', []) if not person(cfg, c['id'])]
            if primary:
                plan = [{'person': 'owner', 'channels': primary, 'after': 0}]
        if plan:
            store._run('INSERT OR IGNORE INTO escalation_jobs(alert_id,plan,due,origin) VALUES(?,?,?,?)',
                       (a['id'], json.dumps(plan), now if (a.get('meta') or {}).get('skip_wait') else now + plan[0]['after'], now))


def _send(store, a, step, chan, msg, now):
    previous = store._one('SELECT * FROM escalation_sends WHERE alert_id=? AND step=? AND channel=?',
                          (a['id'], step, chan.id))
    if previous and getattr(chan, 'type', '') == 'call':
        return True
    if previous and (previous['state'] != 'failed' or now - previous['at'] < alerts.RETRY_AFTER_S):
        return previous['state'] != 'failed'
    store._run('INSERT OR REPLACE INTO escalation_sends VALUES(?,?,?,?,?)',
               (a['id'], step, chan.id, 'uncertain', now))
    store.delivery_add(a['id'], chan.id, getattr(chan, 'owner', ''),
                       Receipt(False, error='Escalation outcome unknown. No automatic retry.', uncertain=True), now=now)
    attempt = store.delivery_latest(a['id'], chan.id)
    if getattr(chan, 'type', '') == 'call':
        store._run('INSERT INTO oncall_call_attempts VALUES(?)', (now,))
    receipt = chan.send(msg)  # exceptions leave durable uncertainty, never a silent retry
    store.delivery_finish(attempt['id'], receipt)
    state = 'sent' if receipt.ok else 'uncertain' if receipt.uncertain else 'failed'
    store._run('UPDATE escalation_sends SET state=? WHERE alert_id=? AND step=? AND channel=?',
               (state, a['id'], step, chan.id))
    return state != 'failed'


def deliver(cfg, store, chans, pending, now):
    if not cfg.get('escalation'):
        return 0
    # Rebase remaining delays on a backward wall-clock correction. Forward jumps
    # make work due, but never replay several steps for one alert in one tick.
    last = store.kv_get('escalation_clock', now)
    if now < last:
        store._run('UPDATE escalation_jobs SET due=due+?,origin=origin+? WHERE done=0', (now-last, now-last))
    store.kv_set('escalation_clock', now)
    enroll(cfg, store, pending, now)
    if store.kv_get('paused', False):
        return 0
    cap = int(cfg.get('hourly_cap') or alerts.HOURLY_CAP)
    used = len(store._all('SELECT id FROM deliveries WHERE sent_at>=? LIMIT ?', (now-3600, cap)))
    count = 0
    targets = {c.id: c for c in alerts.alert_channels(cfg, chans, include_calls=True)}
    for notice in store._all('SELECT * FROM escalation_notices WHERE due<=? ORDER BY due LIMIT ?', (now, BATCH)):
        cid = notice['channel']
        if cid in targets and targets[cid].type == 'call':
            store._run('DELETE FROM escalation_notices WHERE alert_id=? AND channel=?', (notice['alert_id'], cid))
            continue
        a = store.alert(notice['alert_id'])
        if not a or a['state'] == 'cleared':
            store._run('DELETE FROM escalation_notices WHERE alert_id=? AND channel=?', (notice['alert_id'], cid))
            continue
        if a['state'] == 'open':
            store._run('UPDATE escalation_notices SET due=? WHERE alert_id=? AND channel=?', (now+3, a['id'], cid))
            continue
        p = person(cfg, cid)
        if used >= cap or cid not in targets or alerts.in_quiet_hours({'quiet_hours': (p or {}).get('quiet_hours', cfg.get('quiet_hours'))}, now):
            store._run('UPDATE escalation_notices SET due=? WHERE alert_id=? AND channel=?', (now+3, notice['alert_id'], cid))
            continue
        try:
            _send(store, a, -1, targets[cid], Outbound(f"#{a['id']} handled by {notice['name']}.", thread=a['token']), now)
        finally:
            # One notice attempt, including uncertain outcomes.
            store._run('DELETE FROM escalation_notices WHERE alert_id=? AND channel=?', (a['id'], cid))
        used += 1
    rows = store._all('SELECT * FROM escalation_jobs WHERE done=0 AND due<=? ORDER BY due,alert_id LIMIT ?', (now, BATCH))
    for row in rows:
        a = store.alert(row['alert_id'])
        if not a or a['state'] != 'open':
            store._run('UPDATE escalation_jobs SET done=1 WHERE alert_id=?', (row['alert_id'],))
            continue
        plan, i = json.loads(row['plan']), row['step']
        # A quiet or failed earlier recipient must not hold up the backup.
        while i + 1 < len(plan) and now >= row['origin'] + plan[i + 1]['after']:
            i += 1
        step = plan[i]
        complete = True
        for cid in step['channels']:
            if not authorized(cfg, cid, a):
                continue
            p = person(cfg, cid)
            if (p['id'] if p else 'owner') != step['person']:
                continue
            entry = next((c for c in cfg['channels'] if c['id'] == cid), {})
            is_call = entry.get('type') == 'call'
            if is_call and not calls.eligible(store, entry, a, i, now):
                continue
            if is_call and not calls.allowed(cfg, store, entry, a, row, i, now):
                complete = False
                continue
            quiet = (p or {}).get('quiet_hours', cfg.get('quiet_hours'))
            if is_call and str(entry.get('during_quiet', 'false')).lower() == 'true':
                quiet = None
            if cid not in targets or alerts.in_quiet_hours({'quiet_hours': quiet}, now) or used >= cap:
                complete = False
                continue
            subject, text, choices = alerts.render(a)
            if is_call:
                for value in (a.get('ref'), a.get('session_id'), a.get('token')):
                    if value:
                        text = text.replace(value, '')
            before = store._one('SELECT * FROM escalation_sends WHERE alert_id=? AND step=? AND channel=?', (a['id'], i, cid))
            try:
                ok = _send(store, a, i, targets[cid], Outbound(text, subject=subject, thread=a['token'], choices=choices), now)
            except Exception as exc:
                store.log('escalation', f"#{a['id']} via {cid}: outcome unknown ({type(exc).__name__})", now=now)
                ok = True
            after = store._one('SELECT * FROM escalation_sends WHERE alert_id=? AND step=? AND channel=?', (a['id'], i, cid))
            if after and (not before or after['at'] != before['at']):
                used += 1
                count += int(after['state'] == 'sent')
            complete &= (ok or is_call)
        if complete:
            store.alert_mark_sent(a['id'], now=now)
            i += 1
            due = row['origin'] + plan[i]['after'] if i < len(plan) else now
            if (a.get('meta') or {}).get('skip_wait'):
                due = now
            store._run('UPDATE escalation_jobs SET step=?,due=?,done=? WHERE alert_id=?', (i, due, int(i == len(plan)), a['id']))
        else:
            store._run('UPDATE escalation_jobs SET due=? WHERE alert_id=?', (now + 3, a['id']))
    return count


def answered(cfg, store, chans, a, cid, now):
    p = person(cfg, cid)
    name = p['id'] if p else 'owner'
    store.kv_set(f"answered_by:{a['id']}", name)
    recipients = store._all('SELECT DISTINCT channel FROM escalation_sends WHERE alert_id=? AND step>=0', (a['id'],))
    for row in recipients:
        other = row['channel']
        if other != cid:
            store._run('INSERT OR REPLACE INTO escalation_notices VALUES(?,?,?,?)', (a['id'], other, name, now))
