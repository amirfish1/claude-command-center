"""Actual customization GET with disposable state; inert canonical editor reads."""
import json
import sys
from pathlib import Path
import server

base=Path(sys.argv[1]);server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json';server._wt_config_path=lambda:base/'queue-config.json'
def forbidden(*a,**kw):raise AssertionError('No secrets, catalogs, probes, writes or services')
for name in ('_load_spawn_defaults','_factory_spawn_defaults','_write_spawn_defaults_file','_settings_launch_overview','_wt_read_config','byok_get_key','byok_list_profiles','build_system_services','build_ccc_doctor','_worker_fallback_catalog_models','_model_catalog_allows_model'):
    setattr(server,name,forbidden)
server.subprocess.run=forbidden;server.subprocess.Popen=forbidden

class Handler(server.CommandCenterHandler):
    def log_message(self,*a):pass
    def _phone_pin_blocked(self,p):return False
    def _check_proxy_secret(self):return True
    def do_GET(self):
        if self.path=='/':
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write((base/'fixture.html').read_bytes());return
        if self.path=='/api/settings/changes':
            if(base/'fail').exists():self.send_json({'error':'Fixture unavailable'},503);return
            return super().do_GET()
        if self.path=='/api/settings/preferences/registry':return super().do_GET()
        if self.path=='/api/spawn-defaults?policy=1':
            saved=json.loads(server.SPAWN_DEFAULTS_FILE.read_text());value={'engine':'claude','models':{'claude':'auto','codex':'auto'},'reasoning_effort':'','auto_compact_k':250,'worker_engine':'codex','worker_model':'auto','worker_reasoning_effort':'high','worker_auto_compact_k':250,'worker_inherit_session':False,**saved}
            self.send_json({'ok':True,**value,'policy_documents':saved,'policy_revision':'fixture','supported_engines':['claude','codex'],'worker_fallback_engines':['claude','codex'],'model_profile_engines':['claude'],'efforts_by_engine':{'claude':['low','medium','high','max'],'codex':['low','medium','high','xhigh']}});return
        if self.path.startswith('/static/') and self.path.count('/')==2 and self.path.endswith(('.js','.css')):return super().do_GET()
        self.send_json({'error':'Unexpected read'},403)
    def do_POST(self):
        if self.path=='/api/queue/config-options':
            queues=json.loads((base/'queue-config.json').read_text());self.send_json({'ok':True,'defaults':{},'queues':[{'queue':key,'config':value} for key,value in queues.items()],'models_by_engine':{'claude':['auto'],'codex':['auto']},'efforts_by_engine':{'claude':['low','high'],'codex':['low','high']},'repo_paths':[],'github_repos':[]});return
        if self.path=='/api/queue/config':
            payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if payload.get('policy_action')=='read':
                config=json.loads((base/'queue-config.json').read_text())[payload['queue']]
                self.send_json({'ok':True,'queue':payload['queue'],'config':config,'overrides':{key:config[key] for key in ('engine','model','effort','fallback_to_default_worker','headroom_dispatch') if key in config},'policy_revision':'fixture','plan':None,'observed_at':'2026-01-01T00:00:00Z'});return
        self.send_json({'error':'No writes permitted'},403)

httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':f'http://127.0.0.1:{httpd.server_address[1]}'}),flush=True)
httpd.serve_forever()
