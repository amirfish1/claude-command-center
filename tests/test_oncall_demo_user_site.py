"""The demo points HOME at a temp dir; a pip --user WatchTower must still run."""
import os
import sys
from types import SimpleNamespace

from ccc_server.oncall import demo_wt


def test_user_site_install_is_found_from_the_real_home(tmp_path, monkeypatch):
    real = tmp_path / 'real'
    site = real / '.local' / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    (site / 'watchtower').mkdir(parents=True)
    (site / 'watchtower' / '__init__.py').write_text('')
    fake_home = tmp_path / 'demo-home'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.delenv('PYTHONUSERBASE', raising=False)
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
