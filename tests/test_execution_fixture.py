"""Offline fixture admission/selection checks; no native or root execution."""
import base64
import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from ccc_server import codex_managed_proof as proof
from ccc_server import execution_authority as authority
from ccc_server import execution_fixture as fixture
from ccc_server import execution_restart as restart
from ccc_server import codex_managed_recovery as recovery
from ccc_server.execution_artifact import Refused, canonical, digest
from tests.test_codex_managed_proof import packet
from tests.test_execution_restart import harness


@pytest.fixture
def planned(harness, monkeypatch):
    h = harness
    h.manifest.update(scope='fixture', purpose='planned-P6', fixture_identity={
        'private_root': {'source': '/private/state', 'device': 1, 'inode': 11, 'uid': 1001},
        'socket': {'path': '/state/native/control.sock', 'device': 1, 'inode': 2,
                   'directory_device': 1, 'directory_inode': 3},
        'daemon': copy.deepcopy(h.snapshot['daemon']), 'network_namespace': 'net:[123]'})
    del h.manifest['proofs']['P6']
    h.raw = canonical(h.manifest)
    h.anchor['format'] = fixture.FORMAT
    h.anchor['fixture_acceptance'] = h.anchor.pop('managed_acceptance')
    h.anchor['fixture_acceptance']['manifest_sha256'] = digest(h.raw)
    h.anchor['bootstrap_acceptance'] = {'manifest_sha256': '5' * 64, 'reference': 'offline only'}
    old = h.coordinator
    h.coordinator = fixture.FixtureCoordinator(h.anchor, old.authority, old.docker,
        old.verifier, old.profiles, old.identities, old.authenticate, old.read_anchor,
        fixture_identity=copy.deepcopy(h.manifest['fixture_identity']))
    monkeypatch.setattr(fixture, 'observe_binding', lambda *_: copy.deepcopy(h.coordinator.fixture_identity))
    return h


def test_planned_path_executes_without_forging_completed_p6(planned):
    h = planned
    result = h.coordinator.run(h.raw, h.evidence)
    assert result['scope'] == 'fixture' and result['runtime_authorized'] is False
    assert result['purpose'] == 'planned-P6-execution'
    assert 'P6' not in h.manifest['proofs']
    assert result['trust_base']['initial'] == result['trust_base']['final']
    assert result['trust_base']['initial']['control']['identity'] == vars(h.coordinator.authority)
    assert ('stop', 'a' * 64) in h.events and ('release', 'b' * 64) in h.events


def test_fixture_anchor_cannot_select_runtime_coordinator(planned):
    h = planned
    with pytest.raises(Refused, match='runtime proof'):
        restart.Coordinator.run(harness_runtime(h), h.raw, h.evidence)
    assert h.events == []


def harness_runtime(h):
    c = h.coordinator
    return restart.Coordinator(c.anchor, c.authority, c.docker, c.verifier,
                               c.profiles, c.identities, c.authenticate, c.read_anchor)


@pytest.mark.parametrize('failure', ['runtime', 'forged-p6', 'missing-p1', 'evidence',
    'root', 'socket', 'daemon', 'namespace', 'native-observation', 'control-profile', 'acceptance'])
def test_fixture_scope_drift_and_missing_prerequisites_never_stop(planned, failure):
    h = planned
    if failure == 'runtime':
        h.manifest['scope'] = 'runtime'
    elif failure == 'forged-p6':
        h.manifest['proofs']['P6'] = {'verdict': 'pass'}
    elif failure == 'missing-p1':
        del h.manifest['proofs']['P1']
    elif failure == 'evidence':
        h.evidence.clear()
    elif failure in ('root', 'socket', 'daemon', 'namespace'):
        field = {'root': 'private_root', 'namespace': 'network_namespace'}.get(failure, failure)
        h.coordinator.fixture_identity[field] = 'changed'
    elif failure == 'native-observation':
        h.snapshot['daemon']['socket_inode'] += 1
    elif failure == 'control-profile':
        h.anchor['execution_profile_sha256'] = '9' * 64
    h.raw = canonical(h.manifest)
    h.anchor['fixture_acceptance']['manifest_sha256'] = digest(h.raw) if failure != 'acceptance' else '0' * 64
    with pytest.raises((Refused, proof.Unproven)):
        h.coordinator.run(h.raw, h.evidence)
    assert not any(e[0] in ('stop', 'release') for e in h.events)


