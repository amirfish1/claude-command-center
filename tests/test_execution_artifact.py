"""Offline closure and authoritative-observation rejection controls.

No Codex, agent, managed daemon, native fixture or restart is launched.
Docker image construction is a separately recorded source-build check.
"""

import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

import pytest

from ccc_server.execution_artifact import (
    Artifact, ExecutionProfile, ProcessIdentity, Refused, TrustedLaunchVerifier, VerifiedLaunch, build_artifact,
    canonical, digest, manifest_from_tar, manifest_from_saved_image, measured_profile_sha256, verify_container, LinuxProcesses,
)
from ccc_server.execution_authority import read_anchor


def archive(files, links=(), modes=None):
    output = io.BytesIO()
    modes = modes or {}
    with tarfile.open(fileobj=output, mode="w") as target:
        directories = set()
        for name in [*files, *(p for p, _ in links)]:
            directories.update(str(p) for p in Path(name).parents if str(p) != ".")
        for name in sorted(directories):
            item = tarfile.TarInfo(name)
            item.type, item.mode = tarfile.DIRTYPE, 0o755
            target.addfile(item)
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), modes.get(name, 0o755)
            target.addfile(item, io.BytesIO(data))
        for name, destination in links:
            item = tarfile.TarInfo(name)
            item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, destination
            target.addfile(item)
    output.seek(0)
    return output


def test_manifest_canonical_whole_closure_and_modes():
    files = {"usr/python": b"interpreter", "opt/ccc/worker.py": b"source",
             "usr/lib/dependency.so": b"dependency"}
    links = [("usr/bin/python", "../python"), ("python", "/usr/bin/python")]
    first = manifest_from_tar(archive(files, links))
    assert first == manifest_from_tar(archive(dict(reversed(list(files.items()))), links))
    for name in files:
        changed = dict(files, **{name: b"changed"})
        assert digest(manifest_from_tar(archive(changed, links))) != digest(first)
    assert manifest_from_tar(archive(files, links, {"usr/python": 0o644})) != first
    value = json.loads(first)
    assert value["format"] == "ccc-execution-rootfs-v1"
    assert first == canonical(value)


@pytest.mark.parametrize("links", [
    [("link", "../escape")], [("link", "/missing")],
    [("a", "b"), ("b", "a")], [("a", "missing/../present")],
])
def test_missing_escaping_and_cyclic_links_refuse(links):
    with pytest.raises(Refused):
        manifest_from_tar(archive({"present": b"ok"}, links))


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.FIFOTYPE, tarfile.BLKTYPE])
def test_special_files_refuse(kind):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as target:
        member = tarfile.TarInfo("special")
        member.type = kind
        target.addfile(member)
    data.seek(0)
    with pytest.raises(Refused):
        manifest_from_tar(data)


