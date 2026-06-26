# New Session Default Object Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the new-session screen feel like the normal messaging composer, while every newly spawned session is automatically assigned to a durable default object.

**Architecture:** Keep the bottom composer as the only primary launch surface. Demote the center stage to a quiet onboarding hint with a collapsed fresh-folder affordance, and assign new sessions to a deterministic `Inbox` Flow object from the client after spawn once a session id is available.

**Tech Stack:** Stdlib Python server, single-file browser app in `static/app.js`, inline CSS in `static/app.css`, static smoke tests in `tests/test_smoke.py`.

## Global Constraints

- Public OSS: no internal paths, private URLs, PII, or personal workflow names in code, comments, docs, or tests.
- Runtime stays stdlib-only; do not add Python, npm, or browser build dependencies.
- `static/index.html` remains a single-file app shell, and `static/app.js` / `static/app.css` remain inline frontend assets.
- `/api/*` response shapes are stable public contracts; this plan uses existing `/api/objects/*` endpoints and does not change server API shapes.
- Default object title and id must be generic: `Inbox` and `new-session-inbox`.
- New-session primary action remains: type a prompt in the bottom composer and press Enter.
- Fresh-folder creation is secondary and collapsed by default.
- Use TDD: write each test first, watch it fail, then implement the minimal code.
- Do not touch unrelated dirty worktree files.

---

## File Structure

- Modify `tests/test_smoke.py`: add string-level smoke tests for the new-session UI shape and default-object assignment helpers, one red/green slice at a time.
- Modify `static/index.html`: add a small object context slot to the existing new-session input context strip.
- Modify `static/app.js`: render the quieter new-session stage, toggle a new composer class in new-session mode, create/reuse the default object, render the object chip, assign spawned sessions to the object, and reconcile pending assignment when `session_id` arrives late.
- Modify `static/app.css`: give the bottom composer more room in new-session mode, style the quiet center hint, style the collapsed fresh-folder affordance, and style the object context chip.
- Add `changelog.d/changed-new-session-default-object-2026-06-26.md`: one Keep a Changelog snippet.

---

### Task 1: Lock The New-Session UI Shape With A Smoke Test

**Files:**
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: static source text from `static/app.js`, `static/app.css`, and `static/index.html`.
- Produces: a failing test that defines the expected quiet center stage and expanded composer.

- [ ] **Step 1: Write the failing tests**

Add this method to `class TestServerImports(unittest.TestCase)` near the existing frontend smoke tests:

