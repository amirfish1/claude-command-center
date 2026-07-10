#!/usr/bin/env python3
"""Build the CCC Updates hub from `updates/*.md` into `docs/updates/`.

WHAT THIS DOES
    Reads one markdown file per update from `updates/` (YAML front matter + a
    short pain-to-proof body) and emits, into `docs/updates/`:
        index.html      reverse-chron cards, problem-family filter chips, search
        <slug>.html     one page per PUBLISHED update, per-page OG + Twitter meta
        feed.xml        Atom 1.0, absolute URLs on https://ccc.amirfish.ai
        updates.json    machine-readable index (external consumers, the feed)
        styles.css      one shared stylesheet (dark product theme)
    Drafts (status: draft) are skipped. Re-running on an unchanged tree is a
    no-op diff.

DESIGN CHOICE: stdlib-only, no dependencies
    CLAUDE.md's "stdlib-only" ethos governs the shipped product; this build
    script honors the same spirit so `python3 scripts/updates_build.py` runs
    with nothing installed. The one honest gap is that the stdlib has no
    Markdown parser, so this file ships a *small, deliberately constrained*
    renderer (see `render_markdown`) covering exactly the subset an update body
    needs: paragraphs, `##`/`###` headings, `**bold**`, `` `code` ``,
    `[links]()`, `![images]()`, fenced code blocks, and `-`/`*`/`1.` lists.

    TRADEOFF: that renderer is not CommonMark. It handles the template's subset
    and nothing more. If a future author needs tables, nested lists, blockquotes,
    or footnotes, swap `render_markdown`/`render_inline` for the `markdown`
    package (build-time only, never shipped in `server.py`):

        try:
            import markdown  # pip install markdown  (build-time dependency only)
        except ImportError:
            raise SystemExit(
                "This build was switched to the `markdown` package. Run:\n"
                "    python3 -m pip install markdown\n"
                "then re-run: python3 scripts/updates_build.py"
            )

    Until that need is real, the zero-dependency renderer is the better fit for
    a repo that prizes owning its small pieces.

USAGE
    python3 scripts/updates_build.py            # build + self-validate
    python3 scripts/updates_build.py --check    # build, then hard-fail on any
                                                # feed/HTML parse error (CI use)
"""
from __future__ import annotations

import html.parser
import json
import os
import re
import sys
import xml.dom.minidom
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

# ── Constants ────────────────────────────────────────────────────────────────
SITE = "https://ccc.amirfish.ai"
REPO = "https://github.com/amirfish1/claude-command-center"
BUTTONDOWN_USER = "USERNAME-TODO-PENDING-APPROVAL"  # set on Buttondown signup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "updates")
OUT_DIR = os.path.join(ROOT, "docs", "updates")
BRAND_TOKENS = os.path.join(ROOT, "docs", "brand", "tokens.css")  # optional sibling
OG_DEFAULT = "/updates/assets/og-default.png"

# Problem families: taxonomy by pain, mirrors docs/product-story canonical set.
FAMILIES = [
    ("see-everything", "See everything"),
    ("needs-you", "Know what needs you"),
    ("organize", "Organize"),
    ("steer", "Steer"),
    ("unattended", "Run unattended"),
    ("anywhere", "Work from anywhere"),
]
FAMILY_LABELS = dict(FAMILIES)

# Body sections rendered in this canonical order regardless of file order.
SECTION_ORDER = [
    ("pain", "The pain", "u-pain"),
    ("why workarounds fail", "Why workarounds fail", "u-why"),
    ("what ccc does", "What CCC does", "u-solution"),
    ("solution", "What CCC does", "u-solution"),  # alias
    ("proof", "Proof", "u-proof"),
    ("how to try", "How to try", "u-howto"),
    ("limitations", "Limitations", "u-limits"),
    ("related", "Related", "u-related"),
]
SECTION_META = {}
_seen = set()
for _key, _label, _cls in SECTION_ORDER:
    if _label not in _seen:
        _seen.add(_label)
    SECTION_META[_key] = (_label, _cls)
RENDER_ORDER = ["pain", "why workarounds fail", "what ccc does", "proof",
                "how to try", "limitations", "related"]


# ── Escaping ─────────────────────────────────────────────────────────────────
def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def attr(s: str) -> str:
    return esc(s).replace('"', "&quot;")


def absolutize(url: str) -> str:
    """Root-relative -> absolute on SITE; leave full URLs and anchors alone."""
    if url.startswith("/"):
        return SITE + url
    return url


