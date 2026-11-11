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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oncall_capture import Capture, output_dir, cleanup_receipt

REPO = Path(__file__).resolve().parents[1]


def run(*args, **kw):
    proc = subprocess.run(args, text=True, capture_output=True, timeout=120, **kw)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


AGENT_MODEL = 'gpt-6.1-sol'


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


def listener_port(pid):
    """Find only this isolated process's loopback TCP listener on Linux.

    WatchTower logs the requested port (0), not the assigned port. Matching
    socket inodes avoids reserving a port or reading another service's state.
    """
    sockets = set()
    for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            target = fd.readlink().as_posix()
            if target.startswith('socket:['):
                sockets.add(target[8:-1])
        except OSError:
            pass
    ports = []
    for line in (Path('/proc') / str(pid) / 'net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        host, port = fields[1].split(':')
        if fields[3] == '0A' and host == '0100007F' and fields[9] in sockets:
            ports.append(int(port, 16))
    if len(ports) != 1:
        raise RuntimeError('Expected one private watcher loopback listener')
    return ports[0]


def start_watcher(root, argv, capture=False):
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
                if capture:
                    proc.proof_url = f'http://127.0.0.1:{listener_port(proc.pid)}'
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


def wait_automatic_worker(root, ref, wt, watcher, started, timeout=480, observed=None):
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
                if observed:
                    observed(rec)
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
    recorder = None
    dashboard = None

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

    def capture_mail(message, agent):
        # GreenMail has no webmail UI. Render decoded fields from the fetched
        # RFC822 source, and retain that source only in the ignored asset pack.
        parts = message.walk() if message.is_multipart() else [message]
        body = '\n'.join(part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                         for part in parts if part.get_content_type() == 'text/plain')
        payload = {'subject': str(message['Subject']), 'body': body,
                   'from': str(message['From']), 'to': str(message['To']),
                   'source': message.as_string(), 'agent': agent}
        subprocess.run([os.environ['PROOF_NODE'], str(REPO / 'scripts/oncall-mail-capture.cjs')],
                       input=json.dumps(payload), text=True, check=True, timeout=90)

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
            watcher = start_watcher(root, eng.wt.argv(), capture=os.environ.get('PROOF_CAPTURE') == '1')
        output = cli('setup', '--repo', str(project), '--channel', 'ntfy', '--set', 'server=' + web)
        topic = eng.config()["channels"][0]["topic"]
        assert topic.startswith("ccc-oncall-")
        assert eng.config()["repo"] == str(project)
        assert "On-call is on." in output
        print(output)
        if os.environ.get('PROOF_CAPTURE') == '1':
            if not with_agent:
                eng.wt._ok('config', '-q', eng.config()['queue'], '--auto-drain', 'off')
            minor = cli('minor', 'on')
            decisions = cli('decisions')
            rendered = output.replace(str(Path(root)), '[temporary directory]')
            rendered = rendered.replace(web, '[local ntfy server]')
            capture = {'setup': rendered, 'minor': minor, 'decisions': decisions, 'agent': with_agent}
            subprocess.run([os.environ['PROOF_NODE'], str(REPO / 'scripts/oncall-cli-capture.cjs')],
                           input=json.dumps(capture), text=True, check=True, timeout=60)
        print(f'Setup: {time.monotonic() - start:.2f}s; explicit folder and ntfy channel; preview supplied by environment.')
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
            if os.environ.get('PROOF_CAPTURE') == '1':
                subprocess.run([os.environ['PROOF_NODE'], str(REPO / 'scripts/oncall-ticket-capture.cjs'),
                                watcher.proof_url, eng.config()['queue'], ref, 'reported', '--fixture'], check=True, timeout=90)
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
            if os.environ.get('PROOF_CAPTURE') == '1':
                subprocess.run([os.environ['PROOF_NODE'], str(REPO / 'scripts/oncall-ticket-capture.cjs'),
                                watcher.proof_url, eng.config()['queue'], ref, 'fixed', '--fixture'], check=True, timeout=90)
            eng.tick(force=True)
            fixed = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'fixed' and a['ref'] == ref)
            assert mailbox('reporter') == [], 'Reporter was notified before approval'
            publish(f"{fixed['token']} yes")
            eng.tick(force=True)
            assert any('fixed' in str(m).lower() for m in mailbox('reporter')), 'fixed notice missing'
            if os.environ.get('PROOF_CAPTURE') == '1':
                capture_mail(mailbox('reporter')[-1], False)
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
            if os.environ.get('PROOF_CAPTURE') == '1':
                # A real dashboard reads the same isolated store. Ephemeral mode
                # leaves ticking to this existing protocol proof's engine.
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    dashboard_port = sock.getsockname()[1]
                log = (Path(root) / 'capture-dashboard.log').open('w')
                try:
                    dashboard = subprocess.Popen([sys.executable, str(REPO / 'server.py'), '--port', str(dashboard_port)],
                        env=dict(os.environ, CCC_EPHEMERAL='1', CCC_TELEMETRY_DISABLED='1',
                                 CCC_AUTO_UPDATE='0', CCC_BG_HELPER='0', CCC_CONV_READER='0',
                                 CCC_BOOT_WARM_DELAY_S='86400'), stdout=log, stderr=log,
                        cwd=project, start_new_session=True)
                finally:
                    log.close()
                deadline = time.monotonic() + 90
                while True:
                    if dashboard.poll() is not None:
                        raise RuntimeError('Isolated capture dashboard exited: ' + (Path(root) / 'capture-dashboard.log').read_text()[-3000:])
                    try:
                        port = listener_port(dashboard.pid)
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/oncall/status', timeout=2):
                            break
                    except (OSError, RuntimeError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Capture dashboard not ready')
                        threading.Event().wait(.5)
                recorder = Capture(web, topic)
                recorder.step('open')
                recorder.step('report_open', base=f'http://127.0.0.1:{port}')
            started = time.monotonic()
            report_at = time.time()
            mail('reporter@example.test', 'Addition bug', 'add(2, 3) returns -1 instead of 5. See test_sum.py.')
            eng.tick(force=True)
            report = next(r for r in eng.store.reports() if r.get('title') == 'Addition bug')
            agent_ref = report['ticket_ref']
            if recorder:
                recorder.step('mark', kind='report-filed', details={'ticket': agent_ref, 'at': report_at})
            rec = None
            print(f'Worker model: {model}; effort: medium; transport: {transport}', flush=True)
            try:
                if explicit:
                    rec = workers.spawn_run_once_worker(queue_name, agent_ref,
                                                        repo_path=str(project),
                                                        engine='codex', model=model)
                    wait_worker(rec)
                else:
                    rec = wait_automatic_worker(root, agent_ref, eng.wt, watcher, started,
                        observed=(lambda rec: recorder.step('mark', kind='worker-observed',
                            details={'at': time.time(), 'engine': rec.get('engine'),
                                     'model': rec.get('model')})) if recorder else None)
                assert rec.get('engine') == 'codex' and rec.get('model') == model, 'Observed worker engine/model differs from requested Codex model'
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
                fixed_at = time.time()
                eng.tick(force=True)
                if recorder:
                    recorder.step('mark', kind='fix-verified', details={'commit': commit, 'at': fixed_at})
                    recorder.step('report_fixed')
                fixed = next(a for a in eng.store.alerts(state='open')
                             if a['kind'] == 'fixed' and a['ref'] == agent_ref)
                assert len(mailbox('reporter')) == before_mail, 'Notice sent before approval'
                before_approval_at = time.time()
                if recorder:
                    assert any('fixed' in e.get('message', '').lower() for e in events()), 'Fixed alert absent from ntfy: ' + json.dumps({'events': events(), 'status': eng.status()})
                    sent = recorder.step('fixed_reply', text=f"{fixed['id']} yes")
                    approved_at = sent['sent_at']
                else:
                    approved_at = time.time()
                    publish(f"{fixed['token']} yes")
                eng.tick(force=True)
                received = mailbox('reporter')
                assert len(received) == before_mail + 1 and 'fixed' in str(received[-1]).lower()
                delivered_at = time.time()
                if os.environ.get('PROOF_CAPTURE') == '1':
                    recorder.step('fixed_result')
                    recorder.step('finish')
                    recorder.close()
                    recorder = None
                    capture_mail(received[-1], True)
                    (output_dir() / 'agent-proof.json').write_text(json.dumps({
                        'status': 'PASS', 'run_id': os.environ.get('PROOF_RUN_ID'),
                        'model': model, 'effort': 'medium', 'automatic_dispatch': not explicit,
                        'observed_worker_model': rec.get('model'), 'observed_worker_engine': rec.get('engine'),
                        'ticket': agent_ref, 'report_at': report_at, 'fix_verified_at': fixed_at,
                        'commit': commit, 'changed_files': ['sum.py'], 'unchanged_test_passed': True,
                        'mailbox_before_approval_at': before_approval_at, 'mailbox_before_count': before_mail,
                        'phone_approval_sent_at': approved_at, 'mailbox_received_at': delivered_at,
                        'mailbox_after_count': len(received), 'notice_held_until_approval': True}, indent=2))
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
        if recorder:
            recorder.close()
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
    parser.add_argument('--capture', action='store_true', help='Save real local timed browser frames and labelled CLI/mail stills')
    parser.add_argument('--model', default=AGENT_MODEL,
                        help=f'Codex model for --with-agent (default {AGENT_MODEL})')
    args = parser.parse_args()
    if args.with_agent and not args.explicit_start and not Path('/proc/self/environ').exists():
        # An automatically started worker detaches; only /proc lets the proof find and stop it.
        print('BOUNDARY: automatic agent start needs Linux /proc for cleanup. '
              'Running the protocol checks only; use --explicit-start for the agent stage.')
        args.with_agent = False
    def interrupted(signum, frame):
        raise RuntimeError(f'Interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    containers = []
    login_before = None
    with tempfile.TemporaryDirectory(prefix='ccc-oncall-proof-') as tmp:
        root = Path(tmp)
        # Only these executables are visible to the isolated child. In particular,
        # macOS security cannot reach the user's Keychain. Agent tools are opt-in.
        bins = root / 'bin'
        bins.mkdir()
        names = ['python3', 'wt']
        if args.capture and not args.with_agent:
            # require-puppeteer.js locates the shared dependency with git.
            names.append('git')
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
        try:
            run('docker', 'network', 'create', network)
            def container(image, ports, extra=(), command=()):
                name = network + '-' + str(len(containers))
                # Register before docker run, including download/start failures.
                containers.append(name)
                args = ['docker', 'run', '--name', name, '-d', '--network', network]
                for port in ports:
                    args += ['-p', f'127.0.0.1::{port}']
                cid = run(*args, *extra, image, *command)
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
            (root / 'tmp').mkdir(exist_ok=True)
            env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
            env.update(HOME=tmp, TMPDIR=str(root / 'tmp'), XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp, PATH=str(bins),
                       CCC_TELEMETRY_DISABLED='1', CCC_AUTO_UPDATE='0',
                       PYTHONPATH=os.pathsep.join(sys.path), CCC_FF_ONCALL='1',
                       WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
                       # The proof runs its own foreground watcher; newer WatchTower
                       # honours this and never starts a detached one of its own.
                       WT_NO_AUTOSTART='1',
                       WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1',
                       GIT_CONFIG_GLOBAL=os.devnull,
                       SSL_CERT_FILE=str(trust), PROOF_GIT=shutil.which('git'),
                       PROOF_MODEL=args.model, PROOF_EXPLICIT_START='1' if args.explicit_start else '0')
            env.update(CCC_STATE_DIR=str(root / 'state'), CCC_ONCALL_STATE_DIR=str(root / 'oncall'))
            for key in ('PROOF_CAPTURE_DIR', 'PROOF_RUN_ID'):
                if key in os.environ:
                    env[key] = os.environ[key]
            if args.capture:
                node = shutil.which('node')
                chrome = run(node, '-e', "console.log(require('./puppeteer-browser-config.js').findChromePath() || '')", cwd=REPO) if node else ''
                if not chrome or not node:
                    raise RuntimeError('Capture requires Node and cached Puppeteer Chrome')
                env.update(PROOF_CAPTURE='1', PROOF_NODE=node,
                           PROOF_WITH_AGENT='1' if args.with_agent else '0',
                           WATCHTOWER_MACHINE='proof',
                           PUPPETEER_EXECUTABLE_PATH=chrome)
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
            errors = []
            for cid in containers:
                try:
                    run('docker', 'rm', '-f', cid)
                except RuntimeError as exc:
                    if 'No such container' not in str(exc):
                        errors.append(str(exc))
            try:
                run('docker', 'network', 'rm', network)
            except RuntimeError as exc:
                if 'not found' not in str(exc) and 'No such network' not in str(exc):
                    errors.append(str(exc))
            if args.capture:
                cleanup_receipt(root, containers_removed=len(containers), networks_removed=1,
                                docker_cleanup_errors=errors)
            if errors:
                raise RuntimeError('Private Docker cleanup failed: ' + '; '.join(errors))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), '--with-agent' in sys.argv[6:])
    else:
        sys.exit(main())
