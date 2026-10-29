"""Ticket detail: acceptance line, checks and verdicts (CCC-1222).

WatchTower WT-5/WT-6 store `accept`, `gates`, `gate_results`, `gate_pending`,
`gate_accepted_by`, `gate_feedback` and `verifier` on the ticket. The CCC
ticket modal renders them through `_uxqChecksHtml`; this runs the real
function under node against fixture tickets.
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

STUBS = """
const escapeHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const escapeAttr = escapeHtml;
const _uxqRelTime = iso => 'ago';
const _uxFixesIdentityKey = v => String(v || '').toLowerCase();
const _uxqHealthCache = { wt_workers: [{ worker_id: 'verify-CCC-9', session_id: 'sess-verifier' }] };
"""


def _extract(src, name):
    m = re.search(r"  function " + name + r"\(.*?\n  \}\n", src, re.S)
    assert m, name
    return m.group(0)


def _render(item):
    src = (ROOT / "static" / "app.js").read_text()
    code = STUBS + "".join(_extract(src, n) for n in ("_uxqWorkerSid", "_uxqGateName", "_uxqChecksHtml"))
    code += "process.stdout.write(_uxqChecksHtml(" + json.dumps(item) + "));"
    res = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise AssertionError(res.stderr)
    return res.stdout


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ChecksHtmlTest(unittest.TestCase):
    def test_no_acceptance_data_renders_nothing(self):
        self.assertEqual(_render({"ref": "CCC-1", "status": "open"}), "")

    def test_verifier_pending_after_cmd_passed(self):
        html = _render({
            "ref": "CCC-9", "status": "in_review", "accept": "tests pass",
            "effective_gates": ["cmd:pytest -q", "verify", "review"],
            "gate_pending": "verify",
            "gate_results": [{"gate": "cmd:pytest -q", "passed": True, "exit_code": 0,
                              "output_tail": "3 passed", "seconds": 4.2, "at": "x"}],
            "verifier": {"engine": "claude", "model": "claude-sonnet-5", "worker_id": "verify-CCC-9"},
        })
        self.assertIn("tests pass", html)
        self.assertIn('is-passed">passed</span><span class="uxq-check-name">Command: pytest -q', html)
        self.assertIn("exit 0", html)
        self.assertIn('is-pending">running</span><span class="uxq-check-name">Independent verifier', html)
        self.assertIn("claude / claude-sonnet-5", html)
        self.assertIn('data-open-sid="sess-verifier"', html)
        self.assertIn('is-none">not run yet</span><span class="uxq-check-name">Review by submitter', html)
        # gate order preserved
        self.assertLess(html.index("Command:"), html.index("Independent verifier"))
        self.assertLess(html.index("Independent verifier"), html.index("Review by"))

    def test_failed_cmd_sends_back_with_output(self):
        html = _render({
            "ref": "CCC-9", "status": "open", "gates": ["cmd:make test"],
            "gate_feedback": "gate cmd:make test failed (exit 2): boom",
            "gate_results": [{"gate": "cmd:make test", "passed": False, "exit_code": 2,
                              "output_tail": "boom"}],
        })
        self.assertIn("Sent back: gate cmd:make test failed (exit 2): boom", html)
        self.assertIn('is-failed">failed', html)
        self.assertIn("exit 2", html)
        self.assertIn("<summary>output</summary><pre>boom</pre>", html)

    def test_failed_verifier_shows_findings(self):
        html = _render({
            "ref": "CCC-9", "status": "in_progress", "gates": ["verify"],
            "gate_feedback": "independent verification failed: button missing",
            "gate_results": [{"gate": "verify", "passed": False, "output_tail": "button missing",
                              "engine": "codex", "model": "gpt-6"}],
        })
        self.assertIn("<summary>findings</summary><pre>button missing</pre>", html)
        self.assertIn("codex / gpt-6", html)

    def test_rejected_and_accepted_reviews(self):
        rejected = _render({"ref": "CCC-9", "status": "in_progress", "gates": ["review:orchestrator"],
                            "gate_feedback": "rejected by human: wrong copy"})
        self.assertIn('is-failed">rejected</span><span class="uxq-check-name">Review by orchestrator', rejected)
        self.assertIn("Sent back: rejected by human: wrong copy", rejected)
        accepted = _render({"ref": "CCC-9", "status": "closed", "gates": ["review"],
                            "gate_accepted_by": "dashboard"})
        self.assertIn('is-passed">accepted', accepted)
        self.assertIn("by dashboard", accepted)
        self.assertNotIn("Sent back", accepted)


class ServerPayloadTest(unittest.TestCase):
    def test_item_payload_carries_effective_gates(self):
        import server
        from unittest import mock
        with mock.patch.object(server._q, "effective_gates", create=True,
                               return_value=["cmd:true", "review"]):
            out = server._uxq_item_payload({"ref": "CCC-9", "status": "open", "history": []})
        self.assertEqual(out["effective_gates"], ["cmd:true", "review"])


if __name__ == "__main__":
    unittest.main()
