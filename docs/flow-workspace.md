# Flow workspace engineering notes

Status as of 2026-06-05: active planning and implementation notes for the
Flow workspace in CCC.

This document is the permanent home for Flow architecture notes, current
behavior, and near-term feature plans. It supersedes temporary session handoff
notes.

## Current implementation

Primary files:

| File | Responsibility |
|---|---|
| `static/index.html` | Mount point: `#flowBoard` inside the conversation-list panel. |
| `static/app.js` | Flow state, rendering, node drag/reparent, layout, popout behavior. |
| `static/app.css` | Flow board, node, edge, popout, and reader-resizer styling. |
| `server.py` | Repo/session APIs consumed by the wider dashboard. Flow is mostly client-side today. |

Flow renders as:

```text
#flowBoard.flow-board
  .flow-toolbar
  .flow-canvas
    .flow-world
      svg.flow-links
      .flow-node...
```

`renderFlowSidebar(convs)` builds one record per visible node, then rewrites the
board HTML. Pan/zoom state and scroll position are preserved around rewrites.

Current node kinds:

| Kind | Source | Current click behavior |
|---|---|---|
| `repo` | Visible sessions and draft sessions grouped by `repo_path`. | No reader yet. |
| `object` | `flowCustomObjects` in localStorage. | Double-click renames; no reader yet. |
| `group-chat` | Active group chat cache. | Opens group chat reader. |
| `session` | Conversation rows. | Opens the conversation reader. |
| `draft-session` | `flowDraftSessions` in localStorage. | Editable prompt; play starts a session. |

Client-side Flow state currently lives in localStorage:

| Key | Shape |
|---|---|
| `ccc-flow-node-positions` | `{ node_id: {x, y} }` |
| `ccc-flow-node-parents` | `{ child_node_id: parent_node_id }` |
| `ccc-flow-collapsed-nodes` | `{ node_id: true }` |
| `ccc-flow-custom-objects` | `[{ id, title, created_at, updated_at }]` |
| `ccc-flow-draft-sessions` | Draft session records. |
| `ccc-flow-pinned-sessions` | Session ids that stay visible in Flow. |
| `ccc-flow-zoom` | Number, clamped by `FLOW_ZOOM_MIN/MAX`. |
| `ccc-flow-popout-reader` | Whether the Flow popout shows the right reader pane. |
| `ccc-flow-reader-width` | Reader pane width in pixels. |

Layout is incremental and per cluster. The Organize action keeps each cluster
anchored to its current parent position, then greedily resolves overlapping
cluster bounding boxes by the minimum right/down push needed to separate them.
Re-running Organize on a clean board should move nothing.

Edges are SVG groups with a wide transparent hit path and a thin visible path.
Clicking selects an edge. Delete/Backspace removes the parent override. Dragging
an edge reparents the child.

Flow popout mode is enabled by `?ccc_popout=flow`. It forces the sidebar into
Flow, sets the document title to `Flow - CCC`, and hides normal dashboard
chrome. When the reader toggle is on, `.main` is shown as a right pane and the
draggable `.flow-reader-resizer` controls `--flow-reader-width`.

## Open items snapshot

Already shipped:

| Item | Status |
|---|---|
| Flow popout | Done. |
| Flow popout reader toggle | Done. |
| Draggable Flow popout reader resizer | Done. |
| Group chat nodes and drop-to-add-session | Done. |
| Selectable/deletable/drag-to-reparent edges | Done. |

Still relevant:

| Item | Notes |
|---|---|
| Repo/object reader panes | Planned below. |
| Flow visual identity | Planned below. |
| Multi-select for edges | Single-edge selection exists today. |
| Group chat nesting | Group chats are top-level only. |
| R10 overlap tie-break | Two stationary overlapping clusters still need a more user-aware tie-break. |
| Popout Annotate fallback | Current button delegates to the main dashboard button when available. |
| First-time Organize | Initial bin-pack layout can be smarter on untouched boards. |

## Feature 1: repo/object reader panes

### Fix proposal

Problem: Flow only opens rich context for session and group-chat nodes. Repo and
object nodes are visually important, but clicking them does not explain the
current goal, status, completed work, remaining work, active sessions, or open
items.

Tradeoff: A first-class repo/object reader needs a new persistence surface, new
server endpoints, and a small Markdown editing workflow. Keeping it file-backed
is simpler and inspectable, but it needs clear rules so automatic updates do
not overwrite manual notes.

Value: H

Confidence: M/H

### Decision

Add a first-class Flow node inspector in the conversation pane. Clicking a repo
or object opens an editable Markdown-backed status page for that node.

The first implementation should store Flow status files in CCC state, not in
the target repo:

```text
~/.claude/command-center/flow/
  index.json
  repos/
    <repo-key>.md
  objects/
    <object-id>.md
```

Reasons:

- It does not dirty arbitrary worktrees just because the user clicked a repo.
- It avoids accidentally committing private planning notes into public repos.
- It fits existing CCC state patterns in `server.py`.
- It still gives agents and users a real file path when needed.

Later, CCC can add an explicit "Move status file into repo" or "Export to repo"
action for teams that want the state committed.

### Node state file

Each node gets one Markdown file. CCC owns only clearly marked managed blocks;
the user owns everything else.

Initial template:

```markdown
# <node title>

## Flow fields

| Field | Value |
|---|---|
| Status | Active |
| Goal |  |
| Target date |  |
| ETA |  |
| Owner |  |
| Color seed | auto |

## Summary

Write the current state here.

<!-- ccc:auto:start status-table -->
## Current work

| Item | Status | Session | Updated | Notes |
|---|---|---|---|---|
<!-- ccc:auto:end status-table -->

<!-- ccc:auto:start open-items -->
## Open items

- None detected yet.
<!-- ccc:auto:end open-items -->

<!-- ccc:auto:start completed -->
## Completed

- None detected yet.
<!-- ccc:auto:end completed -->

## Manual notes

```

Rules:

- CCC may replace only content inside `ccc:auto:start/end` marker pairs.
- User edits outside markers must be preserved byte-for-byte where practical.
- Missing files are created lazily on first click, not during board render.
- Missing marker blocks are appended rather than inferred from arbitrary text.
- The visible `Flow fields` table is the editable metadata source for goal,
  status, target date, ETA, owner, and optional color seed.

### Derived data

The first version should generate managed blocks from existing deterministic
signals, without an AI summarizer:

| Source | Signal |
|---|---|
| Active sessions for the repo/object subtree | Title, status, branch, last activity, worktree marker. |
| Draft sessions under the node | Prompt, created/updated time. |
| `TODO.md` | Unchecked items already parsed by `server.py`. |
| `PARKING_LOT.md` | Heading/body backlog items already parsed by `server.py`. |
| GitHub issues | Existing backlog issue fetchers and issue-state helpers. |
| Group chats | Active chats that touch repo sessions. |
| Flow child objects | Child object titles and status fields. |

AI-generated prose can come later as an explicit "Summarize" action. The base
framework should be deterministic so it is fast, testable, and trustable.

### API plan

Add stdlib-only endpoints in `server.py`:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/flow/node` | GET | Load or lazily create a repo/object Markdown status file. |
| `/api/flow/node` | POST | Save user-edited Markdown with optimistic mtime checking. |
| `/api/flow/node/refresh` | POST | Rebuild managed blocks from current CCC signals. |
| `/api/flow/index` | GET | Return metadata for visible repo/object nodes, including parsed fields. |

GET `/api/flow/node` query:

```text
kind=repo|object
repo_path=<path, for repo and repo-scoped objects>
object_id=<id, for object>
title=<display title>
create=1
```

Response:

```json
{
  "ok": true,
  "kind": "repo",
  "node_id": "repo:<hash>",
  "title": "claude-command-center",
  "repo_path": "/path/to/repo",
  "state_path": "~/.claude/command-center/flow/repos/<repo-key>.md",
  "content": "...markdown...",
  "mtime": 1770000000.0,
  "fields": {
    "status": "Active",
    "goal": "",
    "target_date": "",
    "eta": "",
    "owner": "",
    "color_seed": "auto"
  },
  "derived": {
    "active_session_count": 3,
    "open_item_count": 4,
    "completed_count": 2
  }
}
```

POST `/api/flow/node` body:

```json
{
  "kind": "repo",
  "repo_path": "/path/to/repo",
  "object_id": "",
  "content": "...markdown...",
  "mtime": 1770000000.0
}
```

Save should fail with `409` if the file changed on disk since the reader loaded
it. The UI can then offer "Reload" and "Overwrite" without silently losing
edits.

### Frontend plan

Add a Flow inspector mode that mounts into the same conversation-pane surface
used by sessions and group-chat reader.

UI shape:

```text
Header:
  title, kind, repo path/object id, status chip, target/ETA chip

Body:
  View tab: rendered Markdown, managed sections visibly labeled
  Edit tab: textarea editor, Save, Refresh auto sections, Open file
