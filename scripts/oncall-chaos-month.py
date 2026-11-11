"""Fast-clock month through the real engine/store with deterministic endpoints.

Endpoint doubles keep this scale experiment local and independent of provider
quotas. Process and CLI faults are exercised separately by the parent proof.
"""
import time
from ccc_server.oncall.engine import Engine
from ccc_server.oncall.store import Receipt


def run(root):
    class Queue:
        def __init__(self):
            self.items, self.calls = {}, 0
        def available(self):
            return True
        def add(self, queue, *, title, **kwargs):
            self.calls += 1
            ref = f'{queue}-{len(self.items)+1}'
            self.items[ref] = {'ref': ref, 'status': 'closed', 'title': title,
                               'resolution': {'summary': 'Fixed fixture', 'commit': 'test-commit'}}
            return ref
        def blocked(self, queue):
            self.calls += 1
            return []
        def ls(self, queue, status='active', limit=200, *, offset=None):
            self.calls += 1
            assert status == 'closed' and limit == 100
            return list(reversed(list(self.items.values())))[offset or 0:(offset or 0)+limit]
    class Channel:
        id = 'local'
        can_receive = False
        def __init__(self):
            self.sent = 0
        def send(self, msg):
            self.sent += 1
            return Receipt(ok=True, external_id=str(self.sent))
    now = [1800000000.0]
    queue, channel = Queue(), Channel()
    eng = Engine(root, wt=queue, clock=lambda: now[0], flag_on=lambda: True,
                 build_channel=lambda cfg, **kwargs: channel)
    cfg = eng.config()
    cfg.update(enabled=True, queue='MONTH', channels=[{'id': 'local', 'type': 'command'}])
    eng.save_config(cfg)
    tick_max, calls_max = 0, 0
    started = time.monotonic()
    try:
        for hour in range(30*24):
            for n in range(5):
                eng.report(f'Fixture report {hour}:{n}')
            before_calls = queue.calls
            before_tick = time.monotonic()
            eng.tick()
            tick_max = max(tick_max, time.monotonic()-before_tick)
            calls_max = max(calls_max, queue.calls-before_calls)
            assert calls_max <= 2, 'Tick made per-ticket endpoint calls'
            now[0] += 3600
        # Finish the bounded closure pages and delivery budget after the month.
        for _ in range(400):
            eng.tick()
            now[0] += 3600
            if channel.sent == 3600:
                break
        counts = {table: eng.store._one(f'SELECT COUNT(*) AS n FROM {table}')['n']
                  for table in ('reports', 'alerts', 'deliveries')}
        assert counts == {'reports': 3600, 'alerts': 3600, 'deliveries': 3600}, counts
        assert channel.sent == 3600
        return {'counts': counts, 'max_tick_seconds': round(tick_max, 4),
                'max_queue_calls_per_tick': calls_max,
                'store_bytes': eng.store.path.stat().st_size,
                'elapsed_seconds': round(time.monotonic()-started, 2)}
    finally:
        eng.store.close()
