"""WatchTower remains responsible for workers; On-call reports stalled queues."""
from . import alerts
from .wt import WTError


def collect(cfg, store, wt, now):
    probe = getattr(wt, 'health', None)
    if probe is None or not (cfg.get('alerts') or {}).get('stuck', True):
        return
    queues = alerts.watched_queues(cfg)
    if not queues:
        return
    offset = int(store.kv_get('health_cursor', 0)) % len(queues)
    for queue in (queues[offset:] + queues[:offset])[:16]:
        key = 'health_incident:' + queue
        try:
            status = probe(queue)
        except WTError as exc:
            store.log('wt', f'health {queue}: {exc}', now=now)
            continue
        incident = store.kv_get(key)
        if status.get('stuck'):
            if not incident:
                incident = f'health:{queue}:{now}'
                store.kv_set(key, incident)
            alerts.raise_event(store, kind='stuck', key=incident, ref=queue,
                title=f'{queue} is not moving', needs_reply=False,
                body='WatchTower reports no progress. ' + str(status.get('stuck_reason') or '') +
                     ' Check the worker and ticket before restarting work. On-call has not restarted or reassigned it.',
                now=now)
        elif incident:
            alerts.clear_event(store, incident, now=now)
            store.kv_set(key, None)
    store.kv_set('health_cursor', (offset + 16) % len(queues))
