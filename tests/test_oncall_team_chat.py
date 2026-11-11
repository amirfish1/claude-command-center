"""Documented API fixtures, identity checks and durable polling budgets."""
import json
from urllib.parse import urlparse, parse_qs
import pytest
from ccc_server.oncall.channels import build
from ccc_server.oncall.store import Store, Outbound


class API:
    def __init__(self, kind):
        self.kind, self.calls, self.rows = kind, [], []
        self.status, self.headers = 200, {}

    def __call__(self, method, url, **kw):
        path = urlparse(url).path
        q = parse_qs(urlparse(url).query)
        body = json.loads(kw['data']) if kw.get('data') else {}
        self.calls.append((method, path, q, body))
        data = {}
        if self.status != 200:
            return self.status, b'{}', self.headers
        if self.kind == 'slack':
            data = {'ok': True}
            if path.endswith('auth.test'): data['user_id'] = 'BOT'
            if path.endswith('conversations.open'): data['channel'] = {'id': 'DM'}
            if path.endswith('conversations.info'): data['channel'] = {'is_im': True, 'user': 'OWNER'}
            if path.endswith('chat.postMessage'): data['ts'] = '100.000001'
            if path.endswith('conversations.replies'):
                start = int(q.get('cursor', ['0'])[0])
                rows = [r for r in self.rows if r['ts'] > q['oldest'][0]]
                data.update(messages=rows[start:start+15], response_metadata={
                    'next_cursor': str(start+15) if start+15 < len(rows) else ''})
        if self.kind == 'discord':
            if path.endswith('/users/@me'): data = {'id': 'BOT'}
            elif method == 'POST': data = {'id': '100'}
            elif path.endswith('/messages'):
                data = list(reversed([r for r in self.rows if int(r['id']) > int(q['after'][0])][:100]))
        if self.kind == 'mattermost':
            if '/users/' in path: data = {'id': 'BOT' if path.endswith('/me') else 'OWNER'}
            elif method == 'POST': data = {'id': '100'}
            else:
                rows = [r for r in self.rows if int(r['id']) > int(q.get('after', ['0'])[0])][:100]
                data = {'order': [r['id'] for r in reversed(rows)], 'posts': {r['id']: r for r in rows}}
        if self.kind == 'matrix':
            if path.endswith('/whoami'): data = {'user_id': 'BOT'}
            elif path.endswith('/state'): data = []
            elif '/send/' in path: data = {'event_id': '100'}
            else:
                rows = self.rows[int(q.get('since', ['0'])[0]):]
                data = {'next_batch': str(len(self.rows)), 'rooms': {'join': {'ROOM': {
                    'timeline': {'events': rows, 'limited': False}}}}}
        return 200, json.dumps(data).encode(), {}

    def reply(self, ident, sender='OWNER', root=None, bot=False):
        root = root or ('100.000001' if self.kind == 'slack' else '100')
        if self.kind == 'slack':
            row = {'ts': f'{ident}.000001', 'thread_ts': root, 'user': sender, 'text': 'keep it'}
            if bot: row['bot_id'] = 'BOT'
        elif self.kind == 'discord':
            row = {'id': str(ident), 'author': {'id': sender, 'bot': bot}, 'content': 'keep it',
                   'message_reference': {'message_id': root}}
        elif self.kind == 'mattermost':
            row = {'id': str(ident), 'user_id': sender, 'message': 'keep it', 'root_id': root,
                   'props': {'from_webhook': bot}}
        else:
            row = {'event_id': str(ident), 'sender': sender, 'type': 'm.room.message',
                   'content': {'msgtype': 'm.text', 'body': 'keep it',
                               'm.relates_to': {'rel_type': 'm.thread', 'event_id': root}}}
        self.rows.append(row)


def make(kind, tmp_path, api, clock=lambda: 1000):
    cfg = dict(type=kind, id=kind, owner='OWNER', channel_id='ROOM',
               api_base='http://localhost', token_vault='fake-token')
    return build(cfg, kv=Store(tmp_path), secret=lambda _: 'test-token', http=api, clock=clock)


@pytest.mark.parametrize('kind', ['slack', 'discord', 'matrix', 'mattermost'])
def test_identity_restart_ack_and_budget(kind, tmp_path):
    api = API(kind)
    ch = make(kind, tmp_path, api)
    assert ch.check()[0]
    assert ch.send(Outbound('decision', thread='a'*16)).ok
    api.reply(101, 'STRANGER')
    api.reply(102, 'BOT')
    api.reply(103, root='unrelated')
    api.reply(104)
    before = len(api.calls)
    messages = ch.poll()
    assert len(api.calls) - before == 1
    assert len(messages) == 1
    assert messages[0].thread == 'a'*16
    # Crash before claim: same inbox returns on a new adapter/store connection.
    ch = make(kind, tmp_path, api)
    assert ch.poll() == messages
    ch.ack_poll([])
    assert ch.poll() == messages
    assert ch.kv.inbound_claim(messages[0])
    ch.ack_poll(messages)
    ch = make(kind, tmp_path, api)
    assert ch.poll() == []
    assert not ch.kv.inbound_claim(messages[0])


