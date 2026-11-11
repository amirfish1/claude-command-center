#!/usr/bin/env python3
"""Read-only GitHub contract and isolated Gitea Actions fixing-worker proof.

--quick skips Docker and uses no account credentials. Full mode copies only
Codex auth.json into a temporary home for one paid fixing worker.
"""
from datetime import datetime, timezone
import argparse
import importlib.util
import os
import shutil
import socket
import signal
import sys
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.error


def run(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=120).strip()
    except subprocess.CalledProcessError as exc:
        # Registration credentials may be arguments to the disposable runner.
        # Do not let CalledProcessError render that command in a traceback.
        raise RuntimeError(f'{Path(args[0]).name} failed (exit {exc.returncode}); see scrubbed proof logs') from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{Path(args[0]).name} exceeded the proof command deadline') from None


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as res:
        return json.load(res), dict(res.headers)


def github():
    start = time.monotonic()
    url = 'https://api.github.com/repos/actions/runner/actions/runs?per_page=2'
    body, headers = get(url, {'User-Agent': 'ccc-oncall-proof'})
    assert isinstance(body['workflow_runs'], list)
    assert len(body['workflow_runs']) == 2
    assert {'id', 'workflow_id', 'head_sha', 'head_branch', 'status', 'conclusion'} <= body['workflow_runs'][0].keys()
    lower = {k.lower(): v for k, v in headers.items()}
    assert 'rel="next"' in lower['link']
    assert 'x-ratelimit-remaining' in lower
    get(url + '&page=2', {'User-Agent': 'ccc-oncall-proof'})
    conditional = 'no ETag returned'
    if lower.get('etag'):
        try:
            get(url, {'User-Agent': 'ccc-oncall-proof', 'If-None-Match': lower['etag']})
            conditional = '200 (feed changed or server did not return 304)'
        except urllib.error.HTTPError as exc:
            assert exc.code == 304
            conditional = '304'
    print(f'PASS GitHub public runs, two pages, rate headers; conditional={conditional}; {time.monotonic()-start:.2f}s', flush=True)


