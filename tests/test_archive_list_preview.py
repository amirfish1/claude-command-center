"""Sidebar list rows carry a short preview of the last assistant reply."""
import importlib


def test_preview_strips_state_block_and_markdown():
    server = importlib.import_module('server')
    text = ("**Done.** Fixed the `send` button. See [the PR](https://x/1).\n\n"
            "<session-state>\nDID: x\n</session-state>")
    assert server._archive_list_preview(text) == 'Done. Fixed the send button. See the PR.'


def test_preview_caps_length_and_handles_empty():
    server = importlib.import_module('server')
    out = server._archive_list_preview('word ' * 100)
    assert len(out) == 140 and out.endswith('…')
    assert server._archive_list_preview('') == ''
    assert server._archive_list_preview(None) == ''


def test_projection_adds_preview_only_when_there_is_text():
    server = importlib.import_module('server')
    row = {'session_id': 's1', 'parent_session_id': 'p', 'last_assistant_text': 'All set.'}
    out = server._archive_list_project_row(row)
    assert out['last_assistant_preview'] == 'All set.'
    assert 'last_assistant_text' not in out
    assert 'last_assistant_preview' not in server._archive_list_project_row({'session_id': 's2', 'parent_session_id': 'p'})
