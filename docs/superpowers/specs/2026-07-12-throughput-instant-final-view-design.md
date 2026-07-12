# Throughput Instant Final View Design

## Goal

The throughput page must show its complete billing-period dashboard in under
100 ms on repeat visits, even when the available data is stale. It must never
render the old all-hours graph before replacing it with the weekly-context
graph. Refreshing remains automatic and non-blocking, with useful progress and
freshness information visible in the header.

## Experience

The billing-period view is the only default throughput view. On a repeat visit,
the page synchronously restores a complete cached render model before starting
network work. On a first-ever visit, it shows the same final layout and chart
frame with a quiet `Preparing first snapshot…` message; it never falls back to
the legacy graph.

The existing status label becomes a compact refresh panel in the top-right. It
shows the last successful refresh, live elapsed time while refreshing, expected
duration, and session progress. A failed refresh leaves the stale dashboard
untouched and changes only the refresh panel. A successful refresh swaps the
complete render model atomically so partial inputs cannot produce an
intermediate chart.

## Architecture

### Complete render model

Introduce a versioned throughput bootstrap object containing everything needed
to paint the final view:

- the aggregate throughput payload;
- weekly quota context used by cards and cumulative overlays;
- reset events used by vertical chart markers;
- refresh metadata, including generation time and session statistics; and
- a schema version and engine/scope identity.

The frontend validates this object before rendering it. Incomplete or
incompatible objects are ignored rather than rendered through a fallback path.

### Two-tier stale-while-revalidate cache

The browser stores the last complete render model in `localStorage`, keyed by
engine and aggregate scope. Reading and parsing this object is synchronous, so
repeat visits can paint without waiting for a request. The server also persists
the same complete bootstrap object and exposes a cache-only endpoint. That
endpoint performs no conversation discovery or transcript parsing.

Boot order is:

1. Restore and render the browser model synchronously when valid.
2. Otherwise show the final-view first-snapshot shell.
3. Request the server bootstrap without blocking the visible page.
4. Render it only if it is complete and newer than the visible model.
5. Start one authoritative background refresh.
6. Commit the fresh model to disk and browser storage, then replace the visible
model in one render transaction.

### Compact weekly usage header

The weekly usage banner is one compact row rather than a multi-line data dump.
Claude, Fable, and Codex use identical meter anatomy: a short product label,
the percentage, and a horizontal gauge. The center status area contains at
most two visible lines: the last authoritative sync/live estimate and the
reset projection/countdown. Secondary details such as plan type and Codex
session usage remain accessible through the status area's native tooltip.

The reset timestamp and `Record reset` action stay aligned on the right. At
narrow widths the three sections wrap without collisions: meters first,
status second, controls last. The banner must not rely on fixed text widths or
allow status copy to overlap meters or controls.

### Unified engine hierarchy and metrics

The Claude/Fable/Codex quota row is global context and remains above engine
selection. The Claude/Codex toggle moves immediately below that row. Aggregate
views omit the redundant engine title and `all_7_days` identifier.

Claude and Codex share one three-card operational summary with identical
semantics: calls, cache-adjusted tokens per day, and cache-hit rate. Quota
percentage is not repeated below the global row. Cost, hypothetical Opus cost,
and dollar-denominated cache savings are removed from the aggregate summary.
Per-session detail remains available when a specific conversation is selected.

The aggregate chart has one billing-period renderer. Its unreachable
all-discovered-hours fallback is removed. The weekly percentage axis is fixed
at 0–100%; values above 100% are clamped to the top edge and retain their true
value in an upward overflow label. Each local calendar-day divider is labeled
`00:00` in addition to its centered day/date label.

There is no staleness cutoff for display. Age is disclosed in the refresh panel;
stale data is preferable to a loader or the legacy graph.

### Authoritative refresh and progress

The refresh operation tracks a small server-side job record. Progress fields
include state, start time, expected duration, sessions discovered, sessions
read, transcript-cache hits, transcripts parsed, and the last successful
completion time. Expected duration uses the most recent successful duration for
the same engine/scope, falling back to a conservative built-in estimate.

The client starts the refresh and polls the lightweight job status endpoint.
The live elapsed timer runs locally between polls. Session counters update as
the server scans. Refresh completion returns or makes available one complete
bootstrap model. Concurrent page loads join the same in-flight refresh rather
than starting duplicate scans.

## Graph Behavior

The final chart retains:

- cache-adjusted three-hour bars;
- cumulative weekly quota and Fable contribution overlays;
- previous-week comparison;
- billing-period navigation and recent-hours zoom;
- automatic and manual vertical reset-limit markers;
- clickable reset details and manual marker edit/delete controls; and
- current tooltips and annotation targeting.

The old all-hours rendering path is removed from default aggregate boot. The
aggregate graph renders only with complete weekly context. Missing weekly data
produces the final chart frame with an explicit unavailable state, never the
legacy graph.

## Failure Handling

- Corrupt browser data is removed and ignored.
- A missing or corrupt server snapshot returns a valid no-snapshot response
  without initiating expensive work.
- Refresh failure preserves the last visible snapshot and reports failure,
  elapsed time, and cached-data age in the refresh panel.
- Browser storage quota failure is non-fatal because the server snapshot remains
  available.
- A response for a previously selected engine or scope cannot overwrite the
  current view.

## Performance Contract

For a valid browser snapshot of representative size, the time from script boot
to completion of the first `renderDashboard` transaction must be below 100 ms
in the repository's Puppeteer/Chromium harness. Network requests and background
refresh start only after that first render transaction. The server cache-only
bootstrap remains a fallback, not the mechanism used to claim the 100 ms repeat
visit target.

## Testing and Verification

Automated tests will prove:

- cached boot renders before any fetch and stays below the 100 ms budget;
- the default boot path contains no call that can draw the legacy aggregate
  graph;
- incomplete weekly/reset context cannot trigger an intermediate render;
- browser and server cache validation rejects wrong schema, engine, or scope;
- cache-only bootstrap performs no conversation discovery or parsing;
- refresh progress exposes timing and session counters;
- concurrent refresh requests share one scan;
- refresh success atomically publishes the complete model;
- refresh failure preserves cached content; and
- reset markers and their interactions remain wired into the final graph.

Visual verification uses `node snapshot.js` (or a focused Puppeteer script built
on the same installed Chromium) to capture the first painted cached state and
the post-refresh state. The two captures must have the same graph structure,
with only data and refresh-status values changing.

## Scope

This change is limited to the throughput page and its public throughput API.
It does not change quota calculations, transcript token extraction, pricing, or
unrelated dashboard behavior.
