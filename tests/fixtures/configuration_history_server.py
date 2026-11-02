"""Disposable native operational history APIs, never live services or credentials."""
import json
import sys
from pathlib import Path
import server
from ccc_server import configuration_history

base=Path(sys.argv[1]);server.COMMAND_CENTER_STATE_DIR=base;server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json';server._wt_config_path=lambda:base/'queue-config.json'
server.MODEL_POLICY_FILE=base/'model-policy.json';server.MODEL_POLICY_FILE.write_text('{"blocked_models":[]}')
server._spawn_fallback_model_for_engine=lambda engine:'auto'
server._model_catalog_allows_model=lambda engine,model:True
server._model_policy_blocked_models=lambda:set()
server._model_policy_blocks=lambda model:False
server._model_policy_error=lambda model:None
server._worker_fallback_catalog_models=lambda engine:[{'id':'auto'},{'id':'default'}]
server._codex_context_1m_enabled=lambda:False
server._codex_context_config=lambda **kw:{'model_context_window':1000000}
def forbidden(*args,**kwargs):raise AssertionError('No processes, credentials, services or probes')
server.subprocess.run=forbidden;server.subprocess.Popen=forbidden;server.byok_get_key=forbidden;server._reconcile_once_async=forbidden
raw=server._factory_spawn_defaults();raw.update(worker_engine='codex',worker_model='auto',worker_reasoning_effort='high',future={'credential':'not-a-secret','path':'/private/example'})
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw));server._wt_config_path().write_text(json.dumps({'MANUAL':{'auto_drain':False,'repo_path':'/private/example'}}))
original_atomic=configuration_history.atomic
def atomic(path,value):
    if (base/'fail-history').exists() or (base/'fail-final').exists() and value['pending'] is None:raise OSError('Fixture unavailable')
    return original_atomic(path,value)
configuration_history.atomic=atomic
class Handler(server.CommandCenterHandler):
    def log_message(self,*a):pass
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def do_GET(self):
        if self.path=='/':
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write((base/'fixture.html').read_bytes());return
        if self.path=='/api/spawn-defaults?policy=1':
            if (base/'fail-consumer').exists():self.send_json({'error':'Fixture controls unavailable'},503);return
            self.send_json({'ok':True,**server._routing_policy_snapshot()[2]});return
        if self.path=='/api/settings/history':
            if (base/'fail-read').exists():self.send_json({'error':'Fixture history unavailable'},503);return
            return super().do_GET()
        if self.path.startswith('/static/') and self.path.count('/')==2 and self.path.endswith(('.js','.css')):return super().do_GET()
        self.send_json({'error':'Unexpected fixture read'},403)
    def do_POST(self):
        if self.path in ('/api/settings/history','/api/spawn-defaults','/api/queue/config'):return super().do_POST()
        self.send_json({'error':'Unexpected fixture action'},403)
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':f'http://127.0.0.1:{httpd.server_address[1]}'}),flush=True)
httpd.serve_forever()
