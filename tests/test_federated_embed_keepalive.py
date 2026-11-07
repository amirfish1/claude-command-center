"""Kept-alive peer embeds: the popout switches conversations in place when its
embedding CCC asks, and only honors that from trusted dashboard origins."""

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text()
SIDEBAR_JS = (ROOT / "static" / "federated-sidebar.js").read_text()


def _function_source(src, name):
    start = src.index("function " + name + "(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(name + " not closed")


class TestEmbedHostOriginTrusted(unittest.TestCase):
    def _run(self, own_origin, origins):
        fn = _function_source(APP_JS, "embedHostOriginTrusted")
        program = (
            "const own = new URL(%s);"
            "const window = { location: { origin: own.origin, hostname: own.hostname } };"
            "%s\n"
            "console.log(JSON.stringify(%s.map(embedHostOriginTrusted)));"
        ) % (json.dumps(own_origin), fn, json.dumps(origins))
        out = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
        return dict(zip(origins, json.loads(out.stdout)))

    def test_trusted_and_untrusted_parents(self):
        got = self._run("https://box.tail1a2b.ts.net", [
            "https://box.tail1a2b.ts.net",        # itself
            "http://127.0.0.1:8090",              # a local dashboard
            "http://localhost:8090",
            "http://100.64.12.34:8090",         # tailnet address
            "https://mac.tail1a2b.ts.net",        # same tailnet
            "https://evil.tail9999.ts.net",       # another tailnet
            "https://ts.net.evil.com",
            "https://example.com",
            "http://100.200.1.1",                 # outside 100.64/10
            "null",
            "",
        ])
        self.assertEqual([k for k, v in got.items() if v], [
            "https://box.tail1a2b.ts.net", "http://127.0.0.1:8090", "http://localhost:8090",
            "http://100.64.12.34:8090", "https://mac.tail1a2b.ts.net"])

    def test_non_tailnet_host_trusts_no_ts_net_origin(self):
        got = self._run("http://127.0.0.1:8091", ["https://mac.tail1a2b.ts.net"])
        self.assertFalse(got["https://mac.tail1a2b.ts.net"])


class TestEmbedMessageContract(unittest.TestCase):
    def test_popout_listener_guards(self):
        fn = _function_source(APP_JS, "wireEmbedHostMessages")
        self.assertIn("ev.source !== window.parent", fn)
        self.assertIn("embedHostOriginTrusted(ev.origin)", fn)
        self.assertIn("'ccc:embed-open'", fn)
        self.assertRegex(fn, re.escape("/^[A-Za-z0-9_.:-]{1,200}$/"))
        # Replies go to the sender's origin, never '*'.
        self.assertNotIn("'*'", fn)
        self.assertEqual(APP_JS.count("postMessage({ type: 'ccc:embed-"), 2)

    def test_host_reuses_one_frame_per_machine(self):
        self.assertIn("frames[node.node_id]", SIDEBAR_JS)
        self.assertIn("type: 'ccc:embed-open'", SIDEBAR_JS)
        self.assertIn("type: 'ccc:embed-hello'", SIDEBAR_JS)
        close = _function_source(SIDEBAR_JS, "closeEmbed")
        self.assertIn("host.hidden = true", close)   # kept alive, not removed
        self.assertNotIn(".remove()", close)


if __name__ == "__main__":
    unittest.main()
