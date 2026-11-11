#!/usr/bin/env python3
"""Opt-in local monitoring proof. Docker required; --with-agent spends Codex quota."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('protocol_proof', REPO / 'scripts/oncall-proof.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
run = helpers.run


def wait(check, seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if check():
                return
        except OSError:
            pass
        threading.Event().wait(.25)
    raise TimeoutError('Local proof condition did not become true')


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


APP = '''from http.server import BaseHTTPRequestHandler, HTTPServer
import traceback
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        counter = Path('/app/requests.count')
        counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
        try:
            scope = {}
            exec(Path('/app/sum.py').read_text(), scope)
            assert scope['add'](2, 3) == 5, 'add(2, 3) must equal 5'
            code, body = 200, b'ok'
        except Exception:
            body = traceback.format_exc().encode()
            with Path('/app/error.log').open('ab') as log:
                log.write(body)
            print(body.decode(), flush=True)
            code = 500
        if self.path == '/metrics':
            body = ('sample_ok ' + ('1' if code == 200 else '0') + '\\n').encode()
            code = 200
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.end_headers()
        self.wfile.write(body)
HTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
'''


def fault_checks(source, rows):
    """Inject HTTP failures at a local socket, not inside the source adapter."""
    status = [429]
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status[0])
            self.send_header('Retry-After', '3600')
            self.end_headers()
            self.wfile.write(b'{}')
        do_POST = do_GET
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    from ccc_server.oncall import sources
    cfg = dict(source.cfg, id='fault-proof')
    cfg['base' if source.type == 'sentry' else 'host'] = f'http://127.0.0.1:{server.server_port}'
    now = [1000.0]
    probe = type(source)(cfg, secret=lambda _: 'proof-local-token', clock=lambda: now[0])
    try:
        assert probe.poll() == [] and '429' in probe.last_error
        assert len(calls) == 1 and not probe.due()
        now[0] += probe.every_s
        assert not probe.due() and probe.poll() == [] and len(calls) == 1
        now[0] += 3600 - probe.every_s
        assert probe.due()
        rows.append(('rate limit (injected)', 'PASS', '1 call; Retry-After 3600s respected even by forced poll'))
        status[0] = 503
        assert probe.poll() == [] and '503' in probe.last_error
        assert not probe._get('baselined')
        rows.append(('HTTP 503 (injected)', 'PASS', '1 call; no baseline saved'))
    finally:
        server.shutdown()
        server.server_close()
    now[0] += 3600
    assert probe.poll() == [] and 'Could not reach' in probe.last_error
    rows.append(('server down', 'PASS', 'closed local socket; no baseline saved'))


def child(root, config):
    sys.path.insert(0, str(REPO))
    from ccc_server.oncall import api, engine, wt, sources
    root = Path(root)
    eng = engine.Engine(root / 'oncall', wt=wt.WT(dispatch=config['agent']))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.handle_api('GET')
        def do_POST(self):
            self.handle_api('POST')
        def handle_api(self, method):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            code, payload = api.handle(method, self.path, body, eng=eng)
            self.send_response(code)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ['CCC_SERVER'] = f'http://127.0.0.1:{server.server_port}'
    def cli(*args, input=''):
        return run(sys.executable, str(REPO / 'ccc'), 'oncall', *args, input=input)
    watcher = None
    rows = []
    try:
        project = root / 'sample-app'
        os.chdir(project)
        baseline = helpers.seed_repo(project)
        if config['app']:
            instructions = project / 'AGENTS.md'
            instructions.write_text(instructions.read_text() +
                '\nException: you may GET this isolated local health URL to verify the fix: ' +
                config['app'] + '/health. No other network access is allowed.\n')
            run(os.environ['PROOF_GIT'], 'commit', '--only', 'AGENTS.md', '-m', 'test: allow local health verification')
            baseline = run(os.environ['PROOF_GIT'], 'rev-parse', 'HEAD')
        if 'command' in config['kinds']:
            (project / 'check_service.py').write_text(
                'import urllib.request, urllib.error, sys\n'
                'try:\n'
                '    urllib.request.urlopen(' + repr(config['app'] + '/health') + ', timeout=5).read()\n'
                'except urllib.error.HTTPError as exc:\n'
                '    print(exc.read().decode())\n'
                '    sys.exit(1)\n')
        if any(k in config['kinds'] for k in ('http', 'logs', 'docker', 'alertmanager')):
            (project / 'sum.py').write_text('def add(a, b):\n    return a + b\n')
            wait(lambda: get(config['app'] + '/health') == b'ok')
        if config['agent']:
            watcher = helpers.start_watcher(root, eng.wt.argv())
        cli('setup', '--set', 'server=' + config['ntfy'], input='\n\n')
        queue = eng.config()['queue']
        eng.wt._ok('config', '-q', queue, '--engine', 'codex', '--model', 'gpt-6.1-terra', '--effort', 'medium',
                   '--auto-drain', 'on' if config['agent'] else 'off')
        if 'alertmanager' in config['kinds']:
            wait(lambda: json.loads(get(config['prometheus'] + '/api/v1/query?query=sample_ok'))['data']['result'] and
                 json.loads(get(config['prometheus'] + '/api/v1/query?query=sample_ok'))['data']['result'][0]['value'][1] == '1')
            wait(lambda: not json.loads(get(config['alertmanager'] + '/api/v2/alerts/groups')))
        for kind in config['kinds']:
            # Each run selects one source so a worker cannot fix another source's bug.
            if kind == 'http':
                settings = {'url': config['app'] + '/health', 'fail_after': '1'}
            elif kind == 'command':
                settings = {'cmd': 'python3 check_service.py', 'cwd': str(project)}
            elif kind == 'logs':
                settings = {'container': config['app_container']} if config['logs_container'] else {'path': str(project / 'error.log')}
            elif kind == 'docker':
                settings = {'container': config['app_container']}
            elif kind == 'alertmanager':
                settings = {'url': config['alertmanager']}
            elif kind == 'posthog':
                settings = {'host': config['posthog'], 'project_id': '1', 'min_events': '1'}
            else:
                settings = {'base': config['sentry'], 'org': 'proof', 'project': 'sample'}
            argv = ([v for key, value in settings.items() for v in ('--' + key, value)] if kind == 'logs' else
                    [v for key, value in settings.items() for v in ('--set', key + '=' + value)])
            if kind in ('sentry', 'posthog'):
                argv += ['--secret-stdin', 'token']
            cli('source', 'add', kind, *argv, input='proof-local-token\n' if kind in ('sentry', 'posthog') else '')
            rows.append((kind + ' CLI add', 'PASS', 'real CLI through On-call HTTP API'))
        if any(k in config['kinds'] for k in ('http', 'logs', 'docker', 'alertmanager')):
            (project / 'sum.py').write_text('def add(a, b):\n    return a - b\n')
        calls = []
        for _, source in eng.sources().values():
            if source.type == 'sentry' and config['clock_skew']:
                source._clock = lambda: time.time() + config['clock_skew']
                rows.append(('source clock injection', 'INFO', str(config['clock_skew']) + ' seconds; real server Date unchanged'))
            original = source._http
            def counted(*args, _original=original, **kwargs):
                calls.append(args[1])
                return _original(*args, **kwargs)
            source._http = counted
        if any(k in config['kinds'] for k in ('sentry', 'posthog')):
            eng.tick(force=True)
            assert not eng.store.reports(), 'Baseline filed old errors'
            rows.append(('monitoring baseline', 'PASS', f'{len(calls)} API calls; zero tickets'))
            # A real Python exception is serialized using the Sentry store protocol.
            import uuid
            try:
                scope = {}
                exec((project / 'sum.py').read_text(), scope)
                assert scope['add'](2, 3) == 5, 'add(2, 3) must equal 5; run test_sum.py'
            except AssertionError as exc:
                event = {'event_id': uuid.uuid4().hex, 'platform': 'python', 'level': 'error',
                         'exception': {'values': [{'type': type(exc).__name__, 'value': str(exc)}]}}
            for number in range(config['burst']):
                event['event_id'] = uuid.uuid4().hex
                if config['burst'] > 1:
                    event['fingerprint'] = ['burst-' + str(number)]
                req = urllib.request.Request(config['ingest'], data=json.dumps(event).encode(),
                        headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=20) as response:
                    assert response.status == 200
        if any(k in config['kinds'] for k in ('logs', 'docker', 'alertmanager')):
            try:
                get(config['app'] + '/health')
            except urllib.error.HTTPError as exc:
                assert exc.code == 500 and b'AssertionError' in exc.read()
            if 'docker' in config['kinds']:
                wait(lambda: json.loads(run('docker', 'inspect', config['app_container']))[0]['State']['Health']['Status'] == 'unhealthy')
            if 'alertmanager' in config['kinds']:
                wait(lambda: bool(json.loads(get(config['alertmanager'] + '/api/v2/alerts/groups'))), 60)
        started = time.monotonic()
        before = len(calls)
        counter = project / 'requests.count'
        service_before = int(counter.read_text()) if counter.exists() else 0
        eng.tick(force=True)
        reports = eng.store.reports(limit=2000)
        if config['burst'] > 1:
            rows.append(('burst poll 1', 'PASS', f'{len(calls)-before} HTTP calls; {len(reports)} total tickets'))
            for index in range(8):
                if len(reports) == config['burst']:
                    break
                poll_start = len(calls)
                eng.tick(force=True)
                reports = eng.store.reports(limit=2000)
                rows.append((f'burst poll {index+2}', 'PASS', f'{len(calls)-poll_start} HTTP calls; {len(reports)} total tickets'))
        assert len(reports) == config['burst'], f'Expected {config["burst"]} tickets, got {len(reports)}; {eng.status()["sources"]}'
        ref = reports[0]['ticket_ref']
        assert ref, 'No ticket reference'
        rows.append(('finding / ticket', 'PASS', f'{len(calls)-before} adapter HTTP calls; ' +
                     (f'{int(counter.read_text()) - service_before} service requests; ' if counter.exists() else '') + ref + f'; {len(reports)} total tickets'))
        before = len(calls)
        eng.tick(force=True)
        assert len(eng.store.reports(limit=2000)) == config['burst']
        rows.append(('next poll', 'PASS', f'{len(calls)-before} HTTP calls; no duplicate'))
        if config['agent']:
            rec = helpers.wait_automatic_worker(root, ref, eng.wt, watcher, started)
            assert rec['model'] == 'gpt-6.1-terra'
            commit = run(os.environ['PROOF_GIT'], 'rev-parse', 'HEAD')
            assert commit != baseline
            assert run(os.environ['PROOF_GIT'], 'diff', '--name-only', baseline, commit) == 'sum.py'
            run(sys.executable, '-B', '-m', 'unittest', '-v')
            item = eng.wt.find(ref)
            assert commit in [e.get('commit') for e in item.get('history', []) if e.get('event') == 'close'] or (item.get('resolution') or {}).get('commit') == commit
            if config['app']:
                assert get(config['app'] + '/health') == b'ok'
            if 'docker' in config['kinds']:
                wait(lambda: json.loads(run('docker', 'inspect', config['app_container']))[0]['State']['Health']['Status'] == 'healthy')
            if 'alertmanager' in config['kinds']:
                wait(lambda: not json.loads(get(config['alertmanager'] + '/api/v2/alerts/groups')), 90)
                rows.append(('Prometheus / Alertmanager recovery', 'PASS', 'real rule resolved after code fix'))
            if 'logs' in config['kinds']:
                size = (project / 'error.log').stat().st_size
                assert get(config['app'] + '/health') == b'ok'
                assert (project / 'error.log').stat().st_size == size
                rows.append(('log recovery', 'PASS', 'healthy request produced no new error'))
            eng.tick(force=True)
            topic = eng.config()['channels'][0]['topic']
            messages = get(config['ntfy'] + '/' + topic + '/json?poll=1&since=all').decode()
            assert 'fixed' in messages.lower() and ref in messages
            rows.append(('automatic fix / ntfy', 'PASS', f'{time.monotonic()-started:.2f}s; commit {commit}'))
        else:
            rows.append(('automatic fix / ntfy', 'BOUNDARY', '--with-agent not selected'))
        source = next(iter(eng.sources().values()))[1]
        if source.type == 'sentry':
            event['event_id'] = __import__('uuid').uuid4().hex
            repeat = urllib.request.Request(config['ingest'], data=json.dumps(event).encode(),
                                            headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(repeat, timeout=20) as response:
                assert response.status == 200
            assert source.poll() == [], 'Same Sentry issue filed twice'
            rows.append(('Sentry same issue recurrence', 'PASS', 'new event on same issue does not file again'))
            wrong = type(source)(source.cfg, secret=lambda _: 'wrong-proof-token')
            ok, detail = wrong.check()
            assert not ok and 'token' in detail.lower()
            rows.append(('wrong token (real server)', 'PASS', 'rejected by GlitchTip'))
            fault_checks(source, rows)
        elif source.type == 'posthog':
            fault_checks(source, rows)
            rows.append(('real PostHog', 'BOUNDARY', 'query contract fixture only; no HogQL engine or real ingestion'))
        elif config['agent'] and source.type in ('http', 'command'):
            # Observe recovery, then a new failure episode without dispatching
            # another paid agent. The ordinary engine has already filed once.
            assert source.poll() == []
            (project / 'sum.py').write_text('def add(a, b):\n    return a - b\n')
            findings = source.poll()
            assert len(findings) == 1 and source.poll() == []
            rows.append(('regression episode', 'PASS', 'healthy poll then failure emits once; no second worker requested'))

    finally:
        helpers.stop_watcher(watcher)
        helpers.cleanup_isolated_processes(root)
        server.shutdown()
        server.server_close()
        print('\nCheck | Result | Measurement / reason', flush=True)
        for row in rows:
            print(' | '.join(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for kind in ('http', 'command', 'sentry', 'posthog', 'logs', 'docker', 'alertmanager'):
        parser.add_argument('--' + kind, action='store_true')
    parser.add_argument('--with-agent', action='store_true')
    parser.add_argument('--burst', type=int, default=1, help='With --sentry, ingest this many new issues without an agent')
    parser.add_argument('--clock-skew', type=int, default=0, help='With --sentry, offset the source clock in seconds')
    parser.add_argument('--logs-container', action='store_true', help='With --logs, read the real Docker log stream')
    parser.add_argument('--backlog', action='store_true', help='Ingest 500 old errors before Sentry setup')
    args = parser.parse_args()
    kinds = [k for k in ('http', 'command', 'sentry', 'posthog', 'logs', 'docker', 'alertmanager') if getattr(args, k)]
    if len(kinds) != 1:
        parser.error('select exactly one source per isolated run')
    if args.burst < 1 or args.burst > 500 or (args.burst != 1 and (not args.sentry or args.with_agent)):
        parser.error('--burst allows 1..500 issues; multiple issues require --sentry without --with-agent')
    if args.clock_skew and not args.sentry:
        parser.error('--clock-skew requires --sentry')
    if args.logs_container and not args.logs:
        parser.error('--logs-container requires --logs')
    if args.backlog and not args.sentry:
        parser.error('--backlog requires --sentry')
    if args.posthog and args.with_agent:
        parser.error('--posthog is a contract fixture, so --with-agent is not supported')
    if not Path('/proc/self/environ').exists():
        parser.error('Linux /proc is required for detached worker cleanup')
    containers = []
    network = None
    fixture = None
    with tempfile.TemporaryDirectory(prefix='ccc-monitoring-proof-') as tmp:
        root = Path(tmp)
        bins = root / 'bin'
        bins.mkdir()
        (root / 'sample-app').mkdir()
        for name in ('python3', 'wt', 'git', 'bash', 'sh', 'env', 'cat', 'ls', 'rg', 'sed', 'node', 'codex'):
            executable = shutil.which(name)
            if executable and (name != 'codex' or args.with_agent):
                (bins / name).symlink_to(executable)
        if args.with_agent:
            auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
            (root / '.codex').mkdir(mode=0o700)
            shutil.copyfile(auth, root / '.codex/auth.json')
            (root / '.codex/auth.json').chmod(0o600)
        env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
        env.update(HOME=tmp, PATH=str(bins), XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp,
                   CODEX_HOME=str(root / '.codex'), CCC_STATE_DIR=str(root / 'state'),
                   PYTHONPATH=os.pathsep.join(sys.path), CCC_FF_ONCALL='1', CCC_TELEMETRY_DISABLED='1',
                   WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_AUTOSTART='1', WT_NO_SELF_UPDATE='1',
                   WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   PROOF_GIT=shutil.which('git'))
        def container(image, *options, command=()):
            name = root.name + '-' + str(len(containers))
            containers.append(name)
            run('docker', 'run', '--name', name, '-d', *options, image, *command)
            return name
        def port(cid, number):
            info = json.loads(run('docker', 'inspect', cid))[0]
            return 'http://127.0.0.1:' + info['NetworkSettings']['Ports'][f'{number}/tcp'][0]['HostPort']
        try:
            ntfy = container('binwiederhier/ntfy:v2.17.0', '-p', '127.0.0.1::80', command=('serve',))
            config = dict(agent=args.with_agent, kinds=kinds, ntfy=port(ntfy, 80), app='', logs_container=args.logs_container, burst=args.burst, clock_skew=args.clock_skew)
            wait(lambda: get(config['ntfy'] + '/v1/health'))
            if args.http or args.command or args.logs or args.docker or args.alertmanager:
                (root / 'sample-app/service.py').write_text(APP)
                (root / 'sample-app/error.log').write_text('ERROR preexisting log entry\n')
                app_options = []
                if args.alertmanager:
                    network = root.name
                    run('docker', 'network', 'create', network)
                    app_options += ['--network', network, '--network-alias', 'sample']
                if args.docker:
                    app_options += ['--health-cmd', '''python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"''',
                                    '--health-interval', '1s', '--health-timeout', '2s', '--health-retries', '1']
                app = container('python:3.12-slim', '-p', '127.0.0.1::8000', '-v',
                                f'{root}/sample-app:/app', *app_options, command=('python', '/app/service.py'))
                config['app_container'] = app
                # Give only inspect/logs access to this one proof container.
                # This helper only permits reads of the proof container.
                docker_bin = shutil.which('docker')
                wrapper = bins / 'docker'
                wrapper.write_text('#!' + sys.executable + '\nimport os, sys\n'
                    + 'args = sys.argv[1:]\n'
                    + 'if not args or args[0] not in ("inspect", "logs") or args[-1] != ' + repr(app) + ':\n'
                    + '    sys.exit("Only proof-container inspect/logs are permitted")\n'
                    + 'os.execv(' + repr(docker_bin) + ', [' + repr(docker_bin) + '] + args)\n')
                wrapper.chmod(0o700)
                config['app'] = port(app, 8000)
            if args.alertmanager:
                am_config = root / 'alertmanager.yml'
                am_config.write_text('route:\n  receiver: local-only\n  group_by: [alertname, instance]\n'
                                     '  group_wait: 0s\n  group_interval: 1s\n  repeat_interval: 1h\n'
                                     'receivers:\n  - name: local-only\n')
                am_config.chmod(0o644)
                am = container('prom/alertmanager:v0.28.1', '--network', network, '--network-alias', 'alertmanager',
                               '-p', '127.0.0.1::9093', '-v', str(am_config) + ':/etc/alertmanager/proof.yml:ro',
                               command=('--config.file=/etc/alertmanager/proof.yml', '--cluster.listen-address='))
                config['alertmanager'] = port(am, 9093)
                rules = root / 'rules.yml'
                rules.write_text('groups:\n  - name: sample\n    rules:\n      - alert: SampleAdditionBroken\n'
                                 '        expr: sample_ok == 0\n        for: 1s\n'
                                 '        annotations:\n          description: "add(2, 3) must return 5. Run test_sum.py."\n')
                rules.chmod(0o644)
                prom_config = root / 'prometheus.yml'
                prom_config.write_text('global:\n  scrape_interval: 1s\n  evaluation_interval: 1s\n'
                    'rule_files: [/etc/prometheus/rules.yml]\n'
                    'alerting:\n  alertmanagers:\n    - static_configs:\n        - targets: [alertmanager:9093]\n'
                    'scrape_configs:\n  - job_name: sample\n    static_configs:\n      - targets: [sample:8000]\n')
                prom_config.chmod(0o644)
                prom = container('prom/prometheus:v3.2.1', '--network', network, '-p', '127.0.0.1::9090',
                                 '-v', str(prom_config) + ':/etc/prometheus/prometheus.yml:ro',
                                 '-v', str(rules) + ':/etc/prometheus/rules.yml:ro')
                config['prometheus'] = port(prom, 9090)
                wait(lambda: get(config['alertmanager'] + '/-/ready'))
                wait(lambda: get(config['prometheus'] + '/-/ready'))
            if args.posthog:
                import re
                groups = [{'group_key': str(i), 'event_count': 3} for i in range(500)]
                class ContractHandler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                        if self.path == '/capture':
                            groups.insert(0, {'group_key': 'new', 'event_count': 4,
                                             'sample_types': '["AssertionError"]',
                                             'sample_values': '["sample addition failed"]'})
                            result = {}
                        else:
                            assert self.path == '/api/projects/1/query/'
                            assert body['query']['kind'] == 'HogQLQuery'
                            sql = body['query']['query']
                            match = re.search(r'OFFSET (\d+)', sql)
                            offset = int(match[1]) if match else 0
                            columns = ['group_key', 'event_count', 'sample_types', 'sample_values']
                            result = {'columns': columns, 'results': [[1]] if sql == 'SELECT 1' else
                                      [[g.get(c) for c in columns] for g in groups[offset:offset+100]]}
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(json.dumps(result).encode())
                fixture = ThreadingHTTPServer(('127.0.0.1', 0), ContractHandler)
                threading.Thread(target=fixture.serve_forever, daemon=True).start()
                config['posthog'] = f'http://127.0.0.1:{fixture.server_port}'
                config['ingest'] = config['posthog'] + '/capture'
            if args.sentry:
                network = root.name
                run('docker', 'network', 'create', network)
                db = container('pgvector/pgvector:pg16', '--network', network, '--network-alias', 'db',
                               '-e', 'POSTGRES_HOST_AUTH_METHOD=trust')
                wait(lambda: subprocess.run(['docker', 'exec', db, 'pg_isready', '-U', 'postgres'], capture_output=True).returncode == 0)
                gt = container('glitchtip/glitchtip:v4.2.0', '--network', network, '-p', '127.0.0.1::8000',
                    '-e', 'DATABASE_URL=postgres://postgres@db/postgres', '-e', 'SECRET_KEY=proof-only-not-secret',
                    '-e', 'CELERY_TASK_ALWAYS_EAGER=true', '-e', 'DATABASE_POOL=false',
                    '-e', 'EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend',
                    command=('sh', '-c', './manage.py migrate && ./manage.py runserver 0.0.0.0:8000 --noreload'))
                config['sentry'] = port(gt, 8000)
                wait(lambda: get(config['sentry'] + '/_health/'), 240)
                seed = '''from django.contrib.auth import get_user_model
from apps.organizations_ext.models import Organization, OrganizationUser, OrganizationOwner
from apps.projects.models import Project, ProjectKey
from apps.api_tokens.models import APIToken
u = get_user_model().objects.create(email='proof@example.test', is_superuser=True, is_staff=True)
o = Organization.objects.create(name='Proof', slug='proof')
m = OrganizationUser.objects.create(organization=o, user=u, role=0)
OrganizationOwner.objects.create(organization=o, organization_user=m)
p = Project.objects.create(name='Sample', slug='sample', organization=o)
t = APIToken.objects.create(user=u, token='proof-local-token')
t.add_permissions(['event:read', 'project:read', 'org:read'])
print('KEY=' + str(ProjectKey.objects.get(project=p).public_key))
print('PROJECT=' + str(p.pk))
'''
                output = run('docker', 'exec', gt, './manage.py', 'shell', '-c', seed)
                values = dict(line.split('=', 1) for line in output.splitlines() if line.startswith(('KEY=', 'PROJECT=')))
                config['ingest'] = f"{config['sentry']}/api/{values['PROJECT']}/store/?sentry_key={values['KEY']}&sentry_version=7"
                if args.backlog:
                    import concurrent.futures
                    import uuid
                    def ingest_old(index):
                        try:
                            raise ValueError('Existing error ' + str(index))
                        except ValueError as exc:
                            event = {'event_id': uuid.uuid4().hex, 'platform': 'python', 'level': 'error',
                                     'fingerprint': ['old-' + str(index)],
                                     'exception': {'values': [{'type': 'ValueError', 'value': str(exc)}]}}
                        req = urllib.request.Request(config['ingest'], data=json.dumps(event).encode(),
                                                     headers={'Content-Type': 'application/json'})
                        with urllib.request.urlopen(req, timeout=60) as response:
                            assert response.status == 200
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(ingest_old, range(500)))
                    count = run('docker', 'exec', gt, './manage.py', 'shell', '-c',
                                'from apps.issue_events.models import Issue; print(Issue.objects.count())')
                    assert count.splitlines()[-1] == '500', count
                    print('Sentry backlog | PASS | 500 distinct issues ingested into GlitchTip', flush=True)
            result = subprocess.run([sys.executable, __file__, '--child', tmp, json.dumps(config)], env=env, timeout=600)
            if result.returncode and args.sentry:
                print(run('docker', 'logs', gt)[-6000:])
            return result.returncode or (2 if args.posthog else 0)
        except Exception:
            for cid in containers:
                logs = subprocess.run(['docker', 'logs', '--tail', '20', cid], capture_output=True, text=True, timeout=10)
                print(logs.stdout + logs.stderr, file=sys.stderr)
            raise
        finally:
            helpers.cleanup_isolated_processes(root)
            for cid in reversed(containers):
                subprocess.run(['docker', 'rm', '-fv', cid], capture_output=True, timeout=30)
            if network:
                run('docker', 'network', 'rm', network)
            if fixture:
                fixture.shutdown()
                fixture.server_close()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(sys.argv[2], json.loads(sys.argv[3]))
    else:
        def interrupted(*_):
            raise RuntimeError('Proof interrupted')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        sys.exit(main())
