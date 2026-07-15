from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DMG_URL = (
    "https://github.com/amirfish1/claude-command-center/"
    "releases/latest/download/ccc.dmg"
)


def _hero(page):
    return page.split('<section class="hero"', 1)[1].split("</section>", 1)[0]


def test_landing_hero_uses_public_safe_product_screenshot():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    asset = ROOT / "docs" / "images" / "ccc-live-session-workspace.png"

    assert asset.is_file()
    assert './images/ccc-live-session-workspace.png?v=1' in page
    assert "CCC live workspace showing sessions, a conversation, and its linked issue queue" in page


def test_landing_page_declares_its_existing_favicon():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert '<link rel="icon" href="/brand/favicon.svg" type="image/svg+xml">' in page


def test_public_stats_page_declares_its_existing_favicon():
    page = (ROOT / "docs" / "stats" / "index.html").read_text(encoding="utf-8")

    assert '<link rel="icon" href="/brand/favicon.svg" type="image/svg+xml">' in page


def test_landing_hero_has_one_direct_download_cta():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    hero = _hero(page)
    ctas = re.findall(
        r'<a\b[^>]*class="[^"]*\bbtn\b[^"]*"[^>]*>.*?</a>',
        hero,
        re.S,
    )

    assert len(ctas) == 1
    assert 'id="downloadCta"' in ctas[0]
    assert f'href="{DMG_URL}"' in ctas[0]
    assert re.sub(r"<[^>]+>", "", ctas[0]).strip() == "DOWNLOAD CCC"


def test_download_is_the_only_emphasized_action_above_fold():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    above_fold = page.split('<div class="demo boot-target"', 1)[0]

    assert 'class="star"' not in above_fold


def test_landing_hero_keeps_alternative_installers_below_fold():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    hero = _hero(page)

    assert 'id="quickInstall"' not in hero
    assert "Tour the live demo" not in hero
    assert "CCC_FROM=landing bash" in page.split('<section id="install">', 1)[1]


def test_download_tracking_never_replaces_link_navigation():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    handler = page.split("const downloadCta =", 1)[1].split(
        "// Scroll-reveal",
        1,
    )[0]

    assert 'downloadCta.addEventListener("click"' in handler
    assert "navigator.sendBeacon(DOWNLOAD_EVENT_URL)" in handler
    assert "keepalive: true" in handler
    assert 'referrerPolicy: "no-referrer"' in handler
    assert "preventDefault" not in handler
    assert "window.location" not in handler
