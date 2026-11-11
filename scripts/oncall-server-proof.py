#!/usr/bin/env python3
"""Isolated container proof; default permits paid gpt-6.1-terra workers.

--quick checks setup, persistence and idle resources without credentials.
All Docker resources and copied login files are removed even on failure.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(*args, timeout=120, **kw):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kw)
    if p.returncode:
        raise RuntimeError((p.stdout + '\n' + p.stderr)[-5000:])
    return p.stdout.strip()


def wait(check, seconds, label):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError(label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    started_at = time.monotonic()
    rows, containers = [], []
    volume = None
    code = 0
    def interrupted(*_):
        raise RuntimeError('Proof interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(1200)
    try:
        if not args.skip_build:
            run('docker', 'build', '--force-rm', '-f', 'Dockerfile.oncall', '-t', 'ccc-oncall:local', '.', cwd=ROOT, timeout=600)
        rows.append(('image bytes', 'PASS', run('docker', 'image', 'inspect', 'ccc-oncall:local', '--format', '{{.Size}}')))
        with tempfile.TemporaryDirectory(prefix='ccc-server-proof-') as tmp:
            root = Path(tmp)
            volume = root.name
            run('docker', 'volume', 'create', volume)
            ntfy = run('docker', 'run', '-d', '--name', root.name + '-ntfy', 'binwiederhier/ntfy:v2.17.0',
                       'serve', '--listen-http', '127.0.0.1:8080', '--cache-file', '/tmp/cache.db')
            containers.append(ntfy)
            command = ['docker', 'run', '-d', '--name', root.name, '--network', 'container:' + ntfy,
                       '-v', volume + ':/state', '-e', 'CCC_TELEMETRY_DISABLED=1',
                       '-e', 'WATCHTOWER_DELEGATE_URL=off', '-e', 'CCC_BG_HELPER=0',
                       '-e', 'CCC_BOOT_WARM_DELAY_S=86400']
            if not args.quick:
                auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
                shutil.copyfile(auth, root / 'auth.json')
                (root / 'auth.json').chmod(0o600)
                codex = Path(shutil.which('codex')).resolve()
                for binary in (codex, codex.with_name('codex-code-mode-host'), codex.with_name('codex-exec-server')):
                    if binary.is_file():
                        command += ['-v', str(binary) + ':/usr/local/bin/' + binary.name + ':ro']
            cid = run(*command, 'ccc-oncall:local')
            containers.append(cid)
            def ex(*words):
                return run('docker', 'exec', cid, *words)
            def ready():
                if run('docker', 'inspect', cid, '--format', '{{.State.Running}}') != 'true':
                    raise RuntimeError('Container exited: ' + run('docker', 'logs', cid))
                return subprocess.run(['docker', 'exec', cid, 'python3', '/app/scripts/oncall-supervisor.py', '--health'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
            wait(ready, 90, 'CCC health')
            assert ex('id', '-u') == '1000'
            ex('python3', '-c', "from pathlib import Path; rows=Path('/proc/net/tcp').read_text().splitlines()[1:]; listeners=[r.split()[1] for r in rows if r.split()[3]=='0A' and r.split()[1].endswith(':1F9A')]; assert listeners==['0100007F:1F9A'], listeners")
            rows.append(('dashboard binding', 'PASS', '127.0.0.1:8090 only'))
            ex('mkdir', '-p', '/state/project', '/state/.codex')
            if not args.quick:
                run('docker', 'cp', str(root / 'auth.json'), cid + ':/state/.codex/auth.json')
                run('docker', 'exec', '-u', '0', cid, 'chown', '1000:1000', '/state/.codex/auth.json')
            seed = """from pathlib import Path
import subprocess
p=Path('/state/project')
(p/'sum.py').write_text('def add(a, b):\\n    return a - b\\n')
(p/'test_sum.py').write_text('import unittest\\nfrom sum import add\\nclass Test(unittest.TestCase):\\n def test_add(self): self.assertEqual(add(2,3),5)\\n')
(p/'AGENTS.md').write_text('Fix only sum.py. Run python3 -m unittest. Commit locally and close the assigned ticket with the commit. No remote, no push, no delegation, no network. Use only this repository and isolated queue. Stop after one ticket.\\n')
for a in [('init',),('config','user.name','Proof Worker'),('config','user.email','worker@example.test'),('add','sum.py','test_sum.py','AGENTS.md'),('commit','-m','test: seed addition bug')]:
 subprocess.run(['git','-C',str(p),*a],check=True,capture_output=True)
