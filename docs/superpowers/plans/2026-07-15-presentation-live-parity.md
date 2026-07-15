# Presentation Live Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make presentation mode expose and operate every canonical regular-view update within one animation frame while retaining semantic slides for completed answers.

**Architecture:** Keep the regular transcript DOM canonical and untouched. A per-pane generic `MutationObserver` projects every changed top-level transcript root into an always-visible live region, while represented assistant roots refresh the semantic deck; delegated mirror events resolve back to canonical controls by child-index path. The selector becomes `Off | Present`, with legacy Mode 1 values normalized to Present.

**Tech Stack:** Single-file browser JavaScript (`static/app.js`), inline HTML/CSS, Python `unittest` source contracts, Puppeteer 25 Chromium verification.

## Global Constraints

- There is no update-type allowlist.
- Every child, text, class, attribute, control-state, and removal change is reflected within one animation frame.
- Mirrored controls route through canonical controls; mirrors never call APIs directly.
- Historical slide navigation is stable while live updates continue.
- Transcript data, `/api/*`, model calls, and token usage remain unchanged.
- Mode 1 is removed; stored `1` and `2` both normalize to Present (`2`).
- Presentation-owned DOM mutations must not recursively enter the projector.

---

### Task 1: Reduce the selector to Off and Present

**Files:**
- Modify: `static/index.html:764-773`
- Modify: `static/app.js:38580-38595,39190-39335`
- Modify: `static/app.css:19225-19232,19340-19352`
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: `normalizePresentationMode(mode)` and `PRESENTATION_MODE_KEY`.
- Produces: two UI states (`off`, `2`) and legacy `1 -> 2` migration.

- [x] **Step 1: Write the failing two-state tests**

Change the selector test to require exactly two controls, labels `Off` and `Present`, no `Mode 1` copy, and no `data-presentation-mode="1"`. Add pure normalization assertions:

```python
def test_selector_exposes_only_off_and_present(self):
    html = INDEX.read_text(encoding="utf-8")
    self.assertEqual(html.count("data-presentation-mode="), 2)
    self.assertIn('data-presentation-mode="off"', html)
    self.assertIn('data-presentation-mode="2"', html)
    self.assertNotIn('data-presentation-mode="1"', html)
    self.assertNotIn("Mode 1", html)

def test_legacy_mode_one_migrates_to_present(self):
    self.assertEqual(_run_javascript_function("normalizePresentationMode", "1"), "2")
    self.assertEqual(_run_javascript_function("normalizePresentationMode", "2"), "2")
    self.assertEqual(_run_javascript_function("normalizePresentationMode", "off"), "off")
```

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_presentation_mode_static.TestPresentationModeStatic.test_selector_exposes_only_off_and_present \
  tests.test_presentation_mode_static.TestPresentationModeStatic.test_legacy_mode_one_migrates_to_present -v
```

Expected: failures showing three controls and `normalizePresentationMode("1") == "1"`.

- [x] **Step 3: Implement the two-state selector and migration**

Replace the toolbar buttons with:

```html
<button type="button" class="conv-presentation-mode" data-presentation-mode="off" aria-pressed="true" title="Normal transcript view">Off</button>
<button type="button" class="conv-presentation-mode" data-presentation-mode="2" aria-pressed="false" title="Present assistant answers as semantic slides with complete live updates">Present</button>
```

Normalize legacy values without renaming the internal stored value:

```javascript
function normalizePresentationMode(mode) {
  const value = String(mode == null ? '' : mode).toLowerCase();
  if (value === '1' || value === '2' || value === 'present') return '2';
  return 'off';
}
```

Remove `.is-presentation-mode-1` toggles and CSS. Update fallback comments to say “one internally scrollable slide” rather than Mode 1.

- [x] **Step 4: Verify GREEN and commit**

Run:

```bash
python3 -m unittest tests.test_presentation_mode_static -v
node --check static/app.js
git diff --check
```

Expected: presentation tests pass and JavaScript parses.

Commit only the four task files:

```bash
git commit --only static/index.html static/app.js static/app.css tests/test_presentation_mode_static.py \
  -m "refactor(ui): remove presentation Mode 1"
```

---

### Task 2: Add generic source-root projection primitives

**Files:**
- Modify: `static/app.js` in the presentation-mode section
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Produces:
  - `presentationSourceRoot(view, node) -> Element|null`
  - `presentationElementPath(root, target) -> number[]|null`
  - `presentationResolvePath(root, path) -> Element|null`
  - `presentationCloneForProjection(source, prefix) -> Element`
  - `presentationRootsAfterLatestAnswer(view) -> Element[]`

- [x] **Step 1: Write failing pure-helper tests**

Use source extraction plus a Node script with a tiny element fixture to prove path round-tripping. Add source contracts proving `presentationSourceRoot` climbs to a direct child and excludes `.conv-presentation-stage`, `presentationCloneForProjection` rewrites `id`, `for`, `aria-labelledby`, `aria-describedby`, `aria-controls`, and local hash references, and form properties are copied.

The path fixture must assert:

```javascript
const path = presentationElementPath(sourceRoot, sourceButton);
const resolved = presentationResolvePath(sourceRoot, path);
assert(resolved === sourceButton);
```

- [x] **Step 2: Run helper tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_presentation_mode_static.TestPresentationModeStatic.test_projection_helpers_are_generic_and_clone_safe -v
```

