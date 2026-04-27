# Drag-to-split conversation pane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drag a conversation card (sidebar list or kanban column) onto the right edge or bottom edge of the conversation pane and open a second conversation alongside the current one — vertical or horizontal split, two panes max, runtime-only state, no API changes.

**Architecture:** Single-file frontend change in `static/index.html`. The five single-instance globals (`currentConversation`, `convLastLine`, `convEventSource`, `_pendingSends`, `_firstUserMsgRendered`) are replaced with a `splitState.panes[i]` array; the old global names are kept as `Object.defineProperty` getter+setter pairs that read/write the *active* pane, so the existing thousands of references compile against the proxy without edits. Renderers, SSE handlers and the composer learn an explicit `paneId` parameter (defaulting to `activePaneId()`). Drop overlay + drop handlers are added to the conversation pane container; per-pane chrome is templated into `.conv-pane` elements wrapped in a `.conv-split[data-orientation]` flex container only when split is engaged.

**Tech Stack:** Vanilla HTML5 drag-and-drop. No new libraries. Single-file `static/index.html` (inline CSS/JS by design — see CLAUDE.md). Python `server.py` is unchanged except for the version bump.

**Spec:** [`docs/superpowers/specs/2026-04-26-conv-split-pane-design.md`](../specs/2026-04-26-conv-split-pane-design.md)

---

## File Map