@pytest.mark.parametrize('kind', ['slack', 'discord', 'matrix', 'mattermost'])
def test_rate_limit_and_token_error(kind, tmp_path):
    api = API(kind)
    now = [1000]
    ch = make(kind, tmp_path, api, lambda: now[0])
    api.status, api.headers = 429, {'Retry-After': '120'}
    assert not ch.check()[0]
    before = len(api.calls)
    assert not ch.check()[0]
    assert len(api.calls) == before
    now[0] += 121
    api.status = 401
    assert not ch.check()[0]
    assert 'Token' in ch.last_error


@pytest.mark.parametrize('kind', ['slack', 'discord', 'mattermost'])
def test_pagination(kind, tmp_path):
    api = API(kind)
    ch = make(kind, tmp_path, api)
    assert ch.check()[0]
    assert ch.send(Outbound('decision', thread='b'*16)).ok
    for ident in range(101, 332): api.reply(ident)
    seen = set()
    for _ in range(20):
        before = len(api.calls)
        rows = ch.poll()
        assert len(api.calls) - before == 1
        for row in rows:
            ch.kv.inbound_claim(row)
            seen.add(row.external_id)
        ch.ack_poll(rows)
    assert len(seen) == 231


@pytest.mark.parametrize('kind', ['slack', 'discord'])
def test_http_standin(kind, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from ccc_server.oncall.chat_common import http
    api = API(kind)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self): self.respond()
        def do_POST(self): self.respond()
        def respond(self):
            size = int(self.headers.get('Content-Length', 0))
            status, raw, headers = api(self.command, 'http://localhost'+self.path,
                                       data=self.rfile.read(size) if size else None)
            self.send_response(status)
            for k, v in headers.items(): self.send_header(k, v)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        ch = make(kind, tmp_path, api)
        ch.cfg['api_base'] = f'http://127.0.0.1:{server.server_port}'
        ch.http = http
        assert ch.check()[0]
        assert ch.send(Outbound('Decision', thread='c'*16)).ok
        for ident in range(101, 302): api.reply(ident)
        seen = set()
        for _ in range(16):
            rows = ch.poll()
            for row in rows:
                ch.kv.inbound_claim(row)
                seen.add(row.external_id)
            ch.ack_poll(rows)
        assert len(seen) == 201
        api.status, api.headers = 429, {'Retry-After': '180'}
        assert not ch.send(Outbound('rate test')).ok
        before = len(api.calls)
        ch.poll()
        assert len(api.calls) == before
        ch._set('retry_at', 0)
        api.status = 401
        assert not ch.send(Outbound('token test')).ok
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_setup_confirms_only_owner_reply(tmp_path, monkeypatch):
    import threading
    from ccc_server.oncall import chat_setup
    api = API('discord')
    ch = make('discord', tmp_path, api)
    assert ch.check()[0]
    original = ch.send
    def send(msg):
        receipt = original(msg)
        api.reply(101, 'STRANGER')
        api.reply(102)
        api.rows[-1]['content'] = 'ready'
        return receipt
    ch.send = send
    class Engine:
        _lock = threading.RLock()
        store = ch.kv
        def channels(self): return {'discord': ch}
    result = chat_setup.roundtrip(Engine(), 'discord', timeout=1)
    assert result['ok']
    assert ch.roots() == {}


def test_configuration_change_does_not_reuse_destination(tmp_path):
    api = API('slack')
    ch = make('slack', tmp_path, api)
    assert ch.check()[0]
    ch.cfg['owner'] = 'OTHER'
    assert ch._get('destination') is None
    assert not ch.check()[0]  # The fixture still returns OWNER's DM.


def test_matrix_limited_timeline_does_not_advance(tmp_path):
    api = API('matrix')
    ch = make('matrix', tmp_path, api)
    assert ch.send(Outbound('Decision', thread='e'*16)).ok
    before = ch._get('cursor')
    def limited(*a, **kw):
        return 200, json.dumps({'next_batch': 'new', 'rooms': {'join': {'ROOM': {
            'timeline': {'limited': True, 'events': []}}}}}).encode(), {}
    ch.http = limited
    assert ch.poll() == []
    assert 'gap' in ch.last_error
    assert ch._get('cursor') == before


def test_slack_poll_budget_with_many_threads(tmp_path):
    api = API('slack')
    ch = make('slack', tmp_path, api)
    assert ch.check()[0]
    ch._set('roots', {f'{n}.000001': 'a'*16 for n in range(100, 1100)})
    before = len(api.calls)
    assert ch.poll() == []
    assert len(api.calls) - before == 1
    assert ch._get('index') == 1


def test_known_rate_rejection_is_not_uncertain(tmp_path):
    api = API('discord')
    ch = make('discord', tmp_path, api)
    api.status, api.headers = 429, {'Retry-After': '30'}
    result = ch.send(Outbound('Decision'))
    assert not result.ok and not result.uncertain
