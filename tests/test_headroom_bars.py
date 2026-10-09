import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class PanelParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.panel_parents = None
        self.panel_attrs = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id") == "headroomBars":
            self.panel_parents = list(self.stack)
            self.panel_attrs = attributes
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def test_panel_is_hidden_in_sidebar_header_not_a_popup():
    html = (ROOT / "static/index.html").read_text()
    parser = PanelParser()
    parser.feed(html)
    assert parser.panel_attrs is not None
    assert "hidden" in parser.panel_attrs
    assert parser.panel_attrs["aria-label"] == "Plan usage left"
    # Last full-width row of the sidebar header (the brand column is too narrow).
    assert parser.panel_parents[-1][1].get("class", "").split() == ["sidebar-header"]
    assert html.count('id="headroomBars"') == 1
    assert html.count('href="/static/headroom-bars.css"') == 1
    assert html.count('src="/static/headroom-bars.js"') == 1
    source = (ROOT / "static/headroom-bars.js").read_text()
    for forbidden in ("innerHTML", "cccNotify", "cccFx", "/api/sessions", "alert(", "Notification("):
        assert forbidden not in source


def test_styles_are_scoped_and_honor_reduced_motion():
    css = (ROOT / "static/headroom-bars.css").read_text()
    assert "prefers-reduced-motion: reduce" in css
    assert "transition: none" in css
    assert "repeat(auto-fill, minmax(96px, 1fr))" in css
    assert ".sidebar-header:has(> .headroom-bars:not([hidden]))" in css
    # The strip itself stays in the header flow; only the shared tooltip
    # (on <body>, so the sidebar cannot clip it) is fixed-positioned.
    strip_css = css.split(".hb-tip {")[0]
    assert "position: fixed" not in strip_css
    assert "position: absolute" not in css
    assert css.count("position: fixed") == 1
    for color in ("--green", "--orange", "--red"):
        assert color in css


def test_headroom_browser_behavior():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for browser helper tests")
    result = subprocess.run(
        [node, "--test", "tests/headroom-bars.test.cjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