- **Modify** `static/index.html` — every behavior change (state, DOM, JS, CSS).
- **Modify** `pyproject.toml` — version bump `0.1.3 → 0.2.0`.
- **Modify** `server.py` — `__version__` bump in lockstep.
- **Modify** `CHANGELOG.md` — append `Added` entry under `[Unreleased]`.
- **Untouched:** `tests/test_smoke.py` (no new assertion needed — the repo's testing posture is import-time-only; manual QA covers the JS feature).

> **Conventions reminder.** Per repo CLAUDE.md: explicit-path `git add` (never `-A`/`-a`/`.`), Conventional Commits (`feat(ui)`, `fix(ui)`, `chore`, `docs`), `pyproject.toml` and `server.py` `__version__` bump in lockstep. Anchor edits by symbol/text, not line numbers — line numbers in this plan are advisory and will drift between tasks.

> **Note for agents:** the smoke-test command in this repo uses `unittest`, not `pytest`. Run `python3 -m unittest tests.test_smoke -v` after each task. All three tests must remain green.

---

## Task 1: Introduce `splitState` and the compatibility shim (no UI change)

**Goal:** Replace the five single-instance globals with a per-pane state map plus a getter/setter shim that proxies the old names to the active pane. Behavior is identical (single-pane), but the foundation is in place.

**Files:**
- Modify: `static/index.html` near `let currentConversation = null;` (~line 4083) and the two later globals (`_firstUserMsgRendered` ~line 7117, `_pendingSends` ~line 7199).

- [ ] **Step 1: Locate the existing globals**

Run from the worktree:

```bash
grep -n "let currentConversation\|let convLastLine\|let convEventSource\|let _firstUserMsgRendered\|let _pendingSends" static/index.html
```

Expected output (line numbers may differ slightly):

```
4083:  let currentConversation = null;
4085:  let convLastLine = 0;
4086:  let convEventSource = null;  // SSE connection for tailing live conversations
7117:  let _firstUserMsgRendered = false;
7199:  let _pendingSends = [];
```

- [ ] **Step 2: Add `splitState` and the shim immediately after the existing `let currentConversation = null;` line**

Replace this block:

```javascript
  let currentConversation = null;
  let showArchived = false;  // false = show non-archived, true = show only archived
  let convLastLine = 0;
  let convEventSource = null;  // SSE connection for tailing live conversations
```

With:

```javascript
  // ── Split-pane state ──
  // The conversation pane can show one or two conversations side-by-side
  // (vertical) or stacked (horizontal). Per-pane state lives in
  // splitState.panes[]; the *active* pane is the one keyboard/sidebar
  // actions target. The old single-instance globals (currentConversation,
  // convLastLine, convEventSource, _pendingSends, _firstUserMsgRendered)
  // are kept as compatibility-shim getters/setters on `window` that proxy
  // to splitState.panes[splitState.activeIndex].* so the thousands of
  // existing references compile against the active pane unchanged.
  // Only the renderer / SSE / composer entry points learn paneId.
  function _newPaneState(id) {
    return {
      id: id,
      conversationId: null,
      lastLine: 0,
      eventSource: null,
      pendingSends: [],
      firstUserMsgRendered: false,
    };
  }
  const splitState = {
    orientation: null, // null | 'vertical' | 'horizontal'
    panes: [_newPaneState('p1')],
    activeIndex: 0,
    ratio: 0.5,
  };
  function activePaneId() { return splitState.panes[splitState.activeIndex].id; }
  function paneByPaneId(pid) { return splitState.panes.find(p => p.id === pid) || null; }
  function paneIndexByPaneId(pid) {
    for (let i = 0; i < splitState.panes.length; i++) if (splitState.panes[i].id === pid) return i;
    return -1;
  }

  // Compatibility shim — read/write the active pane via the old global names.
  // DO NOT remove without auditing every reference to currentConversation,
  // convLastLine, convEventSource, _pendingSends, _firstUserMsgRendered.
  Object.defineProperty(window, 'currentConversation', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].conversationId; },
    set(v) { splitState.panes[splitState.activeIndex].conversationId = v; },
  });
  Object.defineProperty(window, 'convLastLine', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].lastLine; },
    set(v) { splitState.panes[splitState.activeIndex].lastLine = v; },
  });
  Object.defineProperty(window, 'convEventSource', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].eventSource; },
    set(v) { splitState.panes[splitState.activeIndex].eventSource = v; },
  });

  let showArchived = false;  // false = show non-archived, true = show only archived
```

(Note: `currentConversation`, `convLastLine`, `convEventSource` no longer have `let` declarations — they are now `window.*` properties. `showArchived` keeps its `let` declaration on its own line.)

- [ ] **Step 3: Convert `_firstUserMsgRendered` and `_pendingSends` declarations to shim getters**

Find `let _firstUserMsgRendered = false;` (~line 7117) and replace with:

```javascript
  Object.defineProperty(window, '_firstUserMsgRendered', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].firstUserMsgRendered; },
    set(v) { splitState.panes[splitState.activeIndex].firstUserMsgRendered = v; },
  });
```

Find `let _pendingSends = [];` (~line 7199) and replace with:

```javascript
  Object.defineProperty(window, '_pendingSends', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].pendingSends; },
    set(v) { splitState.panes[splitState.activeIndex].pendingSends = v; },
  });
```

- [ ] **Step 4: Run smoke tests**

```bash
cd /Users/amirfish/Apps/claude-command-center-wt-conv-split-pane
python3 -m unittest tests.test_smoke -v
```

Expected: 3 tests pass (the change is frontend-only, but we re-run to confirm we didn't bump server.py by accident).

- [ ] **Step 5: Manual QA in the browser**

Start the dev server in the worktree:

```bash
cd /Users/amirfish/Apps/claude-command-center-wt-conv-split-pane && ./run.sh
```

Open the URL it prints. Verify:
1. The sidebar loads conversation list as before.
2. Clicking a conversation in the sidebar opens it in the conversation pane.
3. Sending a message works.
4. Live streaming (SSE) works (open a live agent session and watch updates arrive).
5. Browser DevTools console shows no errors.

If any of (1)-(5) regress, the shim is wrong — the most likely culprit is missing one of the five globals.

- [ ] **Step 6: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center-wt-conv-split-pane
git add static/index.html
git commit -m "$(cat <<'EOF'
refactor(ui): introduce splitState + compatibility shim for conv pane

Replaces the single-instance globals (currentConversation, convLastLine,
convEventSource, _pendingSends, _firstUserMsgRendered) with a
splitState.panes[] map plus getter/setter proxies on `window` that
transparently target the active pane. Behavior is unchanged for the
single-pane case; this is the foundation for the upcoming drag-to-split
feature.
EOF
)"
```

---

## Task 2: Wrap conversation chrome in `.conv-pane`

**Goal:** Move the existing conv toolbar / view / input bar inside a single `.conv-pane[data-pane-id="p1"]` container, and add a hidden close `×` button to its header. Visual output is unchanged.

**Files:**
- Modify: `static/index.html` near the `<div class="conversations-view" id="conversationsView">` block (~line 2664) and its sibling input bar (~line 2671).

- [ ] **Step 1: Locate the chrome block**

```bash
grep -n 'conversations-view\|conv-input-context\|conv-input-bar' static/index.html | head -10
```

Expected:

```
2664:    <div class="conversations-view" id="conversationsView">
2667:    <div class="conv-input-context" id="convInputContext">
2671:    <div class="conv-input-bar" id="convInputBar">
```

- [ ] **Step 2: Replace the chrome block with the wrapped version**

Find this block (in `static/index.html`, currently around lines 2664–2675):

```html
    <div class="conversations-view" id="conversationsView">
      <div class="empty-state" style="height:auto;padding:40px;">Select a session from the sidebar</div>
    </div>
    <div class="conv-input-context" id="convInputContext">
      <span class="wp-row" data-workspace></span>
      <span class="wp-usage" data-usage></span>
    </div>
    <div class="conv-input-bar" id="convInputBar">
      <input type="text" id="convInput" placeholder="Send to terminal..." autocomplete="off">
      <button class="send-btn" id="convSendBtn" title="Send to terminal">&gt;</button>
      <span class="tty-label" id="convTtyLabel"></span>
    </div>
```

Replace with:

```html
    <div class="conv-split" id="convSplit" data-orientation="">
      <div class="conv-pane" data-pane-id="p1">
        <div class="conv-pane-header" data-role="pane-header">
          <button class="conv-pane-close" data-role="pane-close" title="Close pane" aria-label="Close pane" style="display:none;">&times;</button>
        </div>
        <div class="conversations-view" id="conversationsView">
          <div class="empty-state" style="height:auto;padding:40px;">Select a session from the sidebar</div>
        </div>
        <div class="conv-input-context" id="convInputContext">
          <span class="wp-row" data-workspace></span>
          <span class="wp-usage" data-usage></span>
        </div>
        <div class="conv-input-bar" id="convInputBar">
          <input type="text" id="convInput" placeholder="Send to terminal..." autocomplete="off">
          <button class="send-btn" id="convSendBtn" title="Send to terminal">&gt;</button>
          <span class="tty-label" id="convTtyLabel"></span>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Add minimal CSS**

Find the `<style>` block by searching:

```bash
grep -n '#conversationsView\s*{' static/index.html | head -3
```

Just *above* the first `#conversationsView { ... }` rule, insert:

```css
    .conv-split {
      display: flex;
      flex: 1 1 auto;
      min-height: 0;
      flex-direction: column; /* unsplit = single child fills the column */
    }
    .conv-split[data-orientation="vertical"] { flex-direction: row; }
    .conv-split[data-orientation="horizontal"] { flex-direction: column; }
    .conv-pane {
      display: flex;
      flex-direction: column;
      flex: 1 1 0;
      min-height: 0;
      min-width: 0;
      position: relative; /* drop overlay anchor */
    }
    .conv-pane-header {
      display: none;            /* shown only when split is active */
      flex: 0 0 auto;
      align-items: center;
      justify-content: flex-end;
      padding: 4px 8px;
      border-bottom: 1px solid var(--border, #2a2a2a);
    }
    .conv-split[data-orientation] .conv-pane-header { display: flex; }
    .conv-pane-close {
      background: none;
      border: none;
      color: var(--text-muted, #888);
      font-size: 18px;
      line-height: 1;
      cursor: pointer;
      padding: 2px 6px;
      border-radius: 3px;
    }
    .conv-pane-close:hover { color: var(--text, #eee); background: var(--bg-hover, rgba(255,255,255,0.06)); }
```

(If `--border`, `--text-muted`, `--text`, or `--bg-hover` aren't defined in this codebase, the fallback hex values keep the rules valid — no JS lookup is required.)

- [ ] **Step 4: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 5: Manual QA**

Reload the dev server's UI. Verify:
1. The conversation pane looks identical to before — no header bar visible.
2. Clicking a sidebar conv still opens it.
3. The composer / model picker / footer all still work.
4. No layout shift, no extra whitespace, no scrollbars introduced.

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
refactor(ui): wrap conv chrome in a single .conv-pane container

Adds the .conv-split / .conv-pane / .conv-pane-header DOM scaffold so
the chrome can later be cloned into a second pane on drop. Header bar
and close button are hidden until split is engaged. No behavior change.
EOF
)"
```

---

## Task 3: Render helper for single ↔ split layout

**Goal:** Add `renderSplitLayout()` that mounts the appropriate DOM (single `.conv-pane` or two panes inside `.conv-split[data-orientation]`). Still no drag handlers; this is pure layout machinery.

**Files:**
- Modify: `static/index.html` near `function getConvView()` (~line 6648).

- [ ] **Step 1: Add helpers immediately above `function getConvView()`**

Find:

```javascript
  // Return the active conversation view element. The split-panel
  // (`$convPanelView`) is no longer engaged from the user-facing toggle,
  // so all conversation rendering goes into `.main`'s `$conversationsView`.
  function getConvView() {
    return $conversationsView;
  }
```

Replace with:

```javascript
  // Return the active conversation view element for the active pane.
  // For single-pane mode this is `$conversationsView` (the original element,
  // re-parented into `.conv-pane[data-pane-id="p1"]` by Task 2). For split
  // mode each pane has its own `.conversations-view` inside it; we look
  // it up via the active pane's data-pane-id attribute.
  function getConvViewForPane(pid) {
    const pane = document.querySelector(`.conv-pane[data-pane-id="${pid}"]`);
    return pane ? pane.querySelector('.conversations-view') : null;
  }
  function getConvView() {
    return getConvViewForPane(activePaneId()) || $conversationsView;
  }
  function getConvInputBarForPane(pid) {
    const pane = document.querySelector(`.conv-pane[data-pane-id="${pid}"]`);
    return pane ? pane.querySelector('.conv-input-bar') : null;
  }

  // Build a fresh `.conv-pane` element for paneId, cloning the chrome of
  // pane "p1" so styling / wiring stays in lockstep. Called only when
  // splitting from one pane to two.
  function buildPaneElement(paneId) {
    const tmpl = document.querySelector('.conv-pane[data-pane-id="p1"]');
    const clone = tmpl.cloneNode(true);
    clone.setAttribute('data-pane-id', paneId);
    // Empty state for the new pane; its conversation will be loaded by
    // selectConversation(id, paneId) immediately after attach.
    const view = clone.querySelector('.conversations-view');
    if (view) {
      view.id = '';                           // ids must be unique; only p1 keeps #conversationsView
      view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading…</div>';
    }
    const inputBar = clone.querySelector('.conv-input-bar');
    if (inputBar) inputBar.id = '';            // free the global id
    const ctxBar = clone.querySelector('.conv-input-context');
    if (ctxBar) ctxBar.id = '';
    const closeBtn = clone.querySelector('[data-role="pane-close"]');
    if (closeBtn) closeBtn.style.display = '';
    return clone;
  }

  // Toggle the split layout between single, vertical, horizontal.
  // Re-mounts panes inside `#convSplit` and updates orientation.
  function renderSplitLayout() {
    const $split = document.getElementById('convSplit');
    if (!$split) return;
    if (!splitState.orientation || splitState.panes.length < 2) {
      $split.setAttribute('data-orientation', '');
      // Drop any stray second pane elements (defensive — should already be 1).
      const extras = $split.querySelectorAll('.conv-pane:not([data-pane-id="p1"])');
      extras.forEach(n => n.remove());
      // Hide close buttons in single mode.
      $split.querySelectorAll('.conv-pane-close').forEach(b => b.style.display = 'none');
      return;
    }
    $split.setAttribute('data-orientation', splitState.orientation);
    // Ensure both panes exist in the DOM in order.
    splitState.panes.forEach((p, idx) => {
      let el = $split.querySelector(`.conv-pane[data-pane-id="${p.id}"]`);
      if (!el) {
        el = buildPaneElement(p.id);
        $split.appendChild(el);
      }
      el.style.flex = '1 1 0';
      el.querySelectorAll('.conv-pane-close').forEach(b => b.style.display = '');
    });
    // Mark the active pane.
    $split.querySelectorAll('.conv-pane').forEach(el => {
      el.classList.toggle('is-active', el.getAttribute('data-pane-id') === activePaneId());
    });
  }
```

- [ ] **Step 2: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 3: Manual QA**

Reload UI. Behavior should be identical (no split is engaged yet — `splitState.orientation` is `null`). DevTools console: no errors. Hand-test in console:

```javascript
splitState.orientation = 'vertical';
splitState.panes.push(_newPaneState('p2'));
renderSplitLayout();
```

You should see two side-by-side `.conv-pane` elements inside `#convSplit`. The right pane shows "Loading…" empty state. Reset:

```javascript
splitState.orientation = null;
splitState.panes = [splitState.panes[0]];
splitState.activeIndex = 0;
renderSplitLayout();
```

Verify single-pane returns. (Reloading the page also resets state.)

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): split-pane render helpers (renderSplitLayout, buildPaneElement)

