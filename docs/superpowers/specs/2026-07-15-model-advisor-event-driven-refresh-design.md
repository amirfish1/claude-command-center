# Event-Driven Model Advisor Refresh Design

## Goal

Keep CCC's Model Advisor useful and current without continuously scanning session
transcripts merely to maintain its footer indicator.

## Current problem

The dashboard requests `/api/model-advisor` every 45 seconds even when the
advisor is closed. Building that response scans live and recent sessions and can
read transcript metadata. Repeating that work on a fixed timer creates sustained
background cost unrelated to user activity.

The existing performance worktree replaces polling with a cached report, but its
first browser snapshot only establishes a baseline. After a server or browser
restart, existing model drift can remain undiscovered until the user opens the
advisor or a later session change qualifies for refresh.

## Design

### Server report cache

`model_advisor.AdvisorReportCache` owns the latest stable report, a five-minute
automatic-refresh cooldown, and a condition-protected single-flight refresh.

- A normal GET returns the cached report and never starts a scan.
- `fresh=1` requests a cooldown-limited refresh. Concurrent callers share one
  build.
- `fresh=force` is used only when the user opens the advisor and bypasses the
  cooldown while retaining single-flight behavior.
- Failed scans leave the previous report available and do not advance the
  cooldown.

### Browser scheduling

The browser observes the session list it already receives. It schedules an
advisor refresh when a session appears or disappears, changes live state,
changes model, or grows by at least 32 KiB. Qualifying changes are debounced for
30 seconds, and automatic refresh requests are rate-limited to five minutes.

The initial non-empty session snapshot also schedules one debounced automatic
refresh. This closes the cold-start gap without restoring a permanent polling
timer. An empty initial snapshot does not schedule work.

Opening the advisor requests one forced refresh. While open, the five-second UI
timer reads only the cached report.

The footer performs no independent network polling and renders the most recent
report already available in the page, otherwise retaining its neutral default.

### Pre-push Python discovery

The Bash pre-push performance gate uses Bash-compatible interpreter discovery.
It prefers the repository virtual environment, then searches PATH for each
`python3` executable that can import pytest. If none is available, it preserves
the existing explicit skip message.

## Error handling and compatibility

- The public `/api/model-advisor` response shape remains additive and stable.
- Cached reads always return a complete empty-report shape before the first
  successful build.
- Refresh exceptions wake all waiting callers and preserve the last successful
  report.
- No automatic recommendation is applied. Model switches remain explicit user
  actions through the existing apply endpoint.
- The work is ported onto current `main`; the stale performance worktree remains
  an untouched reference until the current-main integration is verified.

## Verification

- Unit tests cover cached reads, cooldown behavior, forced refresh, concurrent
  single-flight refresh, and failed-refresh recovery.
- Static UI tests cover the lack of footer polling, modal forced refresh,
  meaningful-change scheduling, and the initial-snapshot refresh.
- Bash-focused tests run the pre-push gate under Bash with a synthetic
  pytest-capable interpreter.
- The focused advisor/install suites, full test suite, import/compile checks,
  and Puppeteer dashboard snapshot must pass before integration is considered
  complete.

## Non-goals

- Changing recommendation scoring, model tiers, prices, or savings calculations.
- Automatically applying model switches.
- Adding a new dashboard layout or changing advisor visual design.
- Merging unrelated dashboard-performance work from sibling worktrees.
