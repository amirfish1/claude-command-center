"""Fast-clock plan rehearsal. No transport send, poll or check is called."""
import tempfile
from . import alerts
from .store import Store, Receipt


def preview(eng):
    cfg = eng.config()
    rows = []
    now = eng.clock()
    clock = [now]

    class DryChannel:
        def __init__(self, channel):
            self.channel, self.id, self.owner = channel, channel.id, channel.owner

        def send(self, message):
            missing = self.channel.missing(self.channel.cfg)
            rows.append({'at_seconds': clock[0] - now, 'channel': self.id, 'to': self.owner,
                         'text': message.text, 'missing_fields': missing, 'dry_run': True})
            return Receipt(not missing, error='Missing fields' if missing else '')

    channels = {cid: DryChannel(ch) for cid, ch in eng.channels(cfg).items() if not cid.startswith('github:')}
    with tempfile.TemporaryDirectory(prefix='ccc-escalation-preview-') as root:
        store = Store(root)
        try:
            store.alert_upsert('preview', kind='decision', title='Escalation test', body='Which option?', now=now)
            # Walk real deadlines plus short follow-up ticks. Quiet hours and cap
            # are the configured ones, not bypassed to make the preview look good.
            for step in cfg.get('escalation', []):
                clock[0] = now + step['after']
                alerts.deliver(cfg, store, channels, now=clock[0])
            pending = store._all('SELECT step,due FROM escalation_jobs WHERE done=0')
        finally:
            store.close()
    unavailable = [c['id'] for c in cfg.get('channels', []) if c['id'] not in channels]
    return {'ok': True, 'messages': rows, 'waiting': pending, 'unavailable_channels': unavailable,
            'limit': 'Dry-run validates fields and scheduling, not provider credentials or reachability.'}
