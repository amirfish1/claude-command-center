"""Keep spare connection workers so accepting sockets never starts a thread.

Active connections retain the original unbounded, daemon-thread behavior,
including streams and keep-alive. Only idle workers are bounded and reused.
"""

import queue
import threading


class _ConnectionWorkers:
    def __init__(self, server, reserve):
        self.server = server
        self.reserve = max(2, reserve)
        self.tasks = queue.Queue()
        self.condition = threading.Condition()
        self.idle = self.live = self.pending = 0
        self.closed = self.failed = False
        self.manager = None
        try:
            for _ in range(self.reserve):
                self._start_worker()
            self.manager = threading.Thread(
                target=self._maintain_spares, name="http-admission", daemon=True)
            self.manager.start()
        except BaseException:
            self.close()
            raise

    def _start_worker(self):
        with self.condition:
            if self.closed:
                return
            self.idle += 1
            self.live += 1
        try:
            # Thread.start waits for the new interpreter thread to run. Never
            # hold the admission lock, or make the accept loop wait for it.
            threading.Thread(target=self._worker, name="process_request_thread",
                             daemon=True).start()
        except BaseException:
            with self.condition:
                self.idle -= 1
                self.live -= 1
            raise

    def submit(self, request, address):
        with self.condition:
            accepted = not self.closed
            if accepted:
                self.pending += 1
                self.tasks.put((request, address))
                self.condition.notify_all()
        if not accepted:
            self.server.shutdown_request(request)

    def _maintain_spares(self):
        try:
            while True:
                with self.condition:
                    # Refill below half, not on every short connection. That
                    # avoids creating and retiring a thread per fast read.
                    self.condition.wait_for(lambda: self.closed or
                        self.idle - self.pending < self.reserve // 2)
                    if self.closed:
                        return
                while True:
                    with self.condition:
                        if self.closed or self.idle - self.pending >= self.reserve:
                            break
                    self._start_worker()
        except Exception:
            # Preserve the original per-connection failure/recovery behavior
            # if the host cannot create more spare workers.
            self.failed = True
            self.server.handle_error(None, None)
            self.close()

    def _worker(self):
        idle = True
        try:
            while True:
                task = self.tasks.get()
                with self.condition:
                    self.idle -= 1
                    idle = False
                    if task is None:
                        return
                    self.pending -= 1
                    self.condition.notify_all()
                self.server.process_request_thread(*task)
                with self.condition:
                    if self.closed or self.idle >= self.reserve:
                        return
                    self.idle += 1
                    idle = True
                    self.condition.notify_all()
        finally:
            with self.condition:
                if idle:
                    self.idle -= 1
                self.live -= 1
                self.condition.notify_all()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self.closed = True
            self.condition.notify_all()
            live = self.live
        # Pending sockets have not entered a handler. Active streams retain
        # their original daemon lifetime and are not joined during shutdown.
        while True:
            try:
                request, _ = self.tasks.get_nowait()
            except queue.Empty:
                break
            with self.condition:
                self.pending -= 1
            self.server.shutdown_request(request)
        for _ in range(live):
            self.tasks.put(None)
        if (self.manager is not None and self.manager.ident is not None
                and threading.current_thread() is not self.manager):
            self.manager.join(timeout=1)


class ConnectionAdmissionMixin:
    """Compose before ThreadingMixIn; keep its handler and socket cleanup."""
    connection_spares = 8

    def __init__(self, *args, **kwargs):
        self._connection_workers = None
        super().__init__(*args, **kwargs)
        try:
            self._connection_workers = _ConnectionWorkers(self, self.connection_spares)
        except BaseException:
            super().server_close()
            raise

    def process_request(self, request, address):
        workers = self._connection_workers
        if workers.failed:
            return super().process_request(request, address)
        workers.submit(request, address)

    def server_close(self):
        if self._connection_workers is not None:
            self._connection_workers.close()
        return super().server_close()
