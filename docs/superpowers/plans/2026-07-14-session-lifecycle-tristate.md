# Tri-state Conversation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement distinct Active, Archived, and Trashed conversation states with tab-specific actions and a real Trash bucket.

**Architecture:** Preserve the compatible `archived` boolean and add a persisted `trashed` boolean with the invariant `trashed => archived`. Reuse the reviewed server behavior from `worktree-session-lifecycle-tristate` as reference, but port it onto current `main` rather than merging the stale branch. Make the client renderer explicitly context-aware (`active`, `all-main`, `trash`) so identical session state can expose different actions in Active and All.

**Tech Stack:** Python standard library (`server.py`), vanilla JavaScript/CSS (`static/app.js`, `static/app.css`), pytest, and the repository Puppeteer harness.

## Global Constraints

- `server.py` remains stdlib-only.
- `/api/*` changes are additive; existing archive endpoints and response fields remain compatible.
- The canonical action matrix is the approved spec in `docs/superpowers/specs/2026-07-06-session-lifecycle-tristate-design.md`.
- Active tab: Active rows only; Pin + Archive.
- All main: Active rows expose Pin + Trash; Archived rows expose Pin + Move to Active + Trash.
- Trash: Trashed rows expose Untrash to Archived only; no Pin.
- Never derive Trash placement from `pinned` or lane overrides.
- Each lifecycle action appears once in the DOM.
- GH issue/backlog rows retain their existing GitHub/TODO archive behavior and do not gain a Trashed state.
- Preserve unrelated user changes. Commit only named paths with `git commit --only` and do not push.

## File Map

- `server.py`: lifecycle persistence, row payload fields, session/group-chat state transitions, additive endpoints.
- `static/app.js`: context-aware row actions, All/Trash bucketing, optimistic state updates, group-chat actions.
- `static/app.css`: shared archive/trash/untrash action styling without duplicate rest-state controls.
- `tests/test_conversation_lifecycle.py`: focused server lifecycle and invariant tests.
- `tests/test_sidebar_lifecycle_static.py`: client action-matrix and bucketing contracts.
- `scripts/verify-conversation-lifecycle.js`: Puppeteer round-trip verification against a running local server.
- `changelog.d/added-session-trash-state-2026-07-14.md`: user-visible release note.

---

### Task 1: Persist and expose the Trashed state

**Files:**
- Modify: `server.py` at `ARCHIVED_CONVERSATIONS_FILE`, `_load_archived_conversations`, archive-cache rehydration, and every engine row builder that currently stamps `archived`.
- Create: `tests/test_conversation_lifecycle.py`

**Interfaces:**
- Produces: `TRASHED_CONVERSATIONS_FILE`, `_load_trashed_conversations(*, sweep=True) -> list[str]`, `_save_trashed_conversations(trashed: list[str]) -> list[str]`, and additive `trashed: bool` row fields.

- [ ] **Step 1: Write failing persistence tests**

```python
import server


def test_trashed_conversations_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "trashed-conversations.json"
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", path)
    assert server._load_trashed_conversations() == []
    assert server._save_trashed_conversations(["sid-a", "sid-b"]) == ["sid-a", "sid-b"]
    assert server._load_trashed_conversations() == ["sid-a", "sid-b"]


def test_trashed_loader_ignores_non_string_entries(tmp_path, monkeypatch):
    path = tmp_path / "trashed-conversations.json"
    path.write_text('["sid-a", 7, null, "sid-b"]')
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", path)
    assert server._load_trashed_conversations() == ["sid-a", "sid-b"]
```

- [ ] **Step 2: Run the tests and confirm the missing-symbol failure**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py -q`

Expected: FAIL because `TRASHED_CONVERSATIONS_FILE` and the load/save helpers do not exist.

- [ ] **Step 3: Add the sidecar constant and helpers**

```python
TRASHED_CONVERSATIONS_FILE = COMMAND_CENTER_STATE_DIR / "trashed-conversations.json"


