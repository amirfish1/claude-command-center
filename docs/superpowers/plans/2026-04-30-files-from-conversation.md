# Files-from-conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-conversation index of file-like artifacts (images, PDFs, docs, presentations, videos, MD, HTML) mentioned in a session — surfaced as a `📎 Files (N)` pill in the conversation sticky header that opens a grouped modal; clicking a row opens URLs in a new tab and reveals local files in the macOS default app via a new extension-clamped opener.

**Architecture:** Two new server endpoints in `server.py` — `GET /api/conversations/<id>/files` (full-fidelity JSONL scan, no truncation) and `POST /api/reveal-file` (same-origin + extension-whitelist + `subprocess.Popen(["open", path])`). One single source-of-truth `FILE_CATEGORIES` dict drives both extraction and the opener clamp. Frontend changes confined to `static/index.html` — CSS, a pill appended to the existing `.conv-sticky-header`, and a new `ffc-` prefixed modal modeled on the existing `nsm-` (new-session-modal) pattern at line 3395.

**Tech Stack:** Python 3 stdlib only (`server.py` is dependency-free per `CLAUDE.md`); single-file vanilla JS in `static/index.html`; `subprocess.Popen(["open", path])` for the macOS opener; stdlib `unittest` for tests.

**Spec:** [`docs/superpowers/specs/2026-04-30-files-from-conversation-design.md`](../specs/2026-04-30-files-from-conversation-design.md)

---

## File structure

| File | Action | Responsibility for this feature |
|---|---|---|
| `server.py` | Modify | Adds `FILE_CATEGORIES` / `FILE_EXT_TO_CATEGORY` constants, `_categorize_file_target` helper, `_extract_files_from_conversation` extractor, two new endpoints (`GET /api/conversations/<id>/files`, `POST /api/reveal-file`). |
| `static/index.html` | Modify | CSS for `.ffc-pill` + `.ffc-overlay` modal; pill DOM injected into `.conv-sticky-header`; modal scaffolding with click/Esc/backdrop handlers; fetch lifecycle keyed off `currentConversation`. |
| `tests/fixtures/files-extraction.jsonl` | Create | Small JSONL fixture exercising image-paste, `Read{file_path}` PDF, `Bash` command with embedded path, URL-in-text, and tool_result with a path. |
| `tests/test_files_extraction.py` | Create | stdlib `unittest`; tests for `_categorize_file_target` and `_extract_files_from_conversation` against the fixture. |
| `pyproject.toml` | Modify | Version bump `0.2.1` → `0.3.0` (minor — new endpoints, no breaking changes per `CLAUDE.md` SemVer policy). |
| `server.py` `__version__` | Modify | Same bump, kept in lockstep. |
| `changelog.d/added-files-from-conversation-2026-04-30.md` | Create | Keep-a-Changelog snippet under the existing convention. |

The new spec file at `docs/superpowers/specs/2026-04-30-files-from-conversation-design.md` is already committed.

---

## Task 1 — Backend: extension whitelist constants + categorizer

**Files:**
- Modify: `server.py` — insert at module-top constants, near the existing `FILE_HISTORY_…` / `LOG_DIR` block (find the constants cluster around lines 40–80).
- Create: `tests/test_files_extraction.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_files_extraction.py`:

```python
"""Tests for the per-conversation file index (server-side extraction)."""

import importlib
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class TestCategorize(unittest.TestCase):
    def setUp(self):
        # Re-import server fresh; some sibling tests mutate sys.modules.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_image_extensions_categorized_as_images(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                    ".heic", ".bmp", ".tiff"):
            with self.subTest(ext=ext):
                self.assertEqual(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    "images",
                )

    def test_pdf_categorized(self):
        self.assertEqual(self.server._categorize_file_target("/x/a.pdf"), "pdfs")

    def test_uppercase_extensions_normalized(self):
        # Real conversations contain `.PNG`, `.PDF`, etc. Categorizer must
        # be case-insensitive on the extension.
        self.assertEqual(self.server._categorize_file_target("/x/a.PDF"), "pdfs")
        self.assertEqual(self.server._categorize_file_target("/x/Y.JPEG"), "images")

    def test_excluded_extensions_return_none(self):
        # Code/scripts MUST NOT categorize — they're the load-bearing
        # security clamp on /api/reveal-file. If an attacker convinces the
        # extractor a `.sh` is a file, the modal could render it and the
        # opener would shell out. The whitelist is closed by design.
        for ext in (".py", ".sh", ".js", ".ts", ".rb", ".go", ".rs", ".app",
                    ".command", ".workflow", ".applescript",
                    ".json", ".yaml", ".yml", ".toml", ".css", ".sql",
                    ".lock", ".txt"):
            with self.subTest(ext=ext):
                self.assertIsNone(
                    self.server._categorize_file_target("/tmp/x" + ext),
                    f"{ext} must NOT categorize — it would weaken the opener clamp",
                )

    def test_no_extension_returns_none(self):
        self.assertIsNone(self.server._categorize_file_target("/tmp/somefile"))
        self.assertIsNone(self.server._categorize_file_target("https://example.com/"))

    def test_url_with_known_extension_categorizes(self):
        self.assertEqual(
            self.server._categorize_file_target("https://drive.google.com/foo.pdf"),
            "pdfs",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_files_extraction -v`

Expected: FAIL with `AttributeError: module 'server' has no attribute '_categorize_file_target'`.

- [ ] **Step 3: Add the constants and categorizer to `server.py`**

Find the constants cluster near the top of `server.py` (just after `LOG_DIR` is defined, around lines 40–80). Insert:

