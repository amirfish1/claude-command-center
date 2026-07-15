# New-session Queued-message Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a fresh-session composer from displaying a queued-steer tray owned by the previously open session.

**Architecture:** Keep queued-message rendering unchanged for real conversations. At the new-session lifecycle boundary, remove the active pane's persistent queued tray before rendering fresh-session controls, and verify the user-visible transition in Chromium.

**Tech Stack:** Vanilla JavaScript, Puppeteer 25, Python `unittest` smoke tests.

## Global Constraints

- Do not rebuild the composer or clear its draft and spawn controls.
- Keep cleanup pane-aware through `getConvInputBarForPane(paneId)`.
- Do not change queue delivery or steering behavior for existing sessions.

---

### Task 1: Browser regression

**Files:**
- Create: `scripts/verify-new-session-queue-isolation.js`
- Create: `tests/test_new_session_queue_isolation.py`

**Interfaces:**
- Consumes: `#sidebarNewBtn`, `.queued-steer-tray`, and `#convInputBar` from the dashboard.
- Produces: a zero-exit Puppeteer verifier proving the queued message is absent after the real new-session lifecycle runs.

- [ ] **Step 1: Write the failing browser verifier**

Create a Puppeteer script that loads `http://127.0.0.1:8090`, switches the dashboard to list view, mounts a uniquely marked queued tray in the active composer, clicks `#sidebarNewBtn`, and throws unless the dashboard enters new-session mode with no `.queued-steer-tray` or marker text remaining.

- [ ] **Step 2: Add static harness coverage**

Assert that the verifier uses the repo's `require-puppeteer.js`, clicks the real New session control, and checks both the tray selector and unique marker.

- [ ] **Step 3: Run the verifier to prove RED**

Run: `node scripts/verify-new-session-queue-isolation.js`

Expected: non-zero exit with the stale queued-tray assertion because `enterNewSessionMode()` does not yet remove it.

### Task 2: Lifecycle cleanup

**Files:**
- Modify: `static/app.js` in `enterNewSessionMode()`
- Test: `tests/test_new_session_queue_isolation.py`

**Interfaces:**
- Consumes: `paneId` and `getConvInputBarForPane(paneId)`.
- Produces: removal of the active pane's `.queued-steer-tray` before new-session rendering.

- [ ] **Step 1: Add the minimal cleanup**

Immediately after resolving `paneId`, find the active pane's queued tray and remove it if present:

```js
const staleQueuedTray = getConvInputBarForPane(paneId)?.querySelector('.queued-steer-tray');
if (staleQueuedTray) staleQueuedTray.remove();
```

- [ ] **Step 2: Run focused verification to prove GREEN**

Run: `python3 -m unittest tests.test_new_session_queue_isolation -v`

Expected: all static harness assertions pass.

Run: `node scripts/verify-new-session-queue-isolation.js`

Expected: `PASS new-session composer clears queued messages from the previous session` and exit zero.

### Task 3: Release evidence

**Files:**
- Create: `changelog.d/fixed-new-session-queue-isolation-2026-07-15.md`

**Interfaces:**
- Consumes: the completed lifecycle fix and verifier.
- Produces: public release-note coverage and a scoped commit on `main`.

- [ ] **Step 1: Add the changelog snippet**

Record that opening New session no longer exposes queued messages from the previously viewed session.

- [ ] **Step 2: Run repository verification**

Run: `python3 -m unittest tests.test_new_session_queue_isolation -v`

Run: `python3 -m pytest tests/test_smoke.py -q`

Run: `node scripts/verify-new-session-queue-isolation.js`

Run: `git diff --check`

Expected: every command exits zero.

- [ ] **Step 3: Commit and push**

Commit only the implementation plan, verifier, focused test, `static/app.js`, and changelog snippet with `fix(ui): isolate new session queued messages`, then push `main` to `origin`.
