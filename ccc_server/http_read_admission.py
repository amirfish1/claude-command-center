"""Limit background reads and wake queued work when HTTP admission pauses."""
from contextlib import contextmanager
import threading


class ReadDeferredForRestart(Exception):
    """The read has not executed and can be retried after restart."""


class _Slot:
    def __init__(self, lock, capacity):
        self.ready = threading.Condition(lock)
        self.available = capacity
        self.users = 0


class ReadAdmission:
    def __init__(self, capacity, max_routes):
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._generation = 0
        self._shared = _Slot(self._lock, capacity)
        self._routes = {}
        self._max_routes = max_routes

    def is_set(self):
        return self._paused.is_set()

    def set(self):
        with self._lock:
            self._paused.set()
            self._generation += 1
            self._shared.ready.notify_all()
            for slot in self._routes.values():
                slot.ready.notify_all()

    def clear(self):
        with self._lock:
            self._paused.clear()

    def _route_slot(self, route):
        slot = self._routes.get(route)
        if slot is None:
            if len(self._routes) >= self._max_routes:
                idle = next((key for key, slot in self._routes.items() if not slot.users), None)
                if idle is not None:
                    del self._routes[idle]
            slot = self._routes[route] = _Slot(self._lock, 1)
        return slot

    @contextmanager
    def slot(self, route=None):
        with self._lock:
            if self.is_set():
                raise ReadDeferredForRestart
            generation = self._generation
            slot = self._shared if route is None else self._route_slot(route)
            # Keep the same gate for this route while any caller is queued.
            slot.users += 1
            try:
                while not slot.available and generation == self._generation:
                    slot.ready.wait()
                # A cancelled restart can clear the pause before a waiter runs.
                if generation != self._generation:
                    raise ReadDeferredForRestart
            except BaseException:
                slot.users -= 1
                raise
            slot.available -= 1
        try:
            yield
        finally:
            with self._lock:
                slot.users -= 1
                slot.available += 1
                slot.ready.notify()
