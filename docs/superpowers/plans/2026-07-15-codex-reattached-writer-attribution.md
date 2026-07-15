# Codex Reattached Writer Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop CCC from claiming that its own reattached Codex turn belongs to another writer while preserving FIFO single-writer safety.

**Architecture:** Keep the existing writer gate and durable queue. Change only ownership attribution and user-facing coordination copy: unproven ownership becomes `unknown`, while desktop ownership still requires positive attachment evidence.

**Tech Stack:** Python standard library, `unittest`, single-file JavaScript UI.

## Global Constraints

- Do not weaken the single-writer gate or change FIFO ordering.
- Do not persist volatile active-turn ownership across server restarts.
- Do not add runtime dependencies.

---

### Task 1: Attribute reattached active turns safely

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `_codex_app_server_handle_notification(method, params)` and `_codex_thread_writer_snapshot(session_id, ...)`.
- Produces: `active_writer == "unknown"` when no process ownership is proven; `external_active` remains true so the FIFO gate still queues.

- [ ] **Step 1: Write the failing tests**

Update the turn-notification ownership test to expect `unknown` without
`ccc_turn_start_pending`, and add an active-status snapshot case that expects
`writer == "unknown"` with `external_active == True` when no desktop rollout is
attached.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_smoke.SmokeTests.test_codex_app_server_turn_started_tracks_writer_ownership tests.test_smoke.SmokeTests.test_codex_thread_writer_snapshot_attribution -v
```

Expected: failure because the implementation returns `external`.

- [ ] **Step 3: Implement minimal attribution changes**

Set unproven `turn/started` ownership to `unknown`. In the writer snapshot,
preserve positive desktop detection and otherwise return `unknown` for an
authoritative active state whose owner is not CCC.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2. Expected: both tests pass.

### Task 2: Make coordination copy evidence-based

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: persisted coordination events, including legacy `writer == "external"` values.
- Produces: neutral rendered copy for unknown/legacy-unproven ownership and queued messages.

- [ ] **Step 1: Write a failing copy test**

Create coordination events for `external_turn_started` and `input_queued`, then
assert rendered text says “Active Codex turn detected” and “Message queued
behind the active turn.”

- [ ] **Step 2: Run the copy test to verify it fails**

Run the new test directly with `python3 -m unittest ... -v`. Expected: failure
on the old “another writer” copy.

- [ ] **Step 3: Implement neutral copy**

Update `_CODEX_COORD_EVENT_TEXT`, dynamic coordination rendering, and the
unknown writer-gate response. Keep the more specific desktop and concurrent CCC
messages.

- [ ] **Step 4: Run focused coordination and queue tests**

Run the new copy test plus the existing writer-gate and FIFO queue tests.
Expected: all pass.

### Task 3: Verify and commit

**Files:**
- Modify: `server.py`
- Modify: `tests/test_smoke.py`
- Create: `changelog.d/fixed-codex-writer-attribution-2026-07-15.md`

- [ ] **Step 1: Run the focused Codex coordination test group**

Run the relevant `unittest` methods for writer attribution, app-server turn
ownership, queue gating, and coordination events. Expected: all pass.

- [ ] **Step 2: Run smoke verification**

```bash
python3 -m unittest tests.test_smoke -v
```

Record any unrelated pre-existing failures without modifying their paths.

- [ ] **Step 3: Add the changelog snippet**

Add one `fixed` bullet describing accurate Codex writer attribution after a CCC
restart.

- [ ] **Step 4: Commit only owned paths**

Commit the specific hunks in `server.py` and `tests/test_smoke.py`, the
changelog snippet, and these design documents with Conventional Commit
messages. Do not stage unrelated shared-worktree changes.
