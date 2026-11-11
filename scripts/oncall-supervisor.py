#!/usr/bin/env python3
"""Foreground process group for a container or a native user service."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--health', action='store_true')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8090')))
    args = parser.parse_args()
    state = Path(os.environ.get('CCC_STATE_DIR', str(Path.home() / '.claude/command-center')))
    pids = state / 'oncall-service-pids.json'
    if args.health:
        for pid in json.loads(pids.read_text()):
            os.kill(pid, 0)
        with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/health', timeout=5) as response:
            return 0 if response.status == 200 else 1
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(WT_NO_AUTOSTART='1', WT_NO_SELF_UPDATE='1', CCC_AUTO_UPDATE='0', PYTHONUNBUFFERED='1')
    runtime = tempfile.TemporaryDirectory(prefix='ccc-oncall-runtime-')
    # Containers opt out of CLI autostart globally. Native CLI clients must
    # still discover the supervised watcher through its usual PID file.
    if os.environ.get('WT_NO_AUTOSTART') == '1':
        env['WATCHTOWER_DAEMON_PID'] = str(Path(runtime.name) / 'watcher.pid')
    children = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        commands = [
            ['wt', 'start', '--foreground', '--auto-spawn', '--host', '127.0.0.1', '--port', '0'],
            [sys.executable, str(ROOT / 'ccc_worker.py')],
            [sys.executable, str(ROOT / 'server.py'), '--port', str(args.port)],
        ]
        for command in commands:
            children.append(subprocess.Popen(command, env=env))
        pids.write_text(json.dumps([child.pid for child in children]))
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        return 1  # Let the container/service manager restart the whole group.
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        pids.unlink(missing_ok=True)
        runtime.cleanup()


if __name__ == '__main__':
    sys.exit(main())
