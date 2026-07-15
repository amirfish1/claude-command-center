# Codex Compaction Recovery Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically continue a Codex task that strands at a context-compaction boundary, without duplicating writers or bypassing legitimate waits.

**Architecture:** App-server notifications latch a durable, conversation-keyed compaction episode. The existing singleton pending-input watcher evaluates armed episodes outside the notification lock and uses the normal per-thread write gate to interrupt and resume safely. Recovery state is exposed through existing status and synthetic-event surfaces.

**Tech Stack:** Python 3 standard library, Codex app-server JSON-RPC, `unittest`, CCC's single-file HTML client.

## Global Constraints

- Keep `server.py` stdlib-only.
- Preserve durable user-message FIFO order; recovery never enters that FIFO.
- Never recover across approval, question, limit/active flags, active tools, goal terminal states, explicit final output, or queued user messages.
- Use at most two recovery attempts per compaction episode.
- All recovery records and locks are keyed by conversation.
- API changes are additive only.

---

### Task 1: Recovery episode policy and notification latch

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: `_codex_compaction_recovery_note_item(state, method, params, now) -> None`
- Produces: `_codex_compaction_recovery_note_progress(state, method, params, turn_id, now) -> None`
- Produces: persisted `state["compaction_recovery"]`

- [ ] **Step 1: Write failing tests**

Add notification tests that send `item/started` and `item/completed` for a
`contextCompaction` item and assert one `waiting` episode with its item/turn id.
Add a later non-compaction item test and a later-turn test that assert the
episode becomes `recovered` or `suppressed` rather than remaining armed.

- [ ] **Step 2: Run tests and verify the missing-state failures**

Run:

```bash
python3 -m unittest \
  tests.test_smoke.TestCodexAppServer.test_codex_context_compaction_arms_recovery \
  tests.test_smoke.TestCodexAppServer.test_codex_post_compaction_progress_disarms_recovery -v
```

Expected: FAIL because `compaction_recovery` is absent.

- [ ] **Step 3: Implement the episode latch**

Add constants for grace, cooldown, and maximum attempts. Record the
`contextCompaction` item id and turn id once. Track later activity and final
agent output without starting recovery from inside the notification handler.
Add `compaction_recovery` to the persisted state whitelist and restore this
non-volatile field alongside coordination events.

- [ ] **Step 4: Run the focused tests**

Expected: both tests PASS.

### Task 2: Bounded watchdog evaluation and continuation

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `state["compaction_recovery"]` from Task 1
- Produces: `_run_codex_compaction_recovery_once(session_id, now=None) -> dict`
- Produces: `_run_codex_recovery_watchdog_once(now=None) -> list[dict]`
- Produces: `_codex_compaction_recovery_prompt(session_id) -> str`

- [ ] **Step 1: Write failing policy tests**

Cover grace holding, active tool holding, approval/flags suppression, queued
user-input suppression, goal terminal-state suppression, active-turn interrupt,
idle-turn resume, duplicate scan idempotency, cooldown, and exhaustion after
two failures. Mock app-server interrupt and normal Codex resume at their public
CCC boundaries.

- [ ] **Step 2: Run the policy tests and verify failures**

Run:

```bash
python3 -m unittest \
  tests.test_smoke.TestCodexCompactionRecovery -v
```

Expected: FAIL because the watchdog functions do not exist.

- [ ] **Step 3: Implement the watchdog**

Read and mutate recovery state under `_CODEX_APP_SERVER_LOCK`, then release the
lock before interrupt/resume RPCs. Check `_pending_resume_queue` before recovery
and use the existing `_codex_thread_turn_lock` indirectly through
`resume_session_codex(..., _from_queue=True)`. Record attempts before external
calls so concurrent scans cannot duplicate them. Call the watchdog once per
existing five-second resume-watcher pass.

- [ ] **Step 4: Run policy and pending-input tests**

Run:

```bash
python3 -m unittest \
  tests.test_smoke.TestCodexCompactionRecovery \
  tests.test_smoke.TestPendingInputs -v
```

Expected: PASS with recovery and FIFO behavior covered.

### Task 3: Recovery status and conversation diagnostics

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `state["compaction_recovery"]`
- Produces: additive `codex_compaction_recovery` session-status field
- Produces: `Recovery` sidecar activity and recovery coordination events

- [ ] **Step 1: Write failing status tests**

Assert that `interrupting` and `recovering` records produce tool `Recovery`,
detail “Recovering after compaction,” and a public recovery object. Assert that
synthetic coordination events map recovery kinds to stable readable text.

- [ ] **Step 2: Run tests and verify failures**

Run the named status tests with `python3 -m unittest ... -v`; expect missing
fields/text failures.

- [ ] **Step 3: Implement additive status exposure**

Extend the app-server public-state whitelist, activity fields, public status,
session-status response, and coordination text map. Do not change existing
response shapes or frontend state classification.

- [ ] **Step 4: Run focused status tests**

Expected: PASS.

### Task 4: Full and live end-to-end verification

**Files:**
- Create: `changelog.d/fixed-codex-compaction-recovery-2026-07-15.md`
- Modify only if a verified gap is found: `server.py`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: completed watchdog and public status
- Produces: verified real app-server recovery behavior

- [ ] **Step 1: Run static and focused verification**

```bash
python3 -m py_compile server.py
python3 -m unittest tests.test_smoke.TestCodexCompactionRecovery -v
```

- [ ] **Step 2: Run the full smoke suite**

```bash
python3 -m unittest tests.test_smoke -v
```

Record exact failures and distinguish pre-existing shared-main failures from
watchdog regressions.

- [ ] **Step 3: Restart CCC and run a disposable real Codex thread**

Create a disposable Codex session with a harmless multi-step prompt, compact it
through `/api/session/compact` while its turn is active, and poll
`/api/session-status` plus `/api/conversations/<sid>` until a recovery event and
a later turn appear without a new user message. Confirm only one recovery turn
starts and the thread reaches a terminal reply.

- [ ] **Step 4: Add the changelog snippet and commit owned paths**

The snippet states that CCC now detects and resumes Codex work stranded after
context compaction while respecting user queues and blocking states. Commit
only the spec, plan, server, tests, and snippet paths with a conventional commit.