"""
            ex('python3', '-c', seed)
            ex('ccc', 'oncall', 'setup', '--yes', '--repo', '/state/project', '--queue', 'PROOF',
               '--channel', 'ntfy', '--set', 'server=http://127.0.0.1:8080', '--set', 'topic=proof')
            rows.append(('headless setup', 'PASS', 'non-root; local ntfy test'))
            ex('wt', 'config', '-q', 'PROOF', '--engine', 'codex', '--model', 'gpt-6.1-terra', '--effort', 'medium', '--codex-transport', 'exec',
               '--auto-drain', 'off' if args.quick else 'on')
            before = ex('git', '-C', '/state/project', 'rev-parse', 'HEAD')
            ex('ccc', 'oncall', 'report', 'add(2, 3) returns -1 instead of 5. Fix sum.py and run test_sum.py.')
            if not args.quick:
                def started():
                    return ex('python3', '-c', "from pathlib import Path; import json; p=Path('/state/.watchtower/workers.json'); print(bool(p.exists() and json.loads(p.read_text()).get('workers')))") == 'True'
                wait(started, 90, 'worker start')
                def claimed():
                    tickets = json.loads(ex('wt', 'ls', '-q', 'PROOF', '--status', 'active', '--json'))
                    return any(t.get('status') == 'in_progress' for t in tickets)
                wait(claimed, 90, 'worker claim before restart')
                assert ex('git', '-C', '/state/project', 'rev-parse', 'HEAD') == before, 'Worker finished before restart'
                rows.append(('restart trigger', 'PASS', 'ticket in progress; no fix commit yet'))
            run('docker', 'restart', cid)
            wait(ready, 90, 'health after restart')
            status = json.loads(ex('ccc', 'oncall', 'status', '--json'))
            assert status['enabled'] and status['queue'] == 'PROOF'
            rows.append(('restart / saved setup', 'PASS', 'same volume'))
            if not args.quick:
                def fixed():
                    return ex('git', '-C', '/state/project', 'rev-parse', 'HEAD') != before
                wait(fixed, 480, 'worker did not resume and commit a fix after container restart')
                ex('sh', '-c', 'cd /state/project && python3 -m unittest')
                assert ex('git', '-C', '/state/project', 'diff', '--name-only', before, 'HEAD') == 'sum.py'
                def notice():
                    data = ex('python3', '-c', "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/proof/json?poll=1&since=all').read().decode())")
                    return 'fixed' in data.lower()
                wait(notice, 90, 'fixed notice missing')
                ticket = json.loads(ex('wt', 'find', 'PROOF-1', '--json'))
                assert ticket['status'] == 'closed', 'Fixed notice without a closed ticket'
                commit = ex('git', '-C', '/state/project', 'rev-parse', 'HEAD')
                assert any(h.get('commit') == commit for h in ticket.get('history', [])) or (ticket.get('resolution') or {}).get('commit') == commit
                rows.append(('fix after restart / ntfy', 'PASS', 'gpt-6.1-terra; test passes; commit ' + commit))
            else:
                rows.append(('paid worker recovery', 'BOUNDARY', 'not run in --quick'))
            # Let startup work settle before taking the idle sample.
            time.sleep(60)
            rows.append(('idle resources after 60s quiet', 'MEASURED', run('docker', 'stats', '--no-stream', '--format', '{{.MemUsage}} / {{.CPUPerc}}', cid)))
    except Exception as exc:
        rows.append(('server proof', 'FAIL', str(exc)))
        if containers:
            print(run('docker', 'logs', containers[-1])[-6000:])
        code = 1
    finally:
        for cid in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', cid], capture_output=True, timeout=30)
        if volume:
            subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True, timeout=30)
        signal.alarm(0)
    rows.append(('elapsed seconds', 'MEASURED', f'{time.monotonic() - started_at:.2f}'))
    print('Check | Result | Measurement / reason')
    for row in rows:
        print(' | '.join(row))
    return code


if __name__ == '__main__':
    sys.exit(main())