def test_plan_validator_cannot_be_promoted_to_complete_proof(planned):
    h = planned
    with pytest.raises(proof.Unproven, match='missing mandatory'):
        proof.validate(h.raw, acceptance=h.anchor['fixture_acceptance'], expected_scope='fixture',
            codex=h.manifest['codex'], transition=h.manifest['transition'], evidence=h.evidence,
            fixture_identity=h.manifest['fixture_identity'])
    with pytest.raises(proof.Unproven, match='wrong proof scope'):
        proof.validate(h.raw, acceptance=h.anchor['fixture_acceptance'], expected_scope='runtime',
            codex=h.manifest['codex'], transition=h.manifest['transition'], evidence=h.evidence)


@pytest.fixture
def bootstrap_case(planned, monkeypatch):
    h = planned
    c = h.coordinator
    profile = c.profiles['worker']
    fact = c.verifier.observe(c.identities['worker'].container_id, profile, c.identities['worker'])
    contract = {'format': 1, 'scope': 'fixture-bootstrap', 'worker': copy.deepcopy(authority._wire(fact)),
                'profile': profile.value(), 'control': h.manifest['transition']['control'],
                'fixture_identity': h.manifest['fixture_identity']}
    query = {'contract': base64.b64encode(canonical(contract)).decode(),
             'profile': profile.value(), 'identity': vars(c.identities['worker'])}
    h.anchor['bootstrap_acceptance']['manifest_sha256'] = digest(canonical(contract))
    monkeypatch.setattr(authority, 'TrustedLaunchVerifier', lambda *_: c.verifier)
    # bootstrap imports the verifier from execution_artifact directly.
    from ccc_server import execution_artifact
    monkeypatch.setattr(execution_artifact, 'TrustedLaunchVerifier', lambda *_: c.verifier)
    h.events.clear()
    return h, query, contract


def do_bootstrap(h, query):
    c = h.coordinator
    return fixture.bootstrap(query, anchor=c.anchor, authority=c.authority, docker=c.docker,
        processes=c.verifier.processes, authenticate=c.authenticate, read_anchor=c.read_anchor)


def test_separately_accepted_bootstrap_releases_exact_guarded_fixture(bootstrap_case):
    h, query, _ = bootstrap_case
    result = do_bootstrap(h, query)
    assert result['scope'] == 'fixture-bootstrap' and result['runtime_authorized'] is False
    assert h.events[-1] == ('release', 'a' * 64)
    h.mutate['already-running'] = True
    with pytest.raises(Refused, match='already consumed'):
        do_bootstrap(h, query)
    assert h.events.count(('release', 'a' * 64)) == 1


@pytest.mark.parametrize('failure', ['runtime-anchor', 'wrong-contract', 'scope', 'profile',
    'identity', 'control', 'binding', 'already-running', 'authority-drift', 'query-acceptance'])