Adds the layout machinery that toggles between single-pane and
two-pane split modes. Drag handlers and drop logic come in the next
tasks; for now the helpers are callable from the console for hand-test.
EOF
)"
```

---

## Task 4: Drop overlay markup + drag listeners (no actual splitting yet)

**Goal:** When the user starts dragging a `.conv-item` or `.kanban-card`, an overlay appears over each pane showing two drop zones (right edge and bottom edge). Hovering a zone highlights it; dropping fires a `paneDrop` callback that, for now, just `console.log`s.

**Files:**
- Modify: `static/index.html` — add CSS for the overlay; add an `attachDropZones(paneEl)` function called once per pane on mount; wire it up at the end of `renderSplitLayout()` and on initial DOMContentLoaded.

- [ ] **Step 1: Add overlay CSS**

Inside the `<style>` block, near the `.conv-pane` rules from Task 2, append:

```css
    .conv-pane-drop-overlay {
      position: absolute;
      inset: 0;
      pointer-events: none;
      z-index: 50;
      display: none;
    }
    .conv-pane-drop-overlay.active { display: block; }
    .conv-pane-drop-overlay .drop-zone {
      position: absolute;
      pointer-events: auto;
      border: 1px dashed var(--accent, #4ea1ff);
      background: rgba(78, 161, 255, 0.06);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent, #4ea1ff);
      font-size: 12px;
      font-weight: 500;
      transition: background 0.08s ease;
    }
    .conv-pane-drop-overlay .drop-zone.right  { right: 0; top: 0; bottom: 0; width: 22%; }
    .conv-pane-drop-overlay .drop-zone.bottom { left: 0; right: 0; bottom: 0; height: 22%; }
    .conv-pane-drop-overlay .drop-zone.over   { background: rgba(78, 161, 255, 0.18); }
    /* Suppress overlay below 900px viewport (split is unsupported there). */
    @media (max-width: 900px) {
      .conv-pane-drop-overlay { display: none !important; }
    }
```

- [ ] **Step 2: Add the drop-zone wiring function**

Immediately after `renderSplitLayout()` (added in Task 3), append:

```javascript
  // Returns true if the active drag carries a conversation card payload
  // (sidebar conv-item or kanban-card). Some browsers restrict
  // dataTransfer reads during dragenter/dragover; fall back to checking
  // dataTransfer.types for the payload key set by the source handlers.
  function dragHasConversationPayload(ev) {
    const types = (ev.dataTransfer && ev.dataTransfer.types) || [];
    return Array.from(types).some(t => t === 'text/plain' || t === 'application/x-conv-card');
  }

  // Read the conversation id out of the drop event. Both .conv-item drag
  // and .kanban-card drag set 'text/plain' to a comma-joined id list; we
  // take the first id. (Multi-select drag from kanban → split is not in
  // scope; the first id is the lead card.)
  function readConvIdFromDrop(ev) {
    const raw = ev.dataTransfer ? ev.dataTransfer.getData('text/plain') : '';
    if (!raw) return null;
    const first = String(raw).split(',')[0].trim();
    return first || null;
  }

  function attachDropZones(paneEl) {
    if (!paneEl || paneEl._dropZonesAttached) return;
    paneEl._dropZonesAttached = true;

    const overlay = document.createElement('div');
    overlay.className = 'conv-pane-drop-overlay';
    overlay.innerHTML = `
      <div class="drop-zone right"  data-zone="right">Open on the right</div>
      <div class="drop-zone bottom" data-zone="bottom">Open on the bottom</div>
    `;
    paneEl.appendChild(overlay);

    // Reject drops outright when a 2-pane split is already filled. The
    // overlay never activates, the pane shows no drop affordance, and
    // dragenter/over/leave/drop short-circuit to the pane's children.
    function splitIsFull() {
      return splitState.orientation && splitState.panes.length >= 2;
    }
    function viewportTooNarrow() {
      return window.innerWidth < 900;
    }

    // dragenter fires on every child element entry; track depth so the
    // overlay doesn't flicker when the cursor crosses internal nodes.
    let depth = 0;

    paneEl.addEventListener('dragenter', (ev) => {
      if (!dragHasConversationPayload(ev)) return;
      if (splitIsFull() || viewportTooNarrow()) return;
      depth += 1;
      overlay.classList.add('active');
      ev.preventDefault();
    });
    paneEl.addEventListener('dragleave', (ev) => {
      if (depth === 0) return;
      depth -= 1;
      if (depth === 0) {
        overlay.classList.remove('active');
        overlay.querySelectorAll('.drop-zone').forEach(z => z.classList.remove('over'));
      }
    });
    paneEl.addEventListener('dragover', (ev) => {
      if (!overlay.classList.contains('active')) return;
      ev.preventDefault();          // required to enable drop
      ev.dataTransfer.dropEffect = 'copy';
    });

    overlay.querySelectorAll('.drop-zone').forEach(zone => {
      zone.addEventListener('dragenter', () => zone.classList.add('over'));
      zone.addEventListener('dragleave', () => zone.classList.remove('over'));
      zone.addEventListener('dragover', (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
      zone.addEventListener('drop', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        depth = 0;
        overlay.classList.remove('active');
        zone.classList.remove('over');
        const convId = readConvIdFromDrop(ev);
        const targetPaneId = paneEl.getAttribute('data-pane-id');
        const orientation = zone.getAttribute('data-zone') === 'right' ? 'vertical' : 'horizontal';
        // For now: log only. Task 5 implements the actual split.
        console.log('[conv-split] drop', { convId, targetPaneId, orientation });
      });
    });
    // Also reset on drop outside any zone.
    paneEl.addEventListener('drop', () => {
      depth = 0;
      overlay.classList.remove('active');
      overlay.querySelectorAll('.drop-zone').forEach(z => z.classList.remove('over'));
    });
  }

  // Wire drop zones on every existing pane after each layout change.
  function attachAllPaneDropZones() {
    document.querySelectorAll('.conv-pane').forEach(attachDropZones);
  }
```

- [ ] **Step 3: Call `attachAllPaneDropZones()` on initial render and at the end of `renderSplitLayout()`**

Inside `renderSplitLayout()` (the function added in Task 3), append `attachAllPaneDropZones();` as the last line of the function body.

Then find `document.addEventListener('DOMContentLoaded'`, scroll to inside that listener, and add as the final line of its body:

```javascript
    attachAllPaneDropZones();
```

If there is no `DOMContentLoaded` block, find the IIFE / top-level script block that runs after the body parses and append the call there. (The simplest anchor is right after the line that creates `$conversationsView` const — search `const $conversationsView`.)

- [ ] **Step 4: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 5: Manual QA**

Reload UI. Open DevTools console.
1. From the sidebar, start dragging a conversation onto the conversation pane. The drop overlay (right edge band + bottom edge band) appears.
2. Hover the right band → it highlights. Same for bottom.
3. Drop on right band → console logs `[conv-split] drop { convId: "...", targetPaneId: "p1", orientation: "vertical" }`.
4. Drop on bottom band → same log with `orientation: "horizontal"`.
5. Drop in the center (outside the bands) → overlay clears, no log.
6. Drag a kanban card (switch sidebar to kanban view first via the toggle) → same drop overlay appears.
7. Resize viewport below 900px → start a drag → overlay does **not** appear.

If the overlay flickers when the cursor crosses transcript children, the dragenter depth counter is wrong — re-check.

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): drop-zone overlay + drag listeners on conv pane

Adds the right-edge and bottom-edge drop targets (with hover highlight,
flicker-free dragenter depth counter, and 900px viewport gate). Drop
currently console-logs only; the actual split is implemented in the
next commit.
EOF
)"
```

---

## Task 5: Implement drop → split (the meat)

**Goal:** Replace the `console.log` in the drop handler with logic that creates the second pane, opens the dropped conversation in it, and starts its SSE stream — independently of pane 1.

**Files:**
- Modify: `static/index.html` — convert `selectConversation`, `fetchConversationEvents`, `startConvStream`, `stopConvStream`, `renderConversationEvents` to accept an optional `paneId`; add `openConversationInPane(convId, targetPaneId, orientation)`; replace the `console.log` line from Task 4.

- [ ] **Step 1: Parameterize `selectConversation`**

Find `async function selectConversation(id) {` (~line 6882). Modify the signature:

```javascript
  async function selectConversation(id, paneId) {
    paneId = paneId || activePaneId();
    const paneIdx = paneIndexByPaneId(paneId);
    const pane = paneByPaneId(paneId);
    if (!pane) return;
    // Make this pane active so the existing globals (which proxy through
    // splitState.activeIndex) target the right pane while we run.
    splitState.activeIndex = paneIdx;
    // ... existing body unchanged ...
```

Inside the body, the existing code already references `currentConversation`, `convLastLine`, `convEventSource`, etc. — those still work because the shim now points at `paneIdx`.

The first line that calls `getConvView()` already returns the per-pane view because `getConvView()` resolves via `activePaneId()`. Same for `getConvInputBarForPane`.

- [ ] **Step 2: Parameterize `fetchConversationEvents`, `startConvStream`, `stopConvStream`**

Each of these three functions reads `currentConversation`, `convLastLine`, `convEventSource`. Add `paneId` as an optional parameter; before the body runs, set `splitState.activeIndex = paneIndexByPaneId(paneId || activePaneId());`. Example for `startConvStream`:

```javascript
  function startConvStream(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    // ... existing body unchanged ...
  }
```

Apply the same pattern to `stopConvStream` and `fetchConversationEvents`. **Important:** within `startConvStream`, the `EventSource` instance is now stored on the active pane via the shim, so when SSE events fire and the closure captures `currentConversation`, the read goes through the shim and may have shifted to another pane. Capture the conversation id at stream start in a local variable and compare against it inside the SSE event handlers, rather than rebinding through the shim.

Search the body for `currentConversation` references inside SSE event handler closures; replace the closure-captured comparisons with a local snapshot taken at the top of `startConvStream`:

```javascript
    const streamPaneId = activePaneId();
    const streamConvId = currentConversation;
```

…and in the message/error handlers, compare to `streamConvId` (and look up the pane by `streamPaneId` when writing). This isolates each pane's stream from the active-pane state.

- [ ] **Step 3: Parameterize `renderConversationEvents`**

Find `function renderConversationEvents(events) {` (~line 7533). Change to:

```javascript
  function renderConversationEvents(events, paneId) {
    paneId = paneId || activePaneId();
    const $view = getConvViewForPane(paneId) || $conversationsView;
    // ... rest of body unchanged, but use $view instead of any direct
    // call to getConvView() that previously resolved active-only ...
```

Within the body, replace any unconditional `getConvView()` calls with `$view`. Search the function body and audit each occurrence — most renderers already use a captured `$view` local; if they don't, change the captured variable's source to the parameter-driven one above.

- [ ] **Step 4: Add `openConversationInPane`**

Append after `renderSplitLayout()`:

```javascript
  // Open `convId` in a new pane, splitting the existing pane in the
  // requested orientation. Used by the drop handler. No-op if the same
  // conv is already open in the current pane (avoids a duplicate
  // SSE stream and a confusing UX).
  async function openConversationInPane(convId, targetPaneId, orientation) {
    if (!convId) return;
    if (splitState.orientation && splitState.panes.length >= 2) {
      // Split is already full — caller should not have invoked us, but
      // we guard anyway.
      return;
    }
    if (splitState.panes.length === 1 && splitState.panes[0].conversationId === convId) {
      // Same conversation as the only existing pane — no-op (visible
      // tooltip handled by the caller's UX in Task 8).
      return;
    }
    const newPane = _newPaneState('p2');
    splitState.orientation = orientation;
    splitState.panes.push(newPane);
    renderSplitLayout();          // creates the DOM for p2
    attachAllPaneDropZones();     // wire its drop overlay
    // Make p2 active and load the conversation in it.
    const newIdx = splitState.panes.length - 1;
    splitState.activeIndex = newIdx;
    await selectConversation(convId, newPane.id);
  }
```

- [ ] **Step 5: Replace the Task-4 `console.log` with the real call**

Inside the drop-zone `'drop'` handler (added in Task 4), replace:

```javascript
        console.log('[conv-split] drop', { convId, targetPaneId, orientation });
```

…with:

```javascript
        if (!convId) return;
        // If split is already full, caller shouldn't have shown the overlay,
        // but reject defensively.
        if (splitState.orientation && splitState.panes.length >= 2) return;
        // Same-conv guard: if convId is already open in the current pane,
        // do nothing (Task 8 adds a transient tooltip).
        if (splitState.panes.some(p => p.conversationId === convId)) return;
        openConversationInPane(convId, targetPaneId, orientation);
```

- [ ] **Step 6: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 7: Manual QA**

Reload UI.
1. Drag a conv from the sidebar onto the right band → split appears, the dropped conv loads on the right side, the original stays on the left.
2. The right pane's transcript renders.
3. Live messages stream into both panes simultaneously when both are live agents.
4. Drag a conv onto the bottom band → starts fresh (close the right pane via `×` after Task 7, or reload) → horizontal split appears.
5. The two panes do not flicker each other's transcripts.

If the right pane shows the wrong conversation, the SSE closure capture in Step 2 is wrong — re-check `streamConvId` / `streamPaneId` snapshots.

- [ ] **Step 8: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): drag-to-split — drop opens a second conversation pane

Parameterizes selectConversation / fetchConversationEvents /
startConvStream / stopConvStream / renderConversationEvents on paneId
so each pane streams independently. SSE handlers capture
{convId, paneId} at stream start so the active-pane shim can shift
without crossing transcripts. openConversationInPane wires the drop
zone to a real layout change.
EOF
)"
```

---

## Task 6: Per-pane composer wiring

**Goal:** Each pane's input box / send button targets *its own* conversation, not whichever pane happens to be active. Required so the user can type into either pane and have the message land in that pane's session.

**Files:**
- Modify: `static/index.html` — find `async function sendToTerminal()` (~line 3446); accept an optional `paneId`. Update the per-pane composer wiring inside `buildPaneElement` so each cloned input bar's send button calls `sendToTerminal('p2')`.

- [ ] **Step 1: Parameterize `sendToTerminal`**

Find `async function sendToTerminal()`. Modify:

```javascript
  async function sendToTerminal(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    // The existing body uses the shimmed currentSession / currentConversation,
    // which now resolve to the active pane.
    // ... existing body unchanged ...
  }
```

- [ ] **Step 2: Wire each pane's input bar to the right paneId**

Update `buildPaneElement` (added in Task 3) to wire the cloned input. After the existing assignments, before the final `return clone;`, add:

```javascript
    // Wire the cloned input bar to send into this specific pane.
    const sendBtn = clone.querySelector('.send-btn');
    const input = clone.querySelector('input[type="text"]');
    if (sendBtn) {
      sendBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        sendToTerminal(paneId);
      });
    }
    if (input) {
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' && !ev.shiftKey) {
          ev.preventDefault();
          sendToTerminal(paneId);
        }
      });
    }