# ── Constrained YAML front-matter parser (no PyYAML) ─────────────────────────
def _is_skip(line: str) -> bool:
    s = line.strip()
    return s == "" or s.startswith("#")


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(s: str):
    s = s.strip()
    # inline list: [a, b, "c"]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",")]
    # quoted string
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1]
    # strip a trailing inline comment on unquoted scalars
    hidx = s.find(" #")
    if hidx != -1:
        s = s[:hidx].strip()
    return s


def _parse_mapping(lines, start, indent):
    result = {}
    i, n = start, len(lines)
    while i < n:
        if _is_skip(lines[i]):
            i += 1
            continue
        ind = _leading(lines[i])
        if ind != indent:
            break
        key, sep, rest = lines[i].strip().partition(":")
        if not sep:
            break
        key, rest = key.strip(), rest.strip()
        if rest:
            result[key] = _scalar(rest)
            i += 1
            continue
        # nested block: peek the next meaningful line
        j = i + 1
        while j < n and _is_skip(lines[j]):
            j += 1
        if j >= n:
            result[key] = None
            i += 1
            continue
        nxt_ind = _leading(lines[j])
        is_dash = lines[j].strip().startswith("- ")
        if nxt_ind > indent:
            if is_dash:
                result[key], i = _parse_list(lines, j, nxt_ind)
            else:
                result[key], i = _parse_mapping(lines, j, nxt_ind)
        elif nxt_ind == indent and is_dash:
            result[key], i = _parse_list(lines, j, indent)
        else:
            result[key] = None
            i += 1
    return result, i


def _parse_list(lines, start, indent):
    items = []
    i, n = start, len(lines)
    while i < n:
        if _is_skip(lines[i]):
            i += 1
            continue
        ind = _leading(lines[i])
        if ind != indent or not lines[i].strip().startswith("- "):
            break
        content = lines[i].strip()[2:]
        item_indent = indent + 2
        block = [" " * item_indent + content]
        i += 1
        while i < n:
            if _is_skip(lines[i]):
                block.append(lines[i])
                i += 1
                continue
            jind = _leading(lines[i])
            if jind >= item_indent and not (
                jind == indent and lines[i].strip().startswith("- ")
            ):
                block.append(lines[i])
                i += 1
            else:
                break
        head = block[0].split("#", 1)[0]
        if ":" in head:
            m, _ = _parse_mapping(block, 0, item_indent)
            items.append(m)
        else:
            items.append(_scalar(content.strip()))
    return items, i


def parse_front_matter(fm_text: str) -> dict:
    lines = fm_text.split("\n")
    data, _ = _parse_mapping(lines, 0, 0)
    return data


def split_front_matter(text: str):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("file does not start with a '---' front-matter fence")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1:])
    raise ValueError("front matter is not closed with a second '---'")


# ── Body: split into sections ────────────────────────────────────────────────
def split_sections(body: str) -> dict:
    """Return {normalized_heading: markdown_body} for each `## Heading`."""
    sections = {}
    cur = None
    buf = []
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip().lower()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    return sections


# ── Minimal markdown renderer (constrained subset) ───────────────────────────
def render_inline(text: str, absolute: bool = False) -> str:
    out = esc(text)

    def _img(m):
        alt, src = m.group(1), m.group(2)
        s = absolutize(src) if absolute else src
        return '<img src="%s" alt="%s" loading="lazy">' % (attr(s), attr(alt))

    def _link(m):
        label, url = m.group(1), m.group(2)
        u = absolutize(url) if absolute else url
        ext = u.startswith("http") and SITE not in u
        rel = ' target="_blank" rel="noopener"' if ext else ""
        return '<a href="%s"%s>%s</a>' % (attr(u), rel, label)

    out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _img, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, out)
    out = re.sub(r"`([^`]+)`", lambda m: "<code>%s</code>" % m.group(1), out)
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: "<strong>%s</strong>" % m.group(1), out)
    return out


def render_markdown(md: str, absolute: bool = False) -> str:
    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            out.append("<pre><code>%s</code></pre>" % esc("\n".join(code)))
            continue
        if stripped == "":
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>%s</ul>" % "".join(
                "<li>%s</li>" % render_inline(x, absolute) for x in items))
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>%s</ol>" % "".join(
                "<li>%s</li>" % render_inline(x, absolute) for x in items))
            continue
        m = re.match(r"^(#{3,6})\s+(.+)$", line)
        if m:
            level = min(len(m.group(1)), 6)
            out.append("<h%d>%s</h%d>" % (
                level, render_inline(m.group(2).strip(), absolute), level))
            i += 1
            continue
        para = [stripped]
        i += 1
        while i < n:
            s = lines[i].strip()
            if (s == "" or s.startswith("```")
                    or re.match(r"^\s*[-*]\s+", lines[i])
                    or re.match(r"^\s*\d+\.\s+", lines[i])
                    or re.match(r"^#{3,6}\s+", lines[i])):
                break
            para.append(s)
            i += 1
        out.append("<p>%s</p>" % render_inline(" ".join(para), absolute))
    return "\n".join(out)


