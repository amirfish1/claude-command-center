"""Real ntfy adapter and loopback HTTP faults, no external destination."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
from ccc_server.oncall import alerts
from ccc_server.oncall.channels import NtfyChannel
from ccc_server.oncall.store import Store


def test_accept_then_disconnect_does_not_retry(tmp_path):
    accepted = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            accepted.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    store = Store(tmp_path)
    channel = NtfyChannel({'id': 'local', 'type': 'ntfy', 'topic': 'proof',
                           'server': f'http://127.0.0.1:{server.server_port}'}, kv=store)
    alert, _ = store.alert_upsert('one', kind='permission', title='Choose', body='')
    try:
        assert not alerts.send_alert(store, [channel], alert)
        assert not alerts.send_alert(store, [channel], alert)
        assert len(accepted) == 1
        assert 'unknown' in store.delivery_latest(alert['id'], 'local')['error']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        store.close()


def test_ten_minute_refusal_then_recovery(tmp_path):
    # Refusal is known to happen before acceptance. Advance only the engine clock.
    reservation = socket.socket()
    reservation.bind(('127.0.0.1', 0))
    port = reservation.getsockname()[1]
    reservation.close()
    store = Store(tmp_path)
    cfg = {'channels': [{'id': 'local'}], 'hourly_cap': 12}
    channel = NtfyChannel({'id': 'local', 'type': 'ntfy', 'topic': 'proof',
                           'server': f'http://127.0.0.1:{port}'}, kv=store)
    alert, _ = store.alert_upsert('one', kind='permission', title='Choose', body='')
    for seconds in range(0, 601, 120):
        assert alerts.deliver(cfg, store, {'local': channel}, now=10000 + seconds) == 0
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id":"local-proof"}')
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        assert alerts.deliver(cfg, store, {'local': channel}, now=10720) == 1
        assert alerts.deliver(cfg, store, {'local': channel}, now=10840) == 0
        assert store.alert(alert['id'])['sent_at'] == 10720
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        store.close()
