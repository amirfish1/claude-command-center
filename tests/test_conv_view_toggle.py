"""The transcript view is a Compact | Normal | Verbose segmented control.

Static-source guard: all three segments render in one control, persisted under
'ccc-conv-view', Verbose stays the only mode that expands tools, and the
compact CSS beats the conv-bg themes' !important padding.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class ConvViewToggleTest(unittest.TestCase):
    def test_three_segments_visible(self):
        html = _read("static/index.html")
        # Pane template + main composer carry all three; the mobile top-bar
        # toggle (mobile-view-seg) is deliberately Compact | Normal only.
        for seg, expected in (("compact", 3), ("normal", 3), ("verbose", 2)):
            self.assertEqual(html.count('data-conv-view="%s"' % seg), expected, seg)
        mobile = re.search(r'mobile-view-seg[^>]*>(.*?)</span>', html, re.S).group(1)
        self.assertNotIn('data-conv-view="verbose"', mobile)

    def test_modes_persist(self):
        js = _read("static/app.js")
        self.assertIn("const CONV_VIEW_MODES = ['compact', 'normal', 'verbose'];", js)
        self.assertIn("document.body.dataset.convView = mode;", js)
        self.assertIn("localStorage.setItem('ccc-conv-view', mode)", js)
        self.assertIn("function convVerboseOn() { return convViewMode() === 'verbose'; }", js)
        self.assertIn("document.body.classList.toggle('conv-compact', mode === 'compact')", js)

    def test_legacy_verbose_key_still_honored(self):
        js = _read("static/app.js")
        self.assertIn("localStorage.getItem('ccc-conv-verbose') === '1' ? 'verbose' : 'normal'", js)

    def test_compact_collapses_and_outranks_stitch_theme(self):
        css = _read("static/app.css")
        rule = re.search(
            r"body\.conv-compact\.conv-compact \.conversations-view \.event\.assistant \.assistant-text \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("padding: 3px 10px !important", rule.group(1))
        # Collapsing, not shrinking: messages clamp to two lines and the
        # newest assistant reply is exempt.
        self.assertIn(".event.assistant:has(~ .event.assistant .assistant-text) .assistant-text:not(.cc-open)", css)
        self.assertIn("-webkit-line-clamp: 2;", css)

    def test_tap_expands_in_compact(self):
        js = _read("static/app.js")
        self.assertIn("t.closest('.assistant-text, .event.user_text, .thinking-block, .tool-call')", js)
        self.assertIn("el.classList.toggle('cc-open')", js)

    def test_tool_runs_fold_between_text_messages(self):
        js = _read("static/app.js")
        css = _read("static/app.css")
        self.assertIn("function _ccMarkRuns(view)", js)
        self.assertIn("if (active.length >= 2 || (active.length && prevText)) {", js)
        # Devin/Codex/ACP: tool calls and thinking live inside the
        # .event.assistant next to the text, so they are units too.
        self.assertIn("else if (cc.contains('tool-call')) { found = true; units.push({ el: c, kind: 'tool', calls: 1 }); }", js)
        self.assertIn("else if (cc.contains('thinking-block')) { found = true; units.push({ el: c, kind: 'think' }); }", js)
        # The observer is only live while compact is on.
        self.assertIn("_ccSyncRunsObserver(mode === 'compact');", js)
        # Declared before the boot-time sync call (const TDZ).
        self.assertLess(js.index("const _ccRunsObserver"), js.index("_syncConvViewSegs(convViewMode());"))
        self.assertIn(".cc-run-tail:not(.cc-run-open) { display: none !important; }", css)

    def test_run_chip_rides_on_preceding_text(self):
        js = _read("static/app.js")
        css = _read("static/app.css")
        self.assertIn("chip.className = 'cc-run-chip';", js)
        self.assertIn("_ccChipHost(prevText).appendChild(chip);", js)
        # Empty span + ::before: copy / read-aloud never see the label.
        self.assertIn('.cc-run-chip::before { content: "\\25B8 " attr(data-cc-run-label); }', css)
        self.assertIn(".cc-run-head.cc-run-chipped:not(.cc-run-open) { display: none !important; }", css)
        # Chip-bearing text is never two-line clamped (chip would be cut).
        self.assertIn(".assistant-text:not(.cc-open):not(.cc-final):not(:has(.cc-run-chip)),", css)

    def test_turn_final_summary_keeps_card(self):
        js = _read("static/app.js")
        css = _read("static/app.css")
        self.assertIn("if (u.el.classList.contains('user_text')) { markFinal(turnText); turnText = null; }", js)
        self.assertIn(".assistant-text.cc-final {", css)
        self.assertIn(":not(.cc-final):not(:has(.cc-run-chip))", css)

    def test_compact_tones_down_text(self):
        css = _read("static/app.css")
        # ID-level specificity beats the stitch theme's 0,9,0 !important.
        self.assertIn("body.conv-compact:not(#cc-compact) .conversations-view .event.assistant .assistant-text {", css)
        self.assertIn("font-weight: 400 !important; letter-spacing: normal !important;", css)


if __name__ == "__main__":
    unittest.main()
