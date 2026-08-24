"""Regression coverage for WatchTower titles created from annotations."""

from __future__ import annotations

import unittest

import server


class TestAnnotationWorkerTitles(unittest.TestCase):
    def test_annotation_worker_title_prefers_note_to_page_title(self):
        item = {
            "source": "ccc",
            "id": "ann-test",
            "title": "Command Center for Claude, Codex, Cursor, and Anti-Gravity",
            "note": "Conductor sessions should skip the initial wrapper in their names.",
            "text": "Fix the following UX issue based on this annotation.",
        }

        self.assertEqual(
            server._wt_ticket_context(item),
            "Conductor sessions should skip the initial wrapper in their...",
        )
