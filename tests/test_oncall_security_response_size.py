from http.server import BaseHTTPRequestHandler
import pytest
from ccc_server.oncall import channels
from tests.test_oncall_security_redirects import endpoint

@pytest.mark.parametrize('status', [200, 500])
def test_channel_http_response_is_bounded(status):
    class Large(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'x' * (1024 * 1024 + 1))
    with endpoint(Large) as url:
        with pytest.raises(OSError, match='too large'):
            channels.http_request('GET', url)
