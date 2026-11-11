"""Bounded setup check, separate from normal alert routing."""
import secrets
import time
from .store import Outbound


def roundtrip(eng, channel_id, timeout=90):
    # Keep the background poller from consuming the setup reply while waiting.
    with eng._lock:
        chan = eng.channels().get(channel_id)
        if chan is None or not hasattr(chan, 'ack_poll'):
            return {'ok': False, 'error': 'Pick a team chat channel.'}
        token = secrets.token_hex(8)
        receipt = chan.send(Outbound('CCC On-call setup test. Reply in this thread with: ready', thread=token))
        if not receipt.ok:
            return {'ok': False, 'error': receipt.error}
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                messages = chan.poll()
                matched = False
                for msg in messages:
                    if msg.thread == token:
                        if eng.store.inbound_claim(msg):
                            eng.store.inbound_handled(msg, 'setup test')
                        matched |= msg.text.strip().lower() == 'ready'
                    else:
                        eng._route(eng.config(), eng.channels(), chan.cfg, msg, eng.clock())
                chan.ack_poll(messages)
                if matched:
                    return {'ok': True, 'note': 'Reply round trip confirmed.'}
                time.sleep(min(2, max(0, deadline - time.monotonic())))
            return {'ok': False, 'error': 'Reply was not confirmed within 90 seconds. Run ccc oncall test CHANNEL --roundtrip to retry.'}
        finally:
            roots = chan.roots()
            roots.pop(receipt.external_id, None)
            chan._set('roots', roots)
