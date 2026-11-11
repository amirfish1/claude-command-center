#!/usr/bin/env python3
"""Replay user commands in twelve guides. Full mode also runs paid local proofs.

Requires Docker, OpenSSL and WatchTower. --quick uses no model credentials.
"""
import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
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
        command = lines.pop(0)[2:]
        while command.rstrip().endswith('\\'):
            if not lines:
                raise ValueError('Unfinished command continuation')
            command += '\n' + lines.pop(0)
        if not lines or any(line.startswith('$ ') for line in lines):
            raise ValueError('Use one command and at least one output line per fence')
        result.append(Check(command, lines, bool(match[1])))
    if text.count('```console checked') != len(result):
        raise ValueError('Malformed checked fence')
    return result


def matches(output, expected):
    """Allow trimmed output, but preserve line content and order."""
    lines = iter(output.splitlines())
    return all(any(line == want for line in lines) for want in expected)


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'scripts' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def child(root, guide, capture=False):
    import email
    import html
    import imaplib
    import ssl
    import smtplib
    import time
    import urllib.parse
    import urllib.request
    from email.message import EmailMessage
    sys.path.insert(0, str(REPO))
    from ccc_server.oncall import api, engine, wt
    proof = module('oncall-proof')
    project = root / 'project'
    project.mkdir()
    os.chdir(project)
    eng = engine.Engine(root / 'oncall', wt=wt.WT(dispatch=False))
    slug = guide.stem
    result = {'guide': slug, 'commands': [], 'checks': []}
    source_live = False
    viewer = ''
    now = [time.time()]
    eng.clock = lambda: now[0]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.handle_api('GET')
        def do_POST(self):
            self.handle_api('POST')
        def handle_api(self, method):
            parsed = urllib.parse.urlsplit(self.path)
            route = parsed.path
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            if method == 'GET':
                body = dict(urllib.parse.parse_qsl(parsed.query))
            content_type = 'application/json'
            if route == '/result':
                code, raw, content_type = 200, viewer.encode(), 'text/html; charset=utf-8'
            elif route.startswith('/static/'):
                target = REPO / 'static' / Path(route).name
                code, raw = 200, target.read_bytes()
                content_type = 'text/css; charset=utf-8' if target.suffix == '.css' else 'text/javascript; charset=utf-8'
            elif route == '/settings':
                code, content_type = 200, 'text/html'
                raw = b'<html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/app.css"></head><body><div id="settingsPane"></div><script>window.__CCC_FLAGS__={oncall:true};window.cccSettingsDestinations=[];</script><script src="/static/oncall.js"></script></body></html>'
            else:
                if route.endswith('/actions/runs'):
                    payload = {'workflow_runs': [{'id': 2 if source_live else 1, 'workflow_id': 1, 'name': 'Python tests', 'status': 'completed', 'head_branch': 'main', 'head_sha': 'example-red' if source_live else 'example-green', 'conclusion': 'failure' if source_live else 'success'}]}
                    code = 200
                elif route.endswith('/jobs'):
                    code, payload = 200, {'jobs': []}
                elif route == '/repos/example-org/sample':
                    code, payload = 200, {'default_branch': 'main'}
                elif route.startswith('/repos/'):
                    code, payload = 200, ([{'number': 1, 'title': 'Empty cart crashes', 'body': 'The cart fails with no items.',
                        'user': {'login': 'reporter', 'id': 1}, 'author_association': 'MEMBER'}] if source_live else [])
                elif route.startswith('/api/0/'):
                    code, payload = 200, ([{'id': '1', 'title': 'Empty cart crashes', 'count': '1', 'status': 'unresolved',
                                           'project': {'slug': 'sample'}}] if source_live else [])
                else:
                    code, payload = api.handle(method, route, body, eng=eng)
                raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    os.environ.update(CCC_SERVER=base, SOURCE_URL=base, MAIL_HOST='localhost',
                      NTFY_TOPIC='guide-' + slug, TICKET='SHOP-1')
    watcher = None
    outputs = []
    def mail():
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = 'reporter@example.test', 'oncall@example.test', 'Empty cart crashes'
        msg.set_content('The empty cart crashes instead of showing a total.')
        with smtplib.SMTP_SSL('localhost', int(os.environ['SMTP_PORT']), context=ssl.create_default_context()) as conn:
            conn.login('oncall', 'test-password')
            conn.send_message(msg)
    def mailbox():
        conn = imaplib.IMAP4_SSL('localhost', int(os.environ['IMAP_PORT']), ssl_context=ssl.create_default_context())
        conn.login('reporter', 'test-password')
        conn.select('INBOX', readonly=True)
        ids = conn.search(None, 'ALL')[1][0].split()
        out = [email.message_from_bytes(conn.fetch(i, '(RFC822)')[1][0][1]) for i in ids]
        conn.logout()
        return out
    def publish(text):
        url = os.environ['NTFY_SERVER'] + '/' + os.environ['NTFY_TOPIC'] + '-reply'
        with urllib.request.urlopen(urllib.request.Request(url, data=text.encode()), timeout=10) as response:
            json.load(response)
    def events():
        url = os.environ['NTFY_SERVER'] + '/' + os.environ['NTFY_TOPIC'] + '/json?poll=1&since=all'
        with urllib.request.urlopen(url, timeout=10) as response:
            return [json.loads(line) for line in response if line.strip()]
    def block(ref, question='Should the empty cart show zero or hide the total?'):
        eng.wt._ok('block', ref, '--question', question, '--wait-on', 'owner')
        eng.tick(force=True)
        assert any(question in e.get('message', '') for e in events())
    def received_page(title, text):
        return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<style>body{font:16px/1.5 system-ui;margin:24px;background:#fafafa;color:#172823}h1{font-size:22px}'
            'pre{font:15px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>' + html.escape(title) + '</h1><pre>' + html.escape(text) + '</pre>')
    try:
        watcher = proof.start_watcher(root, eng.wt.argv())
        # Store only obvious test values in this isolated vault.
        from ccc_server import vault
        vault.vault_create_entry('guide-mail', 'test-password')
        vault.vault_create_entry('guide-sentry', 'test-only')
        if slug == 'agents-this-week':
            cfg = eng.config()
            cfg.update(queue='SHOP')
            eng.save_config(cfg)
            eng.wt._ok('config', '-q', 'SHOP', '--auto-drain', 'off')
            eng.report('The empty cart crashes')
            now[0] += 1
        for check in extract(guide.read_text()):
            command = check.command
            if not command.startswith(('ccc oncall ', 'curl ')):
                raise ValueError('Unsupported guide command: ' + command)
            if ' oncall decisions' in command:
                subprocess.run(['git', 'init', '-q'], check=True)
                (project / '.gitignore').write_text('__pycache__/\n')
                subprocess.run(['git', 'add', '.gitignore'], check=True)
                subprocess.run(['git', '-c', 'user.name=Example', '-c', 'user.email=worker@example.test', 'commit', '-qm', 'chore: ignore Python cache files'], check=True)
                commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
                ref = eng.report('Ignore generated Python cache files')
                eng.wt._ok('block', ref, '--worker', 'guide-worker', '--question', 'Ignore generated Python cache files?',
                    '--weight', 'minor', '--class', 'code-change-revertable', '--confidence', '0.99',
                    '--recommend', 'Ignore generated Python cache files.', '--commit', commit, '--undo', 'git revert ' + commit,
                    '--attest', 'money=no,customer-sends=no,public-posts=no,ad-spend=no,irreversible=no')
            if ' oncall tell ' in command:
                eng.tick(force=True)
                mail()
                eng.tick(force=True)
                assert eng.store.reports(state='filed'), 'Email report not filed'
            before_mail = len(mailbox()) if ' oncall tell ' in command else None
            command_start = time.monotonic()
            done = subprocess.run(['bash', '-o', 'pipefail', '-c', command], text=True,
                input='zero\nyes\n' if ' oncall demo' in command else '', capture_output=True, timeout=60)
            outputs.append(done.stdout)
            result['commands'].append({'command': check.command, 'output': done.stdout, 'code': done.returncode, 'seconds': round(time.monotonic()-command_start, 3)})
            # Setup configures auto-drain as it does for a user. Stop dispatch in the
            # test queue before introducing any fixture report; no agent is installed.
            if ' oncall setup ' in command:
                eng.wt._ok('config', '-q', 'SHOP', '--auto-drain', 'off', '--workers', '0')
                eng.tick(force=True)
            if done.returncode:
                raise RuntimeError(command + '\n' + done.stdout + done.stderr)
            if not matches(done.stdout, check.expected):
                raise AssertionError(command + '\nExpected: ' + repr(check.expected) + '\nActual:\n' + done.stdout)
            if ' oncall tell ' in command:
                assert len(mailbox()) == before_mail, 'Reporter received mail before approval'
                eng.tick(force=True)
                alert = next(a for a in eng.store.alerts(state='open') if a['kind'] == 'send')
                publish(str(alert['token']) + ' yes')
                eng.tick(force=True)
                messages = mailbox()
                assert len(messages) == before_mail + 1
                msg = messages[-1]
                body = msg.get_payload(decode=True).decode('utf-8', errors='replace') if not msg.is_multipart() else '\n'.join(
                    p.get_payload(decode=True).decode('utf-8', errors='replace') for p in msg.walk() if p.get_content_type() == 'text/plain')
                assert 'The empty-cart fix is ready to try.' in body
                viewer = received_page('Received mail', 'From: ' + msg['From'] + '\nTo: ' + msg['To'] + '\nSubject: ' + msg['Subject'] + '\n\n' + body)
                result['checks'].append('Reporter mailbox empty of this notice before approval; received after yes')
        if slug == 'fix-email-reports':
            eng.tick(force=True)
            mail()
            eng.tick(force=True)
            report = eng.store.reports(state='filed')[0]
            block(report['ticket_ref'])
            result['checks'].append('Real SMTP report filed; ticket question delivered to real ntfy')
        if slug in ('github-issues', 'sentry-errors', 'fix-red-build'):
            eng.tick(force=True)
            source_live = True
            eng.tick(force=True)
            report = eng.store.reports(state='filed')[0]
            block(report['ticket_ref'], 'The main build is red. Should the empty cart show zero?' if slug == 'fix-red-build' else 'Should the empty cart show zero or hide the total?')
            result['checks'].append('Local API issue filed; ticket question delivered to real ntfy')
        if slug in ('answer-codex-from-phone', 'approval-boundaries'):
            ref = eng.report('Decide how the empty cart should work')
            block(ref, 'Approve a customer message before sending it?' if slug == 'approval-boundaries' else 'Should the empty cart show zero or hide the total?')
        if slug == 'daily-summary':
            from datetime import datetime, timedelta
            ref = eng.report('The empty cart crashes')
            eng.wt._ok('block', ref, '--question', 'Should the empty cart show zero?', '--wait-on', 'owner')
            next_day = datetime.fromtimestamp(now[0]) + timedelta(days=1)
            now[0] = next_day.replace(hour=9, minute=0, second=1, microsecond=0).timestamp()
            eng.tick(force=True)
            eng.tick(force=True)
            summaries = [e for e in events() if 'Yesterday:' in e.get('message', '')]
            assert len(summaries) == 1, summaries
            result['checks'].append('One daily summary delivered across two ticks at next-day 09:00')
        if slug == 'agents-this-week':
            viewer = received_page('Your agents this week', outputs[-1])
        if slug not in ('agents-this-week', 'try-oncall'):
            assert any('This is a test' in event.get('message', '') for event in events())
            result['checks'].append('Setup test message received from the real ntfy cache')
        if capture:
            out = REPO / 'docs/guides/img' / (slug + '.png')
            target, mode = os.environ['NTFY_SERVER'] + '/' + os.environ['NTFY_TOPIC'], 'ntfy'
            if slug in ('agents-this-week', 'approve-fixed-message'):
                target, mode = base + '/result', 'result'
            if slug == 'small-decisions':
                target, mode = base + '/settings', 'decisions'
            if slug == 'try-oncall':
                target, mode = base, 'demo'
            subprocess.run(['node', str(REPO / 'scripts/capture-guides.js'), mode, target, str(out)], check=True, timeout=60)
            assert out.stat().st_size < 300000
        if slug == 'small-decisions':
            subprocess.run(['git', '-c', 'user.name=Example', '-c', 'user.email=worker@example.test', 'revert', '--no-edit', commit], check=True, capture_output=True)
            assert not (project / '.gitignore').exists(), 'Recorded undo did not reverse the change'
            result['checks'].append('Recorded git revert removed the cache-ignore change')
        print(json.dumps(result), flush=True)
    finally:
        proof.stop_watcher(watcher)
        eng.store.close()
        server.shutdown()
        server.server_close()