def test_bootstrap_is_not_generic_release_or_self_acceptance(bootstrap_case, monkeypatch, failure):
    h, query, contract = bootstrap_case
    if failure == 'runtime-anchor':
        h.anchor['format'] = 'ccc-root-authority-v2'
    elif failure == 'wrong-contract':
        h.anchor['bootstrap_acceptance']['manifest_sha256'] = '0' * 64
    elif failure == 'scope':
        contract['scope'] = 'runtime'
    elif failure == 'profile':
        query['profile']['control_stdio'] = False
    elif failure == 'identity':
        contract['worker']['identity']['start_ticks'] += 1
    elif failure == 'control':
        contract['control']['profile_sha256'] = '9' * 64
    elif failure == 'binding':
        def refuse(*_):
            raise Refused('fixture binding drift')
        monkeypatch.setattr(fixture, 'observe_binding', refuse)
    elif failure == 'already-running':
        h.mutate['already-running'] = True
    elif failure == 'authority-drift':
        h.coordinator.authenticate = lambda: None
    else:
        query['acceptance'] = h.anchor['bootstrap_acceptance']
    if failure in ('scope', 'identity', 'control'):
        query['contract'] = base64.b64encode(canonical(contract)).decode()
        h.anchor['bootstrap_acceptance']['manifest_sha256'] = digest(canonical(contract))
    with pytest.raises((Refused, ValueError)):
        do_bootstrap(h, query)
    assert not any(e[0] == 'release' for e in h.events)


@pytest.mark.parametrize('operation', ['fixture_restart', 'fixture_bootstrap'])
def test_actual_root_main_reads_hash_bound_stdin_and_routes_fixture_only(monkeypatch, operation):
    data = canonical({'offline': 'data'})
    anchor = {'format': fixture.FORMAT}
    monkeypatch.setattr(authority, 'read_anchor', lambda _: anchor)
    monkeypatch.setattr(authority, 'RootDocker', object)
    monkeypatch.setattr(authority, 'LinuxProcesses', object)
    monkeypatch.setattr(authority, 'authenticate_self', lambda *_: 'authority')
    monkeypatch.setattr(authority, 'TrustedLaunchVerifier', lambda *_: None)
    monkeypatch.setattr(authority.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(data)))
    monkeypatch.setenv('CCC_ATTESTATION_QUERY', json.dumps({'operation': operation,
        'stdin_bytes': len(data), 'stdin_sha256': digest(data)}))
    calls = []
    def route(query, **kwargs):
        calls.append((query, kwargs['anchor']))
        return {'ok': True}
    monkeypatch.setattr(fixture, 'run_query' if operation == 'fixture_restart' else 'bootstrap', route)
    # main serializes the authenticated ProcessIdentity on exit.
    monkeypatch.setattr(authority, 'authenticate_self', lambda *_: SimpleNamespace(pid=1))
    authority.main()
    assert calls == [({'offline': 'data'}, anchor)]
    anchor['format'] = 'ccc-root-authority-v2'
    monkeypatch.setattr(authority.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(data)))
    with pytest.raises(Refused, match='fixture-only'):
        authority.main()


@pytest.mark.parametrize('operation', ['fixture_restart', 'fixture_bootstrap'])
def test_fixture_client_refuses_runtime_anchor_before_creating_authority(monkeypatch, operation):
    monkeypatch.setattr(authority, 'read_anchor', lambda _: {'format': 'ccc-root-authority-v2'})
    with pytest.raises(Refused):
        getattr(authority.AttestationClient('/unused'), operation)({})


def test_fixture_anchor_has_no_additional_privileges_mounts_or_command_policy():
    anchor = {'image_id': 'sha256:' + 'a' * 64, 'format': fixture.FORMAT}
    actual = authority.authority_config(anchor, '/root/fixture-anchor.json', {})
    runtime = authority.authority_config(dict(anchor, format='ccc-root-authority-v2'),
                                         '/root/fixture-anchor.json', {})
    assert actual == runtime
    assert len(actual['HostConfig']['Mounts']) == 3
    assert actual['HostConfig']['CapAdd'] == ['SYS_PTRACE', 'KILL']
    assert actual['Entrypoint'] == authority.ENTRYPOINT and actual['Cmd'] == []


@pytest.mark.parametrize('failure', [None, 'runtime-acceptance', 'missing-bootstrap',
    'blank-reference', 'bad-hash', 'extra-field'])
