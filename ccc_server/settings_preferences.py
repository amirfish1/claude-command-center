"""Portable, nonsecret preferences; no imports from or writes to live config.

Integration contract:
    defaults = factory_defaults()  # replace with current *effective* defaults
    store = PreferenceStore(caller_owned_state_dir, defaults)
    preview = store.preview_import(profile)  # or preview_change/reset/undo
    result = store.apply(preview)

All methods return JSON-compatible detached data. ``apply`` persists only this
module's preference ledger, NOT browser storage or server configuration. The
caller must build adapters for those surfaces, enforce HTTP origin/auth checks,
and coordinate any live-config transaction separately. Browser booleans here
are typed booleans; adapters translate the existing '1'/'0' and 'on' encodings.
Use a trusted, private state directory, shared by every writer of this ledger.
Effective defaults must include exactly the registry keys (not an entire server
configuration); create a new store when they change. Revisions cover defaults
as well as persisted state. Profiles merge supplied keys; reset removes selected
overrides; undo restores the last retained transaction's overrides, so inherited
defaults remain inherited. Undoing an undo acts as redo. No-op applies add no
history. Explicit overrides equal to defaults are retained and can be reset.

Only the allowlist below is supported. Free-form strings, credentials, model
maps, repository/session state, URLs, paths and machine identifiers are excluded.
Profile format: {"format": "ccc-preferences", "version": 1, "values": {...}}.
No metadata, actor labels, or profile names are accepted or recorded.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType


class ValidationError(ValueError):
    """Invalid input or corrupt ledger; no preference write was performed."""


class StaleRevisionError(ValueError):
    """State/defaults changed after preview; obtain another preview."""


@dataclass(frozen=True)
class Setting:
    scope: str
    source_key: str
    kind: str
    factory_default: object
    choices: tuple = ()
    minimum: int = 0
    maximum: int = 0

    def validate(self, value):
        if self.kind == 'boolean':
            valid = type(value) is bool
        elif self.kind == 'integer':
            valid = type(value) is int and self.minimum <= value <= self.maximum
        else:
            valid = type(value) is str and value in self.choices
        if not valid:
            # Never echo untrusted values (which might contain credentials).
            raise ValidationError('Invalid preference value')
        return value


# Sources: static/index.html prepaint; static/app.js appearance, sessionDensity,
# rowsSpacing and sound helpers; server.py _factory_spawn_defaults and
# ccc_server/textutil.py _validate_auto_compact_k. Dynamic model/engine-dependent
# settings require their own contextual validators and are intentionally absent.
REGISTRY = MappingProxyType({
    'theme': Setting('browser', 'ccc-theme', 'enum', 'dark', ('dark', 'light', 'system')),
    'font': Setting('browser', 'ccc-font', 'enum', 'system', ('system', 'mono')),
    'sounds_enabled': Setting('browser', 'ccc-sounds-enabled', 'boolean', True),
    'compact_rows': Setting('browser', 'ccc-compact-rows', 'boolean', True),
    'wrap_titles': Setting('browser', 'ccc-wrap-titles', 'boolean', False),
    'separate_tabs': Setting('browser', 'ccc-separate-tabs', 'boolean', False),
    'queue_rhs_list': Setting('browser', 'ccc-queue-rhs-list', 'boolean', False),
    'voice_mobile_button': Setting('browser', 'ccc-voice-mobile-btn', 'boolean', True),
    'chips_mode': Setting('browser', 'ccc-chips-mode', 'enum', 'color', ('color', 'level', 'muted')),
    'coding_density': Setting('browser', 'ccc-coding-density', 'enum', 'compact', ('compact', 'cozy', 'detailed')),
    'workers_density': Setting('browser', 'ccc-workers-density', 'enum', 'compact', ('compact', 'cozy', 'detailed')),
    'rows_spacing': Setting('browser', 'ccc-rows-spacing', 'enum', 'roomy', ('cozy', 'roomy', 'airy')),
    'auto_compact_k': Setting('server', 'auto_compact_k', 'integer', 250, minimum=50, maximum=1000),
    'worker_auto_compact_k': Setting('server', 'worker_auto_compact_k', 'integer', 250, minimum=50, maximum=1000),
})
_OPERATIONS = ('change', 'import', 'reset', 'undo')
_MAX_BYTES = 1024 * 1024
_MAX_HISTORY = 100


def factory_defaults():
    """Reference defaults only; the caller supplies its effective defaults."""
    return {key: spec.factory_default for key, spec in REGISTRY.items()}


def registry():
    """Public allowlist with types, portability and backing-surface metadata."""
    return {key: dict(scope=spec.scope, source_key=spec.source_key,
                      type=spec.kind, portable=True,
                      factory_default=spec.factory_default,
                      choices=list(spec.choices), minimum=spec.minimum,
                      maximum=spec.maximum) for key, spec in REGISTRY.items()}


def _values(values, *, deletions=False):
    if type(values) is not dict or any(key not in REGISTRY for key in values):
        raise ValidationError('Expected an allowlisted preference object')
    return {key: None if deletions and value is None else REGISTRY[key].validate(value)
            for key, value in values.items()}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError('Duplicate JSON field')
        result[key] = value
    return result


def _decode(raw):
    if not isinstance(raw, (str, bytes)) or len(raw) > _MAX_BYTES:
        raise ValidationError('Invalid or oversized JSON document')
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError('Invalid JSON document') from exc


def _profile(profile):
    if isinstance(profile, (str, bytes)):
        profile = _decode(profile)
    if (type(profile) is not dict or set(profile) != {'format', 'version', 'values'}
            or profile['format'] != 'ccc-preferences'
            or type(profile['version']) is not int or profile['version'] != 1):
        raise ValidationError('Unsupported preference profile')
    return _values(profile['values'])


@contextmanager
def _lock(directory):
    """Cooperating writers serialize across processes; OS releases on exit."""
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / 'preferences.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        # Closing releases the OS lock, including on validation/write failure.
        os.close(fd)


class PreferenceStore:
    """Atomic, bounded ledger in a caller-selected directory; never auto-migrate."""

    def __init__(self, state_dir, effective_defaults, *, history_limit=50):
        defaults = _values(effective_defaults)
        if set(defaults) != set(REGISTRY):
            raise ValidationError('Supply effective defaults for every registered key')
        if type(history_limit) is not int or not 1 <= history_limit <= _MAX_HISTORY:
            raise ValidationError('History limit must be between 1 and 100')
        self.defaults = MappingProxyType(defaults)
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / 'preferences.json'
        self.history_limit = history_limit

    def _read(self):
        try:
            with self.state_file.open('rb') as handle:
                state = _decode(handle.read(_MAX_BYTES + 1))
        except FileNotFoundError:
            return dict(version=1, sequence=0, overrides={}, history=[])
        if (type(state) is not dict
                or set(state) != {'version', 'sequence', 'overrides', 'history'}
                or type(state['version']) is not int or state['version'] != 1
                or type(state['sequence']) is not int or state['sequence'] < 0
                or type(state['history']) is not list or len(state['history']) > _MAX_HISTORY):
            raise ValidationError('Invalid preference ledger')
        _values(state['overrides'])
        previous = None
        for entry in state['history']:
            if (type(entry) is not dict or set(entry) != {'sequence', 'operation', 'before', 'after'}
                    or type(entry['sequence']) is not int
                    or not 1 <= entry['sequence'] <= state['sequence']
                    or type(entry['operation']) is not str or entry['operation'] not in _OPERATIONS):
                raise ValidationError('Invalid preference history')
            _values(entry['before'])
            _values(entry['after'])
            if previous is not None and (entry['sequence'] != previous['sequence'] + 1
                                         or entry['before'] != previous['after']):
                raise ValidationError('Inconsistent preference history')
            previous = entry
        if ((previous is None and (state['sequence'] != 0 or state['overrides']))
                or (previous is not None and (previous['sequence'] != state['sequence']
                                             or previous['after'] != state['overrides']))):
            raise ValidationError('Inconsistent preference ledger')
        return state

    def _revision(self, state):
        return hashlib.sha256(_json([dict(self.defaults), state]).encode()).hexdigest()

    def _effective(self, overrides):
        return {**self.defaults, **overrides}

    def _snapshot(self, state):
        values = self._effective(state['overrides'])
        return dict(revision=self._revision(state), values=values,
                    overrides=state['overrides'], history=state['history'],
                    changed_from_defaults={key: dict(value=value, default=self.defaults[key],
                                                     scope=REGISTRY[key].scope)
                                           for key, value in values.items()
                                           if value != self.defaults[key]})

    def snapshot(self):
        """Read effective values, explicit overrides, differences and history."""
        return self._snapshot(self._read())

    def export_profile(self):
        """Export effective values, excluding revisions, history and metadata."""
        return dict(format='ccc-preferences', version=1,
                    values=self._effective(self._read()['overrides']))

    def _preview(self, state, operation, updates):
        if type(operation) is not str or operation not in _OPERATIONS:
            raise ValidationError('Unknown preference operation')
        updates = _values(updates, deletions=True)
        if operation in ('change', 'import') and any(v is None for v in updates.values()):
            raise ValidationError('Imported and changed values cannot be null')
        if operation == 'reset' and any(v is not None for v in updates.values()):
            raise ValidationError('Reset must remove overrides')
        if operation == 'undo':
            if not state['history']:
                raise ValidationError('No retained change to undo')
            target = state['history'][-1]['before']
            expected = {key: target.get(key) for key in set(target) | set(state['overrides'])}
            if updates != expected:
                raise ValidationError('Undo must restore the latest transaction')
        after = dict(state['overrides'])
        for key, value in updates.items():
            if value is None:
                after.pop(key, None)
            else:
                after[key] = value
        before_values, after_values = self._effective(state['overrides']), self._effective(after)
        changes = [dict(key=key, scope=REGISTRY[key].scope,
                        before=before_values[key], after=after_values[key],
                        before_overridden=key in state['overrides'], after_overridden=key in after)
                   for key in sorted(updates)
                   if (state['overrides'].get(key), key in state['overrides']) != (after.get(key), key in after)]
        return dict(revision=self._revision(state), operation=operation,
                    updates=updates, changes=changes)

    def preview_change(self, values):
        return self._preview(self._read(), 'change', _values(values))

    def preview_import(self, profile):
        """Validate the entire profile before returning a merge-only preview."""
        return self._preview(self._read(), 'import', _profile(profile))

    def preview_reset(self, *, scope=None, keys=None):
        """Reset all, a backing scope, or explicit keys (never both selectors)."""
        if scope is not None and keys is not None:
            raise ValidationError('Choose scope or keys')
        if scope is not None and scope not in ('browser', 'server'):
            raise ValidationError('Unknown preference scope')
        if keys is not None:
            if (type(keys) not in (list, tuple) or any(type(k) is not str or k not in REGISTRY for k in keys)
                    or len(set(keys)) != len(keys)):
                raise ValidationError('Invalid reset selection')
            selected = keys
        else:
            selected = [key for key, spec in REGISTRY.items() if scope is None or spec.scope == scope]
        return self._preview(self._read(), 'reset', {key: None for key in selected})

    def preview_undo(self):
        state = self._read()
        if not state['history']:
            raise ValidationError('No retained change to undo')
        target = state['history'][-1]['before']
        return self._preview(state, 'undo', {key: target.get(key)
                             for key in set(target) | set(state['overrides'])})

    def _check(self, state, preview):
        if type(preview) is not dict or set(preview) != {'revision', 'operation', 'updates', 'changes'}:
            raise ValidationError('Invalid preference preview')
        if preview['revision'] != self._revision(state):
            raise StaleRevisionError('Preferences or defaults changed; preview again')
        expected = self._preview(state, preview['operation'], preview['updates'])
        # Compare canonical JSON so bools cannot masquerade as integers in diffs.
        try:
            identical = _json(expected) == _json(preview)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValidationError('Invalid preference preview') from exc
        if not identical:
            raise ValidationError('Preview details do not match the proposed change')

    def apply(self, preview):
        """Revalidate, reject stale previews, atomically commit state and history.

        All ledger writers must use this API. Concurrent applies of one revision
        yield at most one changed transaction. Failed validation creates no files.
        A successful no-op preserves the revision and history.
        """
        # Detach untrusted caller data before locking or creating any files.
        try:
            preview = _decode(_json(preview))
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValidationError('Invalid preference preview') from exc
        initial_state = self._read()
        self._check(initial_state, preview)
        if not preview['changes']:
            return self._snapshot(initial_state)
        with _lock(self.state_dir):
            state = self._read()
            self._check(state, preview)
            before = dict(state['overrides'])
            for key, value in preview['updates'].items():
                if value is None:
                    state['overrides'].pop(key, None)
                else:
                    state['overrides'][key] = value
            state['sequence'] += 1
            state['history'].append(dict(sequence=state['sequence'], operation=preview['operation'],
                                         before=before, after=dict(state['overrides'])))
            state['history'] = state['history'][-self.history_limit:]
            self._write(state)
            return self._snapshot(state)

    def _write(self, state):
        raw = _json(state).encode() + b'\n'
        if len(raw) > _MAX_BYTES:
            raise ValidationError('Preference ledger exceeds size limit')
        fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=self.state_dir)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
