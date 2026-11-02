"""Restricted voice setup host: real config API, disposable state, no media."""
import json
import sys
from pathlib import Path
import server
from ccc_server import realtime_voice as voice

base=Path(sys.argv[1])
voice.VOICE_STATE_DIR=base/'voice'
voice.VOICE_CONFIG_FILE=voice.VOICE_STATE_DIR/'voice.json'
voice.VOICE_STATE_DIR.mkdir()
voice.VOICE_CONFIG_FILE.write_text(json.dumps({**voice._default_config(), 'voice':'coral', 'profile':'Missing profile', 'max_minutes':25, 'show_mobile_btn':False}))
server.byok_list_profiles=lambda:[{'name':'Demo profile','providers':['openai']}]
server.byok_get_key=lambda *args: (_ for _ in ()).throw(AssertionError('No key access'))
server._resolve_codex_bin=lambda: (_ for _ in ()).throw(AssertionError('No process access'))

class Handler(server.CommandCenterHandler):
    def _phone_pin_blocked(self,path):return False
    def _check_proxy_secret(self):return True
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path in ('/api/voice/config','/api/voice/voices'):
            if (base/'fail-read').exists():return self.send_json({'ok':False,'error':'simulated read failure'},503)
            return super().do_GET()
        if self.path=='/api/car-mode/status':return self.send_json(json.loads((base/'operator.json').read_text()))
        if self.path=='/api/local-speech/status':return self.send_json(json.loads((base/'offline.json').read_text()))
        if self.path=='/api/free-runtime/tts-voices':
            if (base/'fail-catalog').exists():return self.send_json({'ok':False},503)
            return self.send_json({'ok':True,'voices':[{'id':'Kokoro: af_heart','label':'Local voice','paid':False},{'id':'Deepgram: demo','label':'Paid voice','paid':True}]})
        if self.path=='/':body=(base/'fixture.html').read_bytes();mime='text/html; charset=utf-8'
        elif self.path in ('/static/app.css','/static/voice-setup.css','/static/voice-setup.js','/static/local-speech.js'):
            body=(Path.cwd()/self.path.lstrip('/')).read_bytes();mime='text/css' if self.path.endswith('css') else 'application/javascript'
        else:return self.send_error(404)
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path=='/api/voice/config':
            if (base/'fail-write').exists():return self.send_json({'ok':False,'error':'simulated save failure'},503)
            return super().do_POST()
        # No install, key save, call start, audio, speech playback or service API.
        return self.send_error(405)

(base/'operator.json').write_text(json.dumps({'ok':True,'running':False,'mode':'voice','launcher_present':False,'speech_engine_label':'Local speech','anthropic_key_set':True,'deepgram_key_set':False}))
(base/'offline.json').write_text(json.dumps({'state':'not_installed','reason':'','progress':''}))
httpd=server.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'url':'http://127.0.0.1:'+str(httpd.server_address[1])}),flush=True)
httpd.serve_forever()
