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


@pytest.mark.parametrize(('text', 'expected'), [
    ('Ordinary reply.\n\t', 'Ordinary reply.'),
    ('Before your final reply, check the build.\n', 'Before your final reply, check the build.'),
    ('Mention </session-state> in the docs.', 'Mention </session-state> in the docs.'),
    ('Reply.\n<ccc-session-state-instructions>Reminder.</ccc-session-state-instructions>\u2003', 'Reply.'),
    ('Reply.\nBEFORE YOUR FINAL REPLY\n<!-- <SESSION-STATE>Reminder.</ſESSION-STATE>\n-->\n', 'Reply.'),
    ('Reply.\nBefore your final reply\n</session-state>\n'
     '<ccc-session-state-instructions>Reminder.</ccc-session-state-instructions>\n', 'Reply.'),
    ('Reply.\n<ccc-session-state-instructions>Reminder.</ccc-session-state-instructions>\nKeep this.',
     'Reply.\n<ccc-session-state-instructions>Reminder.</ccc-session-state-instructions>\nKeep this.'),
])
def test_prompt_trailer_stripping_preserves_visible_text(text, expected):
    assert session_graph._strip_ccc_session_state_instruction(text) == expected