def _load_trashed_conversations(*, sweep=True):
    """Load trashed session ids. `sweep` exists for archive-helper parity."""
    try:
        data = json.loads(TRASHED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [sid for sid in data if isinstance(sid, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_trashed_conversations(trashed):
    """Persist trashed session ids."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(trashed, list):
        trashed = []
    TRASHED_CONVERSATIONS_FILE.write_text(json.dumps(trashed, indent=2))
    return trashed
```

- [ ] **Step 4: Port the reviewed row-field propagation**

Use `git diff main...worktree-session-lifecycle-tristate -- server.py` as read-only reference. At every current `archived_set = set(_load_archived_conversations(...))` boundary, load the matching `trashed_set`; immediately after each row's existing `"archived": ...` field, add `"trashed": sid in trashed_set` while preserving any existing `or bool(row.get("..."))` fallback. Thread `trashed_set` through `_live_registry_conversation_row`, and stamp it during `_rehydrate_archive_cached_rows` so cached rows cannot retain stale state.

- [ ] **Step 5: Verify persistence and import behavior**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py tests/test_smoke.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the server data slice**

```bash
git commit --only server.py tests/test_conversation_lifecycle.py -m "feat(sessions): persist trashed conversation state"
```

---

### Task 2: Enforce lifecycle transitions in server APIs

**Files:**
- Modify: `server.py` at the conversation archive handler, group-chat metadata functions, and POST routing.
- Modify: `tests/test_conversation_lifecycle.py`

**Interfaces:**
- Produces: `_set_conversation_trashed(sid: str, trashed: bool) -> dict`, `_group_chat_set_trashed(path: str, trashed: bool, raw_uuid="") -> dict`, `POST /api/conversations/<id>/trash`, `POST /api/group-chats/trash`, and `POST /api/group-chats/untrash`.

- [ ] **Step 1: Write failing transition tests**

```python
def test_trash_active_session_archives_and_trashes(tmp_path, monkeypatch):
    archived = tmp_path / "archived.json"
    trashed = tmp_path / "trashed.json"
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", archived)
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", trashed)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_save_archive_grace", lambda: None)
    monkeypatch.setattr(server, "_kill_session_by_id", lambda sid: {"ok": True})
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: None)
    result = server._set_conversation_trashed("sid-a", True)
    assert result["archived"] is True
    assert result["trashed"] is True
    assert server._load_archived_conversations(sweep=False) == ["sid-a"]
    assert server._load_trashed_conversations() == ["sid-a"]


def test_untrash_returns_to_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    server._save_archived_conversations(["sid-a"])
    server._save_trashed_conversations(["sid-a"])
    result = server._set_conversation_trashed("sid-a", False)
    assert result == {"archived": True, "trashed": False, "killed": None}


def test_unarchive_clears_trashed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    server._save_trashed_conversations(["sid-a"])
    server._clear_trashed_on_unarchive("sid-a")
    assert server._load_trashed_conversations() == []
```

- [ ] **Step 2: Run the transition tests and confirm failure**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py -q`

Expected: FAIL because `_set_conversation_trashed` and `_clear_trashed_on_unarchive` do not exist.

- [ ] **Step 3: Implement the session transition helpers**

Implement `_set_conversation_trashed` so `trashed=True` atomically adds the session to both sidecars, records the same sticky manual-archive grace marker as the archive endpoint, clears the needs-approval marker, logs the transition, and kills a non-backlog/non-pkood live process. `trashed=False` removes only the Trash membership. Return exactly `{"archived": bool, "trashed": bool, "killed": object | None}`. Implement `_clear_trashed_on_unarchive(sid)` and call it whenever the existing archive endpoint successfully sets `archived=False`.

- [ ] **Step 4: Add the additive session trash endpoint**

Add `POST /api/conversations/<id>/trash`. Parse `{session_id?, trashed?, repo_path?}` exactly like the archive endpoint, reject GH issue/backlog rows from the new lifecycle, call `_set_conversation_trashed`, clear the archive response cache, and return `{"ok": True, "archived": ..., "trashed": ..., "killed": ...}`.

- [ ] **Step 5: Add group-chat state and endpoints**

Add `trashed` to `_list_group_chats` rows. `_group_chat_set_trashed(..., True)` must archive first when needed and remove the chat from `_active_coordinations`; `_group_chat_set_trashed(..., False)` leaves it archived. Unarchiving a chat clears `trashed`. Route `/api/group-chats/trash` and `/api/group-chats/untrash` with the existing `{path, id}` validation and 403/404 behavior.

- [ ] **Step 6: Run focused and smoke tests**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py tests/test_group_chat_activity.py tests/test_smoke.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the transition slice**

```bash
git commit --only server.py tests/test_conversation_lifecycle.py -m "feat(sessions): add trash lifecycle transitions"
```

---

### Task 3: Render the exact tab-specific action matrix

**Files:**
- Modify: `static/app.js` in `_renderRow`, `_renderRowsWithRepeatGroups`, and their Active/All/Trash callers.
- Modify: `static/app.css` at `.conv-archive-btn` styling.
- Create: `tests/test_sidebar_lifecycle_static.py`

**Interfaces:**
- Produces: lifecycle contexts `active`, `all-main`, `trash`; `.conv-trash-btn[data-role="trash"]`; `.conv-trash-btn[data-role="untrash"]`.

- [ ] **Step 1: Write failing static action-contract tests**

```python
from pathlib import Path

APP_JS = Path("static/app.js").read_text()


def test_renderer_has_explicit_lifecycle_contexts():
    for context in ("active", "all-main", "trash"):
        assert f"'{context}'" in APP_JS


def test_all_active_rows_trash_without_archive_action():
    assert "lifecycleContext === 'all-main'" in APP_JS
    assert "data-role=\"trash\"" in APP_JS


def test_trash_rows_only_untrash_and_never_pin():
    assert "lifecycleContext !== 'trash'" in APP_JS
    assert "data-role=\"untrash\"" in APP_JS


def test_archived_rows_do_not_render_duplicate_rest_restore():
    assert "archivedRestoreRestHtml" not in APP_JS
```

- [ ] **Step 2: Run the static tests and confirm failure**

Run: `python3 -m pytest tests/test_sidebar_lifecycle_static.py -q`

Expected: FAIL on the missing lifecycle contexts and remaining duplicate restore path.

- [ ] **Step 3: Make `_renderRow` context-aware**

Read `opts.lifecycleContext`, falling back by bucket/state only for search-result callers. Build the action HTML with this exact matrix:

```javascript
const lifecycleContext = opts.lifecycleContext
  || (c.trashed ? 'trash' : (c.archived ? 'all-main' : (_sidebarTab === 'archived' ? 'all-main' : 'active')));
const pinBtn = lifecycleContext !== 'trash' ? _pinButtonHtml(c) : '';

if (lifecycleContext === 'trash') {
  lifecycleButtons = '<button class="conv-trash-btn" data-role="untrash" title="Untrash to Archived" aria-label="Untrash to Archived">&#8617;</button>';
} else if (lifecycleContext === 'all-main') {
  lifecycleButtons = (c.archived
    ? '<button class="conv-archive-btn" data-role="archive" title="Move to Active" aria-label="Move to Active">&#8617;</button>'
    : '')
    + '<button class="conv-trash-btn" data-role="trash" title="Move to Trash" aria-label="Move to Trash">&#128465;</button>';
} else {
  lifecycleButtons = '<button class="conv-archive-btn" data-role="archive" title="Archive" aria-label="Archive">&#128229;</button>';
}
```

Keep backlog and GitHub PR branches unchanged. Remove `archivedRestoreRestHtml`; time remains the sole rest-state content and actions appear once in `.conv-row-actions`.

- [ ] **Step 4: Pass explicit context from every list builder**

Active/In-progress rendering passes `{lifecycleContext: 'active'}`. All main rendering—including folder, flat, repeat-group, lane, and archived-visible group-chat paths—passes `{lifecycleContext: 'all-main'}`. Trash rendering passes `{lifecycleContext: 'trash'}`. Ensure option forwarding preserves the context through `_renderRowsWithRepeatGroups`.

- [ ] **Step 5: Style archive, trash, and untrash consistently**

Extend the shared icon-button selectors to `.conv-trash-btn`; use orange/red hover for Trash and blue hover for Move to Active/Untrash. Delete CSS that hides a duplicate direct-child Restore button on hover.

- [ ] **Step 6: Run static and syntax tests**

Run: `node --check static/app.js && python3 -m pytest tests/test_sidebar_lifecycle_static.py tests/test_sidebar_row_layout.py tests/test_smoke.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the renderer slice**

```bash
git commit --only static/app.js static/app.css tests/test_sidebar_lifecycle_static.py -m "feat(ui): render tri-state actions by tab"
```

---

### Task 4: Wire bucketing and state-changing actions

**Files:**
- Modify: `static/app.js` in All-tab bucketing, session action handlers, archived group-chat rendering, and group-chat API helpers.
- Modify: `tests/test_sidebar_lifecycle_static.py`

**Interfaces:**
- Consumes: the Task 2 endpoints and Task 3 `data-role` hooks.
- Produces: complete Active→Archived, Active/Archived→Trashed, Archived→Active, and Trashed→Archived UI transitions.

- [ ] **Step 1: Add failing bucketing and handler tests**

```python
def test_all_bucketing_uses_trashed_not_pinned():
    assert "const _trashConvs = _archivedConvs.filter(c => c.trashed)" in APP_JS
    assert "const _archivedVisibleConvs = _archivedConvs.filter(c => !c.trashed)" in APP_JS
    assert "_archivedConvs.filter(c => !c.pinned" not in APP_JS


def test_session_trash_handler_calls_additive_endpoint():
    assert "'/trash'" in APP_JS
    assert "{ trashed: wantTrashed }" in APP_JS


def test_group_chats_have_distinct_trash_and_untrash_calls():
    assert "fetch('/api/group-chats/trash'" in APP_JS
    assert "fetch('/api/group-chats/untrash'" in APP_JS
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python3 -m pytest tests/test_sidebar_lifecycle_static.py -q`

Expected: FAIL because the current Trash bucket is still derived from pin/lane state and the endpoints are absent.

- [ ] **Step 3: Replace All-tab bucketing**

```javascript
const _trashConvs = _archivedConvs.filter(c => c.trashed);
const _archivedVisibleConvs = _archivedConvs.filter(c => !c.trashed);
const _allTabConvs = _sessionConvs.concat(
  _openAskConvs,
  _readyToMergeConvs,
  _archivedVisibleConvs
);
```

Pin and lane overrides may affect ordering/lane selection inside All main, but never membership in Trash.

- [ ] **Step 4: Wire session actions with explicit desired state**

The archive handler sends `archived: false` for Move to Active and `archived: true` for Archive; never infer the transition from DOM location. The trash handler sends `{trashed: true}` for Trash and `{trashed: false}` for Untrash, patches both `conversationsData` and `archiveData`, and applies optimistic overrides for `archived` and `trashed`.

- [ ] **Step 5: Split group chats by `trashed`**

Archived-not-trashed chats join All main and expose Move to Active + Trash. Trashed chats appear only in the Trash section and expose Untrash. Add `trashGroupChat` and `untrashGroupChat`, refresh both active and archived chat feeds after success, and keep row-click guards from opening the reader when an action button was clicked.

- [ ] **Step 6: Run client contract and syntax tests**

Run: `node --check static/app.js && python3 -m pytest tests/test_sidebar_lifecycle_static.py tests/test_sidebar_window_invariants.py tests/test_smoke.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the interaction slice**

```bash
git commit --only static/app.js tests/test_sidebar_lifecycle_static.py -m "feat(ui): wire archive trash and untrash transitions"
```

---

### Task 5: Verify the complete lifecycle and document it

**Files:**
- Create: `scripts/verify-conversation-lifecycle.js`
- Create: `changelog.d/added-session-trash-state-2026-07-14.md`

**Interfaces:**
- Consumes: the complete server/client behavior from Tasks 1–4.
- Produces: repeatable browser evidence for all transitions and a release note.

- [ ] **Step 1: Create the Puppeteer verification script**

Use the repository's installed `puppeteer`. The script must open `http://127.0.0.1:8090`, wait for `[data-role="conv-tab-bar"]`, exercise a disposable real session through these transitions, and assert its DOM location/action roles after each step:

```text
Active tab / Active: archive present, trash absent
All main / Archived: archive(Move to Active) + trash present
All main / Active: trash present, archive absent
Trash / Trashed: untrash present, archive/trash/pin absent
All main / Archived after Untrash: archive + trash present
```

The script must restore the session to its original lifecycle state in a `finally` block and exit nonzero on any mismatch.

- [ ] **Step 2: Run targeted tests**

Run: `python3 -m pytest tests/test_conversation_lifecycle.py tests/test_sidebar_lifecycle_static.py tests/test_group_chat_activity.py tests/test_sidebar_window_invariants.py tests/test_sidebar_row_layout.py -q`

Expected: PASS.

- [ ] **Step 3: Run the complete test suite**

Run: `python3 -m pytest -q`

Expected: PASS with no new failures.

- [ ] **Step 4: Run browser verification**

Run: `node scripts/verify-conversation-lifecycle.js`

Expected: exit 0 with a transition-by-transition PASS summary.

- [ ] **Step 5: Add the changelog snippet**

```markdown
- Added distinct Active, Archived, and Trashed conversation states: Active and Archived remain visible in All, Trash is a separate bottom bucket, and every tab now exposes the correct Archive, Move to Active, Trash, or Untrash action.
```

- [ ] **Step 6: Commit verification and changelog**

```bash
git commit --only scripts/verify-conversation-lifecycle.js changelog.d/added-session-trash-state-2026-07-14.md -m "test(ui): verify tri-state conversation lifecycle"
```

- [ ] **Step 7: Request code review and finish the branch**

Use the requesting-code-review skill against the full diff. Address only lifecycle-scope findings, rerun the focused/full/browser checks, then use the finishing-a-development-branch skill to merge into `main`. Only after the merged result passes should `worktree-session-lifecycle-tristate` be removed and its branch deleted.
