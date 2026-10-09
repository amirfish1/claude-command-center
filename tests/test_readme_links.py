import html
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GUIDES = (
    "docs/install.md",
    "docs/cli.md",
    "docs/engine-support.md",
    "docs/byok-and-vault.md",
    "docs/features.md",
    "docs/decision-inbox.md",
    "docs/orchestration.md",
    "docs/configuration.md",
    "docs/architecture.md",
    "docs/GETTING_STARTED.md",
)


def prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


class HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if not value:
                continue
            if key in ("href", "src"):
                self.targets.append(value)
            elif key == "srcset":
                self.targets.extend(item.strip().split()[0] for item in value.split(",") if item.strip())
            elif key == "id" or (key == "name" and tag == "a"):
                self.anchors.add(value)


def links(text):
    text = prose(text)
    parser = HTMLLinks()
    parser.feed(text)
    targets = list(parser.targets)
    definitions = {}
    for match in re.finditer(r'^\s{0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))', text, re.M):
        definitions[" ".join(match[1].lower().split())] = match[2] or match[3]
    targets.extend(
        match[1].strip("<>")
        for match in re.finditer(r'\]\(\s*(<[^>]+>|(?:\\.|[^\s()]|\([^()]*\))+)(?:\s+"[^"]*"|\s+\x27[^\x27]*\x27)?\s*\)', text)
    )
    for match in re.finditer(r'!?\[([^\]]+)\]\[([^\]]*)\]', text):
        label = " ".join((match[2] or match[1]).lower().split())
        targets.append(definitions.get(label, "missing-reference:" + label))
    for label, target in definitions.items():
        if re.search(r'\[' + re.escape(label) + r'\](?![\[(]|:)', text, re.I):
            targets.append(target)
    return list(dict.fromkeys(targets))


def anchors(text):
    text = prose(text)
    parser = HTMLLinks()
    parser.feed(text)
    result = set(parser.anchors)
    seen = {}
    for match in re.finditer(r'^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$', text, re.M):
        heading = re.sub(r'!?\[([^\]]+)\]\([^)]*\)', r'\1', match[1])
        heading = html.unescape(re.sub(r'<[^>]*>', '', heading))
        slug = re.sub(r'[^\w\s-]', '', heading.lower()).replace(' ', '-')
        suffix = seen.get(slug, 0)
        seen[slug] = suffix + 1
        result.add(slug + (f"-{suffix}" if suffix else ""))
    return result


def broken_links(source):
    errors = []
    for target in links(source.read_text(encoding="utf-8")):
        if target.startswith("missing-reference:"):
            errors.append(target)
            continue
        url = urlsplit(html.unescape(target))
        if url.scheme or url.netloc:
            continue
        destination = (source.parent / unquote(url.path)).resolve() if url.path else source
        if not destination.exists():
            errors.append(target + " (missing file)")
        elif url.fragment and destination.suffix.lower() in (".md", ".html"):
            if unquote(url.fragment) not in anchors(destination.read_text(encoding="utf-8")):
                errors.append(target + " (missing anchor)")
    return errors


class ReadmeTests(unittest.TestCase):
    def test_readme_stays_short_and_keeps_the_first_screen(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 350)
        gallery = text.index("## See CCC at work")
        for item in ("Your coding agents outgrew your terminal.", "scripts/install.sh", "brew install ccc", "## Start free in 5 minutes"):
            self.assertLess(text.index(item), gallery)
        hero = re.search(r"docs/images/ccc-v[\d-]+-hero\.png", text)
        self.assertIsNotNone(hero)
        self.assertLess(hero.start(), gallery)
        for item in ("## Quickstart", "## Engine support", "Running on Windows", "install.ps1", ".\\run.ps1", "WSL2", "systemd", "/api/usage/current", "## License"):
            self.assertIn(item, text)
        self.assertIn("<!-- star-history:start -->", text)
        self.assertIn("<!-- star-history:end -->", text)

    def test_readme_and_moved_guides_have_no_broken_local_links(self):
        for relative in ("README.md", "README.ja.md") + GUIDES:
            with self.subTest(file=relative):
                source = ROOT / relative
                self.assertTrue(source.is_file(), relative)
                self.assertEqual(broken_links(source), [], relative)

    def test_japanese_readme_is_linked_and_keeps_install_path(self):
        self.assertIn("(README.ja.md)", (ROOT / "README.md").read_text(encoding="utf-8"))
        text = (ROOT / "README.ja.md").read_text(encoding="utf-8")
        self.assertIn("(README.md)", text)
        for item in ("scripts/install.sh", "brew install ccc", "install.ps1", "./run.sh", "localhost:8090"):
            self.assertIn(item, text)

    def test_readme_retains_seeded_gallery_images(self):
        targets = links((ROOT / "README.md").read_text(encoding="utf-8"))
        for image in ("fleet-scan", "flow-canvas", "split-pane", "search", "group-chat", "queues", "queue-workers", "mobile"):
            self.assertIn(f"docs/images/feature-wall/{image}.gif", targets)
        for image in ("dark", "light"):
            self.assertIn(f"assets/star-history/star-history-{image}.svg", targets)


class LinkCheckerTests(unittest.TestCase):
    def check(self, text, files=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in (files or {}).items():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
            source = root / "README.md"
            source.write_text(text, encoding="utf-8")
            return broken_links(source)

    def test_detects_missing_file_and_fragment(self):
        self.assertEqual(self.check('[gone](gone.md) [bad](guide.md#absent)', {"guide.md": "# Present\n"}), ["gone.md (missing file)", "guide.md#absent (missing anchor)"])

    def test_checks_reference_links_and_images(self):
        self.assertEqual(self.check('[guide][g] ![picture][p]\n\n[g]: guide.md\n[p]: absent.png', {"guide.md": "# Guide"}), ["absent.png (missing file)"])
        self.assertEqual(self.check('[guide][unknown]'), ["missing-reference:unknown"])
        self.assertEqual(self.check('[guide][]\n\n[guide]: absent.md'), ["absent.md (missing file)"])

    def test_checks_html_srcset_and_explicit_anchors(self):
        self.assertEqual(self.check('<a href="guide.html#there">guide</a><img src="ok.png"><source srcset="ok.png 1x, gone.png 2x">', {"guide.html": '<h1 id="there">Guide</h1>', "ok.png": "image"}), ["gone.png (missing file)"])

    def test_accepts_encoded_paths_queries_titles_and_duplicates(self):
        self.assertEqual(self.check('[guide](guide%20one.md?raw=1#title-1 "Read more")', {"guide one.md": "# Title\n# Title\n"}), [])
        self.assertEqual(self.check('[guide](guide%20one.md#missing)', {"guide one.md": "# Title\n"}), ["guide%20one.md#missing (missing anchor)"])

    def test_ignores_code_and_external_links(self):
        self.assertEqual(self.check('```md\n[example](absent.md)\n```\n~~~md\n<img src="absent.png">\n~~~\n[web](https://example.com/page) [mail](mailto:reader@example.com) [cdn](//example.com/image.png)'), [])

    def test_checks_nested_image_links_and_shortcut_references(self):
        self.assertEqual(self.check('[![image](ok.png)](gone.md)\n[guide]\n\n[guide]: absent.md', {"ok.png": "image"}), ["gone.md (missing file)", "absent.md (missing file)"])


if __name__ == "__main__":
    unittest.main()
