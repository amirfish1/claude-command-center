# Codex Conversation Queue Pump Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every durably queued Codex input immediately push its conversation's FIFO queue forward, with app-server lifecycle events driving subsequent messages.

**Architecture:** Add a non-blocking per-conversation pump that peeks at the durable queue head, starts it only when the thread is idle, and removes it only after acceptance. Enqueue, `turn/completed`, idle `thread/status/changed`, and the recovery watcher all invoke this same pump; explicit Steer remains separate.

**Tech Stack:** Python 3.12, stdlib `threading`, existing Codex app-server JSON-RPC integration, `unittest`/`mock`.

## Global Constraints

- Preserve strict FIFO order within each conversation.
- Never send two ordinary queued messages concurrently to one conversation.
- Ordinary input waits behind an active turn; only explicit **Steer** calls `turn/steer`.
- Keep `pending-inputs.json`; SQLite migration is out of scope.
- Keep `server.py` stdlib-only.
- The periodic watcher is recovery-only and must call the same pump.

---

### Task 1: Conversation-scoped FIFO pump

**Files:**
- Modify: `server.py:25765-25780, 26463-26535, 27142-27165, 27412-27505`
- Test: `tests/test_smoke.py:13120-13245`

**Interfaces:**
- Produces: `_schedule_codex_queue_pump(session_id: str) -> None`
- Produces: `_pump_codex_resume_queue(session_id: str) -> dict`
- Extends: `resume_session_codex(session_id, text, *, steer=False, _from_queue=False)`
- Consumes: `_pending_resume_queue`, `_pending_resume_retry_due`, `_resume_queue_engine_busy`, `_save_pending_inputs`

- [ ] **Step 1: Write failing pump tests**

Add focused tests to `TestPendingInputs` proving enqueue scheduling, FIFO head delivery, active-turn holding, failure retention, and concurrent suppression:

```python
def test_queue_codex_resume_schedules_conversation_pump(self):
    with mock.patch.object(self.server, "_schedule_codex_queue_pump") as schedule:
        self.server._queue_codex_resume("sid-a", "first")
    schedule.assert_called_once_with("sid-a")

def test_codex_queue_pump_delivers_and_removes_only_fifo_head(self):
    sid = "sid-fifo"
    with self.server._pending_resume_lock:
        self.server._pending_resume_queue[sid] = ["first", "second"]
    with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
         mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=False), \
         mock.patch.object(self.server, "resume_session_codex", return_value={"ok": True, "accepted": True}) as resume:
        result = self.server._pump_codex_resume_queue(sid)
    self.assertTrue(result["delivered"])
    resume.assert_called_once_with(sid, "first", _from_queue=True)
    with self.server._pending_resume_lock:
        self.assertEqual(self.server._pending_resume_queue[sid], ["second"])

def test_codex_queue_pump_holds_while_turn_is_active(self):
    sid = "sid-active"
    with self.server._pending_resume_lock:
        self.server._pending_resume_queue[sid] = ["wait"]
    with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
         mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=True), \
         mock.patch.object(self.server, "resume_session_codex") as resume:
        result = self.server._pump_codex_resume_queue(sid)
    self.assertEqual(result["waiting"], "busy")
    resume.assert_not_called()

def test_codex_queue_pump_retains_head_after_delivery_failure(self):
    sid = "sid-failure"
    with self.server._pending_resume_lock:
        self.server._pending_resume_queue[sid] = ["keep"]
    with mock.patch.object(self.server, "_pending_resume_retry_due", return_value=True), \
         mock.patch.object(self.server, "_resume_queue_engine_busy", return_value=False), \
         mock.patch.object(self.server, "resume_session_codex", return_value={"ok": False}):
        self.server._pump_codex_resume_queue(sid)
    with self.server._pending_resume_lock:
        self.assertEqual(self.server._pending_resume_queue[sid], ["keep"])

def test_codex_queue_pump_suppresses_concurrent_delivery(self):
    sid = "sid-concurrent"
    lock = self.server._codex_queue_pump_lock(sid)
    lock.acquire()
    try:
        result = self.server._pump_codex_resume_queue(sid)
    finally:
        lock.release()
    self.assertEqual(result["waiting"], "already-pumping")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestPendingInputs.test_queue_codex_resume_schedules_conversation_pump \
  tests.test_smoke.TestPendingInputs.test_codex_queue_pump_delivers_and_removes_only_fifo_head \
  tests.test_smoke.TestPendingInputs.test_codex_queue_pump_holds_while_turn_is_active \
  tests.test_smoke.TestPendingInputs.test_codex_queue_pump_retains_head_after_delivery_failure \
  tests.test_smoke.TestPendingInputs.test_codex_queue_pump_suppresses_concurrent_delivery
```

