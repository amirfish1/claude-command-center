from ccc_server.oncall import alerts, health
from ccc_server.oncall.store import Store, Receipt


def test_stuck_episode_warns_owner_once_and_clears(tmp_path):
    store = Store(tmp_path)
    class Queue:
        stuck = True
        def health(self, queue):
            return {'stuck': self.stuck, 'stuck_reason': 'No worker progress'}
    class Channel:
        id = 'local'
        calls = 0
        def send(self, msg):
            self.calls += 1
            return Receipt(ok=True)
    queue, channel = Queue(), Channel()
    cfg = {'queue': 'DEMO', 'channels': [{'id': 'local'}]}
    for now in (1000, 1060, 1120):
        health.collect(cfg, store, queue, now)
        alerts.deliver(cfg, store, {'local': channel}, now=now)
    assert channel.calls == 1
    queue.stuck = False
    health.collect(cfg, store, queue, 1180)
    assert store.alerts()[0]['state'] == 'cleared'
    queue.stuck = True
    health.collect(cfg, store, queue, 1240)
    alerts.deliver(cfg, store, {'local': channel}, now=1240)
    assert channel.calls == 2
    store.close()
