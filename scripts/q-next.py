#!/usr/bin/env python3
"""Reserve a machine-wide question number; never repair/reset state implicitly."""
import os
from pathlib import Path
import re
import sys
import tempfile
import time


def next_number(directory=None, timeout=5):
    directory = Path(directory or os.environ.get("Q_NEXT_DIR") or
                     Path.home() / ".local" / "state" / "q-next")
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "lock.d"  # interoperates with the original shell helper
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("q-next lock is busy; inspect lock.d manually (not removed)")
            time.sleep(0.025)
    temp = None
    try:
        counter = directory / "counter"
        marker = directory / "initialized"
        if counter.exists():
            raw = counter.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[0-9]+", raw):
                raise ValueError("invalid q-next counter; restore known state manually")
            number = int(raw) + 1
        else:
            if marker.exists() or counter.is_symlink():
                raise ValueError("q-next counter is missing; restore known state manually")
            number = 1
        fd, temp = tempfile.mkstemp(prefix=".counter-", dir=directory)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(str(number) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, counter)
        temp = None
        marker.touch(exist_ok=True)
        return number
    finally:
        if temp:
            os.unlink(temp)
        lock.rmdir()


if __name__ == "__main__":
    try:
        print(next_number())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"q-next: {error}", file=sys.stderr)
        sys.exit(1)
