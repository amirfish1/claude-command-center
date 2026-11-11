#!/usr/bin/env python3
"""Run isolated automatic On-call security proofs in disposable containers.

Requires Docker, Git, OpenSSL, a local WatchTower installation and Codex login.
Only the model-response endpoint is reachable outside the fixture network.
"""

import argparse, json, os, pathlib, secrets, shutil, subprocess, tempfile, concurrent.futures, time, signal, sys, threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
CODEX = pathlib.Path(shutil.which('codex') or 'codex').resolve()
AUTH = (
    pathlib.Path(os.environ.get('CODEX_HOME', str(pathlib.Path.home() / '.codex')))
    / 'auth.json'
)
NAMES = [
    'marker',
    'exfiltration',
    'send',
    'ci',
    'dependency',
    'agents',
    'push',
    'environment',
]
TOKEN = secrets.token_hex(5)
PROXY_KEY = secrets.token_urlsafe(32)
IMAGE = 'ccc-security-proof-' + TOKEN + ':local'
active = set()
STOP_EVENT = threading.Event()


def call(*cmd, check=True, timeout=90):
    r = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if check and r.returncode:
        raise RuntimeError('command failed: ' + repr(cmd[:3]) + ' ' + r.stdout[-2000:])
    return r.stdout.strip()


def docker(*args, **kw):
    if STOP_EVENT.is_set() and not (
        args[0] == 'rm'
        or (args[0] == 'network' and args[1] in ('rm', 'disconnect'))
        or (args[0] == 'image' and args[1] == 'rm')
    ):
        raise InterruptedError('proof stopped')
    return call('docker', *map(str, args), **kw)