def test_root_fixture_anchor_requires_both_exact_separate_acceptances(tmp_path, monkeypatch, failure):
    import stat
    anchor = {'format': fixture.FORMAT, 'image_id': 'sha256:' + 'a' * 64,
              'executed_build_sha256': '1' * 64, 'execution_profile_sha256': '2' * 64,
              'fixture_acceptance': {'manifest_sha256': '3' * 64, 'reference': 'offline plan'},
              'bootstrap_acceptance': {'manifest_sha256': '4' * 64, 'reference': 'offline bootstrap'}}
    if failure == 'runtime-acceptance':
        anchor['managed_acceptance'] = anchor.pop('fixture_acceptance')
    elif failure == 'missing-bootstrap':
        del anchor['bootstrap_acceptance']
    elif failure == 'blank-reference':
        anchor['bootstrap_acceptance']['reference'] = ' '
    elif failure == 'bad-hash':
        anchor['fixture_acceptance']['manifest_sha256'] = 'invalid'
    elif failure == 'extra-field':
        anchor['fixture_acceptance']['runtime'] = True
    target = tmp_path / 'anchor.json'
    target.write_bytes(canonical(anchor))
    # Synthetic ownership metadata only; no root file is provisioned.
    class RootFile:
        parents = ()
        def absolute(self):
            return self
        def lstat(self):
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600)
        def __fspath__(self):
            return str(target)
    monkeypatch.setattr(authority, 'Path', lambda _: RootFile())
    if failure is None:
        assert authority.read_anchor(target) == anchor
    else:
        with pytest.raises(Refused):
            authority.read_anchor(target)


