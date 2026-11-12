"""The demo points HOME at a temp dir; a pip --user WatchTower must still run."""
import os
import subprocess
import sys
from types import SimpleNamespace

from ccc_server.oncall import demo_wt


def test_user_site_install_is_found_from_the_real_home(tmp_path, monkeypatch):
    real = tmp_path / 'real'
    base = tmp_path / 'userbase'
    # Ask Python where its user site is for this base, on any platform.
    site = subprocess.check_output(
        [sys.executable, '-c', 'import site; print(site.getusersitepackages())'],
        env=dict(os.environ, PYTHONUSERBASE=str(base)), text=True).strip()
    os.makedirs(os.path.join(site, 'watchtower'))
    open(os.path.join(site, 'watchtower', '__init__.py'), 'w').close()
    real.mkdir()
    fake_home = tmp_path / 'demo-home'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.setenv('PYTHONUSERBASE', str(base))
    monkeypatch.delenv('PYTHONNOUSERSITE', raising=False)
    monkeypatch.setattr(demo_wt, 'interpreter', lambda: sys.executable)
    import pwd
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(real)))
    argv = demo_wt._wt_python_argv()
    assert argv[:2] == [sys.executable, '-S']
    assert os.path.realpath(argv[-1]) == os.path.realpath(site)


def test_falls_back_to_plain_interpreter_when_package_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(demo_wt, 'interpreter', lambda: sys.executable)
    import pwd
    monkeypatch.setattr(pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    monkeypatch.setattr(demo_wt.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    argv = demo_wt._wt_python_argv()
    assert argv[0] == sys.executable and '-S' not in argv
