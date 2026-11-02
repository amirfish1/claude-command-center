"""Allowlisted customization review. No migrations, catalog loads or probes.

Only explicit supported fields leave this module. Unknown model identifiers
are redacted; unknown document metadata, paths and credentials never leave it.
Effective values describe saved configuration, not a running launch or quota
selection. Factory models depend on environment/CLI state and are not read.
"""
import hashlib
import json
import re
import time
from .settings_preferences import registry

HIDDEN = '[unregistered value hidden]'
FIELDS = {
    'engine': ('Session engine', 'claude'),
    'reasoning_effort': ('Session effort', ''),
    'auto_compact_k': ('Session compaction (K tokens)', 250),
    'worker_engine': ('Worker engine', ''),
    'worker_model': ('Worker model', ''),
    'worker_reasoning_effort': ('Worker effort', ''),
    'worker_auto_compact_k': ('Worker compaction (K tokens)', 250),
    'worker_inherit_session': ('Workers follow session defaults', False),
    'worker_headroom_dispatch': ('Worker headroom dispatch', False),
}
WORKER_FIELDS = ('worker_engine', 'worker_model', 'worker_reasoning_effort', 'worker_auto_compact_k')
QUEUE_FIELDS = ('engine', 'model', 'effort', 'fallback_to_default_worker', 'headroom_dispatch')


def browser_registry():
    result = {key: {**spec, 'edit': 'appearance'} for key, spec in registry().items() if spec['scope'] == 'browser'}
    for key in ('compact_rows', 'wrap_titles', 'separate_tabs', 'chips_mode', 'coding_density', 'workers_density', 'rows_spacing'):
        result[key]['edit'] = 'list-presentation'
    result['queue_rhs_list']['edit'] = 'queue-view'
    result['voice_mobile_button']['edit'] = 'voice'
    result['sounds_enabled']['edit'] = 'notifications'
    extras = {
        'view_issues': ('ccc-view-gh', 'enum', 'show', ['show', 'hide'], 'list-presentation'),
        'reader_scale': ('ccc-conv-font-scale', 'number', 1, [], 'reader'),
        'workers_dense_legacy': ('ccc-workers-dense', 'boolean', True, [], 'list-presentation'),
        'voice_call_button': ('ccc-voice-call-button', 'boolean', False, [], 'voice'),
    }
    for name in ('enabled', 'tasks', 'digest', 'milestones'):
        extras['notify_' + name] = ('ccc-notify-' + name, 'boolean', True, [], 'notifications')
    for key, (source, kind, default, choices, edit) in extras.items():
        result[key] = dict(scope='browser', source_key=source, type=kind, factory_default=default,
                           choices=choices, edit=edit, portable=False)
    return result


def row(key, label, scope, owner, baseline, stored, effective, *, activity='active', explanation='', known=True, changed=None, edit=None, explicit=True):
    if changed is None and known and effective != HIDDEN:
        changed = effective != baseline
    return dict(key=key, label=label, scope=scope, owner=owner, baseline=baseline,
                baseline_known=known, stored=stored, effective=effective, explicit=explicit,
                stored_changed=(stored != baseline) if known and stored != HIDDEN else None, changed=changed,
                activity=activity, explanation=explanation, edit=edit or {'kind':'policy','context':'defaults'})


def model_ids(core):
    # Read in-memory published records only, never invoke a catalog builder.
    result = {'', 'auto', 'adaptive', 'default'}
    for records in getattr(core, '_ENGINE_CURATED_MODELS', {}).values():
        result.update(r['id'] for r in records if isinstance(r, dict) and isinstance(r.get('id'), str))
    result.update(r['id'] for r in getattr(core, '_CODEX_OPENAI_MODELS', ()) if isinstance(r, dict) and isinstance(r.get('id'), str))
    return result


def safe(value, kind, core, models):
    if kind == 'engine':
        normalized = core._normalize_orchestration_spawn_engine(value) if isinstance(value, str) and value else value
        return normalized if normalized in (*core._ORCHESTRATION_SPAWN_ENGINES, '') else HIDDEN
    if kind == 'model':
        return value if isinstance(value, str) and value in models else HIDDEN
    if kind == 'effort':
        return value if isinstance(value, str) and value in ('', 'low', 'medium', 'high', 'max', 'xhigh', 'minimal', 'none') else HIDDEN
    if kind == 'boolean':
        return value if type(value) is bool else HIDDEN
    return value if type(value) is int and 50 <= value <= 1000 else HIDDEN


def kind(key):
    if key.endswith('engine'): return 'engine'
    if key.endswith('model'): return 'model'
    if key.endswith('effort') or key == 'effort': return 'effort'
    if key.endswith('_k'): return 'integer'
    return 'boolean'


