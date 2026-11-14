"""Codex may persist messages while leaving both SQLite title fields empty."""
from unittest import mock

import pytest

import server
from ccc_server import session_graph


@pytest.mark.parametrize("prompt,title,override,queued", [
    ("Explain why CI runs on every push", "", "", True),
    ("Explain why CI runs on every push", "CI runner attribution", "", False),
    ("Explain why CI runs on every push", "", "My topic", False),
    ("Drain the EXAMPLE WatchTower queue and keep it empty.", "", "", False),
    ("", "", "", False),
])
def test_recovered_opener_can_be_titled_without_another_snapshot(
    tmp_path, monkeypatch, prompt, title, override, queued,
):
    fresh = {"title": title, "first_user_message": ""}
    monkeypatch.setattr(session_graph, "_auto_title_enabled", lambda: True)
    monkeypatch.setattr(session_graph, "_auto_title_marker_path", lambda sid: tmp_path / sid)
    monkeypatch.setattr(session_graph, "_auto_title_claim", lambda sid: True)
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {"thread": override})
    monkeypatch.setattr(server, "_wt_read_worker_session_ids", lambda: [])
    with mock.patch.object(server, "_codex_titles_snapshot", side_effect=AssertionError("Already batched")), \
         mock.patch.object(session_graph.threading, "Thread") as thread:
        session_graph._request_codex_auto_title("thread", fresh=fresh, first_message=prompt)
    assert thread.called == queued
    if queued:
        assert thread.call_args.kwargs["args"] == ("thread", prompt)
        thread.return_value.start.assert_called_once()
    assert fresh["first_user_message"] == ""  # do not mutate the shared snapshot


def test_cached_row_passes_recovered_prompt_to_titler(monkeypatch):
    monkeypatch.setattr(server, "_codex_titles_snapshot", lambda: {
        "thread": {"title": "", "first_user_message": ""},
    })
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    with mock.patch.object(server, "_request_codex_auto_title") as request:
        server._rehydrate_archive_cached_rows([{
            "session_id": "thread", "engine": "codex", "mtime": 100,
            "first_message": "Explain CI runners", "display_name": "Explain CI runners",
        }])
    assert request.call_args.kwargs["first_message"] == "Explain CI runners"


def test_image_attachment_is_not_part_of_codex_fallback_name():
    prompt = "Why /tmp/pasted-images/screen.png are CI jobs running?"
    assert server._codex_display_name({}, first_message=prompt) == "Why are CI jobs running?"
    assert server._codex_display_name({}, title=prompt) == "Why are CI jobs running?"
    assert server._codex_display_name({}, override="My screenshots", title=prompt) == "My screenshots"
