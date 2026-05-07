# Multi-Session Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users Ctrl/Shift-click conversation-list rows, open a "Coordinate…" modal with topic + mode + participants, then inject `/group-chat` into each selected session pointing at a fresh per-topic chat file, with a live-reader panel in the conv pane so the user can follow and participate.

**Architecture:** Backend creates `~/.claude/group-chats/<slug>-<ts>.md`, injects `/group-chat chat=<abs-path> topic="..." mode=<mode>` into each session via the existing `_inject_text_into_session` helper, and serves the file via polling endpoints. The `group-chat` skill is extended with `chat=`, `mode=`, and `topic=` arguments; `mode=topic` replaces git-centric phases with freeform task coordination. The UI adds list-view multi-select, a floating toolbar, a modal, and a live-reader panel — all in `static/index.html`.

**Tech Stack:** Python stdlib (server.py), vanilla JS/CSS (single-file static/index.html), Markdown skill file (~/.claude/skills/group-chat/SKILL.md).

---

## File Map

| File | Change |
|---|---|
| `~/.claude/skills/group-chat/SKILL.md` | Add `chat=`, `mode=`, `topic=` args; add topic-mode Phase 0 / Phase B / Phase C sections |
| `server.py` | Add `_coordinate_sessions()`, `_group_chat_read()`, `_group_chat_post()` helpers + 3 route branches |
| `static/index.html` | CSS, `selectedListIds` state, list multi-select, toolbar, modal, live-reader panel |
| `tests/test_smoke.py` | 3 smoke-level assertions for new server helpers |
| `changelog.d/added-multi-session-coordination-2026-05-06.md` | Changelog snippet |

---

## Task 1: Extend group-chat skill

**Files:**
- Modify: `~/.claude/skills/group-chat/SKILL.md`

- [ ] **Step 1: Read current skill to orient**

```bash
cat ~/.claude/skills/group-chat/SKILL.md | head -30
```

Confirm the file exists and starts with `---` frontmatter.

- [ ] **Step 2: Add topic-mode Phase 0 section**

Find the block that starts with `## Phase 0 — Identity & ownership` and contains `### 0.2 Compute owned hunks`. Insert a new `### 0.0 Mode & chat path resolution` block immediately before `### 0.1 Establish a stable session tag`:

```markdown
### 0.0 Mode & chat path resolution

Before anything else, parse two `$ARGUMENTS` values:

- **`chat=<path>`** — if present, use this absolute path as the group chat file for all phases instead of the hardcoded `/Users/amirfish/Apps/claude-command-center/GROUP_CHAT.md`. The server always supplies an absolute path (no `~` expansion needed).
- **`mode=topic|git`** — if `mode=topic`, skip Phase 0.2 (hunk ownership) entirely and follow the **topic-mode** branches in Phases B and C instead. If absent or `mode=git`, the existing git-mode logic applies.
- **`topic="<text>"`** — decorative. Echo it in your first post so context is visible to all participants.

```

- [ ] **Step 3: Add topic-mode Phase 0.2 alternative**

Find the end of `### 0.2 Compute owned hunks` (the paragraph ending with "...flag as orphaned in your next post and ask."). Append immediately after:

```markdown
**Topic-mode alternative for Phase 0.2** (when `mode=topic`): Instead of running `git status` / `git diff`, describe what this session has been working on based on your recent conversation context. This becomes your "ownership claim" — e.g. `working on: UI redesign for auth flow`. Write this claim into your first Phase C post. No git commands.
```

- [ ] **Step 4: Add topic-mode Phase B table**

Find the heading `## Phase B — Role determination (deterministic, no user input)` and locate `### B.1 Compute flags`. After the existing role table (the one with columns `chat_state | has_hunks | other conditions | Role | Action`), insert:

```markdown
### B.2 Topic-mode role table (when `mode=topic`)

When `mode=topic`, ignore `has_hunks` entirely and use this table instead. Apply the **first** matching row:

| `chat_state` | Condition | Role | Action |
|---|---|---|---|
| `DONE` | — | done | C.0 — output `we're done` |
| `EXECUTING` | `i_am_executor` AND last step not yet done | executor | C.5 — continue/complete step |
| `EXECUTING` | not executor | observer | C.0 |
| `CONSENSUS` | `i_am_executor` | executor | C.5 — start step 1 |
| `CONSENSUS` | not executor | observer | C.0 |
| `PROPOSAL_PENDING` | haven't acked, agree | acker | C.3 |
| `PROPOSAL_PENDING` | haven't acked, disagree | counter | C.4 |
| `PROPOSAL_PENDING` | already acked | observer | C.0 |
| `OPEN_QUESTION` or `EMPTY` | no proposal exists | proposer | C.2 |
| `OPEN_QUESTION` or `EMPTY` | proposal exists, unresolved | observer/acker | re-classify |

