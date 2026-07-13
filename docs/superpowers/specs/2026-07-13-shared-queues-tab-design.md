# Shared Queues Tab Design

## Goal

Make WatchTower queues easy to reach on mobile by replacing the main sidebar's
Merge tab with a Queues tab. Keep the existing Queue tab in the right-hand
status rail for desktop use.

## User-visible behavior

- The main sidebar tab bar shows Active, All, Issues, and Queues.
- The dedicated Merge tab and its ready-to-merge list are hidden. Their future
  destination is intentionally out of scope for this change.
- Opening the main Queues tab shows the same queue interface currently hosted
  by the right-hand status rail:
  - the queue list and health summary at the top;
  - the selected-queue dropdown and controls in the middle;
  - the selected queue's ticket list at the bottom.
- The right-hand Queue tab remains available.
- Queue selection, search text, filters, listeners, fetched data, and other
  transient UI state survive moving between the two access points.
- A previously saved `merge` main-tab selection falls back to Active because
  Merge is no longer a valid visible tab.

## Architecture

There remains exactly one live `#queuePanel` DOM subtree. The page gains two
mount points: its existing right-rail pane and a lightweight host rendered by
the main Queues tab.

An idempotent queue-panel placement function chooses the active host:

1. When the main Queues tab is selected, it moves `#queuePanel` into the main
   sidebar host and refreshes the panel.
2. When the right-hand Queue tab is selected, it moves `#queuePanel` back into
   the right-rail host and refreshes the panel.
3. When the main Queues tab is not selected, the panel returns to its right-rail
   host so the right-hand tab remains ready for desktop use.

Moving the existing node, rather than cloning markup or creating a second
renderer, preserves all existing element IDs and event listeners and prevents
the two access points from drifting apart.

## Layout

The main-tab host applies layout-only rules so the existing queue panel fills
the sidebar's available width and scroll area. Queue controls, row markup,
filter behavior, and visual styling remain shared with the right rail.

The placement function runs after main-sidebar renders because that render
replaces the tab body with `innerHTML`. It also runs when either queue entry
point is selected. This prevents the shared panel from being discarded during a
sidebar refresh.

## Failure handling

The placement function is a no-op if the shared panel or requested host is not
present. Existing queue fetch and stale-cache behavior remains unchanged. No
new server endpoints or persistence formats are introduced.

## Testing

- Add a regression assertion that the main tab definition contains Queues and
  no longer contains Merge.
- Verify a saved `merge` tab value is rejected in favor of Active.
- Verify the page contains one queue panel and two stable mount points.
- Exercise both entry points in a DOM-capable browser test and confirm the same
  `#queuePanel` node moves between them without losing selected controls.
- Run the existing smoke suite.
- Use the repository's Puppeteer harness at a mobile viewport to confirm the
  Queues tab is reachable and its top list, dropdown, and ticket list fit the
  sidebar.

## Out of scope

- Relocating or redesigning ready-to-merge content.
- Changing queue APIs, ticket semantics, filtering, or queue-management actions.
- Maintaining two separately rendered queue panels.
