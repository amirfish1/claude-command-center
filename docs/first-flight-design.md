# FIRST FLIGHT: CCC out-of-box tour (W74)

Design note for the onboarding tour. Ship target: single-file-app friendly,
zero dependencies, lazy until triggered.

## Architecture

- `static/tour.js`: the whole tour engine + step definitions + styles
  (injected `<style>` tag). Not loaded on normal boots; fetched from
  `/static/tour.js` only when the tour actually runs.
- Bootstrap in `app.js` (end of main IIFE): 2.5s after load, if
  `localStorage` has no `ccc-tour-done` flag and no `.upd-overlay.open`
  modal is up (the login onboarding wizard owns true first boot), inject
  the script tag. While a modal is open it retries every 4s (max 50), so
  the tour fires right after the wizard closes. Settings gains a
  "Take the tour" row (`#takeTourBtn`) that force-starts the same script.
- First-run detection: absence of `ccc-tour-done` key in localStorage.
  Tour completion or skip sets it (value: `done` or `skipped` + path).

## Spotlight engine

- One fixed-position "cutout" div using the box-shadow trick:
  `box-shadow: 0 0 0 200vmax rgba(...)` positioned over the anchor rect,
  border-radius matched, pointer-events blocked around it.
- Anchors are `data-tour="<name>"` attributes on real DOM elements, all on
  static markup in index.html: session-list, new-session, watchtower,
  search, group-chat, settings, spawn-bar. Dynamic rows are matched by
  class (`#convList .conv-item`). A step's `anchor` may be an array of
  selectors; the first visible match wins (the multi-engine spawn step
  falls back from the composer bar, hidden on fresh installs, to the New
  session button).
- Tooltip card positioned by available viewport space (below > above >
  side); at narrow widths (<= 480px) it becomes a bottom sheet.
- Controls: Back / Next / Skip, progress dots, keyboard: ArrowRight/Enter
  next, ArrowLeft back, Escape skip. Reposition on resize/scroll while
  active only.
- Resilience: anchor missing at step time -> step silently skipped in the
  travel direction. List view is the hero; the tour never anchors on Flow.

## Two flight paths

Welcome card -> one-question fork ("New to agent fleets?" vs "Running
multiple engines already?") -> path-specific spotlight steps -> finale card
with 3 concrete "try this now" suggestions personalized by fork choice.

## Empty-state strategy

Fresh install has zero sessions. If the session list is empty when the tour
needs it, the tour injects a few sample cards (marked `data-tour-sample`)
into the list container and removes them at tour end. While the tour is
active, list re-render is paused via a tour-active flag checked in the
render path, so polling does not wipe the samples mid-step.

## Perf

- No server changes on hot paths; tour is frontend-only.
- tour.js is fetched only when the tour actually runs.
- No timers/listeners registered when the tour is not active.
