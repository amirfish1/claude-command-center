"""Pure routing projections and real consumer contracts with disposable files."""
import copy
import json

import pytest
import server
from ccc_server import launch_preview as preview


def defaults():
    return {'engine': 'claude', 'models': {'claude': 'sonnet-4-6', 'codex': 'gpt-5-codex'},
            'reasoning_effort': 'max', 'worker_engine': 'codex', 'worker_model': 'gpt-5-codex',
            'worker_reasoning_effort': 'high', 'worker_auto_compact_k': 300,
            'worker_fallback': {'enabled': True, 'models': [
                {'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high'},
                {'engine': 'codex', 'model': 'gpt-5-codex', 'effort': 'medium'}]}}


def test_antigravity_preview_distinguishes_session_worker_and_linked_accounts():
    state = defaults()
    state.update(engine='antigravity', worker_engine='antigravity',
                 antigravity_account_id='primary', worker_antigravity_account_id='secondary')
    assert preview.session_plan(state)['fields']['account']['value'] == 'Primary · Google Cloud'
    worker = preview.worker_plan(state)
    assert worker['fields']['account']['value'] == 'Secondary · Google OAuth'
    assert worker['fields']['account']['certainty'] == 'configured'
    state['worker_inherit_session'] = True
    assert preview.worker_plan(state)['fields']['account']['value'] == 'Primary · Google Cloud'
    assert 'account' not in preview.worker_plan(state, {'engine': 'claude'})['fields']


def test_pure_snapshot_never_guesses_runtime_engine_and_never_mutates_inputs():
    state = defaults()
    state['worker_engine'] = ''
    queues = {'PIN': {'engine': 'claude'}, 'MANUAL': {}, 'OLD': {'archived': True}}
    before = copy.deepcopy((state, queues))
    view = preview.snapshot(state, state, queues)
    assert (state, queues) == before
    assert len(view['plans']) == 4
    worker = view['plans'][1]
    assert worker['basis'] == 'configuration_only'
    assert worker['fields']['engine']['value'] is None
    assert worker['fields']['engine']['certainty'] == 'unresolved'
    assert worker['fields']['model']['value'] is None
    pinned = next(plan for plan in view['plans'] if plan['id'] == 'queue:PIN')
    assert pinned['fields']['model']['value'] == 'sonnet-4-6'  # no cross-engine worker model
    assert pinned['fields']['engine']['source'] == 'queue_pin'


def test_pure_queue_sources_and_headroom_are_separate_from_runtime_choice():
    state = defaults()
    conf = {'engine': 'claude', 'model': 'pinned', 'effort': 'low', 'headroom_dispatch': True,
            'fallback_to_default_worker': False}
    plan = preview.worker_plan(state, conf, queue='QUEUE')
    assert {field['source'] for field in plan['fields'].values()} == {'queue_pin'}
    assert not plan['fallback']['enabled']
    assert plan['fallback']['source'] == 'queue_pin'
    assert plan['fallback']['headroom'] is True
    assert any('unresolved' in note for note in plan['notes'])
    assert [row['engine'] for row in plan['fallback']['routes']] == ['claude', 'codex']
    assert plan['fallback']['verified'] is False


def test_unknown_ambient_values_are_not_fabricated_and_session_ignores_worker_fallback():
    state = defaults()
    state['models']['claude'] = 'auto'
    plan = preview.session_plan(state, {'verified': True, 'engine': 'claude', 'model': 'auto', 'effort': ''})
    assert plan['fields']['model']['certainty'] == 'ambient'
    assert plan['fields']['effort']['value'] is None
    assert not plan['fallback']['enabled']
    assert not plan['fallback']['routes']


