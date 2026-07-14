# Conversation List Performance Salvage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an additive, cache-backed sidebar list API without losing current lifecycle or renderer behavior.

**Architecture:** `server.py` projects the existing warm archive rows through an audited allowlist and filters by window. `static/app.js` uses the additive endpoint and tracks the loaded window so a search can widen to all history.

**Tech Stack:** Python stdlib HTTP server, pytest, vanilla browser JavaScript, Puppeteer.

## Global Constraints

- `/api/conversations/all` response shape and behavior remain unchanged.
- Preserve archived, trashed, pinned, All-lane, engine, session, grouping, and current lifecycle metadata.
- Reuse `_archive_all_rows_cached()` rather than starting a second archive scan.
- Valid windows are `1d`, `7d`, and `all`; active search widens to all history.
- Add a changelog snippet; do not touch main or the stale reference worktree.

---

### Task 1: Specify and prove the server projection

**Files:**
- Modify: `tests/test_perf_budget.py`
- Modify: `server.py`

- [ ] **Step 1: Write failing projection tests**

Add rows containing current renderer fields (`archived`, `trashed`, `pinned`,
`all_lane_override`, state/goal/lineage/status metadata) plus `jsonl_path` and
large `last_assistant_text`. Assert projected rows retain the renderer fields,
omit bulky fields, apply `1d`/`7d`/`all`, and use the warm cache helper.

- [ ] **Step 2: Verify the tests fail**

Run: `pytest -q tests/test_perf_budget.py -k archive_list`

- [ ] **Step 3: Implement the narrow helper API**

Add `_archive_list_window`, `_archive_list_window_cutoff`,
`_archive_list_project_rows`, and `_archive_list_rows_cached`; each list route
response contains `ok`, `conversations`, `count`, `total_count`, `window`,
and `fields`.

- [ ] **Step 4: Verify the focused server tests pass**

Run: `pytest -q tests/test_perf_budget.py -k archive_list`

### Task 2: Route and client integration

**Files:**
- Modify: `tests/test_perf_budget.py`
- Modify: `tests/test_sidebar_window_invariants.py`
- Modify: `server.py`
- Modify: `static/app.js`

- [ ] **Step 1: Write failing route/client tests**

Assert `/api/conversations/list` invokes the cache-backed projection, `/all`
remains present, and archive loading targets the list endpoint with `window`.
Assert changing a window refreshes the list and non-empty search widens to
`all`.

- [ ] **Step 2: Verify the tests fail**

Run: `pytest -q tests/test_perf_budget.py tests/test_sidebar_window_invariants.py -k 'archive_list or list_endpoint or search_widens'`

- [ ] **Step 3: Implement the additive route and window-aware loader**

Register `/api/conversations/list` before `/api/conversations/all`, preserve
cache options and ETags, then update the sidebar loader and window controls to
request and track the selected window.

- [ ] **Step 4: Verify focused tests and lifecycle tests pass**

Run: `pytest -q tests/test_perf_budget.py tests/test_sidebar_window_invariants.py tests/test_conversation_lifecycle.py tests/test_sidebar_lifecycle_static.py`

### Task 3: Verify and document the user-facing change

**Files:**
- Create: `changelog.d/fixed-conversation-list-payload-2026-07-14.md`

- [ ] **Step 1: Add the changelog snippet**

Describe the lightweight sidebar archive fetch and preserved lifecycle state.

- [ ] **Step 2: Run verification**

Run focused pytest suites, `node --check static/app.js`, `node snapshot.js`,
and `pytest -q`.

- [ ] **Step 3: Commit the completed implementation**

Commit only changed feature, test, changelog, and process-document paths with
a conventional `fix(perf)` subject.
