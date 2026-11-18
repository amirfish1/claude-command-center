"""Reuse framed app reads without changing other routes' connection behavior."""

import re
import socket
import time
from urllib.parse import parse_qs, urlsplit


def _app_read(path):
    parsed = urlsplit(path)
    if re.fullmatch(r"/static/[A-Za-z0-9_./-]+", parsed.path):
        return True
    if re.fullmatch(r"/(?:view|app-static)/[a-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?", parsed.path):
        return True
    if parsed.path != "/":
        return False
    query = parse_qs(parsed.query)
    return bool(query.get("ccc_app") or (query.get("embed") == ["app"] and
                (query.get("ccc_popout") == ["conversation"] or
                 query.get("popout") == ["conversation"])))


class ReadKeepAliveMixin:
    read_idle_timeout = 5.0

    def handle_one_request(self):
        # A reused connection can next carry an unrelated API call. Restore
        # the original protocol before parsing every request.
        self.protocol_version = type(self).protocol_version
        self._ccc_read_keepalive = False
        self._ccc_read_status = None
        try:
            return super().handle_one_request()
        finally:
            if self._ccc_read_keepalive and not self.close_connection:
                self._ccc_read_previous_timeout = self.connection.gettimeout()
                self.connection.settimeout(self.read_idle_timeout)

    def parse_request(self):
        # Only the idle request-line wait is bounded. Restore the original
        # timeout for header/body processing and slow response downloads.
        if hasattr(self, "_ccc_read_previous_timeout"):
            self.connection.settimeout(self._ccc_read_previous_timeout)
            del self._ccc_read_previous_timeout
            # Do not attribute keep-alive idle time to the next handler.
            self._ccc_req_t0 = time.time()
            self._ccc_req_cpu0 = time.thread_time()
        ok = super().parse_request()
        if (ok and self.command == "GET" and self.request_version == "HTTP/1.1"
                and "close" not in {part.strip().lower() for part in
                                    ",".join(self.headers.get_all("Connection", [])).split(",")}
                and self.headers.get_all("Content-Length", []) in ([], ["0"])
                and not self.headers.get_all("Transfer-Encoding")
                and not self.headers.get_all("Expect") and _app_read(self.path)):
            self.protocol_version = "HTTP/1.1"
            self.close_connection = False
            self._ccc_read_keepalive = True
            # Headers and body are separate writes. Nagle plus delayed ACKs
            # otherwise adds about 40 ms to small reused responses.
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (AttributeError, OSError):
                pass
        return ok

    def send_response(self, code, message=None):
        self._ccc_read_status = code
        return super().send_response(code, message)

    def end_headers(self):
        if getattr(self, "_ccc_read_keepalive", False):
            fields = [line.partition(b":") for line in self._headers_buffer[1:]]
            lengths = [value.strip() for key, _, value in fields
                       if key.lower() == b"content-length"]
            transfer = any(key.lower() == b"transfer-encoding" for key, _, _ in fields)
            framed = (self._ccc_read_status in (204, 304) or
                      bool(lengths) and all(value.isdigit() for value in lengths)
                      and len(set(lengths)) == 1)
            if not framed or transfer:
                self.send_header("Connection", "close")
                self.close_connection = True
        return super().end_headers()
