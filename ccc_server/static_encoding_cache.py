"""Keep bounded, disposable wire representations across dashboard restarts."""

import hashlib
import os
from pathlib import Path
import tempfile
import threading


class StaticEncodingCache:
    # Source identities include nanosecond mtime, ctime and size. Change this
    # namespace when changing the encoder or the representation contract.
    _VERSION = b"static-br6-gzip5-v1\0"
    _HEADER_SIZE = 32

    def __init__(self, directory, max_bytes=32 << 20, max_entries=128, *, protected_keys=None):
        self.directory = Path(directory)
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._protected_keys = protected_keys
        self._lock = threading.Lock()

    def _path(self, key):
        digest = hashlib.sha256(self._VERSION + repr(key).encode()).hexdigest()
        return self.directory / (digest + ".cache")

    def get(self, key):
        try:
            with self._path(key).open("rb") as stream:
                packet = stream.read(self.max_bytes + 1)
            if len(packet) > self.max_bytes or len(packet) <= self._HEADER_SIZE:
                return None
            digest, body = packet[:self._HEADER_SIZE], packet[self._HEADER_SIZE:]
            return body if hashlib.sha256(body).digest() == digest else None
        except OSError:
            return None

    def put(self, key, body):
        weight = len(body) + self._HEADER_SIZE
        if not body or weight > self.max_bytes or self.max_entries < 1:
            return
        temporary = None
        try:
            with self._lock:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                # Readers in another process see either a complete previous
                # entry or a complete new one. A failed write is just a miss.
                with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(hashlib.sha256(body).digest())
                    stream.write(body)
                os.replace(temporary, self._path(key))
                self._trim()
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _trim(self):
        protected = ({self._path(key) for key in self._protected_keys()}
                     if self._protected_keys is not None else set())
        entries = []
        for candidate in self.directory.glob("*.cache"):
            try:
                stat = candidate.stat()
                entries.append((candidate in protected, stat.st_mtime_ns, candidate, stat.st_size))
            except OSError:
                continue
        size = 0
        for index, (_, _, candidate, weight) in enumerate(sorted(entries, reverse=True)):
            size += weight
            if index >= self.max_entries or size > self.max_bytes:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
