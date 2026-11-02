"""Health evidence without refreshes, secret access, or configuration writes.

Cached observations expire; an absent observation is never a passing check.
The response deliberately excludes native errors, paths and account metadata.
"""
import json
import time


def _age(timestamp, now):
    return now - timestamp if type(timestamp) in (int, float) and 0 < timestamp <= now else None


def _row(key, label, state, message, repair, age=None):
    return dict(id=key, label=label, state=state, message=message, repair=repair,
                age_seconds=None if age is None else round(age))


def project(engine_cache, service_cache, *, now, active_engines=None):
    """Project only native cache contracts, never arbitrary error messages."""
    rows = []
    age = _age(engine_cache.get('ts'), now)
    data = engine_cache.get('data')
    engines = data.get('engines') if isinstance(data, dict) else None
    if not isinstance(engines, list) or age is None:
        rows.append(_row('engines', 'Engine installation', 'unchecked',
                         'No installation snapshot. Open engine setup to check local installation and sign-in.', 'engines'))
    else:
        for item in engines:
            if not isinstance(item, dict) or not isinstance(item.get('engine'), str) or item.get('engine') not in ENGINE_IDS:
                continue
            name = item['engine']
            present = item.get('installed')
            state = 'stale' if age >= 60 else 'issue' if present is False else 'observed' if present is True else 'unavailable'
            message = ('Installation snapshot expired; recheck in engine setup.' if state == 'stale' else
                       'CLI not detected. Install or choose a different launch route.' if state == 'issue' else
                       'CLI detected; provider access has not been tested.' if state == 'observed' else 'Installation evidence unavailable.')
            if state == 'issue' and active_engines is not None and name not in active_engines:
                state, message = 'observed', 'CLI not detected. Not selected by saved CCC routes; other launch contexts may still require it.'
            rows.append(_row('engine-' + name, name, state, message, 'engine:' + name, age))
    if not rows:
        rows.append(_row('engines', 'Engine installation', 'unavailable', 'Installation snapshot contains no supported engine observations. Recheck in engine setup.', 'engines'))
    age = _age(service_cache.get('ts'), now)
    payload = service_cache.get('payload')
    services = payload.get('services') if isinstance(payload, dict) and payload.get('ok') is True else None
    native = {r.get('id'): r for r in services if isinstance(r, dict)} if isinstance(services, list) else {}
    for name, label in (('worker', 'Execution worker'), ('watchtower', 'Queue service')):
        item = native.get(name, {})
        state = 'unchecked'
        message = 'No service observation. Open service diagnostics for an explicit status check.'
        if item and age is not None:
            if age >= 30:
                state, message = 'stale', 'Service snapshot expired. Open service diagnostics to refresh.'
            elif name == 'worker' and item.get('state') in ('offline', 'degraded'):
                state, message = 'issue', 'Last native worker health request failed. Review service diagnostics before repairing.'
            elif name == 'watchtower' and item.get('api_ok') is False and type(item.get('api_probe_age_s')) in (int, float) and 0 <= item['api_probe_age_s'] + age < 30:
                state, message = 'issue', 'Last recorded queue API probe failed. Review service diagnostics before repairing.'
            elif item.get('state') == 'online':
                state, message = 'observed', 'Last service snapshot reported online; this is not a new reachability test.'
            else:
                state, message = 'unavailable', 'Native snapshot has no current reachability result.'
        rows.append(_row(name, label, state, message, 'services', age))
    rows.append(_row('connections', 'Provider connections', 'unchecked',
                     'Credential presence does not establish validity or expiry. No verified account-expiry observation is available here.', 'connections'))
    return rows


ENGINE_IDS = frozenset(('claude', 'codex', 'gemini', 'cursor', 'antigravity', 'kilo', 'opencode', 'kimi', 'hermes', 'devin', 'grok', 'aider', 'droid', 'pi'))


