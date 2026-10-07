import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "compare.md"
IMAGE = ROOT / "docs" / "images" / "how-it-compares.svg"
NS = {"svg": "http://www.w3.org/2000/svg"}
NAMES = ("CCC", "freellmapi", "9Router", "OpenRouter", "Claude Squad", "Conductor", "Nimbalyst", "Crystal")
SOURCE_LABELS = {"ccc", "freellmapi", "9router", "openrouter", "squad", "conductor", "nimbalyst", "crystal"}


def test_svg_is_accessible_and_scalable():
    root = ET.parse(IMAGE).getroot()
    assert root.tag == f"{{{NS['svg']}}}svg"
    assert root.get("width") == "1200"
    assert root.get("height") == "860"
    assert root.get("viewBox") == "0 0 1200 860"
    assert root.get("role") == "img"
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    labels = root.get("aria-labelledby", "").split()
    assert len(labels) == 2
    assert {ids[label].tag.rsplit("}", 1)[-1] for label in labels} == {"title", "desc"}
    assert all("".join(ids[label].itertext()).strip() for label in labels)


@pytest.mark.parametrize("name", NAMES)
def test_every_tool_has_visible_text_and_a_guide_entry(name):
    root = ET.parse(IMAGE).getroot()
    visible = " ".join("".join(el.itertext()) for el in root.findall(".//svg:text", NS))
    assert name in visible
    assert name in DOC.read_text()


def test_comparison_distinguishes_roles_and_crystal_replacement():
    root = ET.parse(IMAGE).getroot()
    text = " ".join(root.itertext())
    assert "Crystal is now Nimbalyst" in text
    assert "backends, not rivals" in text
    guide = DOC.read_text()
    assert "Replaced by Nimbalyst" in guide
    assert "not a ranking" in guide
    assert "never sends your Claude subscription OAuth token" in guide
    for group in ("ccc-card", "routers-card", "orchestrators-card", "request-flow"):
        assert root.find(f".//svg:g[@id='{group}']", NS) is not None


def test_svg_is_static_and_self_contained():
    root = ET.parse(IMAGE).getroot()
    forbidden = {"script", "foreignObject", "image", "animate", "animateMotion", "animateTransform", "set"}
    for el in root.iter():
        assert el.tag.rsplit("}", 1)[-1] not in forbidden
        for key, value in el.attrib.items():
            key = key.rsplit("}", 1)[-1].lower()
            assert not key.startswith("on")
            if key == "href":
                assert value.startswith("#")
    style = root.find("svg:style", NS).text
    assert "@import" not in style.lower()
    assert "animation" not in style.lower()
    for target in re.findall(r"url\(([^)]+)\)", style):
        assert target.strip(" '\"").startswith("#")


def test_both_color_schemes_have_complete_palettes():
    style = ET.parse(IMAGE).getroot().find("svg:style", NS).text
    assert "@media (prefers-color-scheme: dark)" in style
    palettes = re.findall(r":root\s*\{([^}]+)\}", style)
    assert len(palettes) == 2
    values = [dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})", palette)) for palette in palettes]
    assert values[0].keys() == values[1].keys()
    assert {"--bg", "--panel", "--ink", "--muted"} <= values[0].keys()
    assert values[0] != values[1]
    assert "—" not in DOC.read_text() + IMAGE.read_text()


def test_local_links_and_fragments_resolve():
    guide = DOC.read_text()
    links = re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", guide)
    assert "images/how-it-compares.svg" in links
    for link in links:
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc:
            continue
        target = (DOC.parent / unquote(parsed.path)).resolve() if parsed.path else DOC
        assert target.is_file(), link
        assert target.is_relative_to(ROOT), link
        if parsed.fragment:
            headings = re.findall(r"^#{1,6}\s+(.+)$", target.read_text(), re.MULTILINE)
            slugs = {re.sub(r"[^\w\s-]", "", heading.lower()).replace(" ", "-") for heading in headings}
            assert unquote(parsed.fragment) in slugs, link


def test_all_source_references_are_used_and_pinned():
    guide = DOC.read_text()
    definitions = dict(re.findall(r"^\[([^\]]+)\]:\s+(\S+)$", guide, re.MULTILINE))
    uses = re.findall(r"\[[^\]\n]+\]\[([^\]]+)\]", guide)
    assert set(definitions) == SOURCE_LABELS
    assert set(uses) == SOURCE_LABELS
    for label in uses:
        assert re.fullmatch(r"https://github\.com/[^/]+/[^/]+/blob/[0-9a-f]{40}/README\.md", definitions[label])
    assert "/62310cf63a007991be9b24a2d03e82216b7664ed/" in definitions["ccc"]


def test_guide_is_discoverable_and_changelog_is_one_bullet():
    related = (ROOT / "docs" / "free-models.md").read_text().split("## Related", 1)[1]
    assert "[How CCC compares](compare.md)" in related
    changelog = (ROOT / "changelog.d" / "added-how-it-compares-2026-10-06.md").read_text().strip()
    assert changelog.startswith("- ")
    assert len(changelog.splitlines()) == 1
