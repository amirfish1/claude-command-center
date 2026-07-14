# Conversation Presentation Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pane-scoped Off, Mode 1, and Mode 2 slide-style conversation reading with no additional model calls or transcript mutations.

**Architecture:** Keep the existing transcript DOM mounted as the source of truth and derive a client-side deck of cloned, already-sanitized nodes. A pure semantic paginator groups rendered Markdown blocks by a viewport-derived budget; pane-local state controls the active slide and a persisted local default seeds new panes.

**Tech Stack:** Plain browser JavaScript, HTML, CSS, Python `unittest`, Node.js for exercising the pure paginator, Puppeteer for visual verification.

## Global Constraints

- Do not change `/api/*` response shapes or transcript files.
- Do not make model calls; every mode must have zero additional token cost.
- Presentation state is scoped to one pane; only the latest default is persisted in `localStorage`.
- Preserve the existing single-file/no-bundler frontend architecture and runtime dependencies.
- Keep tool calls collapsed unless the existing Verbose preference is enabled.

---

### Task 1: Specify presentation-mode contracts with failing tests

**Files:**
- Create: `tests/test_presentation_mode_static.py`
- Test: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: existing `static/index.html`, `static/app.js`, and `static/app.css` source files.
- Produces: executable contracts for `paginatePresentationItems(items, budget)`, `setPresentationMode(paneId, mode)`, the clone-safe `[data-presentation-mode]` controls, streaming refresh, and presentation CSS hooks.

- [ ] **Step 1: Write the failing structural and pure-function tests**

```python
def test_selector_is_clone_safe_and_exposes_three_modes(self):
    html = INDEX.read_text()
    self.assertIn('data-role="presentation-toolbar"', html)
    self.assertEqual(html.count('data-presentation-mode='), 3)

def test_paginator_keeps_a_heading_with_the_following_block(self):
    pages = run_paginator([
        {"id": "intro", "weight": 7},
        {"id": "heading", "weight": 2, "keepWithNext": True},
        {"id": "body", "weight": 4},
    ], 9)
    self.assertEqual([[x["id"] for x in page] for page in pages],
                     [["intro"], ["heading", "body"]])
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_presentation_mode_static -v`

Expected: failures because the selector, paginator, mode functions, refresh hook,
and CSS classes do not exist.

- [ ] **Step 3: Commit the red test contract with the design docs**

```bash
git commit --only tests/test_presentation_mode_static.py \
  docs/superpowers/specs/2026-07-14-conversation-presentation-modes-design.md \
  docs/superpowers/plans/2026-07-14-conversation-presentation-modes.md \
  -m "docs(ui): design conversation presentation modes"
```

### Task 2: Build pane-scoped deck generation and navigation

**Files:**
- Modify: `static/index.html` near the per-pane header and transcript view.
- Modify: `static/app.js` near conversation rendering, streaming rendering, and per-pane controls.
- Modify: `static/app.css` near conversation pane and right-rail layout rules.
- Test: `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: `renderConversationEvents(events, paneId)`, `handleSpawnEvents(events, paneId, convId)`, `convVerboseOn()`, `getConvViewForPane(paneId)`, and clone-safe pane markup.
- Produces: `paginatePresentationItems(items, budget) -> Array<Array<Item>>`, `setPresentationMode(paneId, mode, opts)`, `refreshPresentationForPane(paneId, opts)`, and `stepPresentationSlide(paneId, delta)`.

- [ ] **Step 1: Add the clone-safe segmented selector markup**

Add one toolbar under `.conv-pane-header` with buttons for `off`, `1`, and `2`.
Use `data-role` and `data-presentation-mode` attributes rather than IDs so
`buildPaneElement()` can clone it safely.

- [ ] **Step 2: Implement the pure paginator and deck builder**

Implement `paginatePresentationItems` so a `keepWithNext` block moves with its
successor when the current page lacks room. Build turns from top-level user,
tool-group, assistant, and streaming nodes; clone sanitized answer blocks into
derived slides; attach details to the answer's final slide.

- [ ] **Step 3: Implement pane-local state and restoration**

`setPresentationMode` normalizes modes to `off|1|2`, stores the mode on the pane,
persists only the default key, removes the stage/dock in Off, and rebuilds the
deck otherwise. `refreshPresentationForPane` preserves the current answer/part
key during polling and follows the last live slide while streaming.

- [ ] **Step 4: Wire controls, keyboard navigation, resize, and render hooks**

Use delegated click handlers for clone-safe buttons. Left/right keys act only
when the active pane is in presentation mode and the event target is not an
input, textarea, select, button, link, code, or contenteditable element. Debounce
resize repagination. Refresh after durable transcript batches and streaming
batches.

- [ ] **Step 5: Add responsive presentation styling**

Style the selector, stage, slide, prompt band, details, bottom dock, progress
markers, and Mode 1/Mode 2 typography. Extend the right-rail grid with toolbar
and dock areas. Add narrow-pane/mobile and `prefers-reduced-motion` rules.

- [ ] **Step 6: Run the focused test and verify GREEN**

Run: `python3 -m unittest tests.test_presentation_mode_static -v`

Expected: all presentation-mode tests pass.

- [ ] **Step 7: Commit the user-visible slice**

```bash
git commit --only static/index.html static/app.js static/app.css \
  tests/test_presentation_mode_static.py \
  -m "feat(ui): add conversation presentation modes"
```

### Task 3: Changelog and full verification

**Files:**
- Create: `changelog.d/added-conversation-presentation-modes-2026-07-14.md`
- Modify only if verification exposes a defect: `static/app.js`, `static/app.css`, `static/index.html`, `tests/test_presentation_mode_static.py`

**Interfaces:**
- Consumes: the complete presentation-mode feature.
- Produces: release-note coverage and verified browser behavior.

- [ ] **Step 1: Add the changelog snippet**

```markdown
- Added zero-token-cost presentation modes that turn each conversation pane into a navigable answer deck.
```

- [ ] **Step 2: Run focused and repository smoke tests**

Run: `python3 -m unittest tests.test_presentation_mode_static -v`

Run: `python3 -m pytest tests/test_smoke.py tests/test_split_pane_isolation_static.py -q`

Expected: all tests pass.

- [ ] **Step 3: Run syntax and browser verification**

Run: `node --check static/app.js`

Run the local server, then use `node snapshot.js` and a focused Puppeteer script
to select Mode 1 and Mode 2, navigate slides, resize/split panes, and capture a
screenshot. Expected: no console errors, the original transcript returns in Off,
and each pane retains independent mode and slide state.

- [ ] **Step 4: Commit the completed feature metadata**

```bash
git commit --only changelog.d/added-conversation-presentation-modes-2026-07-14.md \
  -m "docs(changelog): note conversation presentation modes"
```
