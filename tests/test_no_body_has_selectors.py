"""Guard: no `body:has(...)` / `html:has(...)` selectors in the dashboard.

A :has() anchored on <body> or <html> makes every DOM write anywhere in the
page re-match that :has() across the whole document at the next layout read.
On a phone (the session list stays mounted under the reader, ~20k elements)
each forced style recalc cost ~130ms, and "Load earlier messages" forces a
dozen of them: 2-5 s of frozen main thread per page in Chrome at 390px.
The dashboard keeps body flag classes in sync instead (syncBodyHasFlags in
static/app.js). Static-source guard, like tests/test_conv_view_toggle.py.
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# html / body, optionally with classes or :not(...), then :has(
_ANCHORED_HAS = re.compile(
    r"(?<![\w.#\-`])(?:html|body)(?:\.[\w-]+|:not\([^()]*\))*:has\(")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//"))


class NoBodyHasSelectorsTest(unittest.TestCase):
    def test_no_body_or_html_anchored_has(self):
        offenders = []
        files = sorted(
            glob.glob(os.path.join(ROOT, "static", "*.css"))
            + glob.glob(os.path.join(ROOT, "static", "*.js"))
            + glob.glob(os.path.join(ROOT, "static", "*.html")))
        self.assertTrue(files)
        for path in files:
            with open(path, encoding="utf-8") as fh:
                code = _strip_comments(fh.read())
            for m in _ANCHORED_HAS.finditer(code):
                line = code[:m.start()].count("\n") + 1
                offenders.append("%s:%d (comment-stripped) %s" % (
                    os.path.relpath(path, ROOT), line, code[m.start():m.start() + 70]))
        self.assertEqual(offenders, [], "use a body flag class (syncBodyHasFlags) instead")

    def test_pattern_catches_the_old_rules(self):
        for sel in ("body:has(.f2c-config) #pwaInstallBanner",
                    "html:has(body.has-mobile-bottom-nav)",
                    "body.ccc-free-on:has(.x) #y",
                    "body:not(.a):has(.b)"):
            self.assertRegex(sel, _ANCHORED_HAS)
        for sel in (".conv-pane:has(> .status-rail)",
                    "#convToolbar:has(.mobile-actions-menu)",
                    "body .main > #localCiMobileReader:not(:has(> .x))",
                    "`body:has(...)`"):
            self.assertNotRegex(sel, _ANCHORED_HAS)

    def test_flags_used_by_css_are_maintained(self):
        js = _read("static/app.js")
        block = js[js.index("const _BODY_HAS_FLAGS = ["):js.index("function syncBodyHasFlags(")]
        maintained = set(re.findall(r"\['(has-[\w-]+)'", block))
        used = {
            "has-f2c-config": "static/app.css",
            "has-img-lightbox-open": "static/app.css",
            "has-settings-modal-open": "static/app.css",
            "has-start-view": "static/issue-flow.css",
            "has-upd-overlay-open": "static/mobile-ticket-record.js",
            "has-mobile-nav-menu-open": "static/mobile-app-nav.js",
            "has-new-session-composer": "static/free-runtime.js",
        }
        for flag, rel in used.items():
            self.assertIn("body." + ("ccc-free-on." if flag == "has-new-session-composer" else "") + flag,
                          _read(rel), flag)
            self.assertIn(flag, maintained, flag)
        self.assertIn("document.documentElement.classList.toggle('has-mobile-bottom-nav', show);", js)
        self.assertIn("html.has-mobile-bottom-nav { --ccc-rail-w: 0px !important; }", _read("static/app.css"))

    def test_covered_session_list_skips_rendering_on_phones(self):
        css = _read("static/app.css")
        m = re.search(
            r"body:not\(\.kanban-split\)\.mobile-show-main \.sidebar \{([^}]*)\}", css)
        self.assertIsNotNone(m)
        self.assertIn("content-visibility: hidden;", m.group(1))
        # Hidden only after the reader's slide-in finishes.
        self.assertIn("allow-discrete", m.group(1))


if __name__ == "__main__":
    unittest.main()
