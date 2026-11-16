"""Root-observed disposable harness facts; no native launch or worker release.

Runs only in the existing authenticated one-shot authority. A separate exact
root acceptance names the preparation packet. This scope cannot bootstrap a
fixture worker, authorize P6, or select the runtime restart barrier.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
import time

from ccc_server.execution_artifact import canonical, digest, _resolve
from ccc_server.execution_restart import _need, _bytes

FORMAT = 'ccc-root-authority-proof-v1'


def observe(query, *, anchor, docker, processes, allow_expired=False):
    from scripts.codex_restart_preflight import native_config, bound_profiles, FORMAT as PACKET_FORMAT, SOURCES, grant
    from scripts.codex_restart_fixture import _json
    _need(anchor.get('format') == FORMAT and set(query) == {'packet', 'container_id', 'worker', 'native'},
          'proof observation requires separate exact root acceptance')
    raw = _bytes(query['packet'])
    packet = _json(raw)
    accepted = anchor.get('execution_acceptance', {})
    _need(accepted.get('manifest_sha256') == digest(raw) and accepted.get('reference'),
          'disposable packet not accepted by root')
    _need(packet.get('format') == PACKET_FORMAT and packet.get('scope') == 'disposable-proof-preparation'
          and packet.get('runtime_authorized') is False and packet.get('execution_ready') is True
          and packet.get('rebuild_required') == [], 'proof packet is not ready or wrong scope')
    from datetime import datetime
    limits = grant(packet['grant'])
    _need(allow_expired or time.time() < datetime.fromisoformat(limits['expires_at']).timestamp(), 'proof grant expired')
    image = packet['images']['native']
    _need(packet['images']['control'] == {'image_id': anchor['image_id'],
          'artifact_sha256': anchor['executed_build_sha256']}, 'different measured outer control image')
    expected = native_config(image['image_id'], packet['state_root'], packet['user'])
    _need(packet['harness_launch'] == expected, 'harness launch is not the fixed closed policy')
    container = docker.inspect_container(query['container_id'])
    _need(container['Id'] == query['container_id'] and container['Image'] == image['image_id'],
          'proof harness container/image drift')
    artifact = docker.measure(container['Image'])
    _need(artifact.executed_build_sha256 == image['artifact_sha256'], 'proof rootfs drift')
    entries = {row['path']: row for row in json.loads(artifact.manifest)['entries']}
    _need(set(packet['source_sha256']) == set(SOURCES), 'incomplete proof source closure')
    for name, value in packet['source_sha256'].items():
        row = entries.get('opt/ccc/' + name, {})
        _need(row.get('type') == 'file' and row.get('sha256') == value, 'unmeasured proof helper')
    config, host = container['Config'], container['HostConfig']
    for key in ('User', 'WorkingDir', 'Entrypoint', 'Cmd', 'OpenStdin', 'StdinOnce', 'Tty'):
        _need(config.get(key) == expected[key], 'proof launch drift: ' + key)
    _need(sorted(config.get('Env') or []) == sorted(expected['Env']), 'proof environment drift')
    for key in ('ReadonlyRootfs', 'NetworkMode', 'PidMode', 'CgroupnsMode', 'IpcMode', 'Runtime'):
        _need(host.get(key) == expected['HostConfig'][key], 'proof runtime drift: ' + key)
    _need(set(host.get('CapDrop') or []) == {'ALL'} and not host.get('CapAdd')
          and set(host.get('SecurityOpt') or []) in ({'no-new-privileges'}, {'no-new-privileges=true'}),
          'proof privileges retained')
    _need(not any(host.get(key) for key in ('Privileged', 'Init', 'Binds', 'VolumesFrom',
        'Devices', 'DeviceRequests', 'Tmpfs', 'ExtraHosts', 'Links')), 'unmeasured proof runtime facility')
    _need(host.get('Mounts') == expected['HostConfig']['Mounts'], 'proof mount specification drift')
    mounts = container.get('Mounts', [])
    _need(len(mounts) == 2 and sorted((m['Destination'], m['Type'], bool(m['RW'])) for m in mounts)
          == [('/etc', 'image', False), ('/state', 'bind', True)], 'proof has extra mounts')
    _need(next(m for m in mounts if m['Destination'] == '/state')['Source'] == packet['state_root'],
          'proof private root changed')
    docker.verify_runtime_etc(container, artifact)
    identity = processes.identity(container)
    _need(processes.executable(identity) == entries[_resolve(entries, 'usr/local/bin/python3')]['sha256'],
          'proof harness interpreter drift')
    security = processes.security(identity)
    uid = int(packet['user'].split(':')[0])
    _need(uid != 0 and all(int(v) == uid for v in security.get('Uid', '').split())
          and security.get('Uid', '').split() and security.get('NoNewPrivs', '').strip() == '1'
          and all(int(security.get(k, '-1'), 16) == 0 for k in
                  ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')), 'proof kernel privilege drift')
    kernel_mounts = processes.mounts(identity)
    _need('ro' in kernel_mounts.get('/', ()) and 'ro' in kernel_mounts.get('/etc', ())
          and {'noexec', 'nosuid', 'nodev'} <= kernel_mounts.get('/state', set()),
          'proof data/root mount enforcement missing')
    namespace = processes.network_namespace(identity)
    host_namespace = os.readlink('/proc/1/ns/net')
    _need(namespace != host_namespace and packet['actual']['host_netns'] == host_namespace,
          'proof uses host namespace or wrong host witness')
    _need(processes.identity(docker.inspect_container(container['Id'])) == identity,
          'proof identity changed during observation')
    # Bind all actual settings, not the requested configuration hash.
    measured = digest(canonical({'format': 'ccc-proof-harness-profile-v1',
        'config': config, 'host_config': host, 'mounts': mounts,
        'runtime': {k: container.get(k) for k in ('Platform', 'Driver', 'AppArmorProfile', 'ProcessLabel', 'MountLabel')},
        'kernel_environment': sorted(processes.environment(identity))}))
    result = {'scope': 'proof-observation', 'runtime_authorized': False,
        'accepted_packet_sha256': digest(raw), 'reference': accepted['reference'],
        'images': packet['images'], 'source_sha256': packet['source_sha256'],
        'harness': vars(identity), 'native_netns': namespace, 'host_netns': host_namespace,
        'artifact_sha256': artifact.executed_build_sha256, 'measured_profile_sha256': measured,
        'network_none': True, 'no_added_capabilities': True, 'sole_private_writable_mount': True}
    if query['worker'] is not None:
        from ccc_server.execution_authority import _profile, _wire
        from ccc_server.execution_artifact import ProcessIdentity, TrustedLaunchVerifier
        from ccc_server.execution_fixture import observe_binding
        from ccc_server import codex_managed_recovery as recovery
        worker = query['worker']
        _need(set(worker) == {'profile', 'identity', 'role'} and worker['role'] in ('worker', 'replacement'),
              'incomplete proof worker lookup')
        profile = _profile(worker['profile'])
        _need(canonical(profile.value()) == canonical(bound_profiles(packet, container['Id'])[worker['role']].value()),
              'proof role differs from the closed worker policy')
        wanted = ProcessIdentity(**worker['identity'])
        _need(profile.data_mounts == ((packet['state_root'], '/state', False),)
              and profile.user == packet['user'] and profile.control_stdio
              and profile.network == 'container:' + container['Id'], 'proof worker escapes accepted namespace/root')
        fact = TrustedLaunchVerifier(docker, processes).observe(wanted.container_id, profile, wanted)
        _need(fact.image_id == packet['images'][worker['role']]['image_id']
              and fact.executed_build_sha256 == packet['images'][worker['role']]['artifact_sha256'],
              'proof worker uses a different immutable artifact')
        native = query['native']
        _need(recovery._valid_daemon(native), 'native lookup is unknown')
        _need(native['binary_sha256'] == packet['native']['binary_sha256']
              and native['version'] == packet['native']['version']
              and native['protocol_sha256'] == packet['native']['schema_sha256'], 'native lookup pin drift')
        view = Path('/proc') / str(wanted.pid) / 'root'
        root, directory, sock = (view / 'state').stat(), (view / 'state/native').stat(), (view / 'state/native/app.sock').stat()
        binding = {'private_root': {'source': packet['state_root'], 'device': root.st_dev,
                    'inode': root.st_ino, 'uid': uid},
                   'socket': {'path': '/state/native/app.sock', 'device': sock.st_dev, 'inode': sock.st_ino,
                    'directory_device': directory.st_dev, 'directory_inode': directory.st_ino},
                   'daemon': native, 'network_namespace': namespace}
        # Every supplied daemon field and socket listener/cgroup fact is checked
        # by the existing root/kernel observer. A hint never attests itself.
        result['fixture_identity'] = observe_binding(profile, wanted, binding, processes)
        result['worker'] = _wire(fact)
    else:
        _need(query['native'] is None, 'native lookup requires exact guarded worker role')
    return result


def cleanup(query, *, anchor, docker, processes):
    """Dispose exact scoped fixture workers through the measured authority.

    A query cannot target an arbitrary container: every live target must have
    the accepted image, closed worker policy, private root and the harness's
    namespace. Stopped targets require the same immutable launch and exact
    original start identity. No guard is released and no executable is started.
    """
    _need(set(query) == {'packet', 'container_id', 'roles'}, 'invalid cleanup scope')
    observation = observe({'packet': query['packet'], 'container_id': query['container_id'],
        'worker': None, 'native': None}, anchor=anchor, docker=docker, processes=processes, allow_expired=True)
    packet = json.loads(_bytes(query['packet']))
    removed = _cleanup_roles(packet, query['container_id'], query['roles'], docker, processes)
    return {'scope': 'proof-cleanup', 'runtime_authorized': False,
            'accepted_packet_sha256': observation['accepted_packet_sha256'],
            'removed': removed, 'owned_processes_absent': True, 'owned_containers_absent': True}


def _cleanup_roles(packet, container_id, roles, docker, processes):
    from ccc_server.execution_authority import _profile
    from ccc_server.execution_artifact import ProcessIdentity, TrustedLaunchVerifier
    from scripts.codex_restart_preflight import bound_profiles
    _need(isinstance(roles, list) and len(roles) <= 32, 'unbounded cleanup targets')
    seen, removed, targets = set(), [], []
    for binding in roles:
        _need(set(binding) == {'identity', 'profile', 'role'} and binding['role'] in ('worker', 'replacement'),
              'invalid cleanup role')
        identity, profile = _identity(binding['identity']), _profile(binding['profile'])
        _need(canonical(profile.value()) == canonical(bound_profiles(packet, container_id)[binding['role']].value()),
              'cleanup role differs from the closed worker policy')
        _need(identity.container_id not in seen and identity.container_id != container_id,
              'cleanup duplicate or harness target')
        seen.add(identity.container_id)
        _need(profile.user == packet['user'] and profile.control_stdio
              and profile.data_mounts == ((packet['state_root'], '/state', False),)
              and profile.network == 'container:' + container_id, 'cleanup outside private scope')
        _need(all(type(value) is int and value > 0 for value in
                  (identity.pid, identity.start_ticks, identity.launcher_pid, identity.launcher_start_ticks))
              and identity.boot_id and identity.started_at, 'cleanup original identity unknown')
        targets.append((binding, identity, profile))
    # Validate the complete requested scope before stopping or deleting anything.
    for binding, identity, profile in targets:
        rows = docker.request('GET', '/containers/json?all=1')
        if identity.container_id not in {r['Id'] for r in rows}:
            _dead_role(identity)
            removed.append(identity.container_id)
            continue
        container = docker.inspect_container(identity.container_id)
        image = packet['images'][binding['role']]
        _need(container['Id'] == identity.container_id and container['Image'] == image['image_id']
              and container['State']['StartedAt'] == identity.started_at, 'cleanup launch identity drift')
        _closed_launch(container, profile.config(image['image_id']))
        if container['State'].get('Running'):
            fact = TrustedLaunchVerifier(docker, processes).observe(identity.container_id, profile, identity)
            _need(fact.executed_build_sha256 == image['artifact_sha256'], 'cleanup executable closure drift')
            docker.stop_exact(fact)
        else:
            # No live process can be attested after its exit. Match the exact
            # daemon launch plus original kernel identities before removal.
            _dead_role(identity)
        _need(not docker.inspect_container(identity.container_id)['State'].get('Running'), 'cleanup death unknown')
        _dead_role(identity)
        docker.request('DELETE', '/containers/' + identity.container_id)
        removed.append(identity.container_id)
    _need(not set(removed).intersection(row['Id'] for row in docker.request('GET', '/containers/json?all=1')),
          'cleanup container absence unknown')
    return removed


def _identity(value):
    from ccc_server.execution_artifact import ProcessIdentity
    _need(isinstance(value, dict) and set(value) == set(ProcessIdentity.__dataclass_fields__),
          'cleanup incomplete identity')
    _need(isinstance(value['container_id'], str) and re.fullmatch('[0-9a-f]{64}', value['container_id'])
          and all(type(value[k]) is int and value[k] > 0 for k in
                  ('pid', 'start_ticks', 'launcher_pid', 'launcher_start_ticks'))
          and isinstance(value['boot_id'], str)
          and re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']),
          'cleanup malformed identity')
    try:
        _need(datetime.fromisoformat(value['started_at']).utcoffset() is not None,
              'cleanup unknown container start')
    except (TypeError, ValueError) as exc:
        raise ValueError('cleanup malformed container start') from exc
    return ProcessIdentity(**value)


def _dead_role(identity):
    from ccc_server.codex_managed_recovery import exact_process_dead
    _need(all(exact_process_dead({'pid': pid, 'start_ticks': ticks, 'boot_id': identity.boot_id})
              for pid, ticks in ((identity.pid, identity.start_ticks),
                                 (identity.launcher_pid, identity.launcher_start_ticks))),
          'cleanup original processes not absent')


def _closed_launch(container, expected):
    """Match even stopped launches; caller separately proves process death."""
    for key, value in expected.items():
        if key not in ('Image', 'HostConfig'):
            actual = container['Config'].get(key)
            _need((sorted(actual or []) == sorted(value) if key == 'Env' else actual == value),
                  'cleanup launch configuration drift: ' + key)
    host = container['HostConfig']
    for key, value in expected['HostConfig'].items():
        if key == 'SecurityOpt':
            _need(set(host.get(key) or []) in ({'no-new-privileges'}, {'no-new-privileges=true'}),
                  'cleanup privileges drift')
        elif key == 'CapAdd':
            _need(not host.get(key), 'cleanup capabilities drift')
        else:
            _need(host.get(key) == value, 'cleanup runtime drift: ' + key)
    _need(not any(host.get(key) for key in ('Privileged', 'Init', 'Binds', 'VolumesFrom',
        'Devices', 'DeviceRequests', 'Tmpfs', 'ExtraHosts', 'Links')) and not container.get('ExecIDs'),
          'cleanup unmeasured runtime facility')


def finish(query, *, anchor, docker, processes):
    """Final exact-owned cleanup, including the exited or failed harness.

    No passing P6 verdict is made here. This is a separate post-run receipt
    which a final report must retain beside the in-harness observations.
    """
    from ccc_server.execution_artifact import ProcessIdentity, _verified_launch
    from scripts.codex_restart_preflight import native_config, FORMAT as PACKET_FORMAT
    _need(set(query) == {'packet', 'container_id', 'identity', 'roles'}
          and anchor.get('format') == FORMAT, 'invalid final cleanup scope')
    raw = _bytes(query['packet'])
    _need(anchor['execution_acceptance']['manifest_sha256'] == digest(raw), 'cleanup packet not root accepted')
    packet = json.loads(raw)
    _need(packet.get('format') == PACKET_FORMAT and packet.get('scope') == 'disposable-proof-preparation'
          and packet.get('execution_ready') is True and packet.get('runtime_authorized') is False,
          'cleanup packet scope drift')
    wanted = _identity(query['identity'])
    _need(wanted.container_id == query['container_id'], 'final cleanup identity mismatch')
    containers = {row['Id'] for row in docker.request('GET', '/containers/json?all=1')}
    _need(wanted.container_id in containers, 'harness already absent; original cleanup receipt required')
    container = docker.inspect_container(wanted.container_id)
    image = packet['images']['native']
    expected = native_config(image['image_id'], packet['state_root'], packet['user'])
    _need(container['Id'] == wanted.container_id and container['Image'] == image['image_id']
          and container['State']['StartedAt'] == wanted.started_at, 'final harness launch drift')
    _closed_launch(container, expected)
    artifact = docker.measure(image['image_id'])
    _need(artifact.executed_build_sha256 == image['artifact_sha256'], 'final harness closure drift')
    docker.verify_runtime_etc(container, artifact)
    if container['State'].get('Running'):
        observed = observe({'packet': query['packet'], 'container_id': wanted.container_id,
            'worker': None, 'native': None}, anchor=anchor, docker=docker, processes=processes, allow_expired=True)
        _need(observed['harness'] == vars(wanted), 'final harness process identity drift')
    removed = _cleanup_roles(packet, wanted.container_id, query['roles'], docker, processes)
    if container['State'].get('Running'):
        fact = _verified_launch(wanted, image['image_id'], artifact.executed_build_sha256,
            observed['measured_profile_sha256'], processes.executable(wanted))
        docker.stop_exact(fact)
    from ccc_server.codex_managed_recovery import exact_process_dead
    _need(not docker.inspect_container(wanted.container_id)['State'].get('Running')
          and exact_process_dead({'pid': wanted.pid, 'start_ticks': wanted.start_ticks,
                                  'boot_id': wanted.boot_id}), 'final harness death not proved')
    docker.request('DELETE', '/containers/' + wanted.container_id)
    removed.append(wanted.container_id)
    _need(not set(removed).intersection(row['Id'] for row in docker.request('GET', '/containers/json?all=1')),
          'final container absence not proved')
    return {'scope': 'proof-cleanup', 'runtime_authorized': False, 'accepted_packet_sha256': digest(raw),
            'removed': removed, 'owned_processes_absent': True, 'owned_containers_absent': True}