```python
    def test_new_session_stage_demotes_center_card_and_expands_composer(self):
        """New-session mode should make the bottom composer primary and keep
        center content as quiet onboarding, not a competing start form."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("class=\"ns-stage ns-stage-quiet\"", app_js)
        self.assertIn("class=\"ns-stage-title\">New session</div>", app_js)
        self.assertIn("class=\"ns-new-project-details\"", app_js)
        self.assertIn("Create a fresh folder", app_js)
        self.assertNotIn("class=\"ns-hero-title\">🚀 Start a new session</div>", app_js)
        self.assertIn("$convInputBar.classList.toggle('is-new-session-launch', isNewSession);", app_js)
        self.assertIn(".conv-input-bar.is-new-session-launch textarea", app_css)
        self.assertIn("min-height: 96px;", app_css)

```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_new_session_stage_demotes_center_card_and_expands_composer -q
```

Expected: FAIL because the quiet-stage strings and composer class do not exist yet.

- [ ] **Step 3: Commit**

Do not commit after the red test alone. Carry this failing test into Task 2, then commit the passing slice.

---

### Task 2: Make The Bottom Composer The Primary New-Session Surface

**Files:**
- Modify: `static/app.js`
- Modify: `static/app.css`

**Interfaces:**
- Consumes: existing `enterNewSessionMode()`, `_wireNewSessionChooser($view, paneId)`, `updateInputBar()`, and `/api/project/create` flow.
- Produces: `convInputBar.is-new-session-launch` class and a quieter center stage.

- [ ] **Step 1: Replace the center-stage HTML in `enterNewSessionMode()`**

In `static/app.js`, replace the `$view.innerHTML = '<div class="ns-stage">'...` block inside `enterNewSessionMode()` with:

```javascript
      $view.innerHTML = '<div class="ns-stage ns-stage-quiet">'
        + '<div class="empty-state ns-hero ns-hero-quiet" style="height:auto;flex-direction:column;gap:10px;text-align:center;">'
        + '<div class="ns-stage-title">New session</div>'
        + '<div class="ns-stage-subtitle">Choose the object and folder below, then type the first message.</div>'
        + '<details class="ns-new-project-details" id="nsNewProjectDetails">'
        +   '<summary>Create a fresh folder</summary>'
        +   '<div class="ns-choice-card ns-choice-card-compact" id="nsCardNewProject">'
        +     '<div class="ns-choice-title">Fresh folder</div>'
        +     '<span class="ns-name-row">'
        +       '<input type="text" id="nsNewProjectName" class="ns-input" placeholder="Project name…" autocomplete="off" spellcheck="false">'
        +       '<button type="button" id="nsNewProjectDice" class="ns-dice-btn" title="Roll another name">&#127922;</button>'
        +     '</span>'
        +     '<button type="button" id="nsNewProjectCreate" class="ns-create-btn" disabled>Create folder</button>'
        +     '<div class="ns-muted" id="nsNewProjectHint">Creates a folder and selects it below. Then type the first prompt in the composer.</div>'
        +   '</div>'
        + '</details>'
        + '<div class="ns-stage-help">' + escapeHtml(newSessionHelp) + '</div>'
        + '<details class="ns-recipes-details" id="nsExtensionsWrap" style="display:none;">'
        +   '<summary class="ns-recipes-summary">Extend CCC · integration recipes</summary>'
        +   '<div class="nsm-gallery inline-new-session-templates" id="nsExtensionsGallery"></div>'
        + '</details>'
        + '</div></div>';
```

Keep the existing calls immediately after this block:

```javascript
      _wireNewSessionChooser($view, paneId);
      _renderNsExtensions($view, paneId);
```

- [ ] **Step 2: Update fresh-folder button copy after create**

In `_wireNewSessionChooser()`, update the success hint and final button text:

```javascript
        if (hintEl) hintEl.textContent = 'Folder ready. Type the first prompt below and press Enter.';
```

and:

```javascript
        createBtn.textContent = 'Create folder';
```

- [ ] **Step 3: Toggle the launch class in `updateInputBar()`**

In `updateInputBar()`, after `const isNewSession = currentConversation === '__new__';`, add:

```javascript
    if ($convInputBar) {
      $convInputBar.classList.toggle('is-new-session-launch', isNewSession);
    }
```

In the branch that hides the input bar, keep the class from lingering:

```javascript
      $convInputBar.classList.remove('is-new-session-launch');
```

- [ ] **Step 4: Add CSS for the quiet stage and larger composer**

In `static/app.css`, replace the current `.ns-stage`, `.ns-hero-title`, and centered-composer comments with:

```css
/* New-session stage: quiet onboarding. The bottom composer is the primary
   launch surface, so the center of the pane teaches context without competing
   with the input. */
