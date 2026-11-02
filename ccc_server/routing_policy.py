"""Pure helpers for staged edits of existing, distinct launch policies."""
import copy
import hashlib
import json

EDITABLE = frozenset(('engine', 'models', 'reasoning_effort', 'auto_compact_k',
                     'worker_engine', 'worker_model', 'worker_reasoning_effort',
                     'worker_auto_compact_k', 'worker_inherit_session',
                     'worker_fallback', 'model_profiles', 'disabled_engines'))


def revision(raw):
    return hashlib.sha256(b'absent' if raw is None else raw.encode()).hexdigest()


def token(rev, prepared):
    return hashlib.sha256(json.dumps([rev, prepared], sort_keys=True).encode()).hexdigest()


def validate_changes(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - EDITABLE:
        raise ValueError('Choose at least one supported launch-policy change')
    for key, value in changes.items():
        if key == 'disabled_engines':
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError('disabled_engines must be a list of engine identifiers')
        elif key.endswith('auto_compact_k'):
            if type(value) is not int or not 50 <= value <= 1000:
                raise ValueError(key + ' must be an integer from 50 to 1000')
        elif key in ('engine', 'worker_engine', 'worker_model', 'reasoning_effort', 'worker_reasoning_effort'):
            if not isinstance(value, str):
                raise ValueError(key + ' must be text')
        elif key == 'models':
            if not isinstance(value, dict) or not value or any(not isinstance(v, str) for v in value.values()):
                raise ValueError('models must map engines to model identifiers')


def preserve_route(original, validated, origins):
    """Keep unknown document/row metadata using explicit original-row indexes.

    Indexes travel only in the request envelope, never in the stored schema.
    Null denotes a new row. Reorders and tuple edits retain row ownership.
    """
    original = original if isinstance(original, dict) else {}
    old_rows = original.get('models') if isinstance(original.get('models'), list) else []
    rows = validated['models']
    if not isinstance(origins, list) or len(origins) != len(rows):
        raise ValueError('Every edited route needs matching source-row indexes')
    seen, preserved = set(), []
    for row, origin in zip(rows, origins):
        if origin is None:
            base = {}
        elif type(origin) is int and 0 <= origin < len(old_rows) and origin not in seen:
            seen.add(origin)
            base = old_rows[origin]
            if not isinstance(base, dict):
                raise ValueError('An invalid stored route cannot be edited implicitly')
        else:
            raise ValueError('Source-row indexes are stale, duplicated or invalid')
        preserved.append({**copy.deepcopy(base), **row})
    return {**copy.deepcopy(original), **validated, 'models': preserved}


def preserve_documents(stored, changes, prepared, origins):
    origins = origins if isinstance(origins, dict) else {}
    if 'worker_fallback' in changes:
        prepared['worker_fallback'] = preserve_route(stored.get('worker_fallback'), prepared['worker_fallback'], origins.get('worker_fallback'))
    if 'model_profiles' in changes:
        profiles = copy.deepcopy(stored.get('model_profiles')) if isinstance(stored.get('model_profiles'), dict) else {}
        profile_origins = origins.get('model_profiles') or {}
        if not isinstance(profile_origins, dict):
            raise ValueError('Profile source indexes must be an object')
        for name in changes['model_profiles']:
            profiles[name] = preserve_route(profiles.get(name), prepared['model_profiles'][name], profile_origins.get(name))
        prepared['model_profiles'] = profiles
    return prepared


def differences(before, after):
    """All effective top-level changes, including materialized inheritance."""
    missing = {'absent': True}
    return [{'key': key, 'before': before.get(key, missing), 'after': after.get(key, missing)}
            for key in sorted(before.keys() | after.keys()) if before.get(key, missing) != after.get(key, missing)]


def staged_candidate(stored, changes, prepared):
    """An edit does not normalize unrelated legacy or future policy fields."""
    candidate = copy.deepcopy(stored)
    for key in changes:
        if key == 'disabled_engines':
            candidate[key] = copy.deepcopy(stored.get(key)) if isinstance(stored.get(key), list) else []
            candidate[key].extend(engine for engine in prepared[key] if engine not in candidate[key])
        elif key == 'models':
            models = copy.deepcopy(stored.get('models')) if isinstance(stored.get('models'), dict) else {}
            models.update({engine: prepared['models'][engine] for engine in changes['models']})
            candidate['models'] = models
        else:
            candidate[key] = copy.deepcopy(prepared[key])
    if prepared.get('worker_inherit_session') is True or 'worker_inherit_session' in changes:
        for key in ('worker_engine', 'worker_model', 'worker_reasoning_effort', 'worker_auto_compact_k'):
            candidate[key] = prepared[key]
        if 'worker_overrides' in prepared:
            candidate['worker_overrides'] = copy.deepcopy(prepared['worker_overrides'])
        if prepared.get('worker_inherit_session') is True:
            candidate['engine'] = prepared['engine']
            candidate['reasoning_effort'] = prepared['reasoning_effort']
    if 'engine' in changes or 'worker_engine' in changes or 'worker_inherit_session' in changes:
        # Re-enable an explicitly chosen default without deleting future IDs.
        if isinstance(candidate.get('disabled_engines'), list):
            active = (candidate.get('engine'), candidate.get('worker_engine'))
            candidate['disabled_engines'] = [engine for engine in candidate['disabled_engines'] if engine not in active]
    return candidate
