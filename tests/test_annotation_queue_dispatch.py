"""Regression coverage for responsive annotation queue submission."""

from __future__ import annotations

import threading
import time
from unittest import mock

import server


def test_annotation_enqueue_does_not_wait_for_worker_dispatch():
    """A saved annotation must return even when worker reconciliation is slow."""

    class Queue:
        def enqueue(self, **_kwargs):
            return {"number": 1, "project": "CCC", "ref": "CCC-1"}

    started = threading.Event()
    release = threading.Event()

    class Workers:
        def dispatch_after_enqueue(self, _queue, _ref):
            started.set()
            release.wait(timeout=2)

    with (
        mock.patch.object(server, "_q", Queue()),
        mock.patch.object(server, "_WT_WORKERS_AVAILABLE", True),
        mock.patch.object(server, "_wt_workers", Workers()),
    ):
        began = time.monotonic()
        result = server.enqueue_annotation_ux_fixes_queue(
            "The annotation request should not block",
            meta={"selector": "#queue-panel"},
        )
        elapsed = time.monotonic() - began

        assert result["ok"]
        assert elapsed < 0.5
        assert started.wait(timeout=1)
        release.set()
