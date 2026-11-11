"""Local CI and application stand-ins; production source adapters poll them."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shlex
import subprocess
import sys
import threading

from . import sources

SCENES = ('customer', 'ci', 'monitoring')


class Scene:
    def __init__(self, name, project, env):
        self.name, self.project, self.env = name, project, env
        self.run_id, self.red, self.commit = 0, False, ''
        self.requests = []
        scene = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                scene.requests.append(self.path)
                code = 200
                if self.path == '/health':
                    code = 503 if scene.red else 200
                    payload = {'healthy': not scene.red}
                elif self.path.endswith('/actions/runs?per_page=100&limit=100'):
                    payload = {'workflow_runs': [] if not scene.run_id else [dict(
                        id=scene.run_id, workflow_id=1, name='Sample tests', head_branch='main',
                        status='completed', conclusion='failure' if scene.red else 'success',
                        head_sha=scene.commit)]}
                elif '/jobs?' in self.path:
                    payload = {'jobs': [dict(id=1, name='Python tests', conclusion='failure',
                                            steps=[dict(name='test_cart.py', conclusion='failure')])]}
                elif self.path.endswith('/logs'):
                    payload = (scene.project / 'application.log').read_text()
                else:
                    payload = {'default_branch': 'main'}
                body = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        (project / 'application.log').write_text('Application ready.\n')
        (project / 'check_log.py').write_text(
            "from pathlib import Path\nimport sys\nlog = Path('application.log').read_text()\n"
            "print(log)\nsys.exit(1 if 'ERROR' in log else 0)\n")

    def entries(self):
        if self.name == 'ci':
            return [dict(id='demo-ci', type='ci', repo='demo/sample', base=self.base, queue='DEMO')]
        return [dict(id='demo-log', type='command', title='Application log contains an error',
                     cmd=shlex.join([sys.executable, '-S', 'check_log.py']), cwd=str(self.project),
                     queue='DEMO', fail_after=1),
                dict(id='demo-health', type='http', url=self.base + '/health', queue='DEMO', fail_after=1)]

    def build(self, entry, **kwargs):
        # Only command checks need a child environment. Other transports make
        # genuine HTTP requests to this instance's loopback server.
        return sources.build(entry, run=lambda *a, **kw: subprocess.run(*a, env=self.env, **kw), **kwargs)

    def fail(self, commit):
        result = subprocess.run([sys.executable, '-S', 'test_cart.py'], cwd=self.project,
                                env=self.env, capture_output=True, text=True, timeout=10)
        if not result.returncode:
            raise RuntimeError('The source scene needs a real failing test.')
        (self.project / 'application.log').write_text('ERROR: ' + result.stderr)
        self.run_id, self.red, self.commit = 1, True, commit

    def recover(self, commit):
        subprocess.run([sys.executable, '-S', 'test_cart.py'], cwd=self.project,
                       env=self.env, capture_output=True, check=True, timeout=10)
        (self.project / 'application.log').write_text('Application healthy.\n')
        self.run_id, self.red, self.commit = 2, False, commit

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
