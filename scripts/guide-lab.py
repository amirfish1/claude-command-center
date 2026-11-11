"""Disposable local ntfy and TLS mail servers for checked guide commands."""
import contextlib
import importlib.util
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

REPO = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'scripts' / (name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=120, **kwargs).strip()


@contextlib.contextmanager
def servers():
    containers = []
    with tempfile.TemporaryDirectory(prefix='ccc-guide-servers-') as tmp:
        root = Path(tmp)
        cert = root / 'cert.pem'
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-keyout', str(root / 'key.pem'), '-out', str(cert), '-subj', '/CN=localhost',
            '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1')
        run('openssl', 'pkcs12', '-export', '-inkey', str(root / 'key.pem'), '-in', str(cert),
            '-out', str(root / 'mail.p12'), '-passout', 'pass:changeit')
        (root / 'mail.p12').chmod(0o644)
        try:
            def start(image, ports, *extra):
                name = root.name + '-' + str(len(containers))
                containers.append(name)
                argv = ['docker', 'run', '--name', name, '-d']
                for port in ports:
                    argv += ['-p', f'127.0.0.1::{port}']
                argv += list(extra) + [image]
                if image.startswith('binwiederhier/'):
                    argv += ['serve', '--cache-file', '/tmp/cache.db', '--visitor-request-limit-burst', '1000']
                run(*argv)
                bound = json.loads(run('docker', 'inspect', name))[0]['NetworkSettings']['Ports']
                return [int(bound[f'{p}/tcp'][0]['HostPort']) for p in ports]
            web, = start('binwiederhier/ntfy:v2.17.0', [80])
            smtp, imap = start('greenmail/standalone:2.1.3', [3465, 3993], '-v',
                f'{root / "mail.p12"}:/tmp/mail.p12:ro', '-e',
                'JAVA_TOOL_OPTIONS=-XX:ActiveProcessorCount=2 -XX:TieredStopAtLevel=1 -Xms32m -Xmx128m', '-e',
                'GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.startup.timeout=10000 '
                '-Dgreenmail.tls.keystore.file=/tmp/mail.p12 -Dgreenmail.tls.keystore.password=changeit '
                '-Dgreenmail.users=oncall:test-password@example.test,owner:test-password@example.test,reporter:test-password@example.test')
            context = ssl.create_default_context(cafile=str(cert))
            for port in (web, smtp, imap):
                end = time.monotonic() + 40
                while True:
                    try:
                        if port == web:
                            with urllib.request.urlopen(f'http://localhost:{web}/v1/health', timeout=1) as r:
                                assert r.status == 200
                        else:
                            with socket.create_connection(('localhost', port), timeout=1) as sock:
                                with context.wrap_socket(sock, server_hostname='localhost') as tls:
                                    assert tls.recv(256)
                        break
                    except OSError:
                        if time.monotonic() >= end:
                            raise TimeoutError('Local guide server startup')
                        threading.Event().wait(.1)
            yield dict(NTFY_SERVER=f'http://localhost:{web}', SMTP_PORT=str(smtp),
                       IMAP_PORT=str(imap), SSL_CERT_FILE=str(cert))
        except Exception:
            for name in containers:
                logs = subprocess.run(['docker', 'logs', '--tail', '25', name], capture_output=True, text=True, timeout=10)
                print(logs.stdout + logs.stderr, file=sys.stderr)
            raise
        finally:
            failures = []
            for name in reversed(containers):
                removed = subprocess.run(['docker', 'rm', '-fv', name], capture_output=True, text=True, timeout=30)
                if removed.returncode and 'No such container' not in removed.stderr:
                    failures.append(removed.stderr)
            if failures:
                raise RuntimeError('Could not remove guide containers: ' + '; '.join(failures))


def environment(root, settings):
    bins = root / 'bin'
    bins.mkdir()
    for name in ('python3', 'wt', 'git', 'bash', 'sh', 'env', 'cat', 'node', 'curl', 'script'):
        executable = shutil.which(name)
        if executable:
            (bins / name).symlink_to(executable)
    (bins / 'ccc').symlink_to(REPO / 'ccc')
    env = {k: os.environ[k] for k in ('LANG', 'LC_ALL') if k in os.environ}
    env.update(HOME=str(root), PATH=str(bins), XDG_CONFIG_HOME=str(root / 'config'),
               XDG_DATA_HOME=str(root / 'data'), TMPDIR=str(root),
               CCC_STATE_DIR=str(root / 'state'), CCC_FF_ONCALL='1', CCC_TELEMETRY_DISABLED='1',
               WATCHTOWER_STORE=str(root / 'queues.json'), WT_NO_AUTOSTART='1', WT_NO_SELF_UPDATE='1',
               WATCHTOWER_DELEGATE_URL='off', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               PYTHONPATH=os.pathsep.join(sys.path), **settings)
    return env


def cleanup(root):
    """Stop children with this private HOME or a demo HOME inside it."""
    import signal
    if not Path('/proc/self/environ').exists():
        raise RuntimeError('Linux /proc is required for guide process cleanup')
    prefix = str(root).encode()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            values = (entry / 'environ').read_bytes().split(b'\0')
            homes = [v[5:] for v in values if v.startswith(b'HOME=')]
            if any(v == prefix or v.startswith(prefix + b'/') for v in homes):
                os.kill(int(entry.name), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
