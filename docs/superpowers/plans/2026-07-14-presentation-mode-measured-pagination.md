# Measured Mode 2 Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Mode 2's synthetic page budgets with rendered-height packing so compact semantic content shares a slide and only overflowing atomic items move to the next slide.

**Architecture:** Keep transcript parsing and slide rendering in `static/app.js`. Add a pure greedy group packer, then supply it with a DOM-backed fit callback that renders candidates in a hidden layout-active surface sized to the visible slide slot. Preserve the current semantic item across resize-driven repagination and retain the weight paginator only as a fallback.

**Tech Stack:** Vanilla JavaScript and CSS in CCC's existing single-file client, Python `unittest` static/Node harness, Puppeteer 25 browser verification.

## Global Constraints

- Runtime server and client remain dependency-free; do not add npm or Python runtime packages.
- Mode 1, transcript data, `/api/*`, and model-token usage remain unchanged.
- Semantic items are atomic; oversized single items scroll inside their own slide.
- The measurement surface stays layout-active, pointer-inert, and excluded from the accessibility tree.
- Browser verification uses the repository Puppeteer harness, not Playwright or the in-app browser.
- No private session IDs, prompts, or local paths are committed.

---

### Task 1: Semantic group packing

**Files:**
- Modify: `static/app.js:38503-38546`
- Modify: `static/app.js:38577-38603`
- Test: `tests/test_presentation_mode_static.py:34-113`

**Interfaces:**
- Consumes: presentation items shaped as `{ node, key, weight, keepWithNext, breakBefore }`.
- Produces: `presentationItemGroups(items) -> Array<Array<Item>>` and `paginatePresentationGroups(groups, fits, fitsFinal) -> Array<Array<Item>>`.

- [ ] **Step 1: Add failing compact-list and greedy-packer tests**

Add this Node helper, which extracts `presentationItemGroups` and
`paginatePresentationGroups`, supplies a synthetic rendered-height callback,
and returns item IDs:

```python
def _run_group_packer(items, capacity):
    groups_source = _javascript_function_source("presentationItemGroups")
    packer_source = _javascript_function_source("paginatePresentationGroups")
    script = groups_source + "\n" + packer_source + "\n" + f"""
const items = {json.dumps(items)};
const capacity = {json.dumps(capacity)};
const groups = presentationItemGroups(items);
const pages = paginatePresentationGroups(
  groups,
  candidate => candidate.reduce((sum, item) => sum + item.height, 0) <= capacity,
);
process.stdout.write(JSON.stringify(pages.map(page => page.map(item => item.id))));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)
```

Replace the old ordered-list break expectation with:

```python
def test_compact_numbered_items_share_a_page(self):
    pages = _run_group_packer(
        [
            {"id": "heading", "keepWithNext": True, "height": 2},
            {"id": "intro", "height": 2},
            {"id": "number-1", "height": 3},
            {"id": "number-2", "height": 3},
        ],
        capacity=12,
    )
    self.assertEqual(pages, [["heading", "intro", "number-1", "number-2"]])

def test_overflow_moves_a_whole_semantic_group(self):
    pages = _run_group_packer(
        [
            {"id": "intro", "height": 5},
            {"id": "heading", "keepWithNext": True, "height": 2},
            {"id": "body", "height": 4},
        ],
        capacity=8,
    )
    self.assertEqual(pages, [["intro"], ["heading", "body"]])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_presentation_mode_static.TestPresentationModeStatic.test_compact_numbered_items_share_a_page \
  tests.test_presentation_mode_static.TestPresentationModeStatic.test_overflow_moves_a_whole_semantic_group -v
```

Expected: FAIL because `_run_group_packer` and the new JavaScript functions do
not exist.

- [ ] **Step 3: Implement the pure greedy packer**

Add:

```js
function presentationItemGroups(items) {
  const source = Array.isArray(items) ? items : [];
  const groups = [];
  for (let i = 0; i < source.length; i++) {
    const group = [source[i]];
    if (source[i] && source[i].keepWithNext && i + 1 < source.length) {
      group.push(source[++i]);
    }
    groups.push(group);
  }
  return groups;
}

function paginatePresentationGroups(groups, fits, fitsFinal) {
  const pages = [];
  let pageGroups = [];
  (Array.isArray(groups) ? groups : []).forEach(group => {
    const candidate = pageGroups.concat([group]);
    const items = candidate.flat();
    if (pageGroups.length && !fits(items, pages.length)) {
      pages.push(pageGroups);
      pageGroups = [group];
    } else {
      pageGroups = candidate;
    }
  });
  if (pageGroups.length) pages.push(pageGroups);
  if (typeof fitsFinal === 'function' && pages.length) {
    while (pages[pages.length - 1].length > 1
        && !fitsFinal(pages[pages.length - 1].flat(), pages.length - 1)) {
      const moved = pages[pages.length - 1].pop();
      pages.push([moved]);
    }
  }
  return pages.map(page => page.flat());
}
```

