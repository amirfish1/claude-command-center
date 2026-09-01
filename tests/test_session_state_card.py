"""Behavior checks for the compact transcript session-state card."""

from pathlib import Path
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def render_session_state(body: str, app_path: Path) -> str:
    source = app_path.read_text(encoding="utf-8")
    start = source.index("function renderSessionStateBlock(body)")
    end = source.index("\n  function normalizeTaskNotificationField", start)
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
        self.assertNotIn("Needs you", html)

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
            self.assertIn("font-size: 15px", styles)
            self.assertIn(".session-state-block .ssb-reason {", styles)
            self.assertIn("font-size: 13px", styles)


if __name__ == "__main__":
    unittest.main()