```

The original p1 send wiring (the existing `convSendBtn` click handler in the existing code) continues to call `sendToTerminal()` with no argument, which defaults to the active pane — correct.

- [ ] **Step 3: Mark the active pane on click**

Append after `attachAllPaneDropZones()`:

```javascript
  // Click anywhere inside a pane to mark it active (drives composer
  // routing via the shim, and the sidebar `.active` highlight).
  document.addEventListener('click', (ev) => {
    const pane = ev.target.closest && ev.target.closest('.conv-pane');
    if (!pane) return;
    const pid = pane.getAttribute('data-pane-id');
    const idx = paneIndexByPaneId(pid);
    if (idx < 0 || idx === splitState.activeIndex) return;
    splitState.activeIndex = idx;
    document.querySelectorAll('.conv-pane').forEach(el => {
      el.classList.toggle('is-active', el.getAttribute('data-pane-id') === pid);
    });
    // Sidebar highlight follows the new active pane. Mirrors the inline
    // toggle in selectConversation (~line 6906-6917) but reads the conv
    // id from the active pane (via the shim) instead of taking a param.
    const activeConvId = currentConversation;
    if ($convList) {
      $convList.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
    if ($kanbanBoard) {
      $kanbanBoard.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
  }, true);
```

(There is no `updateSidebarActiveHighlight` helper in the existing code — the inlined block above mirrors the toggle logic that lives directly inside `selectConversation` around line 6906–6917.)

- [ ] **Step 4: Add active-pane border CSS**

Inside the existing `<style>` block, near the other `.conv-pane` rules:

```css
    .conv-split[data-orientation] .conv-pane.is-active {
      box-shadow: inset 0 0 0 1px var(--accent, #4ea1ff);
    }
```

(Single-pane mode — `data-orientation=""` — does not show the border.)

- [ ] **Step 5: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 6: Manual QA**

Reload UI.
1. Open one conversation in the sidebar. Drag another into the right edge → split.
2. Click into the left pane → it gets the accent border. Type a message; it lands in the left conversation.
3. Click into the right pane → border moves. Type a message; it lands in the right conversation.
4. Sidebar `.conv-item.active` highlight follows the focused pane.
5. Press Enter in the right pane's input → sends to the right conversation.

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): per-pane composer + active-pane focus tracking

Each pane's input/send routes through sendToTerminal(paneId), so
typing in either pane targets that pane's conversation. Click
anywhere in a pane to mark it active; the sidebar highlight follows.
EOF
)"
```

---

## Task 7: Pane close (×) + cleanup

**Goal:** Click `×` on a pane header → tear down its SSE stream, splice its state out of `splitState.panes`, collapse to single-pane mode, transfer focus to the survivor.

**Files:**
- Modify: `static/index.html` — extend `buildPaneElement` to wire the close button; add `closePane(paneId)`.

- [ ] **Step 1: Add `closePane`**

Append after `openConversationInPane`:

```javascript
  function closePane(paneId) {
    if (splitState.panes.length < 2) return; // can't close the only pane
    const idx = paneIndexByPaneId(paneId);
    if (idx < 0) return;
    const pane = splitState.panes[idx];
    // Tear down SSE.
    if (pane.eventSource) {
      try { pane.eventSource.close(); } catch (e) {}
      pane.eventSource = null;
    }
    // Remove the DOM element.
    const el = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    if (el) el.remove();
    // Splice state and collapse.
    splitState.panes.splice(idx, 1);
    splitState.orientation = null;
    splitState.activeIndex = 0; // survivor is now the only pane
    renderSplitLayout();
    // Re-render sidebar highlight.
    if (typeof updateSidebarActiveHighlight === 'function') {
      updateSidebarActiveHighlight();
    }
  }
```

- [ ] **Step 2: Wire the close button in `buildPaneElement` AND in p1**

Inside `buildPaneElement` (Task 3) before the final `return clone;`, add:

```javascript
    const closeBtn = clone.querySelector('[data-role="pane-close"]');
    if (closeBtn) {
      closeBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        closePane(paneId);
      });
    }
```

For the original `p1` pane (which is in the static HTML and not built by `buildPaneElement`), wire its close button at startup. After `attachAllPaneDropZones();` in the DOMContentLoaded section (Task 4 step 3), append:

```javascript
    document.querySelectorAll('.conv-pane[data-pane-id="p1"] [data-role="pane-close"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        // Closing p1 when split is engaged: keep p2 alive, slide it into p1's slot.
        if (splitState.panes.length < 2) return;
        const survivor = splitState.panes.find(p => p.id !== 'p1');
        if (!survivor) return;
        // Move survivor's conversation into the p1 element so the static
        // HTML's #conversationsView and #convInputBar ids stay live.
        const survivorEl = document.querySelector(`.conv-pane[data-pane-id="${survivor.id}"]`);
        if (survivorEl) survivorEl.remove();
        // Reset splitState: keep only p1, repoint it at the survivor's conv.
        splitState.panes[0].conversationId = survivor.conversationId;
        splitState.panes[0].lastLine = survivor.lastLine;
        splitState.panes[0].pendingSends = survivor.pendingSends;
        splitState.panes[0].firstUserMsgRendered = survivor.firstUserMsgRendered;
        // Tear down survivor's SSE; we'll restart on p1.
        if (survivor.eventSource) { try { survivor.eventSource.close(); } catch (e) {} }
        // Tear down p1's old SSE before restarting on the new conv.
        if (splitState.panes[0].eventSource) {
          try { splitState.panes[0].eventSource.close(); } catch (e) {}
          splitState.panes[0].eventSource = null;
        }
        splitState.panes.splice(1);
        splitState.orientation = null;
        splitState.activeIndex = 0;
        renderSplitLayout();
        // Re-render p1's transcript with the moved conversation.
        if (splitState.panes[0].conversationId) {
          fetchConversationEvents('p1');
          startConvStream('p1');
        }
      });
    });
```

- [ ] **Step 3: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 4: Manual QA**

Reload UI.
1. Open conv A in p1, drag conv B into the right edge (vertical split appears).
2. Click `×` on p2 (the right pane) → split collapses, p1 still shows conv A.
3. Re-split (drag conv B into right edge), now click `×` on p1 (the left pane) → split collapses, p1 (the only pane) now shows conv B (the survivor's conversation).
4. SSE for the closed pane is torn down (verify in DevTools Network panel: only one EventSource open after collapse).

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): close pane via × — tear down SSE + collapse to single

Each pane gets a close affordance in its header. Closing p2 keeps
p1 unchanged. Closing p1 transplants p2's conversation/state into
p1's slot so the static-HTML element ids (#conversationsView,
#convInputBar) stay live and the layout collapses cleanly.
EOF
)"
```

---

## Task 8: Edge cases — same-conv guard tooltip, third-card rejection, viewport fallback, sidebar click semantics

**Goal:** Round out the polish: a transient "Already open" tooltip when the user drops a conv that's already in another pane; resize listener that collapses split below 900px; clicking a `.conv-item` while split is open opens it in the active pane (existing semantics, but verify the active-pane shim path works).

**Files:**
- Modify: `static/index.html` — add a tooltip helper, a resize listener, and verify the click handler.

- [ ] **Step 1: Add transient tooltip helper**

Append after `closePane`:

```javascript
  // Show a 2-second floating message anchored to the conv pane.
  // Used when a drop is rejected because the conv is already open.
  let _convToastTimer = null;
  function showConvToast(msg) {
    let el = document.getElementById('convToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'convToast';
      el.className = 'conv-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('visible');
    if (_convToastTimer) clearTimeout(_convToastTimer);
    _convToastTimer = setTimeout(() => el.classList.remove('visible'), 2000);
  }
```

Add CSS in the `<style>` block:

```css
    .conv-toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--bg-overlay, rgba(20,20,24,0.95));
      color: var(--text, #eee);
      padding: 8px 16px;
      border-radius: 6px;
      font-size: 13px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.15s ease;
      z-index: 1000;
    }
    .conv-toast.visible { opacity: 1; }
```

- [ ] **Step 2: Wire the same-conv toast into the drop handler**

Replace the same-conv guard added in Task 5 step 5:

```javascript
        if (splitState.panes.some(p => p.conversationId === convId)) return;
```

With:

```javascript
        if (splitState.panes.some(p => p.conversationId === convId)) {
          showConvToast('Conversation is already open');
          return;
        }
```

- [ ] **Step 3: Add the resize listener**

Append after `showConvToast`:

```javascript
  // Below ~900px the split layout doesn't fit. Collapse to single-pane
  // (active pane wins). Tear down the inactive pane's SSE. When the
  // viewport grows back, the user can re-split via drag.
  function handleViewportResize() {
    if (window.innerWidth >= 900) return;
    if (!splitState.orientation || splitState.panes.length < 2) return;
    const survivor = splitState.panes[splitState.activeIndex];
    const losers = splitState.panes.filter((_, i) => i !== splitState.activeIndex);
    losers.forEach(p => {
      if (p.eventSource) { try { p.eventSource.close(); } catch (e) {} }
      const el = document.querySelector(`.conv-pane[data-pane-id="${p.id}"]`);
      if (el && p.id !== 'p1') el.remove();
    });
    if (survivor.id !== 'p1') {
      // Move survivor into p1's slot (same logic as p1-close in Task 7).
      splitState.panes[0].conversationId = survivor.conversationId;
      splitState.panes[0].lastLine = survivor.lastLine;
      splitState.panes[0].pendingSends = survivor.pendingSends;
      splitState.panes[0].firstUserMsgRendered = survivor.firstUserMsgRendered;
      if (survivor.eventSource) { try { survivor.eventSource.close(); } catch (e) {} }
      const survivorEl = document.querySelector(`.conv-pane[data-pane-id="${survivor.id}"]`);
      if (survivorEl) survivorEl.remove();
      if (splitState.panes[0].eventSource) {
        try { splitState.panes[0].eventSource.close(); } catch (e) {}
        splitState.panes[0].eventSource = null;
      }
    }
    splitState.panes.splice(1);
    splitState.orientation = null;
    splitState.activeIndex = 0;
    renderSplitLayout();
    if (splitState.panes[0].conversationId) {
      fetchConversationEvents('p1');
      startConvStream('p1');
    }
  }
  window.addEventListener('resize', handleViewportResize);
```

- [ ] **Step 4: Verify sidebar click → active pane**

The existing `.conv-item` click handler calls `selectConversation(id)` (no paneId), which the Task 5 signature change already routes to the active pane via the shim. No code change required here, but verify behavior in QA below.

- [ ] **Step 5: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 6: Manual QA**

Reload UI.
1. Open conv A in p1, drag conv B into the right edge → split.
2. Try to drag conv A onto either drop zone → toast appears: "Conversation is already open". No second pane created.
3. Try to drag a third conv (conv C) onto either drop zone → no overlay, no-op.
4. While split, click conv C in the sidebar → it opens in the *active* pane (whichever side you most recently clicked).
5. Resize the browser window from wide to narrow (<900px) → split collapses, only the active pane survives. Resize back → second drop zones available again on next drag.

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): conv-split edge cases — same-conv guard, viewport fallback

Drop a conv that's already open → transient 'Already open' toast
instead of opening a duplicate stream. Resize below 900px collapses
the split to a single pane (active pane wins, loser's SSE torn down).
EOF
)"
```

---

## Task 9: Visual polish — divider resizer + drop overlay accents

**Goal:** Add a draggable divider between panes that adjusts the split ratio in real time. Hover state on the divider shows the right resize cursor.

**Files:**
- Modify: `static/index.html` — extend `renderSplitLayout` to insert a `.conv-split-divider` element between panes; add drag listeners.

- [ ] **Step 1: Add divider CSS**

Inside the `<style>` block:

```css
    .conv-split-divider {
      flex: 0 0 4px;
      background: var(--border, #2a2a2a);
      cursor: col-resize;
      transition: background 0.1s ease;
    }
    .conv-split-divider:hover { background: var(--accent, #4ea1ff); }
    .conv-split[data-orientation="horizontal"] .conv-split-divider {
      cursor: row-resize;
    }
```

- [ ] **Step 2: Insert divider during render and bind drag listener**

Modify `renderSplitLayout` (Task 3): in the branch that handles `splitState.orientation` set + 2 panes, after appending both pane elements, find or insert a `.conv-split-divider` between them:

```javascript
    // Ensure the divider exists between the two panes.
    let divider = $split.querySelector('.conv-split-divider');
    if (!divider) {
      divider = document.createElement('div');
      divider.className = 'conv-split-divider';
      attachDividerDrag(divider);
    }
    // Reorder: panes[0], divider, panes[1].
    const p0 = $split.querySelector(`.conv-pane[data-pane-id="${splitState.panes[0].id}"]`);
    const p1 = $split.querySelector(`.conv-pane[data-pane-id="${splitState.panes[1].id}"]`);
    $split.append(p0, divider, p1);
    // Apply ratio.
    p0.style.flex = `${splitState.ratio} 1 0`;
    p1.style.flex = `${1 - splitState.ratio} 1 0`;
```

In the unsplit branch, also remove any stray divider:

```javascript
    const oldDivider = $split.querySelector('.conv-split-divider');
    if (oldDivider) oldDivider.remove();
```

- [ ] **Step 3: Add `attachDividerDrag`**

Append after `renderSplitLayout`:

```javascript
  function attachDividerDrag(divider) {
    let dragging = false;
    let startPos = 0;
    let startRatio = 0.5;
    let containerSize = 0;
    let isVertical = true;

    divider.addEventListener('pointerdown', (ev) => {
      const $split = document.getElementById('convSplit');
      if (!$split) return;
      isVertical = $split.getAttribute('data-orientation') === 'vertical';
      containerSize = isVertical ? $split.clientWidth : $split.clientHeight;
      if (containerSize <= 0) return;
      dragging = true;
      startPos = isVertical ? ev.clientX : ev.clientY;
      startRatio = splitState.ratio;
      divider.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    divider.addEventListener('pointermove', (ev) => {
      if (!dragging) return;
      const cur = isVertical ? ev.clientX : ev.clientY;
      const delta = (cur - startPos) / containerSize;
      let next = startRatio + delta;
      next = Math.max(0.15, Math.min(0.85, next));
      splitState.ratio = next;
      const p0el = document.querySelector(`.conv-pane[data-pane-id="${splitState.panes[0].id}"]`);
      const p1el = document.querySelector(`.conv-pane[data-pane-id="${splitState.panes[1].id}"]`);
      if (p0el) p0el.style.flex = `${next} 1 0`;
      if (p1el) p1el.style.flex = `${1 - next} 1 0`;
    });
    divider.addEventListener('pointerup', (ev) => {
      dragging = false;
      try { divider.releasePointerCapture(ev.pointerId); } catch (e) {}
    });
  }
```

- [ ] **Step 4: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass.

- [ ] **Step 5: Manual QA**

Reload UI.
1. Open a vertical split. The divider is a 4px vertical bar between the panes.
2. Hover the divider → background goes accent-blue, cursor changes to `col-resize`.
3. Drag the divider left/right → panes resize live. Clamps at 15% / 85%.
4. Open a horizontal split. The divider is horizontal, cursor is `row-resize`.
5. Drag the divider up/down → panes resize live.
6. Close the split → the divider is removed from the DOM (no stray element left in `#convSplit`).

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): draggable divider between split conv panes

Adds a 4px divider with col-resize / row-resize cursor that drags
the split ratio between 15% and 85%. Lives only while split is
engaged; cleaned up on collapse.
EOF
)"
```

---

## Task 10: Version bump + CHANGELOG

**Goal:** Bump SemVer minor (new user-visible feature) and append a `[Unreleased]` → `Added` entry.

**Files:**
- Modify: `pyproject.toml` (version `0.1.3` → `0.2.0`).
- Modify: `server.py` (`__version__ = "0.1.3"` → `"0.2.0"`).
- Modify: `CHANGELOG.md` (append bullet under `[Unreleased]` → `Added`).

- [ ] **Step 1: Bump pyproject.toml**

Find the line `version = "0.1.3"` and replace with `version = "0.2.0"`.

- [ ] **Step 2: Bump server.py**

Find the line `__version__ = "0.1.3"` and replace with `__version__ = "0.2.0"`.

- [ ] **Step 3: Append CHANGELOG entry**

Open `CHANGELOG.md`. Find the `## [Unreleased]` block and the `### Added` subsection within it. Append at the end of the existing `### Added` bullets:

