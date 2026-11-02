"""Restricted native service APIs: temporary metadata, inert credential backend."""
import json
import sys
from pathlib import Path

import server
from ccc_server import byok, secret_store, vault


def configure(base):
    """No Keychain subprocess, real credentials, or provider network requests."""
    base = Path(base)
    byok._state_dir = lambda: base / 'provider-metadata'
    vault._vault_state_dir = lambda: base / 'credential-metadata'
    secret_store.keychain_available = lambda: True
    secret_store.keychain_set = lambda *args: True
    secret_store.keychain_delete = lambda *args: None

    def forbidden(*args, **kwargs):
        raise AssertionError('Credential reads and external processes are forbidden')
    secret_store.keychain_get = forbidden
    server.byok_get_key = forbidden
    server.vault_get_value = forbidden
    server.byok_usage_summary = lambda **kwargs: {'spawns': 0}
    server.COMMAND_CENTER_STATE_DIR = base
    # Values are obvious inert fixtures; the fake backend never retains them.
    byok.byok_set_key('example-profile', 'openai', 'not-a-secret')
    byok.byok_set_key('example-profile', 'openrouter', 'not-a-secret')
    vault.vault_create_entry('example-login', 'not-a-secret', kind='login', service='Example service', username='Example user')
    return forbidden


class Handler(server.CommandCenterHandler):
    base = None

    def _phone_pin_blocked(self, path): return False
    def _check_proxy_secret(self): return True
    def log_message(self, *args): pass

    def do_GET(self):
        route = self.path.split('?')[0]
        if route in ('/api/vault', '/api/byok/profiles', '/api/byok/usage'):
            if (self.base / 'fail-list').exists():
                return self.send_json({'error': 'Fixture list unavailable'}, 503)
            return super().do_GET()
        if route == '/':
            body = (self.base / 'fixture.html').read_bytes()
            mime = 'text/html; charset=utf-8'
        elif route in ('/static/app.css', '/static/service-connections.css', '/static/service-connections.js'):
            body = (Path.cwd() / route.lstrip('/')).read_bytes()
            mime = 'text/css' if route.endswith('.css') else 'application/javascript'
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ('/api/byok/keys', '/api/byok/keys/delete', '/api/vault/entries', '/api/vault/entries/update', '/api/vault/entries/delete'):
            return self.send_error(405)
        if (self.base / 'fail-save').exists():
            if not self._check_same_origin(): return
            return self.send_json({'ok': False, 'error': 'Fixture storage unavailable'}, 503)
        return super().do_POST()


if __name__ == '__main__':
    base = Path(sys.argv[1])
    forbidden = configure(base)
    server.subprocess.Popen = forbidden
    server.subprocess.run = forbidden
    Handler.base = base
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    print(json.dumps({'url': 'http://127.0.0.1:' + str(httpd.server_address[1])}), flush=True)
    httpd.serve_forever()
