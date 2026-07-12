# Throughput Instant Final View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the complete billing-period throughput view from stale cache in under 100 ms, refresh it atomically in the background, and expose useful live refresh telemetry without ever painting the legacy graph.

**Architecture:** Publish one versioned bootstrap model that combines aggregate throughput, weekly quota context, reset events, and refresh metadata. Restore it synchronously from browser storage for first paint, use a cache-only server snapshot as fallback, and run a single-flight server refresh that atomically persists and returns the next complete model. The existing chart renderer remains authoritative, but aggregate rendering is gated on complete context and never uses the old all-hours fallback.

**Tech Stack:** Python stdlib server and threads, atomic JSON snapshots, single-file HTML/CSS/JavaScript, `localStorage`, pytest static/server tests, Puppeteer/Chromium.

## Global Constraints

- `server.py` remains stdlib-only.
- `static/throughput.html` remains a single-file app.
- A valid browser snapshot completes its first render transaction in under 100 ms.
- Cache-only bootstrap reads never discover conversations or parse transcripts.
- Stale complete data has no display expiry; its age is disclosed in the UI.
- Aggregate data becomes visible only with matching weekly and reset context.
- Refresh results replace the visible model atomically; failures preserve stale content.
- Reset-limit markers, marker interactions, quota overlays, previous-week comparison, navigation, zoom, tooltips, and annotations remain available.
- Browser verification uses this repository's Puppeteer Chromium, never Playwright or the in-app browser.
- Preserve unrelated changes in the shared worktree and commit only explicit paths.

---

### Task 1: Versioned Complete Bootstrap Snapshot

**Files:**
- Modify: `server.py` near `_throughput_snapshot_path`, `_throughput_initial_payload`, and `_throughput_payload`
- Modify: `tests/test_perf_budget.py`

**Interfaces:**
- Produces: `_THROUGHPUT_BOOTSTRAP_SCHEMA = 1`
- Produces: `_throughput_bootstrap_path(session_id, engine_filter=None) -> Path`
- Produces: `_throughput_build_bootstrap(session_id, engine_filter, throughput, *, generated_at=None, refresh=None) -> dict`
- Produces: `_throughput_read_bootstrap(session_id, engine_filter=None) -> dict | None`
- Produces: `_throughput_write_bootstrap(session_id, engine_filter, model) -> bool`
- Changes: `_throughput_initial_payload(...)` returns `{"ok": True, "bootstrap": model_or_none}` and never computes fresh data.

- [ ] **Step 1: Write failing contract tests**

Add tests that stub `_weekly_usage_block()` and `usage_reset_events_payload()` and assert one complete model contains matching schema, scope, engine, throughput, weekly context, reset events, `generated_at`, and refresh metadata. Add corrupt-file, wrong-schema, wrong-engine, and cache-only-no-compute cases:

```python
def test_throughput_bootstrap_round_trips_complete_context(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(server, "_weekly_usage_block", lambda: {"available": True, "pct_per_token": 0.01})
    monkeypatch.setattr(server, "usage_reset_events_payload", lambda **_: {"events": [{"id": "r1"}]})
    payload = {"ok": True, "session_id": "all_7_days", "scope": {"aggregate": True, "engine": "claude"}, "summary": {}, "turns": []}

    model = server._throughput_build_bootstrap("all_7_days", "claude", payload, generated_at=123.0)
    assert server._throughput_write_bootstrap("all_7_days", "claude", model)
    loaded = server._throughput_read_bootstrap("all_7_days", "claude")

    assert loaded["schema"] == server._THROUGHPUT_BOOTSTRAP_SCHEMA
    assert loaded["throughput"] == payload
    assert loaded["weekly"]["pct_per_token"] == 0.01
    assert loaded["reset_events"] == [{"id": "r1"}]
    assert loaded["generated_at"] == 123.0
```

- [ ] **Step 2: Run tests and verify RED**

Run `pytest tests/test_perf_budget.py -k 'throughput_bootstrap or throughput_initial_payload' -q`.

Expected: failures because the bootstrap helpers and complete response contract do not exist.

- [ ] **Step 3: Implement strict model construction and atomic persistence**

Use a stable JSON shape and validate it on read:

```python
_THROUGHPUT_BOOTSTRAP_SCHEMA = 1

def _throughput_build_bootstrap(session_id, engine_filter, throughput, *, generated_at=None, refresh=None):
    engine = _throughput_engine_filter(engine_filter) or "claude"
    return {
        "schema": _THROUGHPUT_BOOTSTRAP_SCHEMA,
        "session_id": session_id,
        "engine": engine,
        "generated_at": float(generated_at or time.time()),
        "throughput": throughput,
        "weekly": _weekly_usage_block(),
        "reset_events": usage_reset_events_payload(days=30).get("events", []),
        "refresh": dict(refresh or {}),
    }
```