# ── Media (proof assets from front matter) ───────────────────────────────────
def render_media(media, absolute: bool = False) -> str:
    if not media:
        return ""
    figs = []
    for item in media:
        if not isinstance(item, dict):
            continue
        mtype = (item.get("type") or "image").lower()
        src = item.get("src") or ""
        if not src:
            continue
        s = absolutize(src) if absolute else src
        alt = item.get("alt") or ""
        caption = item.get("caption") or ""
        if mtype == "video":
            poster = item.get("poster") or ""
            p = (absolutize(poster) if absolute else poster) if poster else ""
            patt = ' poster="%s"' % attr(p) if p else ""
            body = ('<video controls preload="metadata"%s>'
                    '<source src="%s"></video>' % (patt, attr(s)))
        else:  # image or gif
            body = '<img src="%s" alt="%s" loading="lazy">' % (attr(s), attr(alt))
        cap = "<figcaption>%s</figcaption>" % esc(caption) if caption else ""
        figs.append('<figure class="u-figure">%s%s</figure>' % (body, cap))
    if not figs:
        return ""
    return '<div class="u-media">%s</div>' % "".join(figs)


# ── Date helpers ─────────────────────────────────────────────────────────────
def parse_date(s: str) -> datetime:
    return datetime.strptime(str(s).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)


def rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT00:00:00Z")


def human_date(dt: datetime) -> str:
    return dt.strftime("%B %-d, %Y") if os.name != "nt" else dt.strftime("%B %d, %Y")


# ── Shared HTML fragments ────────────────────────────────────────────────────
def head_links() -> str:
    links = ['<link rel="stylesheet" href="styles.css">']
    if os.path.exists(BRAND_TOKENS):
        # Optional sibling brand tokens; loaded AFTER styles.css so its :root
        # custom-property values override the inline fallbacks.
        links.append('<link rel="stylesheet" href="../brand/tokens.css">')
    return "\n".join(links)


def subscribe_block() -> str:
    return f"""<section class="u-subscribe" aria-labelledby="sub-h">
  <h2 id="sub-h">Get the next update</h2>
  <form class="u-sub-form"
        action="https://buttondown.com/api/emails/embed-subscribe/{BUTTONDOWN_USER}"
        method="post" target="popupwindow" onsubmit="return false;">
    <input type="email" name="email" placeholder="you@example.com"
           aria-label="Email address" autocomplete="email" disabled>
    <button type="submit" disabled>Subscribe</button>
  </form>
  <p class="u-sub-note">Email updates launching soon.</p>
  <ul class="u-sub-alts">
    <li><a href="feed.xml">Subscribe by RSS / Atom</a>. Works today, in any reader.</li>
    <li><a href="{REPO}/releases" target="_blank" rel="noopener">Watch releases on GitHub</a>.
        Get a notification on every release in the meantime.</li>
  </ul>
  <p class="u-sub-privacy">No tracking pixels. Your email is used only for product
     updates, and you can unsubscribe at any time.</p>
</section>"""


def site_footer() -> str:
    return f"""<footer class="u-foot">
  <a href="{SITE}">ccc.amirfish.ai</a> ·
  <a href="{REPO}" target="_blank" rel="noopener">Source on GitHub</a> ·
  <a href="feed.xml">Atom feed</a>
</footer>"""


# ── Per-update page ──────────────────────────────────────────────────────────
def render_page(u: dict) -> str:
    slug = u["slug"]
    canonical = f"{SITE}/updates/{slug}.html"
    og_image = absolutize(u.get("og_image") or OG_DEFAULT)
    dt = u["_date"]
    family = u["problem_family"]
    family_label = FAMILY_LABELS.get(family, family)

    body_parts = []
    for key in RENDER_ORDER:
        if key not in u["_sections"]:
            continue
        label, cls = SECTION_META[key]
        rendered = render_markdown(u["_sections"][key])
        block = f'<section class="u-sec {cls}"><h2>{esc(label)}</h2>{rendered}'
        if key == "proof":
            block += render_media(u.get("media"))
        block += "</section>"
        body_parts.append(block)
    # media with no Proof section still shows
    if "proof" not in u["_sections"] and u.get("media"):
        body_parts.append('<section class="u-sec u-proof"><h2>Proof</h2>%s</section>'
                          % render_media(u.get("media")))

    cta = u.get("cta") or {}
    cta_html = ""
    if isinstance(cta, dict) and cta.get("href"):
        cta_html = ('<p class="u-cta-wrap"><a class="u-cta" href="%s">%s</a></p>'
                    % (attr(cta["href"]), esc(cta.get("label") or "Try it")))

    version = u.get("version") or ""
    anchor = "#" + str(version).replace(".", "-") if version else ""
    changelog_html = (
        f'<details class="u-changelog"><summary>Full changelog for v{esc(version)}</summary>'
        f'<p>The complete list of changes in v{esc(version)} lives in the '
        f'<a href="{REPO}/blob/main/CHANGELOG.md{anchor}" target="_blank" rel="noopener">'
        f'CHANGELOG</a>. This page is the story; the changelog is the record.</p>'
        f"</details>" if version else "")

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(u['title'])} · CCC Updates</title>
<meta name="description" content="{attr(u['summary'])}">
<link rel="canonical" href="{canonical}">

