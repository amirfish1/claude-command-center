"""Let queued dashboard reads yield to a shutdown without cutting active work."""
from contextlib import contextmanager


class ReadDeferredForRestart(Exception):
    """The read has not executed and can be retried after restart."""


@contextmanager
def read_slot(lock, stopping):
    while not stopping.is_set():
        if lock.acquire(timeout=0.05):
            break
    else:
        raise ReadDeferredForRestart
    try:
        if stopping.is_set():
            raise ReadDeferredForRestart
        yield
    finally:
        lock.release()
