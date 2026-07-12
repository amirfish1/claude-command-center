# Throughput Combined Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a zero-extra-scan Combined dashboard, correct quota/cache semantics, expose resets, and replace empty forecast days with a compact meaningful timeline.

**Architecture:** Compose validated Claude and Codex bootstraps in the browser and preserve both component summaries inside the composed model. Existing single-engine server APIs remain unchanged; Combined refresh coordinates the two existing single-flight jobs. Chart projection consumes component summaries for separate normalized quota lines while activity buckets are merged.

**Tech Stack:** Single-file HTML/CSS/JavaScript, existing stdlib Python APIs, Python static-contract tests, Puppeteer/Chromium verification.

## Global Constraints

- Combined must not trigger a third transcript scan.
- Claude and Codex reset windows and quota percentages remain independent.
- Cached single-engine paint remains below 100 ms.
- Empty auxiliary sections never remain visible.
- Add no runtime dependencies.

---

### Task 1: Correct metrics, model contributions, resets, and empty states

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_instant_boot_static.py`
- Modify: `tests/test_throughput_weekly_banner_static.py`

- [ ] Write failing contracts for `% / day`, fresh input plus adjusted total, normalized model contributions, explicit reset labels, and hidden empty auxiliary sections.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the three-card projection, model contribution normalization, reset row, and hide-on-empty behavior.
- [ ] Run focused tests and JavaScript parsing.

### Task 2: Browser-composed Combined mode

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_instant_boot_static.py`

- [ ] Write failing contracts for the Combined tab, two-bootstrap composition, merged hourly summaries, and two-job refresh coordination.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement bootstrap composition, cached/server boot, refresh coordination, combined metrics, and no-op combined session archive.
- [ ] Verify Combined uses existing Claude/Codex endpoints only.

### Task 3: Compressed forecast and separate Combined quota lines

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_throughput_chart_zoom_static.py`
- Modify: `tests/test_throughput_weekly_banner_static.py`

- [ ] Write failing contracts for two previous-cycle days, forecast-to-100 truncation, compressed-tail marker, and separate Combined quota series.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the compact slot planner and component-normalized lines.
- [ ] Run all throughput tests, full selected repository suite, JavaScript parsing, and Chromium screenshots for all three modes.