def run_proof(argv):
    """Let the proof clean up its own containers and detached workers on cancel."""
    import signal
    proc = subprocess.Popen(argv, cwd=REPO, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = proc.communicate()
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGINT)
            proc.communicate(timeout=180)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, output, '')


def run_guides(args):
    import time
    lab = module('guide-lab')
    proof = module('oncall-proof')
    guides = [p for p in sorted((REPO / 'docs/guides').glob('*.md')) if p.name != 'README.md']
    if args.guide:
        guides = [p for p in guides if p.stem == args.guide]
    if not guides:
        raise RuntimeError('No guides selected')
    results = []
    started = time.monotonic()
    with lab.servers() as settings:
        if args.capture:
            settings['SNAPSHOT_CHROME'] = lab.run('node', '-e', "console.log(require('./puppeteer-browser-config').findChromePath())", cwd=REPO)
        def check_guide(guide):
            with tempfile.TemporaryDirectory(prefix='ccc-guide-') as tmp:
                root = Path(tmp)
                env = lab.environment(root, settings)
                try:
                    done = subprocess.run([sys.executable, __file__, '--child', tmp, str(guide),
                        *(['--capture'] if args.capture else [])], env=env, text=True, capture_output=True, timeout=120)
                    if done.returncode:
                        raise RuntimeError(guide.stem + '\n' + done.stdout + done.stderr)
                    row = json.loads(done.stdout.splitlines()[-1])
                    print(f"PASS {guide.stem}: {len(row['commands'])} commands; {len(row['checks'])} result checks", flush=True)
                    return row
                finally:
                    lab.cleanup(root)
        # Mail examples share one mailbox, so run them serially. Other guides
        # have independent topics, state and queues and can run concurrently.
        from concurrent.futures import ThreadPoolExecutor
        mail_guides = [p for p in guides if p.stem in ('fix-email-reports', 'approve-fixed-message')]
        other_guides = [p for p in guides if p not in mail_guides]
        with ThreadPoolExecutor(max_workers=4 if not args.capture else 2) as pool:
            futures = [pool.submit(check_guide, p) for p in other_guides]
            for p in mail_guides:
                results.append(check_guide(p))
            for future in futures:
                results.append(future.result())
    if args.record_results:
        Path(args.record_results).write_text(json.dumps(results, indent=2))
    print(f'Guide commands: {time.monotonic() - started:.2f}s; ' + ('model proofs skipped (--commands-only)' if args.commands_only else 'running model proofs'), flush=True)
    if not args.commands_only:
        for script, extra in [('oncall-proof', ['--with-agent']), ('oncall-session-proof', ['--with-agent']),
                              ('oncall-monitoring-proof', ['--sentry', '--with-agent'])]:
            relevant = {'oncall-proof': {'fix-email-reports', 'approve-fixed-message', 'self-host-ntfy'},
                        'oncall-session-proof': {'answer-codex-from-phone'},
                        'oncall-monitoring-proof': {'sentry-errors'}}
            if args.guide and args.guide not in relevant[script]:
                continue
            done = run_proof([sys.executable, str(REPO / 'scripts' / (script + '.py')), *extra])
            required = {'oncall-proof': 'PASS (automatic WatchTower dispatch)',
                        'oncall-session-proof': 'reply user turn / close | PASS',
                        'oncall-monitoring-proof': 'automatic fix / ntfy | PASS'}[script]
            if done.returncode or required not in done.stdout or 'FAIL' in done.stdout:
                raise RuntimeError(script + '\n' + done.stdout + done.stderr)
            print('PASS real model proof: ' + script, flush=True)
        # The stock CI proof also contacts public GitHub. Call only its isolated
        # local stage here, so guide checking makes no external account requests.
        if args.guide and args.guide != 'fix-red-build':
            return
        done = run_proof([sys.executable, __file__, '--local-ci'])
        if done.returncode or 'Owner fixed notice, unchanged workflow and one test, normal push verified' not in done.stdout:
            raise RuntimeError(done.stdout + done.stderr)
        print('PASS real model proof: local CI', flush=True)


