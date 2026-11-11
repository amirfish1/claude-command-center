"""Wheel CLI entry point regressions; no dashboard or external services."""
import runpy
from pathlib import Path

from ccc_server import launcher

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_cli_dispatches_oncall_help(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['ccc', 'oncall', '--help'])
    import pytest
    with pytest.raises(SystemExit) as stopped:
        launcher.cli_main()
    assert stopped.value.code == 0
    assert 'setup' in capsys.readouterr().out


def test_wheel_without_run_sh_uses_python_launcher(monkeypatch):
    client = runpy.run_path(str(ROOT / 'ccc'))
    monkeypatch.setattr('os.path.isfile', lambda name: False)
    seen = []
    monkeypatch.setattr(launcher, 'main', lambda argv: seen.append(argv) or 0)
    assert client['main'](['start']) == 0
    assert seen == [[]]