Expected: FAIL because the pump interfaces and enqueue trigger do not exist.

- [ ] **Step 3: Implement the minimal pump**

Add a guarded lock map and pump in `server.py`:

```python
_codex_queue_pump_locks = {}
_codex_queue_pump_locks_guard = threading.Lock()

def _codex_queue_pump_lock(session_id):
    with _codex_queue_pump_locks_guard:
        return _codex_queue_pump_locks.setdefault(session_id, threading.Lock())

def _schedule_codex_queue_pump(session_id):
    if not session_id:
        return
    threading.Thread(target=_pump_codex_resume_queue, args=(session_id,), daemon=True,
                     name=f"codex-queue-pump-{session_id[:8]}").start()

def _pump_codex_resume_queue(session_id):
    lock = _codex_queue_pump_lock(session_id)
    if not lock.acquire(blocking=False):
        return {"ok": True, "waiting": "already-pumping"}
    try:
        if not _pending_resume_retry_due(session_id):
            return {"ok": True, "waiting": "backoff"}
        if _resume_queue_engine_busy(session_id):
            return {"ok": True, "waiting": "busy"}
        with _pending_resume_lock:
            queue = _pending_resume_queue.get(session_id) or []
            text = queue[0] if queue else None
        if text is None:
            return {"ok": True, "empty": True}
        result = resume_session_codex(session_id, text, _from_queue=True)
        if not result or not result.get("ok") or result.get("queued"):
            _mark_pending_resume_retry(session_id)
            return {"ok": False, "delivered": False, "result": result}
        removed = False
        with _pending_resume_lock:
            queue = _pending_resume_queue.get(session_id) or []
            if queue and queue[0] == text:
                queue.pop(0)
                removed = True
                if not queue:
                    _pending_resume_queue.pop(session_id, None)
        if removed:
            _save_pending_inputs()
        _pending_resume_retry_after.pop(session_id, None)
        return {"ok": True, "delivered": removed, "result": result}
    finally:
        lock.release()
```

Call `_schedule_codex_queue_pump(session_id)` immediately after `_save_pending_inputs()` in `_queue_codex_resume`.

Extend `resume_session_codex` with `_from_queue=False`. When `_from_queue` is true, skip the existing-queue append guard and return a queued result without appending again if an active-writer race is detected.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: five tests pass.

- [ ] **Step 5: Commit the pump slice**

```bash
git add server.py tests/test_smoke.py
git commit -m "fix(codex): pump queued input on enqueue"
```

---

### Task 2: App-server lifecycle triggers and watcher reconciliation

**Files:**
- Modify: `server.py:19980-20132, 26463-26535`
- Test: `tests/test_smoke.py:9706-10085, 13120-13245`

**Interfaces:**
- Consumes: `_schedule_codex_queue_pump(session_id)` from Task 1
- Changes: `_codex_app_server_handle_notification(method, params)` schedules only after idle transitions
- Changes: `_start_resume_queue_watcher()` delegates Codex queue heads to `_pump_codex_resume_queue`

- [ ] **Step 1: Write failing lifecycle-trigger tests**

