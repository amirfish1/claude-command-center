"""Regression coverage for CCC's unified human-readable activity log.

See ACTIVITY_LOG_FILE / _log_activity: a single append-only log of
spawn/inject/kill/codex-app-server-health events, formatted to match
~/.watchtower/activity.log so the two can be tailed side by side.
"""

import importlib
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


class ActivityLogReadBackTests(unittest.TestCase):
    """Round-trip: _log_activity writes, _parse_activity_log_line /
    _read_activity_log must read the same events back out."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_parses_a_written_line_back_into_its_fields(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "engine=codex session=abc123")
                events = server._read_activity_log()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["category"], "spawn")
        self.assertEqual(ev["verb"], "SPAWN")
        self.assertEqual(ev["detail"], "engine=codex session=abc123")
        self.assertTrue(ev["ts"].endswith("UTC"))

    def test_filters_by_session_id_substring(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "engine=codex session=aaa111")
                server._log_activity("spawn", "SPAWN", "engine=codex session=bbb222")
                server._log_activity("inject", "INJECT", "session=aaa111 mode=send")
                events = server._read_activity_log(session_id="aaa111")
        self.assertEqual(len(events), 2)
        self.assertTrue(all("aaa111" in e["detail"] for e in events))

    def test_no_session_id_returns_everything(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "a")
                server._log_activity("inject", "INJECT", "b")
                events = server._read_activity_log()
        self.assertEqual(len(events), 2)

    def test_respects_limit_keeping_most_recent(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                for i in range(5):
                    server._log_activity("spawn", "SPAWN", f"n={i}")
                events = server._read_activity_log(limit=2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["detail"], "n=3")
        self.assertEqual(events[1]["detail"], "n=4")

    def test_missing_log_file_returns_empty_list(self):
        server = self.server
        with mock.patch.object(server, "ACTIVITY_LOG_FILE", Path("/no/such/file/activity.log")):
            self.assertEqual(server._read_activity_log(), [])

    def test_garbage_limit_falls_back_to_default(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "a")
                events = server._read_activity_log(limit="not-a-number")
        self.assertEqual(len(events), 1)


class ActivityLogLineParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_returns_none_for_too_short_a_line(self):
        server = self.server
        self.assertIsNone(server._parse_activity_log_line("not a real log line"))

    def test_returns_none_for_empty_line(self):
        server = self.server
        self.assertIsNone(server._parse_activity_log_line(""))

    def test_detail_containing_double_spaces_is_preserved_whole(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "prompt=\"a  b   c\"")
                events = server._read_activity_log()
        self.assertEqual(events[0]["detail"], 'prompt="a  b   c"')


class ActivityLogPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_collapses_internal_whitespace(self):
        server = self.server
        self.assertEqual(
            server._activity_log_preview("hello\n\nworld   again"),
            "hello world again",
        )

    def test_truncates_long_text_with_ellipsis(self):
        server = self.server
        preview = server._activity_log_preview("x" * 500, limit=20)
        self.assertEqual(len(preview), 21)  # 20 chars + the ellipsis char
        self.assertTrue(preview.endswith("…"))

    def test_leaves_short_text_untouched(self):
        server = self.server
        self.assertEqual(server._activity_log_preview("short"), "short")

    def test_never_raises_on_non_string_input(self):
        server = self.server
        self.assertEqual(server._activity_log_preview(None), "")
        self.assertEqual(server._activity_log_preview(12345), "12345")


class LogActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_appends_one_formatted_line(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "engine=codex session=abc123")
            content = log_path.read_text(encoding="utf-8")
        self.assertIn("UTC", content)
        self.assertIn("spawn", content)
        self.assertIn("SPAWN", content)
        self.assertIn("engine=codex session=abc123", content)
        self.assertEqual(content.count("\n"), 1)

    def test_appends_dont_clobber_earlier_lines(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "first")
                server._log_activity("inject", "INJECT", "second")
            lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("first", lines[0])
        self.assertIn("second", lines[1])

    def test_swallows_write_failure(self):
        server = self.server
        unwritable = Path("/nonexistent-root-only-path/activity.log")
        with mock.patch.object(server, "ACTIVITY_LOG_FILE", unwritable):
            server._log_activity("spawn", "SPAWN", "should not raise")  # no exception


class CodexAppServerHealthLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def test_wedged_transport_logs_before_replacement(self):
        server = self.server
        transport = mock.Mock()
        transport.alive.return_value = True
        transport.started_at = 1000.0
        transport.proc = mock.Mock(pid=777)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=False), \
             mock.patch.object(server, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(server, "_resolve_codex_bin", return_value={"available": False}), \
             mock.patch.object(server.time, "time", return_value=1200.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            server._ensure_codex_app_server(allow_stdio=False)
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("WEDGED", kinds)
        wedged_call = next(c for c in log_activity.call_args_list if c.args[1] == "WEDGED")
        self.assertIn("pid=777", wedged_call.args[2])
        self.assertIn("age=200", wedged_call.args[2])

    def test_dead_transport_logs_before_replacement(self):
        server = self.server
        transport = mock.Mock()
        transport.alive.return_value = False
        transport.started_at = 1000.0
        transport.proc = mock.Mock(pid=888)
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_codex_managed_app_server_enabled", return_value=False), \
             mock.patch.object(server, "_resolve_codex_bin", return_value={"available": False}), \
             mock.patch.object(server.time, "time", return_value=1050.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            server._ensure_codex_app_server(allow_stdio=False)
        kinds = [c.args[1] for c in log_activity.call_args_list]
        self.assertIn("DEAD", kinds)
        dead_call = next(c for c in log_activity.call_args_list if c.args[1] == "DEAD")
        self.assertIn("pid=888", dead_call.args[2])

    def test_healthy_responsive_transport_does_not_log(self):
        server = self.server
        transport = mock.Mock()
        transport.alive.return_value = True
        transport.started_at = 1000.0
        with mock.patch.object(server, "_CODEX_APP_SERVER_TRANSPORT", transport), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZED", True), \
             mock.patch.object(server, "_CODEX_APP_SERVER_INITIALIZING", False), \
             mock.patch.object(server, "_CODEX_APP_SERVER_LAST_LIVE_CHECK", 0.0), \
             mock.patch.object(server, "_codex_app_server_transport_responsive", return_value=True), \
             mock.patch.object(server.time, "time", return_value=1010.0), \
             mock.patch.object(server, "_log_activity") as log_activity:
            result = server._ensure_codex_app_server(allow_stdio=False)
        self.assertIs(result, transport)
        log_activity.assert_not_called()


class ActivityLogEndpointTests(unittest.TestCase):
    """End-to-end: real HTTP request through CommandCenterHandler, not a
    direct function call, so this also catches routing/query-parsing bugs
    a unit test on _read_activity_log alone would miss."""

    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def _serve(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, timeout=5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def test_endpoint_returns_seeded_events_filtered_by_session(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "engine=codex session=matchme")
                server._log_activity("spawn", "SPAWN", "engine=codex session=other")
                base = self._serve()
                with urllib.request.urlopen(
                    base + "/api/activity-log?session_id=matchme", timeout=5
                ) as response:
                    data = json.loads(response.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["events"]), 1)
        self.assertIn("matchme", data["events"][0]["detail"])

    def test_endpoint_without_session_id_returns_everything(self):
        server = self.server
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "activity.log"
            with mock.patch.object(server, "ACTIVITY_LOG_FILE", log_path):
                server._log_activity("spawn", "SPAWN", "a")
                server._log_activity("inject", "INJECT", "b")
                base = self._serve()
                with urllib.request.urlopen(base + "/api/activity-log", timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["events"]), 2)

    def test_endpoint_on_a_missing_log_file_returns_empty_not_an_error(self):
        server = self.server
        with mock.patch.object(server, "ACTIVITY_LOG_FILE", Path("/no/such/file/activity.log")):
            base = self._serve()
            with urllib.request.urlopen(base + "/api/activity-log", timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["events"], [])


if __name__ == "__main__":
    unittest.main()
