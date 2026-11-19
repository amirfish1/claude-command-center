"""Let queued dashboard reads yield to a shutdown without cutting active work."""
from contextlib import contextmanager


class ReadDeferredForRestart(Exception):
    """The read has not executed and can be retried after restart."""


@contextmanager
def read_slot(lock, stopping):
    while not stopping.is_set():
        # Releasing the lock wakes an acquirer immediately; this timeout
        # only bounds how long an idle waiter takes to notice a restart.
        # Hundreds of queued reads otherwise wake twenty times a second
        # and contend with the foreground work they are waiting behind.
        if lock.acquire(timeout=0.25):
            break
    else:
        raise ReadDeferredForRestart
    try:
        if stopping.is_set():
            raise ReadDeferredForRestart
        yield
    finally:
        lock.release()
