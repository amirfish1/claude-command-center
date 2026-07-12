# Throughput Unified Engine Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Claude and Codex one sparse, consistent billing-period experience without duplicated quota/cost information or legacy aggregate rendering.

**Architecture:** Preserve the common quota banner and weekly bootstrap model. Move engine selection into the workspace, project both engines into one operational metric schema, and simplify the existing SVG renderer to one period-scoped aggregate path with a fixed percentage scale.

**Tech Stack:** Single-file HTML/CSS/JavaScript, Python static-contract tests, Puppeteer/Chromium visual verification.

## Global Constraints

- Preserve per-session analysis behavior.
- Preserve automatic and manual reset-marker interactions.
- Keep cached first paint below 100 ms.
- Add no runtime dependencies.

---

### Task 1: Unify page hierarchy and metrics

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_instant_boot_static.py`
- Modify: `tests/test_throughput_weekly_banner_static.py`

**Interfaces:**
- Consumes: existing `renderDashboard(session, data)` aggregate summary.
- Produces: `engine-switch-row` and a three-card aggregate metric projection.

- [ ] Write failing static assertions for toggle placement, hidden aggregate heading, three common metric labels, and absence of aggregate dollar copy.
- [ ] Run the focused tests and confirm they fail for the missing hierarchy.
- [ ] Move the tabs, hide the aggregate header, reduce the grid to three cards, and populate identical calls/tokens-per-day/cache-hit values for both engines.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Remove legacy aggregate rendering and fix graph semantics

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_weekly_banner_static.py`
- Modify: `tests/test_throughput_reset_markers_static.py`

**Interfaces:**
- Consumes: `weeklyChartContext(summary)` and period-scoped `sourceRows`.
- Produces: fixed `WEEKLY_AXIS_MAX = 100`, clamped `yPct`, and midnight divider labels.

- [ ] Write failing assertions for removal of the all-hours fallback, a 100% axis constant, overflow labels, and `00:00` divider labels.
- [ ] Run the focused tests and confirm they fail.
- [ ] Delete the unreachable six-hour fallback, clamp percentage coordinates to 100 while retaining true labels, and draw `00:00` at every day divider.
- [ ] Run static tests, JavaScript parsing, the Chromium instant-render verifier, and inspect Claude and Codex screenshots.