**`i_am_executor`** in topic mode: the latest proposal names your tag as **Executor**. Same as git mode — no change to how executor is determined, only to what executor *does*.
```

- [ ] **Step 5: Add topic-mode Phase C.5 alternative**

Find `### C.5 Executor — run the next step`. After the final paragraph of that section (ending with the `git push origin main` block), append:

```markdown
**Topic-mode Phase C.5** (when `mode=topic`): Steps are freeform task reports, not git operations. For each step:

1. **Announce start:**
   ```markdown
   ---
   ## <YYYY-MM-DD> — `<your-tag>` ▶ starting step <N>
   **Posted:** <fresh timestamp>
   Step <N>: <description of what will be done>.
   — `<your-tag>`
   ```
2. **Do the work** described in the proposal for this step (research, write output, summarise, etc.).
3. **Announce completion:**
   ```markdown
   ---
   ## <YYYY-MM-DD> — `<your-tag>` ✅ step <N> done
   **Posted:** <fresh timestamp>
   Result: <one-sentence summary of outcome>. Moving to step <N+1>.
   — `<your-tag>`
   ```

No `git add`, `git commit`, or `git push`. `DONE` state is reached when all steps have `✅ done` entries and no open question remains.
```

- [ ] **Step 6: Update $ARGUMENTS section at bottom of skill**

Find the `## $ARGUMENTS` section (last section of the file). Append to the bullet list:

```markdown
- `chat=<abs-path>` — use this file instead of the hardcoded `GROUP_CHAT.md`. Server always supplies an absolute path.
- `mode=topic|git` — `git` (default) runs existing logic; `topic` activates topic-mode Phases 0.0, B.2, and C.5.
- `topic="<text>"` — coordination subject; echoed in the session's first post.
```

- [ ] **Step 7: Verify skill is syntactically coherent**

```bash
wc -l ~/.claude/skills/group-chat/SKILL.md
```

Expected: line count > original (~497). No `TBD` or placeholder lines in new sections.

---

## Task 2: Server helper `_coordinate_sessions` + POST `/api/coordinate`

**Files:**
- Modify: `server.py` (add helper function near line 9653 where `_inject_text_into_session` lives, add route near line 15807)
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write failing smoke test first**

Add to `tests/test_smoke.py`, inside the `TestRepoContextHelpers` class, after the last test method:

```python
def test_coordinate_sessions_helper_exists(self):
    """_coordinate_sessions must exist and reject empty topic."""
    for mod in ("server",):
        sys.modules.pop(mod, None)
    import server
    self.assertTrue(hasattr(server, "_coordinate_sessions"))
    result = server._coordinate_sessions({"session_ids": ["abc"], "topic": ""})
    self.assertFalse(result["ok"])
    self.assertIn("error", result)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/amirfish/Apps/claude-command-center-wt-i-want-a-feature-that-i-can-select-multi
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_coordinate_sessions_helper_exists -v
```

Expected: `FAILED` with `AttributeError: module 'server' has no attribute '_coordinate_sessions'`

- [ ] **Step 3: Add `_coordinate_sessions` to server.py**

Find the line `def _inject_text_into_session(session_id, text):` (line ~9653). Insert the following function **immediately before** it:

```python
def _coordinate_sessions(payload):
    """Create a group-chat file and inject /group-chat into selected sessions."""
    session_ids = payload.get("session_ids") or []
    topic = (payload.get("topic") or "").strip()
    mode = (payload.get("mode") or "topic").strip()
    sessions_meta = payload.get("sessions_meta") or []
    include_human = payload.get("include_human", True)

    if not session_ids or not topic:
        return {"ok": False, "error": "missing session_ids or topic"}
    if mode not in ("topic", "git"):
        mode = "topic"

    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    try:
        os.makedirs(group_chats_dir, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"cannot create group-chats dir: {exc}"}

    chat_path = os.path.join(group_chats_dir, f"{slug}-{ts}.md")

    name_map = {m["session_id"]: m.get("display_name") or m["session_id"]
                for m in sessions_meta if m.get("session_id")}
    participant_names = [name_map.get(sid, sid) for sid in session_ids]
    if include_human:
        participant_names.append("human")
    participants_str = ", ".join(f"`{n}`" for n in participant_names)

    now = datetime.now()
    day_name = now.strftime("%A")
    try:
        tz_name = datetime.now().astimezone().strftime("%Z")
    except Exception:
        tz_name = "local"
    full_ts = now.strftime(f"%Y-%m-%d {day_name} %H:%M:%S") + f" {tz_name}"

    header = (
        f"# Group Chat — {topic}\n"
        f"**Started:** {full_ts}\n"
        f"**Mode:** {mode}\n"
        f"**Participants:** {participants_str}\n"
    )
    try:
        with open(chat_path, "w", encoding="utf-8") as fh:
            fh.write(header)
    except OSError as exc:
        return {"ok": False, "error": f"cannot write chat file: {exc}"}

    results = []
    for sid in session_ids:
        text = f'/group-chat chat={chat_path} topic="{topic}" mode={mode}'
        inject_result = _inject_text_into_session(sid, text)
        results.append({
            "session_id": sid,
            "ok": bool(inject_result.get("ok")),
            "error": inject_result.get("error", ""),
        })

    return {"ok": True, "chat_path": chat_path, "results": results}
```

