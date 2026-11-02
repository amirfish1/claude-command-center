"""Scoped, revision-checked queue exceptions; no runtime dispatch or migration."""
import copy
import json
import os
import stat
import tempfile

from .settings_insights import revision

FIELDS = ('engine', 'model', 'effort', 'fallback_to_default_worker', 'headroom_dispatch')


class StalePreview(ValueError):
    pass


def read(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Queue configuration must be an object')
    return value


def defaults_text(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def state_revision(config, defaults):
    return revision([config, defaults])


def entry(config, name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Select an existing queue')
    matches = [key for key in config if key.strip().upper() == name.strip().upper()]
    if len(matches) != 1:
        raise ValueError('Queue no longer available or its name is ambiguous')
    key = matches[0]
    if not isinstance(config[key], dict) or config[key].get('archived'):
        raise ValueError('Queue no longer available')
    return key


def prepare(config, name, changes):
    key = entry(config, name)
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELDS):
        raise ValueError('Choose supported queue-policy changes')
    result = copy.deepcopy(config)
    for field, value in changes.items():
        if field in ('fallback_to_default_worker', 'headroom_dispatch'):
            if value is not None and type(value) is not bool:
                raise ValueError(field + ' must be true, false or null (follow default)')
        elif value is not None and (not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value)):
            raise ValueError(field + ' must be a single line of at most 256 characters')
        if value is None or value == '':
            result[key].pop(field, None)
        else:
            result[key][field] = value
    diff = [{'key': field, 'before': config[key].get(field), 'before_set': field in config[key],
             'after': result[key].get(field), 'after_set': field in result[key]}
            for field in FIELDS if (field in config[key], config[key].get(field)) != (field in result[key], result[key].get(field))]
    return key, result, diff


def publish(path, defaults_path, prepared, expected):
    """CCC writers share the endpoint lock; detect external edits before replace.

    External writers do not take CCC's lock, so this retains their existing
    last-writer contract for the tiny interval after the final revision check.
    """
    fd, temporary = tempfile.mkstemp(prefix='queue-policy-', suffix='.tmp', dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, 'w') as handle:
            json.dump(prepared, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if state_revision(read(path), defaults_text(defaults_path)) != expected:
            raise StalePreview('Queue or worker defaults changed. Refresh and preview again.')
        from .configuration_history import check_publication
        check_publication(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
