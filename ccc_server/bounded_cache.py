# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""An LRU cache bounded by estimated bytes as well as by entry count.

An entry cap alone does not bound memory: the same 512 entries are 5 MB on a
quiet install and gigabytes when each one is a whole transcript. Stdlib-only.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class ByteBudgetCache:
    """Least-recently-used mapping. Inserting evicts until both bounds hold;
    a value larger than the whole budget is not stored at all."""

    def __init__(self, max_bytes, max_entries):
        self.max_bytes = int(max_bytes)
        self.max_entries = int(max_entries)
        self._items = OrderedDict()  # key -> (value, weight)
        self._bytes = 0
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._items)

    def __contains__(self, key):
        return key in self._items

    @property
    def bytes(self):
        return self._bytes

    def get(self, key, default=None):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return default
            self._items.move_to_end(key)
            return item[0]

    def put(self, key, value, weight):
        weight = max(0, int(weight))
        with self._lock:
            self._drop(key)
            if weight > self.max_bytes:
                return False
            self._items[key] = (value, weight)
            self._bytes += weight
            while self._items and (
                self._bytes > self.max_bytes or len(self._items) > self.max_entries
            ):
                self._drop(next(iter(self._items)))
            return True

    def pop(self, key, default=None):
        with self._lock:
            item = self._items.get(key)
            self._drop(key)
            return default if item is None else item[0]

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def _drop(self, key):
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= item[1]
