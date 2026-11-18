"""Footer scanning preserves comment, malformed-tag and last-footer behavior."""
import pytest
import server  # Initialize the shared core before extracted modules.
from ccc_server import session_graph


@pytest.mark.parametrize('text', [
    '',
    'Ordinary reply. ' * 10000,
    '<session-state> DID: Saved. </session-state>',
    '<!-- <session-state>\nDID: Saved.\n</session-state> -->',
    '<!--\n\n<SESSION-STATE>\nDID: Saved.\n</SESSION-STATE>\n-->',
    '<ſeſſion-ſtate>\nDID: Saved.\n</ſeſſion-ſtate>',
    '<session-state>\nDID: First.\n</session-state>'
    '<!-- <session-state>\nDID: Last.\n</session-state> -->',
    '<session-state>\nDID: Complete.\n</session-state>'
    '<session-state>\nDID: Incomplete.',
    '<session-state>\nDID: Outer.\n<session-state>\nDID: Inner.\n</session-state>',
    '<session-state>\nDID: First.\n</session-state>\n-->'
    '<!--<session-state>\nDID: Last.\n</session-state>',
    '<session-state>\nDID: Saved.\nNEXT_STEP_USER: Choose.\n'
    'NEXT_ACTION: {"label":"Continue","reply":"Continue the task."}\n</session-state>',
])
def test_parser_matches_comment_aware_footer_bodies(text, monkeypatch):
    actual = session_graph._parse_session_state(text)
    # The stripping expression remains the compatibility reference for which
    # complete footer wins, including nested and incomplete trailing tags.
    monkeypatch.setattr(session_graph, '_SESSION_STATE_PARSE_RE',
                        session_graph._SESSION_STATE_RE)
    assert actual == session_graph._parse_session_state(text)


def test_comment_wrapper_is_removed_from_visible_reply():
    text = 'Answer.\n<!-- <session-state>\nDID: Saved.\n</session-state> -->'
    assert session_graph._parse_session_state(text)['did'] == 'Saved.'
    assert session_graph._SESSION_STATE_RE.sub('', text).strip() == 'Answer.'


def test_last_complete_footer_wins_over_incomplete_tail():
    text = ('<session-state>\nDID: First.\n</session-state>'
            '<!-- <session-state>\nDID: Last.\n</session-state> -->'
            '<session-state>\nDID: Incomplete.')
    assert session_graph._parse_session_state(text)['did'] == 'Last.'
