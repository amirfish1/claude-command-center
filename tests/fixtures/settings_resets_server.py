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
# Do not use WT audit helpers in an isolated fixture, even if optional WT is installed.
server._WT_CONFIG_AVAILABLE=False
native=getattr(server,'_wt_config',None)
if native:
    native.CONFIG_FILE=base/'queue-config.json'
    native._record_audit=forbidden
import os
os.environ['WATCHTOWER_CONFIG_FILE']=str(base/'queue-config.json')
os.environ['CCC_SPAWN_DEFAULTS_FILE']=str(base/'spawn-defaults.json')
original_write=Path.write_text
def guarded_write(path,*args,**kwargs):
    if 'audit' in path.name and not path.resolve().is_relative_to(base.resolve()):
        raise AssertionError('Non-temporary audit write forbidden')
    return original_write(path,*args,**kwargs)
Path.write_text=guarded_write
import builtins
import io
def guarded_open(original):
    def opened(file, mode='r', *args, **kwargs):
        if isinstance(file, (str, bytes, Path)) and any(flag in mode for flag in 'wax+'):
            candidate=Path(file.decode() if isinstance(file, bytes) else file)
            if 'audit' in candidate.name and not candidate.resolve().is_relative_to(base.resolve()):
                raise AssertionError('Non-temporary audit write forbidden')
        return original(file, mode, *args, **kwargs)
    return opened
builtins.open=guarded_open(builtins.open)
io.open=guarded_open(io.open)
raw.update(engine='codex',auto_compact_k=420)
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))
server._wt_config_path().write_text(json.dumps({'MANUAL':{'engine':'codex','model':'auto','effort':'high','fallback_to_default_worker':False,'headroom_dispatch':True,'auto_drain':False,'repo_path':'/private/example','future':{'credential':'not-a-secret'}}}))
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
            return super().do_GET()
        if self.path.startswith('/api/settings/resets'):return super().do_GET()
        if self.path in ('/api/settings/profiles','/api/settings/preferences/registry'):return super().do_GET()
        if self.path=='/api/settings/history':
            if (base/'fail-read').exists():self.send_json({'error':'Fixture history unavailable'},503);return
            return super().do_GET()
        if self.path.startswith('/static/') and self.path.count('/')==2 and self.path.endswith(('.js','.css')):return super().do_GET()
        self.send_json({'error':'Unexpected fixture read'},403)
    def do_POST(self):
        if self.path in ('/api/settings/resets','/api/settings/profiles','/api/settings/history','/api/spawn-defaults','/api/queue/config'):return super().do_POST()
        self.send_json({'error':'Unexpected fixture action'},403)
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':f'http://127.0.0.1:{httpd.server_address[1]}'}),flush=True)
httpd.serve_forever()
