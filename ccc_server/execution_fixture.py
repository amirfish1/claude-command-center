"""Root-accepted disposable bootstrap and planned replay, never runtime proof.

No service, anchor, native daemon or fixture is started by this module. The
existing immutable authority is the only privileged launcher. Fixture workers
retain the normal guarded, nonroot, no-capability execution profile.
"""
import os
from pathlib import Path, PurePosixPath
import stat

from ccc_server import codex_managed_proof as proof
from ccc_server.execution_artifact import canonical, digest
from ccc_server.execution_restart import Coordinator, _bytes, _need, ROLES

FORMAT = 'ccc-root-authority-fixture-v1'


def observe_binding(profile, identity, expected, processes):
    """Read kernel facts through existing /proc visibility, without a mount.

    Expected PID/path values are lookup hints from the accepted plan. They
    must match fresh socket ownership, executable, process and namespace facts.
    No caller-provided observation is treated as attestation.
    """
    from ccc_server import codex_managed_recovery as recovery
    profile.value()
    _need(isinstance(expected, dict) and set(expected) == {
        'private_root', 'socket', 'daemon', 'network_namespace'}, 'invalid fixture binding')
    root = expected['private_root']
    socket = expected['socket']
    daemon = expected['daemon']
    _need(isinstance(root, dict) and set(root) == {'source', 'device', 'inode', 'uid'}
          and isinstance(socket, dict) and set(socket) == {
              'path', 'device', 'inode', 'directory_device', 'directory_inode'}
          and recovery._valid_daemon(daemon), 'incomplete fixture binding')
    _need(profile.control_stdio and len(profile.data_mounts) == 1
          and profile.data_mounts == ((root['source'], '/state', False),)
          and profile.network.startswith('container:')
          and profile.identity_namespace == 'host', 'fixture profile root/namespace mismatch')
    uid = int(profile.user.split(':')[0])
    name = socket['path']
    _need(isinstance(name, str) and name.startswith('/state/')
          and '..' not in PurePosixPath(name).parts
          and str(PurePosixPath(name)) == name
          and profile.unix_sockets == ('/state/worker.sock', name)
          and dict(profile.environment).get('CCC_CODEX_APP_SERVER_SOCKET') == name,
          'fixture socket differs from exact private profile')
    _need(len(PurePosixPath(name).parts) >= 4, 'fixture socket needs a private directory')
    view = Path(f'/proc/{identity.pid}/root')
    private = (view / 'state').lstat()
    _need(stat.S_ISDIR(private.st_mode) and stat.S_IMODE(private.st_mode) == 0o700
          and private.st_uid == uid, 'fixture root is not private to worker')
    components = PurePosixPath(name).parts[2:]
    target = view / 'state'
    for part in components[:-1]:
        target /= part
        info = target.lstat()
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid
              and stat.S_IMODE(info.st_mode) == 0o700, 'fixture socket directory is unsafe')
    directory = target.lstat()
    target /= components[-1]
    info = target.lstat()
    _need(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid
          and not info.st_mode & 0o077, 'fixture socket is unsafe')
    pid = daemon['pid']
    observed = recovery.process_identity(pid)
    _need(observed == {key: daemon[key] for key in ('pid', 'start_ticks', 'boot_id')},
          'fixture daemon process drift')
    binary = digest(Path(f'/proc/{pid}/exe').read_bytes())
    _need(binary in recovery.SUPPORTED, 'unknown fixture native binary')
    status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines()
                  if ':' in line)
    _need(all(int(value) == uid for value in status['Uid'].split())
          and int(status['NoNewPrivs']) == 1
          and all(int(status[key], 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')),
          'fixture native privileges are not isolated')
    namespace = os.readlink(f'/proc/{pid}/ns/net')
    _need(processes.network_namespace(identity) == namespace,
          'fixture worker and native namespaces differ')
    # The pathname's filesystem inode alone does not identify its listening
    # process. Match the kernel Unix-socket inode to an actual daemon fd too.
    rows = [line.split() for line in Path(f'/proc/{identity.pid}/net/unix').read_text().splitlines()[1:]]
    listeners = [row[6] for row in rows if len(row) == 8 and row[7] == name
                 and row[3] == '00010000' and row[4] == '0001']
    _need(len(listeners) == 1, 'fixture socket listener is missing or ambiguous')
    descriptors = {os.readlink(fd) for fd in Path(f'/proc/{pid}/fd').iterdir()}
    _need('socket:[' + listeners[0] + ']' in descriptors, 'fixture socket belongs to another daemon')
    _need(os.getpgid(pid) != os.getpgid(identity.pid)
          and os.getsid(pid) != os.getsid(identity.pid), 'fixture daemon shares worker lifetime')
    ours, theirs = recovery._cgroups(identity.pid), recovery._cgroups(pid)
    _need(set(ours) == set(theirs) and all(
        theirs[key] != value and not theirs[key].startswith(value + '/')
        for key, value in ours.items()), 'fixture daemon belongs to worker cgroup')
    actual = {'private_root': {'source': profile.data_mounts[0][0], 'device': private.st_dev,
                              'inode': private.st_ino, 'uid': uid},
              'socket': {'path': name, 'device': info.st_dev, 'inode': info.st_ino,
                         'directory_device': directory.st_dev, 'directory_inode': directory.st_ino},
              'daemon': {**observed, 'socket_device': info.st_dev, 'socket_inode': info.st_ino,
                         'directory_device': directory.st_dev, 'directory_inode': directory.st_ino,
                         'binary_sha256': binary, 'version': recovery.SUPPORTED[binary][0],
                         'protocol_sha256': recovery.SUPPORTED[binary][1]},
              'network_namespace': namespace}
    _need(actual == expected and recovery.process_identity(pid) == observed
          and target.lstat() == info and (view / 'state').lstat() == private,
          'fixture socket/daemon/private-root drift')
    return actual


class FixtureCoordinator(Coordinator):
    def __init__(self, *args, fixture_identity, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixture_identity = fixture_identity

    def _acceptance(self):
        _need(self.anchor.get('format') == FORMAT
              and isinstance(self.anchor.get('fixture_acceptance'), dict),
              'root has not accepted a disposable fixture plan')
        return self.anchor['fixture_acceptance']

    def _binding(self, role='replacement'):
        return observe_binding(self.profiles[role], self.identities[role],
                               self.fixture_identity, self.verifier.processes)

    def _facts(self):
        facts = super()._facts()
        for role in ROLES:
            self._binding(role)
        return facts

    def _proof(self, raw, evidence, native, transition):
        binding = self._binding()
        return proof.validate_fixture_plan(raw, acceptance=self._acceptance(), codex=native,
            transition=transition, evidence=evidence, fixture_identity=binding)

    def _validate(self, raw, evidence, receipt, facts, observed_worker=None):
        binding = self._binding()
        _need(receipt.get('observation', {}).get('daemon') == binding['daemon'],
              'worker observation uses a different fixture daemon')
        return super()._validate(raw, evidence, receipt, facts, observed_worker)

    def run(self, raw, evidence):
        # Check the separately accepted plan before freezing even a fixture.
        _need(self._acceptance()['manifest_sha256'] == digest(raw), 'unaccepted fixture plan')
        manifest = proof._decode(raw)
        _need(manifest.get('scope') == 'fixture' and manifest.get('purpose') == 'planned-P6'
              and manifest.get('fixture_identity') == self.fixture_identity,
              'fixture plan scope/binding mismatch')
        result = super().run(raw, evidence)
        return dict(result, scope='fixture', purpose='planned-P6-execution',
                    fixture_identity=self.fixture_identity, runtime_authorized=False)


def run_query(query, *, anchor, authority, docker, processes, authenticate, read_anchor):
    from ccc_server.execution_authority import _profile
    from ccc_server.execution_artifact import ProcessIdentity, TrustedLaunchVerifier
    _need(isinstance(query, dict) and set(query) == {'profiles', 'identities', 'manifest', 'evidence'},
          'unsupported fixture restart request')
    _need(set(query['profiles']) == set(ROLES) and set(query['identities']) == set(ROLES),
          'missing exact fixture roles')
    raw = _bytes(query['manifest'])
    manifest = proof._decode(raw)
    _need(isinstance(query['evidence'], dict) and len(query['evidence']) <= 128,
          'unbounded fixture evidence')
    profiles = {role: _profile(query['profiles'][role]) for role in ROLES}
    identities = {role: ProcessIdentity(**query['identities'][role]) for role in ROLES}
    return FixtureCoordinator(anchor, authority, docker, TrustedLaunchVerifier(docker, processes),
        profiles, identities, authenticate, read_anchor,
        fixture_identity=manifest.get('fixture_identity')).run(
            raw, {key: _bytes(value) for key, value in query['evidence'].items()})


def bootstrap(query, *, anchor, authority, docker, processes, authenticate, read_anchor):
    """Admit exactly one stopped fixture worker under separate root acceptance.

    The contract binds actual guarded identity, image, profile, root, socket,
    daemon and authority. It is not a native/P6 receipt or runtime bootstrap.
    """
    from ccc_server.execution_authority import _profile, _wire
    from ccc_server.execution_artifact import ProcessIdentity, TrustedLaunchVerifier
    _need(anchor.get('format') == FORMAT and set(query) == {'contract', 'profile', 'identity'},
          'fixture bootstrap requires its own root acceptance')
    raw = _bytes(query['contract'])
    accepted = anchor.get('bootstrap_acceptance')
    _need(isinstance(accepted, dict) and accepted.get('manifest_sha256') == digest(raw)
          and isinstance(accepted.get('reference'), str) and accepted['reference'].strip(),
          'fixture bootstrap contract not accepted by root')
    contract = proof._decode(raw)
    _need(set(contract) == {'format', 'scope', 'worker', 'profile', 'control', 'fixture_identity'}
          and type(contract['format']) is int and contract['format'] == 1
          and contract['scope'] == 'fixture-bootstrap',
          'wrong bootstrap contract scope')
    profile, identity = _profile(query['profile']), ProcessIdentity(**query['identity'])
    _need(profile.control_stdio and profile.entry == '/opt/ccc/ccc_worker.py'
          and profile.argv == ('--socket', '/state/worker.sock', '--managed-control-stdio')
          and canonical(profile.value()) == canonical(contract['profile']), 'bootstrap profile mismatch')
    verifier = TrustedLaunchVerifier(docker, processes)
    _need(read_anchor() == anchor and authenticate() == authority, 'bootstrap authority drift')
    fact = verifier.observe(identity.container_id, profile, identity)
    _need(_wire(fact) == contract['worker'] and processes.stopped(identity),
          'bootstrap admission already consumed or identity changed')
    control = {'artifact_sha256': anchor['executed_build_sha256'],
               'profile_sha256': anchor['execution_profile_sha256']}
    _need(contract['control'] == control, 'bootstrap outer launcher mismatch')
    binding = observe_binding(profile, identity, contract['fixture_identity'], processes)
    # Re-observe the full binding and authority immediately before admission.
    _need(observe_binding(profile, identity, binding, processes) == binding
          and read_anchor() == anchor and authenticate() == authority, 'bootstrap final drift')
    verifier.release(fact, profile)
    return {'ok': True, 'scope': 'fixture-bootstrap', 'manifest_sha256': digest(raw),
            'runtime_authorized': False, 'worker': _wire(fact), 'fixture_identity': binding,
            'trust_base': {'control': control, 'identity': vars(authority)}}
