"""Sidebar list rows carry a short preview of the last assistant reply."""
import importlib
import random
import re


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


def test_preview_preserves_text_around_commented_or_unfinished_state():
    server = importlib.import_module('server')
    assert server._archive_list_preview(
        'Before. <!--\n <session-state>Private status</session-state> --> After.'
    ) == 'Before. After.'
    assert server._archive_list_preview('Visible reply. <session-state>Unfinished status') == 'Visible reply.'
    assert server._archive_list_preview('**שלום.** Read [the notes](https://example.invalid/notes).') == 'שלום. Read the notes.'


def test_projection_adds_preview_only_when_there_is_text():
    server = importlib.import_module('server')
    row = {'session_id': 's1', 'parent_session_id': 'p', 'last_assistant_text': 'All set.'}
    out = server._archive_list_project_row(row)
    assert out['last_assistant_preview'] == 'All set.'
    assert 'last_assistant_text' not in out
    assert 'last_assistant_preview' not in server._archive_list_project_row({'session_id': 's2', 'parent_session_id': 'p'})


def test_preview_matches_full_cleanup_for_prefixes_and_link_boundaries():
    server = importlib.import_module('server')
    state_re = re.compile(r"(?:<!--\s*)?<session-state>.*?(</session-state>\s*-->|</session-state>|$)", re.S)
    markdown_re = re.compile(r"[`*_#>|]+|\[([^\]]*)\]\([^)]*\)")

    def full_cleanup(text, limit):
        clean = state_re.sub(' ', text[:2000])
        clean = markdown_re.sub(lambda match: match.group(1) or ' ', clean)
        clean = ' '.join(clean.split())
        return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + '…'

    texts = [
        'word **bold** `code` ' * 150,
        ' \t\n*_#>|' * 100 + 'Visible reply. ' * 40,
        '[**caption**](https://example.invalid/) ' * 80,
        '[unclosed ' * 100,
        'x' * 279 + '[caption](https://example.invalid/) tail',
        'שלום 🐟 café\u2003**text** ' * 150,
        'Visible. <session-state>' + 'hidden ' * 400,
        'a' * 1990 + '[caption](https://example.invalid/)',
    ]
    rng = random.Random(0)
    alphabet = list('ab []()`*_#>|\t\n') + ['שלום', '🐟', '\u2003', '\u0301']
    texts.extend(''.join(rng.choices(alphabet, k=2400)) for _ in range(40))
    for limit in (0, 1, 2, 7, 140, 500):
        for text in texts:
            assert server._archive_list_preview(text, limit) == full_cleanup(text, limit)
