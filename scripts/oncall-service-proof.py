#!/usr/bin/env python3
"""Exercise the native user service in an isolated systemd container only."""
import importlib.util
from pathlib import Path
import subprocess
import signal
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('proof', ROOT / 'scripts/oncall-server-proof.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
run = proof.run


def main():
    def interrupted(*_):
        raise RuntimeError('Service proof interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    name = 'ccc-service-proof-' + uuid.uuid4().hex[:12]
    image = name + ':local'
    cid = None
    try:
        with tempfile.TemporaryDirectory(prefix=name) as tmp:
            Path(tmp, 'Dockerfile').write_text('FROM ccc-oncall:local\nUSER root\nRUN apt-get update && apt-get install -y systemd dbus-user-session && rm -rf /var/lib/apt/lists/*\nENV container=docker\nENTRYPOINT []\nCMD ["/sbin/init"]\n')
            run('docker', 'build', '--force-rm', '-t', image, tmp, timeout=300)
        cid = run('docker', 'run', '-d', '--name', name, '--privileged', '--cgroupns=private',
                  '--tmpfs', '/run', '--tmpfs', '/run/lock', '--tmpfs', '/tmp',
                  '-e', 'CCC_TELEMETRY_DISABLED=1', image)
        def ex(*args):
            return run('docker', 'exec', cid, *args)
        proof.wait(lambda: subprocess.run(['docker', 'exec', cid, 'systemctl', 'is-active', 'systemd-logind'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0, 45, 'systemd boot')
        run('docker', 'cp', str(ROOT / 'ccc_server/service.py'), cid + ':/app/ccc_server/service.py')
        ex('chmod', '644', '/app/ccc_server/service.py')
        run('docker', 'cp', str(ROOT / 'scripts/oncall-supervisor.py'), cid + ':/app/scripts/oncall-supervisor.py')
        ex('chmod', '644', '/app/scripts/oncall-supervisor.py')
        ex('loginctl', 'enable-linger', 'ccc')
        ex('systemctl', 'start', 'user@1000.service')
        def user(*args):
            return run('docker', 'exec', '-u', '1000', '-e', 'XDG_RUNTIME_DIR=/run/user/1000',
                       '-e', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', cid, *args)
        user('systemctl', '--user', 'set-environment', 'CCC_TELEMETRY_DISABLED=1',
             'CCC_AUTO_UPDATE=0', 'WATCHTOWER_DELEGATE_URL=off', 'CCC_BG_HELPER=0')
        user('systemctl', '--user', 'unset-environment', 'WT_NO_AUTOSTART', 'WATCHTOWER_DAEMON_PID')
        user('ccc', 'service', 'install')
        assert 'active (running)' in user('ccc', 'service', 'status')
        proof.wait(lambda: subprocess.run(['docker', 'exec', '-u', '1000', cid, 'python3',
                   '/app/scripts/oncall-supervisor.py', '--health'], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL).returncode == 0, 90, 'native service health')
        user('test', '-f', '/state/.watchtower/daemon.pid')
        user('ccc', 'service', 'uninstall')
        assert ex('test', '!', '-f', '/state/.config/systemd/user/ccc-oncall.service') == ''
        print('Check | Result | Measurement / reason\nLinux user service | PASS | install, status, health, uninstall')
    finally:
        if cid:
            subprocess.run(['docker', 'rm', '-fv', cid], capture_output=True, timeout=30)
        subprocess.run(['docker', 'image', 'rm', image], capture_output=True, timeout=30)


if __name__ == '__main__':
    main()