```markdown
- **Drag-to-split conversation pane.** Drag a conversation card from the
  sidebar list (or a kanban column) onto the right edge or bottom edge
  of the chat pane to open a second conversation alongside the current
  one — vertical or horizontal split. Each pane has its own composer,
  send button, and SSE stream. Click the `×` in a pane header to close
  it; the survivor expands back to full width. Two-pane max; below
  900px viewport the split collapses to single-pane.
```

- [ ] **Step 4: Run smoke tests**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: 3 pass (the version-string regex in `test_server_imports_without_morning` matches `^\d+\.\d+\.\d+` so `0.2.0` is fine).

- [ ] **Step 5: Manual QA**

Reload UI. Verify:
1. Page loads, version footer (if shown) reads `0.2.0`.
2. Drag a conv onto the right edge → still works (regression check).
3. Close pane → still works.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml server.py CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: bump 0.1.3 → 0.2.0 for drag-to-split conversation pane

Minor version bump per SemVer + repo convention (new user-visible
feature). CHANGELOG entry under Unreleased / Added.
EOF
)"
```

---

## Final verification

After all 10 tasks:

```bash
cd /Users/amirfish/Apps/claude-command-center-wt-conv-split-pane
python3 -m unittest tests.test_smoke -v   # 3 pass
git log --oneline feat/conv-split-pane ^main   # ~11 commits (1 spec + 10 tasks)
```

End-to-end QA matrix to walk through:

| # | Action | Expected |
|---|---|---|
| 1 | Sidebar list → drag conv A → drop right edge | Vertical split, conv A on right |
| 2 | Click `×` on right pane | Collapse to left, conv unchanged |
| 3 | Sidebar list → drag conv B → drop bottom edge | Horizontal split, conv B on bottom |
| 4 | Click `×` on top pane | Collapse, bottom's conv slides into single pane |
| 5 | Kanban view → drag card → drop right | Vertical split (kanban + split coexist via sidebar swap, not nested layout) |
| 6 | While split → drag the conv that's already in p1 onto p2's drop zone | Toast "Conversation is already open"; no change |
| 7 | While split → drag a 3rd conv onto either pane | No overlay appears; drop is silent no-op |
| 8 | While split → click a conv-item in the sidebar | Loads in active pane; sidebar highlight follows |
| 9 | While split → resize browser to <900px | Split collapses to active pane |
| 10 | While split → drag the divider | Panes resize live, clamped 15%/85% |
| 11 | Type in left pane while right pane has focus | Goes to left pane's conversation |
| 12 | Live agent stream into one pane | Other pane unaffected |
