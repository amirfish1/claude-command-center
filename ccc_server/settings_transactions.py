"""Single-document settings transactions; no process or cross-service rollback.

Cooperating CCC writers serialize using the existing advisory state-directory
lock. Noncooperating writers are checked immediately before replacement, but
cannot be protected against edits in the final check/replace interval.
"""
import copy
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from .settings_preferences import _lock
from .configuration_history import sync_directory


locked = _lock


class Stale(ValueError):
    pass


def snapshot(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def revision(raw):
    return hashlib.sha256(b'missing' if raw is None else b'present:' + raw).hexdigest()


def object_value(raw):
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Configuration is invalid; repair it before saving') from exc
    if not isinstance(data, dict):
        raise ValueError('Configuration must be an object')
    return data


def encoded(value):
    return json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False)


def publish(path, value, before, source_check=None):
    """Fsync unique temp, recheck source, replace once, fsync parent.

    Return durability warnings only after publication. Never report a directory
    fsync failure as if the old configuration were still installed.
    """
    text = encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            if hasattr(os, 'fchmod'):
                os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if source_check:
            source_check()
        if snapshot(path) != before:
            raise Stale('Configuration changed before publication. Reopen and retry.')
        os.replace(temporary, path)
        try:
            sync_directory(path.parent)
        except OSError:
            return ['Configuration saved; directory durability could not be confirmed.']
        return []
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_queue_form(core, payload, normalized, session_link):
    """Prepare the entire native form and session link before one publication.

    Mirror native WT setter semantics without invoking any writing setter. WT's
    pure canonicalizer/validators are used when available, never its runtime
    engine resolver (which can probe installation). Unknown fields survive.
    """
    path = core._wt_config_path()
    with locked(path.parent):
        raw = snapshot(path)
        config = object_value(raw)
        name = normalized['queue']
        matches = [k for k in config if k.strip().upper() == name]
        if len(matches) > 1:
            raise ValueError('Queue name is ambiguous; repair duplicate entries first')
        matched = matches[0] if matches else None
        previous = config.get(matched, {})
        if not isinstance(previous, dict):
            raise ValueError('Queue configuration must be an object')
        before = copy.deepcopy(config)
        conf = copy.deepcopy(normalized['config'])
        fields = {'engine', 'model', 'effort', 'backend', 'repo_path', 'github_repo',
                  'github_assignee', 'auto_drain', 'product_gate', 'desired_workers', 'claim_types'}
        present_only = {'queue_label', 'grace_s', 'fallback_to_default_worker', 'session_id', 'offer_workers'}
        for key, value in previous.items():
            if key not in fields and (key not in present_only or key not in payload):
                conf[key] = copy.deepcopy(value)
        if matched is None:
            conf['auto_drain'] = False
        # WT drain-on semantics: opting in staffs a previously parked queue.
        if conf['auto_drain'] and conf['desired_workers'] < 1:
            conf['desired_workers'] = 1
        if 'session_id' in payload and session_link:
            conf['session_id'] = session_link
        if 'offer_workers' in payload and payload.get('offer_workers'):
            conf['offer_workers'] = True
        for key in present_only:
            if key in payload and (payload[key] is None or key in ('session_id', 'offer_workers') and not payload[key]):
                conf.pop(key, None)
        if conf.get('fallback_to_default_worker') is None:
            conf.pop('fallback_to_default_worker', None)
        wt = core._wt_config if core._WT_CONFIG_AVAILABLE else None
        repo = conf.get('github_repo', '')
        if repo:
            import re
            if repo.lower() in ('owner/repo', 'acme/repo') or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
                raise ValueError('github_repo must be a non-placeholder OWNER/REPO')
        if conf.get('queue_label') == '*':
            for other, entry in config.items():
                if other != matched and isinstance(entry, dict) and entry.get('queue_label') == '*' and str(entry.get('github_repo', '')).lower() == repo.lower():
                    raise ValueError(f'{other} is already the catch-all queue for {repo}; a repo can have only one')
        model = conf.get('model')
        if model and not payload.get('confirm_blocked_model'):
            error = core._model_policy_error(model)
            if error:
                raise ValueError(error)
        defaults_raw = None
        depends_on_defaults = False
        unresolved_model = False
        if model and wt and hasattr(wt, 'canonical_model'):
            engine = conf.get('engine')
            if not engine:
                depends_on_defaults = True
                defaults_raw = snapshot(core.SPAWN_DEFAULTS_FILE)
                engine = object_value(defaults_raw).get('worker_engine')
            if engine:
                conf['model'] = model = wt.canonical_model(engine, model)
            else:
                # No saved engine: WT's installation-dependent runtime fallback
                # resolves this alias at launch. Do not invent a saved engine.
                unresolved_model = True
        if not payload.get('confirm_blocked_model'):
            error = core._model_policy_error(model)
            if error:
                raise ValueError(error)
            if model and wt and hasattr(wt, 'is_blocked_model') and wt.is_blocked_model(model):
                raise ValueError('Model is blocked by worker policy')
        if 'roles' in payload:
            from .queue_roles import prepare_updates
            # A role model without an explicit role/builder engine depends on
            # saved worker defaults. Never call the installation resolver here.
            updates = payload['roles']
            role_inherits_worker = isinstance(updates, dict) and not conf.get('engine') and any(
                isinstance(value, dict) and value.get('model') and not value.get('engine')
                for value in updates.values())
            role_defaults = snapshot(core.SPAWN_DEFAULTS_FILE) if role_inherits_worker else None
            worker_engine = object_value(role_defaults).get('worker_engine') if role_inherits_worker else None
            conf = prepare_updates(core, conf, updates, worker_engine)
            if role_inherits_worker:
                if depends_on_defaults and role_defaults != defaults_raw:
                    raise Stale('Worker defaults changed during role validation')
                defaults_raw = role_defaults
                depends_on_defaults = True
        # Keep ordinary cleared fields absent, as native consumers expect.
        for key in ('claim_types',):
            if not conf.get(key):
                conf.pop(key, None)
        if matched and matched != name:
            del config[matched]
        config[name] = conf
        def check_sources():
            if depends_on_defaults and snapshot(core.SPAWN_DEFAULTS_FILE) != defaults_raw:
                raise Stale('Worker defaults changed before publication. Reopen and retry.')
        warnings = publish(path, config, raw, source_check=check_sources)
        if unresolved_model:
            warnings.append('Configuration saved; with no saved worker engine, native dispatch resolves the model alias at launch.')
        # WT memo/audit are secondary effects, never additional config writes.
        # Older libraries may not expose these helpers.
        if wt:
            try:
                if hasattr(wt, '_LOAD_MEMO'):
                    wt._LOAD_MEMO['sig'] = None
                if hasattr(wt, '_record_audit'):
                    native_path = wt.config_path() if callable(getattr(wt, 'config_path', None)) else getattr(wt, 'CONFIG_FILE', None)
                    if native_path is not None and Path(native_path).resolve() == path.resolve():
                        wt._record_audit(encoded(config), config)
                    else:
                        warnings.append('Configuration saved; worker audit path differs and was not written.')
            except Exception:
                warnings.append('Configuration saved; worker audit metadata unavailable.')
    return {'queue': name, 'config': conf}, before, config, previous, matched, warnings
