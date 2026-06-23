# Objects-as-day-view — tracker

Turning the existing **Objects** feature (first-class containers that own session
rows, born in Flow, surfaced in the conversation list) into something you can
arrange each morning to reflect "what I have to do today."

**Origin:** brainstorming session 2026-06-23. The grouping already works
(objects own rows via `flowNodeParents`; empty objects render — CCC-92). The
gaps below are what stop it from feeling like a real day tracker.

Status legend: `todo` · `exploring` · `building` · `done`. Each item uses the
Flow proposal format: **problem / tradeoff / value (L/M/H) / confidence (L/M/H)**.

---

## 1. Object status + immediate objective  — `todo`

**Problem:** An object shows only `title + count`. It doesn't say *where we are*
or *what we're trying to achieve right now*. A pile of sessions under a header is
not a tracker. Two missing renders:
  - **On the object header:** a status + a one-line "immediate objective."
  - **On each session row:** what that session achieved / its next step. The raw
    material already exists and is thrown away — the server already parses the
    `<session-state>` block into `c.session_state = {did, insight, next_step_user}`
    but only renders it in the transcript pane, never on the sidebar row
    (`_renderRow`). DID = what it achieved; NEXT_STEP_USER = the immediate next.

**Punted sub-decision:** where the object's status + objective come from —
(a) auto-rolled from child sessions' `session_state`, (b) hybrid auto+override,
or (c) manual. Decide from the gut *after* item 4 lets us arrange a real day.
Maintenance burden is the risk that killed earlier attempts; lean auto.

- Tradeoff: auto = zero upkeep but less precise; manual = precise but the upkeep
  that historically kills these views.
- Value: **H** — this is the actual point of the feature.
- Confidence: **M** — render is easy; the source-of-truth decision is open.

Seams: `_renderObjGroup` / `_folderGroupHeaderHtml` (object header, ~app.js:18726),
`_renderRow` (~app.js:18211), `session_state` parsed in `server.py`
(`_parse_session_state`). Object shape: `ccc-flow-custom-objects` localStorage,
fields `id,title,created_at,updated_at` — no status/objective field yet.

## 2. Empty-object copy for sessionless tasks  — `todo`

**Problem:** Half a real day is sessionless admin (billing, ads) that never gets
a session. As objects they render empty with *"Empty — drag sessions here"* —
wrong message for a standalone to-do. The existing `draft-session` node kind
(Flow's "not-yet-started" placeholder) is the natural home for these tasks under
an object; the empty-state copy and affordance should reflect "add a task," not
only "drag a session."

- Tradeoff: reusing `draft-session` avoids a new concept but inherits its UX.
- Value: **M** — unblocks sessionless half of the day.
- Confidence: **H** — small, localized copy/affordance change (`_renderObjGroup`
  empty branch: `conv-object-empty-hint`).

## 3. Persistent storage for the object organization  — `todo`

**Problem:** Object definitions and parent links live in browser localStorage
(`ccc-flow-custom-objects`, `ccc-flow-node-parents`, `ccc-flow-node-positions`,
`ccc-objects-order`, `ccc-flow-collapsed-nodes`). That means: lost on browser
clear, not shared across machines/popout windows, not available to the server
(so no server-side daily routine / notification can read your day). Move the
organization to durable server-side storage with an `/api/*` surface, the way
COO tracking already mirrors to `coo-notes.json`.

- Tradeoff: server persistence enables cross-surface + automation, but adds an
  API contract and a migration from existing localStorage state.
- Value: **M/H** — prerequisite for any server-side "act on my day" feature.
- Confidence: **M** — pattern exists (`coo-notes.json`), but it's a real
  client→server state move with migration + same-origin POST guard.

## 4. Add objects + assign sessions to objects via the CCC interface  — `todo`

**Problem:** Today objects are created/parented largely through Flow drag
mechanics. Make it first-class in the main conversation list: create an object
inline, and drop / assign a session under an object directly from the row list,
without going to Flow. This is what makes the morning-arrange loop fast enough to
actually use.

- Tradeoff: more affordances in the already-dense conv list; must stay simple
  (the simplicity bar is the whole reason past attempts failed).
- Value: **H** — without fast in-list arranging, the view won't get used.
- Confidence: **M** — drag/drop + create UI in a busy list needs care.

---

## Notes
- Sequencing intuition: arrange a real day with what exists → **4** (make
  arranging fast) → **2** (sessionless tasks fit) → **1** (status/objective) →
  **3** (persist + enable automation). Reorder once arranging reveals the pain.
- Keep personal/day content (specific goals, client names) out of this file —
  public OSS repo.
