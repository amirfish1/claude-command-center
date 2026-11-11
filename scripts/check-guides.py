#!/usr/bin/env python3
"""Replay checked guide transcripts. Full mode includes paid Codex proofs.

Quick mode uses a private HOME, real local API and isolated WatchTower queue.
It never starts an agent. Full mode also runs the existing Docker proofs.
"""
import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    command: str
    expected: list[str]
    slow: bool = False


def extract(text):
    """Each checked fence holds one command and ordered, exact output lines."""
    result = []
    for match in re.finditer(r'^```console checked( slow)?\n(.*?)^```\s*$', text, re.M | re.S):
        lines = match[2].splitlines()
        if not lines or not lines[0].startswith('$ '):
            raise ValueError('Checked fence must start with $ command')
        if len(lines) < 2 or any(line.startswith('$ ') for line in lines[1:]):
            raise ValueError('Use one command and at least one output line per fence')
        result.append(Check(lines[0][2:], lines[1:], bool(match[1])))
    if text.count('```console checked') != len(result):
        raise ValueError('Malformed checked fence')
    return result


def matches(output, expected):
    """Allow trimmed output, but preserve line content and order."""
    lines = iter(output.splitlines())
    return all(any(line == want for line in lines) for want in expected)


def proof_module():
    spec = importlib.util.spec_from_file_location('guide_proof', REPO / 'scripts/oncall-proof.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child(root, guide):
    sys.path.insert(0, str(REPO))
    from ccc_server.oncall import api, engine, wt
    eng = engine.Engine(root / 'oncall', wt=wt.WT(dispatch=False))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.handle_api('GET')
        def do_POST(self):
            self.handle_api('POST')
        def handle_api(self, method):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            if self.path.startswith(('/repos/', '/api/0/')):
                code, payload = 200, []
            else:
                code, payload = api.handle(method, self.path, body, eng=eng)
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ['CCC_SERVER'] = f'http://127.0.0.1:{server.server_port}'
    os.environ['SOURCE_URL'] = os.environ['CCC_SERVER']
    proof = proof_module()
    watcher = None
    try:
        watcher = proof.start_watcher(root, eng.wt.argv())
        # No setup auto-drain: configure a real queue with dispatch disabled.
        eng.wt._ok('config', '-q', 'GUIDE', '--auto-drain', 'off', '--workers', '0')
        cfg = eng.config()
        cfg.update(queue='GUIDE', enabled=True)
        eng.save_config(cfg)
        for check in extract(guide.read_text()):
            if check.slow:
                continue
            args = shlex.split(os.path.expandvars(check.command))
            if args[:2] != ['ccc', 'oncall']:
                raise ValueError('Quick commands must start with ccc oncall')
            done = subprocess.run([sys.executable, str(REPO / 'ccc'), *args[1:]],
                                  text=True, input='test-only\n', capture_output=True, timeout=60)
            print('$ ' + check.command, flush=True)
            print(done.stdout, end='', flush=True)
            if args[2:5] == ['source', 'add', 'command']:
                _, source = next(iter(eng.sources().values()))
                assert len(source.poll()) == 1, 'Failing command did not produce a finding'
                assert source.poll() == [], 'Duplicate command failure finding'
                print('Probe: one real failure finding; repeat suppressed.', flush=True)
            if done.returncode or not matches(done.stdout, check.expected):
                raise AssertionError(f'{guide.name}: output mismatch or exit {done.returncode}: {done.stderr}')
    finally:
        proof.stop_watcher(watcher)
        server.shutdown()
        server.server_close()


def quick(guide):
    proof = proof_module()
    with tempfile.TemporaryDirectory(prefix='ccc-guide-') as tmp:
        root = Path(tmp)
        bins = root / 'bin'
        bins.mkdir()
        for name in ('python3', 'wt'):
            executable = shutil.which(name)
            if not executable:
                raise RuntimeError(f'{name} is required')
            (bins / name).symlink_to(executable)
        env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
        env.update(HOME=tmp, PATH=str(bins), XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp,
                   CCC_STATE_DIR=str(root / 'state'), CCC_FF_ONCALL='1', CCC_TELEMETRY_DISABLED='1',
                   WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_AUTOSTART='1',
                   WT_NO_SELF_UPDATE='1', WATCHTOWER_DELEGATE_URL='off',
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   PYTHONPATH=os.pathsep.join(sys.path))
        try:
            subprocess.run([sys.executable, __file__, '--child', tmp, str(guide)],
                           env=env, cwd=root, timeout=180, check=True)
        finally:
            proof.cleanup_isolated_processes(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Skip Docker and paid model runs')
    args = parser.parse_args()
    guides = sorted((REPO / 'docs/guides').glob('*.md'))
    slow = {}
    count = 0
    for guide in guides:
        checks = extract(guide.read_text())
        if not checks:
            continue
        if any(not c.slow for c in checks):
            quick(guide)
        for check in checks:
            if check.slow:
                slow.setdefault(check.command, []).append(check.expected)
        count += len(checks)
    if not count:
        raise RuntimeError('No checked commands found')
    if not args.quick:
        for command, expected in slow.items():
            argv = shlex.split(command)
            allowed = [['python3', f'scripts/{name}', '--with-agent'] for name in
                       ('oncall-proof.py', 'oncall-session-proof.py')]
            if argv not in allowed:
                raise ValueError('Unknown isolated proof command: ' + command)
            # The proofs own bounded stage deadlines and cleanup. Killing their
            # parent on an outer timeout could strand Docker containers.
            done = subprocess.run(argv, cwd=REPO, text=True, capture_output=True)
            print(done.stdout)
            # Shared commands run once; each guide's lines must occur in its output.
            if done.returncode or not all(matches(done.stdout, lines) for lines in expected):
                raise AssertionError(command + '\n' + done.stderr)
    print(f'PASS: {count} checked blocks; ' + ('slow proofs skipped' if args.quick else 'including real agent proofs'))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        main()
