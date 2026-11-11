"""The capture helper must never navigate to a nonlocal ntfy server."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('base,topic', [
    ('https://example.test', 'session-proof'),
    ('http://example.test:8080', 'session-proof'),
    ('http://127.0.0.1:8080', '../other'),
])
def test_capture_rejects_nonlocal_or_invalid_topic(base, topic):
    result = subprocess.run(
        ['node', str(ROOT / 'scripts/oncall-capture.cjs'), base, topic],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert 'Expected isolated loopback ntfy' in result.stderr


@pytest.mark.parametrize('text,blocked', [
    ('Should I use the blue option?', False),
    ('http://127.0.0.1:1234/session', True),
    ('/dev/shm/example/project', True),
    ('/home/example/project', True),
    ('person@example.test', True),
])
def test_screenshot_guard(text, blocked):
    script = "const check=require('./scripts/oncall-capture-safety.cjs'); console.log(check(process.argv[1]))"
    result = subprocess.check_output(['node', '-e', script, text], cwd=ROOT, text=True, timeout=15)
    assert result.strip() == str(blocked).lower()


def test_discovers_os_assigned_loopback_listener():
    script = '''
import importlib.util, os, socket
spec = importlib.util.spec_from_file_location('proof', 'scripts/oncall-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
with socket.socket() as server:
    server.bind(('127.0.0.1', 0))
    server.listen()
    assert proof.listener_port(os.getpid()) == server.getsockname()[1]
'''
    subprocess.run(['python3', '-c', script], cwd=ROOT, check=True, timeout=15)
