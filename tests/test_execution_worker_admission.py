"""Measured generated fragments and actual ordinary worker admission.

These subprocesses are Python-only; no agent, model, native daemon or restart.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ccc_server.execution_artifact import ExecutionProfile, Refused
from ccc_server import execution_guard

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'ccc_server/execution_generated_sources.json'


def test_catalog_profile_requires_measured_image_source():
    profile = ExecutionProfile('/opt/ccc/ccc_worker.py', generated_sources=(
        ('<catalog-v1>', '/opt/ccc/ccc_server/execution_generated_sources.json'),))
    assert profile.value()['generated_sources']
    with pytest.raises(Refused):
        ExecutionProfile('/opt/ccc/ccc_worker.py', generated_sources=(
            ('<catalog-v1>', '/state/runtime.json'),)).value()


@pytest.mark.parametrize('failure', ['digest', 'filename', 'duplicate', 'schema', 'json-keys'])
def test_generated_catalog_refuses_unknown_or_changed_source(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(execution_guard, 'EXCLUDED', ('/state', '/proc', '/sys'))
    catalog = json.loads(CATALOG.read_text())
    if failure == 'digest':
        catalog['sources'][0]['source'] += 'changed'
    elif failure == 'filename':
        catalog['sources'][0]['filename'] = '/state/plugin.py'
    elif failure == 'duplicate':
        catalog['sources'].append(copy.deepcopy(catalog['sources'][0]))
    elif failure == 'schema':
        catalog['extra'] = True
    target = tmp_path / 'catalog.json'
    text = json.dumps(catalog)
    if failure == 'json-keys':
        text = text[:-1] + ',"format":"ccc-generated-sources-v1"}'
    target.write_text(text)
    with pytest.raises(RuntimeError, match='refused'):
        execution_guard.measured_generated_sources((('<catalog-v1>', str(target)),))


def test_actual_worker_help_uses_only_exact_measured_fragments():
    guard = ROOT / 'ccc_server/execution_guard.py'
    worker = ROOT / 'ccc_worker.py'
    program = f'''
import importlib.util,sys
sys.path.insert(0,{str(ROOT)!r})
spec=importlib.util.spec_from_file_location('guard',{str(guard)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.EXCLUDED=('/state','/proc','/sys')
generated=module.measured_generated_sources((('<catalog-v1>',{str(CATALOG)!r}),))
guard=module.SourceGuard((),generated_sources=generated)
guard.install()
sys.argv=[{str(worker)!r},'--help']
exec(guard.compile_file({str(worker)!r}),{{'__name__':'__main__','__file__':{str(worker)!r}}})
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'CCC persistent execution worker' in result.stdout


def test_disabled_memory_release_does_not_import_a_native_loader():
    program = f'''
import sys,os
sys.path.insert(0,{str(ROOT)!r})
os.environ['CCC_MEMORY_RELEASE']='0'
from ccc_server import memory_release
memory_release.start()
assert 'ctypes' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_worker_control_profiles_bind_namespace_and_exact_state():
    from ccc_server.execution_authority import _profile
    from ccc_server.execution_worker import worker_profile
    worker = worker_profile('/private/data', '1001:1001')
    control = worker_profile('/private/data', '1001:1001', role='control')
    assert worker.sha256 != control.sha256
    assert control.data_mounts == (('/private/data', '/state', True),)
    assert worker.commands == control.commands == ()
    assert '/state/worker.token' in worker.state_files
    assert '/state/config.json' not in worker.state_files
    for profile in (worker, control):
        assert _profile(profile.value()).value() == profile.value()
        host = profile.config('sha256:' + 'a' * 64)['HostConfig']
        assert host['PidMode'] == host['CgroupnsMode'] == 'host'
        assert host['CapDrop'] == ['ALL']
        assert host['ReadonlyRootfs'] and host['NetworkMode'] == 'none'
    account = worker_profile('/private/data', '1001:1001', account_key='a' * 20)
    assert dict(account.environment)['CCC_CODEX_ACCOUNT_KEY'] == 'a' * 20
    assert account.sha256 != worker.sha256
    with pytest.raises(Refused):
        worker_profile('/private/data', '1001:1001', account_key='../arbitrary')
    private = worker_profile('/private/data', '1001:1001', identity_namespace='private')
    assert private.sha256 != worker.sha256
    assert private.config('sha256:' + 'a' * 64)['HostConfig']['PidMode'] == ''
    with pytest.raises(Refused):
        worker_profile('/private/data', '1001:1001', identity_namespace='container:unknown')
    changed = worker.value()
    changed['cgroup_namespace'] = 'private'
    with pytest.raises(Refused):
        _profile(changed)


def test_dependency_bytes_come_from_exact_committed_tree(tmp_path):
    import io
    import tarfile
    from ccc_server.execution_artifact import committed_dependency_tar
    repo = tmp_path / 'dependency'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q')
    package = repo / 'example_dependency'
    package.mkdir()
    (package / '__init__.py').write_text('VALUE = 1\n')
    (repo / 'private-note').write_text('excluded repository content')
    git('add', 'example_dependency/__init__.py', 'private-note')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
    revision = git('rev-parse', 'HEAD')
    (package / '__init__.py').write_text('VALUE = 2\n')
    (package / 'untracked.py').write_text('not measured')
    raw = committed_dependency_tar('example_dependency', repo, revision)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        assert set(archive.getnames()) == {'example_dependency', 'example_dependency/__init__.py'}
        assert archive.extractfile('example_dependency/__init__.py').read() == b'VALUE = 1\n'
    for name, pin in [('json', revision), ('ccc_server', revision), ('../escape', revision),
                      ('example_dependency', 'HEAD'), ('missing', revision)]:
        with pytest.raises(Refused):
            committed_dependency_tar(name, repo, pin)


def test_optional_display_revision_probe_tolerates_execution_refusal(monkeypatch):
    import ast
    tree = ast.parse((ROOT / 'server.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_compute_server_code_rev')
    def refused(*args, **kwargs):
        raise RuntimeError('execution refused: unmeasured subprocess argv')
    monkeypatch.setattr(subprocess, 'check_output', refused)
    scope = {'subprocess': subprocess, '_install_dir': lambda: ROOT}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<test-display-probe>', 'exec'), scope)
    assert scope['_compute_server_code_rev']() == ''


def test_shared_paths_use_the_explicit_ledger_state_root(tmp_path):
    import os
    program = f'''
import sys
sys.path.insert(0,{str(ROOT)!r})
from ccc_server.paths import COMMAND_CENTER_STATE_DIR, CODEX_APP_SERVER_STATE_FILE
from control_plane import state_dir
assert COMMAND_CENTER_STATE_DIR == state_dir()
assert CODEX_APP_SERVER_STATE_FILE.parent == state_dir()
'''
    environment = dict(os.environ, CCC_STATE_DIR=str(tmp_path / 'state'))
    environment.pop('CCC_CODEX_RUNTIME_DIR', None)
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                            env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux seccomp signal isolation')
def test_kernel_visible_peer_is_not_signal_authority():
    victim = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time;time.sleep(20)'])
    try:
        program = f'''
import sys,os,signal
sys.path.insert(0,{str(ROOT)!r})
from ccc_server.execution_guard import install_kernel_policy
install_kernel_policy(restrict_signals=True)
os.kill({victim.pid},0)
for target,number in [({victim.pid},signal.SIGTERM),(os.getpid(),signal.SIGCONT)]:
 try: os.kill(target,number)
 except PermissionError: pass
 else: raise AssertionError('signal authority escaped')
fd=os.pidfd_open({victim.pid})
try:
 try: signal.pidfd_send_signal(fd,signal.SIGTERM)
 except PermissionError: pass
 else: raise AssertionError('pidfd signal authority escaped')
finally: os.close(fd)
assert 'ctypes' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert victim.poll() is None
    finally:
        victim.terminate()
        victim.wait(timeout=5)
