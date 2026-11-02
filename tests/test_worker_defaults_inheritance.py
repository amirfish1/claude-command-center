"""Explicit inheritance persists the flat fields consumed by worker launchers."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import server
from ccc_server.queue_events import compute_queue_worker_plan

FIELDS = server._WORKER_DEFAULT_FIELDS


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'SPAWN_DEFAULTS_FILE', tmp_path / 'spawn-defaults.json')
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: False)
    return server.SPAWN_DEFAULTS_FILE


def read(state):
    return json.loads(state.read_text())


def seed(state):
    raw = server._factory_spawn_defaults()
    raw.update(engine='codex', reasoning_effort='high', auto_compact_k=300,
               worker_engine='claude', worker_model='custom-worker-model',
               worker_reasoning_effort='max', worker_auto_compact_k=600)
    raw['models']['codex'] = 'custom-session-model'
    # Unknown routing and metadata must survive an ordinary policy edit.
    raw['worker_fallback'] = {'enabled': False, 'models': [], 'future': {'strategy': 'custom'}}
    raw['model_profiles'] = {'future-profile': {'models': [], 'custom': [1, 2]}}
    raw['future_policy'] = {'version': 9}
    raw['models']['future-engine'] = 'custom-model'
    state.write_text(json.dumps(raw))
    return raw


def worker(raw):
    return {key: raw[key] for key in FIELDS}


def test_missing_flag_is_independent_and_does_not_migrate(state):
    raw = seed(state)
    before = state.read_bytes()
    assert 'worker_inherit_session' not in server._load_spawn_defaults()
    assert state.read_bytes() == before
    saved = server._save_spawn_defaults({'models': {'codex': 'another-model'}, 'auto_compact_k': 350})
    assert saved['ok']
    assert worker(read(state)) == worker(raw)
    assert 'worker_inherit_session' not in read(state)
    assert 'worker_overrides' not in read(state)


def test_opt_in_materializes_and_future_saves_follow_then_restore(state):
    raw = seed(state)
    saved = server._save_spawn_defaults({'worker_inherit_session': True})
    assert saved['ok']
    assert worker(read(state)) == dict(worker_engine='codex', worker_model='custom-session-model',
                                      worker_reasoning_effort='high', worker_auto_compact_k=300)
    assert read(state)['worker_overrides'] == worker(raw)
    assert server._save_spawn_defaults({'engine': 'claude', 'models': {'claude': 'custom-claude'},
                                       'reasoning_effort': 'max', 'auto_compact_k': 400})['ok']
    assert worker(read(state)) == dict(worker_engine='claude', worker_model='custom-claude',
                                      worker_reasoning_effort='max', worker_auto_compact_k=400)
    # An old caller can echo effective values without overwriting the snapshot.
    assert server._save_spawn_defaults({**worker(read(state)), 'auto_compact_k': 410})['ok']
    assert read(state)['worker_auto_compact_k'] == 410
    assert read(state)['worker_overrides'] == worker(raw)
    assert server._save_spawn_defaults({'worker_inherit_session': False})['ok']
    assert worker(read(state)) == worker(raw)
    assert read(state)['auto_compact_k'] == 410
    assert server._save_spawn_defaults({'worker_model': 'new-independent'})['ok']
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    assert server._save_spawn_defaults({'worker_inherit_session': False})['ok']
    assert read(state)['worker_model'] == 'new-independent'


def test_unknown_policy_and_unknown_legacy_worker_values_survive(state):
    raw = seed(state)
    raw.update(worker_engine='future-worker', worker_reasoning_effort='future-effort', worker_auto_compact_k=0)
    raw['worker_overrides'] = {'future': {'keep': True}}
    state.write_text(json.dumps(raw))
    for change in ({'auto_compact_k': 330}, {'worker_inherit_session': True},
                   {'auto_compact_k': 340}, {'worker_inherit_session': False}):
        assert server._save_spawn_defaults(change)['ok']
        now = read(state)
        for key in ('worker_fallback', 'model_profiles', 'future_policy'):
            assert now[key] == raw[key]
        assert now['models']['future-engine'] == 'custom-model'
    assert worker(read(state)) == worker(raw)
    assert read(state)['worker_overrides']['future'] == {'keep': True}


@pytest.mark.parametrize('change', [
    {'worker_inherit_session': 'true'}, {'worker_inherit_session': 1},
    {'worker_overrides': {}}, {'worker_inherit_session': True, 'engine': 'invalid'},
    {'worker_inherit_session': True, 'reasoning_effort': 'impossible'},
    {'worker_inherit_session': True, 'worker_engine': 'kimi'},
])
def test_invalid_transition_does_not_write(state, change):
    seed(state)
    before = state.read_bytes()
    assert not server._save_spawn_defaults(change)['ok']
    assert state.read_bytes() == before


def test_invalid_first_save_does_not_seed_and_invalid_old_file_does_not_migrate(state):
    assert not server._save_spawn_defaults({'worker_inherit_session': 'yes'})['ok']
    assert not state.exists()
    state.write_text(json.dumps({'engine': 'claude', 'models': {'claude': 'custom'}}))
    before = state.read_bytes()
    assert not server._save_spawn_defaults({'worker_inherit_session': []})['ok']
    assert state.read_bytes() == before


def test_inherited_worker_edits_require_explicit_opt_out(state):
    seed(state)
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    before = state.read_bytes()
    assert not server._save_spawn_defaults({'worker_model': 'different'})['ok']
    assert state.read_bytes() == before
    assert server._save_spawn_defaults({'worker_inherit_session': False, 'worker_model': 'different'})['ok']
    assert read(state)['worker_engine'] == 'claude'
    assert read(state)['worker_model'] == 'different'


def test_corrupt_snapshot_cannot_silently_discard_overrides(state):
    raw = seed(state)
    raw.update(worker_inherit_session=True, worker_overrides={'worker_engine': 'claude'})
    state.write_text(json.dumps(raw))
    before = state.read_bytes()
    assert not server._save_spawn_defaults({'worker_inherit_session': False})['ok']
    assert state.read_bytes() == before


def test_engine_only_legacy_api_save_materializes_compatible_effort(state):
    seed(state)
    assert server._save_spawn_defaults({'worker_inherit_session': True, 'engine': 'claude', 'reasoning_effort': 'max'})['ok']
    assert server._save_spawn_defaults({'engine': 'codex'})['ok']
    assert read(state)['worker_engine'] == 'codex'
    assert read(state)['worker_reasoning_effort'] == ''
    assert read(state)['reasoning_effort'] == ''
    assert read(state)['worker_model'] == 'custom-session-model'


def test_atomic_transition_and_concurrent_partial_saves(state, monkeypatch):
    seed(state)
    replacements = []
    replace = Path.replace
    def capture(source, target):
        if target == state:
            raw = json.loads(source.read_text())
            if raw.get('worker_inherit_session'):
                assert raw['worker_engine'] == raw['engine']
                assert raw['worker_model'] == raw['models'][raw['engine']]
                assert raw['worker_auto_compact_k'] == raw['auto_compact_k']
            replacements.append(raw)
        return replace(source, target)
    monkeypatch.setattr(Path, 'replace', capture)
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    assert len(replacements) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(server._save_spawn_defaults, change) for change in
                ({'auto_compact_k': 370}, {'models': {'codex': 'concurrent-model'}})]
        assert all(job.result()['ok'] for job in jobs)
    assert read(state)['worker_auto_compact_k'] == 370
    assert read(state)['worker_model'] == 'concurrent-model'
    assert not list(state.parent.glob('*.tmp'))


def test_queue_pins_win_and_fallback_profiles_remain_independent(state):
    raw = seed(state)
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    stored = read(state)
    inherited = compute_queue_worker_plan({}, stored)
    assert inherited['engine'] == 'codex'
    assert inherited['model'] == 'custom-session-model'
    pinned = compute_queue_worker_plan({'engine': 'claude', 'model': 'pinned-model', 'effort': 'low'}, stored)
    assert pinned['engine'] == 'claude'
    assert pinned['model'] == 'pinned-model'
    assert pinned['effort'] == 'low'
    assert stored['worker_fallback'] == raw['worker_fallback']
    assert stored['model_profiles'] == raw['model_profiles']


def test_watchtower_reads_materialized_fields_directly(state, monkeypatch):
    # Optional consumer contract: import the installed reader, never edit its
    # repository and redirect every configuration path to this test directory.
    wt = pytest.importorskip('watchtower.config')
    monkeypatch.setattr(wt, 'CCC_SPAWN_DEFAULTS_FILE', state)
    monkeypatch.setattr(wt, 'CONFIG_FILE', state.parent / 'queue-config.json')
    monkeypatch.setattr(wt, 'CCC_MODEL_POLICY_FILE', state.parent / 'policy.json')
    wt.CONFIG_FILE.write_text(json.dumps({'PINNED': {'engine': 'claude'}}))
    seed(state)
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    assert wt._ccc_worker_engine_default() == 'codex'
    assert wt._ccc_worker_model_default('codex') == 'custom-session-model'
    assert wt._ccc_worker_model_default('claude') == ''
    assert wt._ccc_worker_effort_default() == 'high'
    assert wt.worker_auto_compact_tokens() == 300000
    assert wt.engine('PINNED') == 'claude'
    assert server._save_spawn_defaults({'models': {'codex': 'changed'}, 'auto_compact_k': 350})['ok']
    assert wt._ccc_worker_model_default('codex') == 'changed'
    assert wt.worker_auto_compact_tokens() == 350000


def test_model_policy_fallback_matches_new_sessions(state, monkeypatch):
    seed(state)
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: model == 'custom-session-model')
    monkeypatch.setattr(server, '_spawn_fallback_model_for_engine', lambda engine: 'allowed-fallback')
    saved = server._save_spawn_defaults({'worker_inherit_session': True})
    assert saved['ok']
    assert read(state)['models']['codex'] == 'custom-session-model'
    assert read(state)['worker_model'] == server._spawn_default_model_for_engine('codex', read(state)) == 'allowed-fallback'


def test_migration_preserves_unknown_policy_and_worker_fields(state):
    raw = seed(state)
    del raw['models']['pi']
    raw.update(worker_engine='future-worker', worker_reasoning_effort='future-effort')
    state.write_text(json.dumps(raw))
    server._load_spawn_defaults()
    migrated = read(state)
    assert worker(migrated) == worker(raw)
    for key in ('worker_fallback', 'model_profiles', 'future_policy'):
        assert migrated[key] == raw[key]
    assert migrated['models']['future-engine'] == 'custom-model'
    assert 'pi' in migrated['models']
    assert 'worker_inherit_session' not in migrated


def test_metadata_echo_and_read_only_worker_environment_use_effective_values(state, monkeypatch):
    seed(state)
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    loaded = server._load_spawn_defaults()
    assert server._save_spawn_defaults({'worker_overrides': loaded['worker_overrides'], 'auto_compact_k': 450})['ok']
    monkeypatch.setenv('CCC_WORKER_PROCESS', '1')
    assert server._spawn_auto_compact_k_tokens() == 450000
    before = state.read_bytes()
    assert not server._save_spawn_defaults({'worker_overrides': {'worker_model': 'overwrite'}})['ok']
    assert state.read_bytes() == before


def test_failed_transition_write_preserves_the_entire_previous_state(state, monkeypatch):
    seed(state)
    before = state.read_bytes()
    replace = Path.replace
    def fail(source, target):
        if target == state:
            raise OSError('fixture cannot replace file')
        return replace(source, target)
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        server._save_spawn_defaults({'worker_inherit_session': True})
    assert state.read_bytes() == before
    assert not list(state.parent.glob('*.tmp'))


@pytest.mark.parametrize('needs_backfill', [True, False])
def test_first_linked_load_matches_materialized_migration_only(state, needs_backfill):
    raw = seed(state)
    raw.update(worker_inherit_session=True, worker_overrides=worker(raw))
    # An older/direct writer left stale independent worker fields behind.
    assert raw['engine'] == 'codex'
    assert raw['worker_engine'] == 'claude'
    if needs_backfill:
        del raw['models']['pi']
    state.write_text(json.dumps(raw))
    before = state.read_bytes()
    loaded = server._load_spawn_defaults()
    persisted = read(state)
    assert worker(loaded) == worker(persisted)
    if needs_backfill:
        assert worker(loaded) == dict(worker_engine='codex', worker_model='custom-session-model',
                                     worker_reasoning_effort='high', worker_auto_compact_k=300)
        assert 'pi' in persisted['models']
        assert persisted['worker_overrides'] == raw['worker_overrides']
        assert persisted['worker_fallback'] == raw['worker_fallback']
        assert worker(server._load_spawn_defaults()) == worker(loaded)
    else:
        # Nonmigration reads do not repair arbitrary external/manual edits.
        assert state.read_bytes() == before
        assert worker(loaded) == worker(raw)
