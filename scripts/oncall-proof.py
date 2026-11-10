#!/usr/bin/env python3
"""Local protocol proof. Requires Docker, OpenSSL, Python 3 and optional wt.

No live CCC server is used. The HTTP adapter below serves the production
On-call API with its real engine/store; it does not emulate any channel.
"""
import email
import imaplib
import json
import os
from pathlib import Path
import shutil
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


def child(root, web, smtp, imap):
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
        msg.set_content(text)
        with smtplib.SMTP_SSL('localhost', smtp, context=ssl.create_default_context()) as conn:
            conn.login('oncall', 'test-password')
            conn.send_message(msg)

    try:
        project = Path(root) / 'sample-app'
        project.mkdir()
        os.chdir(project)
        start = time.monotonic()
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
            '--set', 'owner=owner@example.test', '--set', 'username=oncall',
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
        # Real queue operations, with no worker session attached and no dispatch.
        manual = cli('report', 'Local proof manual report')
        assert 'Filed as ' in manual
        cli('customer', 'add', 'reporter@example.test')
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
        from watchtower import queue
        commit = run(os.environ['PROOF_GIT'], '-C', str(REPO), 'rev-parse', 'HEAD')
        queue.close(ref, resolution={'summary': 'Local protocol proof completed.', 'commit': commit})
        eng.tick(force=True)
        fixed = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'fixed' and a['ref'] == ref)
        assert mailbox('reporter') == [], 'Reporter was notified before approval'
        publish(f"{fixed['token']} yes")
        eng.tick(force=True)
        assert any('fixed' in str(m).lower() for m in mailbox('reporter')), 'fixed notice missing'
        results.append(('ticket / decision / fixed mailbox', 'PASS'))
        results.append(('agent execution', 'BOUNDARY: no agent session started'))
    except Exception as exc:
        results.append(('proof', 'FAIL: ' + str(exc)))
        raise
    finally:
        print('\nCheck                                  Result')
        for name, result in results:
            print(f'{name:38} {result}')
        server.shutdown()
        server.server_close()


def main():
    containers = []
    with tempfile.TemporaryDirectory(prefix='ccc-oncall-proof-') as tmp:
        root = Path(tmp)
        # Only these executables are visible to the isolated child. In particular,
        # macOS security cannot reach the user's Keychain, and agents cannot start.
        bins = root / 'bin'
        bins.mkdir()
        for name in ('python3', 'wt'):
            executable = shutil.which(name)
            if executable:
                (bins / name).symlink_to(executable)
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
            env = {k: v for k, v in os.environ.items() if not k.startswith(('CCC_', 'WT_', 'WATCHTOWER_', 'CODEX_', 'CLAUDE_'))}
            env.update(HOME=tmp, XDG_CONFIG_HOME=tmp, XDG_DATA_HOME=tmp, PATH=str(bins),
                       PYTHONPATH=os.pathsep.join(sys.path), CCC_FF_ONCALL='1',
                       WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_SELF_UPDATE='1',
                       SSL_CERT_FILE=str(cert), PROOF_GIT=shutil.which('git'))
            proc = subprocess.run([sys.executable, __file__, '--child', tmp,
                                   f'http://127.0.0.1:{http}', str(smtp), str(imap)], env=env, timeout=180)
            if proc.returncode:
                subprocess.run(["docker", "logs", mail_id], timeout=10)
            return proc.returncode
        finally:
            for cid in containers:
                run('docker', 'rm', '-f', cid)
            run('docker', 'network', 'rm', network)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
    else:
        sys.exit(main())