<meta property="og:type" content="article">
<meta property="og:title" content="{attr(u['title'])}">
<meta property="og:description" content="{attr(u['summary'])}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{attr(og_image)}">
<meta property="article:published_time" content="{rfc3339(dt)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{attr(u['title'])}">
<meta name="twitter:description" content="{attr(u['summary'])}">
<meta name="twitter:image" content="{attr(og_image)}">

<link rel="alternate" type="application/atom+xml" title="CCC Updates" href="feed.xml">
{head_links()}
</head>
<body>
<header class="u-top">
  <a class="u-back" href="index.html">← All updates</a>
  <a class="u-brand" href="{SITE}">Claude Command Center</a>
</header>
<main class="u-wrap">
  <article class="u-article">
    <div class="u-meta">
      <a class="u-pill u-fam" href="index.html?family={attr(family)}"
         data-family="{attr(family)}">{esc(family_label)}</a>
      <span class="u-pill u-ver">v{esc(version)}</span>
      <time datetime="{rfc3339(dt)}">{esc(human_date(dt))}</time>
    </div>
    <h1>{esc(u['title'])}</h1>
    <p class="u-lede">{esc(u['summary'])}</p>
    {''.join(body_parts)}
    {cta_html}
    {changelog_html}
  </article>
  {subscribe_block()}
</main>
{site_footer()}
</body>
</html>"""


# ── Index page ───────────────────────────────────────────────────────────────
def render_card(u: dict) -> str:
    slug = u["slug"]
    family = u["problem_family"]
    family_label = FAMILY_LABELS.get(family, family)
    dt = u["_date"]
    haystack = " ".join([
        u["title"], u["summary"], family_label,
        " ".join(u.get("tags") or []), u.get("version") or "",
    ]).lower()
    return f"""<article class="u-card" data-family="{attr(family)}"
         data-text="{attr(haystack)}">
  <a class="u-card-link" href="{attr(slug)}.html">
    <div class="u-meta">
      <span class="u-pill u-fam">{esc(family_label)}</span>
      <span class="u-pill u-ver">v{esc(u.get('version') or '')}</span>
      <time datetime="{rfc3339(dt)}">{esc(human_date(dt))}</time>
    </div>
    <h2>{esc(u['title'])}</h2>
    <p>{esc(u['summary'])}</p>
    <span class="u-more">Read update →</span>
  </a>