`_throughput_read_bootstrap` must return `None` unless schema, session, engine, aggregate throughput scope, weekly object, and reset-event list are valid. `_throughput_write_bootstrap` writes a `.tmp` sibling and calls `os.replace`.

- [ ] **Step 4: Publish the bootstrap after successful aggregate computation**

After `_throughput_payload` completes a successful aggregate, build and persist the complete model. Keep the older aggregate snapshot readable only as a migration input; never send it as a complete bootstrap.

- [ ] **Step 5: Run tests and verify GREEN**

Run `pytest tests/test_perf_budget.py -k 'throughput_bootstrap or throughput_initial_payload or throughput_full_aggregate' -q`.

Expected: all selected tests pass.

- [ ] **Step 6: Commit the server bootstrap slice**

Run `git commit --only server.py tests/test_perf_budget.py -m "feat(throughput): persist complete bootstrap snapshots"`.

### Task 2: Single-Flight Refresh Progress

**Files:**
- Modify: `server.py` near throughput cache globals, aggregate discovery loop, and throughput routes
- Modify: `tests/test_perf_budget.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Produces: `_throughput_refresh_start(session_id, engine_filter=None) -> dict`
- Produces: `_throughput_refresh_status(session_id, engine_filter=None) -> dict`
- Produces: `GET /api/throughput/refresh/start?session_id=...&engine=...`
- Produces: `GET /api/throughput/refresh/status?session_id=...&engine=...`
- Refresh status fields: `state`, `started_at`, `elapsed_ms`, `expected_ms`, `sessions_discovered`, `sessions_read`, `cache_hits`, `parsed`, `last_refreshed_at`, `error`.

- [ ] **Step 1: Write failing refresh tests**

Test status shape, single-flight joining, progress counters, success publication, and stale preservation on error. Use a `threading.Event` to hold a fake refresh and prove two starts share one worker:

```python
def test_throughput_refresh_is_single_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def fake_refresh(*_args, **_kwargs):
        calls.append(1); entered.set(); release.wait(2)
        return {"ok": True, "scope": {"aggregate": True, "engine": "claude"}}, 200
    monkeypatch.setattr(server, "_throughput_payload", fake_refresh)
    first = server._throughput_refresh_start("all_7_days", "claude")
    assert entered.wait(1)
    second = server._throughput_refresh_start("all_7_days", "claude")
    assert first["job_id"] == second["job_id"]
    assert len(calls) == 1
    release.set()
```

- [ ] **Step 2: Run tests and verify RED**

Run `pytest tests/test_perf_budget.py -k 'throughput_refresh' -q`.

Expected: failures because refresh job APIs do not exist.

- [ ] **Step 3: Implement locked job state and rolling estimates**

Add `_THROUGHPUT_REFRESH_JOBS`, `_THROUGHPUT_REFRESH_LOCK`, and small helpers that copy job dictionaries before returning them. Start a daemon thread only when the matching engine/scope has no running job. Measure successful duration and reuse it as `expected_ms`; default the seven-day estimate to 35,000 ms.

- [ ] **Step 4: Instrument aggregate discovery without changing calculations**

When an aggregate job is active, set `sessions_discovered = len(recent)`. Increment `sessions_read` after each eligible conversation. Extend `_throughput_file_turns` with an optional progress callback or return-source flag so disk/memory hits increment `cache_hits` and extraction increments `parsed` without duplicating parsing logic.

- [ ] **Step 5: Add lightweight HTTP routes**

Register start and status routes next to `/api/throughput/initial`. Both validate `session_id`; start returns `202` for a running job and status returns a copied snapshot. The worker atomically publishes the complete bootstrap on success.

- [ ] **Step 6: Run tests and verify GREEN**

Run `pytest tests/test_perf_budget.py -k 'throughput_refresh' -q && pytest tests/test_smoke.py -k 'throughput' -q`.

Expected: all selected tests pass.

- [ ] **Step 7: Commit the refresh slice**

Run `git commit --only server.py tests/test_perf_budget.py tests/test_smoke.py -m "feat(throughput): report background refresh progress"`.

### Task 3: Instant Atomic Browser Boot

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_search_ui_static.py`
- Create: `tests/test_throughput_instant_boot_static.py`

