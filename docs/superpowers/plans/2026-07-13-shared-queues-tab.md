# Shared Queues Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the main sidebar's Merge tab with a Queues tab that reuses the existing right-rail queue panel while keeping both access points available.

**Architecture:** Add stable mount points in the right rail and main sidebar, then move the single existing `#queuePanel` node between them. A small host-preference reconciler parks the node before sidebar `innerHTML` replacement and restores it afterward, preserving its IDs, listeners, controls, and fetched state.

**Tech Stack:** Static HTML, vanilla JavaScript, CSS, Python `unittest`, Puppeteer 25.

## Global Constraints

- Keep exactly one live `#queuePanel` DOM subtree.
- Keep the right-hand Queue tab available.
- Hide the dedicated Merge tab and its ready-to-merge content without relocating that content.
- Reject a persisted `merge` main-tab selection and fall back to Active.
- Do not add dependencies, server endpoints, or persistence formats.
- Preserve all unrelated uncommitted changes in the shared checkout.

---

### Task 1: Shared queue-panel hosts and handoff

**Files:**
- Modify: `tests/test_queue_panel_layout.py`
- Modify: `static/index.html:871-896`
- Modify: `static/app.js:26467-26505, 26580-26620, 27433-27443, 31637-31648, 40058-40077`
- Modify: `static/app.css:16568-16600`
- Create: `changelog.d/added-mobile-queues-tab-2026-07-13.md`

**Interfaces:**
- Consumes: the existing `#queuePanel`, `_renderQueuePanel()`, `setStatusRailTab(tab)`, and main-tab `localStorage` key `ccc-sidebar-tab`.
- Produces: `#statusRailQueueHost`, `#sidebarQueueHost`, `_setSharedQueuePanelHost(hostName)`, `_parkSharedQueuePanelForSidebarRender()`, and `_mountSharedQueuePanel()`.

- [ ] **Step 1: Write the failing regression tests**

Add focused source-contract tests to `TestQueuePanelLayout`:

```python
    def test_main_sidebar_replaces_merge_with_shared_queues_tab(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        tab_block = app_js[
            app_js.index("const _sidebarTab = (() => {"):
            app_js.index("const _tabEmpty =", app_js.index("const _sidebarTab = (() => {"))
        ]
        self.assertIn("t === 'queues'", tab_block)
        self.assertNotIn("t === 'merge'", tab_block)
        self.assertIn("['queues', 'Queues'", tab_block)
        self.assertNotIn("['merge', 'Merge'", tab_block)
        self.assertIn('id="sidebarQueueHost"', app_js)
        self.assertNotIn("_sidebarTab === 'merge'", tab_block)

    def test_queue_panel_has_one_node_and_two_mount_points(self):
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertEqual(index_html.count('id="queuePanel"'), 1)
        self.assertEqual(index_html.count('id="statusRailQueueHost"'), 1)
        self.assertIn('id="sidebarQueueHost"', app_js)
        self.assertIn("function _setSharedQueuePanelHost(hostName)", app_js)
        self.assertIn("function _parkSharedQueuePanelForSidebarRender()", app_js)
        self.assertIn("function _mountSharedQueuePanel()", app_js)
        self.assertIn("_parkSharedQueuePanelForSidebarRender();\n    $convList.innerHTML = _convListHtml;", app_js)
        self.assertIn("$convList.innerHTML = _convListHtml;\n    _mountSharedQueuePanel();", app_js)
        self.assertIn("if (next === 'queue' && queuePane) {", app_js)
        self.assertIn("_setSharedQueuePanelHost('rail');", app_js)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
python3 -m unittest tests.test_queue_panel_layout -v
```

Expected: the two new tests fail because the main tab is still `merge`, the mount points do not exist, and the handoff functions are absent.

- [ ] **Step 3: Add the stable right-rail host**

Wrap the existing queue panel in `static/index.html` without changing its contents:

```html
<div class="shared-queue-host shared-queue-host-rail" id="statusRailQueueHost">
  <div class="files-panel files-queue-panel" id="queuePanel">
    <!-- existing queue panel contents remain unchanged -->
  </div>
</div>
```

- [ ] **Step 4: Replace the main Merge tab with Queues**

Update the sidebar state validation and tab definitions in `renderConversationList`:

```javascript
return (t === 'issues' || t === 'queues' || t === 'inprogress' || t === 'archived') ? t : 'inprogress';
```

```javascript
['queues', 'Queues', ((_uxqHealthCache && _uxqHealthCache.queues) || []).length],
```

Render a mount point rather than the ready-to-merge section:

```javascript
: _sidebarTab === 'queues' ? '<div class="shared-queue-host shared-queue-host-sidebar" id="sidebarQueueHost"></div>'
```