</article>"""


def render_index(updates: list) -> str:
    families_present = [f for f, _ in FAMILIES
                        if any(u["problem_family"] == f for u in updates)]
    chips = ['<button class="u-chip is-active" data-family="all">All</button>']
    for f in families_present:
        chips.append('<button class="u-chip" data-family="%s">%s</button>'
                     % (attr(f), esc(FAMILY_LABELS[f])))
    cards = "\n".join(render_card(u) for u in updates)
    canonical = f"{SITE}/updates/"
    og_image = absolutize(OG_DEFAULT)
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Updates · Claude Command Center</title>
<meta name="description" content="Product updates for CCC: each release told as the pain it removed, with proof and how to try it.">
<link rel="canonical" href="{canonical}">

<meta property="og:type" content="website">
<meta property="og:title" content="Claude Command Center · Updates">
<meta property="og:description" content="Each CCC release, told as the pain it removed, with proof and how to try it.">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{attr(og_image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{attr(og_image)}">

<link rel="alternate" type="application/atom+xml" title="CCC Updates" href="feed.xml">
{head_links()}
</head>
<body>
<header class="u-top">
  <a class="u-brand" href="{SITE}">Claude Command Center</a>
  <a class="u-back" href="{SITE}">Home →</a>
</header>
<main class="u-wrap">
  <div class="u-hero">
    <h1>Updates</h1>
    <p>Every release, told as the pain it removed. Proof included. Subscribe below.</p>
  </div>
  <div class="u-controls">
    <input id="u-search" type="search" placeholder="Search updates"
           aria-label="Search updates" autocomplete="off">
    <div class="u-chips" role="group" aria-label="Filter by problem family">
      {''.join(chips)}
    </div>
  </div>
  <div id="u-list" class="u-grid">
    {cards}
  </div>
  <p id="u-empty" class="u-empty" hidden>No updates match that filter yet.</p>
  {subscribe_block()}
</main>
{site_footer()}
<script>
(function () {{
  var search = document.getElementById('u-search');
  var chips = Array.prototype.slice.call(document.querySelectorAll('.u-chip'));
  var cards = Array.prototype.slice.call(document.querySelectorAll('.u-card'));
  var empty = document.getElementById('u-empty');
  var family = 'all';

  function fromQuery() {{
    var m = /[?&]family=([^&]+)/.exec(location.search);
    if (m) {{
      var f = decodeURIComponent(m[1]);
      chips.forEach(function (c) {{
        var on = c.getAttribute('data-family') === f;
        c.classList.toggle('is-active', on);
        if (on) family = f;
      }});
      if (!chips.some(function (c) {{ return c.classList.contains('is-active'); }})) {{
        family = 'all';
        chips[0].classList.add('is-active');
      }}
    }}
  }}

  function apply() {{
    var q = (search.value || '').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card) {{
      var okFam = family === 'all' || card.getAttribute('data-family') === family;
      var okText = !q || (card.getAttribute('data-text') || '').indexOf(q) !== -1;
      var show = okFam && okText;
      card.style.display = show ? '' : 'none';
      if (show) shown++;
    }});
    empty.hidden = shown !== 0;
  }}

  chips.forEach(function (chip) {{
    chip.addEventListener('click', function () {{
      chips.forEach(function (c) {{ c.classList.remove('is-active'); }});
      chip.classList.add('is-active');
      family = chip.getAttribute('data-family');
      apply();
    }});
  }});
  search.addEventListener('input', apply);
  fromQuery();
  apply();
}})();
</script>
</body>
</html>"""


# ── Atom feed ────────────────────────────────────────────────────────────────
ATOM = "http://www.w3.org/2005/Atom"


def _el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, "{%s}%s" % (ATOM, tag), attrs)
    if text is not None:
        e.text = text
    return e


def render_feed(updates: list) -> str:
    ET.register_namespace("", ATOM)
    feed = ET.Element("{%s}feed" % ATOM)
    _el(feed, "title", "Claude Command Center · Updates")
    _el(feed, "subtitle", "Each release, told as the pain it removed.")
    _el(feed, "id", f"{SITE}/updates/")
    _el(feed, "link", href=f"{SITE}/updates/feed.xml", rel="self")
    _el(feed, "link", href=f"{SITE}/updates/", rel="alternate")
    latest = max((u["_date"] for u in updates), default=datetime.now(timezone.utc))
    _el(feed, "updated", rfc3339(latest))
    author = _el(feed, "author")
    _el(author, "name", "Claude Command Center")

    for u in updates:
        slug = u["slug"]
        url = f"{SITE}/updates/{slug}.html"
        entry = _el(feed, "entry")
        _el(entry, "id", url)
        _el(entry, "title", u["title"])
        _el(entry, "link", href=url, rel="alternate")
        _el(entry, "published", rfc3339(u["_date"]))
        _el(entry, "updated", rfc3339(u["_date"]))
        _el(entry, "summary", u["summary"])
        _el(entry, "category", term=u["problem_family"],
            label=FAMILY_LABELS.get(u["problem_family"], u["problem_family"]))
        ea = _el(entry, "author")
        _el(ea, "name", "Claude Command Center")
        # Content: rendered body sections + media, all URLs absolutized.
        chunks = []
        for key in RENDER_ORDER:
            if key in u["_sections"]:
                label, _cls = SECTION_META[key]
                chunks.append("<h2>%s</h2>" % esc(label))
                chunks.append(render_markdown(u["_sections"][key], absolute=True))
                if key == "proof":
                    chunks.append(render_media(u.get("media"), absolute=True))
        content = _el(entry, "content", "\n".join(chunks), type="html")
        _ = content

    raw = ET.tostring(feed, encoding="unicode")
    return xml.dom.minidom.parseString(raw).toprettyxml(indent="  ")