**Interfaces:**
- Produces: `THROUGHPUT_BOOTSTRAP_SCHEMA = 1`
- Produces: `throughputBootstrapKey(sessionId, engine) -> string`
- Produces: `validateThroughputBootstrap(value, sessionId, engine) -> boolean`
- Produces: `readThroughputBootstrap(...)`, `writeThroughputBootstrap(...)`
- Produces: `applyThroughputBootstrap(model, source) -> boolean`
- Produces: `showFirstSnapshotShell(session)`
- Removes default-aggregate use of `renderChartWaitingForWeeklyContext()` and direct `refreshAggregateInBackground()`.

- [ ] **Step 1: Write failing static behavior tests**

Assert that browser cache read and `applyThroughputBootstrap` occur before the first `fetch(` in boot; that `applyThroughputBootstrap` assigns `weeklyData` and `resetEvents` before one call to `renderDashboard`; that the old direct aggregate refresh boot call is absent; and that invalid models do not render.

```python
def test_cached_boot_precedes_network_and_applies_context_atomically():
    html = HTML.read_text()
    boot = html.index("function bootThroughputPage")
    read = html.index("readThroughputBootstrap", boot)
    apply = html.index("applyThroughputBootstrap", boot)
    fetch = html.index("fetch(", boot)
    assert read < apply < fetch

    apply_fn = html[html.index("function applyThroughputBootstrap"):]
    assert apply_fn.index("weeklyData = model.weekly") < apply_fn.index("renderDashboard(")
    assert apply_fn.index("resetEvents = model.reset_events") < apply_fn.index("renderDashboard(")
```

- [ ] **Step 2: Run tests and verify RED**

Run `pytest tests/test_throughput_instant_boot_static.py tests/test_search_ui_static.py -q`.

Expected: failures because browser bootstrap and atomic boot functions are missing.

- [ ] **Step 3: Implement versioned browser cache**

Parse `localStorage` inside `try/catch`, validate all required fields, remove corrupt entries, and store only complete models. Key by schema, session, and engine. Do not apply any age cutoff.

- [ ] **Step 4: Make complete-model application the only aggregate renderer**

`applyThroughputBootstrap` must set weekly data, weekly-loaded state, reset events, refresh metadata, and aggregate payload before invoking `renderDashboard` once. For missing cache, show `dashboard-content`, fill the final card/chart structure with neutral values, and put `Preparing first snapshot…` inside the same chart SVG.

- [ ] **Step 5: Replace boot sequencing**

Implement `bootThroughputPage()`:

```javascript
function bootThroughputPage() {
  updateThroughputEngineUi();
  const session = currentAggregateSession();
  activeSessionId = session.session_id;
  const cached = readThroughputBootstrap(session.session_id, activeThroughputEngine);
  if (!applyThroughputBootstrap(cached, 'browser')) showFirstSnapshotShell(session);
  queueMicrotask(() => loadServerBootstrapThenRefresh(session));
  toggleSidebar(localStorage.getItem('ccc-throughput-sidebar-collapsed') !== '0');
  loadSessions({ selectDefault: false });
}
```

`loadSessions` must not call `selectSession` when aggregate boot owns the default view. Server fallback may apply only a valid, newer complete model. Then it starts/joins the refresh job.

- [ ] **Step 6: Delete the legacy aggregate transition**

Remove `shouldDeferAggregateChart` and `renderChartWaitingForWeeklyContext`. In `drawChart`, an aggregate without valid weekly context renders a stable unavailable message inside the final billing chart; it never switches bucket modes or draws the all-hours legacy graph.

- [ ] **Step 7: Run tests and verify GREEN**

Run `pytest tests/test_throughput_instant_boot_static.py tests/test_search_ui_static.py tests/test_throughput_reset_markers_static.py tests/test_throughput_chart_zoom_static.py tests/test_throughput_fable_contribution_static.py -q`.

Expected: all selected tests pass.

- [ ] **Step 8: Commit the instant boot slice**

Run `git add tests/test_throughput_instant_boot_static.py && git commit --only static/throughput.html tests/test_search_ui_static.py tests/test_throughput_instant_boot_static.py -m "feat(throughput): render final view from browser cache"`.

