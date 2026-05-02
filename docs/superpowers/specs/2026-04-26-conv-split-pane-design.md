# Drag-to-split conversation pane

**Status:** Design approved 2026-04-26
**Scope:** `static/index.html` (single-file frontend); no server changes.

## Goal

Let the user drag a conversation card (from the sidebar list or a kanban column) onto the conversation pane and drop it on the right edge or bottom edge to open a second conversation alongside the current one.

## Non-goals

- More than two panes. No recursive splits, no tabs.
- Drop on left or top edges. No "replace current" via drop.
- Persisting the split across page reload.
- Keyboard shortcuts for switching panes.
- Any change to `/api/*` or to `server.py`.
- Any change to mobile / narrow-viewport behavior beyond hiding drop zones below ~900px.

## UX flow

1. User starts dragging a `.conv-item` (sidebar) or `.kanban-card` (kanban column).
2. The active conversation pane shows a translucent overlay with two drop targets:
   - **Right edge** (~20% of pane width) — labeled "Open on the right".
   - **Bottom edge** (~20% of pane height) — labeled "Open on the bottom".
3. Hovering a target highlights it. Dropping outside both targets cancels.
4. On drop:
   - Right edge → pane splits vertically. Original conversation stays on the left, dropped conversation opens on the right.
   - Bottom edge → pane splits horizontally. Original on top, dropped on bottom.
5. Each pane has its own header (title + close `×`), transcript, and composer (input + send + model picker).
6. Each pane streams its own conversation independently (separate SSE connection).
7. The **active pane** is the pane most recently clicked or focused (clicking the transcript area, composer, or pane header sets it active; opening the second pane via drop makes it active immediately). The sidebar list's active highlight follows the active pane. Clicking a `.conv-item` while split is open replaces the active pane's conversation (existing click-to-open behavior, scoped to the active pane).
8. Click `×` on a pane header to close it. The remaining pane expands to fill.
9. Dropping a third card while both panes are filled is rejected (no overlay, no-op). User must close a pane first.

## Drop sources

Both card types already carry `draggable="true"` and a payload (`text/plain` = conversation id). The new feature adds drop handlers to the conversation-pane container; existing drag behavior (sidebar reorder, kanban column move) is untouched because those drop targets are different DOM nodes.

## State refactor

Today, single global scalars track the open conversation:

```
currentConversation, convLastLine, convEventSource,
_pendingSends, _firstUserMsgRendered
```

Replace with a small per-pane map plus a layout descriptor:

```js
splitState = {
  orientation: null | 'vertical' | 'horizontal',
  panes: [paneState] | [paneState, paneState],
  activeIndex: 0,
}

paneState = {
  id: 'p1' | 'p2',
  conversationId: string,
  lastLine: number,
  eventSource: EventSource | null,
  pendingSends: Array,
  firstUserMsgRendered: boolean,
}
```

**Compatibility shim.** Keep the old global names (`currentConversation`, `convLastLine`, `convEventSource`, `_pendingSends`, `_firstUserMsgRendered`) defined as `Object.defineProperty` getter+setter pairs on `window` that read/write `splitState.panes[splitState.activeIndex].*`. Both directions matter — existing code both reads (`if (id !== currentConversation) return`) and writes (`currentConversation = id` inside `selectConversation`) these names. This avoids touching the ~thousands of lines that reference those globals; only the renderer / SSE / composer entry points learn about pane id explicitly.

The functions that need a `paneId` parameter (with default = active pane, so unsplit callers don't change):

- `renderConversationEvents(events, paneId = activePaneId())`
- `fetchConversationEvents(paneId = activePaneId())`
- `startConvStream(paneId = activePaneId())`
- `stopConvStream(paneId = activePaneId())`
- `selectConversation(id, paneId = activePaneId())`
- `sendToTerminal(paneId = activePaneId())` and the kanban-mode equivalent `sendToSplitTerminal`

## DOM

The conversation pane is `#conversationsView` inside `.main`. The historical split-pane kanban layout (`#kanbanLayout` / `#convPanelView`) has been retired — `getConvView()` always returns `$conversationsView`, and kanban view today is a sidebar-mode swap, not a split. So there is exactly one drop target and one container to refactor:

- Wrap the existing single-pane chrome (toolbar + view + input) in a `.conv-pane` element with a per-pane header, transcript area, and composer.
- When `splitState.orientation` is set, render two `.conv-pane` elements inside a `.conv-split[data-orientation="vertical|horizontal"]` flex container that replaces the single `.conv-pane` in flow.
- A 4px draggable divider sits between panes; drag adjusts `splitState.ratio` (default 0.5) which drives flex-basis on each pane.
- The drop overlay is a single absolutely positioned element appended to the pane on `dragenter`, removed on `dragleave` / `drop` / `dragend`. A counter tracks nested `dragenter`/`dragleave` events so the overlay doesn't flicker as the cursor crosses child elements.

## Visuals

- Drop target overlay: 20% edge band, 1px dashed accent border, 12% opacity accent fill on hover.
- Active pane: 1px solid `--accent` border on the pane's outer frame; inactive pane has the existing 1px subtle border.
- Divider: 4px wide / tall, hover state nudges to 6px and shows the `col-resize` / `row-resize` cursor.
- Pane header `×`: matches existing icon-button style (the same one used on conversation rows).

## Edge cases

- **Same conversation dragged into the other pane.** Reject (no-op + brief tooltip "Already open"). Avoids two SSE streams for the same conversation id.
- **Closing the active pane.** The remaining pane becomes active; sidebar highlight updates.
- **Toggling kanban view while split.** Drag sources change (sidebar list ↔ kanban board) but the split layout is unaffected — both share the same `#conversationsView` host.
- **Viewport < 900px.** Drop zones do not appear. If a split is already active and the viewport shrinks below the threshold, fall back to the active pane only (the second pane is hidden, its SSE connection closed; restored on resize back).
- **Pkood / live agent panes.** `sendToSplitTerminal` already keys off the conversation; once `paneId` flows through, an agent conversation can sit in either pane.

## Testing

`tests/test_smoke.py` is import-only and stays untouched. Manual QA against the running server:

1. Sidebar list view → drag conv onto pane right → vertical split appears.
2. Sidebar list view → drag conv onto pane bottom → horizontal split appears.
3. Kanban view → drag card onto pane right / bottom → same.
4. Send a message in each pane independently; both stream correctly.
5. Drag a third card while split is full → drop is rejected.
6. Close one pane via `×` → remaining pane expands; SSE for closed pane is torn down.
7. Resize viewport below 900px → split collapses to active pane; resize up → split restored.
8. Dropping the same conv that's already open in the other pane → rejected.

## Files touched

- `static/index.html` — the entire change.
- `CHANGELOG.md` — a bullet under `## [Unreleased]` → `Added`: "Drag a conversation onto the right or bottom edge of the chat pane to open it side-by-side or stacked."
- `pyproject.toml` and `server.py` `__version__` — minor bump (new user-visible feature).

## Risks

- **Compatibility shim leaks.** A future reader sees `currentConversation` and may not realize it's a getter. Mitigation: a single comment block at the shim definition explaining the indirection.
- **Two concurrent SSE streams.** Browsers cap per-origin connections at 6; two is well within budget. No mitigation needed.
- **Drag overlay flicker on `dragleave`.** HTML5 dnd fires `dragleave` on every child crossing. Mitigation: track enter/leave with a counter, only remove overlay when count returns to 0.