```python
# Files-from-conversation: extension whitelist driving both the
# /api/conversations/<id>/files extractor and the /api/reveal-file
# opener's allow-list. Closed set by design — adding `.app` / `.sh` /
# `.command` here would re-introduce the macOS-`open`-as-RCE risk that
# /api/open's path sandbox prevents (see SECURITY.md). Keep this list
# tight; the opener has no path-prefix clamp because this clamp does
# the work.
FILE_CATEGORIES = {
    "images":        {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                      ".heic", ".bmp", ".tiff"},
    "videos":        {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v"},
    "pdfs":          {".pdf"},
    "docs":          {".docx", ".doc", ".odt", ".rtf", ".pages",
                      ".xlsx", ".xls", ".csv", ".ods", ".numbers"},
    "presentations": {".pptx", ".ppt", ".key", ".odp"},
    "markdown":      {".md", ".mdx"},
    "html":          {".html", ".htm"},
}
FILE_EXT_TO_CATEGORY = {
    ext: cat for cat, exts in FILE_CATEGORIES.items() for ext in exts
}


def _categorize_file_target(target):
    """Return the category name for `target` (a path or URL), or None
    if its extension is not in the whitelist. Case-insensitive on the
    extension. URLs lose any query string / fragment before the lookup
    so `foo.pdf?token=…` still classifies as `pdfs`."""
    if not target:
        return None
    s = target
    # Strip URL query / fragment so foo.pdf?x=1 → foo.pdf
    for sep in ("?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    # os.path.splitext handles trailing-dot / no-dot cleanly.
    _, ext = os.path.splitext(s)
    return FILE_EXT_TO_CATEGORY.get(ext.lower())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_files_extraction -v`

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_files_extraction.py
git commit --only server.py tests/test_files_extraction.py -m "feat(server): file-categories whitelist + _categorize_file_target

Module-level FILE_CATEGORIES dict + helper, single source of truth
for the upcoming /api/conversations/<id>/files extractor and the
/api/reveal-file opener clamp."
```

---

## Task 2 — Test fixture: small JSONL exercising every extraction source

**Files:**
- Create: `tests/fixtures/files-extraction.jsonl`

- [ ] **Step 1: Write the fixture file**

Create `tests/fixtures/files-extraction.jsonl` with five lines:

```jsonl
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Take a look at /Users/amir/Desktop/diagram.png and the spec at https://drive.google.com/file/d/abc123/view?usp=sharing — the spec is at https://example.com/spec.pdf"}]},"uuid":"u-1","timestamp":"2026-04-30T12:00:00Z","sessionId":"00000000-ffc-4000-8000-000000000001"}
{"type":"assistant","message":{"id":"m-1","role":"assistant","content":[{"type":"text","text":"I'll read the README first."},{"type":"tool_use","id":"t-1","name":"Read","input":{"file_path":"/Users/amir/Apps/foo/notes.pdf"}}]},"uuid":"a-1","timestamp":"2026-04-30T12:00:01Z","sessionId":"00000000-ffc-4000-8000-000000000001"}
{"type":"user","message":{"role":"user","content":[{"tool_use_id":"t-1","type":"tool_result","content":"see also /Users/amir/Apps/foo/intro.md and https://example.com/video.mp4"}]},"uuid":"u-2","timestamp":"2026-04-30T12:00:02Z","sessionId":"00000000-ffc-4000-8000-000000000001"}
{"type":"assistant","message":{"id":"m-2","role":"assistant","content":[{"type":"tool_use","id":"t-2","name":"Bash","input":{"command":"cp /Users/amir/Downloads/deck.pptx ./build/ && open /Users/amir/Apps/foo/intro.md"}}]},"uuid":"a-2","timestamp":"2026-04-30T12:00:03Z","sessionId":"00000000-ffc-4000-8000-000000000001"}
{"type":"assistant","message":{"id":"m-3","role":"assistant","content":[{"type":"text","text":"Here is the report: /Users/amir/Apps/foo/report.html and a script /Users/amir/Apps/foo/run.sh (should NOT be listed)."}]},"uuid":"a-3","timestamp":"2026-04-30T12:00:04Z","sessionId":"00000000-ffc-4000-8000-000000000001"}
```

This fixture exercises:
- A user text message with one absolute path (`.png`) and two URLs (one ends in `.pdf`, one without an extension).
- An assistant `Read` tool_use with `file_path` (`.pdf`).
- A `tool_result` content string with a path (`.md`) and a URL (`.mp4`).
- An assistant `Bash` tool_use with two paths buried in the command (`.pptx`, `.md` — the `.md` should de-dup against the earlier `.md` mention).
- An assistant text with one path that should categorize (`.html`) and one that should NOT (`.sh`, in the excluded list).

Expected extraction (after de-dup):
- images: `/Users/amir/Desktop/diagram.png`
- pdfs:   `/Users/amir/Apps/foo/notes.pdf`, `https://example.com/spec.pdf`
- docs:   (none — pptx is presentations, mp4 is videos)
- presentations: `/Users/amir/Downloads/deck.pptx`
- videos: `https://example.com/video.mp4`
- markdown: `/Users/amir/Apps/foo/intro.md`
- html:   `/Users/amir/Apps/foo/report.html`

The Drive URL with `usp=sharing` and no extension on the path part should NOT be extracted (would need user-supplied label support — out of scope).

- [ ] **Step 2: Commit the fixture**

```bash
git add tests/fixtures/files-extraction.jsonl
git commit --only tests/fixtures/files-extraction.jsonl -m "test(fixtures): JSONL exercising every files-extraction source"
```

---

## Task 3 — Backend: `_extract_files_from_conversation` extractor

