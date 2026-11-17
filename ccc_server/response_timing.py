"""Opt-in response-write measurements without logging request content."""

import json
import re
import time


class ResponseTimingMixin:
    def end_headers(self):
        measurement = getattr(self, "headers", {}).get("X-CCC-Timing", "")
        if not re.fullmatch(r"[0-9a-f]{16,32}", measurement):
            return super().end_headers()

        # An earlier Server-Timing stamp cannot account for scheduling or
        # blocked writes between preparing and actually sending the headers.
        self.send_header("X-CCC-Timing", measurement)
        emitted = time.time_ns() / 1_000_000
        self.send_header("Server-Timing", 'emit;desc="%.3f"' % emitted)
        started = time.perf_counter()
        self._response_measurement = {"id": measurement, "emit_epoch_ms": emitted}
        result = super().end_headers()
        self._response_measurement.update(
            headers_done_epoch_ms=time.time_ns() / 1_000_000,
            header_write_ms=(time.perf_counter() - started) * 1000,
        )
        return result

    def handle_one_request(self):
        # A keep-alive connection must not reuse the previous request's opt-in.
        self._response_measurement = None
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
