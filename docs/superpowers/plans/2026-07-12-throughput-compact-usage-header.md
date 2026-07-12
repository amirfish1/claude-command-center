# Throughput Compact Usage Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the overlapping weekly usage banner with a compact, responsive single-row summary while preserving all meters, details, and reset controls.

**Architecture:** Keep the existing weekly-usage API and DOM update function. Reshape only the banner markup, CSS grid, and display-copy projection; detailed fields move to an accessible tooltip instead of being discarded.

**Tech Stack:** Single-file HTML/CSS/JavaScript, Python static-contract tests, Puppeteer visual verification.

## Global Constraints

- Preserve Claude, Fable, and Codex weekly percentages and gauges.
- Preserve reset recording and reset-marker behavior.
- Show no more than two visible status lines.
- Wrap without overlap at narrow viewport widths.
- Add no runtime dependencies.

---

### Task 1: Compact banner contract

**Files:**
- Modify: `tests/test_throughput_instant_boot_static.py`
- Modify: `static/throughput.html`

**Interfaces:**
- Consumes: existing `renderWeeklyUsage(d)` weekly usage payload.
- Produces: `weekly-sync-line`, `weekly-reset-line`, and `weekly-sub` tooltip detail.

- [ ] **Step 1: Write the failing static contract test**

Assert that the banner exposes the two compact line targets, uses product-only labels, and does not join all detail fields with `<br>`.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv/bin/python -m pytest tests/test_throughput_instant_boot_static.py -q`

Expected: FAIL because compact targets do not exist.

- [ ] **Step 3: Implement compact markup, copy, and responsive CSS**

Use a three-column grid (`meters`, `status`, `actions`), reduce banner padding and meter gauge width, populate only the sync and reset summary lines, and put the full detail string in `weekly-sub.title`.

- [ ] **Step 4: Verify focused contracts and JavaScript syntax**

Run: `.venv/bin/python -m pytest tests/test_throughput_instant_boot_static.py -q`

Run: `node -e "const fs=require('fs');const s=fs.readFileSync('static/throughput.html','utf8');for(const m of s.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g))new Function(m[1])"`

Expected: all tests pass and JavaScript parses.

- [ ] **Step 5: Verify layout and complete the throughput change**

Run: `node scripts/verify-throughput-instant.js`

Expected: cached render below 100 ms, zero API requests before render, and at least two reset markers. Inspect both output screenshots for banner collisions.
