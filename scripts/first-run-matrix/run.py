#!/usr/bin/env python3
"""Measure documented first-run commands in disposable, network-isolated images."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ('ubuntu:24.04', 'ubuntu:22.04', 'debian:12', 'fedora:latest')
ROUTES = {
    'curl': 'set -o pipefail; curl -fsSL file:///fixture/source/scripts/install.sh | CCC_FROM=readme bash',
    'uvx': 'cd /work/source && uv build --wheel && ccc_wheel=$(ls -t dist/claude_command_center-*.whl | head -n 1) && uvx --from "$ccc_wheel" claude-command-center',
    'pipx': 'cd /work/source && uv build --wheel && ccc_wheel=$(ls -t dist/claude_command_center-*.whl | head -n 1) && pipx run --spec "$ccc_wheel" claude-command-center',
    'brew': 'brew install amirfish1/tap/ccc',
}
PROBE = 'for p in bash curl git python3 uv uvx pipx brew; do if command -v "$p" >/dev/null 2>&1; then printf "%s=present\\n" "$p"; else printf "%s=missing\\n" "$p"; fi; done'


def snapshot(destination):
    """Copy tracked working-tree files, including uncommitted changes, into a local git fixture."""
    source = destination / 'source'
    source.mkdir()
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
    copied = []
    for name in names:
        if not name:
            continue
        relative = os.fsdecode(name)
        src, dst = ROOT / relative, source / relative
        if not src.exists() and not src.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        elif src.is_file():
            shutil.copy2(src, dst)
        else:
            continue
        copied.append(relative)
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '-f', '--', *copied], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'Local source fixture'], check=True)
    return source


def execute(image, route, fixture, timeout, *, label='clean', command=None):
    name = 'ccc-first-run-' + uuid.uuid4().hex
    command = command or ROUTES[route]
    script = 'set -e; mkdir -p /work/source /tmp/ccc-home && cp -r /fixture/source/. /work/source/ && ' + PROBE + '; printf "\\n--- documented command ---\\n"; ' + command
    args = ['docker', 'run', '--name', name, '--network', 'none', '--mount',
            f'type=bind,src={fixture},dst=/fixture,readonly',
            '-e', 'HOME=/tmp/ccc-home', '-e', 'CCC_STATE_DIR=/tmp/ccc-home/state',
            '-e', 'WATCHTOWER_STORE=/tmp/ccc-home/watchtower.json', '-e', 'WT_NO_AUTOSTART=1',
            '-e', 'UV_OFFLINE=1', '-e', 'UV_PYTHON_DOWNLOADS=never', '-e', 'PIP_NO_INDEX=1',
            '-e', 'WT_NO_SELF_UPDATE=1', '-e', 'WATCHTOWER_DELEGATE_URL=off',
            '-e', 'CCC_TELEMETRY_DISABLED=1', '-e', 'CCC_AUTO_UPDATE=0',
            '-e', 'CCC_EPHEMERAL=1', '-e', 'CCC_REPO_URL=file:///work/source',
            '-e', 'GIT_CONFIG_NOSYSTEM=1', '-e', 'GIT_CONFIG_GLOBAL=/dev/null',
            image, 'bash', '-c', script]
    start = time.monotonic()
    try:
        try:
            proc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, timeout=timeout)
            code, output, timed_out = proc.returncode, proc.stdout, False
        except subprocess.TimeoutExpired as exc:
            code, output, timed_out = None, (exc.stdout or b''), True
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False, timeout=30)
    return {'image': image, 'environment': label, 'route': route, 'command': command, 'exit_code': code,
            'timed_out': timed_out, 'elapsed_seconds': round(time.monotonic() - start, 3),
            'output': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='run only ubuntu:24.04 curl')
    parser.add_argument('--image', choices=IMAGES, help='restrict to one image')
    parser.add_argument('--route', choices=ROUTES, help='restrict to one route')
    parser.add_argument('--wheel', type=Path, help='use an already-built wheel for uvx/pipx')
    parser.add_argument('--python-tools', action='store_true', help='in remediation also install pipx and uv')
    parser.add_argument('--timeout', type=int, default=90, help='per-route timeout in seconds')
    parser.add_argument('--remediate', action='store_true', help='also run with Linux prerequisites installed (package downloads)')
    parser.add_argument('--output', type=Path, default=Path('scripts/first-run-matrix/results.json'))
    args = parser.parse_args()
    if args.python_tools and not args.remediate:
        parser.error('--python-tools requires --remediate')
    if args.wheel and not args.wheel.is_file():
        parser.error('--wheel must name an existing wheel')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    results = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'network': 'none', 'source': 'tracked working-tree snapshot', 'results': []}
    images = (args.image,) if args.image else (IMAGES[:1] if args.quick else IMAGES)
    routes = (args.route,) if args.route else (('curl',) if args.quick else tuple(ROUTES))
    commands = dict(ROUTES)
    with tempfile.TemporaryDirectory(prefix='ccc-first-run-') as tmp:
        fixture = Path(tmp)
        source = snapshot(fixture)
        results['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        results['installer_sha256'] = hashlib.sha256((source / 'scripts/install.sh').read_bytes()).hexdigest()
        if args.wheel:
            wheels = fixture / 'wheels'
            wheels.mkdir()
            shutil.copy2(args.wheel, wheels / args.wheel.name)
            wheel = shlex.quote('/fixture/wheels/' + args.wheel.name)
            commands['uvx'] = f'uvx --from {wheel} claude-command-center'
            commands['pipx'] = f'pipx run --spec {wheel} claude-command-center'
            results['wheel'] = {'name': args.wheel.name, 'sha256': hashlib.sha256(args.wheel.read_bytes()).hexdigest()}
        for image in images:
            for route in routes:
                print(f'{image} {route}', flush=True)
                result = execute(image, route, fixture, args.timeout, command=commands[route])
                results['results'].append(result)
                print(f"  exit={result['exit_code']} timeout={result['timed_out']} elapsed={result['elapsed_seconds']}s", flush=True)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(results, indent=2) + '\n')
        if args.remediate:
            for image in images:
                tag = 'ccc-first-run-remediated:' + uuid.uuid4().hex
                manager = ('dnf -y install git python3 curl ca-certificates && dnf clean all'
                           if image.startswith('fedora:') else
                           'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 curl ca-certificates && rm -rf /var/lib/apt/lists/*')
                if args.python_tools:
                    manager += (' && dnf -y install python3-pip pipx && python3 -m pip install --break-system-packages uv'
                                if image.startswith('fedora:') else
                                ' && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip python3-venv pipx && python3 -m pip install --break-system-packages uv')
                dockerfile = f'FROM {image}\nRUN {manager}\n'
                print(f'{image} remediation: installing prerequisites', flush=True)
                started = time.monotonic()
                try:
                    try:
                        build = subprocess.run(['docker', 'build', '--tag', tag, '-'], input=dockerfile,
                                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=480)
                        code, output = build.returncode, build.stdout
                    except subprocess.TimeoutExpired as exc:
                        code, output = None, exc.stdout or b''
                        if isinstance(output, bytes):
                            output = output.decode(errors='replace')
                    results.setdefault('remediation', []).append({'image': image, 'command': manager,
                        'exit_code': code, 'elapsed_seconds': round(time.monotonic() - started, 3),
                        'output': '' if code == 0 else output[-6000:]})
                    if code == 0:
                        for route in routes:
                            print(f'{image} remediated {route}', flush=True)
                            item = execute(tag, route, fixture, args.timeout, label='remediated', command=commands[route])
                            item['image'] = image
                            results['results'].append(item)
                            print(f"  exit={item['exit_code']} timeout={item['timed_out']} elapsed={item['elapsed_seconds']}s", flush=True)
                finally:
                    subprocess.run(['docker', 'image', 'rm', '-f', tag], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=False, timeout=30)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(results, indent=2) + '\n')
    return 0 if (all(r['exit_code'] == 0 for r in results['results']) and
                 all(r['exit_code'] == 0 for r in results.get('remediation', []))) else 1


if __name__ == '__main__':
    raise SystemExit(main())
