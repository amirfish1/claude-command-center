"""Restricted disposable actual API host for the queue-policy browser fixture."""
import json
import sys
from pathlib import Path

import server
from ccc_server import queue_policy

base = Path(sys.argv[1])
server.COMMAND_CENTER_STATE_DIR = base
server.SPAWN_DEFAULTS_FILE = base / 'spawn-defaults.json'
server._wt_config_path = lambda: base / 'queue-config.json'
server._model_policy_error = lambda model: 'Model blocked by policy' if model == 'blocked' else None
server._model_policy_blocks = lambda model: False
server._model_policy_blocked_models = lambda: set()
server._codex_context_1m_enabled = lambda: False
server._codex_context_config = lambda **kwargs: {'model_context_window': 1000000}
server._spawn_fallback_model_for_engine = lambda engine: 'gpt-5-codex' if engine == 'codex' else ''
server._model_catalog_allows_model = lambda engine, model: True

def forbidden(*args, **kwargs):
    raise AssertionError('Fixture forbids worker/service actions and unrelated writes')
server._reconcile_once_async = forbidden
server._log_activity = forbidden
server._wt_log_activity = forbidden
raw = server._factory_spawn_defaults()
raw.update(worker_engine='codex', worker_model='gpt-5-codex', worker_reasoning_effort='high',
           worker_fallback={'enabled': True, 'models': [{'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high'}]},
           worker_headroom_dispatch=True)
raw['models'].update(claude='claude-sonnet-4-6', codex='gpt-5-codex')
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))
config = {'DEMO': {'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high',
                   'fallback_to_default_worker': False, 'headroom_dispatch': False,
                   'auto_drain': True, 'desired_workers': 3, 'future_policy': {'keep': [False, None]},
                   'roles': {'reviewer': {'model': 'claude-sonnet-4-6', 'future_role': True}}},
          'MANUAL': {'auto_drain': False, 'fallback_to_default_worker': False}}
server._wt_config_path().write_text(json.dumps(config))
try:
    from watchtower import config as wt, models
    wt.CONFIG_FILE = server._wt_config_path()
    wt.CCC_SPAWN_DEFAULTS_FILE = server.SPAWN_DEFAULTS_FILE
    wt.CCC_MODEL_POLICY_FILE = base / 'model-policy.json'
    wt.CCC_MODEL_POLICY_FILE.write_text('{}')
    models.entries = lambda engine: [{'id': name, 'label': name, 'efforts': ['low', 'medium', 'high']} for name in {
        'claude': ['claude-sonnet-4-6'], 'codex': ['gpt-5-codex']}.get(engine, [])]
except ImportError:
    server._WT_CONFIG_AVAILABLE = False
publish = queue_policy.publish

def guarded_publish(*args):
    if (base / 'fail-write').exists():
        raise OSError('simulated queue save failure')
    return publish(*args)
queue_policy.publish = guarded_publish

class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self, path): return False
    def _check_proxy_secret(self): return True
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == '/api/settings/queue-engines': return super().do_GET()
        if self.path.startswith('/api/queue/roles'): return self.send_json({'ok': True, 'available': False})
        if self.path == '/':
            body = (base / 'fixture.html').read_bytes(); mime = 'text/html; charset=utf-8'
        elif self.path in ('/static/app.css', '/static/queue-policy.css', '/static/queue-policy.js', '/static/settings-launch-preview.js'):
            body = (Path.cwd() / self.path.lstrip('/')).read_bytes()
            mime = 'text/css' if self.path.endswith('css') else 'application/javascript'
        else: return self.send_error(404)
        self.send_response(200); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path in ('/api/queue/config', '/api/queue/config-options'): return super().do_POST()
        return self.send_error(405)

httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
print(json.dumps({'url': 'http://127.0.0.1:' + str(httpd.server_address[1]), 'consumer_available': server._WT_CONFIG_AVAILABLE}), flush=True)
httpd.serve_forever()