Expected: failure because the projection helpers are absent.

- [x] **Step 3: Implement the primitives**

Implement path helpers over `Element.children`, never selectors tied to update classes:

```javascript
function presentationElementPath(root, target) {
  if (!root || !target || !root.contains(target)) return null;
  const path = [];
  for (let node = target; node && node !== root; node = node.parentElement) {
    const parent = node.parentElement;
    if (!parent) return null;
    path.push(Array.prototype.indexOf.call(parent.children, node));
  }
  return path.reverse();
}

function presentationResolvePath(root, path) {
  let node = root;
  for (const index of Array.isArray(path) ? path : []) {
    node = node && node.children && node.children[index];
    if (!node) return null;
  }
  return node;
}
```

`presentationCloneForProjection` must use `cloneNode(true)`, copy `value`, `checked`, `indeterminate`, `selectedIndex`, `open`, and `scrollTop` by matching element paths, then prefix IDs and rewrite local ID references inside the clone.

`presentationRootsAfterLatestAnswer` scans only direct children, ignores presentation-owned nodes, finds the last meaningful completed `.event.assistant` root, and returns every subsequent canonical root without filtering by update type.

- [x] **Step 4: Verify GREEN and commit**

Run presentation tests, `node --check static/app.js`, and `git diff --check`. Commit `static/app.js` and the test file with:

```text
feat(ui): add generic presentation projection primitives
```

---

### Task 3: Project every canonical mutation into a live region

**Files:**
- Modify: `static/app.js` in stage creation, presentation lifecycle, and refresh
- Modify: `static/app.css` in presentation layout
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: Task 2 projection primitives.
- Produces:
  - `ensurePresentationLiveRegion(stage) -> Element`
  - `ensurePresentationProjection(view, paneId) -> projection state`
  - `schedulePresentationProjectionFlush(view, roots?, refreshDeck?)`
  - `flushPresentationProjection(view)`
  - `disconnectPresentationProjection(view)`

- [x] **Step 1: Write failing observer lifecycle tests**

Require a `MutationObserver` configured with all four mutation dimensions, a per-view state containing `sourceIds: new WeakMap()`, `entries: new Map()`, `dirtyRoots: new Set()`, and one rAF handle. Assert the callback ignores targets within `.conv-presentation-stage`, derives roots generically, and reconciles removed roots with `view.contains(entry.source)`.

Require activation seeding from `presentationRootsAfterLatestAnswer(view)`, disconnection on Off, and fallback polling at `250` ms when `MutationObserver` is unavailable.

- [x] **Step 2: Run observer tests and verify RED**

Run the new test alone; expect missing `ensurePresentationProjection`.

- [x] **Step 3: Implement the live region and projector**

Create this stage child:

```html
<section class="conv-presentation-live-region" aria-label="Live conversation updates" aria-live="polite" hidden>
  <header><span>Live updates</span><span data-presentation-live-count></span></header>
  <div class="conv-presentation-live-list"></div>
</section>
```

The observer callback must only enqueue source roots and schedule one rAF. The flush must:

1. Record whether the live list was pinned to its end.
2. Remove entries whose sources are no longer contained by the view.
3. Replace one wrapper per dirty source using `presentationCloneForProjection`.
4. Preserve a single entry per source through `WeakMap` IDs and the iterable `entries` map.
5. Clear entries incorporated before the newest completed-answer boundary.
6. Update `hidden`, count, and pinned scrolling.
7. Schedule a deck refresh for assistant/stream roots without recursively observing stage mutations.

Wrap clone failures with a text fallback containing `source.innerText || source.textContent` and `source.className`.

Replace the one-row `syncPresentationActivity` timer with the generic live region. Delete its timer and special candidate selection so there is one live-update path.

Style the stage as two rows, with a flexible slide slot and an independently scrollable live region capped near 42% of the stage. The live region must remain visible on mobile and while historical slides are selected.

- [x] **Step 4: Verify GREEN and commit**

Run all presentation tests, JavaScript syntax, and diff checks. Commit `static/app.js`, `static/app.css`, and tests with:

```text
feat(ui): mirror every live conversation mutation
```

---

### Task 4: Forward mirrored interactions to canonical controls

**Files:**
- Modify: `static/app.js` in projection helpers
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: Task 2 path helpers and Task 3 projection entries.
- Produces:
  - `forwardPresentationProjectionEvent(view, event)`
  - capture listeners for `click`, `input`, and `change` on the live list.

- [x] **Step 1: Write the failing interaction test**

Create a browserless Node fixture where a mirrored nested button resolves to the canonical nested button. Assert clicks call the canonical `.click()`, `input`/`change` synchronize `value`, `checked`, and `selectedIndex` before dispatching an equivalent bubbling event, and a missing path is a safe no-op.

Require click delegation in capture phase so `<summary>`, links, and form controls cannot perform a second clone-local default action.

