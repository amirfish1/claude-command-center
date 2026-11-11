"""Loopback transport for the demo. No external messaging service."""
import json
import queue
import secrets
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .store import Inbound, Receipt

PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CCC On-call demo</title><style>
:root{font:16px/1.5 system-ui,sans-serif;color:#172823;background:#e9eee9;--accent:#14644d}
*{box-sizing:border-box}body{margin:0;padding:32px 16px}main{max-width:440px;margin:auto;background:#fbfcf8;border-radius:24px;overflow:hidden;box-shadow:0 12px 48px #143d2520}
header{padding:24px 24px 16px;border-bottom:1px solid #dae2d9}h1{font-size:24px;line-height:1.2;margin:0 0 8px;letter-spacing:-.02em}header p{font-size:14px;color:#456052;margin:0}.live{padding:12px 24px;background:#e0eee4;font-size:14px;color:#174732;position:sticky;top:0;font-weight:600}
#messages{padding:20px 16px;display:flex;flex-direction:column;gap:18px;min-height:180px;max-height:58vh;overflow:auto;scrollbar-color:#9eb6a5 transparent}.message{max-width:94%;align-self:flex-start}.message.you{align-self:flex-end}.sender{font-size:12px;font-weight:600;color:#456052;margin:0 8px 5px}.bubble{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf1e9;border-radius:16px 16px 16px 4px;padding:14px 16px;font-size:15px}.you .bubble{background:#14644d;color:white;border-radius:16px 16px 4px 16px}.you .sender{text-align:right}
footer{padding:16px;border-top:1px solid #dae2d9}#choices{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}button,input{font:inherit;border:1px solid #8ba994;border-radius:10px;min-height:44px;padding:10px 14px}button{cursor:pointer;background:#fff;color:#174732;font-weight:600}button:hover{background:#e0eee4}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible{outline:3px solid #aa6c22;outline-offset:2px}form{display:flex;gap:8px}input{min-width:0;width:100%;background:white;caret-color:var(--accent)}form button{background:var(--accent);color:white;border-color:var(--accent)}form button:hover{background:#104c3a}label{display:block;font-size:13px;color:#456052;margin:0 0 6px}#status{font-size:13px;margin:10px 0 0;color:#456052;min-height:20px}::selection{background:#e3ca95;color:#172823}.note{text-align:center;max-width:440px;margin:16px auto;font-size:13px;color:#456052}
@media(max-width:480px){body{padding:0;background:#fbfcf8}main{border-radius:0;box-shadow:none;max-width:none}header{padding:20px 20px 16px}#messages{max-height:56vh}.note{padding:0 20px}}
</style><main><header><h1>CCC On-call</h1><p>A bug report. One decision. A working fix.</p></header>
<div class="live" role="status" id="ticket">Waiting for the report</div>
<div id="messages" aria-live="polite" aria-label="Message thread"></div>
<footer><div id="choices" aria-label="Suggested replies"></div><label for="reply">Your reply</label><form><input id="reply" required maxlength="2000" autocomplete="off" placeholder="Type a reply"><button>Send</button></form><p id="status">These messages stay on your machine.</p></footer></main><p class="note">Local demo. No account or real customer is connected.</p>
<script>
const key=location.hash.slice(1), box=document.querySelector('#messages'), choices=document.querySelector('#choices');let version='',busy=false,finished=false;
async function send(text){if(busy||finished)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);
try{const r=await fetch('/reply',{method:'POST',headers:{'X-Demo-Key':key,'Content-Type':'application/json'},body:JSON.stringify({text})});if(!r.ok)throw Error();document.querySelector('#reply').value='';document.querySelector('#status').textContent='Reply delivered.';await refresh();}
catch(e){document.querySelector('#status').textContent='Reply failed. Check the terminal and try again.';}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=finished);}}
async function refresh(){try{const r=await fetch('/messages',{headers:{'X-Demo-Key':key}});if(!r.ok)throw Error();const d=await r.json();document.querySelector('#ticket').textContent=d.state;
const v=JSON.stringify(d);if(v!==version){version=v;box.replaceChildren(...d.messages.map(m=>{const item=document.createElement('div');item.className='message'+(m.sender==='You'?' you':'');const name=document.createElement('div');name.className='sender';name.textContent=m.sender;const body=document.createElement('div');body.className='bubble';body.textContent=m.text;item.append(name,body);return item;}));box.scrollTop=box.scrollHeight;
choices.replaceChildren(...d.choices.map(text=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>send(text);b.disabled=busy;return b;}));}
if(d.done){finished=true;clearInterval(timer);document.querySelector('#status').textContent='Demo complete. You can close this tab.';document.querySelectorAll('button,input').forEach(b=>b.disabled=true);}}
catch(e){clearInterval(timer);document.querySelector('#status').textContent='Demo ended or disconnected. Check the terminal.';}}
document.querySelector('form').onsubmit=e=>{e.preventDefault();send(document.querySelector('#reply').value)};const timer=setInterval(refresh,150);refresh();
</script></html>
'''


class Phone:
    id = 'demo'
    owner = 'owner@example.test'
    can_receive = True
    last_error = ''

    def __init__(self, emit):
        self.emit = emit
        self.messages = []
        self.thread_messages = []
        self.state = 'Reported'
        self.choices = []
        self.done = False
        self.seen_done = threading.Event()
        self.inbox = queue.Queue()
        self.replies = queue.Queue()
        self.key = secrets.token_urlsafe(32)
        self.server = None

    def send(self, message):
        self.messages.append(asdict(message))
        self.thread_messages.append(dict(sender='Reporter notice' if message.to else 'On-call', text=message.text))
        if message.choices:
            self.choices = list(message.choices)
        elif 'Reply approve or decline.' in message.text:
            self.choices = ['approve', 'decline']
        elif 'Reply zero or hide.' in message.text:
            self.choices = ['zero', 'hide']
        getattr(self.emit, 'phone', self.emit)(('Reporter: ' if message.to else '') + message.text)
        return Receipt(True, external_id=str(len(self.messages)))

    def poll(self):
        messages = []
        while True:
            try:
                messages.append(self.inbox.get_nowait())
            except queue.Empty:
                return messages

    def receive(self, text, sender=None):
        self.thread_messages.append(dict(sender='Customer' if sender else 'You', text=text))
        if not sender:
            self.choices = []
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
                    payload = dict(messages=phone.thread_messages, state=phone.state, choices=phone.choices, done=phone.done)
                    self.respond(200, json.dumps(payload).encode())
                    if phone.done:
                        phone.seen_done.set()
                    return
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
            if self.done:
                self.seen_done.wait(timeout=1)
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