def policy_rows(core, active_engines=None):
    """Inspect saved fields with native validators; never normalize or seed."""
    active_engines = active_engines if active_engines is not None else set()
    try:
        saved = json.loads(core.SPAWN_DEFAULTS_FILE.read_text())
    except FileNotFoundError:
        active_engines.add(core._factory_spawn_defaults()['engine'])
        return [_row('policy', 'Launch policy', 'unchecked', 'No saved policy. Factory defaults apply; review them in Launch policy.', 'policy')]
    except (OSError, ValueError):
        return [_row('policy', 'Launch policy', 'unavailable', 'Saved policy could not be read. Review configuration access; no changes were made.', 'policy')]
    if not isinstance(saved, dict):
        return [_row('policy', 'Launch policy', 'issue', 'Saved policy must be an object. Review Launch policy; no changes were made.', 'policy')]
    for key in ('engine', 'worker_engine'):
        if isinstance(saved.get(key), str) and saved[key]:
            normalized = core._normalize_orchestration_spawn_engine(saved[key])
            if normalized:
                active_engines.add(normalized)
    documents = []
    if isinstance(saved.get('worker_fallback'), dict) and saved['worker_fallback'].get('enabled') is True:
        documents.append(saved['worker_fallback'])
    if isinstance(saved.get('model_profiles'), dict):
        documents.extend(value for key, value in saved['model_profiles'].items() if key in ('fast', 'standard', 'deep') and isinstance(value, dict))
    for document in documents:
        if isinstance(document.get('models'), list):
            active_engines.update(row['engine'] for row in document['models'] if isinstance(row, dict) and isinstance(row.get('engine'), str))
    failures = []
    def fail(key, message):
        failures.append(_row('policy-' + key, 'Launch policy · ' + {'engine':'Session engine','worker_engine':'Worker engine','reasoning_effort':'Session effort','worker_reasoning_effort':'Worker effort','models':'Models','worker_model':'Worker model','worker_fallback':'Worker fallback','model_profiles':'Capability profiles'}.get(key, key), 'issue', message, 'policy'))
    for key, effort in (('engine', 'reasoning_effort'), ('worker_engine', 'worker_reasoning_effort')):
        raw_engine = saved.get(key)
        if raw_engine is None or raw_engine == '' and key == 'worker_engine':
            continue
        engine = core._normalize_orchestration_spawn_engine(raw_engine)
        if engine not in core._ORCHESTRATION_SPAWN_ENGINES:
            fail(key, 'Saved engine is unsupported. Choose a supported route and preview before applying.')
        elif core._validate_reasoning_effort(saved.get(effort), engine, strict=True) is None:
            fail(effort, 'Saved effort is incompatible with its engine. Review and preview the route.')
    models = saved.get('models', {})
    if not isinstance(models, dict):
        fail('models', 'Saved model routes must be an object. Review Launch policy.')
    else:
        for raw_engine, model in models.items():
            engine = core._normalize_orchestration_spawn_engine(raw_engine)
            if engine not in core._ORCHESTRATION_SPAWN_ENGINES:
                continue  # Future engine metadata is not silently invalidated.
            if not isinstance(model, str) or model and (not core._model_catalog_allows_model(engine, model) or core._model_policy_blocks(model)):
                fail('models', 'A saved model is incompatible with its engine or blocked by model policy. Review the route.')
                break
    worker_engine = core._normalize_orchestration_spawn_engine(saved['worker_engine']) if saved.get('worker_engine') else None
    worker_model = saved.get('worker_model', '')
    if not isinstance(worker_model, str) or worker_model and worker_engine in core._ORCHESTRATION_SPAWN_ENGINES and (not core._model_catalog_allows_model(worker_engine, worker_model) or core._model_policy_blocks(worker_model)):
        fail('worker_model', 'Saved worker model is invalid for its route. Review Launch policy.')
    # Validate each known profile independently; preserve future profile metadata.
    for key in ('worker_fallback', 'model_profiles'):
        if key not in saved:
            continue
        try:
            if key == 'worker_fallback':
                core._validate_worker_fallback(saved[key], strict=True)
            else:
                profiles = saved[key]
                if not isinstance(profiles, dict):
                    raise ValueError()
                core._validate_model_profiles({k: v for k, v in profiles.items() if k in ('fast', 'standard', 'deep')}, strict=True)
        except (ValueError, TypeError):
            fail(key, 'Saved routes fail the native consumer validation. Review supported engines, models and effort in Launch policy.')
        except OSError:
            failures.append(_row('policy-' + key, 'Launch policy · ' + {'engine':'Session engine','worker_engine':'Worker engine','reasoning_effort':'Session effort','worker_reasoning_effort':'Worker effort','models':'Models','worker_model':'Worker model','worker_fallback':'Worker fallback','model_profiles':'Capability profiles'}.get(key, key), 'unavailable', 'Local route catalog could not be read. Retry or review Launch policy.', 'policy'))
    return failures or [_row('policy', 'Launch policy', 'observed', 'Saved engine, effort and fallback/profile validation passed. Runtime quota, queue pins and provider access are separate.', 'policy')]


def snapshot(core):
    from ccc_server.engines import _ENGINES_INSTALLED_CACHE
    now = time.time()
    # Installation TTL uses monotonic time; services use Unix wall time.
    engine_cache = dict(_ENGINES_INSTALLED_CACHE)
    age = _age(engine_cache.get('ts'), time.monotonic())
    engine_cache['ts'] = now - age if age is not None else 0
    active_engines = set()
    policy = policy_rows(core, active_engines)
    rows = project(engine_cache, dict(core._system_services_cache), now=now, active_engines=active_engines)
    rows += policy
    return {'generated_at': now, 'checks': rows}
