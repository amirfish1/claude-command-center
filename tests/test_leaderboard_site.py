import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "site" / "leaderboard" / "index.html"


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_page_is_standalone_and_all_relative_links_exist():
    parser = Elements(HTML.read_text())
    ids = {attrs["id"] for _, attrs in parser.elements if "id" in attrs}
    for tag, attrs in parser.elements:
        if tag == "link" or (tag == "script" and "src" in attrs):
            raise AssertionError("The page must not need a build, remote assets, or site CSS.")
        href = attrs.get("href", "")
        if href.startswith("#"):
            assert href[1:] in ids
        elif href and not href.startswith("https://"):
            assert (HTML.parent / href).exists(), href


def test_table_has_accessible_states_keyboard_scrolling_and_safe_text_rendering():
    html = HTML.read_text()
    parser = Elements(html)
    assert "[hidden]{display:none!important}" in html
    assert "prefers-reduced-motion:reduce" in html
    assert ":focus-visible" in html
    assert any(tag == "caption" for tag, _ in parser.elements)
    headers = [attrs for tag, attrs in parser.elements if tag == "th"]
    assert len(headers) == 7 and all(attrs.get("scope") == "col" for attrs in headers)
    assert any(attrs.get("role") == "region" and attrs.get("tabindex") == "0" for _, attrs in parser.elements)
    assert any(attrs.get("id") == "board-status" and attrs.get("aria-live") == "polite" for _, attrs in parser.elements)
    assert "<noscript>" in html
    assert "e.textContent=text" in html and "innerHTML" not in html
    assert "m.pass_rate*100" in html
    assert 'ms===null)return "Not measured"' in html
    assert 'ms<1000)return ms+" ms"' in html
    assert "timeZone:\"UTC\"" in html
    assert 'fetch("data.json"' in html
    assert "AbortController" in html
    assert "cost per race" not in html
    assert "no cherry picking" not in html.lower()
    assert "m.name" not in html and "m.platform" not in html
    assert "—" not in html


def test_local_benchmark_directions_match_the_existing_ui_and_route():
    html = HTML.read_text()
    assert "/free-models" in html
    assert "Run the race" in html
    assert "Run the race" in (ROOT / "static" / "free-models.html").read_text()
    assert 'path == "/free-models"' in (ROOT / "server.py").read_text()
    assert "scores is always a separate, manual step" in html
    assert "log prompts for training" in html
    assert len([1 for tag, attrs in Elements(html).elements if tag == "li" and attrs.get("class") == "task"]) == 5


def test_plugin_wrapper_is_metadata_only_with_actual_license_and_version():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "ccc-dashboard"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", manifest["name"])
    assert manifest["license"] == "FSL-1.1-MIT"
    assert "FSL-1.1-MIT" in (ROOT / "LICENSE").read_text()
    assert f'version = "{manifest["version"]}"' in (ROOT / "pyproject.toml").read_text()
    assert manifest["repository"] == "https://github.com/amirfish1/claude-command-center"
    assert manifest["homepage"] == "https://ccc.amirfish.ai"
    assert not set(manifest).intersection({"hooks", "mcpServers", "commands", "skills", "agents", "lspServers"})
    assert set(manifest["author"]) == {"name"}
