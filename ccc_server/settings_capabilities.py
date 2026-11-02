"""Optional setup inventory without reading credentials or probing services.

Presence means saved configuration, not a healthy or running connection. Runtime
facts must be supplied by the caller from already-known in-process state.
"""
from pathlib import Path


def inventory(locations, runtime=None):
    """Return only fixed capability IDs, boolean presence and runtime states.

    ``locations`` maps IDs to caller-owned paths. No file contents, paths,
    credentials, process probes or network calls enter the response.
    """
    runtime = runtime or {}
    entries = {}
    for name in ('voice', 'remote', 'free', 'operator'):
        known = True
        configured = False
        for location in locations.get(name, ()):
            try:
                configured = Path(location).exists() or configured
            except (OSError, ValueError):
                known = False
        state = runtime.get(name, 'unknown')
        if state not in ('on', 'off', 'unknown', 'unavailable'):
            state = 'unknown'
        entries[name] = {'configured': True if configured else False if known else None, 'runtime': state}
    return {'capabilities': entries}


def snapshot(core):
    """Adapt native locations without invoking their secret/probe helpers."""
    from ccc_server import cloud_relay, free_router, phone_access, realtime_voice
    import federation
    import time

    session = realtime_voice._SESSION
    voice = 'on' if session and session.state not in ('closed', 'error') else 'off'
    cache = free_router._STATUS_CACHE
    recent = cache.get('data') if time.time() - cache.get('at', 0) < free_router._STATUS_TTL_S else None
    free = ('on' if recent.get('running') else 'off') if recent and isinstance(recent.get('running'), bool) else 'unknown'
    # Listener binding is a known runtime fact, not an end-to-end access test.
    bind = (core.RUNTIME_NETWORK_INFO or {}).get('bind_host')
    remote = 'on' if bind and bind not in ('localhost', '127.0.0.1', '::1') else 'unknown'
    locations = {
        'voice': [realtime_voice.VOICE_CONFIG_FILE],
        'remote': [core.NETWORK_CONFIG_FILE, phone_access.state_file(), federation._peers_file(), cloud_relay.CloudPaths().config],
        'free': [free_router.state_path(), free_router.config_path(), free_router.install_dir()],
        'operator': [core.CAR_MODE_CONFIG_FILE],
    }
    operator = 'unknown' if core._CAR_MODE.get('pid') or (core._CAR_MODE_DIR / 'run.sh').exists() else 'unavailable'
    result = inventory(locations, {'voice': voice, 'remote': remote, 'free': free, 'operator': operator})
    if core._CAR_MODE.get('pid'):
        result['capabilities']['operator']['configured'] = True
    return result
