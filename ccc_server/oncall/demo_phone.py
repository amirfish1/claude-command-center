"""Loopback transport for the demo. No external messaging service."""
import json
import queue
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .store import Inbound, Receipt

PAGE = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CCC On-call demo</title><style>
body{font:18px system-ui;background:#f3f4f6;color:#18202b;max-width:680px;margin:40px auto;padding:20px}
h1{font-size:28px}pre{white-space:pre-wrap;background:white;padding:18px;border-radius:12px;border:1px solid #ccd3dc}
input,button{font:inherit;padding:12px}input{max-width:65%}button{cursor:pointer}
</style><h1>On-call, on your machine</h1><p>Demo messages only. Nothing is sent to a real person.</p>
<div id="messages" aria-live="polite"></div><form><label for="reply">Your reply</label><br>
<input id="reply" required maxlength="2000" autocomplete="off"><button>Send</button></form><p id="status"></p>
<script>
const key=location.hash.slice(1); let version=-1;
async function refresh(){try{const r=await fetch('/messages',{headers:{'X-Demo-Key':key}});const d=await r.json();
if(version!==d.length){version=d.length;document.querySelector('#messages').replaceChildren(...d.map(m=>{
const p=document.createElement('pre');p.textContent=m;return p;}));}}catch(e){document.querySelector('#status').textContent='Demo finished. You can close this tab.';clearInterval(timer);}}
document.querySelector('form').onsubmit=async e=>{e.preventDefault();const input=document.querySelector('#reply');
const r=await fetch('/reply',{method:'POST',headers:{'X-Demo-Key':key,'Content-Type':'application/json'},body:JSON.stringify({text:input.value})});
if(r.ok){input.value='';refresh();}};const timer=setInterval(refresh,500);refresh();
</script>'''


class Phone:
    id = 'demo'
    owner = 'owner@example.test'
    can_receive = True
    last_error = ''

    def __init__(self, emit):
        self.emit = emit
        self.messages = []
        self.inbox = queue.Queue()
        self.replies = queue.Queue()
        self.key = secrets.token_urlsafe(32)
        self.server = None

    def send(self, message):
        self.messages.append(asdict(message))
        self.emit(('Reporter: ' if message.to else '') + message.text)
        return Receipt(True, external_id=str(len(self.messages)))

    def poll(self):
        messages = []
        while True:
            try:
                messages.append(self.inbox.get_nowait())
            except queue.Empty:
                return messages

    def receive(self, text, sender=None):
        self.inbox.put(Inbound(self.id, secrets.token_hex(12), sender or self.owner, text))

    def start(self, lines):
        phone = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, data, content_type='application/json'):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                return (self.headers.get('Host') == phone.base.removeprefix('http://')
                        and self.headers.get('X-Demo-Key') == phone.key
                        and self.headers.get('Origin', phone.base) == phone.base)

            def do_GET(self):
                if self.path == '/' and self.headers.get('Host') == phone.base.removeprefix('http://'):
                    return self.respond(200, PAGE.encode(), 'text/html; charset=utf-8')
                if self.path == '/messages' and self.authorized():
                    return self.respond(200, json.dumps(list(lines)).encode())
                self.respond(403, b'{}')

            def do_POST(self):
                if self.path != '/reply' or not self.authorized():
                    return self.respond(403, b'{}')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 8192:
                        raise ValueError()
                    text = json.loads(self.rfile.read(size))['text']
                    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    return self.respond(400, b'{}')
                phone.replies.put(text.strip())
                self.respond(200, b'{}')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.base + '/#' + self.key

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
