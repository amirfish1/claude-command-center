# Files from this conversation — design

Status: draft
Date: 2026-04-30
Owner: amirfish

## Problem

Conversations in CCC routinely surface useful artifacts — screenshots
the user pasted, PDFs the assistant read with `Read`, presentations
linked from drive, demo videos, MD specs, generated HTML reports. Today
the only way to find them again is to scroll the conversation pane and
eyeball it. There's no listing, no jump-back affordance.

The user wants a **per-conversation index of every file-like artifact
mentioned**, opened externally with one click — local files via the
macOS default app, URLs in a new browser tab. Code/scripts are
explicitly excluded; the value is in non-code attachments (images,
docs, videos, PDFs, etc.).

## Out of scope

- File previews / thumbnails inside the modal.
- Persisting the cached payload across page reloads.
- Indexing files across *other* conversations or globally.
- Hooking the index into the kanban cards, morning view, or
  per-session tooltips.
- An always-on inline panel above the conversation. (Considered;
  rejected — costs vertical space on every conversation, even those
  with zero files.)
- Automatic categorization of *unknown* extensions. The category list
  is closed; new extensions require a code change. This is a feature,
  not a limitation — the whitelist is the security clamp on the
  opener (see Section 2).

## Solution

Three pieces:

1. A new **server endpoint** `GET /api/conversations/<id>/files` walks
   the JSONL with full fidelity (no 200-char tool-detail truncation
   that `/api/conversations` applies) and returns a grouped JSON
   payload.
2. A new **server endpoint** `POST /api/reveal-file` opens a single
   local path in the macOS default app via `open <path>`. Same-origin
   checked. Clamped by **extension whitelist** (not path-prefix) so
   scripts/apps cannot reach `open`. URLs do not touch the server.
3. A new **header pill + modal** in `static/index.html` — `📎 Files
   (N)` next to the conversation title; click opens a modal grouped
   by category (Images / PDFs / Docs / Presentations / Videos /
   Markdown / HTML).

## Section 1 — Server-side extraction

A new module-level constant in `server.py`:

```python
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
```

This dict is the single source of truth — both the extractor and the
`/api/reveal-file` opener import it.

A new function `_extract_files_from_conversation(conversation_id) ->
dict` walks the same JSONL `_resolve_conversation_path()` returns,
single pass, no truncation. For each event:

- **`tool_use.input`** — recursively flatten all string values in the
  input dict. For each string, run the extraction regex (URLs +
  absolute paths) AND, separately, treat single-string fields named
  `file_path` / `notebook_path` as direct path candidates (so
  `Read{file_path: "/x/y.pdf"}` is captured even though the regex
  would also match it).
- **`assistant` text blocks** — run the extraction regex.
- **`user` text** — run the extraction regex.
- **`tool_result` content text** — run the extraction regex.

Extraction regex (one URL pattern, one absolute-path pattern):

