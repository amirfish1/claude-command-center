"""Pure projections of saved launch policy and separately supplied consumer facts.

No filesystem, PATH, quota or service probes live here. Missing consumer facts
are explicitly configuration-only; they never become a guessed launch result.
"""


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _field(value, source, explanation, verified):
    value = _text(value)
    return {'value': value or None, 'source': source, 'explanation': explanation,
            'certainty': ('ambient' if not value or value in ('auto', 'adaptive') else 'resolved') if verified else ('configured' if value else 'unresolved')}


def _fallback(defaults, conf, facts):
    raw = defaults.get('worker_fallback')
    policy = facts.get('policy', raw)
    legacy = policy is None
    valid = isinstance(policy, dict) and type(policy.get('enabled')) is bool and isinstance(policy.get('models'), list)
    enabled = bool(conf['fallback_to_default_worker']) if 'fallback_to_default_worker' in conf else bool(policy.get('enabled')) if valid else False
    source = 'queue_pin' if 'fallback_to_default_worker' in conf else 'legacy_off' if legacy else 'worker_policy'
    routes = facts.get('fallback_routes')
    if routes is None:
        # Configuration only: do not invent legacy engine order or models.
        routes = [{key: _text(row.get(key)) for key in ('engine', 'model', 'effort')}
                  for row in policy.get('models', []) if isinstance(row, dict)] if valid else []
    headroom = bool(conf['headroom_dispatch']) if 'headroom_dispatch' in conf else defaults.get('worker_headroom_dispatch') is True
    return {'enabled': enabled, 'source': source, 'legacy': legacy,
            'routes': routes, 'verified': bool(facts.get('fallback_verified')),
            'headroom': headroom, 'headroom_source': 'queue_pin' if 'headroom_dispatch' in conf else 'worker_policy',
            'explanation': ('Queue fallback On/Off overrides the worker fallback policy. '
                            'Order is configured, not a prediction: dispatch skips failed, unavailable, '
                            'cooling-down or already-attempted engines. '
                            'Queue effort pins also override fallback-route effort.')}


def session_plan(defaults, facts=None):
    facts = facts or {}
    verified = facts.get('verified') is True
    engine = facts.get('engine', defaults.get('engine'))
    models = defaults.get('models') if isinstance(defaults.get('models'), dict) else {}
    configured_model = models.get(engine, '')
    model = facts.get('model', configured_model)
    effort = facts.get('effort', defaults.get('reasoning_effort', ''))
    return {'id': 'session', 'label': 'New session · saved defaults',
            'basis': 'consumer' if verified else 'configuration_only',
            'fields': {
                'engine': _field(engine, 'session_default', 'Saved new-session engine.', verified),
                'model': _field(model, facts.get('model_source') or ('model_policy' if verified and model != configured_model else 'session_default'),
                                facts.get('model_explanation') or ('Model-policy substitution used by the session consumer.' if verified and model != configured_model else 'Saved model for this engine; blank lets the engine decide.'), verified),
                'effort': _field(effort, 'session_default' if effort else 'engine_default',
                                 'Saved session effort, validated against this engine. Blank passes no effort override.', verified)},
            'fallback': {'enabled': False, 'routes': [], 'explanation': 'Worker fallback and managed model profiles do not route ordinary new sessions.'},
            'notes': ['Standard local session with no one-off engine/model/effort overrides. Free routing, remote placement and managed profiles are different launch contexts.',
                      'This resolves saved arguments, not CLI readiness, launch approval or a running session. Blank values are decided by the engine at launch.'],
            'warnings': facts.get('warnings', [])}


def worker_plan(defaults, conf=None, facts=None, queue=None):
    conf, facts = conf or {}, facts or {}
    verified = facts.get('verified') is True
    models = defaults.get('models') if isinstance(defaults.get('models'), dict) else {}
    engine = _text(facts.get('engine')) if verified else _text(conf.get('engine')) or _text(defaults.get('worker_engine'))
    engine_source = 'queue_pin' if conf.get('engine') else 'worker_default' if defaults.get('worker_engine') else 'watchtower_default' if verified else 'unresolved'
    model_source = 'queue_pin' if conf.get('model') and not facts.get('model_pin_warning') else 'worker_default' if engine and engine == defaults.get('worker_engine') and defaults.get('worker_model') else 'session_model_default' if models.get(engine) else 'engine_default'
    configured_model = conf.get('model') if model_source == 'queue_pin' else defaults.get('worker_model') if model_source == 'worker_default' else models.get(engine, '')
    model = facts.get('model') if verified else configured_model
    effort = facts.get('effort') if verified else conf.get('effort') or defaults.get('worker_reasoning_effort')
    effort_source = 'queue_pin' if conf.get('effort') else 'worker_default' if defaults.get('worker_reasoning_effort') else 'engine_default'
    warnings = list(facts.get('warnings', []))
    if not verified and not warnings:
        warnings.append('WatchTower consumer not verified; these are configured values, not an actual launch result.')
    if facts.get('model_guard'):
        warnings.append('WatchTower refuses this Fable worker model and passes no explicit model to the engine.')
    if facts.get('model_pin_warning'):
        warnings.append(facts['model_pin_warning'])
    if verified and 'raw_model' in facts and facts['raw_model'] != facts.get('model'):
        model_source = 'consumer_safeguard' if facts.get('model_guard') else 'model_policy'
    if facts.get('effort_dropped'):
        effort_source = 'consumer_safeguard'
        warnings.append('WatchTower drops the configured effort for this model at launch; the stored setting is unchanged.')
    fallback = _fallback(defaults, conf, facts)
    notes = ['Uses saved worker defaults. Linked session changes take effect after a successful defaults save.',
             'This is resolved routing before dispatch, not a promise that a worker starts. Availability, cooldown and scheduling can defer launch.']
    if fallback['headroom']:
        notes.append('Headroom dispatch is on: current quota/headroom can substitute a different engine, model and effort at dispatch. That choice is unresolved here.')
    if fallback['enabled']:
        notes.append('Failure fallback is on: a runtime failure can select a replacement from the configured order. No replacement is selected by this preview.')
    if queue is not None:
        notes.append('Auto-drain is ' + ('on' if conf.get('auto_drain') else 'off (manual launch only)') + '; running workers keep their current settings.')
    return {'id': 'worker' if queue is None else 'queue:' + queue,
            'label': 'Workers · no queue pins' if queue is None else 'Queue · ' + queue,
            'basis': 'consumer' if verified else 'configuration_only',
            'fields': {
                'engine': _field(engine, engine_source, 'Queue engine pin wins, then saved worker engine, then WatchTower availability fallback.', verified),
                'model': _field(model, model_source, 'Usable queue model pin wins, then a worker model paired with this engine, then its shared model. Consumer policy and launch safeguards can substitute or omit it.', verified),
                'effort': _field(effort, effort_source, 'Queue effort pin wins, then worker effort; the launch consumer omits catalog-incompatible effort.', verified)},
            'pins': {key: _text(conf.get(key)) for key in ('engine', 'model', 'effort') if conf.get(key)},
            'fallback': fallback, 'notes': notes, 'warnings': warnings}


def snapshot(session_defaults, worker_defaults, queues, session_facts=None, worker_facts=None):
    worker_facts = worker_facts or {}
    plans = [session_plan(session_defaults, session_facts),
             worker_plan(worker_defaults, facts=worker_facts.get(None))]
    plans.extend(worker_plan(worker_defaults, conf, worker_facts.get(name), name)
                 for name, conf in sorted(queues.items()) if isinstance(conf, dict) and not conf.get('archived'))
    return {'version': 1, 'plans': plans}
