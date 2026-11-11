"""A proof timeout must terminate its child shell processes too."""
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def test_timeout_reaps_proof_process_group(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'scripts/oncall-security-sandbox-proof.py'
    tree = ast.parse(source.read_text())
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == 'run')
    scope = dict(repo=tmp_path, env=dict(os.environ), subprocess=subprocess,
                 os=os, signal=signal)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), scope)
    marker = tmp_path / 'child.pid'
    program = ('import subprocess,sys,time,pathlib; '
               'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
               'pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            scope['run']([sys.executable, '-c', program, str(marker)], timeout=1)
        assert marker.exists(), 'fixture child did not start'
        stat = Path('/proc') / marker.read_text() / 'stat'
        assert not stat.exists() or stat.read_text().split(') ', 1)[1].split()[0] in ('Z', 'X')
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