@pytest.fixture
def consumer(monkeypatch, tmp_path):
    wt = pytest.importorskip('watchtower.config')
    from watchtower import workers, models
    state = tmp_path / 'spawn-defaults.json'
    queue_file = tmp_path / 'queues.json'
    policy_file = tmp_path / 'policy.json'
    state.write_text(json.dumps(defaults()))
    queue_file.write_text(json.dumps({'PIN': {'auto_drain': True, 'engine': 'claude', 'model': 'sonnet-4-6', 'effort': 'high'},
                                    'DEFAULT': {}, 'MANUAL': {'engine': 'claude'}}))
    policy_file.write_text(json.dumps({'blocked_models': []}))
    for obj, key, value in [(server, 'COMMAND_CENTER_STATE_DIR', tmp_path), (server, 'SPAWN_DEFAULTS_FILE', state),
                            (server, 'MODEL_POLICY_FILE', policy_file), (server, '_WT_CONFIG_AVAILABLE', True),
                            (server, '_wt_config', wt), (wt, 'CONFIG_FILE', queue_file),
                            (wt, 'CCC_SPAWN_DEFAULTS_FILE', state), (wt, 'CCC_MODEL_POLICY_FILE', policy_file)]:
        monkeypatch.setattr(obj, key, value)
    monkeypatch.setattr(server, '_wt_config_path', lambda: queue_file)
    monkeypatch.setattr(server, '_spawn_fallback_model_for_engine', lambda engine: 'gpt-5-codex' if engine == 'codex' else '')
    monkeypatch.setattr(models, 'entries', lambda engine: [
        {'id': name, 'label': name, 'efforts': levels} for name, levels in {
            'codex': [('gpt-5-codex', ('low', 'medium', 'high'))],
            'claude': [('claude-sonnet-4-6', ('low', 'high')), ('claude-fable-5', ())],
        }.get(engine, [])])
    monkeypatch.setattr(workers, 'engine_available', lambda engine: engine == 'codex')
    monkeypatch.delenv('WATCHTOWER_BLOCKED_MODELS', raising=False)
    monkeypatch.delenv('CCC_BLOCKED_MODELS', raising=False)
    return wt, workers, state, queue_file, policy_file


def plans():
    return {row['id']: row for row in server._settings_launch_overview()['launch_preview']['plans']}


def test_session_and_worker_fields_match_native_consumers(consumer):
    wt, _, state, queue_file, _ = consumer
    before = {p: p.read_bytes() for p in (state, queue_file)}
    view = plans()
    session = view['session']['fields']
    engine, model = server._spawn_request_engine_and_model({})
    assert session['engine']['value'] == engine
    assert session['model']['value'] == model
    assert session['effort']['value'] == server._spawn_request_reasoning_effort({}, engine)
    for name in (None, 'PIN', 'DEFAULT', 'MANUAL'):
        fields = view['worker' if name is None else 'queue:' + name]['fields']
        assert fields['engine']['value'] == wt.engine(name)
        assert fields['model']['value'] == wt.model(name)
        assert fields['effort']['value'] == wt.effort(name)
    # The comparison's legacy loads may backfill models; use a complete file
    # below to assert preview reads themselves never seed or migrate.
    assert queue_file.read_bytes() == before[queue_file]


def test_preview_never_seeds_or_migrates_and_uses_stored_worker_tuple(consumer):
    _, _, state, queue_file, _ = consumer
    raw = defaults()
    raw.update(worker_inherit_session=True, worker_engine='claude', worker_model='sonnet-4-6')
    state.write_text(json.dumps(raw))
    before = state.read_bytes(), queue_file.read_bytes()
    view = plans()
    assert view['worker']['fields']['engine']['value'] == 'claude'
    assert (state.read_bytes(), queue_file.read_bytes()) == before
    state.unlink()
    assert server._settings_launch_overview()['ok']
    assert not state.exists()


def test_ignored_pin_policy_substitution_and_effort_launch_safeguard(consumer):
    wt, _, state, queue_file, policy_file = consumer
    cfg = json.loads(queue_file.read_text())
    cfg['PIN'].update(model='not-approved', effort='medium')
    queue_file.write_text(json.dumps(cfg))
    plan = plans()['queue:PIN']
    assert plan['fields']['model']['value'] == wt.model('PIN') == 'claude-sonnet-4-6'
    assert plan['fields']['model']['source'] == 'session_model_default'
    assert plan['fields']['effort']['value'] is None
    assert plan['fields']['effort']['source'] == 'consumer_safeguard'
    assert any('not approved' in warning for warning in plan['warnings'])
    cfg['PIN']['model'] = 'gpt-5-codex'  # approved for another engine; native consumer accepts it
    queue_file.write_text(json.dumps(cfg))
    policy_file.write_text(json.dumps({'blocked_models': ['gpt-5-codex']}))
    plan = plans()['queue:PIN']
    assert plan['fields']['model']['value'] == wt.model('PIN') == 'claude-sonnet-4-6'
    assert plan['fields']['model']['source'] == 'model_policy'


