from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DMG_URL = ET.parse(ROOT / "docs/appcast.xml").find("./channel/item/enclosure").attrib["url"]


def _current_mm():
    """Current release as 'major.minor' (v5.35.0 -> '5.35'), from pyproject."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ver = re.search(r'^version = "(\d+)\.(\d+)\.', text, re.M)
    return f"{ver.group(1)}.{ver.group(2)}"


def _hero(page):
    return page.split('<section class="hero"', 1)[1].split("</section>", 1)[0]


def test_landing_hero_uses_public_safe_product_screenshot():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    capture = re.search(r'src="\./(images/ccc-v(\d+)-(\d+)-hero\.png)"', _hero(page))
    assert capture is not None
    asset = ROOT / "docs" / capture.group(1)

    assert asset.is_file()
    assert f"CCC v{capture.group(2)}.{capture.group(3)} showing the session fleet" in page


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

    downloads = [cta for cta in ctas if 'id="downloadCta"' in cta]
    assert len(downloads) == 1
    assert f'href="{DMG_URL}"' in downloads[0]
    assert re.sub(r"<[^>]+>", "", downloads[0]).strip() == "Download for macOS"
    alternatives = [cta for cta in ctas if cta not in downloads]
    assert len(alternatives) == 1
    assert 'href="#install"' in alternatives[0]


def test_landing_page_names_current_release():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert f"v{_current_mm()}" in page
    assert "v5.8" not in page


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
