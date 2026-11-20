"""Native Antigravity compaction routing and authoritative completion."""
import pytest
import server
from ccc_server import compact, engines
from ccc_server import core


def test_slash_catalog_offers_native_compact(monkeypatch):
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'antigravity')
    result = server.extract_session_slash_commands('test-session')
    assert result['engine'] == 'antigravity'
    assert [command['name'] for command in result['commands']] == ['/autocompact', '/compact']


@pytest.mark.parametrize('response', [
    {'ok': True, 'via': 'antigravity-stream'},
    {'ok': True, 'queued': True, 'via': 'antigravity-resume-queued'},
    {'ok': False, 'code': 'terminal_owned', 'error': 'Use the terminal'},
])
def test_compact_uses_native_antigravity_delivery(monkeypatch, response):
    sent = []
    monkeypatch.setattr(core, '_detect_session_engine', lambda sid: 'antigravity')
    def resume(sid, text):
        sent.append((sid, text))
        return response
    monkeypatch.setattr(core, 'resume_session_antigravity', resume)
    result = compact._compact_session_context_impl('test-session')
    assert sent == [('test-session', '/compact')]
    assert result == dict(response, compact=True, engine='antigravity')
    assert 'status' not in result  # acknowledgement is not completion


def test_native_checkpoint_proves_compaction_without_inventing_token_sizes():
    event = {'type': 'CHECKPOINT', 'source': 'SYSTEM', 'status': 'DONE',
             'created_at': '2026-01-01T01:02:03Z',
             'content': '# Resuming from a compaction\n\nPreserved progress.',
             'input_tokens': 20000, 'output_tokens': 500}
    parsed = engines._parse_antigravity_event(event, 42)
    assert parsed['type'] == 'system'
    assert parsed['subtype'] == 'compact_boundary'
    assert parsed['line'] == 42
    assert parsed['ts'] == event['created_at']
    assert 'pre_tokens' not in parsed['compact']
    assert 'post_tokens' not in parsed['compact']


@pytest.mark.parametrize('changes', [
    {'status': 'RUNNING'}, {'status': 'ERROR'}, {'content': ''},
    {'content': 'An unrelated checkpoint'},
])
def test_unfinished_or_unrelated_checkpoint_is_not_completion(changes):
    event = {'type': 'CHECKPOINT', 'status': 'DONE',
             'content': '# Resuming from a compaction\n\nSummary.', **changes}
    assert engines._parse_antigravity_event(event, 42) is None


def test_checkpoint_finishes_the_turn_without_becoming_a_fake_assistant_answer(tmp_path):
    import json
    transcript = tmp_path / 'transcript.jsonl'
    rows = [
        {'type': 'PLANNER_RESPONSE', 'source': 'MODEL', 'status': 'DONE',
         'content': 'Reply.', 'input_tokens': 250000},
        {'type': 'USER_INPUT', 'source': 'USER_EXPLICIT', 'content': '/compact'},
        {'type': 'CHECKPOINT', 'source': 'SYSTEM', 'status': 'DONE',
         'content': '# Resuming from a compaction\n\nPreserved progress.'},
    ]
    transcript.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    meta = engines._extract_antigravity_tail_meta(transcript)
    assert meta['turn_complete'] is True
    assert meta['pending_tool'] is None
    assert meta['last_assistant_text'] != rows[-1]['content']
    assert 'compact_usage_sample' not in meta
