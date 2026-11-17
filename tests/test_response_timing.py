"""Live HTTP checks for content-free, request-scoped response measurements."""

import http.client
import http.server
import json
import socket
import threading

import pytest

import server


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
    output = capsys.readouterr().out
    if len(measurement) == 16:
        assert output.count("[response-timing]") == 1
        record = json.loads(output.split("[response-timing] ")[1])
        assert record["id"] == measurement
        assert record["emit_epoch_ms"] <= record["headers_done_epoch_ms"] <= record["done_epoch_ms"]
        assert record["header_write_ms"] >= 0 and record["completed"]
        assert not any(value in output for value in ["private", "secret", "body", "value"])
    else:
        assert output == ""


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