def machine_rows(saved, core, models):
    rows = []
    inherited = saved.get('worker_inherit_session') is True
    for key, (label, baseline) in FIELDS.items():
        if key not in saved: continue
        value = safe(saved[key], kind(key), core, models)
        activity = 'inherited' if inherited and key in WORKER_FIELDS else 'active'
        explanation = ('Materialized worker field read by queue consumers. Linked defaults update on a successful session-default save; this review does not recompute or write it.' if activity == 'inherited' else
                       'Saved CCC defaults; affects future matching launches, not existing sessions or workers.')
        if activity == 'inherited':
            source_key = {'worker_engine':'engine', 'worker_model':'models', 'worker_reasoning_effort':'reasoning_effort', 'worker_auto_compact_k':'auto_compact_k'}[key]
            expected = (saved.get('models') or {}).get(saved.get('engine', 'claude')) if source_key == 'models' and isinstance(saved.get('models', {}), dict) else saved.get(source_key)
            if expected is not None and saved[key] != expected:
                activity = 'unknown'
                explanation += ' The stored worker field differs from the saved session source; consumers still read this materialized value until a successful defaults save.'
        if value == HIDDEN: activity = 'unknown'
        rows.append(row(key, label, 'machine', 'This CCC host', baseline, value, value,
                        activity=activity, explanation=explanation, known=baseline is not None))
    raw_models = saved.get('models')
    if 'models' in saved and not isinstance(raw_models, dict):
        rows.append(row('models', 'Per-engine models', 'machine', 'This CCC host', None, HIDDEN, HIDDEN, activity='unknown', known=False))
    if isinstance(raw_models, dict):
        for engine in core._ORCHESTRATION_SPAWN_ENGINES:
            if engine not in raw_models: continue
            value = safe(raw_models[engine], 'model', core, models)
            selected = engine in (saved.get('engine', 'claude'), saved.get('worker_engine'))
            shadowed = engine != saved.get('engine', 'claude') and engine == saved.get('worker_engine') and bool(saved.get('worker_model'))
            activity = 'shadowed' if shadowed else 'active' if selected else 'unknown'
            rows.append(row('models.' + engine, engine + ' model', 'machine', 'This CCC host', None, value, value,
                            activity='unknown' if value == HIDDEN else activity, known=False,
                            explanation='Baseline depends on engine/environment configuration, which is not inspected. Shared model applies only when that engine is selected and no more specific model wins.'))
    retained = saved.get('worker_overrides')
    if 'worker_overrides' in saved and not isinstance(retained, dict):
        rows.append(row('retained', 'Retained worker overrides unavailable', 'machine', 'This CCC host', None, HIDDEN, HIDDEN, activity='unknown', known=False))
    if isinstance(retained, dict):
        for key in WORKER_FIELDS:
            if key not in retained: continue
            value = safe(retained[key], kind(key), core, models)
            rows.append(row('retained.' + key, 'Retained ' + FIELDS[key][0].lower(), 'machine', 'Independent worker overrides',
                            FIELDS[key][1], value, safe(saved.get(key, FIELDS[key][1]), kind(key), core, models), activity='shadowed', known=FIELDS[key][1] is not None,
                            explanation='Saved for restoration when leaving linked defaults. Does not override the current materialized worker tuple.'))
    for field in ('worker_fallback', 'model_profiles'):
        if field not in saved: continue
        documents = {'worker_fallback': saved[field]} if field == 'worker_fallback' else ({k:v for k,v in saved[field].items() if k in ('fast','standard','deep')} if isinstance(saved[field], dict) else {'model_profiles':None})
        for name, document in documents.items():
            edit = {'kind':'policy','context':name if name in ('fast','standard','deep') else 'worker_fallback'}
            if not isinstance(document, dict) or not isinstance(document.get('models'), list):
                rows.append(row(field + '.' + name, name + ' routing', 'machine', 'This CCC host', [], HIDDEN, HIDDEN, activity='unknown', edit=edit));continue
            enabled = document.get('enabled') is True if field == 'worker_fallback' else True
            if field == 'worker_fallback' and 'enabled' in document:
                value = safe(document['enabled'], 'boolean', core, models)
                rows.append(row('worker_fallback.enabled', 'Worker failure fallback', 'machine', 'This CCC host', False, value, value, edit=edit))
            chain = []
            for entry in document['models']:
                if not isinstance(entry, dict): chain.append(HIDDEN);continue
                chain.append({k:safe(entry.get(k,''), k, core, models) for k in ('engine','model','effort')})
            effective_chain = chain if enabled else []
            fallback = saved.get('worker_fallback', {})
            if field == 'model_profiles' and isinstance(fallback, dict) and fallback.get('enabled') is not True:
                effective_chain = chain[:1]
            rows.append(row(field + '.' + name + '.models', name + ' ordered routes', 'machine', 'This CCC host', [], chain, effective_chain,
                            activity='active' if enabled else 'shadowed', edit=edit,
                            explanation='Managed profiles apply only to managed launches and remain distinct from multi-engine worker fallback. Later managed profile routes are shadowed when the shared fallback switch is Off. Candidate order does not predict runtime quota/failure choices.'))
    return rows


