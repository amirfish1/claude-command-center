"""Preflight reads one session and refuses to guess missing usage."""
import json
import os
import sqlite3
import time

import pytest
import server
from ccc_server import archive_store, continuation, continuation_usage, session_brief, ship_graph, usage_limit


def write_events(target, events):
    target.write_text('\n'.join(json.dumps(event) for event in events) + '\n')


def assistant(tokens):
    return {'type': 'assistant', 'message': {'model': 'test-model', 'usage': {
        'input_tokens': tokens, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 3}}}


def test_usage_cache_tracks_file_changes_and_compaction(tmp_path):
    target = tmp_path / 'session.jsonl'
    write_events(target, [assistant(100)])
    assert continuation_usage.snapshot('claude', target)['tokens'] == 123
    write_events(target, [assistant(100), {'type': 'system', 'subtype': 'compact_boundary',
                                         'compactMetadata': {'postTokens': 42}}])
    assert continuation_usage.snapshot('claude', target)['tokens'] == 42
    write_events(target, [assistant(100), {'type': 'system', 'subtype': 'compact_boundary'}])
    assert continuation_usage.snapshot('claude', target)['tokens'] is None


def test_bounded_tail_does_not_scan_back_to_old_usage(tmp_path, monkeypatch):
    target = tmp_path / 'session.jsonl'
    write_events(target, [assistant(100), {'type': 'user', 'text': 'x' * 4096}])
    monkeypatch.setattr(continuation_usage, '_TAIL_BYTES', 1024)
    assert continuation_usage.snapshot('claude', target)['tokens'] is None


def test_empty_usage_is_unknown_but_explicit_zero_is_known(tmp_path):
    target = tmp_path / 'session.jsonl'
    write_events(target, [{'type': 'assistant', 'message': {'usage': {}}}])
    assert continuation_usage.snapshot('claude', target)['tokens'] is None
    write_events(target, [{'type': 'assistant', 'message': {'usage': {'input_tokens': 0}}}])
    assert continuation_usage.snapshot('claude', target)['tokens'] == 0


def test_codex_compaction_never_reuses_pre_compaction_usage(tmp_path):
    target = tmp_path / 'rollout.jsonl'
    event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'input_tokens': 0, 'total_tokens': 4000}}}}
    write_events(target, [event])
    assert continuation_usage.snapshot('codex', target)['tokens'] == 4000
    write_events(target, [event, {'type': 'compacted', 'payload': {}}])
    assert continuation_usage.snapshot('codex', target)['tokens'] is None


def test_single_row_lookup_never_materializes_archive_or_ancestors(tmp_path, monkeypatch):
    target = tmp_path / 'archive.json'
    key = server._archive_response_cache_key()
    rows = [{'session_id': 'parent', 'first_message': 'unrelated'},
            {'session_id': 'session_child', 'parent_session_id': 'parent', 'latest_input_tokens': 4000}]
    archive_store.save(target, {'schema_version': 17, 'build_id': '17',
                               'entries': {key: {'cached_at': time.time(), 'conversations': rows}}})
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', target)
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_archive_all_rows_cached', lambda *a: pytest.fail('whole archive read'))
    decoded = []
    loads = json.loads
    monkeypatch.setattr(archive_store.json, 'loads', lambda text: decoded.append(text) or loads(text))
    assert usage_limit._usage_limit_row_for_session('child')['latest_input_tokens'] == 4000
    assert not any('unrelated' in text for text in decoded)
    assert usage_limit._usage_limit_row_for_session('missing') is None


def test_lightweight_brief_uses_indexed_identity_without_recall_enrichment(monkeypatch):
    conn = sqlite3.connect(':memory:')
    ship_graph._init_db(conn)
    conn.execute('INSERT INTO session_meta VALUES (?,?,?,?,?,?,?,?,?)',
                 ('old', 'repo', '/repo', 1, 1, '[]', '{}', '[]', ''))
    conn.execute('INSERT INTO session_meta VALUES (?,?,?,?,?,?,?,?,?)',
                 ('new', 'repo', '/repo', 2, 2, '[]', '{}', '[]', 'old'))
    monkeypatch.setattr(ship_graph, '_get_connection', lambda: conn)
    monkeypatch.setattr(ship_graph, '_sync_all', lambda *a, **k: pytest.fail('preflight index sync'))
    monkeypatch.setattr(session_brief, '_commits_with_origin', lambda *a: pytest.fail('git enrichment'))
    monkeypatch.setattr(session_brief, '_sdoc_row', lambda *a: pytest.fail('full report read'))
    try:
        result = session_brief.continuation_brief('old')
        assert result['found'] and result['latest'] == 'new' and result['cwd'] == '/repo'
        assert not session_brief.continuation_brief('00000000-0000-0000-0000-000000000000')['found']
    finally:
        conn.close()


def test_missing_archive_row_uses_actual_usage_and_file_activity(tmp_path, monkeypatch):
    from tests.test_continuation import _patch_context_sources, _brief_dict
    target = tmp_path / 'session.jsonl'
    write_events(target, [assistant(200_000)])
    stamp = time.time() - 7200
    os.utime(target, (stamp, stamp))
    _patch_context_sources(monkeypatch, {'old': _brief_dict('old', latest='new'),
                                       'new': _brief_dict('new')}, path=str(target))
    monkeypatch.setattr(ship_graph, '_get_connection', lambda: None)
    known = continuation.decide_send_path('old')
    assert known['context_known'] is True
    assert known['context_tokens'] == 200_023 and known['idle_seconds'] >= 7200
    assert known['path'] == 'new' and known['latest_session_id'] == 'new'
    write_events(target, [{'type': 'user', 'message': {'content': 'no usage'}}])
    unknown = continuation.decide_send_path('old')
    assert unknown['context_known'] is False
