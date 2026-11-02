"""Native read-only health API on disposable policy/cache data, no probes."""
import json
import sys
import time
from pathlib import Path

import server
from ccc_server import engines

base=Path(sys.argv[1])
server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json'
server._worker_fallback_catalog_models=lambda engine:[{'id':'example-model'}]
server._model_policy_blocks=lambda model:False
server._model_catalog_allows_model=lambda engine,model:True

def forbidden(*args,**kw):raise AssertionError('No credentials, probes, writes or service actions')
for name in ('byok_get_key','byok_list_profiles','build_ccc_doctor','_engines_installed','build_system_services','_load_spawn_defaults','_write_spawn_defaults_file'):
    setattr(server,name,forbidden)
server.subprocess.run=forbidden
server.subprocess.Popen=forbidden

class Handler(server.CommandCenterHandler):
    def log_message(self,*args):pass
    def _phone_pin_blocked(self,p):return False
    def _check_proxy_secret(self):return True
    def do_GET(self):
        if self.path=='/':
            raw=(base/'fixture.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw);return
        if self.path=='/api/settings/health':
            if (base/'fail').exists():self.send_json({'error':'Fixture unavailable'},503);return
            age=120 if (base/'stale').exists() else 1
            engines._ENGINES_INSTALLED_CACHE={'ts':time.monotonic()-age,'data':{'engines':[{'engine':'codex','installed':False},{'engine':'claude','installed':True}]}}
            server._system_services_cache={'ts':time.time()-age,'payload':{'ok':True,'services':[{'id':'worker','state':'offline'},{'id':'watchtower','state':'offline','api_ok':False,'api_probe_age_s':1}]}}
            return super().do_GET()
        if self.path=='/api/system-health':self.send_json({'fixture':True});return
        if self.path.startswith('/static/') and self.path.count('/')==2 and self.path.endswith(('.js','.css')):return super().do_GET()
        self.send_json({'error':'Forbidden fixture route'},403)
    def do_POST(self):self.send_json({'error':'No writes permitted'},403)

httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':f'http://127.0.0.1:{httpd.server_address[1]}'}),flush=True)
httpd.serve_forever()
