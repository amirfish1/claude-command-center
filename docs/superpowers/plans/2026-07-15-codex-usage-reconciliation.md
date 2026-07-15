# Codex Usage Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize current Codex quota data and add authoritative account-wide daily totals to the throughput graph without replacing rollout detail.

**Architecture:** Server helpers normalize the two app-server account responses into stable public shapes. Background Codex aggregate refreshes attach account usage to the existing throughput summary, and the browser renders it as a day-aligned reconciliation strip while leaving the detailed bars unchanged.

**Tech Stack:** Python standard library, Codex app-server JSON-RPC, vanilla JavaScript/SVG, pytest, Puppeteer.

## Global Constraints

- Keep `server.py` standard-library-only.
- Preserve existing `/api/usage/current` fields and throughput bootstrap schema compatibility.
- Never expose opaque limit identifiers, credit balances, or unvalidated account fields.
- Account API failures must not fail throughput refreshes.
- Preserve rollout-derived session/model/hourly detail.

---

### Task 1: Normalize Codex account quota and usage responses

**Files:**
- Modify: `server.py`
- Test: `tests/test_codex_account_usage.py`

**Interfaces:**
- Produces: `_codex_usage_from_account_rate_limits(response, now_epoch=None)` returning the existing Codex usage shape or `None`.
- Produces: `_codex_account_usage_from_response(response, now_epoch=None)` returning a sanitized account-usage payload or `None`.

- [ ] Write tests for a 10,080-minute `primary` window, a 300-minute session window, model-scoped bucket exclusion, and malformed fields.
- [ ] Run the focused tests and verify they fail because the normalizers do not exist.
- [ ] Implement duration-based window classification and account-usage sanitization.
- [ ] Run the focused tests and verify they pass.

### Task 2: Use current account contracts with safe fallbacks

**Files:**
- Modify: `server.py`
- Test: `tests/test_codex_account_usage.py`

**Interfaces:**
- Consumes: Task 1 normalizers.
- Produces: `_read_codex_usage()` preferring `account/rateLimits/read` and falling back to rollout events.
- Produces: `_read_codex_account_usage()` reading `account/usage/read` without raising.

- [ ] Write failing tests for app-server response preference and rollout fallback selection.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement app-server reads, base-bucket rollout filtering, and fallback behavior.
- [ ] Run the focused tests and verify they pass.

### Task 3: Attach account usage to Codex aggregate bootstraps

**Files:**
- Modify: `server.py`
- Test: `tests/test_codex_account_usage.py`

**Interfaces:**
- Consumes: `_read_codex_account_usage()`.
- Produces: `summary.account_usage` on successful Codex aggregate refreshes.

- [ ] Write a failing test for non-destructive attachment to an existing throughput summary.
- [ ] Run the focused test and verify it fails.
- [ ] Attach account data during the background Codex refresh and repersist the enriched aggregate snapshot.
- [ ] Run the focused tests and verify they pass.

### Task 4: Render daily reconciliation in the graph

**Files:**
- Modify: `static/throughput.html`
- Test: `tests/test_codex_account_usage.py`

**Interfaces:**
- Consumes: `summary.account_usage.daily` and existing hourly rows.
- Produces: a day-aligned SVG strip with account, local, and unattributed tooltip values.

- [ ] Add static contract assertions for account-usage propagation, strip rendering, and Combined-view preservation.
- [ ] Run the focused tests and verify they fail.
- [ ] Implement Combined-summary propagation and the SVG reconciliation strip.
- [ ] Run the focused tests and verify they pass.

### Task 5: Verify and deliver

**Files:**
- Modify: `changelog.d/fixed-codex-usage-reconciliation-2026-07-15.md`

- [ ] Run `python3 -m pytest tests/test_codex_account_usage.py -q`.
- [ ] Run `python3 -m pytest tests/test_smoke.py -q`.
- [ ] Run `python3 -m pytest tests/test_perf_budget.py -q` after smoke completes.
- [ ] Run the repo Puppeteer harness and inspect the Codex aggregate graph snapshot.
- [ ] Add the changelog snippet.
- [ ] Commit only the files changed for THROUGHPUT-27.
- [ ] Close THROUGHPUT-27 with a resolution summary and continue the queue loop.
