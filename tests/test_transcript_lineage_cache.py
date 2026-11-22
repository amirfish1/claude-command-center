"""Lineage stays correct without decoding unchanged archive transcripts."""
import json
from pathlib import Path

import pytest

import server
from ccc_server import conv_meta_store, engines, log_parse


@pytest.fixture
def head(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_conv_meta_cache', {})
    monkeypatch.setattr(server, '_codex_tail_resume', {})
    monkeypatch.setattr(server, '_conv_meta_cache_dirty', False)
    monkeypatch.setattr(log_parse, '_conv_meta_dirty_paths', set())
    monkeypatch.setattr(server, '_CONV_META_CACHE_FILE', tmp_path / 'metadata.json')
    return tmp_path / 'rollout.jsonl'


def codex(text, role='user'):
    return {'type': 'response_item', 'payload': {'type': 'message', 'role': role,
            'content': [{'type': 'input_text', 'text': text}]}}


def write(head, events):
    head.write_text(''.join(json.dumps(e) + '\n' for e in events))


def test_codex_skips_setup_and_tool_records_but_preserves_full_prompt_lineage(head, monkeypatch):
    write(head, [codex('Setup instruction ' * 10000),
                 codex('Origin session id: false-tool-origin', role='assistant'),
                 codex('Long preamble ' * 100 + '\nOrigin session id: origin-12345678')])
    decodes = []
    original = engines.json.loads
    def decode(raw, *args, **kw):
        decodes.append(len(raw))
        return original(raw, *args, **kw)
    monkeypatch.setattr(engines.json, 'loads', decode)
    assert server._continued_from_session_id_from_transcript(head) == 'origin-12345678'
    assert len(decodes) == 2, 'large marker-free Codex setup must not be JSON-decoded'
    assert max(decodes) < 10000


def test_claude_stops_at_first_non_meta_user_and_shares_both_lookups(head):
    write(head, [{'type': 'user', 'isMeta': True, 'message': {'content': 'Origin session id: ignored-123456'}},
                 {'type': 'user', 'message': {'content': 'Ordinary first prompt'}},
                 {'type': 'user', 'message': {'content': 'Origin session id: later-12345678'}}])
    assert server._continued_from_session_id_from_transcript(head) == ''
    assert server._parent_session_id_from_transcript_return_address(head) == ''
    write(head, [{'type': 'user', 'message': {'content':
        'Origin session id: origin-12345678\n\nReturn address: dispatched by another CCC session (id `parent-12345678`)'}}])
    assert server._continued_from_session_id_from_transcript(head) == 'origin-12345678'
    assert server._parent_session_id_from_transcript_return_address(head) == 'parent-12345678'



def test_unicode_escaped_marker_is_not_filtered_out(head):
    head.write_text(json.dumps(codex('Origin session id: origin-12345678')).replace('Origin', r'\u004frigin') + '\n')
    assert server._continued_from_session_id_from_transcript(head) == 'origin-12345678'


def test_twentieth_record_is_included_but_later_records_are_not(head):
    write(head, [{}] * 19 + [codex('Origin session id: twentieth-123456')])
    assert server._continued_from_session_id_from_transcript(head) == 'twentieth-123456'
    write(head, [{}] * 20 + [codex('Origin session id: too-late-123456')])
    assert server._continued_from_session_id_from_transcript(head) == ''


@pytest.mark.parametrize('text,expected', [('No continuation marker', ''),
        ('Origin session id: origin-12345678', 'origin-12345678')])
def test_positive_and_negative_results_survive_metadata_reload_without_open(head, monkeypatch, text, expected):
    write(head, [codex(text)])
    server._conv_meta_cache[str(head)] = {'mtime': head.stat().st_mtime, 'parser_field': 'preserved'}
    assert server._continued_from_session_id_from_transcript(head) == expected
    server._save_conv_meta_cache()
    server._conv_meta_cache.clear()
    server._load_conv_meta_cache()
    assert server._conv_meta_cache[str(head)]['parser_field'] == 'preserved'
    original = Path.open
    def guarded(candidate, *args, **kw):
        assert candidate != head, 'unchanged cached transcript was reopened'
        return original(candidate, *args, **kw)
    monkeypatch.setattr(Path, 'open', guarded)
    assert server._continued_from_session_id_from_transcript(head) == expected
    assert server._parent_session_id_from_transcript_return_address(head) == ''


def test_append_and_rewrite_invalidate_negative_and_positive_results(head):
    write(head, [codex('Setup')])
    assert server._continued_from_session_id_from_transcript(head) == ''
    with head.open('a') as out:
        out.write(json.dumps(codex('Origin session id: appended-123456')) + '\n')
    assert server._continued_from_session_id_from_transcript(head) == 'appended-123456'
    write(head, [codex('A rewritten ordinary prompt')])
    assert server._continued_from_session_id_from_transcript(head) == ''


def test_missing_file_is_not_a_cached_negative(head):
    assert server._continued_from_session_id_from_transcript(head) == ''
    assert str(head) not in server._conv_meta_cache
    write(head, [codex('Origin session id: created-12345678')])
    assert server._continued_from_session_id_from_transcript(head) == 'created-12345678'


def test_codex_only_parser_updates_persist_without_a_claude_parse(head, monkeypatch):
    write(head, [codex('An ordinary prompt'), {'type': 'event_msg', 'payload': {
        'type': 'agent_message', 'message': 'A reply'}}])
    meta = server._extract_codex_tail_meta(head)
    assert meta['engine'] == 'codex'
    assert server._conv_meta_cache_dirty is True
    server._save_conv_meta_cache()
    assert server._conv_meta_cache_dirty is False
    server._conv_meta_cache.clear()
    server._codex_tail_resume.clear()
    server._load_conv_meta_cache()
    original = Path.open
    def guarded(candidate, *args, **kw):
        assert candidate != head, 'persisted Codex tail metadata was discarded'
        return original(candidate, *args, **kw)
    monkeypatch.setattr(Path, 'open', guarded)
    assert server._extract_codex_tail_meta(head) == meta


def test_metadata_cache_retries_and_preserves_other_writers(head, monkeypatch):
    target = server._CONV_META_CACHE_FILE
    version = log_parse._CONV_META_SCHEMA_VERSION
    previous = {'old': {'mtime': 1, 'title': 'Résumé\n"quoted"'}}
    target.write_text(json.dumps({'schema_version': version, 'entries': previous}))
    server._load_conv_meta_cache()
    other = {'second-process': {'mtime': 2}}
    conv_meta_store.save(target, version, other)
    changes = {str(head): {'mtime': 3, 'cache_key': (3, 8)}}
    server._conv_meta_cache.update(changes)
    log_parse._mark_conv_meta_dirty(str(head))

    def fail(*args, **kwargs):
        raise OSError('test write failure')

    with monkeypatch.context() as failure:
        failure.setattr(conv_meta_store, 'save', fail)
        server._save_conv_meta_cache()
    assert server._conv_meta_cache_dirty
    server._save_conv_meta_cache()
    assert not server._conv_meta_cache_dirty

    server._conv_meta_cache.clear()
    server._load_conv_meta_cache(lazy=True)
    assert not server._conv_meta_cache
    for path, meta in (previous | other | changes).items():
        assert server._conv_meta_cache.get(path) == meta


def test_changed_during_read_does_not_certify_a_partial_head(head, monkeypatch):
    write(head, [codex('Setup')])
    original = Path.stat
    calls = []
    def stat(candidate, *args, **kw):
        if candidate == head:
            calls.append(1)
            if len(calls) == 2:
                with head.open('a') as out:
                    out.write(json.dumps(codex('Origin session id: appended-123456')) + '\n')
        return original(candidate, *args, **kw)
    monkeypatch.setattr(Path, 'stat', stat)
    assert server._continued_from_session_id_from_transcript(head) == ''
    assert str(head) not in server._conv_meta_cache
    assert server._continued_from_session_id_from_transcript(head) == 'appended-123456'


def test_signature_match_persists_lineage_discovered_during_rehydration(head, monkeypatch):
    write(head, [codex('Origin session id: cached-12345678')])
    monkeypatch.setattr(server, '_ARCHIVE_SERVE_TTL', 0)
    monkeypatch.setattr(server, '_archive_corpus_signature', lambda: 'same')
    monkeypatch.setattr(server, '_archive_response_cache_get', lambda key: {
        'signature': 'same', 'conversations': [{'session_id': 'child-12345678'}]})
    def hydrate(rows):
        assert server._continued_from_session_id_from_transcript(head) == 'cached-12345678'
        return rows
    monkeypatch.setattr(server, '_rehydrate_archive_cached_rows', hydrate)
    server._archive_compute_rows('test-lineage', {})
    server._conv_meta_cache.clear()
    server._load_conv_meta_cache()
    assert server._conv_meta_cache[str(head)]['lineage_head']['origin'] == 'cached-12345678'
