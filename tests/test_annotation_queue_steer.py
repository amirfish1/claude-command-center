"""Regression coverage for prompt delivery from the annotation queue."""

from unittest import mock

import server


def test_annotation_queue_steers_an_existing_session():
    class Queue:
        def enqueue(self, **_kwargs):
            return {"number": 1, "project": "CCC", "ref": "CCC-1"}

    session_id = "00000000-0000-4000-8000-000000000730"
    with (
        mock.patch.object(server, "_q", Queue()),
        mock.patch.object(server, "_WT_WORKERS_AVAILABLE", False),
        mock.patch.object(
            server,
            "_find_annotation_ux_queue_session",
            return_value={"session_id": session_id},
        ),
        mock.patch.object(
            server, "_inject_text_into_session", return_value={"ok": True}
        ) as inject,
    ):
        result = server.enqueue_annotation_ux_fixes_queue(
            "Annotation: deliver this promptly",
            inject=True,
            meta={"selector": "#conversationsView"},
        )

    assert result["ok"]
    inject.assert_called_once_with(
        session_id,
        "Annotation: deliver this promptly",
        mode="steer",
        source="annotate-queue",
    )