The existing ready-to-merge data shaping can remain for other consumers, but remove it from the visible tab definition and tab body.

- [ ] **Step 5: Implement the single-node handoff**

Add the following queue-host state and helpers near the existing queue panel functions:

```javascript
let _sharedQueuePanelHost = (() => {
  try { return localStorage.getItem('ccc-sidebar-tab') === 'queues' ? 'sidebar' : 'rail'; }
  catch (_) { return 'rail'; }
})();

function _sharedQueueHostElement(hostName) {
  return document.getElementById(hostName === 'sidebar' ? 'sidebarQueueHost' : 'statusRailQueueHost');
}

function _mountSharedQueuePanel() {
  const panel = document.getElementById('queuePanel');
  const host = _sharedQueueHostElement(_sharedQueuePanelHost);
  if (!panel || !host) return false;
  if (panel.parentElement !== host) host.appendChild(panel);
  if (_sharedQueuePanelHost === 'sidebar') _renderQueuePanel();
  return true;
}

function _setSharedQueuePanelHost(hostName) {
  _sharedQueuePanelHost = hostName === 'sidebar' ? 'sidebar' : 'rail';
  _mountSharedQueuePanel();
}

function _parkSharedQueuePanelForSidebarRender() {
  const panel = document.getElementById('queuePanel');
  const railHost = _sharedQueueHostElement('rail');
  if (panel && railHost && panel.parentElement !== railHost) railHost.appendChild(panel);
}
```

Immediately before and after the conversation-list `innerHTML` replacement, preserve and restore the shared node:

```javascript
_parkSharedQueuePanelForSidebarRender();
$convList.innerHTML = _convListHtml;
_mountSharedQueuePanel();
```

In the main tab click handler, set the desired host before re-rendering:

```javascript
const nextTab = tab.getAttribute('data-conv-tab');
try { localStorage.setItem('ccc-sidebar-tab', nextTab); } catch (_) {}
_setSharedQueuePanelHost(nextTab === 'queues' ? 'sidebar' : 'rail');
renderArchiveList(document.getElementById('convSearch')?.value || '', { force: true });
```

In `setStatusRailTab`, make the most recently selected visible entry point own the shared node:

```javascript
if (next === 'queue' && queuePane) {
  _setSharedQueuePanelHost('rail');
  _renderQueuePanel();
} else {
  let sidebarTab = 'inprogress';
  try { sidebarTab = localStorage.getItem('ccc-sidebar-tab') || 'inprogress'; } catch (_) {}
  if (sidebarTab === 'queues') _setSharedQueuePanelHost('sidebar');
}
```

- [ ] **Step 6: Make the shared panel fill the main tab**

Add layout-only CSS:

```css
#convList:has(> .shared-queue-host-sidebar) {
  display: flex;
  flex-direction: column;
  overflow-y: hidden !important;
}
#convList:has(> .shared-queue-host-sidebar) > .conv-tab-bar {
  flex: 0 0 auto;
}
.shared-queue-host-sidebar {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.shared-queue-host-sidebar > .files-queue-panel {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  height: auto !important;
  padding: 0 8px 10px;
  border-top: 0;
  background: transparent;
}
.shared-queue-host-rail {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.shared-queue-host-rail > .files-queue-panel {
  flex: 1 1 auto;
}
```

- [ ] **Step 7: Run the focused tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_queue_panel_layout -v
```

Expected: all queue-panel layout tests pass.

- [ ] **Step 8: Add the user-visible changelog snippet**

Create `changelog.d/added-mobile-queues-tab-2026-07-13.md` containing:

```markdown
- Replaced the main Merge tab with a Queues tab that reuses the full WatchTower queue view while keeping the desktop right-rail Queue tab available.
```

- [ ] **Step 9: Verify the full behavior**

Run:

```bash
python3 -m unittest tests.test_queue_panel_layout -v
python3 -m unittest tests.test_smoke -v
node --check static/app.js
node snapshot.js
```

Expected: both test modules pass, JavaScript syntax checking exits 0, and `snapshot.js` writes `snapshot.png` without browser errors. Use an ad-hoc Puppeteer 25 check at a mobile viewport to click `[data-conv-tab="queues"]`, confirm one `#queuePanel` is inside `#sidebarQueueHost`, select the RHS Queue tab, and confirm the same node is then inside `#statusRailQueueHost` with its queue dropdown value unchanged.

- [ ] **Step 10: Commit only the feature paths**

```bash
git add tests/test_queue_panel_layout.py static/index.html static/app.js static/app.css changelog.d/added-mobile-queues-tab-2026-07-13.md
git commit --only tests/test_queue_panel_layout.py static/index.html static/app.js static/app.css changelog.d/added-mobile-queues-tab-2026-07-13.md -m "feat(queue): add shared queues tab"
```
