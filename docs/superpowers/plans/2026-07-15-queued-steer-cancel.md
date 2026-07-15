# Queued Steer Cancel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users durably cancel one queued composer message without disturbing the draft or later FIFO entries.

**Architecture:** Reuse the existing one-copy pending-input removal helper behind a dedicated POST endpoint. Add a Cancel action to each queued-steer row and update the DOM only after the endpoint confirms removal.

**Tech Stack:** Python standard library HTTP server, vanilla JavaScript/CSS, `unittest`.

## Global Constraints

- Remove at most one matching queued copy.
- Preserve the order and contents of every remaining queued message.
- Never report success when the entry is already being delivered or absent.
- Do not clear or modify the composer draft.

---

### Task 1: Server cancellation contract

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `_consume_matching_pending_input(session_id, text) -> int`.
- Produces: `POST /api/pending-input/cancel` with `{session_id, text}` and `{ok, cancelled}`.

- [ ] **Step 1: Write failing persistence and endpoint tests**

Add these assertions to `TestPendingInputs`:

```python
def test_consume_matching_pending_input_persists_cancel(self):
    sid = "cancel-session"
    with self.server._pending_resume_lock:
        self.server._pending_resume_queue[sid] = ["cancel me", "keep me"]
    with mock.patch.object(self.server, "_save_pending_inputs") as save:
        removed = self.server._consume_matching_pending_input(sid, "cancel me")
    self.assertEqual(removed, 1)
    self.assertEqual(self.server._pending_resume_queue[sid], ["keep me"])
    save.assert_called_once_with()

def test_pending_input_cancel_endpoint_is_wired(self):
    source = inspect.getsource(self.server.CommandCenterHandler.do_POST)
    self.assertIn('path == "/api/pending-input/cancel"', source)
    self.assertIn("_consume_matching_pending_input(sid, text)", source)
    self.assertIn('"cancelled": 1', source)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m unittest tests.test_smoke.TestPendingInputs.test_consume_matching_pending_input_persists_cancel tests.test_smoke.TestPendingInputs.test_pending_input_cancel_endpoint_is_wired -v
```

Expected: the persistence test passes through existing behavior and the endpoint
test fails because the route is absent.

- [ ] **Step 3: Implement the endpoint**

Add a branch before `/api/inject-input`:

```python
elif path == "/api/pending-input/cancel":
    length = int(self.headers.get("Content-Length", "0"))
    body = self.rfile.read(length) if length > 0 else b""
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}
    sid = str(payload.get("session_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not sid or not text:
        self.send_json({"ok": False, "error": "session_id and text required"}, 400)
    elif _consume_matching_pending_input(sid, text):
        self.send_json({"ok": True, "cancelled": 1, "session_id": sid})
    else:
        self.send_json({"ok": False, "cancelled": 0, "error": "queued message no longer exists"}, 409)
```

- [ ] **Step 4: Run the focused server tests**

Run the command from Step 2. Expected: both tests pass.

### Task 2: Composer Cancel action

**Files:**
- Modify: `static/app.js`
- Modify: `static/app.css`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: queued row `data-session-id` and `.user-msg[data-raw-text]`.
- Produces: `[data-cancel-queued-message]` calling `/api/pending-input/cancel`.

- [ ] **Step 1: Extend the queued-steer test**

Add exact assertions to `test_queued_steer_candidates_stay_above_the_composer`:

```python
self.assertIn("data-cancel-queued-message", app_js)
self.assertIn("el.appendChild(cancel)", app_js)
cancel_handler = app_js[
    app_js.index("const btn = ev.target.closest('[data-cancel-queued-message]')"):
    app_js.index("const btn = ev.target.closest('[data-steer-queued-message]')")
]
self.assertIn("'/api/pending-input/cancel'", cancel_handler)
self.assertIn("if (row && row._pendingRef) removePendingSendEcho(row._pendingRef)", cancel_handler)
self.assertIn("else if (row) row.remove()", cancel_handler)
self.assertIn(".cancel-queued-message", app_css)
```

- [ ] **Step 2: Run the UI test to verify it fails**

```bash
python3 -m unittest tests.test_smoke.TestRepoContextHelpers.test_queued_steer_candidates_stay_above_the_composer -v
```

Expected: failure because `data-cancel-queued-message` is absent.

- [ ] **Step 3: Add the Cancel control and delegated handler**

In `syncQueuedSteerTray`, create a `button.cancel-queued-message` with
`data-cancel-queued-message`, attach the session id, and append it before the
existing Steer button. Add a delegated handler that POSTs:

```javascript
await fetch('/api/pending-input/cancel', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session_id: sid, text }),
});
```

Disable both row buttons during the request. Remove the pending echo or row
only after an `ok` response; otherwise restore both buttons and show
`Cancel failed: …`.

- [ ] **Step 4: Add compact neutral styling**

Style `.cancel-queued-message` beside `.send-queued-steer` using the same
button height and typography, with neutral border/text colors and a hover
background. Position Cancel immediately left of Steer.

- [ ] **Step 5: Run focused UI verification**

```bash
python3 -m unittest tests.test_smoke.TestRepoContextHelpers.test_queued_steer_candidates_stay_above_the_composer -v
node --check static/app.js
```

Expected: test passes and `node --check` exits 0.

### Task 3: Verify and commit

**Files:**
- Create: `changelog.d/added-queued-message-cancel-2026-07-15.md`
- Modify: `server.py`
- Modify: `static/app.js`
- Modify: `static/app.css`
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Add the changelog snippet**

Create `changelog.d/added-queued-message-cancel-2026-07-15.md` containing:

```markdown
- Add a durable Cancel action to queued composer messages so they can be withdrawn without disturbing later FIFO entries.
```

- [ ] **Step 2: Run focused verification**

```bash
python3 -m unittest tests.test_smoke.TestPendingInputs.test_consume_matching_pending_input_removes_only_one_copy tests.test_smoke.TestPendingInputs.test_consume_matching_pending_input_persists_cancel tests.test_smoke.TestPendingInputs.test_pending_input_cancel_endpoint_is_wired tests.test_smoke.TestRepoContextHelpers.test_queued_steer_candidates_stay_above_the_composer -v
node --check static/app.js
```

Expected: four tests pass and JavaScript syntax is valid.

- [ ] **Step 3: Run the complete smoke suite**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: no new failures; record unrelated shared-worktree failures by exact
test id.

- [ ] **Step 4: Commit only owned paths and hunks**

Stage the design, plan, changelog, server route, UI controls, styles, and tests
without staging concurrent changes. Commit with:

```bash
git commit -m "feat(queue): cancel queued composer messages"
```

- [ ] **Step 5: Restart and verify CCC**

```bash
curl -fsS -X POST http://127.0.0.1:8090/api/restart -H 'Origin: http://127.0.0.1:8090' -H 'Content-Type: application/json' -d '{}'
curl -fsS --max-time 5 http://127.0.0.1:8090/api/health
```

Expected: restart is accepted and health returns JSON.
