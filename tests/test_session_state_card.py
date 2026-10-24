"""Behavior checks for the compact transcript session-state card."""

from pathlib import Path
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def render_session_state(body: str, app_path: Path) -> str:
    source = app_path.read_text(encoding="utf-8")
    start = source.index("function renderSessionStateBlock(body)")
    end = source.index("\n  function normalizeTaskNotificationField", start)
    # The Needs-you action helpers (CCC-1218) sit just above the renderer.
    helpers = source.rfind("// CCC-1218:", 0, start)
    if helpers != -1:
        start = helpers
    row_helpers = source.rfind("// Row actions are model-authored replies", 0, start)
    if row_helpers != -1:
        start = row_helpers
    function_source = source[start:end]
    harness = f"""
function escapeHtml(value) {{
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}
const escapeAttr = escapeHtml;
function renderInline(value) {{
  return escapeHtml(value).replace(/`([^`]+)`/g, '<code class="md-code">$1</code>');
}}
const document = {{ addEventListener() {{}} }};
{function_source}
process.stdout.write(renderSessionStateBlock(process.argv[1]));
"""
    result = subprocess.run(
        ["node", "-e", harness, body],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class TestSessionStateCard(unittest.TestCase):
    def test_actionable_summary_leads_with_need_and_brief_reason(self):
        html = render_session_state(
            "DID: Confirmed the reviewer account.\n"
            "INSIGHT: Account mismatches can cause rejection.\n"
            "NEXT_STEP_USER: Use the same login for the form and screencast.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertIn('class="ssb-row ssb-primary ssb-next"', html)
        self.assertIn('<span class="ssb-key">Needs you</span>', html)
        self.assertIn("Use the same login for the form and screencast.", html)
        self.assertIn('class="ssb-row ssb-reason"', html)
        self.assertIn('<span class="ssb-key">Why</span>', html)
        self.assertIn("Account mismatches can cause rejection.", html)
        self.assertNotIn("Confirmed the reviewer account.", html)
        self.assertNotIn(">Did</span>", html)

    def test_no_action_summary_leads_with_completed_work(self):
        html = render_session_state(
            "DID: Updated the dashboard copy.\n"
            "INSIGHT: No migration was needed.\n"
            "NEXT_STEP_USER: None.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertIn('class="ssb-row ssb-primary ssb-done"', html)
        self.assertIn('<span class="ssb-key">Done</span>', html)
        self.assertIn("Updated the dashboard copy.", html)
        self.assertIn('<span class="ssb-key">Needs you</span>None', html)

    def test_completion_assessment_is_collapsed_and_escaped(self):
        import json
        assessment = {"version": 1,
            "resolution": {"score": 0.9, "evidence": "<script>unsafe</script>"},
            "verification": {"score": 0.8, "evidence": "Browser reproduction passed."},
            "delivery": {"score": 0, "applicable": False, "evidence": "Analysis only."},
            "decision_dependency": {"score": 0, "evidence": "No decision remains."},
            "caveats": [{"text": "Optional follow-up TEST-12.", "blocking": False}],
            "remaining_actions": []}
        html = render_session_state("DID: Finished.\nNEXT_STEP_USER: None.\nCOMPLETION: "
            + json.dumps(assessment), PROJECT_ROOT / "static" / "app.js")
        self.assertIn('<details class="ssb-completion">', html)
        self.assertIn('(0.9, 0.8, 0, 0)', html)
        self.assertNotIn('· self-reported</summary>', html)
        self.assertIn('Caveat: Optional follow-up TEST-12.', html)
        self.assertIn('Resolution <span class="ssb-score">0.9</span>', html)
        self.assertIn('Delivery <span class="ssb-score">N/A</span>', html)
        self.assertIn('Close criteria met', html)
        self.assertNotIn('data-role="archive"', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('&quot;version&quot;', html)

    def test_assessment_verdict_exposes_each_blocker_and_invalid_reports(self):
        import json
        base = {"version": 1, **{k: {"score": v, "evidence": "Checked."}
            for k, v in [("resolution", 1), ("verification", 0.9), ("delivery", 1), ("decision_dependency", 0)]},
            "caveats": [], "remaining_actions": []}
        cases = [({"resolution": {"score": 0.8, "evidence": "Partial."}}, "resolution below"),
            ({"verification": {"score": 0.5, "evidence": "Unchecked."}}, "verification below"),
            ({"delivery": {"score": 0.5, "evidence": "Local."}}, "delivery incomplete"),
            ({"decision_dependency": {"score": 0.5, "evidence": "Choice."}}, "a user decision remains"),
            ({"caveats": [{"text": "Blocked.", "blocking": True}]}, "a blocking caveat remains"),
            ({"remaining_actions": [{"text": "Finish.", "confidence": 1, "downside": 0,
                "reversible": True, "within_scope": True}]}, "unfinished actions remain"),
            ({"verification": {"score": True, "evidence": "Bad."}}, "Unassessed"),
            ({"caveats": [{"text": "Missing blocking flag."}]}, "Unassessed")]
        for change, expected in cases:
            with self.subTest(expected=expected):
                html = render_session_state("COMPLETION: " + json.dumps(dict(base, **change)), PROJECT_ROOT / "static" / "app.js")
                self.assertIn(expected, html)
                self.assertNotIn("Close criteria met", html)

    def test_pending_decision_is_visible_even_when_next_step_says_none(self):
        import json
        assessment = {"version": 1, "decision_dependency": {"score": 0.5, "evidence": "Choose a release window."}}
        html = render_session_state("DID: Prepared.\nNEXT_STEP_USER: None.\nCOMPLETION: "
            + json.dumps(assessment), PROJECT_ROOT / "static" / "app.js")
        self.assertIn('class="ssb-row ssb-primary ssb-next"', html)
        self.assertIn('Decision required: Choose a release window.', html)
        self.assertNotIn('Needs you</span>None', html)

    def test_demo_uses_the_same_compact_summary_behavior(self):
        html = render_session_state(
            "DID: Updated the demo.\n"
            "INSIGHT: The demo mirrors production.\n"
            "NEXT_STEP_USER: Review the result.",
            PROJECT_ROOT / "docs" / "demo" / "static" / "app.js",
        )

        self.assertIn('<span class="ssb-key">Needs you</span>', html)
        self.assertIn('<span class="ssb-key">Why</span>', html)
        self.assertNotIn("Updated the demo.", html)

    def test_card_styles_make_the_action_dominant_without_italics(self):
        for relative_path in (
            ("static", "app.css"),
            ("docs", "demo", "static", "app.css"),
        ):
            source = PROJECT_ROOT.joinpath(*relative_path).read_text(encoding="utf-8")
            start = source.index(".session-state-block {")
            end = source.index(".md-table {", start)
            styles = source[start:end]

            self.assertNotIn("font-style: italic", styles)
            self.assertIn(".session-state-block .ssb-primary {", styles)
            self.assertRegex(styles, r"font-size: 1[45]px")
            self.assertIn(".session-state-block .ssb-reason {", styles)
            self.assertRegex(styles, r"font-size: 1[23]px")

    def test_originating_model_action_renders_without_raw_json(self):
        html = render_session_state(
            'NEXT_STEP_USER: Say yes to restart both services.\n'
            'NEXT_ACTION: {"label":"Restart","reply":"Yes, restart the dashboard and worker."}',
            PROJECT_ROOT / "static" / "app.js",
        )
        self.assertIn('data-ssb-value="Yes, restart the dashboard and worker."', html)
        self.assertIn('>Restart</button>', html)
        self.assertNotIn('next action</span>', html)
        self.assertNotIn('&quot;label&quot;', html)

    def test_short_model_label_renders_instead_of_reply_fallback(self):
        html = render_session_state(
            'NEXT_STEP_USER: Confirm the proposed triage routine.\n'
            'NEXT_ACTION: {"label":"Build triage","reply":"Yes, build the triage routine."}',
            PROJECT_ROOT / "static" / "app.js",
        )
        self.assertIn('>Build triage</button>', html)
        self.assertIn('data-ssb-value="Yes, build the triage routine."', html)
        self.assertNotIn('>Reply</button>', html)

    def test_needs_you_offers_restart_for_ccc_kickstart(self):
        html = render_session_state(
            "NEXT_STEP_USER: Run `launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center`"
            " and the `.worker` service, then reload CCC to see the new models.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertIn('class="ssb-actions"', html)
        self.assertIn('data-ssb-act="restart"', html)
        self.assertNotIn('data-ssb-act="run"', html)
        self.assertNotIn('data-ssb-act="reload"', html)
        self.assertIn('<code class="md-code">', html)

    def test_needs_you_offers_run_copy_open_and_reply(self):
        html = render_session_state(
            "NEXT_STEP_USER: Decide whether to ship; if yes run `brew upgrade ccc`, "
            "open https://example.com/docs and review ~/dev/scratch/plan.md.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertIn('data-ssb-act="run" data-ssb-value="brew upgrade ccc"', html)
        self.assertIn('data-ssb-act="copy" data-ssb-value="brew upgrade ccc"', html)
        self.assertIn('href="https://example.com/docs"', html)
        self.assertIn('>Open example.com</a>', html)
        self.assertIn('data-path="~/dev/scratch/plan.md"', html)
        self.assertIn('>Open plan.md</a>', html)
        self.assertIn('data-ssb-act="reply"', html)

    def test_quoted_reply_becomes_one_tap_say_chip(self):
        html = render_session_state(
            'NEXT_STEP_USER: Say "ship now" or \u201chold\u201d if you want SLOTPERF-5 released '
            "before the 09:00 PT run; either way I'll delete `GMAIL_REFRESH_TOKEN`.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertIn('data-ssb-act="say" data-ssb-value="ship now"', html)
        self.assertIn('data-ssb-act="say" data-ssb-value="hold"', html)
        self.assertLess(html.index('data-ssb-value="ship now"'), html.index('data-ssb-act="reply"'))

    def test_quoted_command_is_not_a_say_chip(self):
        html = render_session_state(
            "NEXT_STEP_USER: Type `git push origin main` when ready.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertNotIn('data-ssb-act="say"', html)
        self.assertIn('data-ssb-act="run"', html)

    def test_plain_ask_gets_no_action_row(self):
        html = render_session_state(
            "NEXT_STEP_USER: Use the same login for the form and screencast.",
            PROJECT_ROOT / "static" / "app.js",
        )

        self.assertNotIn("ssb-actions", html)

    def test_run_in_terminal_passes_command_as_argv(self):
        from ccc_server import run_in_terminal as rit

        line = rit.build_shell_line('echo "a \\ b"', str(PROJECT_ROOT))
        self.assertTrue(line.startswith("cd "))
        argv = rit.osascript_argv(line)
        self.assertEqual(argv[-1], line)
        self.assertNotIn(line, " ".join(argv[:-1]))
        with self.assertRaises(ValueError):
            rit.build_shell_line("echo a\nrm x")
        with self.assertRaises(ValueError):
            rit.build_shell_line("   ")


if __name__ == "__main__":
    unittest.main()
