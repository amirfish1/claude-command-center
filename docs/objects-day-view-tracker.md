# Objects-as-day-view — tracker

Turning the existing **Objects** feature (first-class containers that own session
rows, born in Flow, surfaced in the conversation list) into something you can
arrange each morning to reflect "what I have to do today."

**Origin:** brainstorming session 2026-06-23. The grouping already works
(objects own rows via `flowNodeParents`; empty objects render — CCC-92). The
gaps below are what stop it from feeling like a real day tracker.

## Issues at a glance

| ID | Title | Problem (one line) | Value | Conf. | Status |
|----|-------|--------------------|:-----:|:-----:|--------|
| **GOAL-1** | Object status + immediate objective | Object shows only `title + count` — no "where are we / what's the immediate target", and the already-parsed session outcome (`DID`/`NEXT_STEP`) is never shown on the row | H | M | `done`¹ |
| **GOAL-2** | Sessionless tasks under objects | Sessionless admin (billing, ads) had no home — only "drag sessions here". Now each object has a `+ task` adding a draft-session (editable, with play-to-spawn) | M | H | `done`³ |
| **GOAL-3** | Persistent storage for the organization | Object defs + parent links live in browser localStorage — lost on clear, not cross-machine, invisible to the server (blocks any automation) | M/H | M | `done` |
| **GOAL-4** | Create objects + assign sessions via the CCC API | UI drag already works; there's no programmatic `/api/*` way to create an object or parent a session under it — blocks agents/automation arranging the day. Depends on GOAL-3 (server-side state) | H | M | `done`² |

Legend — Status: `todo` · `exploring` · `building` · `done`. Value / Confidence: L/M/H.

¹ GOAL-1 shipped **manual-first** (commits `3108592` row DID/next line, `5e9b8d1`
object status chip + objective). Open follow-up: auto-roll the object's
status/objective from its child sessions' `session_state` instead of by hand.
³ GOAL-2 done (commits `cdb92ec`, `9185b47`): draft-session tasks under objects
in the conv list with play-to-spawn, reusing Flow draft infra. Open follow-ups:
(a) draft tasks persist to `ccc-flow-draft-sessions` localStorage only — not yet
mirrored to the server like objects/parents are; (b) tasks require a resolvable
repo (auto-inferred from the object's sessions / any known repo) — a truly
repo-less reminder is a later change to the Flow draft model.

² GOAL-4 done. Server side: `objects_store.py` + `/api/objects/*`
(`docs/objects-api.md`). Client side (commit `204a729`): the browser mirrors
objects/parents/order to the API on every change (debounced reconcile — import
upserts, a server-diff drives delete/unassign) and bootstrap-unions from the
server on load. The day's organization now persists to `objects.json`, durable
and readable by automation. Open follow-up: GOAL-1 auto-roll, and GOAL-2
sessionless-task copy.
Suggested order: **GOAL-2 → GOAL-1 → GOAL-3 → GOAL-4** (fit sessionless tasks →
status/objective → persist server-side → API on top of it). GOAL-3 + GOAL-4 are
one server-side effort: the API (GOAL-4) is only meaningful once state is durable
(GOAL-3). Reorder once arranging a real day reveals the pain.

---

## Details

### GOAL-1 — Object status + immediate objective
Two missing renders:
- **Object header:** a status + a one-line "immediate objective."
- **Session row:** what it achieved / its next step. Raw material already exists
  and is thrown away — the server parses `<session-state>` into
  `c.session_state = {did, insight, next_step_user}` but renders it only in the
  transcript pane, never on the sidebar row. DID = achieved; NEXT_STEP_USER = next.

**Punted sub-decision:** where the object's status + objective come from —
(a) auto-rolled from child sessions' `session_state`, (b) hybrid auto+override,
or (c) manual. Decide from the gut *after* GOAL-4 lets us arrange a real day.
Maintenance burden is the risk that killed earlier attempts; lean auto.

Seams: `_renderObjGroup` / `_folderGroupHeaderHtml` (object header, ~app.js:18726),
`_renderRow` (~app.js:18211), `_parse_session_state` (`server.py`). Object shape:
`ccc-flow-custom-objects` localStorage — `id,title,created_at,updated_at`, no
status/objective field yet.

### GOAL-2 — Empty-object copy for sessionless tasks
Half a real day is sessionless admin that never gets a session. The existing
`draft-session` node kind (Flow's "not-yet-started" placeholder) is the natural
home under an object; the empty-state copy/affordance should say "add a task,"
not only "drag a session." Small, localized change (`_renderObjGroup` empty
branch, `conv-object-empty-hint`).

### GOAL-3 — Persistent storage for the organization
State lives in localStorage: `ccc-flow-custom-objects`, `ccc-flow-node-parents`,
`ccc-flow-node-positions`, `ccc-objects-order`, `ccc-flow-collapsed-nodes`. Move
to durable server-side storage with an `/api/*` surface (the way COO tracking
mirrors to `coo-notes.json`). Enables cross-surface use + server-side daily
routines/notifications reading your day. Adds an API contract + migration from
existing localStorage; respect the same-origin POST guard.

### GOAL-4 — Create objects + assign sessions via the CCC API
The in-list UI drag already exists (create object inline, drop a session under
it). What's missing is the **programmatic** path: `/api/*` endpoints to create an
object and to parent/unparent a session under it. Enables an agent, a daily
routine, or another surface to arrange the day without a human dragging — e.g.
"every morning, group today's live sessions under their objects." Depends on
GOAL-3: the API mutates server-side state, so it's only meaningful once the
organization is persisted off localStorage. Respect the `/api/*` contract rules
and the same-origin POST guard (see CLAUDE.md § API contracts).

---

## Notes
- Keep personal/day content (specific goals, client names) out of this file —
  public OSS repo.
