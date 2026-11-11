"""Shared HTTP and durable batch acknowledgments for owner chat adapters."""
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from .channels import Channel, _NoRedirect, _response_body
from .store import Inbound, Receipt


def http(method, url, *, headers, data, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, _response_body(response), dict(response.headers)


def quote(value):
    return urllib.parse.quote(str(value), safe='')


class ChatChannel(Channel):
    can_receive = True
    base = ''
    auth_scheme = 'Bearer'

    def __init__(self, cfg, **kw):
        if kw.get('http') is None:
            kw['http'] = http
        super().__init__(cfg, **kw)
        self.pending = None

    def _key(self, name):
        identity = [self.type, self._opt('owner'), self._opt('channel_id'),
                    self._opt('api_base', self.base), self._opt('token_vault')]
        digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
        return super()._key(digest + ':' + name)

    @property
    def owner(self):
        return self._opt('owner')

    def send(self, msg):
        self._definite_failure = False
        result = super().send(msg)
        if self._definite_failure:
            result.uncertain = False
        return result

    def call(self, method, path, body=None, query=None):
        if self.clock() < float(self._get('retry_at', 0)):
            self._definite_failure = True
            raise OSError('Rate limited. Waiting for Retry-After.')
        token = self._secret('token_vault')
        if not token:
            self._definite_failure = True
            raise OSError('No access token is saved.')
        url = self._opt('api_base', self.base).rstrip('/') + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        result = self.http(method, url, headers={
            'Authorization': self.auth_scheme + ' ' + token,
            'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body).encode(), timeout=20)
        status, raw = result[:2]
        headers = {k.lower(): v for k, v in (result[2] if len(result) > 2 else {}).items()}
        data = json.loads(raw)
        if status == 429:
            fallback = data.get('retry_after', data.get('retry_after_ms', 60000) / 1000) if isinstance(data, dict) else 60
            seconds = headers.get('retry-after', fallback)
            try:
                seconds = max(1, float(seconds))
            except (ValueError, TypeError):
                seconds = 60
            self._set('retry_at', self.clock() + seconds)
            self._definite_failure = True
            raise OSError('Rate limited. Waiting for Retry-After.')
        if status in (401, 403):
            self._definite_failure = True
            raise OSError('Token or permissions rejected.')
        if not 200 <= status < 300 or isinstance(data, dict) and data.get('ok') is False:
            self._definite_failure = status < 500
            # Do not reflect provider payloads or tokens into logs.
            raise OSError('Chat API rejected the request (HTTP %s).' % status)
        return data

    def roots(self):
        return dict(self._get('roots', {}) or {})

    def remember(self, external_id, msg):
        if not external_id:
            raise OSError('Chat API returned no message id.')
        roots = self.roots()
        if msg.thread and not msg.reply_to:
            roots[str(external_id)] = msg.thread
            self._set('roots', roots)
        return Receipt(ok=True, external_id=str(external_id))

    def inbound(self, ident, sender, text, root, *, bot=False):
        roots = self.roots()
        if (bot or sender != self.owner or sender == self._get('self')
                or root not in roots or not text or not ident):
            return None
        return Inbound(self.id, str(ident), sender, str(text),
                       thread=roots[root], reply_to=root, received_at=self.clock())

    def batch(self, messages, updates):
        self.pending = (set(m.external_id for m in messages), updates)
        if not messages:
            self.ack_poll([])
        return messages

    def ack_poll(self, messages):
        """Engine calls only after every returned message has been durably claimed."""
        if self.pending is None:
            return
        ids, updates = self.pending
        if not ids.issubset({m.external_id for m in messages}):
            return
        for key, value in updates.items():
            self._set(key, value)
        self.pending = None