**Note:** `datetime` is already imported at module level as `from datetime import datetime, timedelta`.

- [ ] **Step 4: Run test to confirm it passes**

```bash
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_coordinate_sessions_helper_exists -v
```

Expected: `PASSED`

- [ ] **Step 5: Add POST `/api/coordinate` route**

Find the block (line ~15807):
```python
        elif path == "/api/inject-input":
```

Insert the following **immediately before** that block:

```python
        elif path == "/api/coordinate":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            self.send_json(_coordinate_sessions(payload))
```

- [ ] **Step 6: Commit**

```bash
git commit --only server.py tests/test_smoke.py -m "feat(coordinate): add _coordinate_sessions helper and POST /api/coordinate"
```

---

## Task 3: Server `_group_chat_read` + GET `/api/group-chat/read`

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write failing smoke test**

Add to `tests/test_smoke.py` inside `TestRepoContextHelpers`:

```python
def test_group_chat_read_helper_exists_and_rejects_traversal(self):
    """_group_chat_read must exist and block path traversal outside group-chats/."""
    for mod in ("server",):
        sys.modules.pop(mod, None)
    import server
    self.assertTrue(hasattr(server, "_group_chat_read"))
    result, forbidden = server._group_chat_read("/etc/passwd")
    self.assertIsNone(result)
    self.assertEqual(forbidden, "forbidden")
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_group_chat_read_helper_exists_and_rejects_traversal -v
```

Expected: `FAILED` — `AttributeError`

- [ ] **Step 3: Add `_group_chat_read` helper**

Insert the following immediately before `_coordinate_sessions` in `server.py`:

```python
def _group_chat_read(path):
    """Read a group-chat file. Returns (result_dict, None) or (None, 'forbidden')."""
    group_chats_dir = os.path.realpath(os.path.expanduser("~/.claude/group-chats"))
    try:
        real_path = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return None, "forbidden"
    if not (real_path.startswith(group_chats_dir + os.sep) or real_path == group_chats_dir):
        return None, "forbidden"
    try:
        stat_result = os.stat(real_path)
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        return {"ok": True, "content": content, "mtime": stat_result.st_mtime}, None
    except FileNotFoundError:
        return {"ok": False, "error": "not found"}, None
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, None
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_group_chat_read_helper_exists_and_rejects_traversal -v
```

Expected: `PASSED`

- [ ] **Step 5: Add GET `/api/group-chat/read` route**

In `do_GET`, find this block near the end of the GET dispatcher:

```python
        elif path == "/api/identity":
```

Insert immediately **before** it:

```python
        elif path == "/api/group-chat/read":
            from urllib.parse import urlparse, parse_qs
            parsed_qs = urlparse(self.path)
            qs_params = parse_qs(parsed_qs.query)
            chat_path = (qs_params.get("path") or [""])[0]
            if not chat_path:
                self.send_json({"ok": False, "error": "missing path"})
            else:
                result, forbidden = _group_chat_read(chat_path)
                if forbidden:
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"forbidden"}')
                else:
                    self.send_json(result)
```

- [ ] **Step 6: Commit**

```bash
git commit --only server.py tests/test_smoke.py -m "feat(coordinate): add _group_chat_read helper and GET /api/group-chat/read"
```

---

## Task 4: Server `_group_chat_post` + POST `/api/group-chat/post`

**Files:**
- Modify: `server.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Write failing smoke test**

Add to `tests/test_smoke.py` inside `TestRepoContextHelpers`:

```python
def test_group_chat_post_helper_exists_and_rejects_traversal(self):
    """_group_chat_post must exist and block writes outside group-chats/."""
    for mod in ("server",):
        sys.modules.pop(mod, None)
    import server
    self.assertTrue(hasattr(server, "_group_chat_post"))
    result = server._group_chat_post("/etc/passwd", "hacked")
    self.assertFalse(result["ok"])
    self.assertIn("forbidden", result.get("error", ""))
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_group_chat_post_helper_exists_and_rejects_traversal -v
```

Expected: `FAILED` — `AttributeError`

- [ ] **Step 3: Add `_group_chat_post` helper**

Insert immediately before `_group_chat_read` in `server.py`:

```python
def _group_chat_post(path, text):
    """Append a human entry to a group-chat file."""
    group_chats_dir = os.path.realpath(os.path.expanduser("~/.claude/group-chats"))
    try:
        real_path = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return {"ok": False, "error": "forbidden"}
    if not real_path.startswith(group_chats_dir + os.sep):
        return {"ok": False, "error": "forbidden"}
    now = datetime.now()
    day_name = now.strftime("%A")
    try:
        tz_name = datetime.now().astimezone().strftime("%Z")
    except Exception:
        tz_name = "local"
    full_ts = now.strftime(f"%Y-%m-%d {day_name} %H:%M:%S") + f" {tz_name}"
    entry = f"\n---\n\n## {full_ts} — Human\n\n{text}\n"
    try:
        with open(real_path, "a", encoding="utf-8") as fh:
            fh.write(entry)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
