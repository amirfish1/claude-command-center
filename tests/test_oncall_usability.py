"""On-call focus, feedback, setup labels and decision history in a real browser."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_oncall_browser_usability():
    root = Path(__file__).resolve().parents[1]
    if not shutil.which('node'):
        pytest.skip('Node is absent')
    probe = subprocess.run(
        ['node', '-e', 'require(process.argv[1])', str(root / 'require-puppeteer.js')],
        capture_output=True, text=True, timeout=15,
    )
    if probe.returncode:
        pytest.skip('Puppeteer is absent')
    result = subprocess.run(
        ['node', str(root / 'tests/oncall-usability-browser.cjs')],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: 72 browser checks' in result.stdout