```

Click behavior:

| Flow node | Behavior |
|---|---|
| Repo | Open repo Flow inspector. |
| Object | Open object Flow inspector. |
| Session | Keep current `selectConversation`. |
| Draft session | Keep current draft edit/play behavior. |
| Group chat | Keep current group-chat reader. |

The inspector should work in both the normal dashboard and the Flow popout
reader pane. In the popout, clicking repo/object should leave the board visible
on the left and mount the inspector on the right.

### Object model adjustment

Custom objects are localStorage-only today. For repo/object readers, server-side
state should become the durable source for object details:

```json
{
  "objects": [
    {
      "id": "obj-...",
      "title": "Release work",
      "repo_path": "",
      "created_at": 1770000000000,
      "updated_at": 1770000000000,
      "state_path": "~/.claude/command-center/flow/objects/obj-....md"
    }
  ]
}
```

Migration path:

1. Keep reading `ccc-flow-custom-objects` for compatibility.
2. On first server-backed Flow call, upsert those objects into
   `~/.claude/command-center/flow/index.json`.
3. Continue writing localStorage for one release as a cache.
4. Later, make `index.json` authoritative and leave localStorage for positions
   only.

### Update ownership

Who updates the Markdown:

| Actor | Scope |
|---|---|
| User | Edits all non-managed text and Flow fields. |
| CCC | Rewrites managed blocks on explicit Refresh and optionally on file open. |
| Agents | In first version, agents should not edit the file directly unless the prompt explicitly asks. |
| Hooks | Later version can append structured session events to CCC state for safer auto-updates. |

The first implementation should include a "Refresh auto sections" button rather
than silently rewriting on every poll. After trust is established, CCC can add a
preference for auto-refresh-on-open.

### Implementation slices

1. Server file helpers:
   - Add `FLOW_STATE_DIR = COMMAND_CENTER_STATE_DIR / "flow"`.
   - Add repo-key hashing and object-safe filename helpers.
   - Add template creation, marker-block replacement, and Flow field parsing.
   - Tests: helper-level smoke tests for path creation and marker preservation.

2. API endpoints:
   - Add GET/POST `/api/flow/node`.
   - Add POST `/api/flow/node/refresh`.
   - Enforce repo context with existing `resolve_repo_path`.
   - Clamp state paths under `FLOW_STATE_DIR`.

3. Frontend inspector:
   - Add `openFlowNodeInspector(node)` in `static/app.js`.
   - Add repo/object click branches in `wireFlowBoard`.
   - Add reader markup and CSS in existing single-file app style.
   - Preserve session and group-chat click behavior.

4. Flow metadata in node cards:
   - Fetch `/api/flow/index` opportunistically.
   - Show status, goal snippet, and ETA/date on repo/object cards.
   - Fall back to current meta when state has not been loaded.

5. Agent update loop, later:
   - Add a prompt snippet to spawned Flow draft sessions with the state file
     path and a rule to report final status.
   - Prefer hook-captured structured events over direct agent edits.

## Feature 2: Flow visual identity and automatic colors

### Fix proposal

Problem: Repo nodes and object nodes do not read as a coherent top-level
concept. Repo nodes share one color, object nodes share another, and the board
does not visually connect a parent work item to its sessions or metadata like
goal, ETA, or target date.

Tradeoff: Stronger color identity improves scanning, but colors cannot replace
status semantics. Session status colors already matter, so parent colors should
mark ownership/context while status remains visible.

Value: H

Confidence: H

### Decision

Treat repos and user-created objects as one visual category: work items.

Implementation terms can keep `kind: repo` and `kind: object`, but visual
styling should use a shared `.flow-node-work-item` class and a small subtype
label. A repo is a work item with a `repo_path`; an object is a work item without
one unless it is explicitly linked to a repo later.

### Automatic accent colors

Users should not pick colors in the first version. CCC should derive a stable
accent from a seed:

| Node | Seed |
|---|---|
| Repo | Canonical `repo_path`. |
| Object | `color_seed` Flow field if set, else object id. |
| Session | Parent work item accent, plus its own status class. |
| Draft session | Parent work item accent, plus draft styling. |

Use a fixed curated palette instead of arbitrary hue hashing. This keeps colors
distinct and avoids unreadable combinations in dark/light themes.

Example palette ids:

```text
blue, teal, green, amber, orange, red, pink, purple, indigo, slate
```

Each palette entry should provide CSS variable values:

```text
--flow-accent
--flow-accent-soft
--flow-accent-line
```

Node HTML can set:

```html
<div class="flow-node flow-node-work-item"
     style="--flow-accent: ...; --flow-accent-soft: ...;">