.ns-stage {
  min-height: min(42vh, 340px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: clamp(32px, 8vh, 84px) 20px 20px;
  box-sizing: border-box;
}
.ns-stage-quiet .ns-hero-quiet {
  animation: nsHeroIn 0.18s ease-out;
  opacity: 0.92;
}
.ns-stage-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
}
.ns-stage-subtitle,
.ns-stage-help {
  font-size: 12.5px;
  color: var(--text-muted);
  line-height: 1.45;
  max-width: 560px;
}
.ns-new-project-details {
  width: min(460px, 92vw);
  margin-top: 4px;
}
.ns-new-project-details > summary {
  cursor: pointer;
  color: var(--text-muted);
  font-size: 12px;
  user-select: none;
}
.ns-new-project-details[open] > summary {
  color: var(--accent, #58a6ff);
}
.ns-choice-card-compact {
  max-width: none;
  margin-top: 8px;
  box-shadow: none;
}
.ns-choice-card-compact:hover {
  transform: none;
  box-shadow: none;
}
.conv-input-bar.is-new-session-launch {
  padding: 12px 22px 18px;
  background: color-mix(in srgb, var(--bg) 88%, var(--surface) 12%);
  border-top: 1px solid var(--border);
}
.conv-input-bar.is-new-session-launch textarea {
  min-height: 96px;
  font-size: 15px;
  line-height: 1.45;
  border-color: var(--accent, #58a6ff);
  box-shadow: 0 0 0 3px rgba(88,166,255,0.10);
}
.conv-input-context.visible.is-new-session {
  padding: 8px 22px 0;
}
```

Keep the existing `@keyframes nsHeroIn` block.

- [ ] **Step 5: Run the first test**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_new_session_stage_demotes_center_card_and_expands_composer -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git status --short
git add tests/test_smoke.py static/app.js static/app.css
git commit --only tests/test_smoke.py static/app.js static/app.css -m "fix(ui): demote new session center card"
```

---

### Task 3: Add The Default Object Context And Assignment Hook

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/app.css`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: existing `flowCustomObjects`, `flowNodeParents`, `flowNodeKey(kind, id)`, `persistFlowCustomObjects()`, `persistFlowNodeParents()`, `_objectsApiPost(path, body)`, `refreshConversationList()`, `conversationsData`, and spawn responses from `spawnFromInlineInput(body)`.
- Produces: default object constants, object context chip, immediate object assignment for responses with `session_id`, and pending assignment reconciliation for delayed session ids.

- [ ] **Step 1: Write the failing default-object assignment test**

Add this method to `class TestServerImports(unittest.TestCase)`:

```python
    def test_new_session_default_object_assignment_is_wired(self):
        """Every new session should be assigned to a generic durable object."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")

        self.assertIn('id="newSessionObjectContext"', index_html)
        self.assertIn("const NEW_SESSION_DEFAULT_OBJECT_ID = 'new-session-inbox';", app_js)
        self.assertIn("const NEW_SESSION_DEFAULT_OBJECT_TITLE = 'Inbox';", app_js)
        self.assertIn("function ensureNewSessionDefaultObject()", app_js)
        self.assertIn("function assignSpawnedSessionToDefaultObject(data)", app_js)
        self.assertIn("function reconcilePendingNewSessionObjectAssignments()", app_js)
        self.assertIn("assignSpawnedSessionToDefaultObject(data);", app_js)
        self.assertIn("_objectsApiPost('assign', { session_node_id: flowNodeKey('session', sid), object_id: objectId })", app_js)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_new_session_default_object_assignment_is_wired -q
```

Expected: FAIL because the object context slot and assignment helpers do not exist yet.

- [ ] **Step 3: Add the object context slot to the input context strip**

In `static/index.html`, inside `<div class="conv-input-context" id="convInputContext" ...>`, place this immediately after the workspace row:

```html
          <span id="newSessionObjectContext" class="new-session-object-context" aria-label="Object for new session"></span>
```

- [ ] **Step 4: Add default object constants and storage key**

In `static/app.js`, near the existing object sync helpers and before `_objectsApiPost(path, body)`, add:

```javascript
  const NEW_SESSION_DEFAULT_OBJECT_ID = 'new-session-inbox';
  const NEW_SESSION_DEFAULT_OBJECT_TITLE = 'Inbox';
  const NEW_SESSION_PENDING_OBJECT_KEY = 'ccc-pending-new-session-object-assignments';
```

- [ ] **Step 5: Add helper functions**

In `static/app.js`, after `_objectsApiPost()` and before `_objectsGet()`, add:

```javascript
  function ensureNewSessionDefaultObject() {
    const existing = (flowCustomObjects || []).find(o => o && o.id === NEW_SESSION_DEFAULT_OBJECT_ID);
    if (existing) return existing;
    const now = Date.now();
    const obj = {
      id: NEW_SESSION_DEFAULT_OBJECT_ID,
      title: NEW_SESSION_DEFAULT_OBJECT_TITLE,
      created_at: now,
      updated_at: now,
      status: 'active',
      objective: 'Triage newly started sessions',
    };
    flowCustomObjects.push(obj);
    persistFlowCustomObjects();
    return obj;
  }

  function renderNewSessionObjectContext() {
    const wrap = document.getElementById('newSessionObjectContext');
    if (!wrap) return;
    if (currentConversation !== '__new__') {
      wrap.innerHTML = '';
      wrap.style.display = 'none';
      return;
    }
    const obj = ensureNewSessionDefaultObject();
    wrap.style.display = '';
    wrap.innerHTML = '<span class="nso-label">Object</span>'
      + '<span class="nso-chip" title="New sessions are grouped under this object">'
      + escapeHtml(obj.title || NEW_SESSION_DEFAULT_OBJECT_TITLE)
      + '</span>';
  }

  function loadPendingNewSessionObjectAssignments() {
    try {
      const raw = JSON.parse(localStorage.getItem(NEW_SESSION_PENDING_OBJECT_KEY) || '{}');
      return raw && typeof raw === 'object' ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function savePendingNewSessionObjectAssignments(map) {
    try { localStorage.setItem(NEW_SESSION_PENDING_OBJECT_KEY, JSON.stringify(map || {})); } catch (_) {}
  }

  async function assignSessionNodeToObject(sid, objectId) {
    if (!sid || !objectId) return false;
    const nodeId = flowNodeKey('session', sid);
    const parentNode = flowNodeKey('object', objectId);
    flowNodeParents[nodeId] = parentNode;
    persistFlowNodeParents();
    _objectsApiPost('assign', { session_node_id: flowNodeKey('session', sid), object_id: objectId }).catch(() => {});
    return true;
  }

  function rememberPendingNewSessionObjectAssignment(spawnId, objectId) {
    if (!spawnId || !objectId) return;
    const pending = loadPendingNewSessionObjectAssignments();
    pending[String(spawnId)] = { object_id: objectId, created_at: Date.now() };
    savePendingNewSessionObjectAssignments(pending);
  }

  function assignSpawnedSessionToDefaultObject(data) {
    const obj = ensureNewSessionDefaultObject();
    const objectId = obj && obj.id;
    const sid = data && data.session_id;
    if (sid) {
      assignSessionNodeToObject(sid, objectId);
      return;
    }
    const spawnId = (data && (data.spawn_id || data.pid)) || '';
    rememberPendingNewSessionObjectAssignment(spawnId, objectId);
  }

  function reconcilePendingNewSessionObjectAssignments() {
    const pending = loadPendingNewSessionObjectAssignments();
    const keys = Object.keys(pending);
    if (!keys.length) return;
    const now = Date.now();
    let changed = false;
    for (const key of keys) {
      const rec = pending[key] || {};
      if (now - Number(rec.created_at || 0) > 30 * 60 * 1000) {
        delete pending[key];
        changed = true;
        continue;
      }
      const row = (conversationsData || []).find(c => String(c.spawn_pid || c.pid || '') === key);
      const sid = row && (row.session_id || row.id);
      if (!sid) continue;
      assignSessionNodeToObject(sid, rec.object_id || NEW_SESSION_DEFAULT_OBJECT_ID);
      delete pending[key];
      changed = true;
    }
    if (changed) savePendingNewSessionObjectAssignments(pending);
  }
```

- [ ] **Step 6: Render the object context in new-session mode**

In `enterNewSessionMode()`, after `populateSpawnCwdPicker();`, add:

```javascript
    renderNewSessionObjectContext();
```

In `updateInputBar()`, after toggling `is-new-session-launch`, add:

```javascript
    renderNewSessionObjectContext();
```

- [ ] **Step 7: Assign after successful spawn**

In `spawnFromInlineInput(body)`, inside `if (data.ok) {` immediately after `const placeholder = adoptPendingSpawnPid(tempPid, data.pid, data.log);`, add:

```javascript
        assignSpawnedSessionToDefaultObject(data);
```

- [ ] **Step 8: Reconcile delayed ids after list refreshes**

Find `refreshConversationList()` and call the reconciliation helper after `conversationsData` has been updated from the server payload:

```javascript
      reconcilePendingNewSessionObjectAssignments();
```

Place the call before the sidebar render in that function so a newly resolved session appears under `Inbox` on the same refresh.

- [ ] **Step 9: Add CSS for the object chip**

In `static/app.css`, near the spawn cwd picker styles, add:

```css
.conv-input-context .new-session-object-context {
  display: none;
  align-items: center;
  gap: 6px;
  grid-area: object;
  min-width: 0;
}
.conv-input-context.is-new-session .new-session-object-context {
  display: inline-flex;
}
.conv-input-context.visible.is-new-session {
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "object object"
    "chips chips"
    "cwd worktree";
}
.nso-label {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.nso-chip {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--accent, #58a6ff) 45%, var(--border) 55%);
  background: rgba(88,166,255,0.12);
  color: var(--accent, #58a6ff);
  font-size: 12px;
  font-weight: 650;
}
```

- [ ] **Step 10: Run the second test**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_new_session_default_object_assignment_is_wired -q
```

Expected: PASS.

- [ ] **Step 11: Run focused frontend smoke tests**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_new_session_stage_demotes_center_card_and_expands_composer tests/test_smoke.py::TestServerImports::test_new_session_default_object_assignment_is_wired -q
```

Expected: both tests PASS.

- [ ] **Step 12: Commit Task 3**

Run:

```bash
git status --short
git add tests/test_smoke.py static/index.html static/app.js static/app.css
git commit --only tests/test_smoke.py static/index.html static/app.js static/app.css -m "feat(ui): assign new sessions to inbox object"
```

---

### Task 4: Add Changelog And Final Verification

**Files:**
- Create: `changelog.d/changed-new-session-default-object-2026-06-26.md`
- Verify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: passing implementation from Tasks 2 and 3.
- Produces: release-note snippet and final verification output.

- [ ] **Step 1: Write the changelog snippet**

Create `changelog.d/changed-new-session-default-object-2026-06-26.md` with:

```markdown
- Demoted the new-session center panel and grouped newly spawned sessions under a default Inbox object.
```

- [ ] **Step 2: Run smoke tests**

Run:

```bash
python3 -m pytest tests/test_smoke.py -q
```

Expected: PASS.

- [ ] **Step 3: Inspect the diff**

Run:

```bash
git diff -- tests/test_smoke.py static/index.html static/app.js static/app.css changelog.d/changed-new-session-default-object-2026-06-26.md
```

Expected: diff only covers the new-session UI, default object assignment, tests, and changelog snippet.

- [ ] **Step 4: Commit Task 4**

Run:

```bash
git add changelog.d/changed-new-session-default-object-2026-06-26.md
git commit --only changelog.d/changed-new-session-default-object-2026-06-26.md -m "docs: note new session inbox object"
```

---

## Self-Review

- Spec coverage: the plan makes the bottom composer primary, demotes the center card, preserves fresh-folder creation as a secondary flow, and assigns every new spawn to a durable default object.
- Placeholder scan: no placeholder markers or deferred behavior remain in the plan.
- Type consistency: helper names and constants are consistent across tests and implementation tasks.
- API risk: no server response shapes change; the implementation uses existing local object sync and `/api/objects/assign` for parent links.
- OSS hygiene: object title/id and changelog copy are generic and public-safe.