**Files:**
- Modify: `server.py` — add the extractor function near other conversation-parsing helpers (around `parse_conversation` at line 4610).
- Modify: `tests/test_files_extraction.py` — add a behavioral test against the fixture.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_files_extraction.py`:

```python
class TestExtractor(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

        # Point _resolve_conversation_path at our fixture by patching
        # _conversation_dirs() to return the fixtures dir, where we
        # symlink/copy the fixture under the conversation-id name the
        # extractor expects. Simplest: monkey-patch the resolver itself.
        self.fixture = REPO / "tests" / "fixtures" / "files-extraction.jsonl"
        self._orig_resolve = self.server._resolve_conversation_path
        self.server._resolve_conversation_path = lambda cid: self.fixture

    def tearDown(self):
        self.server._resolve_conversation_path = self._orig_resolve

    def test_extracts_expected_files_per_category(self):
        result = self.server._extract_files_from_conversation("ignored")
        self.assertIn("groups", result)
        self.assertIn("count", result)
        self.assertFalse(result["truncated"])

        groups = result["groups"]

        def targets(cat):
            return [r["target"] for r in groups.get(cat, [])]

        self.assertEqual(set(targets("images")),
                         {"/Users/amir/Desktop/diagram.png"})
        self.assertEqual(set(targets("pdfs")),
                         {"/Users/amir/Apps/foo/notes.pdf",
                          "https://example.com/spec.pdf"})
        self.assertEqual(set(targets("presentations")),
                         {"/Users/amir/Downloads/deck.pptx"})
        self.assertEqual(set(targets("videos")),
                         {"https://example.com/video.mp4"})
        self.assertEqual(set(targets("markdown")),
                         {"/Users/amir/Apps/foo/intro.md"})
        self.assertEqual(set(targets("html")),
                         {"/Users/amir/Apps/foo/report.html"})

        # Total == sum across non-empty groups.
        self.assertEqual(result["count"],
                         sum(len(v) for v in groups.values()))

    def test_excluded_extensions_never_appear(self):
        result = self.server._extract_files_from_conversation("ignored")
        all_targets = []
        for rows in result["groups"].values():
            all_targets.extend(r["target"] for r in rows)
        for t in all_targets:
            self.assertFalse(
                t.lower().endswith(".sh"),
                f"shell script leaked into extractor: {t}",
            )
            self.assertFalse(
                t.lower().endswith(".py"),
                f"python file leaked into extractor: {t}",
            )

    def test_de_duplicates_repeats(self):
        # `/Users/amir/Apps/foo/intro.md` appears twice in the fixture
        # (tool_result + Bash command). Must collapse to one row.
        result = self.server._extract_files_from_conversation("ignored")
        md_targets = [r["target"] for r in result["groups"].get("markdown", [])]
        self.assertEqual(md_targets.count("/Users/amir/Apps/foo/intro.md"), 1)

    def test_each_row_has_label_target_kind_first_line(self):
        result = self.server._extract_files_from_conversation("ignored")
        for cat, rows in result["groups"].items():
            for r in rows:
                with self.subTest(cat=cat, target=r.get("target")):
                    self.assertIn("label", r)
                    self.assertIn("target", r)
                    self.assertIn("kind", r)
                    self.assertIn("first_line", r)
                    self.assertIn(r["kind"], ("path", "url"))

    def test_missing_jsonl_returns_empty(self):
        self.server._resolve_conversation_path = lambda cid: Path("/no/such/file.jsonl")
        result = self.server._extract_files_from_conversation("ignored")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["groups"], {})
        self.assertFalse(result["truncated"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_files_extraction.TestExtractor -v`

Expected: FAIL with `AttributeError: module 'server' has no attribute '_extract_files_from_conversation'`.

- [ ] **Step 3: Implement the extractor in `server.py`**

Find `parse_conversation` at `server.py:4610`. Insert directly **after** `parse_conversation`:

```python
# Regex for files-from-conversation extraction. Two patterns: HTTP(S)
# URLs, and absolute Unix paths anchored to whitespace/quote/paren so
# we don't pull tokens out of the middle of code identifiers.
_FFC_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")
_FFC_PATH_RE = re.compile(r"(?:^|(?<=[\s\"'`(\[]))(/[^\s\"'`<>)\]]+)")
_FFC_PATH_TRAIL_PUNCT = ".,;:!?)]}>'\""
_FFC_MAX_ENTRIES = 500


def _ffc_clean_match(s, is_url):
    """Strip trailing punctuation that the regex pulled in. URLs lose
    `).,;` etc. Paths the same. Returns the cleaned string or '' if
    cleaning leaves nothing useful."""
    if not s:
        return ""
    while s and s[-1] in _FFC_PATH_TRAIL_PUNCT:
        s = s[:-1]
    return s


def _ffc_iter_targets(text):
    """Yield (target, kind) for every URL/path mention in `text`.
    Does NOT filter by extension — caller (the extractor) does that."""
    if not isinstance(text, str) or not text:
        return
    for m in _FFC_URL_RE.finditer(text):
        cleaned = _ffc_clean_match(m.group(0), is_url=True)
        if cleaned:
            yield (cleaned, "url")
    for m in _FFC_PATH_RE.finditer(text):
        cleaned = _ffc_clean_match(m.group(1), is_url=False)
        if cleaned:
            yield (cleaned, "path")


def _ffc_flatten_strings(value):
    """Walk a tool_use input dict yielding every nested string. Used
    to scan entire `Bash{command: …}` / `Edit{old_string: …}` payloads,
    not just the surface fields, so a path buried in a long bash
    command is still caught."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _ffc_flatten_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _ffc_flatten_strings(v)


def _extract_files_from_conversation(conversation_id):
    """Walk the JSONL once and return a grouped, de-duped, capped
    payload of file-like artifacts mentioned anywhere in the
    conversation — tool_use inputs, assistant/user text, tool_results.
    Categorization is by extension whitelist (FILE_CATEGORIES);
    everything else (code, scripts, unknown extensions) is dropped.
    Returns {"count": int, "truncated": bool, "groups": {cat: [row…]}}.

    Cheap to call: single linear pass, no I/O beyond the JSONL read.
    """
    filepath = _resolve_conversation_path(conversation_id)
    seen = {}  # target -> {label, target, kind, category, first_line}
    line_num = 0
    truncated = False

    def consider(target, kind, line):
        nonlocal truncated
        if not target or target in seen:
            return
        category = _categorize_file_target(target)
        if not category:
            return
        if len(seen) >= _FFC_MAX_ENTRIES:
            truncated = True
            return
        # Label: basename for paths, URL last-path-segment (or host) for URLs.
        if kind == "url":
            try:
                parsed = urllib.parse.urlsplit(target)
                tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
                label = tail or parsed.netloc or target
            except ValueError:
                label = target
        else:
            label = os.path.basename(target) or target
        seen[target] = {
            "label": label,
            "target": target,
            "kind": kind,
            "category": category,
            "first_line": line,
        }

    try:
        with open(filepath, "r") as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = ev.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    for target, kind in _ffc_iter_targets(content):
                        consider(target, kind, line_num)
                    continue
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        for target, kind in _ffc_iter_targets(block.get("text", "")):
                            consider(target, kind, line_num)
                    elif btype == "tool_use":
                        # Direct fields first (so file_path with an exotic
                        # character the path-regex misses still lands).
                        inp = block.get("input")
                        if isinstance(inp, dict):
                            for fld in ("file_path", "notebook_path", "path"):
                                v = inp.get(fld)
                                if isinstance(v, str) and v.startswith("/"):
                                    consider(v, "path", line_num)
                        # Then deep scan every nested string.
                        for s in _ffc_flatten_strings(inp):
                            for target, kind in _ffc_iter_targets(s):
                                consider(target, kind, line_num)
                    elif btype == "tool_result":
                        rc = block.get("content")
                        texts = []
                        if isinstance(rc, str):
                            texts.append(rc)
                        elif isinstance(rc, list):
                            for sub in rc:
                                if isinstance(sub, dict) and sub.get("type") == "text":
                                    texts.append(sub.get("text", ""))
                        for t in texts:
                            for target, kind in _ffc_iter_targets(t):
                                consider(target, kind, line_num)
    except FileNotFoundError:
        return {"count": 0, "truncated": False, "groups": {}}

    # Group + sort by first_line ascending within each category.
    groups = {}
    for row in seen.values():
        cat = row.pop("category")
        groups.setdefault(cat, []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["first_line"])

    return {"count": len(seen), "truncated": truncated, "groups": groups}
```

Also ensure `urllib.parse` is imported at the top of `server.py` (it likely already is — search for `import urllib`; if not present, add `import urllib.parse`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_files_extraction -v`

Expected: all `TestCategorize` (6 tests) AND `TestExtractor` (5 tests) PASS — 11 tests total.

If `test_extracts_expected_files_per_category` shows mismatches, inspect by adding a `print(result)` and re-running; the most common slip is the path-regex anchor (the `(?<=…)` lookbehind) failing on a string that actually starts with `/`. The leading `(?:^|…)` alternative covers that.

- [ ] **Step 5: Commit**

```bash
git commit --only server.py tests/test_files_extraction.py -m "feat(server): _extract_files_from_conversation

Single-pass JSONL extractor — tool_use inputs (deep-scanned),
assistant/user text, tool_results. De-duped by target, capped at 500,
returned grouped by category with first-line ordering."
```

---

## Task 4 — Backend: `GET /api/conversations/<id>/files` endpoint

**Files:**
- Modify: `server.py` — add a new branch in the GET dispatcher.
- Modify: `tests/test_smoke.py` — add an import-time assertion that the route registration didn't break.

- [ ] **Step 1: Locate the GET dispatcher branch for `/api/conversations/<id>/stream`**

Grep: `cd /Users/amirfish/Apps/claude-command-center && grep -n "/api/conversations/\\[a-f0-9-\\]" server.py`

Expected: lines around 8536 (`stream$`) and 8554 (`/api/conversations/<id>` bare). The new branch slots between them.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_smoke.py` (find `class TestServerImports` and append a new test method):

```python
    def test_files_endpoint_route_registered(self):
        """Smoke check: GET /api/conversations/<id>/files dispatcher
        branch must be present in the do_GET source. Route registration
        in this codebase is by literal regex string, so a substring grep
        is the cheapest assertion."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = Path(server.__file__).read_text()
        self.assertIn("/api/conversations/[a-f0-9-]+/files", src)
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_smoke -v -k files_endpoint`

Expected: FAIL — substring not found.

- [ ] **Step 4: Add the dispatcher branch**

Find `server.py:8536` (`elif re.match(r"^/api/conversations/[a-f0-9-]+/stream$", path):`). **Above** that branch (so the more specific `/files` doesn't get shadowed by anything), insert:

```python
        elif re.match(r"^/api/conversations/[a-f0-9-]+/files$", path):
            conv_id = path.rsplit("/", 2)[1]
            payload = _extract_files_from_conversation(conv_id)
            self.send_json(payload)
```

Verify the surrounding code: this branch lives inside `do_GET`, after the `/api/conversations` (list) branch and before `/api/conversations/<id>/stream`. The match-order matters: more specific first.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_smoke -v -k files_endpoint`

Expected: PASS.

- [ ] **Step 6: Manual smoke (optional but cheap)**

Start the server: `cd /Users/amirfish/Apps/claude-command-center && ./run.sh &` (note the PID).

Then: `curl -s http://127.0.0.1:8090/api/conversations/00000000-mock-4000-8000-000000000001/files | python3 -m json.tool`

(Use whatever port `run.sh` uses — read it from `run.sh` if 8090 is wrong.) Expected: a JSON object with `count`, `truncated`, `groups` keys. Even an unknown id returns `{"count": 0, "truncated": false, "groups": {}}`.

Kill the server: `kill <PID>`.

- [ ] **Step 7: Commit**

```bash
git commit --only server.py tests/test_smoke.py -m "feat(server): GET /api/conversations/<id>/files endpoint"
```

---

## Task 5 — Backend: `POST /api/reveal-file` endpoint

**Files:**
- Modify: `server.py` — add a new branch in the POST dispatcher.
- Modify: `tests/test_smoke.py` — add an import-time assertion that the route is registered.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_smoke.py` inside `class TestServerImports`:

```python
    def test_reveal_file_route_registered(self):
        """Smoke check: POST /api/reveal-file branch present in do_POST."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = Path(server.__file__).read_text()
        self.assertIn('"/api/reveal-file"', src)
        # Defense-in-depth: extension clamp must be referenced near the
        # endpoint. Cheap signal that the security control wasn't dropped.
        idx = src.find('"/api/reveal-file"')
        self.assertGreater(idx, 0)
        nearby = src[idx:idx + 2000]
        self.assertIn("FILE_EXT_TO_CATEGORY", nearby,
                      "extension clamp missing near /api/reveal-file route")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_smoke -v -k reveal_file`

Expected: FAIL.

- [ ] **Step 3: Add the dispatcher branch**

Find the existing `/api/open` POST branch at `server.py:9244`. Insert **above** it (to keep document/data POSTs grouped):

```python
        elif path == "/api/reveal-file":
            # SECURITY: macOS `open` will execute apps and scripts. Unlike
            # /api/open (which clamps targets to repo_path/LOG_DIR), we
            # accept any path — but only if its extension is in
            # FILE_EXT_TO_CATEGORY. The whitelist excludes .app, .sh,
            # .command, .py, etc., so subprocess.Popen(["open", path])
            # cannot trigger code execution. Adding executable types to
            # FILE_CATEGORIES would re-introduce the RCE risk.
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            target = (payload.get("path") or "").strip()
            if not target:
                self.send_json({"ok": False, "error": "missing path"}, 400)
            elif not os.path.isabs(target):
                self.send_json({"ok": False, "error": "path must be absolute"}, 400)
            else:
                ext = os.path.splitext(target)[1].lower()
                if ext not in FILE_EXT_TO_CATEGORY:
                    self.send_json(
                        {"ok": False, "error": "extension not allowed", "ext": ext},
                        403,
                    )
                elif not os.path.exists(target):
                    self.send_json({"ok": False, "error": "not found", "path": target}, 404)
                else:
                    try:
                        subprocess.Popen(["open", target])
                        print(f"[reveal-file] {target}", file=sys.stderr)
                        self.send_json({"ok": True, "path": target})
                    except Exception as e:
                        self.send_json({"ok": False, "error": str(e)}, 500)
```

Confirm `import sys` and `import subprocess` are already present at the top of `server.py` (they are — `subprocess` is used by `/api/open`).

- [ ] **Step 4: Run the smoke test to verify it passes**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest tests.test_smoke -v -k reveal_file`

Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest discover tests -v`

Expected: all tests pass (smoke + classify + morning + files-extraction). No regressions.

- [ ] **Step 6: Manual smoke (optional)**

With server running, from the same machine (same-origin):

```bash
curl -s -X POST http://127.0.0.1:<PORT>/api/reveal-file \
  -H "Origin: http://127.0.0.1:<PORT>" \
  -H "Content-Type: application/json" \
  -d '{"path": "/Users/amirfish/Apps/claude-command-center/README.md"}'
```

Expected: `{"ok": true, "path": "..."}` and the README opens in the user's default `.md` app (TextEdit / Typora / VS Code, depending). Check `[reveal-file]` log line on the server stderr.

Try with a `.sh` path: expect 403 with `"extension not allowed"`. Try with a `.pdf` that doesn't exist: expect 404.

- [ ] **Step 7: Commit**

```bash
git commit --only server.py tests/test_smoke.py -m "feat(server): POST /api/reveal-file with extension clamp

Opens any local file whose extension is in FILE_EXT_TO_CATEGORY via
macOS \`open\`. No path-prefix sandbox — the whitelist excludes
executable types, which is what /api/open's path clamp guarded
against. Same-origin POST."
```

---

## Task 6 — Frontend: CSS for the pill + modal

**Files:**
- Modify: `static/index.html` — add CSS in the existing `<style>` block.

- [ ] **Step 1: Locate the existing modal CSS**

The new-session modal CSS is at `static/index.html:1938` (`.nsm-backdrop`). Modal CSS lives in the same `<style>` block. Find a sensible insertion point right after the `.nsm-` block ends (look for the next class after `.nsm-` that isn't part of it) — somewhere around line 1965, before the `.rpm-` block.

- [ ] **Step 2: Add the pill + modal CSS**

Insert this CSS block in the `<style>` section (location: after the `.nsm-` rules, before the `.rpm-` rules):

```css
  /* --- Files-from-conversation (pill + modal) ----------------------- */
  /* Pill lives in .conv-sticky-header; only rendered when count > 0. */
  .ffc-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 10px; margin-left: 8px;
    border: 1px solid var(--border, #30363d);
    border-radius: 999px;
    background: var(--surface-2, #161b22);
    color: var(--text, #c9d1d9);
    font-size: 11px; font-weight: 500; cursor: pointer;
    line-height: 1.4;
  }
  .ffc-pill:hover { border-color: var(--accent, #58a6ff); color: var(--accent, #58a6ff); }
  .ffc-pill[hidden] { display: none !important; }
  .ffc-pill-count { opacity: 0.7; }

  /* Modal — modeled on .nsm- pattern. */
  .ffc-overlay {
    position: fixed; inset: 0; z-index: 200;
    display: flex; align-items: center; justify-content: center;
  }
  .ffc-overlay[hidden] { display: none !important; }
  .ffc-backdrop {
    position: absolute; inset: 0;
    background: rgba(0,0,0,0.65);
    -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
  }
  .ffc-dialog {
    position: relative;
    background: var(--surface, #0d1117);
    border: 1px solid var(--border, #30363d);
    border-radius: 8px;
    width: min(640px, 92vw);
    max-height: 80vh;
    display: flex; flex-direction: column;
    box-shadow: 0 16px 64px rgba(0,0,0,0.5);
  }
  .ffc-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border, #30363d);
  }
  .ffc-title { font-size: 14px; font-weight: 600; color: var(--text, #c9d1d9); }
  .ffc-close {
    background: none; border: none; cursor: pointer;
    color: var(--text-muted, #8b949e); font-size: 18px; line-height: 1;
    padding: 4px 8px; border-radius: 4px;
  }
  .ffc-close:hover { background: var(--surface-2, #161b22); color: var(--text, #c9d1d9); }
  .ffc-body {
    overflow-y: auto; padding: 8px 16px 16px;
  }
  .ffc-section { margin-top: 12px; }
  .ffc-section:first-child { margin-top: 0; }
  .ffc-section-title {
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--text-muted, #8b949e); margin-bottom: 6px;
  }
  .ffc-row {
    display: flex; flex-direction: column; gap: 2px;
    padding: 8px 10px; border-radius: 6px;
    cursor: pointer;
  }
  .ffc-row:hover { background: var(--surface-2, #161b22); }
  .ffc-row-label {
    display: flex; align-items: center; gap: 8px;
    color: var(--text, #c9d1d9); font-size: 13px;
  }
  .ffc-row-icon { width: 18px; text-align: center; }
  .ffc-row-target {
    color: var(--text-muted, #8b949e); font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
    cursor: copy;
  }
  .ffc-row-target:hover { color: var(--text, #c9d1d9); }
  .ffc-row-toast {
    color: #f85149; font-size: 11px; margin-top: 2px;
  }
  .ffc-footer {
    padding: 8px 16px;
    border-top: 1px solid var(--border, #30363d);
    color: var(--text-muted, #8b949e); font-size: 11px;
  }
  .ffc-empty {
    color: var(--text-muted, #8b949e); padding: 16px; text-align: center;
  }
```

- [ ] **Step 3: Reload the page (no dev-build step) — verify CSS parses**

There's nothing dynamic to verify yet (no DOM is using these classes). Just confirm the page still loads with no console CSS errors.

Run: `cd /Users/amirfish/Apps/claude-command-center && ./run.sh &` then open `http://127.0.0.1:<PORT>` in browser, open devtools console, confirm zero CSS warnings.

Kill the server: `kill <PID>`.

- [ ] **Step 4: Commit**

```bash
git commit --only static/index.html -m "feat(ui): CSS for files-from-conversation pill + modal"
```

---

## Task 7 — Frontend: pill DOM injection + fetch lifecycle

**Files:**
- Modify: `static/index.html` — add JS for the pill, the per-conversation cache, and the fetch on conversation switch.

- [ ] **Step 1: Locate the conversation-switch hook**

The active conversation id lives in `currentConversation` (declared at `static/index.html:5133`). The conversation events render through `renderConversationEvents` at line 9689; the sticky header is created inside that function around line 9725 (`sticky.className = 'conv-sticky-header'`).

The cleanest hook for "fetch the files list when a new conversation is selected" is `renderConversationEvents` itself — it already runs once per `/api/conversations/<id>` response.

- [ ] **Step 2: Add the FFC module-scoped state and fetch helper**

Find a sensible location in `static/index.html` — near the bottom of the JS block, after other helpers like `nowStamp` / `getConvView`. Insert:

```javascript
  // ----------------------------------------------------------------
  // Files-from-conversation: per-conversation cache + pill rendering.
  // The pill lives in .conv-sticky-header and only renders when the
  // count is > 0. Click → openFfcModal (Task 8).
  // ----------------------------------------------------------------
  const _ffcCache = new Map(); // conversation_id -> {count, truncated, groups}

  async function ffcFetch(convId) {
    if (!convId || convId === '__new__' || convId.startsWith('backlog-') || convId.startsWith('pkood-')) {
      return null;
    }
    if (_ffcCache.has(convId)) {
      return _ffcCache.get(convId);
    }
    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(convId) + '/files');
      if (!r.ok) {
        _ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
        return _ffcCache.get(convId);
      }
      const data = await r.json();
      _ffcCache.set(convId, data);
      return data;
    } catch (e) {
      // Network / parse failure — silent. Pill just stays hidden.
      _ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
      return _ffcCache.get(convId);
    }
  }

  function ffcInvalidate(convId) {
    if (convId) _ffcCache.delete(convId);
  }

  function ffcEnsurePill(stickyEl, data) {
    if (!stickyEl) return;
    let pill = stickyEl.querySelector('.ffc-pill');
    const hasFiles = data && data.count > 0;
    if (!hasFiles) {
      if (pill) pill.hidden = true;
      return;
    }
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'ffc-pill';
      pill.title = 'Files mentioned in this conversation';
      pill.innerHTML = '📎 Files <span class="ffc-pill-count"></span>';
      pill.addEventListener('click', () => {
        const cur = currentConversation;
        const cached = cur ? _ffcCache.get(cur) : null;
        if (cached) openFfcModal(cached);
      });
      // Append to the sticky header. CSS sets margin-left so it sits
      // next to whatever was already there.
      stickyEl.appendChild(pill);
    }
    pill.hidden = false;
    pill.querySelector('.ffc-pill-count').textContent = '(' + data.count + ')';
  }

  async function ffcRefreshForCurrent() {
    const cur = currentConversation;
    if (!cur) return;
    const data = await ffcFetch(cur);
    // The conversation may have switched while we were fetching. Bail
    // if so — the new conversation's renderer will trigger its own.
    if (currentConversation !== cur) return;
    const sticky = document.querySelector('.conversations-view .conv-sticky-header');
    ffcEnsurePill(sticky, data);
  }
```

- [ ] **Step 3: Wire the fetch into the conversation render path**

Find `function renderConversationEvents(events) {` at `static/index.html:9689`. At the **end of the function** (just before the closing `}`), append:

```javascript
    // Files-from-conversation pill: the sticky header may have just
    // been created (above) or may already exist. Fire-and-forget; the
    // pill stays hidden if the conversation has no qualifying files.
    ffcRefreshForCurrent();
```

- [ ] **Step 4: Wire cache invalidation to streaming hand-offs**

When new events stream into an open conversation, `_extract_files_from_conversation` results may have changed. Find every call site that mutates the open conversation's events (search the file for `renderConversationEvents` calls to find them). For the streaming-append path specifically, add an `ffcInvalidate(currentConversation); ffcRefreshForCurrent();` call after the append.

Cheap heuristic: search the file for `renderConversationEvents(data.events)` — there are at least two call sites (initial load + streaming polls). For the streaming poll site (the one that appends, not replaces), add the invalidation. Identify it by the surrounding context comment about polling/streaming.

If you can't unambiguously find the streaming-poll site, fall back to invalidating on a 30s timer:

```javascript
  setInterval(() => {
    if (currentConversation) {
      ffcInvalidate(currentConversation);
      ffcRefreshForCurrent();
    }
  }, 30000);
```

(Place this once, near the bottom of the JS block.) The fallback adds one extra GET every 30s — cheap.

- [ ] **Step 5: Manual smoke**

`./run.sh &`, open the dashboard, click on a conversation that you know contains an image or PDF Read, watch for `📎 Files (N)` to appear in the sticky header. Click switches between conversations: pill should update its count or disappear.

If the pill never appears: open devtools, check Network tab for `/api/conversations/<id>/files` — verify it returned a non-zero `count`. If count is 0 but you know the convo had files, the regex anchoring may be missing a case; capture the JSONL line and add a unit test in Task 3 covering it.

Kill server.

- [ ] **Step 6: Commit**

```bash
git commit --only static/index.html -m "feat(ui): files-from-conversation pill + fetch lifecycle"
```

---

## Task 8 — Frontend: modal scaffolding + click handlers + toasts

**Files:**
- Modify: `static/index.html` — add the modal HTML once, plus `openFfcModal` / `closeFfcModal` JS.

- [ ] **Step 1: Add the modal markup**

Find where the new-session modal is defined: `static/index.html:3395` (`<div id="newSessionModal" class="nsm-overlay" …>`). After that block (or any other top-level modal block in the body), insert:

```html
  <div id="ffcOverlay" class="ffc-overlay" hidden role="dialog" aria-modal="true" aria-labelledby="ffcTitle">
    <div class="ffc-backdrop" id="ffcBackdrop"></div>
    <div class="ffc-dialog">
      <div class="ffc-header">
        <div class="ffc-title" id="ffcTitle">Files in this conversation</div>
        <button type="button" class="ffc-close" id="ffcClose" aria-label="Close">×</button>
      </div>
      <div class="ffc-body" id="ffcBody"></div>
      <div class="ffc-footer" id="ffcFooter" hidden></div>
    </div>
  </div>
```

- [ ] **Step 2: Add the `openFfcModal` / `closeFfcModal` JS**

Append to the same JS block where `ffcRefreshForCurrent` lives (Task 7):

```javascript
  const FFC_CATEGORY_ORDER = [
    {key: 'images',        label: 'Images',        icon: '📷'},
    {key: 'pdfs',          label: 'PDFs',          icon: '📕'},
    {key: 'docs',          label: 'Docs',          icon: '📄'},
    {key: 'presentations', label: 'Presentations', icon: '📊'},
    {key: 'videos',        label: 'Videos',        icon: '🎬'},
    {key: 'markdown',      label: 'Markdown',      icon: '📝'},
    {key: 'html',          label: 'HTML',          icon: '🌐'},
  ];

  function ffcEscapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function ffcRenderRow(row, icon) {
    const div = document.createElement('div');
    div.className = 'ffc-row';

    const labelEl = document.createElement('div');
    labelEl.className = 'ffc-row-label';
    labelEl.innerHTML = '<span class="ffc-row-icon">' + icon + '</span>' +
                       '<span>' + ffcEscapeHtml(row.label) + '</span>';
    if (row.kind === 'url') {
      const a = document.createElement('a');
      a.href = row.target;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.style.color = 'inherit';
      a.style.textDecoration = 'none';
      a.appendChild(labelEl);
      div.appendChild(a);
    } else {
      div.appendChild(labelEl);
      div.addEventListener('click', async (e) => {
        if (e.target.classList.contains('ffc-row-target')) return; // don't double-trigger on path-copy
        try {
          const r = await fetch('/api/reveal-file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: row.target}),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({error: 'open failed'}));
            ffcShowRowToast(div, j.error || ('HTTP ' + r.status));
          }
        } catch (err) {
          ffcShowRowToast(div, 'network error');
        }
      });
    }

    const tgt = document.createElement('div');
    tgt.className = 'ffc-row-target';
    tgt.textContent = row.target;
    tgt.title = 'Click to copy';
    tgt.addEventListener('click', (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(row.target).then(() => {
        const orig = tgt.textContent;
        tgt.textContent = 'copied';
        setTimeout(() => { tgt.textContent = orig; }, 900);
      }).catch(() => {});
    });
    div.appendChild(tgt);

    return div;
  }

  function ffcShowRowToast(rowEl, msg) {
    let toast = rowEl.querySelector('.ffc-row-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'ffc-row-toast';
      rowEl.appendChild(toast);
    }
    toast.textContent = msg;
    setTimeout(() => {
      if (toast.parentNode === rowEl) rowEl.removeChild(toast);
    }, 3000);
  }

  function openFfcModal(data) {
    const overlay = document.getElementById('ffcOverlay');
    const body    = document.getElementById('ffcBody');
    const footer  = document.getElementById('ffcFooter');
    if (!overlay || !body) return;
    body.innerHTML = '';

    if (!data || !data.count) {
      const empty = document.createElement('div');
      empty.className = 'ffc-empty';
      empty.textContent = 'No files mentioned in this conversation.';
      body.appendChild(empty);
    } else {
      for (const cat of FFC_CATEGORY_ORDER) {
        const rows = (data.groups || {})[cat.key];
        if (!rows || !rows.length) continue;
        const section = document.createElement('div');
        section.className = 'ffc-section';
        const title = document.createElement('div');
        title.className = 'ffc-section-title';
        title.textContent = cat.label + ' (' + rows.length + ')';
        section.appendChild(title);
        for (const row of rows) {
          section.appendChild(ffcRenderRow(row, cat.icon));
        }
        body.appendChild(section);
      }
    }

    if (data && data.truncated) {
      footer.hidden = false;
      footer.textContent = 'Showing first 500 — conversation contains more.';
    } else {
      footer.hidden = true;
      footer.textContent = '';
    }

    overlay.hidden = false;
    document.addEventListener('keydown', _ffcEscHandler);
  }

  function closeFfcModal() {
    const overlay = document.getElementById('ffcOverlay');
    if (overlay) overlay.hidden = true;
    document.removeEventListener('keydown', _ffcEscHandler);
  }

  function _ffcEscHandler(e) {
    if (e.key === 'Escape') closeFfcModal();
  }

  // Wire close handlers once on DOM ready.
  document.addEventListener('DOMContentLoaded', () => {
    const backdrop = document.getElementById('ffcBackdrop');
    const close    = document.getElementById('ffcClose');
    if (backdrop) backdrop.addEventListener('click', closeFfcModal);
    if (close)    close.addEventListener('click', closeFfcModal);
  });
```

- [ ] **Step 3: Manual smoke**

`./run.sh &`. Open a conversation with files. Click the pill. Verify:

- Modal appears, sections render in the fixed order (Images / PDFs / Docs / Presentations / Videos / Markdown / HTML), empty sections skipped.
- Hovering a row highlights the background.
- Clicking a URL row → new tab opens.
- Clicking a path row → file opens in macOS default app (PDF in Preview, MD in Typora/TextEdit, etc.).
- Clicking the faint full-path/URL line → "copied" tooltip appears, clipboard contains the target.
- Clicking outside the dialog or pressing Esc → modal closes.

If a path open fails (e.g. you renamed the file since the conversation), verify the inline red toast shows under that row and disappears after ~3s.

Kill server.

- [ ] **Step 4: Commit**

```bash
git commit --only static/index.html -m "feat(ui): files-from-conversation modal with grouped sections"
```

---

## Task 9 — Versioning + changelog + final sweep

**Files:**
- Modify: `pyproject.toml`
- Modify: `server.py` (the `__version__ = ` line)
- Create: `changelog.d/added-files-from-conversation-2026-04-30.md`

- [ ] **Step 1: Bump the version in `pyproject.toml`**

Edit `pyproject.toml`:

```toml
version = "0.3.0"
```

(was `0.2.1`).

- [ ] **Step 2: Bump `__version__` in `server.py` to match**

Find the line `__version__ = "0.2.1"` near the top of `server.py`. Change to:

```python
__version__ = "0.3.0"
```

- [ ] **Step 3: Create the changelog snippet**

Create `changelog.d/added-files-from-conversation-2026-04-30.md`:

```
- Files from this conversation — header pill listing every image, PDF, doc, presentation, video, MD, and HTML mentioned in a session, openable in one click via macOS default app (local) or new browser tab (URLs).
```

- [ ] **Step 4: Run the full test suite one more time**

Run: `cd /Users/amirfish/Apps/claude-command-center && python3 -m unittest discover tests -v`

Expected: every test passes, no skips beyond what was already in baseline.

- [ ] **Step 5: Final commit (atomic, only the version + changelog files)**

```bash
git add pyproject.toml server.py changelog.d/added-files-from-conversation-2026-04-30.md
git commit --only pyproject.toml server.py changelog.d/added-files-from-conversation-2026-04-30.md -m "chore(release): bump 0.2.1 → 0.3.0 — files-from-conversation"
```

- [ ] **Step 6: Verify the working tree is clean of feature changes**

Run: `cd /Users/amirfish/Apps/claude-command-center && git status --short`

Expected: only sibling-session in-flight files (e.g. unrelated `static/index.html` modifications, unrelated `changelog.d/*.md` snippets) — none of which were authored by this feature. Our commits should be clean.

Run: `cd /Users/amirfish/Apps/claude-command-center && git log --oneline -10`

Expected: a sequence of clean commits (one per task plus the spec commit), ordered newest-first.

---

## Self-review (already performed by the planner)

Spec coverage check — every spec section maps to a task:

| Spec section | Task |
|---|---|
| Section 1: extension whitelist + extractor | Tasks 1, 2, 3 |
| Section 2: `/api/reveal-file` opener | Task 5 |
| Section 2: extension clamp (security) | Task 5 step 3 + smoke test |
| Section 3: pill in conversation header | Task 7 |
| Section 3: modal layout (grouped sections) | Tasks 6, 8 |
| Section 3: click → URL anchor / path POST | Task 8 |
| Section 3: click-to-copy on full path | Task 8 |
| Section 3: Esc / backdrop close | Task 8 |
| Data flow (fetch on switch + cache) | Task 7 |
| Error handling (404 toast, 403 toast) | Task 8 |
| Truncation footer | Task 8 step 2 (`data.truncated` branch) |
| Versioning + changelog | Task 9 |

No placeholders. Type names consistent across tasks (`FILE_CATEGORIES`, `FILE_EXT_TO_CATEGORY`, `_categorize_file_target`, `_extract_files_from_conversation`, `ffcFetch`, `ffcRefreshForCurrent`, `openFfcModal`, `closeFfcModal`, `FFC_CATEGORY_ORDER`).
