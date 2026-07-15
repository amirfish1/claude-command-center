"""Regression coverage for duplicate annotation queue submissions."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

import server


class _BlockingQueue:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def enqueue(self, **kwargs):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        return {"number": self.calls, "project": "CCC", "ref": f"CCC-{self.calls}"}


class TestAnnotationQueueDedupe(unittest.TestCase):
    def test_rejects_identical_submission_while_first_enqueue_is_in_flight(self):
        queue = _BlockingQueue()
        meta = {
            "annotation_id": "ann-test",
            "note": "The button is broken",
            "selector": "#queuePanel",
            "url": "http://localhost:8090/",
            "source": "ccc",
        }
        first_result = []

        with (
            mock.patch.object(server, "_q", queue),
            mock.patch.object(server, "_WT_WORKERS_AVAILABLE", False),
        ):
            first = threading.Thread(
                target=lambda: first_result.append(
                    server.enqueue_annotation_ux_fixes_queue("The button is broken", meta=meta)
                )
            )
            first.start()
            self.assertTrue(queue.started.wait(timeout=1))

            duplicate = server.enqueue_annotation_ux_fixes_queue("The button is broken", meta=meta)
            queue.release.set()
            first.join(timeout=1)

        self.assertEqual(1, queue.calls)
        self.assertEqual(409, duplicate["status"])
        self.assertIn("already being submitted", duplicate["error"])
        self.assertTrue(first_result[0]["ok"])

    def test_preview_disables_submit_until_the_queue_request_settles(self):
        app_js = (server.CCC_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = app_js.index("function annShowUxFixesPreview(ann, onSubmit)")
        body = app_js[start:app_js.index("async function annOpenUxFixesQueue", start)]

        self.assertIn("let submitting = false;", body)
        self.assertIn("submitBtn.disabled = true;", body)
        self.assertIn("await onSubmit(edited);", body)
