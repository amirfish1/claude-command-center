#!/usr/bin/env python3
"""Validate and install without Claude credentials or paid sessions (Linux)."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('session_proof', REPO / 'scripts/oncall-session-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def main():
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ccc-plugin-proof-') as tmp:
        root = Path(tmp)
        bins = root / 'bin'
        bins.mkdir()
        for name in ('python3', 'git', 'bash', 'sh', 'env', 'ps', 'cat', 'uname'):
            found = shutil.which(name)
            if found:
                (bins / name).symlink_to(found)
        (bins / 'ccc').symlink_to(REPO / 'ccc')
        env = {'HOME': tmp, 'PATH': str(bins), 'CLAUDE_CONFIG_DIR': str(root / 'claude'),
               'CODEX_HOME': str(root / 'codex'), 'CCC_STATE_DIR': str(root / 'state'),
               'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
               'CCC_EPHEMERAL': '1', 'CCC_ONCALL_EPHEMERAL': '1',
               'CCC_TELEMETRY_DISABLED': '1', 'CCC_AUTO_UPDATE': '0',
               'CCC_BG_HELPER': '0', 'CCC_CONV_READER': '0', 'CCC_BOOT_WARM_DELAY_S': '86400',
               'WATCHTOWER_STORE': str(root / 'queues.json'), 'WT_NO_AUTOSTART': '1',
               'WT_NO_SELF_UPDATE': '1', 'WATCHTOWER_DELEGATE_URL': 'off',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        def run(*args):
            result = subprocess.run(args, env=env, cwd=root, text=True,
                                    capture_output=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            print(result.stdout.strip())
            return result.stdout.strip()
        try:
            claude = shutil.which('claude')
            if not claude:
                raise RuntimeError('Claude CLI is required for manifest validation')
            run(claude, 'plugin', 'validate', str(REPO / 'plugins/oncall'))
            run(claude, 'plugin', 'validate', str(REPO / '.claude-plugin/marketplace.json'))
            run(claude, 'plugin', 'install', 'oncall', '--marketplace', str(REPO), '--json')
            listed = run(claude, 'plugin', 'list', '--json')
            assert 'oncall@claude-command-center' in listed
            run(sys.executable, str(REPO / 'ccc'), 'oncall', 'install-codex')
            assert not (root / 'codex/config.toml').exists()
            hook_start = time.monotonic()
            result = subprocess.run([sys.executable, str(REPO / 'plugins/oncall/scripts/hook.py'),
                                     'SessionStart'], input=json.dumps({'session_id': 'proof-session-1234'}),
                                    env=env, text=True, capture_output=True, timeout=2)
            assert result.returncode == 0 and not result.stderr
            assert time.monotonic() - hook_start < 2
            url = run(sys.executable, str(REPO / 'ccc'), 'oncall', 'agent-service')
            record = json.loads((root / 'state/oncall-agent-process.json').read_text())
            # This pid was created by this proof, inside its unique HOME.
            os.kill(record['pid'], signal.SIGSTOP)
            try:
                again = run(sys.executable, str(REPO / 'ccc'), 'oncall', 'agent-service')
                assert url == again and url.startswith('http://127.0.0.1:')
            finally:
                os.kill(record['pid'], signal.SIGCONT)
            os.kill(record['pid'], signal.SIGKILL)
            # Wait on process death via procfs only in this bounded observer.
            def gone():
                try:
                    return (Path('/proc') / str(record['pid']) / 'stat').read_text().split(') ', 1)[1][0] == 'Z'
                except OSError:
                    return True
            proof.wait_for(gone, 5, 'isolated server exit')
            restarted = run(sys.executable, str(REPO / 'ccc'), 'oncall', 'agent-service')
            new_record = json.loads((root / 'state/oncall-agent-process.json').read_text())
            assert new_record['pid'] != record['pid'] and restarted.startswith('http://127.0.0.1:')
            print('PASS: isolated install, busy-service reuse and restart after crash; %.1fs' % (time.monotonic() - started))
        finally:
            proof.cleanup(root, [])


if __name__ == '__main__':
    main()