python -m pytest tests/test_smoke.py::TestRepoContextHelpers::test_group_chat_post_helper_exists_and_rejects_traversal -v
```

Expected: `PASSED`

- [ ] **Step 5: Add POST `/api/group-chat/post` route**

Find the `elif path == "/api/coordinate":` block you added in Task 2. Insert immediately **after** its closing line:

```python
        elif path == "/api/group-chat/post":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            chat_path = (payload.get("path") or "").strip()
            text = (payload.get("text") or "").strip()
            if not chat_path or not text:
                self.send_json({"ok": False, "error": "missing path or text"})
            else:
                self.send_json(_group_chat_post(chat_path, text))
```

- [ ] **Step 6: Run all smoke tests**

```bash
python -m pytest tests/test_smoke.py -v
```

Expected: all tests pass. If any fail unrelated to this task, investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git commit --only server.py tests/test_smoke.py -m "feat(coordinate): add _group_chat_post helper and POST /api/group-chat/post"
```

---

## Task 5: UI — CSS + `selectedListIds` state + list multi-select

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add CSS for selected list rows and toolbar**

Find the CSS block that contains:
```css
  .conv-item.active { background: var(--surface-2); border-left-color: var(--purple); }
```
(around line 1435). Immediately **after** that line, add:

```css
  .conv-item.list-selected { border-left: 3px solid var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); }
  .coord-toolbar { display:none; position:sticky; bottom:0; z-index:20; background:var(--surface); border-top:1px solid var(--border); padding:8px 12px; gap:8px; align-items:center; font-size:12px; color:var(--text-muted); flex-shrink:0; }
  .coord-toolbar.visible { display:flex; }
  .coord-toolbar .coord-count { flex:1; }
  .coord-toolbar .coord-btn { padding:4px 10px; font-size:12px; background:var(--accent); color:#fff; border:none; border-radius:4px; cursor:pointer; }
  .coord-toolbar .coord-clear-btn { padding:4px 8px; font-size:13px; background:transparent; border:1px solid var(--border); color:var(--text-muted); border-radius:4px; cursor:pointer; line-height:1; }
```

- [ ] **Step 2: Add `selectedListIds` state variable**

Find line 8023:
```js
  let selectedCardIds = new Set();
```
Immediately **after** it, add:
```js
  let selectedListIds = new Set();
```

- [ ] **Step 3: Add toolbar HTML inside `#convListPanel`**

Find the HTML block (around line 4462):
```html
      <div class="log-list" id="convList" style="overflow-y:auto;flex:1;min-height:0;max-height:none;">
```
Immediately **after the closing** `</div>` of `#convList` (line 4464: `</div>`) and **before** the `<div class="kanban-board"` line, add:

```html
      <div class="coord-toolbar" id="coordToolbar">
        <span class="coord-count" id="coordCount"></span>
        <button class="coord-btn" id="coordBtn">Coordinate&hellip;</button>
        <button class="coord-clear-btn" id="coordClearBtn" title="Clear selection">&times;</button>
      </div>
```

- [ ] **Step 4: Add `updateCoordToolbar()` function**

Find the line (around 8025):
```js
  async function moveCardsToColumn(cardIds, targetCol) {
```
Immediately **before** it, add:

```js
  function updateCoordToolbar() {
    const toolbar = document.getElementById('coordToolbar');
    const countEl = document.getElementById('coordCount');
    if (!toolbar) return;
    if (selectedListIds.size >= 2) {
      toolbar.classList.add('visible');
      if (countEl) countEl.textContent = selectedListIds.size + ' sessions selected';
    } else {
      toolbar.classList.remove('visible');
    }
  }
```

- [ ] **Step 5: Wire toolbar buttons**

Find the block that starts (around line 6887):
```js
  const $cpCloseBtn = document.getElementById('cpCloseBtn');
```
After that area, find a convenient spot near other `getElementById` wiring. Add:

```js
  const $coordBtn = document.getElementById('coordBtn');
  const $coordClearBtn = document.getElementById('coordClearBtn');
  if ($coordBtn) $coordBtn.addEventListener('click', () => openCoordModal());
  if ($coordClearBtn) $coordClearBtn.addEventListener('click', () => {
    selectedListIds.clear();
    document.querySelectorAll('.conv-item.list-selected').forEach(el => el.classList.remove('list-selected'));
    updateCoordToolbar();
  });
```

- [ ] **Step 6: Add multi-select click handlers inside `renderConversationList`**