```python
def test_codex_turn_completed_schedules_queue_pump(self):
    server = self.server
    sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
    with mock.patch.object(server, "_schedule_codex_queue_pump") as schedule:
        server._codex_app_server_handle_message({
            "method": "turn/completed",
            "params": {"threadId": sid, "turnId": "turn-1"},
        })
    schedule.assert_called_once_with(sid)

def test_codex_idle_status_schedules_queue_pump(self):
    server = self.server
    sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
    with mock.patch.object(server, "_schedule_codex_queue_pump") as schedule:
        server._codex_app_server_handle_message({
            "method": "thread/status/changed",
            "params": {"threadId": sid, "status": {"type": "idle", "activeFlags": []}},
        })
    schedule.assert_called_once_with(sid)
```

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestRepoContextHelpers.test_codex_turn_completed_schedules_queue_pump \
  tests.test_smoke.TestRepoContextHelpers.test_codex_idle_status_schedules_queue_pump
```

Expected: FAIL because notifications update state but do not schedule delivery.

- [ ] **Step 3: Schedule pumps from idle lifecycle notifications**

In `_codex_app_server_handle_notification`, set a local `pump_after_notification` flag for `turn/completed` and idle `thread/status/changed`. After state persistence and telemetry recording, call `_schedule_codex_queue_pump(thread_id)`. The scheduler creates a new thread, so app-server handling never performs a nested JSON-RPC request while holding `_CODEX_APP_SERVER_LOCK`.

In the recovery watcher, detect Codex sessions before popping the queue:

```python
if _is_codex_session(sid):
    _pump_codex_resume_queue(sid)
    continue
```

Other engines retain their existing watcher delivery behavior.

- [ ] **Step 4: Run focused queue and notification tests**

```bash
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestPendingInputs \
  tests.test_smoke.TestRepoContextHelpers.test_codex_app_server_tracks_notifications \
  tests.test_smoke.TestRepoContextHelpers.test_codex_turn_completed_schedules_queue_pump \
  tests.test_smoke.TestRepoContextHelpers.test_codex_idle_status_schedules_queue_pump \
  tests.test_smoke.TestRepoContextHelpers.test_resume_codex_preserves_existing_queue_order
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit lifecycle integration**

```bash
git add server.py tests/test_smoke.py
git commit -m "fix(codex): advance queue from turn events"
```

---

### Task 3: Recovery verification and changelog

**Files:**
- Create: `changelog.d/fixed-codex-conversation-queue-pump-2026-07-14.md`
- Verify: `server.py`, `tests/test_smoke.py`

**Interfaces:**
- Verifies the public behavior produced by Tasks 1 and 2.

- [ ] **Step 1: Add the changelog snippet**

```markdown
Queued Codex messages now advance immediately on new input and turn completion while preserving per-conversation FIFO order; the periodic watcher is retained only for recovery.
```

- [ ] **Step 2: Run final focused verification**

```bash
/opt/homebrew/bin/python3.12 -m unittest tests.test_smoke.TestPendingInputs
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestRepoContextHelpers.test_codex_app_server_tracks_notifications \
  tests.test_smoke.TestRepoContextHelpers.test_resume_codex_active_app_server_turn_uses_durable_queue \
  tests.test_smoke.TestRepoContextHelpers.test_resume_codex_preserves_existing_queue_order
git diff --check
```

Expected: all selected tests pass and `git diff --check` prints nothing.

- [ ] **Step 3: Restart CCC and verify the real stuck conversation**

```bash
curl -sS -X POST http://127.0.0.1:8090/api/restart \
  -H 'Origin: http://127.0.0.1:8090' \
  -H 'Content-Type: application/json' -d '{}'
```

After the server is healthy, inspect `/api/conversations/019f5f35-2359-7740-84c2-202661cabd92` and require the pending list to shrink in FIFO order. Do not claim success from process health alone.

- [ ] **Step 4: Commit the verified user-visible fix**

```bash
git add changelog.d/fixed-codex-conversation-queue-pump-2026-07-14.md
git commit -m "docs(changelog): note Codex queue pump fix"
```