- [x] **Step 2: Run interaction test and verify RED**

Expected: missing `forwardPresentationProjectionEvent`.

- [x] **Step 3: Implement generic forwarding**

Resolve the wrapper ID to `state.entries`, calculate the target path relative to the mirrored source root, resolve the same path in the canonical root, and:

```javascript
if (event.type === 'click') {
  event.preventDefault();
  event.stopPropagation();
  canonicalTarget.click();
} else {
  canonicalTarget.value = mirrorTarget.value;
  canonicalTarget.checked = mirrorTarget.checked;
  canonicalTarget.selectedIndex = mirrorTarget.selectedIndex;
  canonicalTarget.dispatchEvent(new Event(event.type, { bubbles: true }));
}
```

Do not branch on button classes, tool names, or action types.

- [x] **Step 4: Verify GREEN and commit**

Run presentation tests and syntax checks. Commit `static/app.js` and tests with:

```text
feat(ui): forward presentation live controls
```

---

### Task 5: Prove the parity matrix in Chromium

**Files:**
- Create: `scripts/verify-presentation-live-parity.js`
- Create: `tests/test_presentation_live_parity_harness.py`
- Modify: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: local CCC server at `CCC_PRESENTATION_PARITY_URL` or `http://127.0.0.1:8090` and Puppeteer from `require-puppeteer.js`.
- Produces: a deterministic non-destructive parity verifier with nonzero exit on any mismatch.

- [x] **Step 1: Write the failing harness contract test**

Require the verifier to use repository Puppeteer, bound navigation, `try/finally`, browser close, condition-based waits (no `page.waitForTimeout`), and matrix labels for:

```text
pending queued delivered failed removed durable
sending thinking long-thinking generating tool tokens elapsed
stream completed approval question wake warning error done
attribute class disabled enabled click input change details
historical-cursor split-pane resize off-restore legacy-mode-one
added edited tool-group tool-complete approval-state queue-reason
outcome-banner dismissal frame-bound
```

Run the Python contract test and verify RED because the script is absent.

- [x] **Step 2: Create the synthetic canonical-view fixture**

The script loads the dashboard, replaces one pane's conversation contents with public fake events, programmatically clicks Present, and then mutates canonical roots one transition at a time. It must not select or modify a real conversation or call mutation APIs.

For each transition, wait with `page.waitForFunction` until the mirror exists, then compare normalized source and mirror snapshots:

```javascript
function snapshot(node) {
  return {
    text: node.innerText,
    classes: node.className,
    controls: Array.from(node.querySelectorAll('button,input,select,textarea,details')).map(control => ({
      tag: control.tagName,
      disabled: !!control.disabled,
      checked: !!control.checked,
      value: control.value || '',
      open: !!control.open,
    })),
  };
}
```

Strip only projection-specific ID prefixes before comparison. Verify removals by proving both source and mirror are absent.

- [x] **Step 3: Add interaction, cursor, split, resize, and Off checks**

Attach canonical listeners to fake controls and prove mirror interaction increments canonical state. Park on an older slide, add updates, and prove the slide key is unchanged. Clone a second pane and prove observers/mirrors do not cross panes. Resize the viewport and prove both slide cursor and mirrors survive. Switch Off and prove the observer/live stage is removed while the canonical transcript remains unchanged. Seed localStorage with legacy `1`, reload, and prove Present is selected.

- [x] **Step 4: Run Chromium verification and focused tests**

Run:

```bash
node scripts/verify-presentation-live-parity.js
python3 -m unittest \
  tests.test_presentation_mode_static \
  tests.test_presentation_live_parity_harness \
  tests.test_composer_bottom_pin_static \
  tests.test_split_pane_isolation_static -v
node --check static/app.js
git diff --check
```

Expected: every named parity row prints `PASS`, all focused tests pass, and syntax/diff checks exit 0.

- [x] **Step 5: Add changelog and commit**

Create `changelog.d/fixed-presentation-live-parity-2026-07-15.md`:

```markdown
- Presentation mode now mirrors every live conversation update and control without hiding regular-view activity, and the selector is simplified to Off or Present.
```

Commit the verifier, tests, and changelog with:

```text
fix(ui): preserve every live update in presentation
```

---

### Task 6: Completion audit

**Files:**
- Inspect all files changed by Tasks 1-5
- Inspect `docs/superpowers/specs/2026-07-15-presentation-live-parity-design.md`

- [x] **Step 1: Run the complete parity verifier twice**

The second run proves lifecycle cleanup prevents duplicate observers or stale projection state.

- [x] **Step 2: Run the full repository suite**

Run:

```bash
.venv/bin/python3 -m pytest tests/ -q --tb=no
```

Record exact pass/failure counts and distinguish failures in changed presentation files from unrelated shared-main failures.

- [x] **Step 3: Audit every spec requirement against evidence**

Map each Required Parity Evidence bullet to a named Chromium `PASS` line or focused automated assertion. Treat missing evidence as incomplete work and add the missing test before completion.

- [x] **Step 4: Verify repository state**

Run `git status --short`, `git diff --check`, and `git log --oneline` for the task commits. Do not push without explicit user authorization.