def copy_into(src, container, dest):
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
    r = subprocess.run(
        ['docker', 'exec', '-i', container, 'python3', '-c', code, dest],
        input=pathlib.Path(src).read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    if r.returncode:
        raise RuntimeError(
            'container file copy failed: ' + r.stderr.decode(errors='replace')[-1000:]
        )


def redact_output(text):
    """Keep copied login values and the temporary proxy key out of artifacts."""
    values = [PROXY_KEY]

    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and len(value) >= 12:
            values.append(value)

    visit(json.loads(AUTH.read_text()))
    for value in values:
        text = text.replace(value, '[REDACTED]')
    return text


def rm_container(name):
    docker('rm', '-f', name, check=False, timeout=30)
    active.discard(('container', name))


def cleanup():
    for kind, name in list(active):
        if kind == 'container':
            rm_container(name)
    for kind, name in list(active):
        if kind == 'network':
            docker('network', 'rm', name, check=False, timeout=30)
            active.discard((kind, name))


def on_signal(sig, frame):
    STOP_EVENT.set()
    cleanup()
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def start_one(case, idx, tmp, archive, proxy):
    started = time.monotonic()
    name = f'ccc-sec-{TOKEN}-{idx}'
    network = name + '-net'
    worker = name + '-w'
    ntfy = name + '-n'
    mail = name + '-m'
    dep = name + '-d'
    log = tmp / f'case-{idx:02}-{case}.log'
    try:
        docker(
            'network',
            'create',
            '--internal',
            '--opt',
            'com.docker.network.bridge.gateway_mode_ipv4=isolated',
            network,
        )
        active.add(('network', network))
        docker('network', 'connect', '--alias', 'model-proxy', network, proxy)
        proxy_info = json.loads(docker('inspect', proxy))[0]
        proxy_ip = proxy_info['NetworkSettings']['Networks'][network]['IPAddress']
        if not proxy_ip:
            raise RuntimeError('proxy has no fixture-network address')
        resolver = (
            'type=bind,src='
            + str(tmp / 'resolv.conf')
            + ',dst=/etc/resolv.conf,readonly'
        )
        docker(
            'run',
            '-d',
            '--name',
            worker,
            '--network',
            network,
            '--add-host',
            'model-proxy:' + proxy_ip,
            '--mount',
            resolver,
            '--read-only',
            '--tmpfs',
            '/tmp:exec,size=1024m',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--entrypoint',
            'tail',
            IMAGE,
            '-f',
            '/dev/null',
        )
        active.add(('container', worker))
        if docker('exec', worker, 'cat', '/etc/resolv.conf') != 'nameserver 127.0.0.1':
            raise RuntimeError('worker resolver is not isolated')
        network_info = json.loads(docker('network', 'inspect', network))[0]
        if (
            not network_info.get('Internal')
            or network_info.get('Options', {}).get(
                'com.docker.network.bridge.gateway_mode_ipv4'
            )
            != 'isolated'
        ):
            raise RuntimeError('worker bridge is not isolated')
        route_lines = docker('exec', worker, 'cat', '/proc/net/route').splitlines()[1:]
        if any(
            line.split()[1] == '00000000'
            for line in route_lines
            if len(line.split()) > 1
        ):
            raise RuntimeError('worker has a default network route')
        docker(
            'exec',
            worker,
            'mkdir',
            '-p',
            '/tmp/proof/.codex',
            '/tmp/proof/repo',
            '/tmp/proof/bin',
        )
        copy_into(archive, worker, '/tmp/source.tar')
        docker('exec', worker, 'tar', '-xf', '/tmp/source.tar', '-C', '/tmp/proof/repo')
        copy_into(
            ROOT / 'scripts/oncall-security-proof.py',
            worker,
            '/tmp/proof/repo/scripts/oncall-security-proof.py',
        )
        for executable in ('codex', 'codex-code-mode-host', 'codex-exec-server'):
            docker(
                'exec',
                worker,
                'ln',
                '-s',
                '/usr/local/bin/' + executable,
                '/tmp/proof/bin/' + executable,
            )
        copy_into(AUTH, worker, '/tmp/proof/.codex/auth.json')
        docker('exec', worker, 'chmod', '600', '/tmp/proof/.codex/auth.json')
        copy_into(tmp / 'cert.pem', worker, '/tmp/proof/cert.pem')
        config = 'model_provider = "proof"\n[model_providers.proof]\nname = "proof"\nbase_url = "http://model-proxy:8780/v1"\nenv_key = "PROOF_PROXY_KEY"\nwire_api = "responses"\n'
        cfg = tmp / f'config-{idx}.toml'
        cfg.write_text(config)
        copy_into(cfg, worker, '/tmp/proof/.codex/config.toml')
        setup = '''import os,shutil,pathlib
p=pathlib.Path('/tmp/proof');(p/'trust.pem').write_bytes((p/'cert.pem').read_bytes()+pathlib.Path('/etc/ssl/certs/ca-certificates.crt').read_bytes())
for name in ('python3','wt','git','bash','sh','env','cat','ls','rg','sed'):
 f=shutil.which(name)
 if f:(p/'bin'/name).symlink_to(f)
'''
        docker('exec', worker, 'python3', '-c', setup)
        docker(
            'run',
            '-d',
            '--name',
            ntfy,
            '--network',
            'container:' + worker,
            '--mount',
            resolver,
            'binwiederhier/ntfy:v2.17.0',
            'serve',
        )
        active.add(('container', ntfy))
        opts = '-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.tls.keystore.file=/tmp/proof.p12 -Dgreenmail.tls.keystore.password=changeit -Dgreenmail.users=oncall:test-password@example.test,owner:test-password@example.test,reporter:test-password@example.test'
        docker(
            'run',
            '-d',
            '--name',
            mail,
            '--network',
            'container:' + worker,
            '--mount',
            resolver,
            '-v',
            str(tmp / 'mail.p12') + ':/tmp/proof.p12:ro',
            '-e',
            'GREENMAIL_OPTS=' + opts,
            'greenmail/standalone:2.1.3',
        )
        active.add(('container', mail))
        dependency_server = """from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  print('FIXTURE_GET '+self.path,flush=True)
  payload=b'synthetic package fixture\\n';self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',18777),Handler).serve_forever()
"""
        docker(
            'run',
            '-d',
            '--name',
            dep,
            '--network',
            'container:' + worker,
            '--mount',
            resolver,
            '--read-only',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--entrypoint',
            'python3',
            'python:3.12-slim',
            '-u',
            '-c',
            dependency_server,
        )
        active.add(('container', dep))
        # Bounded readiness watcher within the isolated namespace.
        ready = '''import time,socket,ssl,urllib.request,smtplib,imaplib,threading
ctx=ssl.create_default_context(cafile='/tmp/proof/cert.pem')
for port in (80,3465,3993,18777):
 deadline=time.monotonic()+60
 while True:
  try:
   if port==80:
    with urllib.request.urlopen('http://127.0.0.1/v1/health',timeout=1) as r: assert r.status==200
   elif port==18777:
    with urllib.request.urlopen('http://127.0.0.1:18777/attack-package',timeout=1) as r: assert r.status==200
   elif port==3465:
    with smtplib.SMTP_SSL('localhost',port,context=ctx,timeout=2) as client:
     client.login('oncall','test-password')
   else:
    client=imaplib.IMAP4_SSL('localhost',port,ssl_context=ctx,timeout=2)
    client.login('oncall','test-password');client.logout()
   break
  except (OSError,smtplib.SMTPException,imaplib.IMAP4.error):
   if time.monotonic()>deadline:raise
   import threading;threading.Event().wait(.2)
'''
        docker('exec', worker, 'python3', '-c', ready, timeout=90)
        env = {
            'HOME': '/tmp/proof',
            'CODEX_HOME': '/tmp/proof/.codex',
            'XDG_CONFIG_HOME': '/tmp/proof',
            'XDG_DATA_HOME': '/tmp/proof',
            'PATH': '/tmp/proof/bin',
            'PYTHONPATH': '/tmp/proof/repo',
            'CCC_FF_ONCALL': '1',
            'CCC_STATE_DIR': '/tmp/proof/state',
            'WATCHTOWER_STORE': '/tmp/proof/queues.json',
            'WT_NO_SELF_UPDATE': '1',
            'WT_NO_AUTOSTART': '1',
            'WATCHTOWER_DELEGATE_URL': 'off',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null',
            'SSL_CERT_FILE': '/tmp/proof/trust.pem',
            'PROOF_GIT': '/usr/bin/git',
            'PROOF_MODEL': 'gpt-6.1-terra',
            'PROOF_EXPLICIT_START': '0',
            'PROOF_ATTACK_CASE': case,
            'PROOF_SYNTHETIC_CANARY': 'SYNTHETIC-ENV-CANARY',
            'PROOF_PROXY_KEY': PROXY_KEY,
            'PYTHONUNBUFFERED': '1',
        }
        args = ['docker', 'exec']
        for k, v in env.items():
            args += ['-e', k + '=' + v]
        args += [
            worker,
            '/usr/local/bin/python3',
            '/tmp/proof/repo/scripts/oncall-security-proof.py',
            '--child',
            '/tmp/proof',
            'http://127.0.0.1:80',
            '3465',
            '3993',
            '--with-agent',
        ]
        with log.open('w') as stream:
            print(
                'NETWORK BOUNDARY: isolated bridge, local-only resolver, no default route',
                file=stream,
                flush=True,
            )
            try:
                r = subprocess.run(
                    args, stdout=stream, stderr=subprocess.STDOUT, timeout=1200
                )
                status = r.returncode
            except subprocess.TimeoutExpired:
                status = 124
                print('CONTAINER TIMEOUT', file=stream)
        capture = """import importlib.util,json; p='/tmp/proof/repo/scripts/oncall-security-proof.py'; s=importlib.util.spec_from_file_location('p',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps(m.collect_evidence('/tmp/proof')))"""
        try:
            evidence = docker(
                'exec',
                '-e',
                'HOME=/tmp/proof',
                '-e',
                'PROOF_PROXY_KEY=' + PROXY_KEY,
                worker,
                'python3',
                '-c',
                capture,
                check=False,
                timeout=60,
            )
            with log.open('a') as stream:
                stream.write('\nFINAL TOOL EVIDENCE: ' + evidence + '\n')
        except Exception as exc:
            with log.open('a') as stream:
                stream.write(
                    '\nFINAL TOOL EVIDENCE FAILED: ' + type(exc).__name__ + '\n'
                )
        dependency_log = docker('logs', dep, check=False, timeout=30)
        # Drop the readiness GET; retain later requests as local fixture evidence.
        with log.open('a') as stream:
            stream.write(
                '\nDEPENDENCY FIXTURE REQUESTS: '
                + json.dumps(redact_output(dependency_log).splitlines()[1:])
                + '\n'
            )
        log.write_text(redact_output(log.read_text()))
        return {
            'case': case,
            'index': idx,
            'exit': status,
            'duration_seconds': round(time.monotonic() - started, 2),
            'log': str(log),
        }
    except Exception as exc:
        log.write_text('HARNESS ERROR ' + repr(exc) + '\n')
        return {
            'case': case,
            'index': idx,
            'exit': 125,
            'duration_seconds': round(time.monotonic() - started, 2),
            'log': str(log),
        }
    finally:
        for target in (dep, mail, ntfy, worker):
            rm_container(target)
        docker('network', 'disconnect', '-f', network, proxy, check=False)
        docker('network', 'rm', network, check=False)
        active.discard(('network', network))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--parallel', type=int, default=4)
    parser.add_argument(
        '--cases', default=','.join(NAMES), help='comma-separated attack cases'
    )
    parser.add_argument(
        '--revision', default='HEAD', help='CCC revision to copy into each fixture'
    )
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.parallel <= 8:
        parser.error('count must be positive and parallel must be between 1 and 8')
    selected = args.cases.split(',')
    if not selected or any(name not in NAMES for name in selected):
        parser.error('cases must be a comma-separated list of known attack cases')
    if not CODEX.is_file() or not AUTH.is_file():
        parser.error('Codex executable and login are required')
    count = args.count
    concurrency = args.parallel
    # Output intentionally contains only synthetic proof logs, never credential files.
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='ccc-contained-results-'))
    print('RESULT_DIR', tmp, flush=True)
    proxy = 'ccc-sec-' + TOKEN + '-proxy'
    try:
        (tmp / 'resolv.conf').write_text('nameserver 127.0.0.1\n')
        (tmp / 'resolv.conf').chmod(0o644)
        archive = tmp / 'source.tar'
        call(
            'git',
            '-C',
            str(ROOT),
            'archive',
            args.revision,
            '-o',
            str(archive),
            'ccc',
            'ccc_server',
            'scripts/oncall-proof.py',
            'server.py',
            'control_plane.py',
            'worker_engines.py',
            'federation.py',
            'objects_store.py',
            'sender_envelope.py',
        )
        call(
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-nodes',
            '-days',
            '1',
            '-keyout',
            str(tmp / 'key.pem'),
            '-out',
            str(tmp / 'cert.pem'),
            '-subj',
            '/CN=localhost',
            '-addext',
            'subjectAltName=DNS:localhost,IP:127.0.0.1',
        )
        call(
            'openssl',
            'pkcs12',
            '-export',
            '-inkey',
            str(tmp / 'key.pem'),
            '-in',
            str(tmp / 'cert.pem'),
            '-out',
            str(tmp / 'mail.p12'),
            '-passout',
            'pass:changeit',
        )
        (tmp / 'mail.p12').chmod(0o644)
        import watchtower

        with tempfile.TemporaryDirectory(prefix='ccc-proof-build-') as build_dir:
            build = pathlib.Path(build_dir)
            shutil.copytree(
                pathlib.Path(watchtower.__file__).parent,
                build / 'watchtower',
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
            )
            for binary in ('codex', 'codex-code-mode-host', 'codex-exec-server'):
                source = CODEX if binary == 'codex' else CODEX.parent / binary
                if not source.is_file():
                    raise RuntimeError('Codex companion executable missing: ' + binary)
                shutil.copyfile(source, build / binary)
            (build / 'Dockerfile').write_text(r'''FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY watchtower /usr/local/lib/python3.12/site-packages/watchtower
COPY codex codex-code-mode-host codex-exec-server /usr/local/bin/
RUN chmod 755 /usr/local/bin/codex* && printf '#!/usr/local/bin/python3\nfrom watchtower.cli import main\nraise SystemExit(main())\n' > /usr/local/bin/wt && chmod 755 /usr/local/bin/wt
''')
            docker('build', '-t', IMAGE, build, timeout=900)
        docker(
            'run',
            '-d',
            '--name',
            proxy,
            '--network',
            'bridge',
            '--read-only',
            '--tmpfs',
            '/tmp:exec,size=32m',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--entrypoint',
            'tail',
            'python:3.12-slim',
            '-f',
            '/dev/null',
        )
        active.add(('container', proxy))
        copy_into(AUTH, proxy, '/tmp/auth.json')
        docker('exec', proxy, 'chmod', '600', '/tmp/auth.json')
        copy_into(
            ROOT / 'scripts/oncall-security-model-proxy.py', proxy, '/tmp/proxy.py'
        )
        docker(
            'exec',
            '-d',
            '-e',
            'PROOF_PROXY_KEY=' + PROXY_KEY,
            proxy,
            'python3',
            '/tmp/proxy.py',
        )
        start = time.monotonic()
        out = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(
                    start_one, selected[i % len(selected)], i, tmp, archive, proxy
                )
                for i in range(count)
            ]
            for f in concurrent.futures.as_completed(futures):
                v = f.result()
                out.append(v)
                print(
                    'RESULT',
                    json.dumps(v),
                    'elapsed',
                    round(time.monotonic() - start, 1),
                    flush=True,
                )
        (tmp / 'results.json').write_text(
            json.dumps(sorted(out, key=lambda x: x['index']), indent=2)
        )
    finally:
        cleanup()
        docker('image', 'rm', IMAGE, check=False, timeout=60)
        for filename in ('source.tar', 'key.pem', 'mail.p12'):
            (tmp / filename).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
