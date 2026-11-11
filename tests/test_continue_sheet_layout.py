"""The Continue in a new session sheet keeps its button on screen in a real browser."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_continue_sheet_button_is_reachable_on_every_screen_size():
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
        ['node', str(root / 'tests/continue-sheet-layout.browser.cjs')],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: 30 continue sheet checks' in result.stdout
