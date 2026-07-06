# Tri-state session lifecycle: Active / Archived / Trashed

## Problem

CCC has one persisted boolean, `archived`, for "this conversation is out of
the way." Whether an archived row still shows in the All tab's main list or
folds into the collapsed Trash bucket at the bottom is decided today by an
unrelated flag, `pinned` (static/app.js:24584-24585) — a pin-to-top feature
that was never meant to carry this meaning. Effects: pinning a row and later
archiving it silently "escapes" the Trash bucket; there's no explicit Trash
action or button, just archive-then-accidentally-visible-or-hidden depending
on pin state; the row-level "needs you" dot could show on a row buried in
Trash with no path back to Active in view (CCC-499, fixed separately).

## Goal

Three explicit, orthogonal-from-pin states, one step at a time:

```
Active  <-- Archive/Unarchive -->  Archived  <-- Trash/Restore -->  Trashed
```

- **Active**: shows in the Active tab.
- **Archived**: out of the Active tab, still visible in the All tab's main
  list.
- **Trashed**: out of the Active tab, folded into the collapsed Trash bucket
  at the bottom of the All tab.

Restoring from Trashed goes to Archived (one rung up), not straight to
Active — matches the ladder, no skipping.

`pinned` is fully orthogonal: it only affects sort rank within whichever
list a row lands in. Trashing or archiving a row does not touch its pin.

## Scope

Applies uniformly to session/conversation rows and group chats. GH-issue /
backlog rows are explicitly **excluded** from Trashed — their "Archived"
already means "closed on GitHub as not-planned" (GitHub-side truth, not a
local flag); they stay Active/Archived-only, unchanged.

## Data model

Two flags, not a single enum, to minimize blast radius against the ~20
existing `c.archived` / `sid in archived_set` call sites in server.py that
only care about "is this out of Active" (they're correct unchanged, since
`archived` still means exactly that):

- `archived: bool` — unchanged meaning and unchanged call sites.
- `trashed: bool` — new, subset semantics (`trashed` implies `archived`).
  Server enforces the subset invariant on every write: setting `archived`
  false always clears `trashed` too (a row can't be trashed while active).

### Persistence

- Sessions: new flat-list sidecar file `TRASHED_CONVERSATIONS_FILE`,
  structurally identical to the existing `ARCHIVED_CONVERSATIONS_FILE`
  (`_load_archived_conversations` / `_save_archived_conversations` pattern).
- Group chats: new `trashed_at` field next to the existing `archived_at` in
  the per-chat metadata store, written by a new `_group_chat_set_trashed()`
  mirroring `_group_chat_set_archived()` (server.py:34240).
- Backlog/GH-issue rows: no change.

### Migration

None. Ships with the trashed set/field empty. Every currently-archived row
(pinned or not) renders as "Archived, visible in All's main list" the moment
this ships — previously-hidden unpinned-archived rows will resurface into
the main list until someone manually trashes them. Explicitly accepted:
no migration script, owner will manage the one-time cleanup by hand.

## API

Additive only — no changes to existing endpoint contracts (`/api/*` is the
stable surface per this repo's CLAUDE.md; renaming/removing/reshaping is a
breaking change, adding is not):

- `/api/conversations/<id>/archive` (existing, unchanged) — Active⇄Archived.
- `/api/conversations/<id>/trash` (new, POST `{trashed: bool}`) —
  Archived⇄Trashed. Setting `archived=false` via the archive endpoint on a
  trashed row clears `trashed` server-side too.
- `/api/group-chats/<path>/trash` (new) — mirrors the existing group-chat
  archive endpoint.
- `trashed` field added to conversation and group-chat row payloads
  (additive field).

## UI

### Buttons (static/app.js ~22645-22660, `_renderRow`)

- **Active row**: single Archive button (📥, unchanged).
- **Archived row** (not trashed): two buttons — Unarchive (↩, back to
  Active) + new Trash button (🗑, down to Trashed).
- **Trashed row**: single Restore button (↩, back to Archived — not
  Active).

### Bucketing (static/app.js ~24580-24680)

Replace the `pinned`-based split with a `trashed`-based split:

```js
const _trashConvs = _archivedConvs.filter(c => c.trashed);
const _archivedVisibleConvs = _archivedConvs.filter(c => !c.trashed);
const _allTabConvs = _sessionConvs.concat(_openAskConvs, _readyToMergeConvs, _archivedVisibleConvs);
```

`pinned` no longer participates in this split — only in sort rank within
whichever bucket a row ends up in.

### Kanban classification

`classifyKanbanColumn` (static/app.js:19936, `if (c.archived) return
'archived'`) needs **no change** — a trashed row has `archived=true` too, so
it's already excluded from Active.

## Testing / verification

- `tests/test_smoke.py` unaffected at the import level; add a smoke-level
  assertion that the new trash sidecar file loads/saves round-trip cleanly
  (mirrors existing archived-file test if one exists).
- Manual puppeteer check: one Active, one Archived, one Trashed row each
  show the correct button set and clicking each transitions to the
  expected next state (dev server, `node` script from repo dir per house
  convention).
