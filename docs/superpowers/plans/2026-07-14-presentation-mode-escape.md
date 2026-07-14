# Presentation Mode Escape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an unconsumed Escape key exit the active conversation pane from presentation Mode 2 to Off, including while the composer is focused.

**Architecture:** Add one named scheduler beside the existing presentation keyboard handler. The scheduler waits until keyboard-event dispatch finishes, then rechecks `defaultPrevented` and the active pane's current mode before using the existing `setPresentationMode` path.

**Tech Stack:** Browser JavaScript in `static/app.js`; Python `unittest` source-contract tests.

## Global Constraints

- Escape changes only Mode 2 to Off; Mode 1 remains unchanged.
- An Escape event consumed by another UI layer must not exit presentation mode.
- The behavior applies while the composer or another editable target is focused.
- Existing left/right slide navigation remains unchanged.

---

### Task 1: Exit Mode 2 with Escape

**Files:**
- Modify: `tests/test_presentation_mode_static.py`
- Modify: `static/app.js`
- Create: `changelog.d/fixed-presentation-escape-2026-07-14.md`

**Interfaces:**
- Consumes: `activePaneId()`, `presentationPaneElement(paneId)`, `normalizePresentationMode(mode)`, and `setPresentationMode(paneId, mode)`.
- Produces: `schedulePresentationEscape(ev) -> boolean`, returning `true` only when it schedules an Escape check.

- [ ] **Step 1: Write the failing regression test**

Add a test that extracts `schedulePresentationEscape`, verifies it defers with `setTimeout`, rechecks `ev.defaultPrevented`, requires current mode `2`, and calls `setPresentationMode(paneId, 'off')`. Verify in the document keyboard handler that `schedulePresentationEscape(ev)` appears before the editable-target exclusion.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_presentation_mode_static.TestPresentationModeStatic.test_escape_exits_mode_two_even_from_the_composer -v
```

Expected: failure because `schedulePresentationEscape` is absent.

- [ ] **Step 3: Implement the minimal scheduler**

Add beside the existing presentation keyboard handler:

```javascript
function schedulePresentationEscape(ev) {
  if (!ev || ev.key !== 'Escape') return false;
  setTimeout(() => {
    if (ev.defaultPrevented) return;
    const paneId = activePaneId();
    const pane = presentationPaneElement(paneId);
    if (!pane || normalizePresentationMode(pane.dataset.presentationMode) !== '2') return;
    setPresentationMode(paneId, 'off');
  }, 0);
  return true;
}
```

In the existing document keydown listener, call the scheduler after the modifier/default-prevented guard and before filtering editable targets. Return when it schedules Escape; preserve the existing arrow-key path verbatim.

- [ ] **Step 4: Run focused verification and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_presentation_mode_static -v
node --check static/app.js
git diff --check
```

Expected: all presentation tests pass, JavaScript parses, and the diff check exits 0.

- [ ] **Step 5: Add the changelog and commit**

Create the changelog entry:

```markdown
- Pressing Escape now exits conversation presentation Mode 2, including while the composer is focused.
```

Commit only the three task files with Conventional Commit subject:

```text
fix(ui): exit presentation Mode 2 with Escape
```
