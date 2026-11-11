"""Every source scene uses production intake with each ticket backend."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from ccc_server.oncall import demo
from ccc_server.oncall.demo_wt import installed


@pytest.mark.parametrize('scene', ['customer', 'ci', 'monitoring'])
@pytest.mark.parametrize('backend', ['fixture', 'watchtower'])
def test_scene(tmp_path, monkeypatch, scene, backend):
    if backend == 'watchtower' and not installed():
        pytest.skip('WatchTower is absent')
    monkeypatch.setattr(demo, 'installed', lambda: backend == 'watchtower')
    lines = []
    args = argparse.Namespace(scene=scene, json=True, terminal=True, agent=False)
    start = time.monotonic()
    result = demo.walkthrough(tmp_path, args, lines.append, lines)
    assert time.monotonic() - start < 2
    assert result['backend'] == backend and result['scene'] == scene
    assert result['test_before'] != 0 and result['test_after'] == 0
    if scene == 'ci':
        assert any('/logs' in url for url in result['source_requests'])
        assert any('same build is green' in line for line in lines)
    elif scene == 'monitoring':
        assert '/health' in result['source_requests']
        assert any('log is clear' in line for line in lines)
        assert 'ERROR' not in (tmp_path / 'project/application.log').read_text()


@pytest.mark.parametrize('scene', ['ci', 'monitoring'])
def test_source_scene_cli_budget(tmp_path, scene):
    root = Path(__file__).resolve().parents[1]
    start = time.monotonic()
    proc = subprocess.run([sys.executable, str(root / 'ccc'), 'oncall', 'demo',
                           '--scene', scene, '--json'], cwd=tmp_path,
                          env=dict(os.environ, HOME=str(tmp_path), TMPDIR=str(tmp_path)),
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)['scene'] == scene
    assert time.monotonic() - start < 2
    assert not list(tmp_path.iterdir())