Find the block (around line 10359):
```js
    $convList.querySelectorAll('.conv-item').forEach(el => {
      el.addEventListener('click', (ev) => {
        // Ignore clicks that started the inline editor, archive button,
        // or that landed on the title (which now triggers rename instead
        // of opening the conversation — the pencil's job moved here).
        if (ev.target.closest('[data-role="edit"]') || ev.target.closest('[data-role="archive"]') || ev.target.closest('[data-role="merge"]') || ev.target.closest('[data-role="start"]') || ev.target.closest('[data-role="unpin-repo"]') || ev.target.closest('.conv-title-input') || ev.target.closest('[data-role="title"]')) return;
        selectConversation(el.dataset.id);
      });
```

Replace the inner `click` handler (keep the `attachDragHandlers` call) with:

```js
    $convList.querySelectorAll('.conv-item').forEach(el => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-role="edit"]') || ev.target.closest('[data-role="archive"]') || ev.target.closest('[data-role="merge"]') || ev.target.closest('[data-role="start"]') || ev.target.closest('[data-role="unpin-repo"]') || ev.target.closest('.conv-title-input') || ev.target.closest('[data-role="title"]')) return;
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey) {
          ev.preventDefault();
          if (selectedListIds.has(el.dataset.id)) {
            selectedListIds.delete(el.dataset.id);
            el.classList.remove('list-selected');
          } else {
            selectedListIds.add(el.dataset.id);
            el.classList.add('list-selected');
          }
          updateCoordToolbar();
          return;
        }
        if (selectedListIds.size > 0) {
          document.querySelectorAll('.conv-item.list-selected').forEach(n => n.classList.remove('list-selected'));
          selectedListIds.clear();
          updateCoordToolbar();
        }
        selectConversation(el.dataset.id);
      });
```

- [ ] **Step 7: Restore `.list-selected` class on re-render**

After the `el.classList.add('list-selected')` restore in the existing kanban code (the pattern `if (selectedCardIds.has(card.dataset.id)) card.classList.add('selected');` at line ~8872), find the equivalent spot in the list render.

In `renderConversationList`, find the block that restores `.active` class after re-render. Look for the block starting at line ~10669:
```js
      $convList.querySelectorAll('.conv-item').forEach(n => {
```
After the `.active` restore loop, add a second pass:

```js
    $convList.querySelectorAll('.conv-item').forEach(el => {
      if (selectedListIds.has(el.dataset.id)) el.classList.add('list-selected');
    });
    updateCoordToolbar();
```

- [ ] **Step 8: Commit**

```bash
git commit --only static/index.html -m "feat(coordinate): list multi-select, toolbar, selectedListIds state"
```

---

## Task 6: UI — Coordinate modal

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add modal CSS**

Find the `.coord-toolbar` CSS block added in Task 5. Immediately after it, add:

```css
  .coord-modal-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.55); z-index:1000; align-items:center; justify-content:center; }
  .coord-modal-backdrop.visible { display:flex; }
  .coord-modal { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:20px; width:420px; max-width:calc(100vw - 32px); box-shadow:0 8px 32px rgba(0,0,0,0.5); display:flex; flex-direction:column; gap:14px; }
  .coord-modal h3 { margin:0; font-size:14px; font-weight:600; color:var(--text); }
  .coord-modal label { font-size:12px; color:var(--text-muted); display:block; margin-bottom:4px; }
  .coord-modal input[type="text"] { width:100%; box-sizing:border-box; padding:6px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; }
  .coord-modal .mode-row { display:flex; gap:16px; font-size:13px; }
  .coord-modal .mode-hint { font-size:11px; color:var(--text-muted); margin-top:2px; }
  .coord-modal .participants-list { display:flex; flex-direction:column; gap:6px; max-height:160px; overflow-y:auto; }
  .coord-modal .participant-row { display:flex; align-items:center; gap:8px; font-size:12px; }
  .coord-modal .participant-row .p-name { font-weight:500; color:var(--text); }
  .coord-modal .participant-row .p-cwd { color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:200px; }
  .coord-modal .modal-footer { display:flex; justify-content:flex-end; gap:8px; margin-top:4px; }
  .coord-modal .modal-cancel { padding:6px 14px; background:transparent; border:1px solid var(--border); color:var(--text-muted); border-radius:4px; cursor:pointer; font-size:13px; }
  .coord-modal .modal-start { padding:6px 14px; background:var(--accent); color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:13px; font-weight:500; }
  .coord-modal .modal-error { font-size:12px; color:var(--red); display:none; }
  .coord-modal .modal-error.visible { display:block; }
```

- [ ] **Step 2: Add modal HTML to `<body>`**

Find the closing `</body>` tag at the very end of the file. Immediately **before** it, add:

```html
<div class="coord-modal-backdrop" id="coordModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="coordModalTitle">
  <div class="coord-modal">
    <h3 id="coordModalTitle">Coordinate sessions</h3>
    <div>
      <label for="coordTopicInput">Topic</label>
      <input type="text" id="coordTopicInput" placeholder="e.g. Redesign the auth flow" maxlength="120" autocomplete="off">
    </div>
    <div>
      <label>Mode</label>
      <div class="mode-row">
        <label><input type="radio" name="coordMode" value="topic" id="coordModeTopicRadio"> Topic coordination</label>
        <label><input type="radio" name="coordMode" value="git" id="coordModeGitRadio"> Git coordination</label>
      </div>
      <div class="mode-hint" id="coordModeHint"></div>
    </div>
    <div>
      <label>Participants</label>
      <div class="participants-list" id="coordParticipantsList"></div>
    </div>
    <div class="modal-error" id="coordModalError"></div>
    <div class="modal-footer">
      <button class="modal-cancel" id="coordModalCancel">Cancel</button>
      <button class="modal-start" id="coordModalStart">Start coordination</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add `openCoordModal()` function**

Find the `updateCoordToolbar()` function added in Task 5. Immediately **after** it, add:

```js
  function openCoordModal() {
    if (selectedListIds.size < 2) return;
    const backdrop = document.getElementById('coordModalBackdrop');
    const topicInput = document.getElementById('coordTopicInput');
    const participantsList = document.getElementById('coordParticipantsList');
    const modeHint = document.getElementById('coordModeHint');
    const errorEl = document.getElementById('coordModalError');
    if (!backdrop) return;

    // Build participant rows from selectedListIds
    const selectedRows = Array.from(selectedListIds)
      .map(id => conversationsData.find(c => c.id === id))
      .filter(Boolean);

    // Auto-detect mode: git if all share the same repo root, topic otherwise
    const cwds = selectedRows.map(r => rowRepoPath(r)).filter(Boolean);
    const allSameRepo = cwds.length === selectedRows.length && cwds.length > 0 && cwds.every(p => p === cwds[0]);
    const autoMode = allSameRepo ? 'git' : 'topic';
    const gitRadio = document.getElementById('coordModeGitRadio');
    const topicRadio = document.getElementById('coordModeTopicRadio');
    if (autoMode === 'git' && gitRadio) gitRadio.checked = true;
    else if (topicRadio) topicRadio.checked = true;
    if (modeHint) modeHint.textContent = allSameRepo
      ? 'Auto-detected: all sessions share a repo.'
      : 'Auto-detected: sessions span multiple repos.';

    // Render participants
    participantsList.innerHTML = selectedRows.map(r => {
      const sid = r.session_id || r.id;
      const name = escapeHtml(r.display_name || sid);
      const cwd = rowRepoPath(r) || r.session_cwd || '';
      const shortCwd = cwd.length > 40 ? '…' + cwd.slice(-39) : cwd;
      return '<div class="participant-row">'
        + '<input type="checkbox" checked data-sid="' + escapeAttr(sid) + '" data-name="' + escapeAttr(r.display_name || sid) + '" data-cwd="' + escapeAttr(cwd) + '">'
        + '<span class="p-name">' + name + '</span>'
        + '<span class="p-cwd">' + escapeHtml(shortCwd) + '</span>'
        + '</div>';
    }).join('') + '<div class="participant-row">'
      + '<input type="checkbox" checked id="coordHumanCheck">'
      + '<span class="p-name">You (human)</span>'
      + '<span class="p-cwd">posts directly to chat</span>'
      + '</div>';

    if (errorEl) { errorEl.textContent = ''; errorEl.classList.remove('visible'); }
    if (topicInput) { topicInput.value = ''; }
    backdrop.classList.add('visible');
    setTimeout(() => { if (topicInput) topicInput.focus(); }, 50);
  }

  async function startCoordination() {
    const backdrop = document.getElementById('coordModalBackdrop');
    const topicInput = document.getElementById('coordTopicInput');
    const errorEl = document.getElementById('coordModalError');
    const topic = (topicInput ? topicInput.value : '').trim();
    if (!topic) {
      if (errorEl) { errorEl.textContent = 'Topic is required.'; errorEl.classList.add('visible'); }
      if (topicInput) topicInput.focus();
      return;
    }
    const modeEl = document.querySelector('input[name="coordMode"]:checked');
    const mode = modeEl ? modeEl.value : 'topic';
    const includeHuman = !!(document.getElementById('coordHumanCheck') || {checked: true}).checked;

    const checkedBoxes = Array.from(
      (document.getElementById('coordParticipantsList') || {querySelectorAll: () => []})
        .querySelectorAll('input[type="checkbox"][data-sid]')
    ).filter(cb => cb.checked);

    const sessionIds = checkedBoxes.map(cb => cb.dataset.sid);
    const sessionsMeta = checkedBoxes.map(cb => ({
      session_id: cb.dataset.sid,
      display_name: cb.dataset.name || cb.dataset.sid,
      cwd: cb.dataset.cwd || '',
    }));

    if (sessionIds.length < 2) {
      if (errorEl) { errorEl.textContent = 'Select at least 2 sessions.'; errorEl.classList.add('visible'); }
      return;
    }

    const startBtn = document.getElementById('coordModalStart');
    if (startBtn) startBtn.disabled = true;
    try {
      const result = await ccPostJson('/api/coordinate', {
        session_ids: sessionIds, topic, mode, sessions_meta: sessionsMeta, include_human: includeHuman,
      });
      if (!result.ok) {
        if (errorEl) { errorEl.textContent = result.error || 'Failed to start coordination.'; errorEl.classList.add('visible'); }
        return;
      }
      // Per-session failure toasts
      (result.results || []).forEach(r => {
        if (!r.ok) showOpToast('Could not reach session — check its terminal (' + (r.error || 'tty not found') + ')', 'error');
      });
      if (backdrop) backdrop.classList.remove('visible');
      selectedListIds.clear();
      document.querySelectorAll('.conv-item.list-selected').forEach(el => el.classList.remove('list-selected'));
      updateCoordToolbar();
      openGroupChatReader(result.chat_path, topic, mode, includeHuman);
    } catch (err) {
      if (errorEl) { errorEl.textContent = 'Request failed: ' + err.message; errorEl.classList.add('visible'); }
    } finally {
      if (startBtn) startBtn.disabled = false;
    }
  }