def queue_rows(config, saved, core, models):
    rows = []
    for name, conf in sorted(config.items()):
        if not isinstance(conf, dict): raise ValueError('Invalid queue entry')
        if conf.get('archived'): continue
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', name):
            rows.append(row('queue-unavailable', 'Queue scope unavailable', 'queue', 'Unsupported queue identifier', None, HIDDEN, HIDDEN, activity='unknown', known=False, edit={'kind':'none'}));continue
        engine = conf.get('engine') or saved.get('worker_engine') or ''
        baseline = {
            'engine':saved.get('worker_engine') or None,
            'model':saved.get('worker_model') if engine == saved.get('worker_engine') and saved.get('worker_model') else (saved.get('models') or {}).get(engine) if isinstance(saved.get('models',{}),dict) else None,
            'effort':saved.get('worker_reasoning_effort') or '',
            'fallback_to_default_worker':saved.get('worker_fallback',{}).get('enabled') if isinstance(saved.get('worker_fallback'),dict) else None,
            'headroom_dispatch':saved.get('worker_headroom_dispatch') is True,
        }
        for key in QUEUE_FIELDS:
            if key not in conf: continue
            value = safe(conf[key], 'boolean' if key in ('fallback_to_default_worker','headroom_dispatch') else key, core, models)
            base = safe(baseline[key], 'boolean' if key in ('fallback_to_default_worker','headroom_dispatch') else key, core, models) if baseline[key] is not None else None
            ignored = key in ('engine','model','effort') and not conf[key]
            effective = base if ignored else value
            activity = 'inherited' if ignored else 'active'
            if effective in (None,HIDDEN) or key in ('engine','model','effort'):
                activity = 'unknown' if not ignored else 'inherited'
            rows.append(row(name + '.' + key, key.replace('_',' '), 'queue', name, base, value, effective,
                            activity=activity, known=base not in (None,HIDDEN), edit={'kind':'queue','context':name,'field':key},
                            explanation=('Auto-drain off: applies to future manual queue launches. ' if not conf.get('auto_drain') else 'Applies to future queue launches. ') +
                            ('Blank pin follows worker/shared defaults. ' if ignored else 'Explicit false is Off, not Follow default. ' if value is False else '') +
                            'Configured resolution only; model compatibility, effort guards, headroom and runtime fallback may change launch arguments. Running workers keep their settings.'))
    return rows


def _read(path):
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, {}, 'absent'
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError('Expected an object')
    return raw, value, 'available'


def snapshot(core):
    sources = [('machine', core.SPAWN_DEFAULTS_FILE), ('queue', core._wt_config_path())]
    states = {}; groups = []; models = model_ids(core)
    for scope, path in sources:
        try:
            raw, value, state = _read(path)
            states[scope] = (raw, value, state)
        except (OSError, ValueError, TypeError):
            states[scope] = (None, None, 'unavailable')
    saved = states['machine'][1]
    for scope, path in sources:
        raw, value, state = states[scope]
        try:
            rows = machine_rows(value,core,models) if scope == 'machine' and value is not None else queue_rows(value,saved or {},core,models) if value is not None else []
            if scope == 'queue' and saved is None:
                for item in rows:
                    item.update(baseline=None, baseline_known=False, stored_changed=None, changed=None, activity='unknown')
            if state != 'unavailable' and _read(path)[0] != raw:
                raise RuntimeError('Settings changed while reading; refresh again')
        except OSError as exc:
            raise RuntimeError('Settings changed while reading; refresh again') from exc
        except (ValueError, TypeError, AttributeError):
            rows, state = [], 'unavailable'
        groups.append({'scope':scope,'status':state,'rows':rows})
    for scope, path in sources:
        if states[scope][2] != 'unavailable':
            try:
                if _read(path)[0] != states[scope][0]:
                    raise RuntimeError('Settings changed while reading; refresh again')
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError('Settings changed while reading; refresh again') from exc
    raw_revision = [states[key][0] for key in ('machine','queue')]
    return {'version':1, 'observed_at':time.time(), 'revision':hashlib.sha256(json.dumps(raw_revision).encode()).hexdigest(),
            'groups':groups, 'browser_registry':browser_registry(),
            'coverage':'Covered: registered browser presentation, reader, notification and call-access preferences; saved session/worker defaults, retained worker overrides, fallback and known capability profiles; active and manual queue routing, fallback and headroom pins. Excluded: credentials/accounts, repository branding, paths, network/voice service configuration, budgets, schedules, engine availability, feature gates, archived queues and arbitrary storage. Model values outside published in-memory identifiers are hidden. No runtime/catalog/probe reads; queue resolution is configuration-only.'}
