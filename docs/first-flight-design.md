# FIRST FLIGHT: CCC first-run guide

Design note for the onboarding walkthrough. Ship target: single-file-app
friendly, zero dependencies, lazy until triggered.

## Architecture

- `static/tour.js`: the guide engine + step definitions + styles
  (injected `<style>` tag). Not loaded on normal boots; fetched from
  `/static/tour.js` only when the guide actually runs.
- Bootstrap in `app.js` (end of main IIFE): 2.5s after load, if
  `localStorage` has no `ccc-tour-done` flag and no `.upd-overlay.open`
  modal is up, inject the script tag. Settings "Take the tour" and
  "Run onboarding" both force-start the same script.
- First-run detection: absence of `ccc-tour-done` in localStorage.
  Completion or skip sets it. Users who already finished the old tour
  are not forced to retake it.
- Auto-start is suppressed at viewport widths ≤1200px. Settings replay
  still works everywhere.

## One linear story

Welcome → CLI setup (install / log in / re-detect) → LHS → reviewing
existing conversations → status → transcript → search → creating first
conversation → composer → engine picker → send → Settings → Workers →
RHS → Queue → first queue → Orchestration → Delegation → health →
finale.

CLI setup is an action step against `/api/onboarding/status`,
`/api/onboarding/install-terminal`, and `/api/onboarding/login-terminal`.
It is not a decorative chip row. A fixture payload can be injected so
tests do not need a real CLI binary.

## Spotlight engine

- One fixed-position "cutout" div using the box-shadow trick:
  `box-shadow: 0 0 0 200vmax rgba(...)` positioned over the anchor rect.
- Anchors are real dashboard ids/selectors: `#convList`, `#sidebarNewBtn`,
  `#convSearch`, `#settingsBtn`, `[data-conv-tab="workers"]`,
  `#statusRail`, `[data-rail-tab="queue"]`,
  `[data-orch-playbook="delegate"]`, `#convInputBar`.
- Before a step whose target lives behind a tab or collapsed chrome, the
  guide reveals that surface (Workers tab, Queue rail pane, Delegate
  playbook) and only then spotlights. Missing anchors are skipped only
  as a last resort.
- Tooltip card positioned by available viewport space; at narrow widths
  (≤ 520px) it becomes a bottom sheet.
- Controls: Back / Next / Skip, `N / total` progress, keyboard:
  ArrowRight/Enter next, ArrowLeft back, Escape skip.

## Empty-state strategy

Fresh install has zero sessions. If the session list is empty when a
review step needs rows, the guide injects sample cards (marked
`data-tour-sample`) and removes them at end. Samples teach review; they
do not replace the creating-first-conversation step on New session /
composer. While the guide is active, list re-render is paused via
`window.__cccTourActive`.

## Perf

- No server changes on hot paths; the guide is frontend-only.
- `tour.js` is fetched only when the guide actually runs.
- Engine detect is not re-probed on every dashboard poll; the CLI step
  fetches `/api/onboarding/status` when shown, and again on Re-detect.
- No timers/listeners registered when the guide is not active.
