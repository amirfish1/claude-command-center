# Tri-state session lifecycle (Active / Archived / Trashed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single `archived` flag into two independent flags —
`archived` (out of the Active tab) and `trashed` (folded into the All tab's
collapsed Trash bucket) — for sessions and group chats, decoupling `pinned`
from that split entirely. GH-issue/backlog rows keep Active/Archived-only.

**Architecture:** Mirror the existing `archived`/`ARCHIVED_CONVERSATIONS_FILE`
pattern with a parallel `trashed`/`TRASHED_CONVERSATIONS_FILE` sidecar for
sessions, and a parallel `trashed_at` sidecar field (next to `archived_at`)
for group chats. New endpoints are additive only — existing
`/api/conversations/<id>/archive` and `/api/group-chats/{archive,unarchive}`
keep their current contracts. Client-side, swap the `pinned`-based All-tab
bucket split for a `trashed`-based split, and give archived-not-trashed rows
a second action button (Trash) alongside the existing Unarchive.

**Tech Stack:** Python stdlib (server.py, no pip deps), vanilla JS
(static/app.js, no bundler), puppeteer for manual UI verification
(`node <script>.js` run from the repo root — that's where puppeteer
resolves).

## Global Constraints

- `server.py` is stdlib-only — no new imports beyond what's already used
  (`json`, `time`, `os`, `re`, `Path`).
- `/api/*` is this repo's stable surface — every change in this plan is
  **additive** (new endpoints, new response fields). Do not rename or
  remove any existing endpoint or field.
- Multi-agent shared clone: commit with `git commit --only <paths>`, never
  `git add -A`/`.`/`-a`. Do not push unless asked.
- Line numbers below were read from the current `main` HEAD at plan-writing
  time. This is a shared clone other sessions also edit — if a `grep` for
  the anchor snippet doesn't land on the stated line, trust the grep, not
  the number.
- No migration script (explicit product decision) — the new `trashed`
  state starts empty for everyone.
- GH-issue/backlog rows are **out of scope** for `trashed` — leave their
  archive branch (`isBacklogRow` in `_renderRow`, the `backlog_match`
  branch in the archive endpoint) untouched.

---

### Task 1: Server — trashed-conversations sidecar file

**Files:**
- Modify: `server.py:8569` (constant block) and `server.py:9540-9565`
  (load/save functions, right after `_load_archived_conversations` /
  `_save_archived_conversations`)
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: `TRASHED_CONVERSATIONS_FILE` (Path constant),
  `_load_trashed_conversations(*, sweep=True) -> list[str]`,
  `_save_trashed_conversations(trashed: list) -> list[str]` — same
  signatures as their `archived` counterparts, used by every later task.

- [ ] **Step 1: Add the sidecar file constant**

Find (server.py:8569):
```python
ARCHIVED_CONVERSATIONS_FILE = COMMAND_CENTER_STATE_DIR / "archived-conversations.json"  # [session_id,...]
```
Add directly below it:
```python
TRASHED_CONVERSATIONS_FILE = COMMAND_CENTER_STATE_DIR / "trashed-conversations.json"  # [session_id,...] — subset of archived
```

- [ ] **Step 2: Add load/save helpers**

Find `_load_archived_conversations` / `_save_archived_conversations`
(server.py:9540-9565):
```python
def _load_archived_conversations(*, sweep=True):
    """Load list of archived session_ids from the side-car file."""
    try:
        data = json.loads(ARCHIVED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            archived = [s for s in data if isinstance(s, str)]
            return _auto_unarchive_live_sessions(archived) if sweep else archived
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_archived_conversations(archived):
    """Persist list of archived session_ids."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(archived, list):
        archived = []
    ARCHIVED_CONVERSATIONS_FILE.write_text(json.dumps(archived, indent=2))
    return archived
```
Add directly below it:
```python
def _load_trashed_conversations(*, sweep=True):
    """Load list of trashed session_ids from the side-car file. `sweep` is
    accepted for signature parity with `_load_archived_conversations` but
    unused — trashed rows have no live-session auto-unarchive sweep."""
    try:
        data = json.loads(TRASHED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_trashed_conversations(trashed):
    """Persist list of trashed session_ids."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(trashed, list):
        trashed = []
    TRASHED_CONVERSATIONS_FILE.write_text(json.dumps(trashed, indent=2))
    return trashed
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python3 -c "import server"`
Expected: no output, exit code 0 (a stdlib-only module with a syntax error
or bad indent fails loudly here).

- [ ] **Step 4: Commit**

```bash
git commit --only server.py -m "feat(sessions): add trashed-conversations sidecar file"
```

---

### Task 2: Server — thread `trashed_set` through existing row builders

**Files:**
- Modify: `server.py` at each of the 10 sites listed below.
- Test: `python3 -m pytest tests/test_smoke.py -q`

