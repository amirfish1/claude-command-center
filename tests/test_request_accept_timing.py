"""Separate server dispatch from handler work without timing idle keep-alive."""
import http.client
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import server


def test_first_request_reports_dispatch_but_keepalive_does_not():
    accepted = threading.Event()
    release = threading.Event()

    class DelayedServer(server.ThreadedHTTPServer):
        def finish_request(self, request, address):
            accepted.set()
            assert release.wait(5)
            return super().finish_request(request, address)

    class Handler(server.CommandCenterHandler):
        protocol_version = 'HTTP/1.1'

        def do_GET(self):
            self.send_json({'ok': True})

        def log_message(self, *args):
            pass

    httpd = DelayedServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)

    def read():
        connection.request('GET', '/')
        response = connection.getresponse()
        response.read()
        return response.getheader('Server-Timing')

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(read)
            assert accepted.wait(5)
            # Model a handler waiting for dispatch after socket acceptance.
            assert not release.wait(0.08)
            release.set()
            timing = first.result(timeout=5)
        wait = next(part for part in timing.split(',') if 'req_wait;' in part)
        duration = float(wait.split('dur=')[1].split(';')[0])
        assert duration >= 60
        assert 'req_wait;' not in read()
        assert httpd._request_accept_times == {}
    finally:
        release.set()
        connection.close()
        httpd.shutdown()
        httpd.server_close()
        serving.join(timeout=5)


def test_rejected_connection_retires_accept_timestamp():
    class RejectServer(server.ThreadedHTTPServer):
        def verify_request(self, request, address):
            return False

    httpd = RejectServer(('127.0.0.1', 0), server.CommandCenterHandler)
    connection = socket.create_connection(httpd.server_address, timeout=5)
    try:
        httpd.handle_request()
        assert httpd._request_accept_times == {}
    finally:
        connection.close()
        httpd.server_close()