def local(provider="gitea", pull_request=False):
    image = "codeberg.org/forgejo/forgejo:17.0.1" if provider == "forgejo" else "gitea/gitea:1.25.1"
    binary = "forgejo" if provider == "forgejo" else "gitea"
    runner_image = "data.forgejo.org/forgejo/runner:13.2.1" if provider == "forgejo" else "gitea/act_runner:0.2.13"
    runner_binary = "forgejo-runner" if provider == "forgejo" else "act_runner"
    prefix = "FORGEJO" if provider == "forgejo" else "GITEA"
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ccc-ci-proof-') as temp:
        name = Path(temp).name
        runner = name + '-runner'
        network = name + '-net'
        run('docker', 'network', 'create', network)
        try:
            run('docker', 'run', '-d', '--name', name, '--network', network, '--network-alias', 'forge', '--tmpfs', '/data:rw,nosuid,nodev,exec,size=512m', '-p', '127.0.0.1::3000',
                '-e', f'{prefix}__security__INSTALL_LOCK=true',
                '-e', f'{prefix}__database__DB_TYPE=sqlite3',
                '-e', f'{prefix}__service__DISABLE_REGISTRATION=true',
                '-e', f'{prefix}__actions__ENABLED=true', image)
            info = json.loads(run('docker', 'inspect', name))[0]
            port = info['NetworkSettings']['Ports']['3000/tcp'][0]['HostPort']
            base = 'http://127.0.0.1:' + port
            deadline = time.monotonic() + 60
            while True:
                try:
                    version, _ = get(base + '/api/v1/version')
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError('Gitea readiness')
                    threading.Event().wait(.25)
            spec, _ = get(base + '/swagger.v1.json')
            paths = spec['paths']
            required = ['/repos/{owner}/{repo}/actions/runs',
                        '/repos/{owner}/{repo}/actions/runs/{run_id}/jobs',
                        '/repos/{owner}/{repo}/actions/jobs/{job_id}/logs']
            import re
            normalize = lambda p: re.sub(r'\{[^}]+\}', '{}', p)
            missing = [p for p in required if normalize(p) not in {normalize(k) for k in paths}]
            print('Actions routes: ' + ', '.join(k for k in paths if '/actions/' in k and ('jobs' in k or 'runs' in k)), flush=True)
            print(f'{provider} {version["version"]}: API schema checked in {time.monotonic()-start:.2f}s', flush=True)
            if missing:
                raise RuntimeError('Local API lacks required endpoints: ' + ', '.join(missing))
            run('docker', 'exec', '--user', 'git', name, binary, 'admin', 'user', 'create',
                '--username', 'demo', '--password', 'test-password', '--email', 'demo@example.test', '--admin')
            token = run('docker', 'exec', '--user', 'git', name, binary, 'admin', 'user',
                        'generate-access-token', '--username', 'demo', '--token-name', 'proof',
                        '--scopes', 'all', '--raw')
            req = urllib.request.Request(base + '/api/v1/user/repos',
                    data=json.dumps({'name': 'sample', 'default_branch': 'main'}).encode(),
                    headers={'Authorization': 'token ' + token, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                json.load(response)
            registration = run('docker', 'exec', '--user', 'git', name, binary, 'actions',
                               'generate-runner-token')
            # Host execution here means inside this disposable runner container.
            run('docker', 'run', '-d', '--name', runner, '--network', network,
                '--user', '0:0', '-e', 'PROOF_REGISTRATION=' + registration, '--entrypoint', 'sh',
                runner_image, '-c',
                f'apk add --no-cache python3 git bash && {runner_binary} register --no-interactive '
                '--instance http://forge:3000 --token "$PROOF_REGISTRATION" '
                f'--name proof --labels python:host && {runner_binary} daemon')
            ntfy = name + '-ntfy'
            run('docker', 'run', '-d', '--name', ntfy, '-p', '127.0.0.1::80',
                'binwiederhier/ntfy:v2.17.0', 'serve')
            port_ntfy = json.loads(run('docker', 'inspect', ntfy))[0]['NetworkSettings']['Ports']['80/tcp'][0]['HostPort']
            root = Path(temp)
            (root / 'actions-token').write_text(token)
            (root / 'actions-token').chmod(0o600)
            bins = root / 'bin'
            bins.mkdir()
            for executable in ('python3', 'wt', 'codex', 'git', 'bash', 'sh', 'env', 'cat', 'ls', 'rg', 'sed', 'node', 'ps', 'lsof', 'uname'):
                target = shutil.which(executable)
                if target:
                    (bins / executable).symlink_to(target)
            auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
            (root / '.codex').mkdir(mode=0o700)
            shutil.copyfile(auth, root / '.codex/auth.json')
            (root / '.codex/auth.json').chmod(0o600)
            env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
            env.update(HOME=temp, PATH=str(bins), CODEX_HOME=str(root / '.codex'),
                       CCC_STATE_DIR=str(root / 'state'), CCC_ONCALL_STATE_DIR=str(root / 'oncall'),
                       CCC_EPHEMERAL='1', CCC_ONCALL_EPHEMERAL='1', CCC_TELEMETRY_DISABLED='1',
                       CCC_AUTO_UPDATE='0', CCC_BG_HELPER='0', CCC_CONV_READER='0', CCC_BOOT_WARM_DELAY_S='86400',
                       PROOF_PROVIDER=provider, PROOF_PR='1' if pull_request else '0', XDG_CONFIG_HOME=temp, XDG_DATA_HOME=temp,
                       WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_AUTOSTART='1',
                       WT_NO_SELF_UPDATE='1', WATCHTOWER_DELEGATE_URL='off', CCC_FF_ONCALL='1',
                       GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       PROOF_GIT=shutil.which('git'), PYTHONPATH=os.pathsep.join(sys.path))
            original_login = auth.read_bytes()
            subprocess.run([sys.executable, __file__, '--child', temp, base,
                            'http://127.0.0.1:' + port_ntfy], env=env, check=True, timeout=600)
            if (root / '.codex/auth.json').read_bytes() != original_login:
                print('Codex renewed the temporary login. It will be discarded; a later sign-in may be needed.', flush=True)

        except BaseException:
            # Retain only a scrubbed tail in the caller's output, before cleanup.
            for container in (runner, name):
                result = subprocess.run(['docker', 'logs', '--tail', '30', container],
                                        capture_output=True, text=True, timeout=10)
                log = result.stdout + result.stderr
                for value in (locals().get('token', ''), locals().get('registration', '')):
                    if value:
                        log = log.replace(value, '[redacted]')
                print(container.rsplit('-', 1)[-1] + ' failure log:\n' + log[-6000:], flush=True)
            raise
        finally:
            helper().cleanup_isolated_processes(temp)
            subprocess.run(['docker', 'rm', '-fv', runner, name + '-ntfy', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
            subprocess.run(['docker', 'network', 'rm', network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def helper():
    spec = importlib.util.spec_from_file_location('protocol_proof', Path(__file__).with_name('oncall-proof.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child(temp, base, ntfy):
    root = Path(temp)
    repo = root / 'sample'
    repo.mkdir()
    os.chdir(repo)
    h = helper()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ccc_server.oncall import store, wt
    wt_client = wt.WT()
    processes = []
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    dashboard_url = f'http://127.0.0.1:{port}'
    os.environ['CCC_SERVER'] = dashboard_url
    watcher = h.start_watcher(temp, wt_client.argv())
    def start_process(script, *args):
        with (root / (script + '.log')).open('a') as log:
            proc = subprocess.Popen([sys.executable, str(h.REPO / script), *args],
                                    stdout=log, stderr=log, start_new_session=True)
        processes.append(proc)
        return proc
    def dashboard_ready():
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                return get(dashboard_url + '/api/oncall/status')[0]
            except OSError:
                threading.Event().wait(.5)
        raise TimeoutError('Isolated dashboard readiness')
    start_process('ccc_worker.py')
    dashboard = start_process('server.py', '--port', str(port))
    dashboard_ready()
    database = store.Store(root / 'oncall')
    def config():
        return get(dashboard_url + '/api/oncall/status')[0]['config']
    target_branch = 'deps/update' if os.environ.get('PROOF_PR') == '1' else 'main'
    provider = os.environ.get('PROOF_PROVIDER', 'gitea')
    token = (root / 'actions-token').read_text().strip()
    auth_headers = {'Authorization': 'token ' + token}
    start = time.monotonic()
    def stamp(label):
        print(f'{datetime.now(timezone.utc).isoformat()} {label}: {time.monotonic()-start:.2f}s elapsed', flush=True)
    def cli(*args, input=''):
        return h.run(sys.executable, str(h.REPO / 'ccc'), 'oncall', *args, input=input)
    try:
        run('git', 'init', '-b', 'main')
        run('git', 'config', 'user.email', 'demo@example.test')
        run('git', 'config', 'user.name', 'CI proof')
        run('git', 'remote', 'add', 'origin', base.replace('http://', 'http://demo:test-password@') + '/demo/sample.git')
        (repo / 'sum.py').write_text('def add(a, b): return a + b\n')
        (repo / 'test_sum.py').write_text('import unittest\nfrom sum import add\nclass SumTest(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n')
        (repo / 'AGENTS.md').write_text('Fix sum.py only. Never change workflow files or tests. Never force-push. '
            f'Commit and push only to origin {target_branch}. Run python3 -m unittest. '
            'Verify the Actions run for your commit is green using the local API before closing the ticket.\n'
            + 'Local API: ' + base + '/api/v1/repos/demo/sample/actions/runs\n'
            + 'Read ../actions-token for local API Authorization: token VALUE. Never print it.\n')
        workflows = repo / ('.forgejo/workflows' if provider == 'forgejo' else '.gitea/workflows')
        workflows.mkdir(parents=True)
        workflow = workflows / 'test.yml'
        workflow.write_text('name: unit\non: [push]\njobs:\n  unit:\n    runs-on: python\n    steps:\n'
            '      - name: tests\n        run: |\n'
            '          git clone http://forge:3000/demo/sample.git source\n'
            '          cd source\n          git checkout "$GITHUB_SHA"\n          python3 -m unittest -v\n')
        run('git', 'add', 'sum.py', 'test_sum.py', 'AGENTS.md', str(workflow.relative_to(repo)))
        run('git', 'commit', '-m', 'test: seed passing suite')
        run('git', 'push', '-u', 'origin', 'main')
        def wait_result(sha, expected):
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                rows, _ = get(base + '/api/v1/repos/demo/sample/actions/runs', auth_headers)
                for row in rows.get('workflow_runs', []):
                    if provider == 'forgejo':
                        from ccc_server.oncall.ci_source import normalize_forgejo_run
                        row = normalize_forgejo_run(row)
                    if row.get('head_sha') == sha and row.get('status') == 'completed':
                        assert row.get('conclusion') == expected, row
                        return row
                threading.Event().wait(1)
            raise TimeoutError('CI did not finish: ' + expected)
        wait_result(run('git', 'rev-parse', 'HEAD'), 'success')
        stamp('Initial green')
        cli('setup', '--set', 'server=' + ntfy, input='\n\n')
        wt_client._ok('config', '-q', config()['queue'], '--engine', 'codex', '--model', 'gpt-6.1-terra', '--effort', 'medium')
        cli('source', 'add', 'ci', '--set', 'repo=demo/sample', '--set', 'provider=' + provider,
            *(['--set', 'pull_requests=yes', '--set', 'pr_allowlist=demo'] if target_branch != 'main' else []), '--set', 'base=' + base + '/api/v1', '--set', 'every_s=30', '--secret-stdin', 'token', input=token + '\n')
        stamp('Source configured')
        main_sha = run('git', 'rev-parse', 'HEAD')
        if target_branch != 'main':
            run('git', 'checkout', '-b', target_branch)
        baseline_test = (repo / 'test_sum.py').read_bytes()
        baseline_workflow = workflow.read_bytes()
        (repo / 'sum.py').write_text('def add(a, b): return a - b\n')
        run('git', 'add', 'sum.py')
        run('git', 'commit', '-m', 'test: seed addition failure')
        red = run('git', 'rev-parse', 'HEAD')
        run('git', 'push', '-u', 'origin', target_branch)
        if target_branch != 'main':
            request = urllib.request.Request(base + '/api/v1/repos/demo/sample/pulls',
                data=json.dumps({'title': 'Dependency update', 'head': target_branch, 'base': 'main'}).encode(),
                headers={**auth_headers, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=60) as response:
                pull_number = json.load(response)['number']
        wait_result(red, 'failure')
        stamp('Red run')
        deadline = time.monotonic() + 480
        seen = set()
        while time.monotonic() < deadline:
            reports = database.reports()
            if reports and reports[0].get('ticket_ref') and 'ticket' not in seen:
                assert len(reports) == 1
                assert 'AssertionError' in reports[0]['text'], 'Failed test log missing from ticket'
                assert 'Never force-push' in reports[0]['text']
                stamp('Ticket filed ' + reports[0]['ticket_ref'])
                seen.add('ticket')
                dashboard.terminate()
                dashboard.wait(timeout=15)
                dashboard = start_process('server.py', '--port', str(port))
                dashboard_ready()
                stamp('Dashboard restarted between red and green')
            records = root / '.watchtower/workers.json'
            if records.exists() and 'worker' not in seen:
                workers = json.loads(records.read_text()).get('workers', [])
                if workers:
                    assert workers[0]['model'] == 'gpt-6.1-terra'
                    stamp('Real worker started')
                    seen.add('worker')
            if reports and reports[0]['state'] == 'closed':
                stamp('Ticket closed after green')
                break
            threading.Event().wait(1)
        else:
            raise TimeoutError('Automatic fix path did not complete')
        assert len(database.reports()) == 1
        assert 'AssertionError' in database.reports()[0]['text'], 'Failed test log missing from ticket'
        assert (repo / 'test_sum.py').read_bytes() == baseline_test
        assert workflow.read_bytes() == baseline_workflow
        fixed = run('git', 'rev-parse', 'HEAD')
        assert fixed != red
        run('git', 'merge-base', '--is-ancestor', red, fixed)
        assert run('git', 'ls-remote', 'origin', 'refs/heads/' + target_branch).split()[0] == fixed
        if target_branch != 'main':
            assert run('git', 'ls-remote', 'origin', 'refs/heads/main').split()[0] == main_sha
            pull, _ = get(base + f'/api/v1/repos/demo/sample/pulls/{pull_number}', auth_headers)
            assert pull['state'] == 'open' and pull['head']['sha'] == fixed
            stamp('PR branch fixed; default branch unchanged; PR still open')
        wait_result(fixed, 'success')
        print('Verified pushed fix commit: ' + fixed, flush=True)
        tests = subprocess.run([sys.executable, '-m', 'unittest', '-v'], capture_output=True,
                               text=True, timeout=30, check=True)
        transcript = tests.stdout + tests.stderr
        assert 'Ran 1 test in ' in transcript and 'skipped' not in transcript.lower(), transcript
        topic = config()['channels'][0]['topic']
        with urllib.request.urlopen(ntfy + '/' + topic + '/json?poll=1&since=all') as response:
            events = [json.loads(line) for line in response if line.strip()]
        assert any('fixed' in e.get('message', '').lower() for e in events)
        stamp('Owner fixed notice, unchanged workflow and one test, normal push verified')
    finally:
        h.stop_watcher(watcher)
        for proc in reversed(processes):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Only the read-only public GitHub check')
    parser.add_argument("--provider", choices=("gitea", "forgejo"), default="gitea")
    parser.add_argument("--pull-request", action="store_true")
    args = parser.parse_args()
    github()
    if not args.quick:
        local(args.provider, args.pull_request)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(*sys.argv[2:])
    else:
        main()
