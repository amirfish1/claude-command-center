# Kanban column rules

Every code path that moves a card between columns. Confusion usually comes
from forgetting that the kanban classifier runs *every* poll (~10s) — most
"transitions" aren't moves, they're re-classifications of the same card based
on signals that changed underneath it.

For the visual version, see [`kanban-rules.html`](kanban-rules.html).

## The columns

Defined in [`static/index.html:4497-4516`](../static/index.html). In display
order:

| Key | Label | Meaning |
|---|---|---|
| `backlog` | Backlog | Open GitHub issue or `TODO.md` / `PARKING_LOT.md` item — no session yet |
| `needs-attention` | Needs attention | Open issue with the `needs-attention` GitHub label |
| `icebox` | Icebox | Parked. The `icebox` GitHub label is set, or you dragged it here. Active intent: "don't work on this right now" |
| `working` | Working | Live session — Claude is running. Glows when mid-turn |
| `review` | Review | Committed or pushed work, waiting for human verification |
| `testing` | In Testing | Manually moved here. No automatic routing in or out |
| `verified` | Verified | Marked done, or the linked GitHub issue closed as `COMPLETED` |
| `archived` | Archived | Dismissed, or issue closed for any non-completed reason |
| `inactive` | Inactive | Dead session that never used a tool — graveyard / resumable shelf |

> **What changed (planning → icebox refactor).** The old `planning` column conflated two
> unrelated states: a transient "live but no tool fired yet" pre-window, and a long-lived
> "parked by user" intent. The transient state was always on its way to `working` within
> seconds, so it didn't earn a column. We kept the meaningful half (parked) and renamed
> the column to `icebox` to match the GitHub label that drives it. A small in-card badge
> can show "fresh / no tools yet" inside the Working column when that signal is useful.

## The classifier

There is exactly one function that decides which column a card belongs to:
`classifyKanbanColumn` ([`static/index.html:4360`](../static/index.html)). It
runs on every render, for every card. **Cards don't have a stored column
field — the column is recomputed each tick from signals.**

Rules are checked in this order; the first match wins:

```
1. Fresh-session sticky (first ~60s after spawn)        ─ kept as a safety net for flicker
2. Manual override from drag-drop  (localStorage)       ─ unless stale, see below
3. verified flag                                         → verified
4. archived flag                                         → archived
5. source === 'backlog':
     issue CLOSED + reason=COMPLETED   → verified
     issue CLOSED + any other reason   → archived
     label needs-attention             → needs-attention
     label icebox                      → icebox
     otherwise                         → backlog
6. label icebox  (issue or linked session)              → icebox
       ── this rule sits ABOVE the live+sidecar block: the explicit human
          "park this" signal wins over implicit liveness.
7. live + sidecar present                               → working
       ── waiting / has_writes / pre-tool: all working.
8. stage === 'pushed' OR 'committed'                    → review
9. !live + 'coding' + last=assistant                    → review   (dormant w/ unsaved edits)
10. label needs-attention                               → needs-attention
11. label claude-in-progress                            → working
12. !live + no sidecar                                  → inactive
13. live + pkood (any state)                            → working
14. live (any other)                                    → working
15. fallback                                            → inactive
```

The `testing` column is **never** assigned by the classifier. The only way
into it is a manual drag-drop, and the only way out is another manual move.

### Live + icebox tiebreak

Rule 6 is the deliberate tiebreak: a card with the `icebox` label AND a live
process lands in `icebox`, not `working`. This happens when you spawn a
session against an already-iceboxed issue, or when someone adds the `icebox`
label mid-flight via `gh issue edit` or the issue page. The explicit "park"
signal beats implicit liveness — to actually work on the card, remove the
label or drag the card out.

## Two trigger categories

Every transition is one of:

- **Explicit** — a human clicked a button or dragged a card. The UI either
  writes a `columnOverrides[sid]` entry to localStorage (rule 2) or persists
  a `verified` / `archived` flag to the server (rules 3–4) and lets the next
  classifier pass land the card in the new column.
- **Implicit** — a signal under the card changed. A hook updated the sidecar,
  the GitHub poll saw a label or close reason flip, a commit landed, the
  Claude process died. Nothing "moved" the card; the next classifier pass
  reads the new signals and lands on a different column.

The `verified` and `archived` server flags are *mechanism* — explicit
actions persist them so the choice survives a reload, and they may also
auto-close a linked GitHub issue. They aren't a third trigger type.