**Interfaces:**
- Consumes: `_load_trashed_conversations` (Task 1).
- Produces: every conversation row payload gains a `"trashed"` boolean
  field, mirroring the existing `"archived"` field, for later tasks
  (client bucketing) to read.

Every one of these 10 sites already computes `archived_set` right before
building rows, then stamps `"archived": sid in archived_set` (or
`session_id in archived_set`) per row. Add a `trashed_set` the same way,
and a parallel `"trashed"` field on the same row dict, immediately after
the existing `"archived"` line.

**Definition sites** (add `trashed_set = set(_load_trashed_conversations(sweep=False))`
— or, for the two call sites using `_load_archived_conversations()` with no
`sweep` kwarg, `trashed_set = set(_load_trashed_conversations())` — on the
line directly below each `archived_set = ...` line):
`4867, 5925, 14466, 20222, 23201, 24755, 26419, 27313, 28517, 36833`

**Usage sites** (add `"trashed": sid in trashed_set,` — or
`"trashed": sid in trashed_set or bool(row.get("trashed")),` /
`"trashed": sid in trashed_set or bool(s.get("trashed")),` when the
existing `"archived"` line on that site has an `or bool(...)` fallback —
directly below each `"archived": ...,` line): `5221, 14832, 20395, 23329,
23463, 24898, 26572, 27429, 28673, 28779`

- [ ] **Step 1: Worked example (first pair) — server.py:4867 / server.py:5221**

Find:
```python
        archived_set = set(_load_archived_conversations(sweep=False))
```
Change to:
```python
        archived_set = set(_load_archived_conversations(sweep=False))
        trashed_set = set(_load_trashed_conversations(sweep=False))
```
Find (server.py:5221, in the row dict this same function builds):
```python
                "archived": session_id in archived_set,
```
Change to:
```python
                "archived": session_id in archived_set,
                "trashed": session_id in trashed_set,
```

- [ ] **Step 2: Repeat the same two-line addition at the remaining 9 site pairs**

Match each definition line to the nearest `"archived":` row-dict line in
the same function (they're always in the same function body — grep
`grep -n '"archived":' server.py` to relocate if line numbers drifted).
Use the `or bool(row.get(...))` / `or bool(s.get(...))` variant of the
`"trashed"` line only where the existing `"archived"` line at that exact
spot already has that `or bool(...)` suffix (server.py:20395, 26572,
27429) — plain `sid in trashed_set` everywhere else.

- [ ] **Step 3: Run the smoke suite**

Run: `python3 -m pytest tests/test_smoke.py -q`
Expected: all tests pass (same count as before this task — this only adds
a field, no behavior change yet since nothing reads `trashed` client-side
until Task 6).

- [ ] **Step 4: Commit**

```bash
git commit --only server.py -m "feat(sessions): expose trashed flag on conversation rows"
```

---

### Task 3: Server — `/api/conversations/<id>/trash` endpoint + invariant enforcement

