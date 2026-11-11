"""The isolated close must not invoke installed-checkout housekeeping."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from ccc_server.oncall.demo import isolated_env


def test_real_close_verifies_proof_without_cli_housekeeping(tmp_path):
    spec = importlib.util.find_spec('watchtower')
    if not spec or not spec.origin:
        pytest.skip('WatchTower package is absent')
    project = tmp_path / 'project'
    project.mkdir()
    env = isolated_env(tmp_path)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=project, env=env, text=True).strip()
    git('init', '-q')
    git('config', 'user.name', 'Demo')
    git('config', 'user.email', 'demo@example.test')
    (project / 'sample.txt').write_text('fixed\n')
    git('add', 'sample.txt')
    git('commit', '-qm', 'Sample fix')
    commit = git('rev-parse', 'HEAD')
    repo = Path(__file__).resolve().parents[1]
    script = f'''
import sys
sys.path[:0] = { [str(repo), str(Path(spec.origin).parent.parent)]!r}
from watchtower import cli, deploy
from ccc_server.oncall.demo_wt import bridge
def forbidden(*args, **kwargs):
    raise AssertionError('Installed-checkout housekeeping must never run')
cli.cmd_close = forbidden
deploy.sync = forbidden
bridge()
'''
    commands = [
        ['add', '-q', 'DEMO', '--title', 'Sample bug', '--text', 'Sample', '--no-dispatch'],
        ['claim', '-q', 'DEMO', 'DEMO-1', '--worker', 'demo-worker'],
        ['close', 'DEMO-1', '--worker', 'demo-worker', '--commit', '0' * 40, '--summary', 'Fixed'],
        ['find', 'DEMO-1', '--json'],
        ['close', 'DEMO-1', '--worker', 'demo-worker', '--commit', commit, '--summary', 'Fixed'],
        ['find', 'DEMO-1', '--json'],
    ]
    proc = subprocess.run([sys.executable, '-S', '-c', script], cwd=project, env=env,
                          input='\n'.join(map(json.dumps, commands)) + '\n',
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert replies[2]['code'] == 1
    assert json.loads(replies[3]['out'])['status'] != 'closed'
    assert replies[4]['code'] == 0, replies[4]
    ticket = json.loads(replies[5]['out'])
    assert ticket['status'] == 'closed'
    assert ticket['resolution']['commit'] == commit