## Triggers — explicit (user actions)

These are the only ways a *user* causes a column change.

### Drag-and-drop a card to another column
- Code: `moveCardToColumn` [`static/index.html:4243-4358`](../static/index.html)
- Writes a `columnOverrides[session_id]` entry to localStorage (`ccc-column-overrides`)
- For target = `verified` → also `POST /api/conversations/{id}/verify {verified: true}`
- For target = `archived` → also `POST /api/conversations/{id}/archive {archived: true}`
- For target = `working` (from backlog) → also `POST /api/issues/{n}/mark-in-progress`
- For target = `icebox` → also `POST /api/issues/{n}/mark-icebox` (adds the `icebox` label)
- Dragging *out of* `icebox` removes the `icebox` label
- Dragging *out of* verified/archived clears that flag on the server

### Click "Verify" button
- On a card or in the Needs-Your-Attention panel ([`static/index.html:3739`, `5134`](../static/index.html))
- → `POST /api/conversations/{id}/verify {verified: true}` → column becomes `verified`
- **Side effect:** if a GitHub issue is linked, the server auto-closes it as
  `completed` and posts the latest commit SHA as a comment
  ([`server.py:6498-6537`](../server.py))

### Click "Archive" button
- → `POST /api/conversations/{id}/archive {archived: true}` → column becomes `archived`
- **Side effect for backlog cards:** closes the GitHub issue with
  reason `not planned` ([`server.py:6408-6432`](../server.py))

### "Start session" on a backlog card
- [`static/index.html:5482-5523`](../static/index.html)
- Spawns a Claude session linked to the issue. Card moves `backlog → working`
  on the next poll because a live session now exists.

### Drag a column header to reorder
- [`static/index.html:4865-4898`](../static/index.html)
- **Does not move cards.** Persists view order to `ccc-column-order` only.

## Triggers — implicit (automatic re-classification)

No code "moves" the card here. The signals change and the next render of
`classifyKanbanColumn` returns a different column.

### Claude Code `PostToolUse` / `Stop` hook fires
- Hooks: [`hooks/post-tool-use.py`](../hooks/post-tool-use.py), [`hooks/stop.py`](../hooks/stop.py)
- Writes `~/.claude/command-center/live-state/<sid>.json` with sidecar fields
  (`sidecar_status`, `sidecar_has_writes`, `tool`, `file`, `timestamp`)
- **No column change** — both pre-tool and post-tool live sessions land in
  `working` after the planning→icebox refactor. The sidecar still drives the
  in-card "mid-turn" glow and status badge, just not column membership.

### Claude session exits (process dies)
- Detected by `ps -A` mismatch in `/api/sessions`
- `is_live` flips to false. Sidecar file remains.
- Effect depends on stage:
  - has commits or edits + last was assistant → `review` (rules 8–9)
  - nothing meaningful happened → `inactive` (rule 12)

### Git commit / push lands
- Detected by `sessionStage()` reading `git log` ([`server.py`](../server.py))
- Effect: `working → review` once the session is no longer live (rule 8)
- A *live* session stays in `working` even after committing, because rule 7
  matches first. The transition happens when the process exits.

### GitHub issue state poll (5-min cache)
- `/api/sessions` enriches each card with `gh_state`, `gh_labels`,
  `issue_state_reason`
- Backlog cards re-route on issue close:
  - closed as `COMPLETED` → `verified` (rule 5)
  - closed as `NOT_PLANNED` / `DUPLICATE` / unspecified → `archived` (rule 5)
- Any card re-routes on label change:
  - `icebox` added → `icebox` (rule 6) — overrides liveness
  - `icebox` removed → drops back to natural classification (typically `working`)
  - `needs-attention` added/removed (rules 5, 10)
  - `claude-in-progress` added on a dead session → `working` (rule 11)

### Fresh-session sticky window expires
- [`_applyFreshSessionSticky` `static/index.html:4371`](../static/index.html)
- For 60s after a session is first observed, its initial column is pinned
  to suppress flicker while signals settle
- Released early if real progress fires (`has_edit`, `has_commit`,
  `has_push`, or `last_event_type` change)
- Manual overrides, `verified`, `archived`, `backlog`, and `needs-attention`
  bypass the sticky and route immediately
- After the planning→icebox refactor the sticky is mostly a safety net —
  the old Planning↔Review bounce it was written to suppress can no longer
  happen. Worth deleting once the new model has bedded in.

