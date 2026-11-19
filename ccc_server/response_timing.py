"""Opt-in response-write measurements without logging request content."""

import json
import re
import socket
import struct
import sys
import time


def _queued_payload_ms(connection, request_started):
    """Lower bound on payload age at a request boundary.

    Linux TCP_INFO's last_data_recv is milliseconds since the latest payload
    arrived. Subtract time since the boundary; later segments reduce the bound.
    This reads socket metadata, never packet content, and needs no privileges.
    """
    if sys.platform != "linux" or not hasattr(socket, "TCP_INFO"):
        return None
    try:
        info = connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
        elapsed = (time.perf_counter() - request_started) * 1000
        if len(info) < 56:
            return None
        age = struct.unpack_from("=I", info, 52)[0]
        return max(0.0, age - elapsed)
    except (OSError, AttributeError, TypeError, struct.error):
        return None


class ResponseTimingMixin:
    def parse_request(self):
        if not getattr(self, "_response_first_request", True):
            # The request line has arrived. On a reused connection, the
            # earlier handle_one_request timestamp includes the idle wait.
            self._response_started = time.perf_counter()
        return super().parse_request()

    def end_headers(self):
        measurement = getattr(self, "headers", {}).get("X-CCC-Timing", "")
        if not re.fullmatch(r"[0-9a-f]{16,32}", measurement):
            return super().end_headers()

        # An earlier Server-Timing stamp cannot account for scheduling or
        # blocked writes between preparing and actually sending the headers.
        self.send_header("X-CCC-Timing", measurement)
        queued_ms = None
        if (hasattr(self, "_response_started") and self.command == "GET"
                and self.headers.get("Content-Length", "0") == "0"
                and not self.headers.get("Transfer-Encoding")):
            queued_ms = _queued_payload_ms(self.connection, self._response_started)
            if queued_ms is not None:
                self.send_header("Server-Timing", "socket_wait;dur=%.3f" % queued_ms)
        emitted = time.time_ns() / 1_000_000
        self.send_header("Server-Timing", 'emit;desc="%.3f"' % emitted)
        started = time.perf_counter()
        self._response_measurement = {"id": measurement, "emit_epoch_ms": emitted}
        if queued_ms is not None:
            self._response_measurement["socket_wait_ms"] = queued_ms
        result = super().end_headers()
        self._response_measurement.update(
            headers_done_epoch_ms=time.time_ns() / 1_000_000,
            header_write_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    def handle_one_request(self):
        # A keep-alive connection must not reuse the previous request's opt-in.
        self._response_measurement = None
        self._response_started = time.perf_counter()
        self._response_first_request = not getattr(self, "_response_connection_seen", False)
        self._response_connection_seen = True
        completed = False
        try:
            result = super().handle_one_request()
            completed = True
            return result
        finally:
            if self._response_measurement is not None:
                measurement = self._response_measurement
                measurement.update(
                    done_epoch_ms=time.time_ns() / 1_000_000,
                    completed=completed,
                )
                print("[response-timing] " + json.dumps(measurement), flush=True)
