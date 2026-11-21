"""Live HTTP checks for content-free, request-scoped response measurements."""

import http.client
import http.server
import json
import socket
import sys
import threading
import time
import re

import pytest

import server


def _timing_lines(output):
    prefix = "[response-timing] "
    return [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]


def test_reused_app_read_accounting_excludes_idle_time(monkeypatch):
    records = []
    monkeypatch.setattr(server._cpu_accounting, 'record',
                        lambda path, cpu, wall: records.append((path, wall)))

    class Handler(server.CommandCenterHandler):
        def do_GET(self):
            self.send_json({'ok': True})

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
    try:
        connection.request('GET', '/view/demo')
        response = connection.getresponse()
        response.read()
        assert response.version == 11 and not response.will_close
        time.sleep(.25)
        connection.request('GET', '/view/demo')
        response = connection.getresponse()
        response.read()
        handler_ms = float(re.search(r'app;dur=(\d+)', response.getheader('Server-Timing'))[1])
    finally:
        connection.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert len(records) == 2
    assert abs(records[1][1] * 1000 - handler_ms) < 100


@pytest.mark.parametrize("measurement", ["0123456789abcdef", "private-value", ""])
def test_response_measurements_are_opt_in_and_content_free(measurement, capsys):
    class Handler(server.CommandCenterHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"body")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
    try:
        connection.request("GET", "/private?secret=value", headers={"X-CCC-Timing": measurement})
        response = connection.getresponse()
        assert response.read() == b"body"
        assert response.getheader("X-CCC-Timing") == (measurement if len(measurement) == 16 else None)
        if len(measurement) == 16:
            assert 'emit;desc="' in response.getheader("Server-Timing")
        # Reusing the socket without opting in must produce no second record.
        connection.request("GET", "/unmeasured")
        response = connection.getresponse()
        assert response.read() == b"body"
        assert response.getheader("X-CCC-Timing") is None
    finally:
        connection.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    # Another test's background thread can write after the timing record.
    print("[fixture] unrelated diagnostic")
    timing_lines = _timing_lines(capsys.readouterr().out)
    if len(measurement) == 16:
        line, = timing_lines
        record = json.loads(line)
        assert record["id"] == measurement
        assert record["emit_epoch_ms"] <= record["headers_done_epoch_ms"] <= record["done_epoch_ms"]
        assert record["header_write_ms"] >= 0 and record["completed"]
        assert not any(value in line for value in ["private", "secret", "body", "value"])
    else:
        assert timing_lines == []


def test_malformed_request_can_return_an_error_before_headers_exist(capsys):
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as connection:
            connection.sendall(b"GET / HTTP/invalid\r\n\r\n")
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        assert b"400" in response
    finally:
        thread.join(timeout=5)
        httpd.server_close()
    assert "[response-timing]" not in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "linux" or not hasattr(socket, "TCP_INFO"),
                    reason="Linux socket metadata")
def test_socket_wait_distinguishes_backlog_from_handler_and_keepalive(capsys):
    class Handler(server.CommandCenterHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            time.sleep(.12)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
    # Put a complete request in the kernel backlog before starting accept().
    sent_epoch_ms = time.time() * 1000
    connection.request("GET", "/", headers={"X-CCC-Timing": "0123456789abcdef"})
    time.sleep(.15)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        response = connection.getresponse()
        assert response.read() == b""
        assert "socket_wait;dur=" in response.getheader("Server-Timing")
        # A measured keepalive request must not count idle time as backlog.
        time.sleep(.2)
        connection.request("GET", "/", headers={"X-CCC-Timing": "fedcba9876543210"})
        response = connection.getresponse()
        assert response.read() == b""
        assert "socket_wait;dur=" in response.getheader("Server-Timing")
    finally:
        connection.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    records = [json.loads(line) for line in _timing_lines(capsys.readouterr().out)]
    assert len(records) == 2
    # The 120ms handler delay must not be included in the 150ms backlog age.
    assert records[0]["socket_wait_ms"] >= 100
    assert records[0]["socket_wait_ms"] <= records[0]["emit_epoch_ms"] - sent_epoch_ms - 100
    assert 0 <= records[1]["socket_wait_ms"] < 100


@pytest.mark.skipif(sys.platform != "linux" or not hasattr(socket, "TCP_INFO"),
                    reason="Linux socket metadata")
def test_socket_wait_measures_a_queued_request_on_a_reused_socket(capsys):
    first_started = threading.Event()
    release_first = threading.Event()

    class Handler(server.CommandCenterHandler):
        def do_GET(self):
            if self.path == "/view/first":
                first_started.set()
                release_first.wait(timeout=5)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01})
    thread.start()
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as connection:
            connection.sendall(b"GET /view/first HTTP/1.1\r\nHost: localhost\r\n\r\n")
            assert first_started.wait(timeout=5)
            connection.sendall(b"GET /view/second HTTP/1.1\r\nHost: localhost\r\n"
                               b"Connection: close\r\nX-CCC-Timing: fedcba9876543210\r\n\r\n")
            time.sleep(.2)
            release_first.set()
            with connection.makefile("rb") as responses:
                first = responses.readline()
                while responses.readline() not in (b"\r\n", b""):
                    pass
                second = responses.read()
            assert first.startswith(b"HTTP/1.1 200")
            assert b" 200 " in second and b"socket_wait;dur=" in second
    finally:
        release_first.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    records = [json.loads(line) for line in _timing_lines(capsys.readouterr().out)]
    assert len(records) == 1
    assert records[0]["socket_wait_ms"] >= 150


def test_socket_metadata_unavailable_keeps_response_measurement_optional():
    from ccc_server.response_timing import _queued_payload_ms

    class Unavailable:
        def getsockopt(self, *args):
            raise OSError("unsupported")

    assert _queued_payload_ms(Unavailable(), time.perf_counter()) is None