### Stale manual override auto-clear
- [`static/index.html:4407-4427`](../static/index.html)
- On every render, an override is dropped if:
  - card is now `verified` or `archived` (the server flag wins), OR
  - override was `working`/`icebox` AND stage is now `pushed` OR linked
    issue is `CLOSED`, OR
  - override was `archived` for a backlog card whose issue is now `OPEN`
- Idle alone never clears — user intent is respected for parked work

### pkood agent status changes
- For `source: 'pkood'` sessions, the agent is treated as live — any pkood
  status routes to `working` (rule 13)

## Aliases that confuse

These are the most common sources of "wait, why is it in that column?"

| Sounds the same | Actually | Where it lives |
|---|---|---|
| `waiting` | Sidecar status (between Claude turns) — routes to `working`, not its own column | sidecar JSON |
| `blocked` | Internal "needs your attention" classification kind — *not* a kanban column | `_classify_attention` in server.py |
| `needs-attention` | Real kanban column. Driven by GitHub label of the same name | column |
| `icebox` | Column name *and* GitHub label name — they match. Adding the label routes the card to the column; removing it sends the card back (typically to `working` or `backlog`) | both |
| `claude-in-progress` | GitHub label that *routes a dead session into* `working`. Says "this is the active work" | label |
| `verified` | Card flag stored server-side. Closes linked GH issue as completed | server flag |
| `archived` | Card flag stored server-side. Closes linked GH issue as not-planned | server flag |
| `testing` | Manual-only column. Nothing routes in or out automatically | column |

## API endpoints that change column membership

| Endpoint | Effect | File |
|---|---|---|
| `POST /api/conversations/{id}/verify` | sets/clears verified flag | [`server.py:6466`](../server.py) |
| `POST /api/conversations/{id}/archive` | sets/clears archived flag, may close GH issue | [`server.py:6398`](../server.py) |
| `POST /api/conversations/{id}/create-issue` | links a new GH issue | [`server.py:6541`](../server.py) |
| `POST /api/conversations/{id}/link-issue` | links an existing GH issue | [`server.py:6558`](../server.py) |
| `POST /api/issues/{n}/mark-in-progress` | adds `claude-in-progress`, reopens issue | [`server.py:4729`](../server.py) |
| `POST /api/issues/{n}/mark-icebox` | adds `icebox`, removes `claude-in-progress` | [`server.py:4783`](../server.py) |
| `GET  /api/sessions` | enriches with GH state — drives implicit re-routing | [`server.py:5544`](../server.py) |

## Refresh cadence

- `/api/sessions` polled every **10 s** by the browser
- GitHub state cached **5 min** server-side (per issue)
- Sidecar updates: **real-time** via hooks
- localStorage overrides: **immediate**

## Migration checklist (planning → icebox)

These docs already reflect the new model. The code change itself is pending —
checklist for the implementation pass:

1. **Rename the column** in [`static/index.html:4497-4516`](../static/index.html):
   - Drop the `planning` entry.
   - Insert an `icebox` entry where `planning` was, with hint *"Parked.
     `icebox` GitHub label is set, or you dragged it here."*
   - Update the `working` hint: *"Live session — Claude is running. Glows when mid-turn."*
2. **Reorder / rewrite `classifyKanbanColumn`** in
   [`static/index.html:4360`](../static/index.html):
   - Move the `label icebox` check **above** the `live + sidecar` block
     (rule 6 above rule 7) so the live + icebox tiebreak resolves to icebox.
   - In rule 6 (live + sidecar) drop the `else: planning` branch — return
     `working` for any live + sidecar case.
   - In rule 14 (live + JSONL only) drop the `else: planning` branch — return
     `working` as the live default.
   - Replace every remaining `'planning'` literal with `'icebox'`.
3. **Migrate stale localStorage on first load** — in `_classifyKanbanColumnNatural`'s
   override-staleness check (around `static/index.html:4407`), treat
   `columnOverrides[sid] === 'planning'` as stale and drop it. One-time cleanup
   for users coming from the old build.
4. **In-card "fresh / pre-tool" badge (optional, recovers the lost signal):**
   if a session has `is_live && !sidecar_has_writes && session_age < 30s`,
   render a small "planning…" badge inside the working-column card. No new
   column, just a visual cue.
5. **Drop the fresh-session sticky** ([`_applyFreshSessionSticky`](../static/index.html))
   once the new model has bedded in. The Planning↔Review bounce it was
   written to suppress can no longer happen.
