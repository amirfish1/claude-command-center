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
    'managed-restart-control.json',
    'codex-managed-recovery.json', 'codex-managed-recovery.json.lock',
    'codex-thread-registry.json', 'codex-thread-registry.json.lock',
    'codex-app-server-state.json', 'pending-inputs.json', 'pending-inputs.json.lock',
))


def worker_profile(state_root, user, *, role='worker', identity_namespace='host', account_key=None,
                   control_stdio=False, managed_socket=None):
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
    if control_stdio and role != 'worker':
        raise Refused('root authority is the restart control role; health profile cannot accept stdin')
    sockets = ['/state/worker.sock']
    if managed_socket is not None:
        if (not control_stdio or not isinstance(managed_socket, str)
                or not re.fullmatch(r'/state/[a-z0-9_-]+/[a-z0-9_-]+\.sock', managed_socket)):
            raise Refused('managed socket must be an exact private state socket')
        sockets.append(managed_socket)
        environment.append(('CCC_CODEX_APP_SERVER_SOCKET', managed_socket))
    result = ExecutionProfile(
        '/opt/ccc/ccc_worker.py',
        argv=('--health', '--socket', '/state/worker.sock') if role == 'control'
        else ('--socket', '/state/worker.sock', *(['--managed-control-stdio'] if control_stdio else [])),
        environment=tuple(environment),
        data_mounts=((str(state_root), '/state', role == 'control'),),
        user=user,
        generated_sources=(('<catalog-v1>', CATALOG),),
        unix_sockets=tuple(sockets), state_files=STATE_FILES,
        identity_namespace=identity_namespace,
        control_stdio=control_stdio,
    )
    result.value()
    return result