Change list fragments to use `breakBefore: index === 0`, so the fallback
paginator starts a list at a semantic boundary without forcing every numbered
item onto its own page.

- [ ] **Step 4: Run focused presentation tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_presentation_mode_static -v
node --check static/app.js
```

Expected: all presentation tests pass and JavaScript syntax exits 0.

- [ ] **Step 5: Commit the semantic packing slice**

```bash
git add -- static/app.js tests/test_presentation_mode_static.py
git commit --only static/app.js tests/test_presentation_mode_static.py \
  -m "fix(ui): pack compact presentation items together"
```

---

### Task 2: Rendered-height measurement

**Files:**
- Modify: `static/app.js:38711-38778`
- Modify: `static/app.js:38795-38842`
- Modify: `static/app.js:38949-38992`
- Modify: `static/app.css:19038-19227`
- Test: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: `presentationItemGroups`, `paginatePresentationGroups`, the attached presentation stage, and the visible slide-slot dimensions.
- Produces: `ensurePresentationMeasureSurface(stage)`, `presentationCandidateFits(surface, turn, items, pageIndex, includeDetails)`, and `paginatePresentationItemsMeasured(view, turn)`.

- [ ] **Step 1: Add failing measurement-surface contract tests**

Add this test so the expected integration points fail together:

```python
def test_mode_two_uses_layout_active_measurement_surface(self):
    app_js = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    build_deck = _javascript_function_source("buildPresentationDeck")
    ensure_surface = _javascript_function_source("ensurePresentationMeasureSurface")

    self.assertIn("paginatePresentationItemsMeasured(view, turn)", build_deck)
    self.assertIn("paginatePresentationItems(turn.blocks, budget)", build_deck)
    self.assertIn("aria-hidden", ensure_surface)
    self.assertIn(".conv-presentation-measure", css)
    self.assertIn("visibility: hidden", css)
    self.assertIn("pointer-events: none", css)
```

The CSS contract is:

```css
.conv-presentation-measure {
  position: fixed;
  visibility: hidden;
  pointer-events: none;
  display: flex;
}
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_presentation_mode_static.TestPresentationModeStatic.test_mode_two_uses_layout_active_measurement_surface -v
```

Expected: FAIL because the measurement surface and measured paginator are
missing.

- [ ] **Step 3: Implement the hidden measurement surface and fit callback**

Create the surface under the stage with `aria-hidden="true"`, size it from
`.conv-presentation-slide-slot.clientWidth/clientHeight`, and render a candidate
slide into it:

```js
function ensurePresentationMeasureSurface(stage) {
  let surface = stage.querySelector(':scope > .conv-presentation-measure');
  if (surface) return surface;
  surface = document.createElement('div');
  surface.className = 'conv-presentation-measure';
  surface.setAttribute('aria-hidden', 'true');
  stage.appendChild(surface);
  return surface;
}

function presentationCandidateFits(view, turn, items, pageIndex, includeDetails) {
  const stage = ensurePresentationStage(view);
  const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
  const width = slot.clientWidth;
  const height = slot.clientHeight;
  if (width < 1 || height < 1) return null;
  const surface = ensurePresentationMeasureSurface(stage);
  surface.style.width = width + 'px';
  surface.style.height = height + 'px';
  const partCount = includeDetails ? pageIndex + 1 : pageIndex + 2;
  const slide = buildPresentationSlide(turn, items, pageIndex, partCount, '2');
  surface.replaceChildren(slide);
  const body = slide.querySelector('.conv-presentation-body');
  const fits = !!body && body.scrollHeight <= body.clientHeight + 2;
  surface.replaceChildren();
  return fits;
}

