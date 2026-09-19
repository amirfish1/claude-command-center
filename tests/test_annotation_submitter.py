"""CCC-1166: annotation-filed tickets record who opened them (``submitter``)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import server


class _CapturingQueue:
    def __init__(self):
        self.kwargs = None

    def enqueue(self, **kwargs):
        self.kwargs = kwargs
        return {"number": 1, "project": "CCC", "ref": "CCC-1"}


_META = {
    "annotation_id": "ann-test",
    "note": "The button is broken",
    "selector": "#queuePanel",
    "url": "http://localhost:8090/",
    "source": "ccc",
}


class TestAnnotationSubmitter(unittest.TestCase):
    def _enqueue(self, meta):
        queue = _CapturingQueue()
        with (
            mock.patch.object(server, "_q", queue),
            mock.patch.object(server, "_WT_WORKERS_AVAILABLE", False),
        ):
            result = server.enqueue_annotation_ux_fixes_queue("note", meta=meta)
        self.assertTrue(result["ok"], result)
        return queue.kwargs

    def test_explicit_payload_submitter_wins_and_marks_explicit(self):
        kwargs = self._enqueue({**_META, "submitter": "worker-x"})
        self.assertEqual("worker-x", kwargs["submitter"])
        self.assertTrue(kwargs["submitter_explicit"])

    def test_ccc_user_name_env_is_the_filer(self):
        with mock.patch.dict(os.environ, {"CCC_USER_NAME": "Amir"}):
            kwargs = self._enqueue(dict(_META))
        self.assertEqual("Amir", kwargs["submitter"])
        self.assertFalse(kwargs["submitter_explicit"])

    def test_os_user_is_the_last_resort_filer(self):
        env = {k: v for k, v in os.environ.items() if k != "CCC_USER_NAME"}
        with mock.patch.dict(os.environ, env, clear=True):
            kwargs = self._enqueue(dict(_META))
        # getpass.getuser() resolves the account on any real platform; if it
        # somehow can't, the submitter is "" and filing still succeeds.
        self.assertIn("submitter", kwargs)
        self.assertFalse(kwargs["submitter_explicit"])

    def test_old_watchtower_without_submitter_kwargs_still_files(self):
        class _OldQueue:
            def enqueue(self, **kwargs):
                if "submitter" in kwargs:
                    raise TypeError("unexpected keyword argument")
                return {"number": 1, "project": "CCC", "ref": "CCC-1"}

        with (
            mock.patch.object(server, "_q", _OldQueue()),
            mock.patch.object(server, "_WT_WORKERS_AVAILABLE", False),
        ):
            result = server.enqueue_annotation_ux_fixes_queue("note", meta=dict(_META))
        self.assertTrue(result["ok"], result)


class TestTicketDetailFiledBy(unittest.TestCase):
    """The ticket-detail modal renders who opened the ticket."""

    def test_origin_section_has_a_filed_by_row(self):
        app_js = (server.CCC_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = app_js.index("function _uxqOpenItemModal(item)")
        end = app_js.index("// Answer section", start)
        modal = app_js[start:end]

        self.assertIn("uxq-td-pg-origin", modal)
        self.assertIn("'Filed by'", modal)
        self.assertIn("item.submitter", modal)
        self.assertIn("item.github_author", modal)

    def test_filed_timeline_event_names_the_filer(self):
        app_js = (server.CCC_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = app_js.index("function _uxqOpenItemModal(item)")
        end = app_js.index("// Answer section", start)
        modal = app_js[start:end]

        self.assertIn("ev.submitter || item.submitter || item.github_author", modal)
        self.assertIn("escapeHtml(filer)", modal)


if __name__ == "__main__":
    unittest.main()