# ── Stylesheet ───────────────────────────────────────────────────────────────
STYLES = """/* CCC Updates hub — generated by scripts/updates_build.py. Do not edit here. */
/* Brand tokens: the optional sibling docs/brand/tokens.css (linked after this
   file when present) defines the canonical --ccc-bg / --ccc-surface / --ccc-text
   / --ccc-primary / --ccc-accent set. Every color below reads a brand token by
   its canonical name with a dark literal fallback, so the hub is coherent whether
   or not tokens.css exists. The pages carry data-theme="dark" so they stay dark
   (matching the dark-only product site) even for a light-OS visitor, since
   tokens.css only flips to light for :root:not([data-theme="dark"]). */
:root {
  /* Shared names: literals here; tokens.css (linked after) wins via the cascade
     when present. Never self-reference (--ccc-bg: var(--ccc-bg,...)) — that is a
     cyclic custom property and computes to invalid. */
  --ccc-bg: #0d1117;
  --ccc-primary: #6e8caf;
  --ccc-accent: #f5a623;
  /* Hub-local names mapped onto brand tokens by a DIFFERENT name (no cycle),
     with a dark literal fallback for when tokens.css is absent. */
  --ccc-bg-2: var(--ccc-surface, #161b22);
  --ccc-bg-3: var(--ccc-surface-2, #1c232b);
  --ccc-fg: var(--ccc-text, #e6edf3);
  --ccc-fg-dim: var(--ccc-text-muted, #9aa3ad);
  --ccc-fg-mute: var(--ccc-text-muted, #6b727c);
  --ccc-line: var(--ccc-border, #232830);
  --ccc-line-strong: var(--ccc-border, #2e343d);
  --ccc-accent-soft: rgba(245, 166, 35, 0.12);
  --ccc-sans: var(--ccc-font-ui, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif);
  --ccc-mono: var(--ccc-font-mono, ui-monospace, "SF Mono", Menlo, Consolas, monospace);
  --ccc-maxw: 760px;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--ccc-bg, #0d1117);
  color: var(--ccc-fg, #e6edf3);
  font-family: var(--ccc-sans);
  line-height: 1.6;
  font-size: 17px;
}
a { color: var(--ccc-primary, #6e8caf); text-decoration: none; }
a:hover { text-decoration: underline; }
img, video { max-width: 100%; height: auto; display: block; }
h1, h2, h3 { line-height: 1.25; font-weight: 650; }

.u-top {
  display: flex; justify-content: space-between; align-items: center;
  gap: 1rem; padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--ccc-line, #232830);
  font-size: 0.9rem;
}
.u-brand { color: var(--ccc-fg, #e6edf3); font-weight: 600; }
.u-back { color: var(--ccc-fg-dim, #9aa3ad); }

.u-wrap { max-width: var(--ccc-maxw); margin: 0 auto; padding: 2rem 1.25rem 4rem; }

.u-hero { margin: 1rem 0 2rem; }
.u-hero h1 { font-size: 2.2rem; margin: 0 0 0.5rem; }
.u-hero p { color: var(--ccc-fg-dim, #9aa3ad); margin: 0; max-width: 46ch; }

.u-controls { margin: 0 0 1.75rem; }
#u-search {
  width: 100%; padding: 0.6rem 0.85rem; font-size: 1rem;
  background: var(--ccc-bg-2, #161b22); color: var(--ccc-fg, #e6edf3);
  border: 1px solid var(--ccc-line-strong, #2e343d); border-radius: 8px;
  font-family: inherit;
}
#u-search:focus { outline: 2px solid var(--ccc-primary, #6e8caf); outline-offset: 1px; }
.u-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.85rem; }
.u-chip {
  padding: 0.35rem 0.75rem; font-size: 0.85rem; cursor: pointer;
  background: var(--ccc-bg-2, #161b22); color: var(--ccc-fg-dim, #9aa3ad);
  border: 1px solid var(--ccc-line-strong, #2e343d); border-radius: 999px;
  font-family: inherit;
}
.u-chip:hover { color: var(--ccc-fg, #e6edf3); }
.u-chip.is-active {
  background: var(--ccc-accent-soft, rgba(245,166,35,0.12));
  color: var(--ccc-accent, #f5a623);
  border-color: var(--ccc-accent, #f5a623);
}

.u-grid { display: grid; gap: 1rem; }
.u-card {
  border: 1px solid var(--ccc-line, #232830); border-radius: 12px;
  background: var(--ccc-bg-2, #161b22); transition: border-color .15s ease;
}
.u-card:hover { border-color: var(--ccc-line-strong, #2e343d); }
.u-card-link { display: block; padding: 1.25rem 1.35rem; color: inherit; }
.u-card-link:hover { text-decoration: none; }
.u-card h2 { font-size: 1.3rem; margin: 0.5rem 0 0.4rem; color: var(--ccc-fg, #e6edf3); }
.u-card p { margin: 0 0 0.75rem; color: var(--ccc-fg-dim, #9aa3ad); }
.u-more { color: var(--ccc-primary, #6e8caf); font-size: 0.9rem; font-weight: 550; }
.u-empty { color: var(--ccc-fg-mute, #6b727c); text-align: center; padding: 2rem 0; }

.u-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
  font-size: 0.8rem; color: var(--ccc-fg-mute, #6b727c); }
.u-pill {
  display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px;
  font-size: 0.75rem; font-weight: 550; border: 1px solid var(--ccc-line-strong, #2e343d);
}
.u-fam { color: var(--ccc-accent, #f5a623); border-color: var(--ccc-accent, #f5a623);
  background: var(--ccc-accent-soft, rgba(245,166,35,0.12)); }
a.u-fam:hover { text-decoration: none; }
.u-ver { color: var(--ccc-primary, #6e8caf); }

.u-article { margin-bottom: 3rem; }
.u-article h1 { font-size: 2.1rem; margin: 0.75rem 0 0.5rem; }
.u-lede { font-size: 1.15rem; color: var(--ccc-fg-dim, #9aa3ad); margin: 0 0 2rem; }
.u-sec { margin: 2rem 0; }
.u-sec h2 {
  font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--ccc-fg-mute, #6b727c); margin: 0 0 0.6rem;
}
.u-sec p { margin: 0 0 0.9rem; }
.u-pain { border-left: 3px solid var(--ccc-accent, #f5a623); padding-left: 1.1rem; }
.u-pain p:first-of-type { font-size: 1.15rem; color: var(--ccc-fg, #e6edf3); }
.u-limits {
  background: var(--ccc-bg-2, #161b22); border: 1px solid var(--ccc-line, #232830);
  border-radius: 10px; padding: 1rem 1.2rem;
}
.u-limits h2 { color: var(--ccc-fg-dim, #9aa3ad); }
.u-media { margin: 1rem 0; display: grid; gap: 1rem; }
.u-figure { margin: 0; border: 1px solid var(--ccc-line, #232830); border-radius: 10px;
  overflow: hidden; background: var(--ccc-bg-2, #161b22); }
.u-figure figcaption { padding: 0.6rem 0.85rem; font-size: 0.85rem;
  color: var(--ccc-fg-mute, #6b727c); }
pre {
  background: var(--ccc-bg-3, #1c232b); border: 1px solid var(--ccc-line, #232830);
  border-radius: 8px; padding: 0.9rem 1rem; overflow-x: auto;
}
code { font-family: var(--ccc-mono); font-size: 0.9em; }
p code, li code { background: var(--ccc-bg-3, #1c232b); padding: 0.1em 0.35em;
  border-radius: 4px; }

.u-cta-wrap { margin: 2.5rem 0 1rem; }
.u-cta {
  display: inline-block; padding: 0.7rem 1.4rem; border-radius: 8px;
  background: var(--ccc-accent, #f5a623); color: #1a1205; font-weight: 650;
}
.u-cta:hover { text-decoration: none; filter: brightness(1.06); }
.u-changelog { margin: 1.5rem 0; color: var(--ccc-fg-dim, #9aa3ad); }
.u-changelog summary { cursor: pointer; color: var(--ccc-primary, #6e8caf); }

.u-subscribe {
  margin: 3rem 0 1rem; padding: 1.75rem; border-radius: 14px;
  border: 1px solid var(--ccc-line-strong, #2e343d);
  background: var(--ccc-bg-2, #161b22);
}
.u-subscribe h2 { font-size: 1.3rem; margin: 0 0 1rem; }
.u-sub-form { display: flex; gap: 0.6rem; flex-wrap: wrap; }
.u-sub-form input {
  flex: 1 1 220px; padding: 0.6rem 0.8rem; font-size: 1rem; font-family: inherit;
  background: var(--ccc-bg-3, #1c232b); color: var(--ccc-fg, #e6edf3);
  border: 1px solid var(--ccc-line-strong, #2e343d); border-radius: 8px;
}
.u-sub-form button {
  padding: 0.6rem 1.2rem; font-size: 1rem; font-family: inherit; font-weight: 600;
  border-radius: 8px; border: 1px solid var(--ccc-line-strong, #2e343d);
  background: var(--ccc-bg-3, #1c232b); color: var(--ccc-fg-dim, #9aa3ad);
}
.u-sub-form input:disabled, .u-sub-form button:disabled { opacity: 0.55; cursor: not-allowed; }
.u-sub-note { color: var(--ccc-accent, #f5a623); font-size: 0.9rem; margin: 0.6rem 0 1rem; }
.u-sub-alts { margin: 0 0 1rem; padding-left: 1.1rem; color: var(--ccc-fg-dim, #9aa3ad);
  font-size: 0.95rem; }
.u-sub-alts li { margin: 0.3rem 0; }
.u-sub-privacy { color: var(--ccc-fg-mute, #6b727c); font-size: 0.82rem; margin: 0; }

.u-foot { max-width: var(--ccc-maxw); margin: 0 auto; padding: 2rem 1.25rem 3rem;
  color: var(--ccc-fg-mute, #6b727c); font-size: 0.85rem; }

@media (min-width: 640px) {
  .u-grid { grid-template-columns: 1fr; }
  .u-hero h1 { font-size: 2.6rem; }
}
"""


