"""Bounded, single-flight census reads and evidence-based startup readiness."""
import threading
import time
from collections import OrderedDict


class CensusCache:
    def __init__(self, fresh_s=3.0, max_stale_s=15.0, max_entries=8):
        self.fresh_s = fresh_s
        self.max_stale_s = max_stale_s
        self.max_entries = max_entries
        self.lock = threading.Lock()
        self.slots = OrderedDict()
        self.started = time.monotonic()
        self.ready = False
        self.last_build_ms = None
        self._warm_stop = None

    def start_warmup(self, builder):
        """Keep the default census current even between occasional CLI calls.

        Refreshes still use get's single-flight and failure backoff. Returning
        the stop event also lets short-lived servers stop this daemon cleanly.
        """
        with self.lock:
            if self._warm_stop is not None:
                return self._warm_stop
            stop = self._warm_stop = threading.Event()

        def refresh():
            while not stop.is_set():
                self.get(86400, builder)
                stop.wait(max(.01, self.fresh_s))

        threading.Thread(target=refresh, name='ccc-census-warmup', daemon=True).start()
        return stop

    def _readiness_locked(self):
        now = time.monotonic()
        active = [s['started'] for s in self.slots.values() if s['running']]
        canonical = self.slots.get(86400.0)
        available = (canonical is not None and canonical['payload'] is not None
                     and now - canonical['at'] <= self.max_stale_s)
        state = 'warming' if not self.ready else ('ready' if available else 'busy')
        since = min(active) if active else (canonical['started'] if canonical else self.started)
        return {'state': state, 'elapsed_s': round(max(0, time.monotonic() - since), 1)
                if state != 'ready' else 0, 'last_build_ms': self.last_build_ms}

    def readiness(self):
        with self.lock:
            return self._readiness_locked()

    def get(self, since_s, builder):
        key = float(since_s) if since_s is not None else None
        with self.lock:
            now = time.monotonic()
            slot = self.slots.get(key)
            if slot is None:
                if len(self.slots) >= self.max_entries:
                    victims = [k for k, s in self.slots.items()
                               if k != 86400.0 and not s['running']]
                    if victims:
                        del self.slots[victims[0]]
                    else:
                        return self._unavailable_locked()
                slot = {'payload': None, 'at': 0, 'started': now, 'running': False, 'attempted': False}
                self.slots[key] = slot
            self.slots.move_to_end(key)
            age = now - slot['at']
            if (not slot['running'] and (slot['payload'] is None or age >= self.fresh_s)
                    and (not slot['attempted'] or now - slot['started'] >= self.fresh_s)):
                slot['running'] = True
                slot['attempted'] = True
                slot['started'] = now
                threading.Thread(target=self._build, args=(key, slot, builder),
                                 name='ccc-census-refresh', daemon=True).start()
            if slot['payload'] is not None and age <= self.max_stale_s:
                return {**slot['payload'], 'cached': True,
                        'refreshing': slot['running'], 'age_s': round(age, 2)}, 200
            return self._unavailable_locked()

    def _unavailable_locked(self):
        readiness = self._readiness_locked()
        return {'ok': False, 'sessions': [], 'now': time.time(),
                'code': 'sessions_' + readiness['state'],
                'error': 'Sessions are warming up' if readiness['state'] == 'warming'
                         else 'Sessions are refreshing; retry shortly',
                'sessions_readiness': readiness}, 503

    def _build(self, key, slot, builder):
        try:
            payload = builder(key)
            if not isinstance(payload, dict) or payload.get('ok') is not True:
                return
            with self.lock:
                slot['payload'] = payload
                slot['at'] = time.monotonic()
                self.last_build_ms = round((slot['at'] - slot['started']) * 1000, 1)
                # The CLI defaults to 24 hours; a live-only result must not
                # announce that the more expensive default request is ready.
                if key == 86400.0:
                    self.ready = True
        except Exception:
            # A failed build is never an empty success or readiness evidence.
            pass
        finally:
            with self.lock:
                slot['running'] = False
