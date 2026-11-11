"""Small indexed retention batches. Waiting work has no expiry."""
import calendar
from datetime import datetime, timezone

BATCH = 100
# Predicates are static, shared with partial indexes, never supplied by callers.
TABLES = {
    'alerts': ('closed_at', "state IN ('answered','cleared')"),
    'deliveries': ('sent_at', '1=1'),
    'inbound': ('received_at', "handled NOT IN ('','needs_review')"),
    'customer_messages': ('created_at', "state IN ('sent','answered','dropped')"),
    'reports': ('updated_at', "state IN ('closed','dropped')"),
    'log': ('at', '1=1'),
}


def cutoff(now):
    date = datetime.fromtimestamp(now, timezone.utc)
    month = date.year * 12 + date.month - 1 - 13
    year, month = divmod(month, 12)
    return date.replace(year=year, month=month + 1,
                        day=min(date.day, calendar.monthrange(year, month + 1)[1])).timestamp()


def initialize(db):
    for table, (age, predicate) in TABLES.items():
        db.execute(f'CREATE INDEX IF NOT EXISTS retention_{table} ON {table}({age},id) WHERE {predicate}')


def prune(store, now, batch=BATCH):
    """Inspect at most one batch per table; rotate past protected rows."""
    before = cutoff(now)
    removed = {}
    for table, (age, predicate) in TABLES.items():
        cursor = store.kv_get('retention:' + table, [0, 0])
        sql = (f'SELECT * FROM {table} INDEXED BY retention_{table} WHERE {predicate} '
               f'AND {age}<? AND ({age},id)>(?,?) ORDER BY {age},id LIMIT ?')
        rows = store._all(sql, (before, *cursor, batch))
        if not rows and cursor != [0, 0]:
            rows = store._all(sql, (before, 0, 0, batch))
        removed[table] = 0
        for row in rows:
            if table == 'deliveries' and row['alert_id']:
                alert = store.alert(row['alert_id'])
                if alert and alert['state'] == 'open':
                    continue
            if table == 'customer_messages':
                if row['alert_id']:
                    alert = store.alert(row['alert_id'])
                    if alert and alert['state'] == 'open':
                        continue
                if row['kind'] == 'ask' and row['state'] == 'sent':
                    report = store.report_by_ref(row['ref'])
                    if not report or report['state'] not in ('closed', 'dropped'):
                        continue
            if table == 'reports':
                # Keep the conversation identity while any customer action waits.
                if store._one("SELECT id FROM customer_messages WHERE ref=? AND state IN "
                              "('pending','uncertain','failed') LIMIT 1", (row['ticket_ref'],)):
                    continue
            store._run(f'DELETE FROM {table} WHERE id=?', (row['id'],))
            if table == 'alerts':
                for key in (f"try:{row['id']}", f"reply-action:{row['id']}"):
                    store._run('DELETE FROM kv WHERE key=?', (key,))
                store._run('DELETE FROM kv WHERE key GLOB ?', (f"owner-repeat:{row['id']}:*",))
            removed[table] += 1
        if rows:
            store.kv_set('retention:' + table, [rows[-1][age], rows[-1]['id']])
    return removed
