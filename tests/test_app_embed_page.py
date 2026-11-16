"""The app pane shares conversation controls without loading hidden panels."""

from html.parser import HTMLParser
from pathlib import Path

from ccc_server.app_embed import conversation_page


SOURCE = (Path(__file__).resolve().parents[1] / "static/index.html").read_text()


class Assets(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.ids = set()
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if attrs.get("id"):
            self.ids.add(attrs["id"])


def test_embed_preserves_all_shared_controls_and_conversation_extensions():
    before = Assets(SOURCE)
    after = Assets(conversation_page(SOURCE))
    assert before.ids == after.ids
    for name in ("app", "notify", "local-speech", "browser-voice", "codex-live-source",
                 "question-tags", "restart-transport", "restart-recovery", "session-recaps",
                 "free-runtime", "free-router", "queue-ticket-submit", "limit-failover"):
        assert f"/static/{name}.js" in after.scripts
    assert len(after.scripts) < len(before.scripts) / 2
    assert not any("settings-" in url or "onboarding/" in url for url in after.scripts)


def test_embed_boot_precedes_renderer_and_keeps_icon_markup_intact():
    html = conversation_page(SOURCE)
    assert html.index('/static/app-embed-boot.js') < html.index('/static/app.js')
    assert '<link rel="preload" as="script" href="/static/app.js">' in html
    icon = next(line for line in SOURCE.splitlines() if 'rel="icon"' in line)
    assert icon in html
    assert 'class="voice-call-btn-off conversation-popout ccc-app-embed"' in html
