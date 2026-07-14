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

Three explicit states, orthogonal from pinning:

```
Active  <-- Archive/Move to Active -->  Archived  <-- Trash/Untrash -->  Trashed
```

- **Active**: shows in the Active tab.
- **Archived**: out of the Active tab, still visible in the All tab's main
  list.
- **Trashed**: out of the Active tab, folded into the collapsed Trash bucket
  at the bottom of the All tab.

Untrashing goes to Archived, never directly to Active. The All tab does allow
an Active row to move directly to Trashed because its lifecycle action is
Trash, not Archive; the server establishes the required `archived=true`
invariant as part of that transition.

`pinned` is fully orthogonal: it only affects sort rank within the Active tab
or the All tab's main list. Pin is not offered inside Trash. Trashing or
archiving a row does not mutate its stored pin value.

## Canonical view and action matrix

The visible tab and bucket are part of the action contract. Row actions cannot
be selected from lifecycle state alone.

| Location | State shown | Lifecycle actions | Pin |
|---|---|---|---|
| Active tab | Active | Archive | Yes |
| All main list | Active | Trash | Yes |
| All main list | Archived | Move to Active; Trash | Yes |
| All / Trash bucket | Trashed | Untrash to Archived | No |

The Active tab contains only Active rows. The All tab contains all three
states: Active and Archived share the main list, while Trashed rows appear in
the separate Trash section at the bottom.

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
- Group chats: new `trashed: bool` field in the per-chat metadata store,
  written by a new `_group_chat_set_trashed()` mirroring
  `_group_chat_set_archived()`. `trashed_at` may also be stamped for ordering
  and diagnostics, but the boolean is the lifecycle source of truth.
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
  Active/Archived→Trashed and Trashed→Archived. Trashing an Active row sets
  `archived=true` in the same server operation. Setting `archived=false` via
  the archive endpoint on a trashed row clears `trashed` server-side too.
- `/api/group-chats/trash` and `/api/group-chats/untrash` (new) — mirror the
  existing group-chat archive/unarchive endpoints and accept the same
  `{path, id}` reference payload.
- `trashed` field added to conversation and group-chat row payloads
  (additive field).

## UI

### Buttons (static/app.js, `_renderRow`)

`_renderRow` must receive or derive an explicit rendering context (`active`,
`all-main`, or `trash`). It must not infer the action set only from
`c.archived` / `c.trashed`.

- **Active tab / Active row**: Pin + Archive (📥).
- **All main / Active row**: Pin + Trash (🗑). There is no Archive action in
  this location.
- **All main / Archived row**: Pin + Move to Active (↩) + Trash (🗑).
- **Trash / Trashed row**: Untrash (↩) only; it moves to Archived and the row
  returns to the All main list. Pin is absent.

Each lifecycle action appears once in the DOM. Archived and Trashed rows do
not render a second rest-state Restore button in addition to the hover action
bar. Tooltips and `aria-label`s use the same verbs as this matrix.

### Bucketing (static/app.js ~24580-24680)

Replace the `pinned`-based split with a `trashed`-based split:

```js
const _trashConvs = _archivedConvs.filter(c => c.trashed);
const _archivedVisibleConvs = _archivedConvs.filter(c => !c.trashed);
const _allTabConvs = _sessionConvs.concat(_openAskConvs, _readyToMergeConvs, _archivedVisibleConvs);
```

`pinned` no longer participates in this split. It affects sort rank in Active
and All main only; Trash neither exposes pin controls nor promotes pinned rows
out of the bucket.

### Kanban classification

`classifyKanbanColumn` (static/app.js:19936, `if (c.archived) return
'archived'`) needs **no change** — a trashed row has `archived=true` too, so
it's already excluded from Active.

## Testing / verification

- `tests/test_smoke.py` unaffected at the import level; add a smoke-level
  assertion that the new trash sidecar file loads/saves round-trip cleanly
  (mirrors existing archived-file test if one exists).
- Add static contract tests for the complete location × state action matrix,
  including absence checks (no Archive on an Active row in All, no Pin in
  Trash, and no duplicate Restore/Untrash buttons).
- Add server tests for the invariants `trashed ⇒ archived`, Active→Trashed in
  one request, Trashed→Archived on untrash, and clearing `trashed` when moving
  to Active.
- Puppeteer check: exercise the full round trip in both tabs using the repo's
  harness: Active tab Archive; All main Move to Active; All main Active Trash;
  Trash Untrash to Archived. Verify the row lands in the correct list after
  every transition and exposes exactly the expected action set.