function paginatePresentationItemsMeasured(view, turn) {
  const stage = ensurePresentationStage(view);
  const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
  if (!slot || slot.clientWidth < 1 || slot.clientHeight < 1) return null;
  const groups = presentationItemGroups(turn.blocks);
  return paginatePresentationGroups(
    groups,
    (items, pageIndex) => presentationCandidateFits(
      view, turn, items, pageIndex, false
    ),
    (items, pageIndex) => presentationCandidateFits(
      view, turn, items, pageIndex, true
    ),
  );
}
```

The synthetic part count suppresses Details for non-final candidates. The final
fit callback includes Details so the final page is rebalanced if its visible
details consume space. The surface is cleared after every measurement.

- [ ] **Step 4: Wire Mode 2 to measured packing with defensive fallback**

In `refreshPresentationForPane`, apply the presentation classes and ensure the
stage before building the deck so the measurement surface inherits the real
Mode 2 typography and receives nonzero dimensions. In `buildPresentationDeck`,
use measured pagination only for completed Mode 2 turns; retain
`paginatePresentationItems(turn.blocks, budget)` when measurement returns null
or throws. Live turns and Mode 1 remain `[turn.blocks]`.

- [ ] **Step 5: Add measurement CSS**

The surface must be fixed offscreen, use the measured slot width/height, inherit
presentation typography, center its candidate slide exactly like the visible
slot, and use `contain: layout style paint`. It must never be focusable or
pointer-active.

- [ ] **Step 6: Run focused tests and verify GREEN**

```bash
python3 -m unittest tests.test_presentation_mode_static -v
node --check static/app.js
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the measured pagination slice**

```bash
git add -- static/app.js static/app.css tests/test_presentation_mode_static.py
git commit --only static/app.js static/app.css tests/test_presentation_mode_static.py \
  -m "fix(ui): measure Mode 2 slide capacity"
```

---

### Task 3: Resize repagination and semantic cursor preservation

**Files:**
- Modify: `static/app.js:38577-38695`
- Modify: `static/app.js:38711-38738`
- Modify: `static/app.js:38935-39070`
- Test: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: item `key` values and the existing `presentationKey`/cursor logic.
- Produces: slide `data-presentation-item-keys`, `presentationCursorIndex(deck, previousSlide, fallback)`, and a pane-local debounced `ResizeObserver`.

- [ ] **Step 1: Add failing cursor and resize tests**

Add a pure cursor test where the old slide's first item moves into a newly
combined slide:

```python
def test_repagination_preserves_the_current_semantic_item(self):
    deck = [
        {"dataset": {"presentationItemKeys": "a,b"}},
        {"dataset": {"presentationItemKeys": "c,d"}},
    ]
    previous = {"dataset": {"presentationItemKeys": "c"}}
    self.assertEqual(
        _run_javascript_function("presentationCursorIndex", deck, previous, 0),
        1,
    )
```

Add this static observer contract:

```python
def test_mode_two_repaginates_after_meaningful_slot_resize(self):
    source = APP_JS.read_text(encoding="utf-8")
    observer = _javascript_function_source("ensurePresentationResizeObserver")
    self.assertIn("new ResizeObserver", observer)
    self.assertIn("Math.abs(width - previous.width) < 4", observer)
    self.assertIn("refreshPresentationForPane(paneId, { preserveCursor: true })", observer)
    self.assertIn("disconnectPresentationResizeObserver(view)", source)
```

- [ ] **Step 2: Run the tests and verify RED**

Run the two new tests. Expected: FAIL because semantic item datasets and the
observer do not exist.

- [ ] **Step 3: Add semantic item keys and cursor matching**

After each completed answer's blocks are extracted, assign deterministic keys:

```js
blocks.forEach((block, index) => { block.key = 'item-' + index; });
```

Store them when building a slide:

```js
slide.dataset.presentationItemKeys = items
  .map(item => String(item && item.key || ''))
  .filter(Boolean)
  .join(',');
```

Use this pure cursor helper. Semantic content wins over the positional
presentation key because the same `answer:part` key can refer to different
content after pages merge or split:

```js
function presentationCursorIndex(deck, previousSlide, fallback) {
  const slides = Array.isArray(deck) ? deck : [];
  if (!slides.length) return 0;
  const previousData = (previousSlide && previousSlide.dataset) || {};
  const itemKey = String(previousData.presentationItemKeys || '').split(',')[0];
  if (itemKey) {
    const containing = slides.findIndex(slide => (
      String(((slide && slide.dataset) || {}).presentationItemKeys || '')
        .split(',').includes(itemKey)
    ));
    if (containing >= 0) return containing;
  }
  const exactKey = String(previousData.presentationKey || '');
  if (exactKey) {
    const exact = slides.findIndex(slide => (
      String(((slide && slide.dataset) || {}).presentationKey || '') === exactKey
    ));
    if (exact >= 0) return exact;
  }
  return Math.max(0, Math.min(slides.length - 1, Number(fallback) || 0));
}
```

- [ ] **Step 4: Add pane-local slot observation**

Attach one observer per view with these functions:

