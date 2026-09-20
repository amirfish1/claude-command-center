---
globs: "static/app.js,static/app.css,static/index.html"
---
# Flow workspace

Flow is CCC's canvas-style workspace for arranging repos, objects, sessions,
draft sessions, and group chats. Keep this note current when changing Flow
internals so future agent sessions do not rely on scratch handoff files.

## Core implementation map

- Markup host: `#flowBoard` in `static/index.html`.
- Behavior: `static/app.js`.
- Styles: `static/app.css`.
- Render entry point: `renderFlowSidebar(convs)`.
- Primary DOM stack: `.flow-board` -> `.flow-canvas` -> `.flow-world` ->
  positioned `.flow-node` elements.
- Zoom/pan: `.flow-world` uses `transform: scale(flowZoom)`;
  `#flowBoard.scrollLeft` and `scrollTop` hold pan state.
- Node identity: every node carries `data-flow-kind` and
  `data-flow-node-id`; parentable nodes also use `data-flow-parent`.
- Repo and group-chat affordances also carry stable path/id attributes such as
  `data-repo-path`, `data-gc-path`, and `data-gc-id`.
- Parent and position state are localStorage-backed maps:
  `flowNodeParents` and `flowNodePositions`.

## Node kinds

- `repo`: top-level repo cluster root.
- `object`: draggable container node; can own sessions/drafts and nested
  objects.
- `group-chat`: top-level group-chat node; accepts dropped sessions.
- `session`: conversation/session child node.
- `draft-session`: not-yet-started child node.

## Layout and organize

`organizeFlowSessions(targetEl)` is incremental and cluster-based:

- Each cluster anchors from its current parent position.
- Re-running Organize on a clean board should move 0 px.
- Unplaced top-level clusters seed from the bin-pack cursor.
- Unplaced nested clusters seed below the ancestor footprint.
- If cluster bounding boxes overlap, a greedy resolver picks the worst pair and
  pushes the less-displaced cluster by the minimum right/down distance using
  `CLUSTER_MARGIN`.

Preserve user-placed parents where possible. Avoid layout rewrites that make
Organize surprise users by moving stable clusters on every run.

## Edges

`redrawFlowLinks` renders SVG `<g class="flow-edge">` groups with a wide
transparent hit path above a thin visible edge. Current interactions:

- Click selects one edge.
- Backspace/Delete removes the selected edge.
- Dragging an edge endpoint reparents through `startEdgeReparentDrag`.

## Pan and render stability

Pan uses pointer events on `#flowBoard` and mutates scroll position. During
pan, the board gets `.is-panning`; polling renders must treat this the same as
an active drag and defer until release. Before rewriting Flow DOM with
`innerHTML`, preserve scroll position and restore it after `applyFlowZoom`.

## Popout mode

Flow popout mode is activated by `?ccc_popout=flow`:

- `body.flow-popout` is applied.
- `ccc-session-view=flow` is forced in localStorage.
- `document.title` becomes `Flow`.
- The right-side conversation reader is toggled by `ccc-flow-popout-reader`.
- The reader split is resizable through `.flow-reader-resizer`; width persists
  in `ccc-flow-reader-width` and drives `--flow-reader-width`.

## Known follow-ups

- Naming clarity: decide whether `Flow` is the right product label, or whether
  `Board`, `Canvas`, or `Workspace` would be clearer in user-facing copy.
- Multi-select for edges plus bulk delete.
- Group-chat nesting under repos or objects, if that becomes useful.
- R10 tie-break edge case: when two user-placed clusters truly overlap and
  both have zero displacement, the resolver currently pushes the later cluster
  in ordered-parent order.
- Popout annotation fallback: the popout Annotate action should not depend on a
  still-open main dashboard window.
- First-time Organize on a fully loaded board could use a smarter initial
  layout when there are no saved user positions.

## Working agreements

- For Flow fix proposals, use: problem / tradeoff / value (L/M/H) /
  confidence (L/M/H).
- Keep commits small and scoped. Use `git commit --only <paths>`.
- Push when the fix or feature is finished (see `CLAUDE.md` § Push); not for
  mid-work checkpoints.
