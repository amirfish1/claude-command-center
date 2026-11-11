"""Reject stale benchmark output before provenance or paid worker startup."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


RUNNER = Path(__file__).resolve().parents[1] / 'scripts/oncall-bench/run.py'


@pytest.mark.parametrize('filename', [
    'results.json', '1-sum.json', '1-sum.json.evidence.json',
])
def test_cli_rejects_stale_output(tmp_path, filename):
    output = tmp_path / 'output'
    output.mkdir()
    stale = output / filename
    content = b'{"pass": true, "reason": "previous run"}\n'
    stale.write_bytes(content)
    table = output / 'table.log'
    table.write_text('supervisor output\n')
    home = tmp_path / 'home'
    home.mkdir()
    result = subprocess.run(
        [sys.executable, str(RUNNER), '--with-agent', '--quick', '--output', str(output)],
        env={**os.environ, 'PATH': '', 'HOME': str(home)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert 'choose a fresh output directory' in result.stderr
    assert 'Traceback' not in result.stderr
    assert result.stdout == ''
    assert stale.read_bytes() == content
    assert table.read_text() == 'supervisor output\n'
    assert {p.name for p in output.iterdir()} == {filename, 'table.log'}
    assert not list(home.iterdir())


@pytest.mark.parametrize('existing', ['missing', 'empty', 'table.log'])
def test_fresh_output_reaches_provenance(tmp_path, monkeypatch, existing):
    # Stop at provenance so accepted directories never launch external tools.
    monkeypatch.syspath_prepend(str(RUNNER.parent))
    spec = importlib.util.spec_from_file_location('oncall_bench_run', RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    output = tmp_path / 'output'
    if existing != 'missing':
        output.mkdir()
    if existing == 'table.log':
        (output / 'table.log').write_text('supervisor output\n')
    class ReachedProvenance(Exception):
        pass
    def stop():
        raise ReachedProvenance
    monkeypatch.setattr(runner, 'capture_provenance', stop)
    monkeypatch.setattr(sys, 'argv', [str(RUNNER), '--with-agent', '--quick', '--output', str(output)])
    with pytest.raises(ReachedProvenance):
        runner.main()