def test_fable_guard_and_fallback_effort_follow_real_launch_predicates(consumer):
    wt, workers, _, queue_file, _ = consumer
    cfg = json.loads(queue_file.read_text())
    cfg['PIN'].update(model='claude-fable-5', effort='medium', fallback_to_default_worker=True, headroom_dispatch=True)
    queue_file.write_text(json.dumps(cfg))
    assert workers._is_fable_model(wt.model('PIN'))
    plan = plans()['queue:PIN']
    assert plan['fields']['model']['value'] is None
    assert plan['fields']['model']['source'] == 'consumer_safeguard'
    fallback = plan['fallback']
    assert fallback['enabled'] and fallback['verified'] and fallback['headroom']
    for route in fallback['routes']:
        expected = wt.fallback_effort('PIN', route['engine'])
        if wt._effort_rejected(route['engine'], route['model'] or wt.model('PIN'), expected):
            expected = ''
        assert route['effort'] == expected
        assert route['model'] == wt.fallback_model(route['engine'])


def test_queue_fallback_off_and_legacy_on_override_native_policy(consumer):
    wt, _, state, queue_file, _ = consumer
    cfg = json.loads(queue_file.read_text())
    cfg['PIN']['fallback_to_default_worker'] = False
    queue_file.write_text(json.dumps(cfg))
    assert plans()['queue:PIN']['fallback']['enabled'] == wt.fallback_to_default_worker('PIN') is False
    raw = json.loads(state.read_text())
    raw.pop('worker_fallback')
    state.write_text(json.dumps(raw))
    cfg['PIN']['fallback_to_default_worker'] = True
    queue_file.write_text(json.dumps(cfg))
    fallback = plans()['queue:PIN']['fallback']
    assert fallback['enabled'] == wt.fallback_to_default_worker('PIN') is True
    assert fallback['legacy']
    assert [r['engine'] for r in fallback['routes']] == list(dict.fromkeys(e for e in wt.fallback_chain() if e))


def test_unavailable_native_consumer_does_not_label_a_guess_actual(consumer, monkeypatch):
    monkeypatch.setattr(server, '_WT_CONFIG_AVAILABLE', False)
    view = plans()
    assert view['worker']['basis'] == 'configuration_only'
    assert view['queue:PIN']['basis'] == 'configuration_only'
    assert view['worker']['warnings']


def test_changed_files_reject_mixed_consumer_snapshot(consumer, monkeypatch):
    wt, _, state, _, _ = consumer
    engine = wt.engine
    def changed(name):
        raw = json.loads(state.read_text()); raw['worker_auto_compact_k'] += 1
        state.write_text(json.dumps(raw))
        return engine(name)
    monkeypatch.setattr(wt, 'engine', changed)
    with pytest.raises(ValueError, match='changed while reading'):
        server._settings_launch_overview()


def test_mismatched_native_queue_file_is_configuration_only(consumer, monkeypatch, tmp_path):
    wt, _, _, _, _ = consumer
    monkeypatch.setattr(wt, 'CONFIG_FILE', tmp_path / 'other-queues.json')
    view = plans()
    assert view['worker']['basis'] == 'configuration_only'
    assert view['queue:PIN']['basis'] == 'configuration_only'


def test_session_model_validation_is_reported_without_availability_probes(consumer, monkeypatch):
    _, _, state, _, _ = consumer
    raw = json.loads(state.read_text())
    raw['engine'] = 'codex'
    raw['models']['codex'] = 'unapproved-codex-model'
    state.write_text(json.dumps(raw))
    monkeypatch.setattr(server, '_codex_model_availability_error', lambda model: pytest.fail('No runtime availability probe'))
    session = plans()['session']
    assert session['fields']['model']['value'] == 'unapproved-codex-model'
    assert any('launch would be rejected' in warning for warning in session['warnings'])
    assert state.read_text() == json.dumps(raw)


def test_session_alias_matches_launch_validator(consumer):
    _, _, state, _, _ = consumer
    raw = json.loads(state.read_text())
    alias, canonical = next(iter(server._CODEX_MODEL_ALIASES.items()))
    raw['engine'] = 'codex'
    raw['models']['codex'] = alias
    state.write_text(json.dumps(raw))
    session = plans()['session']
    assert session['fields']['model']['value'] == server._validate_codex_model(alias)[0] == canonical
    assert session['fields']['model']['source'] == 'consumer_validation'
