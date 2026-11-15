"""Closed Python worker/control profiles; no native launch authority.

These profiles do not select a running worker or provision a root authority.
Kernel identity visibility is explicit and enters the measured profile. Native
subprocesses remain denied, including optional engine tools and plugins.
"""
import re

from .execution_artifact import ExecutionProfile, Refused

CATALOG = '/opt/ccc/ccc_server/execution_generated_sources.json'
STATE_FILES = tuple('/state/' + name for name in (
    'worker.token', 'worker.pid', 'control-plane.sqlite3',
    'control-plane.sqlite3-wal', 'control-plane.sqlite3-shm',
    'codex-restart-fence.json', 'codex-restart-fence.lock',
    'archive-sticky.json', 'ship-acks.json', 'spawned-pids.json',
    'spawned-pids.lock',
))


def worker_profile(state_root, user, *, role='worker', identity_namespace='host', account_key=None):
    """Declare one private ledger/socket, never mutable code or configuration.

    The control role only queries health; its mount is read-only. Host PID and
    cgroup visibility permits kernel peer observations across daemon/client
    namespaces without accepting PID zero or caller-reported process scopes.
    This grants no permission to interrupt or adopt an existing process.
    """
    if role not in ('worker', 'control'):
        raise Refused('unknown worker profile role')
    environment = [("CCC_STATE_DIR", "/state"), ("CCC_MEMORY_RELEASE", "0"),
                   ("CCC_WORKER_PROCESS", "1"), ("HOME", "/opt/ccc")]
    if account_key is not None:
        if not re.fullmatch(r"[0-9a-f]{20}", account_key):
            raise Refused("account identity must be an exact account key")
        environment.append(("CCC_CODEX_ACCOUNT_KEY", account_key))
    result = ExecutionProfile(
        '/opt/ccc/ccc_worker.py',
        argv=('--health', '--socket', '/state/worker.sock') if role == 'control'
        else ('--socket', '/state/worker.sock'),
        environment=tuple(environment),
        data_mounts=((str(state_root), '/state', role == 'control'),),
        user=user,
        generated_sources=(('<catalog-v1>', CATALOG),),
        unix_sockets=('/state/worker.sock',), state_files=STATE_FILES,
        identity_namespace=identity_namespace,
    )
    result.value()
    return result
