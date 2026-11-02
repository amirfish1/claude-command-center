"""Behavioral contract for the isolated, nonsecret preference ledger."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ccc_server import settings_preferences as prefs


@pytest.fixture
def store(tmp_path):
    return prefs.PreferenceStore(tmp_path / 'ledger', prefs.factory_defaults(), history_limit=3)


def change(store, **values):
    return store.apply(store.preview_change(values))


def test_registry_matches_real_backing_keys_and_factory_defaults():
    root = Path(__file__).resolve().parents[1]
    browser = (root / 'static/app.js').read_text() + (root / 'static/index.html').read_text()
    server = (root / 'server.py').read_text()
    registry = prefs.registry()
    for key, spec in registry.items():
        assert spec['portable'] is True
        if spec['scope'] == 'browser':
            assert repr(spec['source_key']) in browser
        else:
            assert f'"{spec["source_key"]}": 250' in server
    assert prefs.factory_defaults()['theme'] == 'dark'
    assert prefs.factory_defaults()['rows_spacing'] == 'roomy'
    assert registry['auto_compact_k']['minimum'] == 50
    assert registry['auto_compact_k']['maximum'] == 1000
    registry['theme']['choices'].append('unregistered')
    assert 'unregistered' not in prefs.registry()['theme']['choices']


def test_reads_and_previews_create_nothing_and_are_detached(store):
    snapshot = store.snapshot()
    assert not snapshot['history'] and not snapshot['changed_from_defaults']
    assert not store.state_dir.exists()
    store.preview_change({'theme': 'light'})
    store.preview_import(store.export_profile())
    store.preview_reset()
    assert not store.state_dir.exists()
    snapshot['values']['theme'] = 'light'
    assert store.snapshot()['values']['theme'] == 'dark'


def test_effective_defaults_override_factory_and_are_detached(tmp_path):
    defaults = prefs.factory_defaults()
    defaults.update(theme='system', auto_compact_k=500)
    store = prefs.PreferenceStore(tmp_path, defaults)
    defaults['theme'] = 'light'
    assert store.snapshot()['values']['theme'] == 'system'
    result = change(store, theme='dark', auto_compact_k=250)
    assert result['changed_from_defaults'] == {
        'theme': {'value': 'dark', 'default': 'system', 'scope': 'browser'},
        'auto_compact_k': {'value': 250, 'default': 500, 'scope': 'server'},
    }
    restored = prefs.PreferenceStore(tmp_path, dict(store.defaults))
    assert restored.snapshot() == result


@pytest.mark.parametrize('values', [
    {'api_key': 'sk-ant-test-XXXX'}, {'repo_path': '/example/project'},
    {'machine_id': 'example-machine'}, {'models': {}},
    {'theme': 'sk-ant-test-XXXX'}, {'theme': ['dark']},
    {'theme': None}, {'sounds_enabled': 1}, {'sounds_enabled': '0'},
    {'auto_compact_k': True}, {'auto_compact_k': 250.0},
    {'auto_compact_k': '250'}, {'auto_compact_k': 49},
    {'worker_auto_compact_k': 1001}, {'font': 'unregistered'},
    {'theme': 'light', 'sounds_enabled': 'true'}, [],
])
def test_invalid_changes_never_create_files_or_echo_values(store, values):
    with pytest.raises(prefs.ValidationError) as error:
        store.preview_change(values)
    assert 'sk-ant-test' not in str(error.value)
    assert not store.state_dir.exists()


@pytest.mark.parametrize('defaults,limit', [({}, 3), (prefs.factory_defaults(), 0),
    (prefs.factory_defaults(), 101), (prefs.factory_defaults(), True)])
def test_invalid_constructor_is_side_effect_free(tmp_path, defaults, limit):
    target = tmp_path / 'absent'
    with pytest.raises(prefs.ValidationError):
        prefs.PreferenceStore(target, defaults, history_limit=limit)
    assert not target.exists()


def test_profile_roundtrip_merge_and_no_private_metadata(store, tmp_path):
    change(store, theme='light', sounds_enabled=False, worker_auto_compact_k=500)
    profile = store.export_profile()
    assert set(profile) == {'format', 'version', 'values'}
    assert set(profile['values']) == set(prefs.REGISTRY)
    other = prefs.PreferenceStore(tmp_path / 'other', prefs.factory_defaults())
    imported = other.apply(other.preview_import(json.dumps(profile)))
    assert imported['values'] == store.snapshot()['values']
    partial = dict(format='ccc-preferences', version=1, values={'font': 'mono'})
    assert other.apply(other.preview_import(partial))['values']['theme'] == 'light'
    raw = other.state_file.read_text()
    for forbidden in ('sk-ant-test', 'repo_path', 'machine_id', str(tmp_path), 'actor', 'hostname'):
        assert forbidden not in raw
    assert set(imported['history'][0]) == {'sequence', 'operation', 'before', 'after'}


@pytest.mark.parametrize('profile', [
    {}, {'format': 'ccc-preferences', 'version': 2, 'values': {}},
    {'format': 'ccc-preferences', 'version': True, 'values': {}},
    {'format': 'ccc-preferences', 'version': 1, 'values': {}, 'name': 'example'},
    {'format': 'ccc-preferences', 'version': 1, 'values': {'theme': 'light', 'token': 'sk-ant-test-XXXX'}},
    {'format': 'ccc-preferences', 'version': 1, 'values': {'theme': None}},
    '{"format":"ccc-preferences","version":1,"values":{"theme":"dark","theme":"light"}}',
    '{"format":"ccc-preferences","version":1,"version":1,"values":{}}',
    '{bad json', b'\xff', ' ' * (1024 * 1024 + 1),
])
def test_invalid_import_is_all_or_nothing(store, profile):
    result = change(store, font='mono')
    raw = store.state_file.read_bytes()
    with pytest.raises(prefs.ValidationError):
        store.preview_import(profile)
    assert store.state_file.read_bytes() == raw
    assert store.snapshot() == result


def test_preview_describes_typed_scope_changes_and_is_revalidated(store):
    preview = store.preview_change({'sounds_enabled': False, 'auto_compact_k': 50})
    assert preview['changes'] == [
        dict(key='auto_compact_k', scope='server', before=250, after=50,
             before_overridden=False, after_overridden=True),
        dict(key='sounds_enabled', scope='browser', before=True, after=False,
             before_overridden=False, after_overridden=True),
    ]
    tampered = deepcopy(preview)
    tampered['changes'][0]['after'] = 51
    with pytest.raises(prefs.ValidationError):
        store.apply(tampered)
    assert not store.state_dir.exists()
    tampered = deepcopy(preview)
    tampered['updates']['api_key'] = 'sk-ant-test-XXXX'
    with pytest.raises(prefs.ValidationError):
        store.apply(tampered)
    assert not store.state_dir.exists()
    store.apply(preview)


def test_stale_revision_rejected_including_aba_and_defaults_drift(store):
    old = store.preview_change({'font': 'mono'})
    initial = store.snapshot()['revision']
    change(store, theme='light')
    store.apply(store.preview_undo())
    assert store.snapshot()['values'] == prefs.factory_defaults()
    assert store.snapshot()['revision'] != initial
    with pytest.raises(prefs.StaleRevisionError):
        store.apply(old)
    fresh = store.preview_change({'font': 'mono'})
    changed_defaults = {**prefs.factory_defaults(), 'theme': 'system'}
    newer = prefs.PreferenceStore(store.state_dir, changed_defaults)
    with pytest.raises(prefs.StaleRevisionError):
        newer.apply(fresh)


def test_reset_scope_selection_and_undo_preserve_inheritance(store):
    change(store, theme='light', font='mono', auto_compact_k=500)
    preview = store.preview_reset(scope='browser')
    assert {row['key'] for row in preview['changes']} == {'theme', 'font'}
    result = store.apply(preview)
    assert result['overrides'] == {'auto_compact_k': 500}
    undo = store.apply(store.preview_undo())
    assert undo['overrides'] == {'theme': 'light', 'font': 'mono', 'auto_compact_k': 500}
    store.apply(store.preview_undo())  # undo the undo
    assert store.snapshot()['overrides'] == {'auto_compact_k': 500}
    store.apply(store.preview_reset(keys=['auto_compact_k']))
    assert store.snapshot()['overrides'] == {}
    assert store.snapshot()['changed_from_defaults'] == {}


@pytest.mark.parametrize('selection', [dict(scope='repo'), dict(keys=['token']),
    dict(keys=['theme', 'theme']), dict(keys='theme'), dict(keys=[[]]),
    dict(scope='browser', keys=['theme'])])
def test_reset_invalid_selection_never_writes(store, selection):
    with pytest.raises(prefs.ValidationError):
        store.preview_reset(**selection)
    assert not store.state_dir.exists()


def test_noops_history_bounds_and_explicit_default_overrides(store):
    revision = store.snapshot()['revision']
    assert store.apply(store.preview_reset())['revision'] == revision
    assert not store.state_dir.exists()
    with pytest.raises(prefs.ValidationError):
        store.preview_undo()
    result = change(store, theme='dark')
    assert not result['changed_from_defaults']
    assert result['overrides'] == {'theme': 'dark'}
    assert len(store.preview_reset()['changes']) == 1
    assert change(store, theme='dark')['revision'] == result['revision']
    for index in range(8):
        change(store, theme='light' if index % 2 == 0 else 'system')
    history = store.snapshot()['history']
    assert len(history) == 3
    assert [entry['sequence'] for entry in history] == [7, 8, 9]
    # Reload validates the retained chain, and history reads are detached.
    assert prefs.PreferenceStore(store.state_dir, prefs.factory_defaults()).snapshot()['history'] == history
    history[0]['before']['theme'] = 'mono'
    assert store.snapshot()['history'][0]['before']['theme'] != 'mono'


def test_failed_atomic_replace_preserves_state_and_history(store, monkeypatch):
    change(store, theme='light')
    before = store.state_file.read_bytes()
    preview = store.preview_change({'font': 'mono'})
    def fail_replace(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(prefs.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        store.apply(preview)
    assert store.state_file.read_bytes() == before
    assert not list(store.state_dir.glob('.preferences-*'))
    if os.name != 'nt':
        assert store.state_file.stat().st_mode & 0o777 == 0o600


def test_simultaneous_instances_cannot_overwrite_one_revision(store):
    preview = store.preview_change({'theme': 'light'})
    def attempt(_):
        instance = prefs.PreferenceStore(store.state_dir, prefs.factory_defaults())
        try:
            instance.apply(preview)
            return 'applied'
        except prefs.StaleRevisionError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(attempt, range(24)))
    assert outcomes.count('applied') == 1
    assert outcomes.count('stale') == 23
    assert len(store.snapshot()['history']) == 1


def test_revision_is_rechecked_after_acquiring_writer_lock(store, monkeypatch):
    preview = store.preview_change({'theme': 'light'})
    original_lock = prefs._lock

    @contextmanager
    def intervening_lock(directory):
        # Simulate another writer winning between pre-validation and locking.
        with monkeypatch.context() as patch:
            patch.setattr(prefs, '_lock', original_lock)
            change(store, font='mono')
        with original_lock(directory):
            yield

    monkeypatch.setattr(prefs, '_lock', intervening_lock)
    with pytest.raises(prefs.StaleRevisionError):
        store.apply(preview)
    assert store.snapshot()['overrides'] == {'font': 'mono'}
    assert len(store.snapshot()['history']) == 1


def test_undo_and_preview_metadata_cannot_be_forged(store):
    change(store, theme='light')
    before = store.state_file.read_bytes()
    undo = store.preview_undo()
    undo['updates']['theme'] = 'system'
    with pytest.raises(prefs.ValidationError):
        store.apply(undo)
    preview = store.preview_change({'font': 'mono'})
    preview['actor'] = 'example-machine'
    with pytest.raises(prefs.ValidationError):
        store.apply(preview)
    assert store.state_file.read_bytes() == before


def test_processes_share_revision_lock(store):
    preview = json.dumps(store.preview_change({'theme': 'light'}))
    script = '''
import json, sys
from ccc_server.settings_preferences import PreferenceStore, factory_defaults, StaleRevisionError
try:
    PreferenceStore(sys.argv[1], factory_defaults()).apply(json.loads(sys.argv[2]))
    print('applied')
except StaleRevisionError:
    print('stale')
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(store.state_dir), preview],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(8)]
    outputs = []
    for process in processes:
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, err
        outputs.append(out.strip())
    assert outputs.count('applied') == 1
    assert outputs.count('stale') == 7
    assert len(store.snapshot()['history']) == 1


@pytest.mark.parametrize('damage', ['json', 'unknown', 'history', 'extra', 'boolean_version'])
def test_corrupt_ledger_fails_closed_without_overwriting(store, damage):
    change(store, theme='light')
    state = json.loads(store.state_file.read_text())
    if damage == 'json':
        store.state_file.write_text('{broken')
    else:
        if damage == 'unknown':
            state['overrides']['token'] = 'sk-ant-test-XXXX'
        elif damage == 'history':
            state['history'][-1]['after']['theme'] = 'dark'
        elif damage == 'extra':
            state['hostname'] = 'example-machine'
        else:
            state['version'] = True
        store.state_file.write_text(json.dumps(state))
    raw = store.state_file.read_bytes()
    with pytest.raises(prefs.ValidationError):
        store.snapshot()
    with pytest.raises(prefs.ValidationError):
        store.preview_reset()
    assert store.state_file.read_bytes() == raw
