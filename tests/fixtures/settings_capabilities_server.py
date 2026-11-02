"""Actual metadata/config/phone routers with inert runtime operations only."""
import json
import sys
import time
from pathlib import Path

import server
import federation
from ccc_server import cloud_relay, free_router, phone_access, realtime_voice as voice

base=Path(sys.argv[1])
server.COMMAND_CENTER_STATE_DIR=base
voice.VOICE_STATE_DIR=base/'voice'
voice.VOICE_CONFIG_FILE=voice.VOICE_STATE_DIR/'voice.json'
voice._SESSION=None
server.NETWORK_CONFIG_FILE=base/'network.json'
server.CAR_MODE_CONFIG_FILE=base/'operator-config.json'
server._CAR_MODE_DIR=base/'operator-install'
server._CAR_MODE={}
server.RUNTIME_NETWORK_INFO={'bind_host':'127.0.0.1'}
phone_access.state_file=lambda:base/'phone.json'
federation._peers_file=lambda:base/'peers.json'
cloud_relay._default_state_dir=lambda:str(base/'cloud')
free_router.state_path=lambda:base/'router.json'
free_router.config_path=lambda:base/'router-config.json'
free_router.install_dir=lambda:base/'router-install'
free_router._STATUS_CACHE={'data':None,'at':0}
server.byok_list_profiles=lambda:[]
server.byok_usage_summary=lambda **kwargs:{'spawns':0}

def forbidden(*args,**kwargs):raise AssertionError('No credentials, processes, network probes or service actions')
server.byok_get_key=forbidden
server._car_mode_effective_keys=forbidden
server._detect_tailnet_origins=forbidden
free_router.status=forbidden
phone_access.tailscale_status=forbidden
phone_access.serve_status=forbidden
server.subprocess.Popen=forbidden
server.subprocess.run=forbidden
server._resolved_feature_flags=lambda:{'phone_access':(base/'flag-on').exists()}
server._feature_flag=lambda name:(base/'flag-on').exists()
server._feature_flags_meta=lambda:[{'name':'phone_access','label':'Phone access','desc':'Gate for explicit phone sharing','on':(base/'flag-on').exists()}]

def flag(name,on):
    if (base/'fail-flag').exists():return {'ok':False,'error':'Fixture flag save failed'}
    if on:(base/'flag-on').touch()
    else:(base/'flag-on').unlink(missing_ok=True)
    return {'ok':True,'flags':server._resolved_feature_flags()}
server._save_feature_flag=flag

def phone_status(include_qr=True):
    return {'ok':True,'enabled':(base/'phone.json').exists(),'flag':server._feature_flag('phone_access'),
        'tailscale':{'installed':True,'running':True,'logged_in':True,'backend_state':'Running','hostname':'example.test'},
        'serve':{'entries':[],'funnel':[],'plan':{'action':'create','https_port':443,'target':'http://127.0.0.1:1'}},'url':''}
phone_access.overview=phone_status

def share(on):
    if(base/'fail-save').exists():return {'ok':False,'error':'Fixture sharing change failed'}
    if on:(base/'phone.json').write_text('{"fixture":"configured"}')
    else:(base/'phone.json').unlink(missing_ok=True)
    return {'ok':True,'detail':'Fixture metadata only'}
phone_access.enable=lambda:share(True)
phone_access.disable=lambda:share(False)
phone_access.run_roundtrip_test=lambda:{'ok':True,'detail':'Inert fixture; no network test performed'}

class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def log_message(self,*args):pass
    def do_GET(self):
        route=self.path.split('?')[0]
        if route=='/api/settings/capabilities':
            if(base/'fail-inventory').exists():return self.send_json({'error':'Fixture inventory unavailable'},503)
            return super().do_GET()
        if route in ('/api/voice/config','/api/voice/voices','/api/phone-access/status'):return super().do_GET()
        if route=='/api/features':return self.send_json({'preview':server._resolved_feature_flags(),'preview_meta':server._feature_flags_meta()})
        if route=='/api/free-router/status':return self.send_json({'installed':True,'running':(base/'router-on').exists(),'healthy':True,'state':'ready','node_ok':True,'keys':[]})
        if route=='/api/car-mode/status':return self.send_json({'ok':True,'running':(base/'operator-on').exists(),'mode':'setup','launcher_present':False,'speech_engine_label':'Local speech','anthropic_key_set':False,'deepgram_key_set':False})
        if route=='/api/free-runtime/tts-voices':return self.send_json({'ok':True,'voices':[]})
        if route=='/api/free-settings/ping':return self.send_json({'configured':True})
        if route.startswith('/api/free-settings/') or route.startswith('/api/free-router/') or route=='/api/domestic-providers':return self.send_json({'ok':True,'keys':[],'providers':[],'summary':{},'strategy':{},'platforms':[]})
        if route=='/':body=(base/'fixture.html').read_bytes();mime='text/html; charset=utf-8'
        elif route.startswith('/static/') and Path(route).name in ('app.css','settings-capabilities.css','settings-capabilities.js','voice-setup.js','voice-setup.css','free-settings.js','free-settings.css','phone-access.js'):
            body=(Path.cwd()/'static'/Path(route).name).read_bytes();mime='text/css' if route.endswith('css') else 'application/javascript'
        else:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path in ('/api/phone-access/enable','/api/phone-access/disable','/api/phone-access/test','/api/features/flag','/api/voice/config'):
            if self.path=='/api/voice/config' and(base/'fail-save').exists():return self.send_json({'ok':False,'error':'Fixture config save failed'},503)
            return super().do_POST()
        if self.path=='/api/car-mode/stop':
            if not self._check_same_origin():return
            if(base/'fail-save').exists():return self.send_json({'ok':False,'error':'Fixture operator stop failed'},503)
            (base/'operator-on').unlink(missing_ok=True)
            return self.send_json({'ok':True,'running':False})
        if self.path in ('/api/free-router/start','/api/free-router/stop'):
            if not self._check_same_origin():return
            if(base/'fail-save').exists():return self.send_json({'ok':False,'error':'Fixture router action failed'},503)
            if self.path.endswith('/start'):(base/'router-on').touch()
            else:(base/'router-on').unlink(missing_ok=True)
            return self.send_json({'ok':True})
        return self.send_error(405)

httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True)
httpd.serve_forever()
