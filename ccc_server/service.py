"""Native On-call service installation. No shell interpolation or root service."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

NAME = 'ccc-oncall.service'
ROOT = Path(__file__).resolve().parents[1]


def quote(value):
    value = str(value)
    if any(c in value for c in '\n\r\0'):
        raise ValueError('Service paths must not contain control characters')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit(root, python, home, executable_path):
    return '\n'.join([
        '[Unit]', 'Description=CCC On-call', 'After=network-online.target', '',
        '[Service]', 'Type=simple',
        'ExecStart=:' + quote(python) + ' ' + quote(Path(root) / 'scripts/oncall-supervisor.py'),
        'WorkingDirectory=' + str(home).replace('%', '%%'),
        'Environment=' + quote('HOME=' + str(home)),
        'Environment=' + quote('PATH=' + executable_path),
        'Restart=on-failure', 'RestartSec=5', 'KillMode=control-group',
        'TimeoutStopSec=30', 'UMask=0077', 'StandardOutput=journal', 'StandardError=journal', '',
        '[Install]', 'WantedBy=default.target', '',
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ccc service')
    parser.add_argument('action', choices=['install', 'status', 'uninstall'])
    args = parser.parse_args(argv)
    if sys.platform == 'darwin':
        if args.action != 'install':
            parser.error('macOS supports printing a plist only; manage it with launchctl')
        print(plistlib.dumps({
            'Label': 'com.github.claude-command-center.oncall',
            'ProgramArguments': [sys.executable, str(ROOT / 'scripts/oncall-supervisor.py')],
            'EnvironmentVariables': {'HOME': str(Path.home()), 'PATH': os.environ['PATH']},
            'RunAtLoad': True, 'KeepAlive': True,
        }).decode())
        return 0
    if sys.platform != 'linux':
        parser.error('Only Linux installation and macOS plist output are supported')
    target = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'systemd/user' / NAME
    def ctl(*words):
        subprocess.run(['systemctl', '--user', *words], check=True)
    if args.action == 'status':
        return subprocess.run(['systemctl', '--user', 'status', NAME]).returncode
    if args.action == 'install':
        content = unit(ROOT, sys.executable, Path.home(), os.environ['PATH'])
        if target.exists() and target.read_text() != content:
            parser.error(f'{target} already exists with different contents; uninstall first')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        ctl('daemon-reload')
        ctl('enable', '--now', NAME)
        print('Installed. For startup without login, an administrator must enable linger for this user.')
    else:
        if target.exists():
            ctl('disable', '--now', NAME)
            target.unlink()
            ctl('daemon-reload')
    return 0
