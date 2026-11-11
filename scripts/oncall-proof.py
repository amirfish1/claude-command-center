#!/usr/bin/env python3
"""Local protocol proof. Requires Docker, OpenSSL, Python 3 and optional wt.

No live CCC server is used. The HTTP adapter below serves the production
On-call API with its real engine/store; it does not emulate any channel.
"""
import argparse
import email
import imaplib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[1]


def run(*args, **kw):
    proc = subprocess.run(args, text=True, capture_output=True, timeout=120, **kw)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


AGENT_MODEL = 'gpt-6.1-terra'


def _watchtower_workers():
    try:
        from watchtower import workers
    except ImportError:
        return None
    return workers


def seed_repo(project):
    """A real failing test, with no remote or inherited git hooks."""
    git = os.environ['PROOF_GIT']
    run(git, 'init', str(project))
    run(git, '-C', str(project), 'config', 'user.name', 'Proof Worker')
    run(git, '-C', str(project), 'config', 'user.email', 'worker@example.test')
    (project / 'sum.py').write_text('def add(a, b):\n    return a - b\n')
    (project / 'test_sum.py').write_text(
        'import unittest\nfrom sum import add\n\n'
        'class AdditionTest(unittest.TestCase):\n'
        '    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n')
    (project / 'AGENTS.md').write_text(
        'Fix only sum.py. Do not modify tests. Run python3 -m unittest -v.\n'
        'Commit locally and close the assigned ticket with that commit.\n'
        'There is no remote; do not push. Do not delegate or use the network.\n'
        'Use only this repository and the isolated queue. Stop after one ticket.\n')
    run(git, '-C', str(project), 'add', 'sum.py', 'test_sum.py', 'AGENTS.md')
    run(git, '-C', str(project), 'commit', '--only', 'sum.py', 'test_sum.py',
        'AGENTS.md', '-m', 'test: seed addition bug')
    failed = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v'],
                            cwd=project, capture_output=True, text=True)
    assert failed.returncode == 1 and 'AssertionError: -1 != 5' in failed.stderr
    return run(git, '-C', str(project), 'rev-parse', 'HEAD')