# ── HTML validation ──────────────────────────────────────────────────────────
class _Validator(html.parser.HTMLParser):
    """Feeding through html.parser raises on malformed markup."""


def validate_html(text: str) -> None:
    p = _Validator()
    p.feed(text)
    p.close()


# ── Load + build ─────────────────────────────────────────────────────────────
REQUIRED = ["slug", "title", "date", "version", "problem_family", "summary", "status"]


def load_updates() -> list:
    updates = []
    for name in sorted(os.listdir(SRC_DIR)):
        if not name.endswith(".md"):
            continue
        if name.startswith("_") or name.lower() == "readme.md":
            continue
        path = os.path.join(SRC_DIR, name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        fm_text, body = split_front_matter(text)
        fm = parse_front_matter(fm_text)
        missing = [k for k in REQUIRED if not fm.get(k)]
        if missing:
            raise SystemExit(f"[updates] {name}: missing required field(s): {missing}")
        if fm["status"] != "published":
            print(f"  skip (status={fm['status']}): {name}")
            continue
        if fm["problem_family"] not in FAMILY_LABELS:
            raise SystemExit(
                f"[updates] {name}: problem_family '{fm['problem_family']}' is not one of "
                f"{list(FAMILY_LABELS)}")
        fm["_date"] = parse_date(fm["date"])
        fm["_sections"] = split_sections(body)
        updates.append(fm)
    updates.sort(key=lambda u: (u["_date"], u["slug"]), reverse=True)
    return updates


def json_index(updates: list) -> str:
    rows = [{
        "slug": u["slug"],
        "title": u["title"],
        "summary": u["summary"],
        "date": u["date"],
        "version": u.get("version"),
        "problem_family": u["problem_family"],
        "family_label": FAMILY_LABELS.get(u["problem_family"], u["problem_family"]),
        "url": f"{SITE}/updates/{u['slug']}.html",
        "og_image": absolutize(u.get("og_image") or OG_DEFAULT),
        "tags": u.get("tags") or [],
    } for u in updates]
    return json.dumps({"updates": rows}, indent=2, ensure_ascii=False) + "\n"


def prune_stale(slugs: set) -> None:
    keep = {"index.html"} | {f"{s}.html" for s in slugs}
    for name in os.listdir(OUT_DIR):
        if name.endswith(".html") and name not in keep:
            os.remove(os.path.join(OUT_DIR, name))
            print(f"  pruned stale page: {name}")


def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def main() -> int:
    strict = "--check" in sys.argv
    os.makedirs(os.path.join(OUT_DIR, "assets"), exist_ok=True)
    print(f"[updates] source: {SRC_DIR}")
    updates = load_updates()
    print(f"[updates] {len(updates)} published update(s)")

    pages = {}
    for u in updates:
        pages[u["slug"]] = render_page(u)
    index_html = render_index(updates)
    feed_xml = render_feed(updates)
    updates_json = json_index(updates)

    # Validate before writing so a broken build never lands on disk.
    errors = []
    for slug, page in pages.items():
        try:
            validate_html(page)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"page {slug}.html: {exc}")
    try:
        validate_html(index_html)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"index.html: {exc}")
    try:
        xml.dom.minidom.parseString(feed_xml.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"feed.xml: {exc}")
    try:
        json.loads(updates_json)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"updates.json: {exc}")

    if errors:
        for e in errors:
            print(f"  FAIL {e}")
        if strict or True:
            raise SystemExit("[updates] validation failed; nothing written")

    for slug, page in pages.items():
        write(os.path.join(OUT_DIR, f"{slug}.html"), page)
    write(os.path.join(OUT_DIR, "index.html"), index_html)
    write(os.path.join(OUT_DIR, "feed.xml"), feed_xml)
    write(os.path.join(OUT_DIR, "updates.json"), updates_json)
    write(os.path.join(OUT_DIR, "styles.css"), STYLES)
    prune_stale(set(pages))

    tokens = "linked" if os.path.exists(BRAND_TOKENS) else "inline fallback"
    print(f"[updates] brand tokens: {tokens}")
    print(f"[updates] wrote {len(pages)} page(s) + index, feed, json, styles")
    print("[updates] validation: PASS (all pages html.parser-clean, feed XML-valid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