@pytest.fixture
def proc_binding(tmp_path, monkeypatch, harness):
    """Synthetic /proc tree and an unlistened socket; no fixture server."""
    if os.getuid() == 0:
        pytest.skip('fixture socket ownership checks require a nonroot test user')
    h = harness
    worker = h.identities['worker']
    view = tmp_path / str(worker.pid) / 'root' / 'state'
    directory = view / 'native'
    directory.mkdir(parents=True)
    view.chmod(0o700)
    directory.chmod(0o700)
    sock = socket.socket(socket.AF_UNIX)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        sock.bind(f'/proc/self/fd/{descriptor}/control.sock')
    finally:
        os.close(descriptor)
    (directory / 'control.sock').chmod(0o600)
    uid = os.getuid()
    # Tests run unprivileged, matching the production profile constraint.
    profile = replace(h.coordinator.profiles['worker'], user=f'{uid}:{os.getgid()}',
        network='container:' + 'e' * 64,
        unix_sockets=('/state/worker.sock', '/state/native/control.sock'),
        environment=h.coordinator.profiles['worker'].environment +
            (('CCC_CODEX_APP_SERVER_SOCKET', '/state/native/control.sock'),))
    native = tmp_path / '201'
    native.mkdir()
    (native / 'exe').write_bytes(b'offline fake native executable')
    binary = digest((native / 'exe').read_bytes())
    monkeypatch.setattr(recovery, 'SUPPORTED', {binary: ('offline', '4' * 64)})
    (native / 'status').write_text(f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nNoNewPrivs:\t1\n' +
        ''.join(key + ':\t00000000\n' for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')))
    (native / 'fd').mkdir()
    (native / 'fd' / '3').symlink_to('socket:[444]')
    net = tmp_path / str(worker.pid) / 'net'
    net.mkdir()
    (net / 'unix').write_text('Num RefCount Protocol Flags Type St Inode Path\n'
        '0: 2 0 00010000 0001 01 444 /state/native/control.sock\n')
    info, parent, root = (directory / 'control.sock').stat(), directory.stat(), view.stat()
    daemon = dict(h.snapshot['daemon'], binary_sha256=binary, version='offline', protocol_sha256='4' * 64,
                  socket_device=info.st_dev, socket_inode=info.st_ino,
                  directory_device=parent.st_dev, directory_inode=parent.st_ino)
    binding = {'private_root': {'source': '/private/state', 'device': root.st_dev,
                               'inode': root.st_ino, 'uid': uid},
               'socket': {'path': '/state/native/control.sock', 'device': info.st_dev, 'inode': info.st_ino,
                          'directory_device': parent.st_dev, 'directory_inode': parent.st_ino},
               'daemon': daemon, 'network_namespace': 'net:[123]'}
    monkeypatch.setattr(fixture, 'Path', lambda name: tmp_path / str(name).removeprefix('/proc/'))
    monkeypatch.setattr(recovery, 'process_identity', lambda _: {key: daemon[key] for key in ('pid', 'start_ticks', 'boot_id')})
    monkeypatch.setattr(recovery, '_cgroups', lambda pid: {'0': '/native' if pid == 201 else '/worker'})
    readlink = os.readlink
    monkeypatch.setattr(fixture.os, 'readlink', lambda name: 'net:[123]' if str(name) == '/proc/201/ns/net' else readlink(name))
    monkeypatch.setattr(fixture.os, 'getpgid', lambda pid: pid)
    monkeypatch.setattr(fixture.os, 'getsid', lambda pid: pid)
    processes = SimpleNamespace(network_namespace=lambda _: 'net:[123]')
    yield SimpleNamespace(profile=profile, identity=worker, expected=binding, processes=processes,
                          native=native, view=view, net=net, directory=directory)
    sock.close()


def test_kernel_binding_reads_actual_socket_process_and_namespace(proc_binding):
    h = proc_binding
    assert fixture.observe_binding(h.profile, h.identity, h.expected, h.processes) == h.expected


@pytest.mark.parametrize('failure', ['root-mode', 'socket-mode', 'symlink', 'native-capability',
    'native-root', 'new-privileges', 'namespace', 'socket-owner', 'cgroup', 'inode', 'daemon-epoch', 'extra-mount', 'host-socket'])
def test_kernel_binding_refuses_privilege_path_namespace_and_identity_drift(proc_binding, monkeypatch, failure):
    h = proc_binding
    if failure == 'root-mode':
        h.view.chmod(0o755)
    elif failure == 'socket-mode':
        (h.directory / 'control.sock').chmod(0o666)
    elif failure == 'symlink':
        h.directory.rename(h.view / 'other')
        h.directory.symlink_to(h.view / 'other')
    elif failure == 'native-capability':
        (h.native / 'status').write_text((h.native / 'status').read_text().replace('CapEff:\t00000000', 'CapEff:\t00000001'))
    elif failure == 'native-root':
        content = (h.native / 'status').read_text().splitlines()
        (h.native / 'status').write_text('\n'.join('Uid:\t0\t0\t0\t0' if line.startswith('Uid:') else line for line in content))
    elif failure == 'new-privileges':
        (h.native / 'status').write_text((h.native / 'status').read_text().replace('NoNewPrivs:\t1', 'NoNewPrivs:\t0'))
    elif failure == 'namespace':
        h.processes.network_namespace = lambda _: 'net:[999]'
    elif failure == 'socket-owner':
        (h.native / 'fd' / '3').unlink()
    elif failure == 'cgroup':
        monkeypatch.setattr(recovery, '_cgroups', lambda _: {'0': '/worker'})
    elif failure == 'inode':
        h.expected['socket']['inode'] += 1
    elif failure == 'daemon-epoch':
        h.expected['daemon']['start_ticks'] += 1
        monkeypatch.setattr(recovery, 'process_identity', lambda _: {'pid': 201, 'start_ticks': 2010, 'boot_id': 'boot'})
    elif failure == 'extra-mount':
        h.profile = replace(h.profile, data_mounts=h.profile.data_mounts + (('/extra', '/logs', False),))
    else:
        h.profile = replace(h.profile, unix_sockets=h.profile.unix_sockets + ('/var/run/docker.sock',))
    with pytest.raises(Refused):
        fixture.observe_binding(h.profile, h.identity, h.expected, h.processes)
