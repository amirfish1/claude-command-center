"""Restricted native engine API fixture. All process/network actions are inert."""
import json
import sys
from pathlib import Path
import server

base=Path(sys.argv[1])
server.COMMAND_CENTER_STATE_DIR=base
server.SPAWN_DEFAULTS_FILE=base/'spawn-defaults.json'
server._model_policy_blocks=lambda model:False
server._model_policy_blocked_models=lambda:set()
server._model_catalog_allows_model=lambda engine,model:True
server._worker_fallback_catalog_models=lambda engine:[{'id':m} for m in {'claude':['claude-sonnet-4-6'],'codex':['gpt-5-codex']}.get(engine,[])]
server._codex_context_1m_enabled=lambda:False
server._codex_context_config=lambda **kwargs:{'model_context_window':1000000}
raw=server._factory_spawn_defaults()
raw.update(engine='claude',future_policy={'keep':True})
raw['models'].update(claude='claude-sonnet-4-6',codex='gpt-5-codex')
server.SPAWN_DEFAULTS_FILE.write_text(json.dumps(raw))

def state():return json.loads((base/'engines.json').read_text())
def installed(fresh=False):
    return {'engines':[{'engine':e,'kind':'spawn','installed':v['installed'],'detail':'/example/bin/'+e} for e,v in state().items()]}
def onboarding():
    return {'completed':True,'clis':{e:{'name':e,'available':v['installed'],'logged_in':v['auth'],'command':e,'install_instruction':'example-install '+e,'login_instruction':e+' login'} for e,v in state().items() if v['auth'] is not None}}
server._engines_installed=installed
server._get_onboarding_status=onboarding
server.byok_list_profiles=lambda:[]
server.read_graphql_quota=lambda:{'ok':False,'reason':'Not checked in fixture'}
server.build_doctor_instances=lambda:{'status':'ok','instances':[]}
server.build_memory_doctor=lambda:{}
server._model_policy_health=lambda:{}
server._opencode_configured_providers=lambda:[]
# Native API routers execute, but none of their launch helpers can run a CLI.
server._launch_install_terminal=lambda engine:{'ok':True,'command':'example-install '+engine,'terminal_app':'Fixture terminal'}
server._launch_login_terminal=lambda engine:{'ok':False,'inline_login':True,'error':'Use inline fixture login'}
server._start_onboarding_login_session=lambda engine:{'ok':True,'session_id':'fixture-login','command':engine+' login','running':True,'output':'Complete sign-in on the host.','offset':1,'hints':{'urls':['https://example.test/auth'],'codes':[]}}
server._onboarding_login_status=lambda session_id,offset=0:{'ok':True,'session_id':session_id,'running':not(base/'login-done').exists(),'output':'','offset':1,'exit_code':0 if(base/'login-done').exists() else None}
server._cancel_onboarding_login_session=lambda session_id:{'ok':True,'output':'Cancelled'}
server._send_onboarding_login_input=lambda session_id,text:{'ok':True}
server._kimi_setup_verify=lambda:{'ok':not(base/'fail-test').exists(),'error':'Simulated ACP failure' if(base/'fail-test').exists() else ''}

class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def log_message(self,*args):pass
    def do_GET(self):
        path=self.path.split('?')[0]
        if path=='/api/engines/doctor' and(base/'fail-status').exists():return self.send_json({'ok':False},503)
        if path in ('/api/engines/doctor','/api/onboarding/status','/api/onboarding/login/status','/api/spawn-defaults'):return super().do_GET()
        if path in ('/api/usage/current','/api/engines/update-status'):return self.send_json({'ok':True})
        if path=='/':body=(base/'fixture.html').read_bytes();mime='text/html; charset=utf-8'
        elif path in ('/static/app.css','/static/engine-connection.css','/static/engine-connection.js','/static/settings-routing-policy.js','/static/onboarding/onboarding.js','/static/onboarding/onboarding.css'):
            body=(Path.cwd()/path.lstrip('/')).read_bytes();mime='text/css' if path.endswith('css') else 'application/javascript'
        else:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path in ('/api/onboarding/install-terminal','/api/onboarding/login-terminal','/api/onboarding/login/start','/api/onboarding/login/input','/api/onboarding/login/cancel','/api/engines/kimi/verify','/api/spawn-defaults'):return super().do_POST()
        return self.send_error(405)

(base/'engines.json').write_text(json.dumps({'claude':{'installed':True,'auth':True},'codex':{'installed':False,'auth':False},'kimi':{'installed':True,'auth':True},'kilo':{'installed':True,'auth':None},'aider':{'installed':True,'auth':None}}))
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True)
httpd.serve_forever()