def quick_checks(args):
    """No Docker or model connection: replay standalone demo and stored counts.

    Transport commands remain checked by default/full and --commands-only.
    Every guide is parsed here; skipped transport steps are named explicitly.
    """
    import time
    lab = module('guide-lab')
    started = time.monotonic()
    cache = {}
    for guide in sorted((REPO / 'docs/guides').glob('*.md')):
        if guide.name == 'README.md' or args.guide and guide.stem != args.guide:
            continue
        checks = extract(guide.read_text())
        selected = [c for c in checks if c.command in ('ccc oncall demo', 'ccc oncall week')]
        for check in selected:
            if check.command not in cache:
                with tempfile.TemporaryDirectory(prefix='ccc-guide-quick-') as tmp:
                    root = Path(tmp)
                    env = lab.environment(root, {})
                    try:
                        if check.command == 'ccc oncall week':
                            done = subprocess.run([sys.executable, __file__, '--child', tmp, str(guide)],
                                env=env, text=True, capture_output=True, timeout=45)
                            if done.returncode:
                                raise RuntimeError(done.stdout + done.stderr)
                            cache[check.command] = json.loads(done.stdout.splitlines()[-1])['commands'][0]['output']
                        else:
                            done = subprocess.run([sys.executable, str(REPO / 'ccc'), 'oncall', 'demo'],
                                cwd=root, env=env, input='zero\nyes\n', text=True, capture_output=True, timeout=45)
                            if done.returncode:
                                raise RuntimeError(done.stdout + done.stderr)
                            cache[check.command] = done.stdout
                    finally:
                        lab.cleanup(root)
            if not matches(cache[check.command], check.expected):
                raise AssertionError(guide.name + ': standalone command output changed')
        skipped = len(checks) - len(selected)
        print(f"{'PASS' if selected else 'PARSED'} {guide.stem}: {len(selected)} fast commands; {skipped} transport commands skipped", flush=True)
    print(f'Quick checks: {time.monotonic()-started:.2f}s; run without --quick for every command and model proofs.')


def main():
    import signal
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Parse all guides and replay demo/week only; no Docker or model login')
    parser.add_argument('--commands-only', action='store_true', help='Replay all commands against local servers, without paid model proofs')
    parser.add_argument('--capture', action='store_true', help='Write real browser result captures to docs/guides/img')
    parser.add_argument('--guide', help='Check just this guide slug')
    parser.add_argument('--record-results', help='Write command outputs to this JSON file')
    args = parser.parse_args()
    if args.quick:
        if args.capture:
            parser.error('Use --commands-only --capture for browser captures')
        quick_checks(args)
    else:
        run_guides(args)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(Path(sys.argv[2]), Path(sys.argv[3]), '--capture' in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == '--local-ci':
        module('oncall-ci-proof').local()
    else:
        main()