```

- [ ] **Step 4: Wire modal buttons**

Find the block where `$coordBtn` and `$coordClearBtn` are wired (added in Task 5). Immediately after those lines, add:

```js
  const $coordModalCancel = document.getElementById('coordModalCancel');
  const $coordModalStart = document.getElementById('coordModalStart');
  const $coordModalBackdrop = document.getElementById('coordModalBackdrop');
  if ($coordModalCancel) $coordModalCancel.addEventListener('click', () => {
    if ($coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
  });
  if ($coordModalStart) $coordModalStart.addEventListener('click', () => startCoordination());
  if ($coordTopicInput) {
    document.getElementById('coordTopicInput').addEventListener('keydown', ev => {
      if (ev.key === 'Enter') startCoordination();
      if (ev.key === 'Escape' && $coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
    });
  }
  if ($coordModalBackdrop) $coordModalBackdrop.addEventListener('click', ev => {
    if (ev.target === $coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
  });
```

Note: `$coordTopicInput` is not declared yet; replace the reference with `document.getElementById('coordTopicInput')` inline as shown.

- [ ] **Step 5: Commit**

```bash
git commit --only static/index.html -m "feat(coordinate): coordinate modal — topic, mode, participants"
```

---

## Task 7: UI — Live-reader panel

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add reader CSS**

After the `.coord-modal .modal-error.visible` CSS block added in Task 6, add:

```css
  .gc-reader { display:flex; flex-direction:column; height:100%; overflow:hidden; }
  .gc-reader-header { display:flex; align-items:center; gap:8px; padding:10px 14px; border-bottom:1px solid var(--border); flex-shrink:0; }
  .gc-reader-header .gc-topic { font-weight:600; font-size:13px; color:var(--text); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .gc-reader-header .gc-mode-badge { font-size:11px; padding:2px 6px; border-radius:10px; background:var(--surface-2); color:var(--text-muted); flex-shrink:0; }
  .gc-reader-header .gc-close { background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:16px; padding:2px 6px; line-height:1; }
  .gc-reader-body { flex:1; overflow-y:auto; padding:14px; font-size:13px; line-height:1.55; color:var(--text); white-space:pre-wrap; font-family:monospace; }
  .gc-reader-body .gc-poll-error { color:var(--red); font-size:12px; padding:6px 0; }
  .gc-reader-input-row { display:flex; gap:8px; padding:10px 14px; border-top:1px solid var(--border); flex-shrink:0; }
  .gc-reader-input-row input { flex:1; padding:6px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; }
  .gc-reader-input-row button { padding:6px 12px; background:var(--accent); color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:13px; }
```

- [ ] **Step 2: Add `openGroupChatReader()` function**

Immediately after the `startCoordination()` function added in Task 6, add:

```js
  let _gcReaderInterval = null;
  let _gcReaderPath = null;
  let _gcLastMtime = null;
  let _gcPollFailCount = 0;

  function openGroupChatReader(chatPath, topic, mode, includeHuman) {
    _gcReaderPath = chatPath;
    _gcLastMtime = null;
    _gcPollFailCount = 0;

    // Build reader DOM in the conv pane (element uses class="conv-pane", no ID)
    const pane = document.querySelector('.conv-pane');
    if (!pane) return;

    const topicSafe = escapeHtml(topic);
    const modeSafe = escapeHtml(mode);
    pane.innerHTML = '<div class="gc-reader" id="gcReader">'
      + '<div class="gc-reader-header">'
        + '<span class="gc-topic" title="' + topicSafe + '">' + topicSafe + '</span>'
        + '<span class="gc-mode-badge">' + modeSafe + '</span>'
        + '<button class="gc-close" id="gcCloseBtn" title="Close reader">&times;</button>'
      + '</div>'
      + '<div class="gc-reader-body" id="gcReaderBody">Loading…</div>'
      + (includeHuman
        ? '<div class="gc-reader-input-row" id="gcInputRow">'
            + '<input type="text" id="gcHumanInput" placeholder="Add to chat…" autocomplete="off">'
            + '<button id="gcSendBtn">Send</button>'
          + '</div>'
        : '')
      + '</div>';

    document.getElementById('gcCloseBtn').addEventListener('click', closeGroupChatReader);

    if (includeHuman) {
      const gcSendBtn = document.getElementById('gcSendBtn');
      const gcHumanInput = document.getElementById('gcHumanInput');
      if (gcSendBtn) gcSendBtn.addEventListener('click', () => sendHumanGcPost());
      if (gcHumanInput) gcHumanInput.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') sendHumanGcPost();
      });
    }

    // Start polling
    if (_gcReaderInterval) clearInterval(_gcReaderInterval);
    pollGroupChatReader();
    _gcReaderInterval = setInterval(pollGroupChatReader, 3000);
  }

  async function pollGroupChatReader() {
    if (!_gcReaderPath) return;
    const body = document.getElementById('gcReaderBody');
    if (!body) { closeGroupChatReader(); return; }
    try {
      const res = await fetch('/api/group-chat/read?path=' + encodeURIComponent(_gcReaderPath));
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      _gcPollFailCount = 0;
      // Remove stale error banner if present
      const errBanner = body.querySelector('.gc-poll-error');
      if (errBanner) errBanner.remove();
      if (!data.ok) { body.textContent = data.error || 'File not found.'; return; }
      if (data.mtime !== _gcLastMtime) {
        _gcLastMtime = data.mtime;
        const atBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 40;
        body.textContent = data.content;
        if (atBottom) body.scrollTop = body.scrollHeight;
      }
    } catch (_err) {
      _gcPollFailCount++;
      if (_gcPollFailCount >= 3) {
        let errBanner = body.querySelector('.gc-poll-error');
        if (!errBanner) {
          errBanner = document.createElement('div');
          errBanner.className = 'gc-poll-error';
          body.prepend(errBanner);
        }
        errBanner.textContent = '⚠ Lost connection to chat file — retrying…';
      }
    }
  }

  async function sendHumanGcPost() {
    if (!_gcReaderPath) return;
    const input = document.getElementById('gcHumanInput');
    const text = input ? input.value.trim() : '';
    if (!text) return;
    try {
      await ccPostJson('/api/group-chat/post', { path: _gcReaderPath, text });
      if (input) input.value = '';
      await pollGroupChatReader();
    } catch (err) {
      showOpToast('Send failed: ' + err.message, 'error');
    }
  }

  function closeGroupChatReader() {
    if (_gcReaderInterval) { clearInterval(_gcReaderInterval); _gcReaderInterval = null; }
    _gcReaderPath = null;
    _gcLastMtime = null;
    _gcPollFailCount = 0;
    // Restore pane to its last conversation or empty state
    const pane = document.querySelector('.conv-pane');
    if (pane) pane.innerHTML = '';
    if (typeof currentConversation === 'string' && currentConversation) {
      try { selectConversation(currentConversation); } catch (_) {}
    }
  }
```

- [ ] **Step 3: Verify conv pane selector**

The conv pane element uses `class="conv-pane"` with no ID (confirmed at HTML line 4551). The `openGroupChatReader` and `closeGroupChatReader` functions must use `document.querySelector('.conv-pane')`. Verify your code does NOT reference `getElementById('convPane')` — that element does not exist.

- [ ] **Step 4: Commit**

```bash
git commit --only static/index.html -m "feat(coordinate): live-reader panel with polling and human input"
```

---

## Task 8: Changelog entry + final smoke run

**Files:**
- Create: `changelog.d/added-multi-session-coordination-2026-05-06.md`

- [ ] **Step 1: Write changelog snippet**

Create `changelog.d/added-multi-session-coordination-2026-05-06.md`:

```markdown
Multi-session coordination: Ctrl/Shift-click sessions in the conversation list, click "Coordinate…", enter a topic, and Claude Code sessions self-organize via a fresh per-topic group-chat file. Live-reader panel in the conv pane lets you follow and participate directly from the CCC.
```

- [ ] **Step 2: Run full smoke test suite**

```bash
python -m pytest tests/test_smoke.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit changelog**

```bash
git commit --only changelog.d/added-multi-session-coordination-2026-05-06.md -m "chore: changelog snippet for multi-session coordination"
```

---

## Quick manual test checklist

After all tasks:

1. Start the dev server: `./run.sh`
2. Open CCC in browser at `http://localhost:8090`
3. Ctrl-click two session rows → toolbar appears saying "2 sessions selected"
4. Click "Coordinate…" → modal opens with topic field, mode radio (auto-detected), two participant checkboxes + "You (human)"
5. Type a topic, click "Start coordination"
6. Conv pane switches to live-reader showing the new `.md` file header
7. Type a message in the human input, press Enter → message appended to file, reader updates
8. Open `~/.claude/group-chats/` in Terminal → confirm the file was created with correct header
9. Click ✕ in reader header → conv pane returns to last conversation
10. Click ✕ in toolbar → selection cleared, toolbar hides
