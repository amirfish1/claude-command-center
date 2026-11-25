"""CCC-678: the runtime globals must precede archive-boot.js.

archive-boot.js starts the page's first sidebar list read as soon as it runs.
It reads window.__CCC_LIST_READER_PORT__ to send that read to the
conversation-reader process; injected before </head>, the port arrived after
the read had already gone to the busy dashboard.
"""
import os
import unittest
from unittest import mock

import server
from ccc_server import list_reader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RuntimeGlobalsOrderTest(unittest.TestCase):
    def _inject(self, html):
        with mock.patch.object(list_reader, "enabled", return_value=True), \
             mock.patch.object(server._conv_reader, "is_child", return_value=False), \
             mock.patch.object(server._conv_reader, "status",
                               return_value={"ready": True, "http_port": 18091}):
            return server._inject_runtime_globals(html, 8091)

    def test_globals_precede_archive_boot_in_real_template(self):
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
            html = self._inject(f.read())
        port = html.index("window.__CCC_LIST_READER_PORT__=18091;")
        boot = html.index('<script src="/static/archive-boot.js">')
        self.assertLess(port, boot)
        self.assertEqual(html.count("window.__CCC_HTTP_PORT__=8091;"), 1)

    def test_head_with_attributes_and_missing_head(self):
        html = self._inject('<html><HEAD data-x="1"><script src="a.js"></script></HEAD></html>')
        self.assertTrue(html.startswith('<html><HEAD data-x="1">\n<script>window.__CCC_HTTP_PORT__'))
        no_head = self._inject("<html><body></body></html>")
        self.assertNotIn("__CCC_HTTP_PORT__", no_head)


if __name__ == "__main__":
    unittest.main()
