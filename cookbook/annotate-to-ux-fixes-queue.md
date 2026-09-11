# Annotate → UX-fixes queue

Wire an **Annotate** dev tool in your own app into CCC's durable, numbered
UX-fixes queue — so "this button is misaligned" becomes a claimable work item
that a Claude Code session picks up and implements, instead of a sticky note
you forget.

## What this gives you

You're dogfooding your app. You spot a UI problem. Instead of context-switching
to write a ticket, you:

1. Click the floating **Annotate** button in your app.
2. Click the broken element, type one sentence.
3. Done. The annotation — note, URL, CSS selector, element info, rect,
   viewport — lands in CCC's queue as a numbered item like `MYAPP-7`.

On the other side, a long-running Claude Code session **claims** items from the
queue when it's free, gets the full formatted prompt (with anchors precise
enough to find the exact element), implements the fix, closes the item, and
claims the next one.

## Why a queue and not direct injection

The naive version injects annotation text straight into a running agent
session. That interrupts whatever the session was doing, leaves no record a
second session can see, and silently drops work if no session is running.

The queue fixes all three:

- **Durable** — every annotation is a row in a JSON file
  (`~/.claude/command-center/ux-fixes-queue.json`), not a paragraph lost in a
  transcript. Nothing is dropped if no worker is alive.
- **Numbered** — items get per-project refs (`MYAPP-1`, `MYAPP-2`), so humans
  can say "take #7" and the UI can show "Queued as MYAPP-7".
- **Pull, not push** — workers *claim* items when free instead of being
  interrupted. Multiple sessions can drain the queue in parallel; cross-process
  locking and numbering are CCC's problem, not yours.

CCC owns the queue file, locking, and claim semantics (via `watchtower.queue`,
a hard dependency — see `github.com/amirfish1/watchtower`). Your app only
speaks HTTP.

## Architecture

```
your app (browser)                your app (server)                 CCC (port 8090)
┌──────────────────┐   POST       ┌───────────────────┐   POST      ┌──────────────────────┐
│ Annotate overlay  │ ──────────▶ │ /api/dev/annotate │ ──────────▶ │ /api/annotations/    │
│ note + selector + │             │ formats prompt,   │             │   ux-fixes-queue     │
│ rect + viewport   │             │ adds repo_path    │             │ → item MYAPP-7       │
└──────────────────┘              └───────────────────┘             └──────────┬───────────┘
                                                                               │ claim/next
                                                                    ┌──────────▼───────────┐
                                                                    │ Claude Code worker   │
                                                                    │ session ("UX-fixes") │
                                                                    └──────────────────────┘
```

The hop through your own server route (rather than the browser calling CCC
directly) keeps the dev tool inside your app's auth/gating, and lets the server
attach the one field the browser can't know: the **absolute repo path on
disk**, which drives project routing on the CCC side.

## The HTTP contract

### Enqueue (your app → CCC)

`POST http://127.0.0.1:8090/api/annotations/ux-fixes-queue`

```json
{
  "text":  "<full formatted prompt for the coding agent>",
  "note":  "<the user's raw note>",
  "url": "https://localhost:3000/dashboard",
  "title": "Dashboard",
  "selector": "#nav > button.save",
  "screenshot_path": "",
  "repo_path": "/absolute/path/to/your/repo",
  "source": "myapp"
}
```

Response: `{"ok": true, "action": "queued", "number": 12, "item": {"ref": "MYAPP-7", "project": "MYAPP", ...}}`

Notes:

- **`repo_path` is the routing key.** The repo directory's basename becomes the
  project code (`my-app` → `MY-APP` refs). Omit it and items misfile under a
  generic project.
- No auth or Origin header needed: CCC's trust model is loopback-only, and a
  server-to-server fetch sends no `Origin`, so it passes CCC's CSRF gate.
- Use a short timeout (~10s) and **never drop the annotation on failure** —
  return the formatted prompt to the user for manual paste instead.

### Worker loop (Claude session → CCC)

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/ux-fixes/claim` | `{"session_id": "<id>"}` | Claim the oldest open item |
| `POST /api/ux-fixes/next` | `{"session_id": "<id>", "close_number": 12}` | Close item 12, claim the next |
| `POST /api/ux-fixes/update` | `{"number": 12, "status": "closed", "session_id": "<id>"}` | Set status (`open` / `in_progress` / `closed`) |
| `GET  /api/ux-fixes/list` | — | Inspect the whole queue |

Each item's `text` field is the complete implementation prompt — the worker
just claims, reads, implements, closes, repeats.

## Step-by-step deployment

1. **Install CCC** on the machine where you develop:
   see the [Quickstart](../README.md#quickstart). Verify it answers:
   `curl -s http://127.0.0.1:8090/api/ux-fixes/list`
2. **Implement the integration in your app** — hand the prompt below to Claude
   Code in your project's repo and let it do steps 3–4 for you.
