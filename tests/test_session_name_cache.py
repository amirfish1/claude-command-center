"""Live rename freshness and isolation for the shared sidebar name reader."""
import concurrent.futures
import json
import os

import pytest

import server
from ccc_server import log_parse


@pytest.fixture
def names_file(tmp_path, monkeypatch):
    filename = tmp_path / 'names.json'
    monkeypatch.setattr(log_parse, 'SESSION_NAMES_FILE', filename)
    monkeypatch.setattr(log_parse, '_session_names_cache', {'key': None, 'names': {}})
    return filename


def test_concurrent_readers_normalize_once_and_get_isolated_copies(names_file, monkeypatch):
    names_file.write_text(json.dumps({'session': 'Long title ' * 40, 'empty': ''}))
    calls = []
    truncate = server._truncate_session_name

    def tracked(value):
        calls.append(value)
        return truncate(value)

    monkeypatch.setattr(server, '_truncate_session_name', tracked)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: log_parse._load_session_name_overrides(), range(16)))
    assert len(calls) == 1
    assert all(result == {'session': truncate('Long title ' * 40)} for result in results)
    results[0]['session'] = 'Caller mutation'
    results[1]['new'] = 'Caller insertion'
    assert log_parse._load_session_name_overrides() == results[2]


def test_atomic_peer_rename_with_same_size_and_mtime_is_visible(names_file):
    names_file.write_text('{"session":"Old"}')
    assert log_parse._load_session_name_overrides() == {'session': 'Old'}
    previous = names_file.stat()
    replacement = names_file.with_suffix('.replacement')
    replacement.write_text('{"session":"New"}')
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    replacement.replace(names_file)
    assert log_parse._load_session_name_overrides() == {'session': 'New'}
    names_file.write_text('{"session":"Now"}')
    assert log_parse._load_session_name_overrides() == {'session': 'Now'}


def test_missing_invalid_and_repaired_files_never_replay_old_names(names_file):
    names_file.write_text('{"session":"Old"}')
    assert log_parse._load_session_name_overrides() == {'session': 'Old'}
    names_file.unlink()
    assert log_parse._load_session_name_overrides() == {}
    for invalid in ('{', '[]', 'null'):
        names_file.write_text(invalid)
        assert log_parse._load_session_name_overrides() == {}
    names_file.write_text('{"session":"New"}')
    assert log_parse._load_session_name_overrides() == {'session': 'New'}


def test_cache_tracks_file_path_and_title_limit(names_file, monkeypatch):
    names_file.write_text('{"session":"Long name"}')
    assert log_parse._load_session_name_overrides() == {'session': 'Long name'}
    monkeypatch.setattr(server, 'SESSION_NAME_MAX_CHARS', 5)
    assert log_parse._load_session_name_overrides() == {'session': 'Long…'}
    other = names_file.with_name('other.json')
    other.write_text('{"session":"Other"}')
    monkeypatch.setattr(log_parse, 'SESSION_NAMES_FILE', other)
    assert log_parse._load_session_name_overrides() == {'session': 'Other'}
