"""Small replayable event stream for dashboard state changes.

The hub deliberately owns no product state.  Writers publish only after their
durable mutation succeeds; HTTP handlers replay these immutable envelopes to
browsers and ask for a resync when a cursor is no longer in the bounded ring.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DashboardEventSnapshot:
    events: tuple
    resync_required: bool
    boot_id: str
    latest_seq: int


class DashboardEventHub:
    """Thread-safe, bounded replay buffer for dashboard events."""

    def __init__(self, *, capacity=512, boot_id=None):
        capacity = int(capacity)
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._boot_id = str(boot_id or uuid.uuid4())
        self._events = deque(maxlen=capacity)
        self._entity_versions = {}
        self._seq = 0
        self._condition = threading.Condition()

    @property
    def boot_id(self):
        return self._boot_id

    @property
    def latest_seq(self):
        with self._condition:
            return self._seq

    def publish(self, topic, *, entity=None, patch=None, invalidate=None):
        topic = str(topic or "").strip()
        if not topic:
            raise ValueError("topic is required")
        if entity is not None:
            if not isinstance(entity, dict):
                raise TypeError("entity must be a mapping")
            entity_type = str(entity.get("type") or "").strip()
            entity_id = str(entity.get("id") or "").strip()
            if not entity_type or not entity_id:
                raise ValueError("entity requires type and id")
            normalized_entity = {"type": entity_type, "id": entity_id}
            entity_key = (entity_type, entity_id)
        else:
            normalized_entity = None
            entity_key = None
        if patch is not None and not isinstance(patch, dict):
            raise TypeError("patch must be a mapping")
        if invalidate is not None and not isinstance(invalidate, (list, tuple)):
            raise TypeError("invalidate must be a sequence")

        with self._condition:
            self._seq += 1
            entity_version = None
            if entity_key is not None:
                entity_version = self._entity_versions.get(entity_key, 0) + 1
                self._entity_versions[entity_key] = entity_version
            event = {
                "schema": 1,
                "boot_id": self._boot_id,
                "seq": self._seq,
                "topic": topic,
                "entity": normalized_entity,
                "entity_version": entity_version,
                "patch": copy.deepcopy(patch) if patch is not None else {},
                "invalidate": copy.deepcopy(list(invalidate or ())),
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self._events.append(event)
            self._condition.notify_all()
            return copy.deepcopy(event)

    def snapshot_since(self, seq, *, boot_id=None):
        with self._condition:
            return self._snapshot_since_locked(seq, boot_id=boot_id)

    def wait_since(self, seq, *, boot_id=None, timeout=15.0):
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                snapshot = self._snapshot_since_locked(seq, boot_id=boot_id)
                if snapshot.resync_required or snapshot.events:
                    return snapshot
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return snapshot
                self._condition.wait(remaining)

    def _snapshot_since_locked(self, seq, *, boot_id=None):
        try:
            cursor = max(0, int(seq or 0))
        except (TypeError, ValueError):
            cursor = 0
        if boot_id is not None and str(boot_id) != self._boot_id:
            return DashboardEventSnapshot((), True, self._boot_id, self._seq)
        oldest_seq = self._events[0]["seq"] if self._events else self._seq + 1
        if cursor < oldest_seq - 1:
            return DashboardEventSnapshot((), True, self._boot_id, self._seq)
        events = tuple(copy.deepcopy(event) for event in self._events if event["seq"] > cursor)
        return DashboardEventSnapshot(events, False, self._boot_id, self._seq)