3. **Add the Annotate overlay** (dev-only floating button → click element →
   note → submit) and a server route that formats the prompt and enqueues it.
4. **Smoke-test**: run your app, submit a test annotation, and confirm it shows
   up in `GET /api/ux-fixes/list` with the right project ref.
5. **Start a worker**: open a Claude Code session in your repo (name it
   something like `UX-fixes-queue`) and tell it to drain the queue using the
   worker-loop endpoints above. In CCC's dashboard you'll see it claim items
   as they arrive.

## The prompt

Paste this into Claude Code inside **your app's repo** (fill in the
ALL-CAPS placeholders):

```text
Add an "Annotate → UX-fixes queue" dev tool to this project, integrated with
Claude Command Center (CCC).

## Background
CCC (github.com/amirfish1/claude-command-center) is a local dashboard that runs
on http://127.0.0.1:8090. It owns a durable, numbered UX-fixes queue
(~/.claude/command-center/ux-fixes-queue.json) and exposes HTTP endpoints for
it. Read that repo first — the authoritative reference is:
- cookbook/annotate-to-ux-fixes-queue.md (this recipe: contract + payloads)
- watchtower/queue.py (queue semantics: numbered items, per-project refs like
  "MYAPP-3", statuses open|in_progress|closed, claim/next workflow — a hard
  dependency of CCC, see github.com/amirfish1/watchtower)
- server.py — search for "/api/annotations/ux-fixes-queue", "/api/ux-fixes/claim",
  "/api/ux-fixes/next", "/api/ux-fixes/update", "/api/ux-fixes/list"

Prerequisite: CCC is installed and running locally on port 8090.

## What to build in THIS project

1. A dev-only "Annotate" UI affordance (small floating button, rendered only on
   localhost / development builds). Clicking it lets me click any element on
   the page, type a note, and submit. Capture: the note, page URL, document
   title, a CSS selector for the clicked element, element tag/id/role/text,
   bounding rect, and viewport size + scroll.

2. A server route (e.g. POST /api/dev/annotate) that:
   - is gated to dev/localhost (and auth if the app has it),
   - formats the annotation into a human-readable prompt for a coding agent:
       "UI annotation from APP_NAME (Annotate dev tool)" header,
       the user's note, then an "Anchors:" block (URL, title, selector,
       element, rect, viewport), ending with
       "Please inspect this repo and implement the requested UI fix.",
   - then POSTs JSON to http://127.0.0.1:8090/api/annotations/ux-fixes-queue
     (base URL overridable via env var, e.g. ANNOTATE_CCC_URL):
       {
         "text":  "<the full formatted prompt>",
         "note":  "<raw note>",
         "url": "...", "title": "...", "selector": "...",
         "screenshot_path": "",
         "repo_path": "<ABSOLUTE PATH TO THIS REPO'S ROOT ON DISK>",
         "source": "APP_SLUG"
       }
     with a ~10s timeout. No auth/Origin header needed — CCC trusts loopback,
     and a server-to-server fetch sends no Origin so it passes CCC's CSRF gate.
   - IMPORTANT: repo_path drives project routing on the CCC side. The repo
     directory's basename becomes the project code, so items get refs like
     "MYAPP-1", "MYAPP-2". Omit it and items misfile under a generic project.
   - On success CCC returns {ok:true, number, item:{ref, project, ...}} — show
     the ref back to me in the UI ("Queued as MYAPP-4").
   - If CCC is unreachable, do NOT drop the annotation: return the formatted
     prompt in the response so I can paste it manually.

3. Document the worker loop in the project README or CLAUDE.md: a Claude Code
   session drains the queue by calling CCC:
   - POST /api/ux-fixes/claim  {"session_id": "<id>"}            → claim oldest open item
   - POST /api/ux-fixes/next   {"session_id": "...", "close_number": N} → close N, claim next
   - POST /api/ux-fixes/update {"number": N, "status": "closed", "session_id": "..."}
   - GET  /api/ux-fixes/list                                      → inspect the queue
   Each item's "text" field is the full implementation prompt.

## Constraints
- The queue file, numbering, locking, and claim semantics live entirely in CCC.
  Do NOT reimplement or write the queue JSON directly — only call the HTTP API.
- The annotate tool must be unreachable in production builds.
- Match this codebase's existing conventions for routes and components.

Verify by running the app, submitting a test annotation, and confirming it
appears in GET http://127.0.0.1:8090/api/ux-fixes/list with the right project ref.
```

## Optional extras

- **Screenshots** — on macOS your server route can shell out to
  `/usr/sbin/screencapture -i <path>` for an interactive region grab, then pass
  the saved path as `screenshot_path`. The worker session reads the image with
  its Read tool. Skip this on first pass; it's strictly additive.
- **Express lane** — items accept a `lane` field (`normal` | `express`) for
  future priority routing.
- **Inject mode** — passing `"inject": true` to the enqueue endpoint falls back
  to the old behavior (interrupt the named session immediately). Useful for
  urgent one-offs, not the default.
