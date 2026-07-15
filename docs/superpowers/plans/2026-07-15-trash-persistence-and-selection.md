# Trash Persistence and Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Trash durable in every All-tab lane and advance the open conversation after a successful Trash action.

**Architecture:** Protect the two lifecycle sidecar files with one re-entrant server lock and repair the persisted `trashed => archived` invariant whenever lifecycle state is loaded. In the client, use one visible-row neighbor helper for Archive and Trash, independent of lane or grouping.

**Tech Stack:** Python stdlib HTTP server, vanilla JavaScript, pytest, Puppeteer.

## Global Constraints

- `trashed => archived` is mandatory in storage, API rows, and UI classification.
- All → Coding, Workers, and Messages use the same Trash transition.
- Only trashing the currently open conversation changes selection.
- Preserve the stdlib-only server and single-file frontend architecture.

---

### Task 1: Durable conversation lifecycle state

**Files:**
- Modify: `server.py:9645-10720`
- Test: `tests/test_conversation_lifecycle.py`

**Interfaces:**
- Consumes: `ARCHIVED_CONVERSATIONS_FILE`, `TRASHED_CONVERSATIONS_FILE`
- Produces: `_conversation_lifecycle_lock: threading.RLock`, `_load_conversation_lifecycle_state() -> tuple[list[str], list[str]]`

- [ ] **Step 1: Write failing invariant and concurrency tests**

Add tests which seed a trashed-only session and assert the lifecycle loader repairs the archive file, then run two synchronized `_set_conversation_trashed(..., True)` calls and assert neither ID is lost:

```python
def test_lifecycle_load_repairs_trashed_without_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    server._save_archived_conversations([])
    server._save_trashed_conversations(["worker-a"])
    archived, trashed = server._load_conversation_lifecycle_state()
    assert archived == ["worker-a"]
    assert trashed == ["worker-a"]
    assert server._load_archived_conversations(sweep=False) == ["worker-a"]


def test_parallel_trash_operations_do_not_lose_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_save_archive_grace", lambda: None)
    monkeypatch.setattr(server, "_kill_session_by_id", lambda sid: {"ok": True})
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda sid: server._set_conversation_trashed(sid, True), ["worker-a", "worker-b"]))
    assert set(server._load_archived_conversations(sweep=False)) == {"worker-a", "worker-b"}
    assert set(server._load_trashed_conversations()) == {"worker-a", "worker-b"}
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py -q`

Expected: FAIL because `_load_conversation_lifecycle_state` does not exist and concurrent writes can lose an ID.

- [ ] **Step 3: Implement the locked lifecycle loader and transitions**

Add a module-level `threading.RLock`. Under that lock, load both files, append every missing trashed ID to the archive list, persist repairs, and return both lists. Hold the same lock around the complete read-modify-write body of `_set_conversation_trashed`, single archive, and bulk archive transitions. Ensure row builders report `archived = sid in archived_set or sid in trashed_set`.

- [ ] **Step 4: Run lifecycle tests**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py tests/test_sidebar_lifecycle_static.py tests/test_all_lane_overrides.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the server slice**

```bash
git add server.py tests/test_conversation_lifecycle.py
git commit -m "fix(lifecycle): preserve concurrent trash state"
```

### Task 2: Advance selection after Trash in every All lane

**Files:**
- Modify: `static/app.js:28270-28420`
- Modify: `scripts/verify-conversation-lifecycle.js`
- Modify: `tests/test_sidebar_lifecycle_static.py`
- Create: `changelog.d/fixed-trash-persistence-selection-2026-07-15.md`

**Interfaces:**
- Consumes: rendered `.conv-item[data-id]` order and `currentConversation`
- Produces: `_visibleConversationNeighborId(convId) -> string`

- [ ] **Step 1: Add failing source and browser assertions**

Assert the Trash handler captures a visible neighbor only when `currentConversation === convId`, selects it only after a successful `trashed: true` response, and that the browser verifier exercises a Workers-lane row.

- [ ] **Step 2: Run the assertions and verify failure**

Run: `python3 -m pytest tests/test_sidebar_lifecycle_static.py -q`

Expected: FAIL because the Trash handler does not select a neighbor.

- [ ] **Step 3: Implement one visual-order neighbor helper**

Add a helper that filters connected, visible `.conv-item[data-id]` elements, finds the target, and returns the next ID or previous ID. Use it in Archive and Trash. In Trash, capture before the request and call `selectConversation(nextId)` only after success, only for `wantTrashed`, and only when the trashed row was current.

- [ ] **Step 4: Verify all lanes and refresh behavior**

Run:

```bash
python3 -m pytest tests/test_conversation_lifecycle.py tests/test_sidebar_lifecycle_static.py tests/test_all_lane_overrides.py -q
node scripts/verify-conversation-lifecycle.js
node snapshot.js
```

Expected: pytest passes; browser verification reports success for Active, All/Coding, All/Workers, and Trash; `snapshot.png` is written without browser errors.

- [ ] **Step 5: Add the changelog and commit**

Use this changelog text:

```markdown
- Fixed Trash persistence across refresh, including Workers in All, and advance to the next visible conversation when trashing the open row.
```

Then commit:

```bash
git add static/app.js scripts/verify-conversation-lifecycle.js tests/test_sidebar_lifecycle_static.py changelog.d/fixed-trash-persistence-selection-2026-07-15.md docs/superpowers/specs/2026-07-15-trash-advance-selection-design.md docs/superpowers/plans/2026-07-15-trash-persistence-and-selection.md
git commit -m "fix(ui): keep trashed workers out of all lanes"
```

### Task 3: Final verification

**Files:**
- Verify only

**Interfaces:**
- Consumes: Tasks 1 and 2
- Produces: evidence that storage, API, and UI agree after refresh

- [ ] **Step 1: Run focused and smoke suites**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py tests/test_sidebar_lifecycle_static.py tests/test_all_lane_overrides.py tests/test_smoke.py -q`

Expected: all tests pass.

- [ ] **Step 2: Confirm the worktree is scoped**

Run: `git status --short && git diff main...HEAD --stat`

Expected: only lifecycle, UI selection, verification, spec, plan, and changelog files appear.