def wait_worker(rec, timeout=240):
    """Wait on process completion, with a hard timeout and process-group cleanup."""
    def expired(*_):
        raise TimeoutError(f'WatchTower worker exceeded {timeout} seconds')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(timeout)
    try:
        _, status = os.waitpid(rec['pid'], 0)
        assert os.waitstatus_to_exitcode(status) == 0, 'Worker failed; see worker log'
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        try:
            os.killpg(rec['pid'], signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(rec['pid'], 0)
        except ChildProcessError:
            pass


def start_watcher(root, argv):
    """Use the foreground service entry point, never the host service manager."""
    log = Path(root) / 'watcher.log'
    with log.open('wb') as stream:
        proc = subprocess.Popen([*argv, 'start', '--foreground', '--auto-spawn',
                                 '--host', '127.0.0.1', '--port', '0'],
                                stdout=stream, stderr=subprocess.STDOUT,
                                start_new_session=True)
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(log.read_text())
            if 'HTTP server on' in log.read_text():
                return proc
            threading.Event().wait(.1)
        raise TimeoutError('Isolated WatchTower watcher did not start')
    except BaseException:
        proc.kill()
        proc.wait()
        raise


def stop_watcher(proc):
    if proc is not None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def cleanup_isolated_processes(root):
    """Kill only Linux processes carrying this proof's unique HOME marker.

    Workers create their own sessions, so killing the watcher's process group
    alone does not clean them up. This also covers a timed-out proof child.
    """
    if not Path('/proc/self/environ').exists():
        return
    marker = ('HOME=' + str(root)).encode()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if marker in (entry / 'environ').read_bytes().split(b'\0'):
                os.kill(int(entry.name), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def wait_automatic_worker(root, ref, wt, watcher, started, timeout=480):
    """One bounded observer; it never dispatches, claims or changes policy."""
    record_file = Path(root) / '.watchtower/workers.json'
    deadline = time.monotonic() + timeout
    rec = None
    while time.monotonic() < deadline:
        if watcher.poll() is not None:
            raise RuntimeError('Isolated WatchTower watcher exited')
        if rec is None and record_file.exists():
            records = json.loads(record_file.read_text()).get('workers', [])
            if records:
                rec = records[0]
                print(f'Report to observed worker start: {time.monotonic() - started:.2f}s', flush=True)
                print(f"Observed worker: {rec.get('worker_id')}; pid: {rec.get('pid')}; "
                      f"engine: {rec.get('engine')}; model: {rec.get('model')}", flush=True)
        if rec and (wt.find(ref) or {}).get('status') == 'closed':
            return rec
        threading.Event().wait(1)
    logs = '\n'.join(p.read_text(errors='replace')[-12000:]
                     for p in (Path(root) / '.watchtower/logs').glob('*') if p.is_file())
    raise TimeoutError('Automatic dispatch/fix exceeded 480 seconds.\n'
                       + (Path(root) / 'watcher.log').read_text()[-12000:] + logs)


def child(root, web, smtp, imap, with_agent=False):
    sys.path.insert(0, str(REPO))
    from ccc_server.oncall import api, engine, wt
    eng = engine.Engine(Path(root) / 'oncall', wt=wt.WT(dispatch=False))

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
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ['CCC_SERVER'] = f'http://127.0.0.1:{server.server_port}'
    results = []
    watcher = None

    def cli(*args, input=''):
        return run(sys.executable, str(REPO / 'ccc'), 'oncall', *args, input=input)

    def publish(text):
        with urllib.request.urlopen(urllib.request.Request(web + '/' + topic + '-reply', data=text.encode()), timeout=10) as res:
            return json.load(res)

    def events():
        with urllib.request.urlopen(web + '/' + topic + '/json?poll=1&since=all', timeout=10) as res:
            return [json.loads(line) for line in res if line.strip()]

    def mailbox(user):
        conn = imaplib.IMAP4_SSL('localhost', imap, ssl_context=ssl.create_default_context())
        conn.login(user, 'test-password')
        conn.select('INBOX', readonly=True)
        ids = conn.search(None, 'ALL')[1][0].split()
        out = [email.message_from_bytes(conn.fetch(i, '(RFC822)')[1][0][1]) for i in ids]
        conn.logout()
        return out

    def mail(sender, subject, text, reply_to=''):
        import smtplib
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = sender, 'oncall@example.test', subject
        if reply_to:
            msg['In-Reply-To'] = reply_to
        # GreenMail does not perform DMARC. This is trusted receiver evidence
        # supplied by the local protocol fixture, not a DNS authentication proof.
        msg['Authentication-Results'] = 'fixture-mail; dmarc=pass header.from=example.test'
        msg.set_content(text)
        with smtplib.SMTP_SSL('localhost', smtp, context=ssl.create_default_context()) as conn:
            conn.login('oncall', 'test-password')
            conn.send_message(msg)

    try:
        project = Path(root) / 'sample-app'
        project.mkdir()
        os.chdir(project)
        start = time.monotonic()
        if eng.wt.available():
            watcher = start_watcher(root, eng.wt.argv())
        output = cli('setup', '--set', 'server=' + web, input='\n\n')
        topic = eng.config()["channels"][0]["topic"]
        assert topic.startswith("ccc-oncall-")
        assert output.count("Folder of the code agents should fix") == 1
        assert output.count("Pick a number") == 1
        print(output)
        print(f'Setup: {time.monotonic() - start:.2f}s; 2 prompts (folder, channel; preview supplied by environment).')
        assert any('This is a test' in e.get('message', '') for e in events()), 'ntfy test missing'
        eng.tick(force=True)  # establish inbound cursors before phone replies
        publish('pause')
        eng.tick(force=True)
        assert eng.store.kv_get('paused') is True, 'ntfy reply not routed'
        publish('resume')
        eng.tick(force=True)
        assert eng.store.kv_get('paused') is False
        results.append(('ntfy send / subscribe / reply', 'PASS'))
        cli('channel', 'add', 'email', '--intake', '--set', 'address=oncall@example.test',
            '--set', 'owner=owner@example.test', '--set', 'authserv_id=fixture-mail', '--set', 'username=oncall',
            '--set', 'smtp_host=localhost', '--set', f'smtp_port={smtp}', '--set', 'smtp_security=tls',
            '--set', 'imap_host=localhost', '--set', f'imap_port={imap}',
            '--secret-stdin', 'password', input='test-password\n')
        cli('test', 'email')
        assert any('CCC On-call test' in m['Subject'] for m in mailbox('owner'))
        eng.tick(force=True)
        mail('owner@example.test', 'status', 'status')
        tick = eng.tick(force=True)
        assert tick['inbound'] >= 1
        assert any('On-call' in str(m) and 'waiting' in str(m) for m in mailbox('owner'))
        results.append(('email SMTP / IMAP / reply', 'PASS'))
        if not eng.wt.available():
            results.append(('ticket / decision / fixed', 'BOUNDARY: install WatchTower; no agent started'))
            return
        from watchtower import queue
        cli('customer', 'add', 'reporter@example.test')
        if not with_agent:
            # Fixture closures must never become real agent work, even on a slow run.
            eng.wt._ok('config', '-q', eng.config()['queue'], '--auto-drain', 'off')
            manual = cli('report', 'Local proof manual report')
            assert 'Filed as ' in manual
            mail('reporter@example.test', 'Proof bug', 'The local proof button is broken.')
            eng.tick(force=True)
            report = next(r for r in eng.store.reports() if r.get('reporter_address') == 'reporter@example.test')
            ref = report['ticket_ref']
            assert ref
            eng.wt._ok('block', ref, '--question', 'Keep the fallback?', '--wait-on', 'owner')
            eng.tick(force=True)
            alert = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'decision' and a['ref'] == ref)
            msg = next(m for m in mailbox('owner') if alert['token'] in m['Subject'])
            mail('owner@example.test', 'Re: ' + msg['Subject'], 'Keep it.', msg['Message-ID'])
            eng.tick(force=True)
            assert not eng.wt.find(ref)['needs_input'], 'email decision reply not applied'
            # Queue closure is a fixture event, not a claim that an agent fixed code.
            commit = run(os.environ['PROOF_GIT'], '-C', str(REPO), 'rev-parse', 'HEAD')
            queue.close(ref, resolution={'summary': 'Local protocol proof completed.', 'commit': commit})
            eng.tick(force=True)
            fixed = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'fixed' and a['ref'] == ref)
            assert mailbox('reporter') == [], 'Reporter was notified before approval'
            publish(f"{fixed['token']} yes")
            eng.tick(force=True)
            assert any('fixed' in str(m).lower() for m in mailbox('reporter')), 'fixed notice missing'
            results.append(('ticket / decision / fixed mailbox', 'PASS'))
        if not with_agent:
            results.append(('agent execution', 'BOUNDARY: no agent session started'))
        elif not shutil.which('codex') or not (Path.home() / '.codex/auth.json').exists():
            results.append(('agent execution', 'BOUNDARY: Codex executable or login missing'))
        elif not _watchtower_workers():
            results.append(('agent execution', 'BOUNDARY: WatchTower is not importable from this Python'))
        else:
            workers = _watchtower_workers()
            model = os.environ.get('PROOF_MODEL') or AGENT_MODEL
            baseline = seed_repo(project)
            queue_name = eng.config()['queue']
            eng.wt._ok('config', '-q', queue_name,
                       '--engine', 'codex', '--model', model, '--effort', 'medium')
            from watchtower import config as wt_config
            transport = wt_config.codex_transport(queue_name)
            assert wt_config.auto_drain(queue_name), 'Setup did not enable auto-drain'
            explicit = os.environ.get('PROOF_EXPLICIT_START') == '1'
            if explicit:
                eng.wt._ok('config', '-q', queue_name, '--auto-drain', 'off',
                           '--codex-transport', 'exec')
                transport = 'exec'
            eng.wt._dispatch = not explicit
            before_mail = len(mailbox('reporter'))
            started = time.monotonic()
            mail('reporter@example.test', 'Addition bug', 'add(2, 3) returns -1 instead of 5. See test_sum.py.')
            eng.tick(force=True)
            report = next(r for r in eng.store.reports() if r.get('title') == 'Addition bug')
            agent_ref = report['ticket_ref']
            rec = None
            print(f'Worker model: {model}; effort: medium; transport: {transport}', flush=True)
            try:
                if explicit:
                    rec = workers.spawn_run_once_worker(queue_name, agent_ref,
                                                        repo_path=str(project),
                                                        engine='codex', model=model)
                    wait_worker(rec)
                else:
                    rec = wait_automatic_worker(root, agent_ref, eng.wt, watcher, started)
                item = eng.wt.find(agent_ref)
                assert item['status'] == 'closed', 'Worker did not close the ticket'
                commit = run(os.environ['PROOF_GIT'], '-C', str(project), 'rev-parse', 'HEAD')
                assert commit != baseline, 'No fix commit'
                assert run(os.environ['PROOF_GIT'], '-C', str(project), 'diff',
                           '--name-only', baseline, commit) == 'sum.py', 'Unexpected files changed'
                run(os.environ['PROOF_GIT'], '-C', str(project), 'diff', '--exit-code', 'HEAD',
                    '--', 'sum.py', 'test_sum.py', 'AGENTS.md')
                run(sys.executable, '-B', '-m', 'unittest', '-v', cwd=project)
                close_commits = [e.get('commit') for e in item.get('history', []) if e.get('event') == 'close']
                assert commit in close_commits or (item.get('resolution') or {}).get('commit') == commit
                eng.tick(force=True)
                fixed = next(a for a in eng.store.alerts(state='open')
                             if a['kind'] == 'fixed' and a['ref'] == agent_ref)
                assert len(mailbox('reporter')) == before_mail, 'Notice sent before approval'
                publish(f"{fixed['token']} yes")
                eng.tick(force=True)
                received = mailbox('reporter')
                assert len(received) == before_mail + 1 and 'fixed' in str(received[-1]).lower()
                print(f'Report to fixed notice: {time.monotonic() - started:.2f}s')
                print(f'Observed fix commit: {commit}')
                results.append(('agent execution / approved fixed notice', 'PASS (explicit drain once)' if explicit else 'PASS (automatic WatchTower dispatch)'))
            except Exception:
                results.append(('agent execution / approved fixed notice', 'FAIL'))
                print(f'Report to fixed notice: not completed ({time.monotonic() - started:.2f}s elapsed)')
                print(Path(rec['log']).read_text()[-12000:] if rec and 'log' in rec else str(rec))
                raise
    except Exception as exc:
        results.append(('proof', 'FAIL: ' + str(exc)))
        raise
    finally:
        print('\nCheck                                  Result')
        for name, result in results:
            print(f'{name:38} {result}')
        stop_watcher(watcher)
        cleanup_isolated_processes(root)
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-agent', action='store_true',
                        help='Run one paid Codex worker using an isolated copy of the login')
    parser.add_argument('--explicit-start', action='store_true',
                        help='With --with-agent, retain the explicit exec drain-once comparison')
    parser.add_argument('--model', default=AGENT_MODEL,
                        help=f'Codex model for --with-agent (default {AGENT_MODEL})')
    args = parser.parse_args()
    if args.with_agent and not args.explicit_start and not Path('/proc/self/environ').exists():
        # An automatically started worker detaches; only /proc lets the proof find and stop it.
        print('BOUNDARY: automatic agent start needs Linux /proc for cleanup. '
              'Running the protocol checks only; use --explicit-start for the agent stage.')
        args.with_agent = False
    containers = []
    login_before = None
    with tempfile.TemporaryDirectory(prefix='ccc-oncall-proof-') as tmp:
        root = Path(tmp)
        # Only these executables are visible to the isolated child. In particular,
        # macOS security cannot reach the user's Keychain. Agent tools are opt-in.
        bins = root / 'bin'
        bins.mkdir()
        names = ['python3', 'wt']
        if args.with_agent:
            names += ['codex', 'git', 'bash', 'sh', 'env', 'cat', 'ls', 'rg', 'sed']
        for name in names:
            executable = shutil.which(name)
            if executable:
                (bins / name).symlink_to(executable)
        if args.with_agent:
            codex_home = root / '.codex'
            codex_home.mkdir(mode=0o700)
            auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
            if auth.is_file():
                shutil.copyfile(auth, codex_home / 'auth.json')
                (codex_home / 'auth.json').chmod(0o600)
                login_before = auth.read_bytes()
        cert = root / 'cert.pem'
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(root / 'key.pem'), '-out', str(cert), '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1')
        run('openssl', 'pkcs12', '-export', '-inkey', str(root / 'key.pem'), '-in', str(cert),
            '-out', str(root / 'mail.p12'), '-passout', 'pass:changeit')
        (root / 'mail.p12').chmod(0o644)
        network = 'ccc-proof-' + root.name
        run('docker', 'network', 'create', network)
        try:
            def container(image, ports, extra=(), command=()):
                args = ['docker', 'run', '-d', '--network', network]
                for port in ports:
                    args += ['-p', f'127.0.0.1::{port}']
                cid = run(*args, *extra, image, *command)
                containers.append(cid)
                bound = json.loads(run('docker', 'inspect', cid))[0]['NetworkSettings']['Ports']
                return cid, [int(bound[f'{p}/tcp'][0]['HostPort']) for p in ports]
            _, (http,) = container('binwiederhier/ntfy:v2.17.0', [80], command=('serve',))
            mail_id, (smtp, imap) = container('greenmail/standalone:2.1.3', [3465, 3993], extra=(
                '-v', f'{root / "mail.p12"}:/tmp/proof.p12:ro', '-e',
                'GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 '
                '-Dgreenmail.tls.keystore.file=/tmp/proof.p12 -Dgreenmail.tls.keystore.password=changeit '
                '-Dgreenmail.users=oncall:test-password@example.test,owner:test-password@example.test,reporter:test-password@example.test'))
            # One bounded readiness watcher, no agent-side polling turns.
            context = ssl.create_default_context(cafile=str(cert))
            for port in (http, smtp, imap):
                deadline = time.monotonic() + 60
                while True:
                    try:
                        if port == http:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/v1/health', timeout=1) as response:
                                assert response.status == 200
                        else:
                            with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
                                with context.wrap_socket(sock, server_hostname='localhost') as tls:
                                    assert tls.recv(256), 'No protocol greeting'
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Local server did not become ready')
                        threading.Event().wait(.2)
            trust = root / 'trust.pem'
            system_ca = ssl.get_default_verify_paths().cafile
            trust.write_bytes(cert.read_bytes() + (Path(system_ca).read_bytes() if system_ca else b''))
            env = {k: os.environ[k] for k in ('LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
            env.update(HOME=tmp, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp, PATH=str(bins),
                       PYTHONPATH=os.pathsep.join(sys.path), CCC_FF_ONCALL='1',
                       WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
                       # The proof runs its own foreground watcher; newer WatchTower
                       # honours this and never starts a detached one of its own.
                       WT_NO_AUTOSTART='1',
                       WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull,
                       SSL_CERT_FILE=str(trust), PROOF_GIT=shutil.which('git'),
                       PROOF_MODEL=args.model, PROOF_EXPLICIT_START='1' if args.explicit_start else '0')
            proc = subprocess.run([sys.executable, __file__, '--child', tmp,
                                   f'http://127.0.0.1:{http}', str(smtp), str(imap), *(['--with-agent'] if args.with_agent else [])], env=env, timeout=600)
            copied = root / '.codex' / 'auth.json'
            if login_before is not None and copied.is_file() and copied.read_bytes() != login_before:
                # The renewed login lives only in the temporary copy and is discarded.
                print('Note: Codex renewed its login during the agent run. If Codex asks you to '
                      'sign in again, run: codex login')
            if proc.returncode:
                subprocess.run(["docker", "logs", mail_id], timeout=10)
            return proc.returncode
        finally:
            cleanup_isolated_processes(root)
            for cid in containers:
                run('docker', 'rm', '-f', cid)
            run('docker', 'network', 'rm', network)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), '--with-agent' in sys.argv[6:])
    else:
        sys.exit(main())