def saved_image(layer, *, oci=True, wrong_diff=False, corrupt_layer=False):
    config = canonical({'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + ('f' * 64 if wrong_diff else digest(layer))]}})
    config_name = 'blobs/sha256/' + digest(config)
    payload = gzip.compress(layer, mtime=0) if oci else layer
    layer_name = 'blobs/sha256/' + digest(payload)
    record = {'Config': config_name, 'Layers': [layer_name]}
    files = {config_name: config, layer_name: payload if not corrupt_layer else b'corrupted',
             'manifest.json': canonical([record])}
    if oci:
        root = canonical({'schemaVersion': 2, 'config': {'digest': 'sha256:' + digest(config)},
                          'layers': [{'digest': 'sha256:' + digest(payload),
                                      'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}]})
        image_id = 'sha256:' + digest(root)
        files['blobs/sha256/' + digest(root)] = root
    else:
        image_id = 'sha256:' + digest(config)
    return archive(files).read(), image_id


@pytest.mark.parametrize('oci', [False, True])
def test_saved_image_chain_verifies_legacy_and_containerd_layouts(oci):
    layer = archive({'usr/python': b'interpreter'}).read()
    saved, image_id = saved_image(layer, oci=oci)
    assert manifest_from_saved_image(saved, image_id) == manifest_from_tar(io.BytesIO(layer))


@pytest.mark.parametrize('option', ['wrong_diff', 'corrupt_layer'])
def test_saved_image_rejects_fake_content_chain(option):
    saved, image_id = saved_image(archive({'source': b'code'}).read(), **{option: True})
    with pytest.raises(Refused):
        manifest_from_saved_image(saved, image_id)


def fixture():
    files = {"usr/local/bin/python3.12": b"python", "opt/ccc/worker.py": b"worker",
             "opt/ccc/ccc_server/execution_guard.py": b"guard",
             "usr/lib/dep.so": b"dependency"}
    manifest = manifest_from_tar(archive(files, [("usr/local/bin/python3", "python3.12")]))
    artifact = Artifact("sha256:" + "a" * 64, digest(manifest), manifest)
    profile = ExecutionProfile("/opt/ccc/worker.py")
    ident = "b" * 64
    config = profile.config(artifact.image_id)
    container = {"Id": ident, "Image": artifact.image_id, "Config": config,
                 "HostConfig": config.pop("HostConfig"),
                 "Mounts": [{"Type": "image", "Name": artifact.image_id[:31],
                             "Source": "/root-daemon/image-mount", "Destination": "/etc", "RW": False}],
                 "State": {"Running": True, "Paused": False, "Pid": 123,
                           "StartedAt": "2026-01-01T00:00:00Z"}}

    class Docker:
        layer_changes = []

        def inspect_container(self, ref):
            value = copy.deepcopy(container)
            value["Id"] = ref
            value["State"]["Pid"] += int(ref[0], 16)
            return value

        def changes(self, _):
            return self.layer_changes

        def verify_runtime_etc(self, *_):
            pass

        def measure(self, _):
            return artifact

    class Processes:
        start = 456
        sha = digest(b"python")
        mount_table = {"/": {"ro"}, "/etc": {"ro"}, "/dev": {"rw", "nosuid"}, "/dev/pts": {"rw"},
                       "/etc/hosts": {"ro"}, "/etc/hostname": {"ro"}, "/etc/resolv.conf": {"ro"}}

        def identity(self, value):
            return ProcessIdentity(value["Id"], value["State"]["Pid"], self.start,
                                   "boot-id", value["State"]["StartedAt"])

        def executable(self, _):
            return self.sha

        def mounts(self, _):
            return self.mount_table

        def security(self, _):
            return {'NoNewPrivs': '1', 'Seccomp': '2', 'Seccomp_filters': '2'}

        def environment(self, _):
            return container['Config'].get('Env', [])

        mappings = [("/usr/local/bin/python3.12", "r-xp")]

        def executable_mappings(self, _):
            return self.mappings

    return container, artifact, profile, Docker(), Processes()


def test_positive_observation_binds_actual_image_executable_profile_and_start():
    container, artifact, profile, docker, processes = fixture()
    expected = processes.identity(docker.inspect_container(container["Id"]))
    result = TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile, expected)
    assert result.identity == expected
    assert result.executed_build_sha256 == artifact.executed_build_sha256
    assert result.execution_profile_sha256 == measured_profile_sha256(container, profile)
    assert result.executable_sha256 == digest(b"python")


@pytest.mark.parametrize("mutation", [
    lambda c: c["HostConfig"].update(ReadonlyRootfs=False),
    lambda c: c["HostConfig"].update(Privileged=True),
    lambda c: c["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
    lambda c: c["HostConfig"].update(SecurityOpt=[]),
    lambda c: c["HostConfig"].update(NetworkMode="host"),
    lambda c: c["HostConfig"].update(IpcMode="host"),
    lambda c: c["HostConfig"].update(PidMode="host"),
    lambda c: c["HostConfig"].update(CgroupnsMode="host"),
    lambda c: c["HostConfig"].update(Init=True),
    lambda c: c["Config"].update(Env=["PYTHONPATH=/state"]),
    lambda c: c["Config"].update(Env=["CCC_PLUGIN_PATH=/state"]),
    lambda c: c["Config"].update(Cmd=["fake profile"]),
    lambda c: c["Config"].update(User="0:0"),
    lambda c: c.update(Mounts=[{"Source": "/host", "Destination": "/opt/ccc", "RW": False}]),
])
def test_unsafe_or_unmeasured_launch_refuses(mutation):
    container, _, profile, docker, processes = fixture()
    mutation(container)
    with pytest.raises(Refused):
        TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile)


@pytest.mark.parametrize("mount,flags", [("/", {"rw"}), ("/etc/hosts", {"rw"}),
                                        ("/dev/shm", {"rw", "noexec"}),
                                        ("/dev/mqueue", {"rw"}), ("/opt/plugin", {"ro"})])
def test_kernel_writable_root_automatic_files_shared_memory_or_extra_mount_refuses(mount, flags):
    container, _, profile, docker, processes = fixture()
    processes.mount_table = dict(processes.mount_table, **{mount: flags})
    with pytest.raises(Refused):
        TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile)


def test_actual_executable_and_pid_start_checked_again():
    container, _, profile, docker, processes = fixture()
    expected = processes.identity(docker.inspect_container(container["Id"]))
    processes.sha = digest(b"changed local dependency/interpreter")
    with pytest.raises(Refused, match="actual process executable"):
        TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile)
    processes.sha = digest(b"python")
    processes.start += 1
    with pytest.raises(Refused, match="PID/start"):
        TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile, expected)


def test_ordered_transition_and_initial_final_identities():
    container, artifact, profile, docker, processes = fixture()
    verifier = TrustedLaunchVerifier(docker, processes)
    refs = {role: value * 64 for role, value in zip(("worker", "control", "replacement"), "bcd")}
    profiles = dict.fromkeys(refs, profile)
    identities = {role: processes.identity(docker.inspect_container(ref)) for role, ref in refs.items()}
    a = (artifact.executed_build_sha256, measured_profile_sha256(container, profile))
    tested = dict.fromkeys(refs, a)
    assert set(verifier.verify_transition(refs, profiles, tested, identities)) == set(refs)
    for role in refs:
        pair = dict(tested, **{role: ("f" * 64, profile.sha256)})
        with pytest.raises(Refused, match="untested ordered"):
            verifier.verify_transition(refs, profiles, pair, identities)
    processes.start += 1
    with pytest.raises(Refused, match="PID/start"):
        verifier.verify_transition(refs, profiles, tested, identities)


def test_secret_values_never_enter_profile_or_observation():
    container, _, profile, docker, processes = fixture()
    container["Config"]["Env"] = ["CCC_CREDENTIAL_TEST=not-a-real-secret"]
    result = TrustedLaunchVerifier(docker, processes).observe(container["Id"], profile)
    assert "not-a-real-secret" not in repr(result) + canonical(profile.value()).decode()
    with pytest.raises(Refused, match="secret values"):
        ExecutionProfile(profile.entry, environment=(("CCC_CREDENTIAL_TEST", "fake"),)).value()


def test_fake_digest_mutable_layer_and_loaded_dependency_refuse():
    container, artifact, profile, docker, processes = fixture()
    verifier = TrustedLaunchVerifier(docker, processes)
    docker.measure = lambda _: Artifact(artifact.image_id, "f" * 64, artifact.manifest)
    with pytest.raises(Refused, match="fake"):
        verifier.observe(container["Id"], profile)
    docker.measure = lambda _: artifact
    docker.layer_changes = [{"Path": "/opt/ccc/worker.py", "Kind": 0}]
    with pytest.raises(Refused, match="writable layer"):
        verifier.observe(container["Id"], profile)
    docker.layer_changes = []
    for mappings in ([('/state/plugin.so', 'r-xp')], [('', 'rwxp')],
                     [('/usr/lib/dep.so (deleted)', 'r-xp')]):
        processes.mappings = mappings
        with pytest.raises(Refused):
            verifier.observe(container["Id"], profile)


def test_full_runtime_defaults_drift_and_secret_value_redaction():
    container, _, profile, docker, processes = fixture()
    verifier = TrustedLaunchVerifier(docker, processes)
    initial = verifier.observe(container["Id"], profile)
    container["HostConfig"]["Memory"] = 1048576
    changed = verifier.observe(container["Id"], profile)
    assert changed.execution_profile_sha256 != initial.execution_profile_sha256
    container["Config"]["Env"] = ["CCC_CREDENTIAL_TEST=first-fake-value"]
    first = verifier.observe(container["Id"], profile)
    container["Config"]["Env"] = ["CCC_CREDENTIAL_TEST=second-fake-value"]
    second = verifier.observe(container["Id"], profile)
    assert first.execution_profile_sha256 == second.execution_profile_sha256
    assert first.execution_profile_sha256 != changed.execution_profile_sha256  # Name is bound.


def test_distinct_control_profile_and_ordered_upgrade_are_not_interchangeable():
    container, artifact, profile, docker, processes = fixture()
    new_manifest = manifest_from_tar(archive({
        'usr/local/bin/python3.12': b'python', 'opt/ccc/worker.py': b'new worker',
        'opt/ccc/ccc_server/execution_guard.py': b'guard'},
        [('usr/local/bin/python3', 'python3.12')]))
    replacement = Artifact('sha256:' + 'f' * 64, digest(new_manifest), new_manifest)
    control_profile = ExecutionProfile(profile.entry, environment=(('CCC_ROLE', 'control'),))
    inspect = docker.inspect_container

    def actual(ref):
        value = inspect(ref)
        if ref.startswith('c'):
            value['Config']['Env'] = ['CCC_ROLE=control']
            value['Config']['Cmd'] = control_profile.config(value['Image'])['Cmd']
        if ref.startswith('d'):
            value['Image'] = replacement.image_id
            value['Config']['Image'] = replacement.image_id
            value['HostConfig']['Mounts'][0]['Source'] = replacement.image_id[:31]
            value['Mounts'][0]['Name'] = replacement.image_id[:31]
        return value

    docker.inspect_container = actual
    processes.environment = lambda i: actual(i.container_id)['Config'].get('Env', [])
    docker.measure = lambda image: replacement if image == replacement.image_id else artifact
    verifier = TrustedLaunchVerifier(docker, processes)
    refs = {role: value * 64 for role, value in zip(('worker', 'control', 'replacement'), 'bcd')}
    profiles = {'worker': profile, 'control': control_profile, 'replacement': profile}
    identities = {role: processes.identity(actual(ref)) for role, ref in refs.items()}
    observed = {r: verifier.observe(ref, profiles[r], identities[r]) for r, ref in refs.items()}
    tested = {r: (f.executed_build_sha256, f.execution_profile_sha256) for r, f in observed.items()}
    assert verifier.verify_transition(refs, profiles, tested, identities) == observed
    assert tested['control'][1] != tested['worker'][1]
    reversed_pair = dict(tested, worker=tested['replacement'], replacement=tested['worker'])
    with pytest.raises(Refused, match='untested ordered'):
        verifier.verify_transition(refs, profiles, reversed_pair, identities)
    with pytest.raises(Refused, match='distinct process'):
        verifier.verify_transition(refs, profiles, tested, dict.fromkeys(refs, identities['worker']))


def test_build_uses_only_committed_source_and_explicit_base_link_pruning(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'worker.py').write_text('print("committed")')
    (repo / '.gitattributes').write_text('worker.py export-ignore\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'worker.py', '.gitattributes'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test source'], check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    (repo / 'worker.py').write_text('local uncommitted change')
    (repo / 'untracked-plugin.py').write_text('untracked')
    base_digest = 'example.invalid/python@sha256:' + 'a' * 64
    base = archive({'usr/python': b'interpreter', 'etc/example.conf': b'fixed',
                    'etc/á-public.pem': b'certificate'}, [('missing-doc', '/missing')]).read()

    class Images:
        imported = None

        def inspect_image(self, _):
            return {'Id': 'sha256:' + 'b' * 64, 'RepoDigests': [base_digest], 'Config': {}}

        def export(self, _):
            return base

        def command(self, *args, input):
            assert args == ('import', '-')
            self.imported = input
            return ('sha256:' + 'c' * 64).encode()

        def measure(self, image_id):
            value = manifest_from_tar(io.BytesIO(self.imported))
            return Artifact(image_id, digest(value), value)

    images = Images()
    with pytest.raises(Refused, match='missing artifact'):
        build_artifact(base_digest, repo, revision, docker=images)
    artifact = build_artifact(base_digest, repo, revision, prune_base_links=['missing-doc'], docker=images)
    entries = {e['path']: e for e in json.loads(artifact.manifest)['entries']}
    assert entries['opt/ccc/worker.py']['sha256'] == digest(b'print("committed")')
    assert 'opt/ccc/untracked-plugin.py' not in entries
    assert 'missing-doc' not in entries
    assert entries['opt/ccc-runtime/etc/á-public.pem']['sha256'] == digest(b'certificate')
    with pytest.raises(Refused):
        build_artifact(base_digest, repo, revision, prune_base_links=['usr/python'], docker=images)


def test_prepared_launch_stops_before_admission_and_release_rechecks_identity():
    container, artifact, profile, docker, processes = fixture()
    verifier = TrustedLaunchVerifier(docker, processes)
    requests = []

    def request(method, endpoint, body=None):
        requests.append((method, endpoint))
        return {'Id': container['Id']} if endpoint.startswith('/containers/create') else None

    docker.request = request
    processes.stopped = lambda _: True
    releases = []
    processes.release = releases.append
    fact = verifier.prepare_launch('explicit-test-name', artifact.image_id, artifact.executed_build_sha256, profile)
    assert any('name=ccc-execution-explicit-test-name' in endpoint for _, endpoint in requests)
    assert not any('signal=CONT' in endpoint for _, endpoint in requests)
    verifier.release(fact, profile)
    assert releases == [fact.identity]
    processes.start += 1
    before = list(requests)
    with pytest.raises(Refused, match='PID/start'):
        verifier.release(fact, profile)
    assert requests == before
    assert releases == [fact.identity]


def test_native_profiles_and_missing_kernel_guard_refuse():
    container, _, profile, docker, processes = fixture()
    with pytest.raises(Refused, match='subprocess closure'):
        ExecutionProfile(profile.entry, commands=(('/bin/true',),)).value()
    processes.security = lambda _: {'NoNewPrivs': '1', 'Seccomp': '2', 'Seccomp_filters': '1'}
    with pytest.raises(Refused, match='kernel execution guard'):
        TrustedLaunchVerifier(docker, processes).observe(container['Id'], profile)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux seccomp execution boundary')
def test_actual_kernel_filter_denies_even_a_bypass_of_python_subprocess_audit():
    # Ordinary Python only. execve is denied BEFORE /bin/true can execute.
    guard_path = Path(__file__).resolve().parents[1] / 'ccc_server/execution_guard.py'
    program = f"""
import importlib.util,subprocess,errno,os
spec=importlib.util.spec_from_file_location('guard', {str(guard_path)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.install_kernel_policy()
import ssl,sqlite3,threading
thread=threading.Thread(target=lambda:None);thread.start();thread.join()
try:
    subprocess.run(['/bin/true'], check=True)
except PermissionError as exc:
    assert exc.errno == errno.EPERM
    print('kernel-denied')
else:
    raise AssertionError('execve bypassed the kernel filter')
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'kernel-denied'


@pytest.mark.parametrize('mutation', [None, 'image', 'profile', 'mount', 'executable', 'layer'])
def test_authority_bootstrap_requires_actual_pinned_execution(monkeypatch, mutation):
    from types import SimpleNamespace
    import ccc_server.execution_authority as authority
    _, artifact, _, _, processes = fixture()
    anchor = {'format': 'ccc-root-authority-v1', 'image_id': artifact.image_id,
              'executed_build_sha256': artifact.executed_build_sha256}
    config = authority.authority_config(anchor, '/root/test-anchor.json', {})
    current = {'Id': 'e' * 64, 'Image': artifact.image_id, 'Config': config,
               'HostConfig': config.pop('HostConfig'),
               'State': {'Pid': authority.os.getpid(), 'Running': True, 'StartedAt': 'test'},
               'Mounts': [{'Source': source, 'Destination': destination, 'Type': 'bind', 'RW': False}
                          for source, destination in [(authority.SOCKET, authority.SOCKET),
                                                      ('/root/test-anchor.json', authority.ANCHOR)]] +
                         [{'Source': '/root-daemon/image-mount', 'Destination': '/etc',
                           'Type': 'image', 'Name': artifact.image_id[:31], 'RW': False}]}
    anchor['execution_profile_sha256'] = authority.authority_profile_sha256(current)
    processes.mount_table = {'/': {'ro'}, '/etc': {'ro'}, authority.ANCHOR: {'ro'}, authority.SOCKET: {'ro'}}

    class Docker:
        def request(self, *_):
            return [{'Id': current['Id']}]

        def inspect_container(self, _):
            return copy.deepcopy(current)

        def measure(self, _):
            return artifact

        def changes(self, _):
            return [{'Kind': 0}] if mutation == 'layer' else []

        def verify_runtime_etc(self, *_):
            pass

    monkeypatch.setattr(authority.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(authority, 'sys', SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True),
                                                        dont_write_bytecode=True))
    if mutation == 'image':
        current['Image'] = 'sha256:' + 'f' * 64
    elif mutation == 'profile':
        current['HostConfig']['PidMode'] = 'private'
    elif mutation == 'mount':
        processes.mount_table = dict(processes.mount_table, **{'/etc/hosts': {'rw'}})
    elif mutation == 'executable':
        processes.sha = digest(b'forged executable')
    if mutation is None:
        assert authority.authenticate_self(Docker(), anchor, processes).pid == authority.os.getpid()
    else:
        with pytest.raises(Refused):
            authority.authenticate_self(Docker(), anchor, processes)


def test_unprovisioned_or_writable_authority_cannot_issue_facts(tmp_path):
    from ccc_server.execution_authority import AttestationClient
    with pytest.raises(FileNotFoundError):
        AttestationClient(tmp_path / "missing").verify_transition({}, {}, (), {})
    target = tmp_path / "anchor.json"
    target.write_text("{}")
    target.chmod(0o666)
    with pytest.raises(Refused, match="root-controlled"):
        read_anchor(target)
    with pytest.raises(Refused, match='authenticated authority'):
        VerifiedLaunch(ProcessIdentity('a' * 64, 1, 2, 'boot', 'start'),
                       'sha256:' + 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64)


@pytest.mark.parametrize("action,ok", [
    ("import textwrap; print(textwrap.dedent('ok'))", True),
    ("import importlib; importlib.import_module('dynamic_plugin')", False),
    ("exec('print(123)')", False),
    ("import marshal; marshal.loads(b'bad')", False),
    ("import ctypes", False),
    ("import subprocess; subprocess.run(['/bin/sh', '-c', 'true'])", False),
    ("import os; os.environ['UNMEASURED_SETTING']='changed'", False),
])
def test_real_python_guard_blocks_data_import_bytecode_and_subprocess(tmp_path, action, ok):
    # The test source root substitutes for a measured image; the data root is
    # still excluded. This tests actual import/audit execution, not booleans.
    measured = tmp_path / "image"
    measured.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "dynamic_plugin.py").write_text("raise AssertionError('executed data')")
    entry = measured / "entry.py"
    entry.write_text(action)
    guard_path = Path(__file__).resolve().parents[1] / "ccc_server/execution_guard.py"
    program = f"""
import importlib.util,sys,subprocess
spec=importlib.util.spec_from_file_location('guard', {str(guard_path)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.EXCLUDED=({str(data)!r}, '/proc', '/sys', '/state')
guard=module.SourceGuard(())
if {'dynamic_plugin' in action!r}: sys.path.append({str(data)!r})
guard.install()
exec(guard.compile_file({str(entry)!r}), {{'__name__':'__main__'}})
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                            capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr
    assert "executed data" not in result.stderr


@pytest.mark.parametrize('source,ok', [('lambda x: x + 1', True), ('lambda x: x + 2', False)])
def test_generated_code_requires_exact_premeasured_source(tmp_path, source, ok):
    # A fixed generated dataclass/namedtuple body can be committed into the
    # image. Factory names or caller frames alone grant no permission.
    generated = tmp_path / 'generated-source.py'
    generated.write_text('lambda x: x + 1')
    entry = tmp_path / 'entry.py'
    entry.write_text(f'print(eval({source!r})(1))')
    guard_path = Path(__file__).resolve().parents[1] / 'ccc_server/execution_guard.py'
    program = f"""
import importlib.util,sys
spec=importlib.util.spec_from_file_location('guard', {str(guard_path)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.EXCLUDED=('/state', '/proc', '/sys')
guard=module.SourceGuard((), [('<string>', open({str(generated)!r},'rb').read())])
guard.install()
exec(guard.compile_file({str(entry)!r}), {{'__name__':'__main__'}})
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr
    if ok:
        assert result.stdout.strip() == '2'


@pytest.mark.parametrize('declared', [False, True])
def test_actual_guard_refuses_undeclared_mutable_configuration_reads(tmp_path, declared):
    data = tmp_path / 'private-data'
    data.mkdir()
    ledger = data / 'pending-inputs.json'
    ledger.write_text('{"inputs":[]}')
    entry = tmp_path / 'entry.py'
    entry.write_text(f'print(open({str(ledger)!r}).read())')
    guard_path = Path(__file__).resolve().parents[1] / 'ccc_server/execution_guard.py'
    program = f"""
import importlib.util,sys
spec=importlib.util.spec_from_file_location('guard', {str(guard_path)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.EXCLUDED=('/state', '/proc', '/sys'); module.DATA_ROOTS=({str(data)!r},)
guard=module.SourceGuard((), state_files=({str(ledger)!r},) if {declared!r} else ())
guard.install()
exec(guard.compile_file({str(entry)!r}), {{'__name__':'__main__'}})
"""
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program], capture_output=True, text=True)
    assert (result.returncode == 0) == declared, result.stderr
    if not declared:
        assert 'undeclared mutable input/configuration' in result.stderr


def test_mutable_configuration_profiles_refuse():
    for kwargs in ({'state_files': ('/state/settings.json',)},
                   {'environment': (('OPENSSL_CONF', '/state/settings'),)},
                   {'environment': (('OPENSSL_CONF', '../../state/settings'),)}):
        with pytest.raises(Refused):
            ExecutionProfile('/opt/ccc/worker.py', **kwargs).value()


def test_image_snapshot_profile_ignores_only_container_specific_backing_path():
    container, _, profile, docker, _ = fixture()
    first = measured_profile_sha256(container, profile)
    container['Mounts'][0]['Source'] = '/root-daemon/other-container-id'
    assert measured_profile_sha256(container, profile) == first
    container['HostConfig']['Mounts'][0]['ImageOptions']['Subpath'] = 'unmeasured'
    assert measured_profile_sha256(container, profile) != first


@pytest.mark.parametrize('wrong', [None, 'image', 'bytes', 'mode', 'type'])
def test_actual_etc_snapshot_must_match_immutable_source(wrong):
    from ccc_server.execution_authority import RootDocker
    manifest = manifest_from_tar(archive({'etc/fixed.conf': b'fixed'}))
    artifact = Artifact('sha256:' + 'a' * 64, digest(manifest), manifest)
    data = archive({'etc/fixed.conf': b'changed' if wrong == 'bytes' else b'fixed'},
                   modes={'etc/fixed.conf': 0o644 if wrong == 'mode' else 0o755}).read()
    if wrong == 'type':
        data = archive({}, [('etc/fixed.conf', '/outside')]).read()
    container = {'Id': 'b' * 64, 'Image': artifact.image_id,
                 'HostConfig': ExecutionProfile('/opt/ccc/worker.py').config(artifact.image_id)['HostConfig']}

    class Docker(RootDocker):
        def request(self, method, endpoint, body=None, *, raw=False):
            if endpoint.startswith('/images/'):
                return {'Id': 'sha256:' + 'f' * 64 if wrong == 'image' else artifact.image_id}
            assert endpoint.endswith('/archive?path=/etc')
            return data

    if wrong is None:
        Docker().verify_runtime_etc(container, artifact)
    else:
        with pytest.raises(Refused):
            Docker().verify_runtime_etc(container, artifact)


def test_kernel_environment_defaults_are_bound_and_unknown_inputs_refuse():
    container, _, profile, _, _ = fixture()
    container['Config']['Hostname'] = 'generated-launch-name'
    env = ['PATH=/usr/bin', 'HOME=/root', 'HOSTNAME=generated-launch-name']
    first = measured_profile_sha256(container, profile, env)
    changed = ['PATH=/bin', 'HOME=/root', 'HOSTNAME=generated-launch-name']
    assert measured_profile_sha256(container, profile, changed) != first
    with pytest.raises(Refused, match='unknown implicit'):
        measured_profile_sha256(container, profile, [*env, 'UNMEASURED_LOADER=enabled'])
    with pytest.raises(Refused, match='mutable HOME'):
        measured_profile_sha256(container, profile, ['HOME=/state'])


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux pidfd admission boundary')
def test_real_pidfd_release_refuses_wrong_start_then_releases_exact_child():
    program = 'import os,signal;os.kill(os.getpid(),signal.SIGSTOP);print("released")'
    child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program], stdout=subprocess.PIPE, text=True)
    processes = LinuxProcesses()
    try:
        stat = Path(f'/proc/{child.pid}/stat').read_text()
        start = int(stat[stat.rfind(')') + 2:].split()[19])
        identity = ProcessIdentity('b' * 64, child.pid, start, 'test-boot', 'test-start')
        deadline = time.monotonic() + 3
        while not processes.stopped(identity) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert processes.stopped(identity)
        with pytest.raises(Refused, match='PID was reused'):
            processes.release(ProcessIdentity('b' * 64, child.pid, start + 1, 'test-boot', 'test-start'))
        assert processes.stopped(identity)
        processes.release(identity)
        output, _ = child.communicate(timeout=3)
        assert child.returncode == 0
        assert output.strip() == 'released'
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
