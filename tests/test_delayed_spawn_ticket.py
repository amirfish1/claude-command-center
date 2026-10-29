"""UI origin and native session IDs may arrive in either order."""

import json

import server
from ccc_server import idea_tickets, usage_limit


def _registry(monkeypatch, tmp_path, rows):
    registry = tmp_path / 'spawned-pids.json'
    registry.write_text(json.dumps(rows))
    monkeypatch.setattr(server, 'SPAWNED_PIDS_FILE', registry)
    noted = []
    monkeypatch.setattr(idea_tickets, 'note_ui_spawn', lambda sid, via: noted.append((sid, via)))
    return registry, noted


def test_native_id_arrives_after_ui_tag(monkeypatch, tmp_path):
    registry, noted = _registry(monkeypatch, tmp_path, [
        {'pid': 4242, 'engine': 'antigravity', 'spawned_via': 'ui'},
    ])
    usage_limit._update_spawn_session_id_in_registry(4242, 'native-id')
    assert json.loads(registry.read_text())[0]['session_id'] == 'native-id'
    assert noted == [('native-id', 'ui')]


def test_ui_tag_resolves_id_that_arrived_after_response(monkeypatch, tmp_path):
    _, noted = _registry(monkeypatch, tmp_path, [
        {'pid': 4242, 'session_id': 'native-id', 'spawned_via': 'ui'},
    ])
    server._tag_spawned_via_in_registry(pid=4242, via='ui')
    assert noted == [('native-id', 'ui')]


def test_delayed_external_id_keeps_external_origin(monkeypatch, tmp_path):
    _, noted = _registry(monkeypatch, tmp_path, [
        {'pid': 4242, 'engine': 'antigravity', 'spawned_via': 'watchtower'},
    ])
    usage_limit._update_spawn_session_id_in_registry(4242, 'native-id')
    assert noted == [('native-id', 'watchtower')]


def test_antigravity_opening_text_is_used_for_manual_filing(monkeypatch, tmp_path):
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({
        'type': 'USER_INPUT',
        'content': '<USER_REQUEST>Fix the broken menu</USER_REQUEST>\n'
                   '<ADDITIONAL_METADATA>metadata</ADDITIONAL_METADATA>',
    }) + '\n')
    monkeypatch.setattr(server, '_find_session_jsonl', lambda sid: None)
    monkeypatch.setattr(server, '_antigravity_transcript_path', lambda sid: transcript)
    monkeypatch.setattr(server, '_spawn_registry_entry_for_session', lambda sid: {})
    monkeypatch.setattr(idea_tickets, '_claim', lambda *a, **kw: True)
    captured = []
    monkeypatch.setattr(idea_tickets, '_file_new', lambda *a: captured.append(a))
    idea_tickets.file_ticket('native-id', 'SMALL-BUGS')
    assert captured[0][2] == 'Fix the broken menu'
