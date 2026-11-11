"""Bounded, private-by-default counts from retained On-call and queue records."""
from collections import Counter
from datetime import datetime, timedelta
import json
import sqlite3
import statistics

from . import alerts, wt as wtmod

ROW_LIMIT = 20000
TICKET_LIMIT = 2000
QUEUE_LIMIT = 16


def stamp(value):
    if isinstance(value, (float, int)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0


def windows(now, since=None, weeks=2):
    weeks = int(weeks)
    if not 1 <= weeks <= 52:
        raise ValueError('weeks must be between 1 and 52')
    end = datetime.fromtimestamp(now)
    start = datetime.fromisoformat(since.replace('Z', '+00:00')) if since else end - timedelta(days=7 * weeks)
    if start.tzinfo:
        end = datetime.fromtimestamp(now, start.tzinfo)
    if start >= end:
        raise ValueError('since must be before now')
    result = []
    for n in range(weeks):
        lo = start + timedelta(days=7*n)
        hi = min(lo + timedelta(days=7), end)
        if lo >= end:
            break
        result.append((lo.timestamp(), hi.timestamp()))
    return list(reversed(result))


def bounded_query(store, sql, args=()):
    with store._lock:
        steps = [0]
        def budget():
            steps[0] += 1
            return steps[0] > 2000
        store._db.set_progress_handler(budget, 1000)
        try:
            return store._all(sql, args)
        except sqlite3.OperationalError as exc:
            raise ValueError('Weekly query exceeded its work budget; use a shorter --since range.') from exc
        finally:
            store._db.set_progress_handler(None, 0)


def read_rows(store, table, column, start, end):
    # Fixed identifiers supplied only by this module. Never load message bodies.
    columns = {
        'reports': 'source,reporter_channel,reporter_address,ticket_ref,created_at',
        'alerts': 'kind,state,answer,meta,sent_at,closed_at,owner_action,owner_action_at',
        'customer_messages': 'kind',
    }
    selection = ('deliveries.ok,deliveries.alert_id,deliveries.message_kind,alerts.kind AS alert_kind'
                 if table == 'deliveries' else columns[table])
    source = 'deliveries LEFT JOIN alerts ON alerts.id=deliveries.alert_id' if table == 'deliveries' else table
    rows = bounded_query(store,
        f'SELECT {selection} FROM {source} WHERE {table}.{column}>=? AND {table}.{column}<? LIMIT ?',
        (start, end, ROW_LIMIT + 1))
    if len(rows) > ROW_LIMIT:
        raise ValueError('Weekly row limit reached; use a shorter --since range.')
    return rows


def median(values):
    return statistics.median(values) if values else None


def collect(eng, *, since=None, weeks=2, detail=False, now=None):
    now = eng.clock() if now is None else now
    spans = windows(now, since, weeks)
    store = eng.store
    cfg = eng.config()
    queues = alerts.watched_queues(cfg)
    if len(queues) > QUEUE_LIMIT:
        raise ValueError('Weekly queue limit reached (16).')
    tickets = {}
    limits = []
    for queue in queues:
        try:
            rows = eng.wt.ls(queue, status='all', limit=TICKET_LIMIT + 1)
            if len(rows) > TICKET_LIMIT:
                raise ValueError('Weekly ticket limit reached (2000 per queue).')
            tickets.update((t['ref'], t) for t in rows)
        except wtmod.WTError:
            limits.append('WatchTower unavailable; ticket outcomes and decisions are incomplete.')
    periods = []
    for lo, hi in spans:
        reports = read_rows(store, 'reports', 'created_at', lo, hi)
        sent = read_rows(store, 'deliveries', 'sent_at', lo, hi)
        answered = read_rows(store, 'alerts', 'closed_at', lo, hi)
        messages = read_rows(store, 'customer_messages', 'created_at', lo, hi)
        raised = read_rows(store, 'alerts', 'created_at', lo, hi)
        refs = {r['ticket_ref'] for r in reports if r['ticket_ref']}
        outcomes = Counter()
        durations = []
        for ref in refs:
            ticket = tickets.get(ref)
            if ticket is None:
                outcomes['unknown'] += 1
            elif ticket.get('status') != 'closed':
                outcomes['still_open'] += 1
        # Include fixes of reports received before this period. One indexed read,
        # not a queue subprocess or a database query for each ticket.
        closed = {ref: t for ref, t in tickets.items() if t.get('status') == 'closed'
                  and lo <= stamp(t.get('closed_at')) < hi}
        if closed:
            placeholders = ','.join('?' for _ in closed)
            first_reports = bounded_query(store,
                f"SELECT ticket_ref, MIN(created_at) AS first_at FROM reports WHERE ticket_ref IN ({placeholders}) GROUP BY ticket_ref",
                tuple(closed))
            for report in first_reports:
                ticket = closed[report['ticket_ref']]
                if wtmod.closed_with_fix(ticket):
                    outcomes['fixed'] += 1
                    duration = stamp(ticket.get('closed_at')) - report['first_at']
                    if duration >= 0:
                        durations.append(duration)
                else:
                    outcomes['closed_without_fix'] += 1
        if outcomes['unknown']:
            limits.append('Some report tickets are missing from WatchTower; outcomes are incomplete.')
        kinds = Counter()
        # One message per successful channel delivery, including retries.
        for d in sent:
            if d['ok'] and d['alert_id']:
                kinds[d['alert_kind'] or 'unknown'] += 1
            elif d['ok'] and d['message_kind'] == 'direct':
                kinds['direct'] += 1
        replies = [a for a in answered if a['state'] == 'answered']
        reply_times = [a['closed_at']-a['sent_at'] for a in replies
                       if a['sent_at'] is not None and a['closed_at'] >= a['sent_at']]
        actions = read_rows(store, 'alerts', 'owner_action_at', lo, hi)
        approvals = Counter(a['owner_action'] for a in actions)
        for a in replies:
            if a['owner_action_at'] is not None:
                continue
            meta = json.loads(a['meta'])
            if a['kind'] not in ('send', 'fixed') or not meta.get('message_id'):
                continue
            word = a['answer'].strip().lower()
            approvals['refused' if word in alerts.NO_WORDS else
                      'approved' if word in alerts.GO_WORDS else 'reworded'] += 1
        decisions = undone = 0
        for ticket in tickets.values():
            for event in ticket.get('history') or []:
                if lo <= stamp(event.get('at')) < hi:
                    decisions += event.get('event') == 'auto_decision'
                    undone += event.get('event') == 'auto_decision_reversed'
        period = dict(start=lo, end=hi,
            reports=dict(received=len(reports), by_source=dict(Counter(r['source'] for r in reports)),
                         by_channel=dict(Counter(r['reporter_channel'] or 'none' for r in reports)),
                         held=sum(a['kind'] == 'report' for a in raised),
                         released=sum(a['kind'] == 'report' and a['answer'].strip().lower() in alerts.GO_WORDS for a in replies),
                         dropped=sum(a['kind'] == 'report' and a['answer'].strip().lower() in alerts.NO_WORDS for a in replies)),
            tickets=dict(filed=len(refs), **{k: outcomes[k] for k in ('fixed','closed_without_fix','still_open','unknown')}),
            report_to_fix_seconds=dict(median=median(durations), worst=max(durations, default=None)),
            owner_messages=dict(kinds), replies=dict(received=len(replies), median_seconds=median(reply_times)),
            decisions=dict(made=decisions, undone=undone),
            reporter_messages=dict(asked=sum(m['kind']=='ask' for m in messages),
                **{k: approvals[k] for k in ('approved','reworded','refused')}),
            failed_deliveries=sum(not d['ok'] for d in sent))
        if detail:
            period['reporters'] = sorted({r['reporter_address'] for r in reports if r['reporter_address']})
        periods.append(period)
    return dict(ok=True, periods=periods, limits=sorted(set(limits)),
                basis='Reports and filed/open tickets use report cohorts; fixes and closures use closure time.')


def render(data, markdown=False):
    lines = []
    for p in data['periods']:
        lines.append(('## ' if markdown else '') + datetime.fromtimestamp(p['start']).strftime('%Y-%m-%d') +
                     ' to ' + datetime.fromtimestamp(p['end']).strftime('%Y-%m-%d'))
        # Export only fixed labels and numeric values, never source names or addresses.
        for group in ('reports', 'tickets', 'report_to_fix_seconds', 'replies', 'decisions', 'reporter_messages'):
            values = ', '.join(f'{k.replace("_", " ")}: {v if v is not None else "not recorded"}'
                               for k, v in p[group].items() if not isinstance(v, dict))
            lines.append(('- ' if markdown else '') + group.replace('_',' ') + ': ' + values)
        lines.append(f'Owner messages: {sum(p["owner_messages"].values())}; failed deliveries: {p["failed_deliveries"]}')
    return '\n'.join(lines)


def daily(eng, now):
    cfg, store = eng.config(), eng.store
    setting = cfg.get('daily_summary') or {}
    local = datetime.fromtimestamp(now)
    day = local.date().isoformat()
    if not setting.get('enabled'):
        # Switching it off also cancels a digest queued during quiet hours.
        store._run("UPDATE alerts SET state='cleared', closed_at=? WHERE kind='summary' AND state='open'", (now,))
        return
    if local.strftime('%H:%M') < setting.get('time', '09:00'):
        return
    if store.kv_get('summary_day') == day:
        return
    start = datetime.combine(local.date()-timedelta(days=1), datetime.min.time())
    end = datetime.combine(local.date(), datetime.min.time())
    data = collect(eng, since=start.isoformat(), weeks=1, now=end.timestamp())
    p = data['periods'][0]
    waiting = bounded_query(store, "SELECT COUNT(*) AS n FROM alerts WHERE state='open' AND needs_reply=1")[0]['n']
    held = bounded_query(store, "SELECT COUNT(*) AS n FROM reports WHERE state='held'")[0]['n']
    # Expire an unsent prior day's digest rather than delivering a stale backlog.
    for a in store.alerts(state='open', limit=500):
        if a['kind'] == 'summary':
            store.alert_close(a['id'], 'cleared', now=now)
    if (p['reports']['received'] or sum(v for k, v in p['owner_messages'].items() if k != 'summary')
            or p['failed_deliveries'] or waiting or held or p['decisions']['made']
            or p['replies']['received'] or any(p['reporter_messages'].values())):
        body = (f"Yesterday: {p['reports']['received']} reports, {p['tickets']['fixed']} fixed.\n"
                f"{waiting} questions and {held} reports waiting.\n"
                f"{sum(p['owner_messages'].values())} owner messages; {p['failed_deliveries']} failed deliveries.")
        if data['limits']:
            body += '\nQueue counts are incomplete.'
        store.alert_upsert('summary:'+day, kind='summary', title='On-call daily summary',
                           body=body, needs_reply=False, now=now)
    store.kv_set('summary_day', day)