```js
function disconnectPresentationResizeObserver(view) {
  if (!view) return;
  if (view._presentationResizeObserver) view._presentationResizeObserver.disconnect();
  if (view._presentationResizeTimer) clearTimeout(view._presentationResizeTimer);
  view._presentationResizeObserver = null;
  view._presentationResizeTimer = null;
  view._presentationResizeSlot = null;
  view._presentationResizeSize = null;
}

function ensurePresentationResizeObserver(view, paneId) {
  if (!view || typeof ResizeObserver !== 'function') return;
  const stage = ensurePresentationStage(view);
  const slot = stage.querySelector(':scope > .conv-presentation-slide-slot');
  if (!slot || view._presentationResizeSlot === slot) return;
  disconnectPresentationResizeObserver(view);
  view._presentationResizeSlot = slot;
  view._presentationResizeObserver = new ResizeObserver(entries => {
    const rect = entries[0] && entries[0].contentRect;
    if (!rect) return;
    const width = Math.round(rect.width);
    const height = Math.round(rect.height);
    const previous = view._presentationResizeSize;
    view._presentationResizeSize = { width, height };
    if (!previous) return;
    if (Math.abs(width - previous.width) < 4
        && Math.abs(height - previous.height) < 4) return;
    if (view._presentationResizeTimer) clearTimeout(view._presentationResizeTimer);
    view._presentationResizeTimer = setTimeout(() => {
      const pane = presentationPaneElement(paneId);
      if (!pane || normalizePresentationMode(pane.dataset.presentationMode) !== '2') return;
      refreshPresentationForPane(paneId, { preserveCursor: true });
    }, 100);
  });
  view._presentationResizeObserver.observe(slot);
}
```

Call `ensurePresentationResizeObserver(view, targetPaneId)` after rendering a
Mode 2 deck. Call `disconnectPresentationResizeObserver(view)` in Mode 1 and Off.

- [ ] **Step 5: Run focused tests and verify GREEN**

```bash
python3 -m unittest tests.test_presentation_mode_static -v
node --check static/app.js
```

Expected: all presentation tests pass.

- [ ] **Step 6: Commit cursor and resize behavior**

```bash
git add -- static/app.js tests/test_presentation_mode_static.py
git commit --only static/app.js tests/test_presentation_mode_static.py \
  -m "fix(ui): preserve slides across measured repagination"
```

---

### Task 4: Browser verification and release note

**Files:**
- Create: `changelog.d/fixed-mode-two-slide-packing-2026-07-14.md`
- Verify: `static/app.js`, `static/app.css`, `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: completed measured-pagination behavior.
- Produces: browser evidence at three pane heights and a public changelog entry.

- [ ] **Step 1: Verify compact synthetic content at three viewport heights**

Use the repository Puppeteer harness against `http://127.0.0.1:8090`. At
viewport heights 720, 1000, and 1450, inject a public synthetic completed answer
containing a heading, one paragraph, and four compact numbered/list items into a
conversation view, switch to Mode 2 through the real toolbar, and assert:

```js
slidesForAnswer.length === 1
visibleBody.scrollHeight <= visibleBody.clientHeight + 2
```

Then append atomic items until two slides are required and assert navigation
reaches both without either visible body overflowing.

- [ ] **Step 2: Recheck the reported long-conversation shape without committing private data**

In a temporary untracked Puppeteer script, open the affected conversation by
clicking its sidebar row, locate generated slides by the distinctive rendered
text, and record page counts plus `scrollHeight/clientHeight`. Do not write its
session ID or content into tests, fixtures, screenshots, or committed files.

Expected: the sparse heading/intro fragment is combined with subsequent compact
content, compact lists share slides, and every resulting visible slide fits.

- [ ] **Step 3: Add the changelog snippet**

Create the file with:

```markdown
- Fixed Mode 2 presentation pagination to fill slides by rendered content height instead of splitting compact numbered and bulleted content into sparse pages.
```

- [ ] **Step 4: Run the focused and full verification gates**

```bash
python3 -m unittest tests.test_presentation_mode_static \
  tests.test_composer_bottom_pin_static \
  tests.test_split_pane_isolation_static -v
node --check static/app.js
git diff --check
.venv/bin/python3 -m pytest tests/ -q --tb=no
```

Expected: presentation-focused commands exit 0. Record any unrelated pre-existing
full-suite failures exactly; do not change unrelated production code or stale
tests.

- [ ] **Step 5: Commit the verified user-visible slice**

```bash
git add -- changelog.d/fixed-mode-two-slide-packing-2026-07-14.md
git commit --only changelog.d/fixed-mode-two-slide-packing-2026-07-14.md \
  -m "docs(changelog): note measured slide packing"
```

- [ ] **Step 6: Confirm final repository state**

```bash
git status --short
git log -5 --oneline
```

Expected: no uncommitted files from this implementation and no private
diagnostic artifacts. Do not push unless the user explicitly asks.