### Task 4: World-Class Refresh Panel

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_instant_boot_static.py`

**Interfaces:**
- Produces: `renderRefreshPanel(status)`
- Produces: `startRefreshStatusPolling(session)` and `stopRefreshStatusPolling()`
- Replaces: `setStatus(text, fetching)` for aggregate refresh state.

- [ ] **Step 1: Write failing refresh-panel tests**

Assert accessible `role="status"`, `aria-live="polite"`, and dedicated values for last refresh, elapsed/expected, sessions read/discovered, and cached/parsed counts. Assert a local 100 ms timer updates elapsed display while server polling is slower.

- [ ] **Step 2: Run tests and verify RED**

Run `pytest tests/test_throughput_instant_boot_static.py -k 'refresh_panel' -q`.

Expected: failures because the panel is absent.

- [ ] **Step 3: Build the compact panel and responsive styles**

Replace the header status label with a two-line compact panel. The primary line shows `Refreshing · 8.4s / ~12s` or `Updated 2m ago`. The secondary line shows `Reading 143 / 281 sessions · 132 cached · 11 parsed`. Use a subtle animated dot and no blocking overlay. On narrow screens, allow the secondary line to wrap without increasing the header controls' minimum width.

- [ ] **Step 4: Poll progress and atomically accept completion**

Start/join refresh after cached paint, poll status at 500 ms, and update elapsed locally every 100 ms. When status becomes `complete`, request the cache-only bootstrap with a generation cache-buster, validate it, store it, and apply it once. On `failed`, keep the existing dashboard and show the error state.

- [ ] **Step 5: Preserve manual refresh behavior**

The Refresh Sessions button starts or joins the same job; it never hides `dashboard-content` and never calls `showLoader` for aggregate scope. Individual-session selection may retain its loader.

- [ ] **Step 6: Run tests and verify GREEN**

Run `pytest tests/test_throughput_instant_boot_static.py -q`.

Expected: all tests pass.

- [ ] **Step 7: Commit the refresh UI slice**

Run `git commit --only static/throughput.html tests/test_throughput_instant_boot_static.py -m "feat(throughput): show live nonblocking refresh progress"`.

### Task 5: Performance and Visual Proof

**Files:**
- Create: `scripts/verify-throughput-instant.js`
- Modify: `tests/test_throughput_instant_boot_static.py`
- Create: `changelog.d/fixed-throughput-instant-final-view-2026-07-12.md`

**Interfaces:**
- Produces: a repeatable Puppeteer measurement and `throughput-first-paint.png`, `throughput-refreshed.png` artifacts (verification artifacts remain uncommitted).

- [ ] **Step 1: Add a failing performance invariant test**

The verifier seeds a representative valid bootstrap in `localStorage` with `page.evaluateOnNewDocument`, installs a `PerformanceObserver`, and records `performance.mark('throughput-bootstrap-rendered')` from `applyThroughputBootstrap`. It fails when the mark exceeds 100 ms after navigation start or any `/api/` request begins before the mark.

- [ ] **Step 2: Run the verifier and observe RED or capture the baseline**

Start the server on an unused local port, then run `node scripts/verify-throughput-instant.js`.

Expected before optimization: nonzero exit with the violated timing/order assertion.

- [ ] **Step 3: Optimize only measured boot bottlenecks**

Keep synchronous boot work to storage parse, validation, context assignment, and one dashboard render. Defer sessions, rankings, history, refresh start, and other fetches with `queueMicrotask` or `requestAnimationFrame` until after the render mark.

- [ ] **Step 4: Capture both visual states**

The verifier saves the viewport immediately after the cached render mark and again after refresh completion. Confirm both images show the same billing-period graph structure and reset markers; only values and refresh text may change.

- [ ] **Step 5: Add the changelog snippet**

Write:

```markdown
The throughput dashboard now restores its complete billing-period graph instantly from stale cache, refreshes atomically in the background with live timing and session progress, and never flashes the legacy graph.
```

- [ ] **Step 6: Run full verification**

Run:

```bash
python3 -m pytest tests/test_perf_budget.py tests/test_smoke.py tests/test_search_ui_static.py tests/test_throughput_instant_boot_static.py tests/test_throughput_reset_markers_static.py tests/test_throughput_chart_zoom_static.py tests/test_throughput_fable_contribution_static.py tests/test_throughput_engine_tabs_static.py tests/test_throughput_weekly_banner_static.py -q
python3 -m pytest tests/test_smoke.py -q
node scripts/verify-throughput-instant.js
git diff --check -- server.py static/throughput.html tests scripts/verify-throughput-instant.js changelog.d/fixed-throughput-instant-final-view-2026-07-12.md
```

Expected: zero pytest failures, Puppeteer reports cached render below 100 ms and no pre-render API requests, both screenshots exist, and `git diff --check` exits zero.

- [ ] **Step 7: Commit the verified user-visible slice**

Run:

```bash
git add scripts/verify-throughput-instant.js changelog.d/fixed-throughput-instant-final-view-2026-07-12.md
git commit --only server.py static/throughput.html tests/test_perf_budget.py tests/test_smoke.py tests/test_search_ui_static.py tests/test_throughput_instant_boot_static.py scripts/verify-throughput-instant.js changelog.d/fixed-throughput-instant-final-view-2026-07-12.md -m "fix(throughput): eliminate legacy graph flash"
```