```

CSS then uses:

```css
.flow-node-work-item {
  border-left-color: var(--flow-accent);
  background:
    linear-gradient(var(--flow-accent-soft), var(--flow-accent-soft)),
    var(--surface);
}
```

### Preserve status semantics

Session nodes should not lose their current status colors. Instead:

- Keep session left border color from status (`working`, `waiting`, `review`,
  `failed`, etc.).
- Add parent accent through connector lines, a small top rule, or a subtle
  corner mark.
- Use parent accent for edge stroke where possible.
- Keep existing status chips as the strongest "what is happening" signal.

This gives the board two visual channels:

| Channel | Meaning |
|---|---|
| Parent accent | Which repo/object owns this work. |
| Session status color | What state the execution is in. |

### Node content hierarchy

Repo/object card content should be:

```text
<subtype>       <status chip> <date/ETA chip>
Title
Goal snippet
Counts: 3 active sessions - 4 open - 2 done
```

Session card content stays execution-oriented:

```text
engine/source
Session title
status - branch - updated
chips
```

Draft cards stay prompt-oriented:

```text
Draft session
<editable prompt>
saved locally - updated
```

### Goal, date, and ETA

Goal/date/ETA should come from the Flow fields table in the Markdown state file.
This keeps the data user-editable and avoids another settings modal.

Fields:

| Field | Display |
|---|---|
| `Status` | Chip on repo/object card and inspector header. |
| `Goal` | One-line snippet on repo/object card. |
| `Target date` | Date chip when present. |
| `ETA` | ETA chip when present, displayed after target date. |
| `Owner` | Inspector header only in first version. |
| `Color seed` | Optional stable override, not a color picker. |

If both Target date and ETA exist, show both compactly. If neither exists, keep
the current counts/last-updated meta.

### Implementation slices

1. Palette helper:
   - Add deterministic hash-to-palette function in `static/app.js`.
   - Add CSS variables for work item cards and accent-tinted edges.
   - Tests can stay smoke-level; visual verification is manual/browser.

2. Shared work item class:
   - Render repo/object records with `flow-node-work-item`.
   - Keep subtype-specific classes only for tiny differences like controls.
   - Replace fixed purple/orange repo/object styling with accent variables.

3. Parent accent propagation:
   - Build a `nodeAccentById` map while rendering records.
   - For child sessions/drafts, set parent accent variables.
   - For edges, color by parent or child-parent pair. Start with parent color.

4. Metadata display:
   - Parse Flow fields from `/api/flow/index`.
   - Add goal/status/date/ETA snippets to repo/object node records.
   - Keep current fallback meta until state loads.

5. CSS polish:
   - Make repo/object cards visually equal as work items.
   - Keep compact dimensions stable so added chips do not resize the board
     during polling.
   - Check light and dark themes.

## Combined framework

These two features should land together as a framework:

1. A work item is the top-level Flow concept.
2. Repos and custom objects are both work items.
3. Work items have Markdown-backed state files.
4. Work item fields drive the card title metadata, goal, ETA/date, and optional
   color seed.
5. Sessions and draft sessions are execution nodes attached to work items.
6. Clicking a work item opens the editable status document.
7. Clicking a session opens the conversation.
8. CCC owns managed Markdown blocks; users own manual notes.

This keeps Flow from becoming only a layout view. It becomes the place to answer
"what is this work, what is happening now, what is fixed, and what remains?"

## First build target

The smallest useful shipped slice:

- Add the Flow state file helpers and `/api/flow/node`.
- Clicking repo/object opens an inspector with Markdown view/edit/save.
- The first lazy-created file has Flow fields and managed block markers.
- The inspector has a Refresh auto sections button.
- Repo/object cards use automatic deterministic accents.
- Repo/object cards show goal/status/date/ETA when the file exists.

Do not start with:

- AI summarization.
- A color picker.
- Moving state files into user repos.
- Fully migrating positions/parents out of localStorage.
- Background auto-rewrites on every poll.

Those can follow once the file contract and inspector feel right.

## Verification checklist

- `python3 -m py_compile server.py`
- `.venv/bin/python3 -m pytest tests/test_smoke.py` (bare `python3` may lack pytest — see CONTRIBUTING.md § Testing)
- Manual browser check:
  - Normal dashboard: repo click opens inspector.
  - Flow popout with reader pane: repo/object click opens inspector on right.
  - Session click still opens conversation.
  - Group chat click still opens group-chat reader.
  - Edit/save survives reload.
  - Refresh auto sections preserves manual notes.
  - Light/dark themes keep accent colors readable.
  - Polling does not resize work item cards unexpectedly.

## Commit rules reminder

Flow changes should stay small and committed with explicit paths:

```bash
git commit --only static/app.js static/app.css server.py tests/test_smoke.py changelog.d/<snippet>.md -m "feat(flow): ..."
```

Do not push unless explicitly asked.