**Files:**
- Modify: `server.py:50420-50534` (existing archive handler — add
  invariant enforcement) and add a new `elif` branch immediately after it
  (before `server.py:50535`'s `elif path == "/api/conversations/archive-bulk":`)
- Test: manual curl against the dev server (no existing endpoint test
  harness in this repo to extend — see Task 8 for the puppeteer pass)

**Interfaces:**
- Consumes: `_load_trashed_conversations` / `_save_trashed_conversations`
  (Task 1), `_load_archived_conversations` (existing).
- Produces: `POST /api/conversations/<id>/trash` — body `{trashed: bool,
  session_id?, repo_path?}` (same shape as the existing `/archive`
  endpoint's `archivePayloadForRow`), response `{ok, trashed}`. Later
  tasks (client fetch calls) depend on this exact path and response shape.

- [ ] **Step 1: Enforce the subset invariant in the existing archive handler**

Find (server.py, inside the existing archive `elif` block, the branch
that runs on unarchive):
```python
                elif (not want) and is_arch:
                    archived.remove(sid)
                    now_archived = False
                    _archive_grace.pop(sid, None)
                    _save_archive_grace()
                    _log_archive_event("unarchive", sid, "manual")
```
Change to (clear trashed on unarchive — a row can't be trashed while
active):
```python
                elif (not want) and is_arch:
                    archived.remove(sid)
                    now_archived = False
                    _archive_grace.pop(sid, None)
                    _save_archive_grace()
                    _log_archive_event("unarchive", sid, "manual")
                    trashed = _load_trashed_conversations()
                    if sid in trashed:
                        trashed.remove(sid)
                        _save_trashed_conversations(trashed)
```

- [ ] **Step 2: Add the new `/trash` endpoint**

Find (server.py:50535):
```python
        elif path == "/api/conversations/archive-bulk":
```
Insert directly above it:
```python
        elif re.match(r"^/api/conversations/[^/]+/trash$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id") or conv_id
            desired = payload.get("trashed")
            try:
                archived = _load_archived_conversations()
                trashed = _load_trashed_conversations()
                want = desired if isinstance(desired, bool) else (sid not in trashed)
                # A row can only be trashed while archived — trashing an
                # active row archives it first (one API call instead of
                # forcing the client to chain /archive then /trash).
                if want and sid not in archived:
                    archived.append(sid)
                    _save_archived_conversations(archived)
                if want and sid not in trashed:
                    trashed.append(sid)
                    _save_trashed_conversations(trashed)
                    now_trashed = True
                elif (not want) and sid in trashed:
                    trashed.remove(sid)
                    _save_trashed_conversations(trashed)
                    now_trashed = False
                else:
                    now_trashed = sid in trashed
                self.send_json({"ok": True, "trashed": now_trashed})
            except OSError as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
```

- [ ] **Step 3: Restart the dev server and smoke-test the endpoint manually**

Run: `curl -s -H "Origin: http://127.0.0.1:8090" -X POST http://127.0.0.1:8090/api/conversations/<a-real-session-id>/trash -d '{"trashed": true}'`
Expected: `{"ok": true, "trashed": true}`. Repeat with `{"trashed": false}`,
expect `{"ok": true, "trashed": false}`.
(POST `/api/restart` with an `Origin` header first if the dev server was
already running before this edit — it doesn't hot-reload Python.)

- [ ] **Step 4: Run the smoke suite**

Run: `python3 -m pytest tests/test_smoke.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git commit --only server.py -m "feat(sessions): add /api/conversations/<id>/trash endpoint"
```

---

### Task 4: Server — group-chat `trashed_at` + trash/untrash endpoints

**Files:**
- Modify: `server.py:34155-34283` (`_list_group_chats` — expose the new
  field), `server.py:34343-34369` (`_group_chat_set_archived` — clear
  trashed on unarchive), add `_group_chat_set_trashed` right after it, and
  add two new `elif` branches after the existing
  `/api/group-chats/unarchive` block (`server.py:51257-51279`)
- Test: manual curl (same rationale as Task 3)

**Interfaces:**
- Produces: `_group_chat_set_trashed(raw_path, trashed: bool, raw_uuid="") -> dict`
  (mirrors `_group_chat_set_archived`'s `{ok, error?}` contract),
  `POST /api/group-chats/trash` / `POST /api/group-chats/untrash` (body
  `{path, id}`, same as existing archive/unarchive), `"trashed"` field on
  every `_list_group_chats()` row.

- [ ] **Step 1: Expose `trashed` on group-chat rows**

Find (server.py:34261, inside the dict `_list_group_chats` appends to
`out`):
```python
            "archived_at": meta.get("archived_at"),
```
Change to:
```python
            "archived_at": meta.get("archived_at"),
            "trashed": bool(meta.get("trashed")),
```

- [ ] **Step 2: Clear `trashed` when a chat is unarchived**

Find (server.py:34343-34369):
```python
def _group_chat_set_archived(raw_path: str, archived: bool, raw_uuid: str = "") -> dict:
    """Flip the archived flag on a chat sidecar. Drops the chat from the
    active watcher dict on archive (so it stops getting nudged). Returns
    the same shape as other group-chat handlers: {ok, error?}.
    """
    real_path = _resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    fields = {"archived": bool(archived)}
    if archived:
        fields["archived_at"] = time.time()
    else:
        fields["archived_at"] = None
    if not _update_group_chat_sidecar(real_path, **fields):
        return {"ok": False, "error": "could not update sidecar"}
```
Change to (clear `trashed` in the same write when un-archiving — a chat
can't be trashed while active):
```python
def _group_chat_set_archived(raw_path: str, archived: bool, raw_uuid: str = "") -> dict:
    """Flip the archived flag on a chat sidecar. Drops the chat from the
    active watcher dict on archive (so it stops getting nudged). Returns
    the same shape as other group-chat handlers: {ok, error?}.
    """
    real_path = _resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    fields = {"archived": bool(archived)}
    if archived:
        fields["archived_at"] = time.time()
    else:
        fields["archived_at"] = None
        fields["trashed"] = False
    if not _update_group_chat_sidecar(real_path, **fields):
        return {"ok": False, "error": "could not update sidecar"}
```

- [ ] **Step 3: Add `_group_chat_set_trashed`**

Add directly below `_group_chat_set_archived` (after its closing `return
{"ok": True}` at server.py:34369):
```python
def _group_chat_set_trashed(raw_path: str, trashed: bool, raw_uuid: str = "") -> dict:
    """Flip the trashed flag on a chat sidecar. Requires the chat to
    already be archived — trashing an active chat archives it first (one
    call instead of forcing the client to chain archive then trash).
    Mirrors _group_chat_set_archived's contract: {ok, error?}.
    """
    real_path = _resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    fields = {"trashed": bool(trashed)}
    if trashed and not _load_group_chat_sidecar(real_path).get("archived"):
        fields["archived"] = True
        fields["archived_at"] = time.time()
    if not _update_group_chat_sidecar(real_path, **fields):
        return {"ok": False, "error": "could not update sidecar"}
    return {"ok": True}
```

- [ ] **Step 4: Add the two new endpoints**

Find (server.py:51257):
```python
        elif path == "/api/group-chats/unarchive":
```
Locate the end of that `elif` block (it ends right before the next
`elif path == "/api/group-chats/pause":`) and insert two new blocks after
it:
```python
        elif path == "/api/group-chats/trash":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            chat_path = (payload.get("path") or "").strip()
            chat_uuid = (payload.get("id") or payload.get("uuid") or "").strip()
            if not chat_path and not chat_uuid:
                self.send_json({"ok": False, "error": "missing path or id"})
                return
            result = _group_chat_set_trashed(chat_path, True, chat_uuid)
            if result.get("error") == "forbidden":
                self.send_json(result, 403)
            elif result.get("error") == "not found":
                self.send_json(result, 404)
            else:
                self.send_json(result)
        elif path == "/api/group-chats/untrash":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            chat_path = (payload.get("path") or "").strip()
            chat_uuid = (payload.get("id") or payload.get("uuid") or "").strip()
            if not chat_path and not chat_uuid:
                self.send_json({"ok": False, "error": "missing path or id"})
                return
            result = _group_chat_set_trashed(chat_path, False, chat_uuid)
            if result.get("error") == "forbidden":
                self.send_json(result, 403)
            elif result.get("error") == "not found":
                self.send_json(result, 404)
            else:
                self.send_json(result)
```

- [ ] **Step 5: Restart the dev server and smoke-test manually**

Run: `curl -s -H "Origin: http://127.0.0.1:8090" -X POST http://127.0.0.1:8090/api/group-chats/trash -d '{"id":"<a-real-chat-uuid>"}'`
Expected: `{"ok": true}`. Then `/api/group-chats/untrash` with the same
body, expect `{"ok": true}`.

- [ ] **Step 6: Run the smoke suite and commit**

```bash
python3 -m pytest tests/test_smoke.py -q
git commit --only server.py -m "feat(group-chats): add trashed state + trash/untrash endpoints"
```

---

### Task 5: Client — row action buttons (Unarchive + Trash / Restore)

**Files:**
- Modify: `static/app.js:22701-22716` (`_renderRow`'s `archiveBtn`
  construction)
- Test: manual (Task 8's puppeteer pass covers this)

**Interfaces:**
- Consumes: `c.archived`, `c.trashed` (Task 2's new field).
- Produces: `.conv-trash-btn` (new button class, `data-role="trash"`) for
  Task 7's click handler to bind to.

- [ ] **Step 1: Split the archived-row branch into trashed vs archived-visible**

Find (static/app.js:22701-22716):
```js
      let startBtn = '';
      let archiveBtn;
      const pinTitle = c.pinned ? 'Unpin conversation' : 'Pin conversation';
      const pinBtn = '<button class="conv-pin-btn' + (c.pinned ? ' is-unpin' : '') + '" data-role="pin" title="' + pinTitle + '" aria-label="' + pinTitle + '"><span class="conv-pin-glyph">&#128204;</span></button>';
      if (isBacklogRow) {
        const _issueAttr = escapeAttr(c.issue_number || '');
        const _titleAttr = escapeAttr(c.display_name || c.ai_title || c.first_message || '');
        // Issue rows carry their concrete repo so the spawn handler can target
        // the right folder without relying on server state.
        const _spawnCwdAttr = escapeAttr(c.spawn_cwd || c.folder_path || '');
        startBtn = '<button class="conv-start-btn" data-role="start" data-issue="' + _issueAttr + '" data-title="' + _titleAttr + '" data-spawn-cwd="' + _spawnCwdAttr + '" title="Spawn a session to work on this issue" aria-label="Start issue session">&#9654;</button>';
        archiveBtn = '<button class="conv-archive-btn is-close" data-role="archive" title="Archive issue (close as not planned)" aria-label="Archive issue">&#128229;</button>';
      } else if (isGithubPrRow) {
        archiveBtn = '';
      } else {
        archiveBtn = '<button class="conv-archive-btn" data-role="archive" title="' + (c.archived ? 'Unarchive' : 'Archive') + '">' + (c.archived ? '&#8617;' : '&#128229;') + '</button>';
      }
```
Change the trailing `else` branch to:
```js
      } else if (c.trashed) {
        // Trashed row: one step back up the ladder, to Archived — not
        // straight to Active (CCC-499 follow-up: strict one-rung-at-a-time
        // lifecycle, Active <-> Archived <-> Trashed).
        archiveBtn = '<button class="conv-trash-btn is-restore" data-role="untrash" title="Restore to Archived" aria-label="Restore to Archived">&#8617;</button>';
      } else if (c.archived) {
        // Archived-but-visible row: both directions are one click away —
        // back up to Active, or down into Trash.
        archiveBtn = '<button class="conv-archive-btn" data-role="archive" title="Unarchive" aria-label="Unarchive">&#8617;</button>'
          + '<button class="conv-trash-btn" data-role="trash" title="Move to Trash" aria-label="Move to Trash">&#128465;</button>';
      } else {
        archiveBtn = '<button class="conv-archive-btn" data-role="archive" title="Archive" aria-label="Archive">&#128229;</button>';
      }
```

- [ ] **Step 2: Verify no syntax break**

Run: `node --check static/app.js`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git commit --only static/app.js -m "feat(ui): split archived row actions into Unarchive + Trash"
```

---

### Task 6: Client — All-tab bucketing swap (trashed-based, not pinned-based)

**Files:**
- Modify: `static/app.js:24831-24838`

**Interfaces:**
- Consumes: `c.trashed` (Task 2).
- Produces: `_archivedVisibleConvs` (renamed from `_pinnedArchived`) —
  consumed by the rest of the existing `_allTabConvs` construction
  unchanged.

- [ ] **Step 1: Swap the split**

Find (static/app.js:24831-24838):
```js
    let _archivedHtml = '';
    // CCC-468: archived rows no longer interleave with the live rows in the
    // All tab — "archive" hid nothing there. They render in a collapsed
    // "Trash" section pinned to the very bottom instead. Pinned archived
    // rows are exempt (a pin is an explicit ask to keep it visible).
    const _trashConvs = _archivedConvs.filter(c => !c.pinned);
    const _pinnedArchived = _archivedConvs.filter(c => c.pinned);
    const _allTabConvs = _sessionConvs.concat(_openAskConvs, _readyToMergeConvs, _pinnedArchived);
```
Change to:
```js
    let _archivedHtml = '';
    // CCC-468 + tri-state follow-up: archived rows no longer interleave
    // with the live rows in the All tab. Whether an archived row shows in
    // the main All list or folds into the collapsed Trash section is now
    // its own `trashed` flag — NOT `pinned` (pin only affects sort rank
    // within whichever bucket a row lands in; it used to be misused as
    // this split's proxy, see 2026-07-06-session-lifecycle-tristate-design.md).
    const _trashConvs = _archivedConvs.filter(c => c.trashed);
    const _archivedVisibleConvs = _archivedConvs.filter(c => !c.trashed);
    const _allTabConvs = _sessionConvs.concat(_openAskConvs, _readyToMergeConvs, _archivedVisibleConvs);
```

- [ ] **Step 2: Verify no syntax break**

Run: `node --check static/app.js`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git commit --only static/app.js -m "feat(ui): bucket All tab by trashed flag instead of pinned"
```

---

### Task 7: Client — wire up click handlers + group-chat archived-visible bucket

**Files:**
- Modify: `static/app.js:26632-26696` (existing `.conv-archive-btn` click
  handler — add a sibling `.conv-trash-btn`/`untrash` handler),
  `static/app.js:24840-24875` (`_archivedGroupChatsForRender` +
  `_renderArchivedGcRow` — split trashed vs archived-visible, parametrize
  the row renderer), `static/app.js:24908-24927` (both All-tab list
  builders — merge archived-visible group chats into the main list, not
  just Trash), `static/app.js:25357-25378` (gc row click handlers),
  `static/app.js:40298-40319` (add `trashGroupChat()` /
  `untrashGroupChat()` mirroring `unarchiveGroupChat()`)

**Interfaces:**
- Consumes: `.conv-trash-btn[data-role="trash"]`,
  `.conv-trash-btn[data-role="untrash"]` (Task 5), `POST
  /api/conversations/<id>/trash` (Task 3), `POST
  /api/group-chats/{trash,untrash}` (Task 4), `gc.trashed` (Task 4's new
  field on `_list_group_chats` rows).
- Produces: fully working end-to-end trash/restore for both sessions and
  group chats.

- [ ] **Step 1: Session row click handler — trash and untrash**

Find (static/app.js:26632), the whole existing
`$convList.querySelectorAll('.conv-archive-btn').forEach(...)` block ends
at the matching `});` right before `$convList.querySelectorAll('.conv-wake-btn')`
(static/app.js:26697). Insert a new block directly after it:
```js
    $convList.querySelectorAll('.conv-trash-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const item = btn.closest('.conv-item');
        const convId = item.dataset.id;
        const sessionId = item.dataset.sessionId;
        const wantTrashed = btn.dataset.role !== 'untrash';
        try {
          const c = conversationsData.find(x => x.id === convId || x.session_id === sessionId);
          const repoPath = (c && rowRepoPath(c)) || item.dataset.repoPath || '';
          const data = await ccPostJson('/api/conversations/' + convId + '/trash',
            Object.assign(archivePayloadForRow(c || { repo_path: repoPath }, sessionId), { trashed: wantTrashed }));
          if (!data.ok) throw new Error(data.error || 'trash failed');
          if (c) {
            c.trashed = data.trashed;
            if (wantTrashed) c.archived = true;
            setOptimisticOverride(c.session_id, { trashed: c.trashed, archived: c.archived });
          }
          if (typeof archiveData !== 'undefined' && Array.isArray(archiveData)) {
            const ac = archiveData.find(x => x.session_id === sessionId);
            if (ac) { ac.trashed = data.trashed; if (wantTrashed) ac.archived = true; }
          }
          renderSidebar(filterConversations($convSearch.value));
        } catch (err) {
          showOpToast('Trash failed (' + err.message + ')', 'error');
        }
      });
    });
```

- [ ] **Step 2: Group-chat archived-visible split + parametrized row renderer**

Find (static/app.js:24840-24844):
```js
    const _archivedGroupChatsForRender = _hideGroupChatsForSearch
      ? []
      : (Array.isArray(_archivedGroupChats)
          ? _archivedGroupChats.filter(gc => _archiveWindowAllowsRow(gc, _ipWindowCutoff))
          : []);
```
Change to (split into two lists — the flat/folder builders below need
both):
```js
    const _archivedGroupChatsForRender = _hideGroupChatsForSearch
      ? []
      : (Array.isArray(_archivedGroupChats)
          ? _archivedGroupChats.filter(gc => _archiveWindowAllowsRow(gc, _ipWindowCutoff))
          : []);
    const _trashedGroupChats = _archivedGroupChatsForRender.filter(gc => gc.trashed);
    const _archivedVisibleGroupChats = _archivedGroupChatsForRender.filter(gc => !gc.trashed);
```
Find (static/app.js:24853-24875), `_renderArchivedGcRow`:
```js
    const _renderArchivedGcRow = (gc) => {
      const gcId = gc.uuid || gc.id || '';
      const topic = gc.topic ? escapeHtml(gc.topic.slice(0, 80)) : '(untitled)';
      const partCount = (gc.session_ids || []).length;
      const partLabel = partCount
        ? '<span class="archive-row-gc-partcount">' + partCount + '</span>'
        : '';
      return '<div class="conv-item conv-item-archived-gc" data-role="archived-gc-row"'
        + ' data-gc-id="' + escapeHtml(gcId) + '"'
        + ' data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
        + ' data-gc-topic="' + escapeHtml(gc.topic || '') + '"'
        + ' data-gc-mode="' + escapeHtml(gc.mode || 'topic') + '"'
        + ' title="Archived group chat — click to open reader">'
        +   '<span class="archive-row-gc-icon" title="Group chat">💬</span>'
        +   '<span class="archive-row-gc-topic">' + topic + '</span>'
        +   partLabel
        +   '<button type="button" class="conv-archived-gc-unarchive-btn"'
        +     ' data-role="archived-gc-unarchive"'
        +     ' data-gc-id="' + escapeHtml(gcId) + '"'
        +     ' data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
        +     ' title="Restore from trash">&#8617;</button>'
        + '</div>';
    };
```
Change to (parametrize the button set on `gc.trashed`):
```js
    const _renderArchivedGcRow = (gc) => {
      const gcId = gc.uuid || gc.id || '';
      const topic = gc.topic ? escapeHtml(gc.topic.slice(0, 80)) : '(untitled)';
      const partCount = (gc.session_ids || []).length;
      const partLabel = partCount
        ? '<span class="archive-row-gc-partcount">' + partCount + '</span>'
        : '';
      const actionBtns = gc.trashed
        ? '<button type="button" class="conv-archived-gc-unarchive-btn" data-role="archived-gc-untrash"'
          + ' data-gc-id="' + escapeHtml(gcId) + '" data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
          + ' title="Restore to Archived">&#8617;</button>'
        : '<button type="button" class="conv-archived-gc-unarchive-btn" data-role="archived-gc-unarchive"'
          + ' data-gc-id="' + escapeHtml(gcId) + '" data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
          + ' title="Unarchive">&#8617;</button>'
          + '<button type="button" class="conv-archived-gc-trash-btn" data-role="archived-gc-trash"'
          + ' data-gc-id="' + escapeHtml(gcId) + '" data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
          + ' title="Move to Trash">&#128465;</button>';
      return '<div class="conv-item conv-item-archived-gc" data-role="archived-gc-row"'
        + ' data-gc-id="' + escapeHtml(gcId) + '"'
        + ' data-gc-path="' + escapeHtml(gc.path_tilde) + '"'
        + ' data-gc-topic="' + escapeHtml(gc.topic || '') + '"'
        + ' data-gc-mode="' + escapeHtml(gc.mode || 'topic') + '"'
        + ' title="' + (gc.trashed ? 'Trashed' : 'Archived') + ' group chat — click to open reader">'
        +   '<span class="archive-row-gc-icon" title="Group chat">💬</span>'
        +   '<span class="archive-row-gc-topic">' + topic + '</span>'
        +   partLabel
        +   actionBtns
        + '</div>';
    };
```

- [ ] **Step 3: Merge archived-visible group chats into the main All list (both builders)**

Find (static/app.js, folder-grouped branch):
```js
      // Archived group chats live in the Trash section (CCC-468), so the
      // flat tail below the folder groups carries only unarchived chats.
      _arcRows = _folderRowsHtml + (_gcItems || []).map(it => it.html).join('');
      _arcCount = _allTabConvs.length + _archivedGroupChatsForRender.length + (_gcItems || []).length + _trashConvs.length;
```
Change to:
```js
      // Trashed group chats live in the Trash section; archived-but-not-
      // trashed ones join the flat tail alongside unarchived chats (group
      // chats aren't project-scoped, so they never join the folder groups
      // themselves).
      _arcRows = _folderRowsHtml + (_gcItems || []).concat(_archivedVisibleGroupChats.map(gc => ({ html: _renderArchivedGcRow(gc) }))).map(it => it.html).join('');
      _arcCount = _allTabConvs.length + _archivedGroupChatsForRender.length + (_gcItems || []).length + _trashConvs.length;
```
Find (static/app.js, flat chronological branch):
```js
      // Archived group chats live in the Trash section (CCC-468).
      // Active/paused/closed (unarchived) group chats still interleave here
      // so they appear in the All view, not just in Current Sessions.
      for (const gci of (_gcItems || [])) {
        _archivedItems.push({ pinRank: Infinity, mtime: gci.mtime || 0, html: gci.html });
      }
```
Change to:
```js
      // Trashed group chats live in the Trash section. Active/paused/closed
      // (unarchived) AND archived-but-not-trashed group chats interleave
      // here so they appear in the All view, not just in Current Sessions.
      for (const gci of (_gcItems || [])) {
        _archivedItems.push({ pinRank: Infinity, mtime: gci.mtime || 0, html: gci.html });
      }
      for (const gc of _archivedVisibleGroupChats) {
        _archivedItems.push({ pinRank: Infinity, mtime: (gc.archived_at || gc.last_mtime) || 0, html: _renderArchivedGcRow(gc) });
      }
```

- [ ] **Step 4: Point the Trash section's group-chat loop at `_trashedGroupChats`**

Find (in the `_trashHtml` builder, the loop that currently iterates
`_archivedGroupChatsForRender`):
```js
      for (const gc of _archivedGroupChatsForRender) {
        _trashItems.push({
          mtime: (gc.archived_at || gc.closed_at || gc.last_mtime) || 0,
          html: _renderArchivedGcRow(gc),
        });
      }
```
Change `_archivedGroupChatsForRender` to `_trashedGroupChats`:
```js
      for (const gc of _trashedGroupChats) {
        _trashItems.push({
          mtime: (gc.archived_at || gc.closed_at || gc.last_mtime) || 0,
          html: _renderArchivedGcRow(gc),
        });
      }
```

- [ ] **Step 5: Add `trashGroupChat()` / `untrashGroupChat()` and wire click handlers**

Find (static/app.js:40298-40319), `unarchiveGroupChat`. Add directly
below its closing `}`:
```js
  async function trashGroupChat(chatPath, chatId) {
    if (!chatPath && !chatId) return;
    try {
      const res = await fetch('/api/group-chats/trash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: chatPath || '', id: chatId || '' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data || !data.ok) {
        showOpToast?.('Could not trash group chat: ' + ((data && data.error) || 'unknown'), 'error');
        return;
      }
      try { await refreshArchivedGroupChats(); } catch (_) {}
      const $s = document.getElementById('convSearch');
      renderArchiveList($s ? $s.value : '');
      showOpToast?.('Group chat moved to Trash');
    } catch (err) {
      showOpToast?.('Could not trash group chat: ' + ((err && err.message) || 'network error'), 'error');
    }
  }

  async function untrashGroupChat(chatPath, chatId) {
    if (!chatPath && !chatId) return;
    try {
      const res = await fetch('/api/group-chats/untrash', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: chatPath || '', id: chatId || '' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data || !data.ok) {
        showOpToast?.('Could not restore group chat: ' + ((data && data.error) || 'unknown'), 'error');
        return;
      }
      try { await refreshArchivedGroupChats(); } catch (_) {}
      const $s = document.getElementById('convSearch');
      renderArchiveList($s ? $s.value : '');
      showOpToast?.('Group chat restored to Archived');
    } catch (err) {
      showOpToast?.('Could not restore group chat: ' + ((err && err.message) || 'network error'), 'error');
    }
  }
```
Find (static/app.js:25370-25378):
```js
    $convList.querySelectorAll('[data-role="archived-gc-unarchive"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        const path = btn.dataset.gcPath;
        const chatId = btn.dataset.gcId || null;
        if (path || chatId) unarchiveGroupChat(path, chatId);
      });
    });
```
Add directly below it:
```js
    $convList.querySelectorAll('[data-role="archived-gc-trash"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        const path = btn.dataset.gcPath;
        const chatId = btn.dataset.gcId || null;
        if (path || chatId) trashGroupChat(path, chatId);
      });
    });
    $convList.querySelectorAll('[data-role="archived-gc-untrash"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        const path = btn.dataset.gcPath;
        const chatId = btn.dataset.gcId || null;
        if (path || chatId) untrashGroupChat(path, chatId);
      });
    });
```
Also update the row-click guard right above the existing unarchive
handler (static/app.js:25357-25360) so a click on either new button
doesn't also open the reader:
```js
    $convList.querySelectorAll('[data-role="archived-gc-row"]').forEach(row => {
      row.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-role="archived-gc-unarchive"]')) return;
```
Change the guard line to:
```js
        if (ev.target.closest('[data-role="archived-gc-unarchive"], [data-role="archived-gc-trash"], [data-role="archived-gc-untrash"]')) return;
```

- [ ] **Step 6: Verify no syntax break**

Run: `node --check static/app.js`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit**

```bash
git commit --only static/app.js -m "feat(ui): wire trash/untrash for sessions and group chats"
```

---

### Task 8: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Restart the dev server**

Run: `curl -s -H "Origin: http://127.0.0.1:8090" -X POST http://127.0.0.1:8090/api/restart`

- [ ] **Step 2: Puppeteer state-transition check**

Write a throwaway script in the repo root (delete after), e.g.
`check_lifecycle.js`:
```js
const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:8090', { waitUntil: 'domcontentloaded' });
  await new Promise(r => setTimeout(r, 4000));
  // Switch to the All tab.
  await page.click('[data-conv-tab="archived"]');
  await new Promise(r => setTimeout(r, 500));
  const buttons = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('.conv-item'));
    return rows.slice(0, 20).map(r => ({
      inTrash: !!r.closest('.conv-trash-list'),
      hasArchiveBtn: !!r.querySelector('.conv-archive-btn'),
      hasTrashBtn: !!r.querySelector('.conv-trash-btn:not(.is-restore)'),
      hasRestoreBtn: !!r.querySelector('.conv-trash-btn.is-restore'),
    }));
  });
  console.log(JSON.stringify(buttons, null, 2));
  await browser.close();
})();
```
Run: `node check_lifecycle.js` (from the repo root)
Expected: rows outside `.conv-trash-list` with an archived-not-trashed
session show both `hasArchiveBtn: true` (the Unarchive button reuses the
`.conv-archive-btn` class) and `hasTrashBtn: true`; rows inside
`.conv-trash-list` show `hasRestoreBtn: true` and neither of the other two.
Delete the script when done: `rm check_lifecycle.js`.

- [ ] **Step 3: Click-through one full round trip**

In the same or a follow-up puppeteer script: click an Active row's Archive
button, confirm it disappears from the Active tab and appears in the All
tab's main list (not Trash). Click its new Trash button, confirm it moves
into the collapsed Trash section. Expand Trash, click Restore, confirm it
reappears in the All tab's main list (not back in Active). Click Unarchive,
confirm it reappears in the Active tab.

- [ ] **Step 4: Run the full smoke suite one last time**

Run: `python3 -m pytest tests/test_smoke.py -q`
Expected: all tests pass, same count as `main` before this feature plus
any new assertions from Task 1.

- [ ] **Step 5: Changelog snippet (Tier B — user-visible feature)**

Create `changelog.d/added-session-trash-state-2026-07-06.md`:
```
Added a Trash state distinct from Archived — archived sessions and group chats now show separate Unarchive and Trash actions, and pinning no longer decides whether an archived row hides in Trash.
```
Commit:
```bash
git commit --only changelog.d/added-session-trash-state-2026-07-06.md -m "docs(changelog): note tri-state session lifecycle"
```