- URLs: `https?://[^\s<>"'`]+` — strip trailing punctuation
  (`.,;:!?)]}>` and matching close-brace if there's no open-brace).
- Absolute paths: `(?:^|[\s"'`(\[])(/[^\s"'`<>)\]]+)` — must start at
  string-start or after whitespace/quote/paren to avoid matching
  middle-of-token slashes.

Each match is dispatched by extension:

- If the extension (lowercase) is in `FILE_EXT_TO_CATEGORY`, keep it.
- Otherwise drop it. (No "other" bucket — keeps the modal scoped to
  the seven announced categories.)

Results are de-duped by canonical target string (URL or abs path,
case-sensitive on path, lowercase scheme+host for URL). First-seen
line number is preserved for ordering inside each category. Total cap:
500 entries (defensive — a runaway conversation with thousands of
paths shouldn't blow up the modal). If hit, response includes
`"truncated": true`.

Response shape:

```json
{
  "count": 7,
  "truncated": false,
  "groups": {
    "images": [
      {"label": "diagram.png",
       "target": "/Users/amir/Apps/foo/diagram.png",
       "kind": "path",
       "first_line": 142}
    ],
    "pdfs": [
      {"label": "spec.pdf",
       "target": "https://drive.google.com/.../spec.pdf",
       "kind": "url",
       "first_line": 88}
    ]
  }
}
```

`label` is the basename for paths, the last URL path segment for URLs
(falling back to the host if the path is `/`). `kind` is `"path"` or
`"url"`. Empty groups are omitted from the response.

Endpoint registration: a new branch in the GET dispatcher matching
`^/api/conversations/[a-f0-9-]+/files$`, alongside the existing
`/api/conversations/<id>/stream` branch. Returns 404 with `{count: 0,
groups: {}}` body if the JSONL doesn't resolve, matching the existing
`/api/conversations/<id>` shape.

## Section 2 — Reveal-file opener

A new POST endpoint:

```
POST /api/reveal-file
Body: {"path": "/abs/path/to/file.pdf"}
```

Steps, in order:

1. **Same-origin check** via the existing `_check_same_origin()`. 403
   on fail.
2. **Extension clamp.** Lowercase the extension; if not in
   `FILE_EXT_TO_CATEGORY`, return 403 `{ok: false, error: "extension
   not allowed"}`. **This is the load-bearing security control.** It
   replaces the path-prefix sandbox that `/api/open` uses. The reason
   `/api/open` clamps paths is that macOS `open` will execute apps and
   scripts (`.app`, `.sh`, `.command`, etc.); with a closed whitelist
   of inert document/media extensions, that risk is gone.
3. **Existence check.** `os.path.exists(path)` — 404 if missing.
4. **Absolute-path check.** Reject relative paths (400). The UI only
   ever sends absolute paths (extracted from JSONL), so this is
   defense-in-depth for hand-crafted requests.
5. **Shell out.** `subprocess.Popen(["open", str(path)])`. No `-R`
   (we want default-app launch, not Finder reveal — user said "open
   externally").
6. **Log line.** Single `print("[reveal-file] <path>", file=sys.stderr)`
   so opens are auditable from `tail -f` on the server log.
7. Return `{"ok": true, "path": str(path)}`.

URLs do NOT route through this endpoint; they're rendered as `<a
href="…" target="_blank" rel="noopener noreferrer">` and the browser
handles them. No server roundtrip.

### Why a new endpoint and not `/api/open`

`/api/open` does two things this feature doesn't want:

- It defaults to `open -R` (Finder reveal) and only launches with an
  explicit `launch: true` flag. The user's mental model here is
  "click → it opens", not "click → Finder shows me where it is".
- Its sandbox clamp rejects every path outside `REPO_ROOT` and
  `LOG_DIR`. The most interesting files in a conversation —
  screenshots in `~/Downloads`, PDFs in `~/Desktop`, anything pasted
  from outside the repo — would silently 403.

Reusing `/api/open` would require relaxing both behaviors and would
muddy the security comment that lives there ("RCE-as-a-feature"). A
purpose-built endpoint is clearer for the next reader.

## Section 3 — Frontend: pill + modal

### The pill

Lives in the conversation-pane header strip. Hidden by default;
revealed when the count is `> 0`. Format: `📎 Files (N)`. Sits to the
right of the existing session-title block, before the
status/verified/archive controls. CSS reuses the existing pill styling
used for context badges and worktree chips elsewhere in the header.

When the conversation is switched, the count resets and the pill
hides until the new conversation's `/files` fetch resolves.

### Fetch lifecycle

After the existing conversation events render
(`renderConversationEvents` in `static/index.html`), kick off
`fetch('/api/conversations/<id>/files')`. On resolve:

- Stash the payload on a module-scoped `currentFiles` variable keyed by
  conversation id (so re-clicking is free; switching conversations
  invalidates the cache).
- Update the pill's count and visibility.

The fetch is fire-and-forget; failure leaves the pill hidden, no toast
(no point alerting the user to a missing affordance).

### The modal

Reuses the existing modal scaffolding (the same dimming
backdrop+centered-card pattern used by other modals in `index.html`).
Sections rendered in fixed order:

```
Images       (3)
PDFs         (2)
Docs         (1)
Presentations
Videos       (1)
Markdown
HTML
```

Empty sections are skipped. Each row:

```
[icon]  filename.ext              ← clickable
        /full/path/or/url          ← faint, muted, click-to-copy
```

Icons are unicode (`📷 🎬 📄 📕 📊 📝 🌐`) — keeps the single-file app
free of icon-font dependencies.

Click on the filename:

- `kind: "url"` → native anchor; new tab.
- `kind: "path"` → `fetch('/api/reveal-file', {method: 'POST', body:
  JSON.stringify({path})})`. On `ok: false`, render an inline toast
  inside the modal row ("not found at /…/foo.pdf") that auto-dismisses
  after 3 s.

Click on the faint full-path/URL line copies it to the clipboard via
`navigator.clipboard.writeText`, with a transient "copied" tooltip.

Modal closes on Esc, backdrop click, or close-button click. Standard
patterns already present in the file.

If the response had `truncated: true`, the modal footer shows a small
muted line: "Showing first 500 — conversation contains more."

## Data flow

```
1. User selects conversation X
2. Existing flow: fetch /api/conversations/X     → events render
3. New flow:      fetch /api/conversations/X/files → pill updates
4. User clicks 📎 Files (7)                      → modal opens (cached payload)
5. User clicks row "spec.pdf"
   - kind=url   → <a target=_blank> handles it
   - kind=path  → POST /api/reveal-file → subprocess.Popen(["open", path])
```

## Error handling

| Case | Behavior |
| --- | --- |
| Unknown conversation id | `/files` returns `{count: 0, groups: {}}`. Pill stays hidden. |
| `/files` request fails (5xx, network) | Pill stays hidden. No toast. |
| File no longer exists at path | `/api/reveal-file` returns 404. Inline modal-row toast. |
| Path has disallowed extension (defense-in-depth — UI shouldn't render this) | `/api/reveal-file` returns 403. Inline modal-row toast: "not allowed". |
| Same-origin check fails | 403 generic. Matches existing endpoints. |
| `subprocess.Popen` raises | 500 with `error: <stringified exception>`. Modal-row toast. |
| Truncated payload (>500 entries) | Modal renders with footer note. |

## Security

- **Extension whitelist is the load-bearing control.** Keep
  `FILE_CATEGORIES` tight. Adding `.app`, `.sh`, `.command`,
  `.applescript`, `.workflow`, or any executable type to the whitelist
  would re-introduce the RCE that `/api/open`'s path-clamp prevents.
- **No path-prefix clamp**, by design. Document this in a comment at
  the endpoint so the next reviewer doesn't "fix" it by adding one.
- **Same-origin enforced** like every other POST endpoint in
  `server.py`.
- The extracted paths come from the user's own JSONL — i.e. files the
  user (or assistants the user ran) already touched. The trust
  boundary here is "the user's own conversation history", which is
  the same boundary the rest of the dashboard already trusts.

## Testing

Per repo policy (`tests/test_smoke.py` is import-only), the bar is
"doesn't break the import." Two additions:

- A small fixture JSONL at `tests/fixtures/files-extraction.jsonl`
  containing one assistant turn, one user turn with an image paste,
  one `Read` tool call with a PDF, one `Bash` tool call with a path
  buried in the command, and one URL in assistant text.
- A behavioral test in `tests/test_smoke.py` (or a sibling
  `tests/test_files_extraction.py` — match what's there) calling
  `_extract_files_from_conversation` against the fixture and asserting
  the expected counts per category. stdlib `unittest`, no mocks.

No test for `/api/reveal-file` shelling to `open` — fragile across CI
and dev environments, low value. Manual smoke during implementation.

## Versioning + changelog

This adds new endpoints (no changes to existing `/api/*` shapes). Per
SemVer policy in `CLAUDE.md`, that's a **minor** bump.

- Bump `pyproject.toml` and `server.py` `__version__` from current
  patch level to next minor.
- Add `changelog.d/added-files-from-conversation-<date>.md` with a
  one-line entry: `Files from this conversation — header pill listing
  every image, doc, PDF, video, presentation, MD, and HTML mentioned
  in a conversation, openable in one click.`

## Risks

- **Regex over-extraction.** A path like `/usr/local/etc/foo.png` in
  a Bash command output could be a real file or a string in a log. We
  rely on `os.path.exists` at open-time, not at extraction-time —
  extracted entries that don't exist will surface a 404 toast on
  click. Acceptable: false positives are visible and dismissible.
- **Regex under-extraction.** Backtick-fenced or quote-stripped paths
  may be missed. The regex is intentionally conservative
  (whitespace/quote/paren as left-anchor); we accept under-extraction
  in exchange for not pulling tokens out of code identifiers.
- **Conversation size.** Large conversations (10k+ events) get one
  extra full-JSONL pass on conversation switch. Single pass, in-memory,
  no caching: should be sub-100ms for typical sessions. If this turns
  out to bite, cache by `(conversation_id, jsonl_mtime)` keyed in
  memory, same shape as the existing session-cache pattern.
- **macOS-only `open`.** This endpoint is mac-specific, like
  `/api/open`. Linux/Windows users get a 500 (or `open` not found).
  Not a regression — every CCC opener today is macOS-only.
