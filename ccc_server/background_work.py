"""Share one interpreter slot between expensive dashboard maintenance jobs.

HTTP handlers keep using their own admission gates. Cache builders queue here
instead of all competing for the GIL at startup or when their TTLs expire.
FIFO admission prevents a frequent warmer from starving an occasional scan.
Nested cache reads on the same thread reuse its slot.
"""
from collections import deque
from contextlib import contextmanager
import threading


_condition = threading.Condition()
_queue = deque()
_local = threading.local()


@contextmanager
def slot():
    if getattr(_local, "active", False):
        yield
        return
    token = object()
    with _condition:
        _queue.append(token)
        try:
            _condition.wait_for(lambda: _queue[0] is token)
        except BaseException:
            _queue.remove(token)
            _condition.notify_all()
            raise
    _local.active = True
    try:
        yield
    finally:
        _local.active = False
        with _condition:
            _queue.popleft()
            _condition.notify_all()
