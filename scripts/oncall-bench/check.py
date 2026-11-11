#!/usr/bin/env python3
"""Run the On-call and performance suite with private, disposable user state."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    repo = Path(__file__).resolve().parents[2]
    # Resolve before changing HOME/cwd so user-site pytest and WatchTower stay visible.
    pythonpath = os.pathsep.join(str(Path(entry).resolve()) for entry in sys.path)
    tests = [str(test.relative_to(repo)) for test in sorted(repo.glob('tests/test_oncall_*.py'))]
    tests.append('tests/test_perf_budget.py')
    with TemporaryDirectory(prefix='oncall-bench-check-') as tmp:
        root = Path(tmp)
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
        env.update(
            HOME=tmp, XDG_CONFIG_HOME=str(root / 'config'),
            XDG_DATA_HOME=str(root / 'data'), CCC_STATE_DIR=str(root / 'state'),
            WATCHTOWER_STORE=str(root / 'queues.json'), PYTHONPATH=pythonpath,
            WT_NO_AUTOSTART='1', WT_NO_SELF_UPDATE='1', WATCHTOWER_DELEGATE_URL='off',
            CCC_TELEMETRY_DISABLED='1', GIT_CONFIG_NOSYSTEM='1',
            GIT_CONFIG_GLOBAL=os.devnull,
        )
        try:
            return subprocess.run(
                [sys.executable, '-m', 'pytest', *tests], cwd=repo, env=env, timeout=600,
            ).returncode
        except subprocess.TimeoutExpired:
            print('On-call checks timed out after 600 seconds.', file=sys.stderr)
            return 124


if __name__ == '__main__':
    sys.exit(main())
