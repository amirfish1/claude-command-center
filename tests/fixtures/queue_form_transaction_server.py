"""Disposable native full-queue API; no real services, credentials or audit writes."""
import json
import sys
from pathlib import Path
import server
from ccc_server import settings_transactions
from types import SimpleNamespace

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
raw=server._factory_spawn_defaults();raw.update(engine='codex',auto_compact_k=420)
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))
server._wt_config_path().write_text(json.dumps({'MANUAL':{'engine':'codex','model':'auto','effort':'high','fallback_to_default_worker':False,'headroom_dispatch':True,'auto_drain':False,'repo_path':'/private/example','future':{'credential':'not-a-secret'}}}))
server._WT_CONFIG_AVAILABLE=True
server._wt_config=SimpleNamespace(ROLE_KEYS=('planner','verifier'),
    _models=SimpleNamespace(ENGINES=('claude','codex')),
    canonical_model=lambda engine,model:model,
    is_approved_model=lambda engine,model: model in ('auto','default') and not (base/'reject-role').exists(),
    is_blocked_model=lambda model:False, CONFIG_FILE=base/'unused-audit-source.json',
    _record_audit=forbidden, set_role=forbidden, engine=forbidden)
original_sync=settings_transactions.sync_directory
def sync(directory):
    if (base/'fail-sync').exists():raise OSError('Fixture durability unavailable')
    return original_sync(directory)
settings_transactions.sync_directory=sync
class Handler(server.CommandCenterHandler):
    def log_message(self,*a):pass
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def do_GET(self):
        if self.path=='/':
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write((base/'fixture.html').read_bytes());return
        if self.path.startswith('/api/queue/roles?'):
            conf=json.loads(server._wt_config_path().read_text())['MANUAL']
            rows=[dict(role=role,label=role.title(),hint='Fixture role',editable=True,engine=conf.get(role+'_engine','codex'),model=conf.get(role+'_model','auto'),source='queue',override_engine=conf.get(role+'_engine',''),override_model=conf.get(role+'_model','')) for role in ('planner','verifier')]
            self.send_json(dict(ok=True,available=True,roles=rows,builder_engine='codex',engines=[dict(engine=e,models=['auto','default']) for e in ('claude','codex')]))
            return
        if self.path.startswith('/static/') and self.path.count('/')==2 and self.path.endswith(('.js','.css')):return super().do_GET()
        self.send_json({'error':'Unexpected fixture read'},403)
    def do_POST(self):
        if self.path=='/api/queue/config-options':
            conf=json.loads(server._wt_config_path().read_text())['MANUAL']
            self.send_json(dict(ok=True,defaults={},queues=[dict(queue='MANUAL',config=conf)],models_by_engine={'claude':['auto','default'],'codex':['auto','default']}))
            return
        if self.path=='/api/queue/config':return super().do_POST()
        self.send_json({'error':'Unexpected fixture action'},403)
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.PORT=httpd.server_address[1]
print(json.dumps({'url':f'http://127.0.0.1:{httpd.server_address[1]}'}),flush=True)
httpd.serve_forever()
