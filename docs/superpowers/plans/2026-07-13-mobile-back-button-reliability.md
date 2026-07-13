# Mobile Back Button Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep mobile Back visible and usable across conversation switches and subagent-tab lifecycle changes.

**Architecture:** `#convToolbar` remains the sole owner of `#mobileBackBtn`; dynamic transcript and tab-strip rendering never reparents or removes that node. Existing responsive CSS continues to show it only in the mobile overlay and hide it on desktop.

**Tech Stack:** Static HTML, CSS, vanilla JavaScript, Python `unittest`, Puppeteer 25.

## Global Constraints

- Preserve the public API and the stdlib-only runtime.
- Do not modify unrelated work already present in `static/app.js`, `static/app.css`, or `tests/test_smoke.py`.
- Use the repository's Puppeteer harness; do not add Playwright or browser dependencies.
- Do not push or release.

---

### Task 1: Make the Mobile Back Control Structurally Stable

**Files:**
- Modify: `tests/test_smoke.py:3115-3128`
- Modify: `static/app.js:10044-10078, 34163-34188`
- Modify: `static/app.css:6694-6706`
- Create: `changelog.d/fixed-mobile-back-2026-07-13.md`

**Interfaces:**
- Consumes: the existing `#mobileBackBtn` element in `#convToolbar` and the existing `mobileShowMain(false)` click behavior.
- Produces: the invariant that `#mobileBackBtn.parentElement.id === "convToolbar"` for the entire page lifetime.

- [ ] **Step 1: Replace the old reparenting test with a failing stability test**

In `tests/test_smoke.py`, replace `test_mobile_back_button_moves_into_visible_tab_strip` with:

```python
    def test_mobile_back_button_stays_in_stable_toolbar(self):
        """Dynamic task-tab rendering must never own the only mobile exit."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn(
            '<div class="toolbar" id="convToolbar">\n'
            '      <button class="mobile-back-btn" id="mobileBackBtn"',
            index_html,
        )
        self.assertNotIn("syncMobileBackIntoTabStrip", app_js)
        self.assertNotIn("insertBefore($mobileBackBtn", app_js)
        self.assertNotIn(".conv-tab-strip.has-mobile-back", app_css)
        self.assertIn("#convToolbar .font-size-controls { display: none !important; }", app_css)
        self.assertIn("order: -100;", app_css)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_mobile_back_button_stays_in_stable_toolbar -v
```

Expected: FAIL because `static/app.js` still contains `syncMobileBackIntoTabStrip` and `insertBefore($mobileBackBtn`, and `static/app.css` still contains `.conv-tab-strip.has-mobile-back`.

- [ ] **Step 3: Remove mobile Back reparenting from JavaScript**

In `static/app.js`, keep the stable lookup and click handler:

```javascript
  const $mobileBackBtn = document.getElementById('mobileBackBtn');
  if ($mobileBackBtn) $mobileBackBtn.addEventListener('click', () => mobileShowMain(false));
```

Delete `$mobileBackHome`, `syncMobileBackIntoTabStrip`, and `syncMobileBackForVisibleTabStrip`. Remove the call to `syncMobileBackForVisibleTabStrip()` from `handleMobileBreakpointChange`, leaving:

```javascript
  function handleMobileBreakpointChange() {
    // When transitioning to narrow viewport with an active conversation,
    // show the pane overlay; when transitioning to wide, hide it
    // (wide screens show both pane + sidebar side-by-side).
    const hasConversation = !!currentConversation;
    if (isMobile() && hasConversation) {
      mobileShowMain(true);
    } else if (!isMobile()) {
      mobileShowMain(false);
    }
  }
```

In `_convPaneRenderTabStrip`, remove both calls to `syncMobileBackIntoTabStrip`. Preserve tab rendering exactly:

```javascript
    if (!taskIds.length) {
      strip.hidden = true;
      strip.innerHTML = '';
      return;
    }
```

and:

```javascript
    strip.innerHTML = html;
    strip.hidden = false;
```

- [ ] **Step 4: Remove CSS for the obsolete temporary placement**

Delete only these selectors and declarations from `static/app.css`:

```css
    .conv-tab-strip.has-mobile-back {
      align-items: center;
      gap: 6px;
      padding-top: 4px;
    }
    .conv-tab-strip.has-mobile-back #mobileBackBtn {
      flex: 0 0 auto;
      min-height: 28px;
      padding: 4px 8px;
    }
    .conv-tab-strip.has-mobile-back #mobileBackBtn .mb-label {
      display: none;
    }
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_mobile_back_button_stays_in_stable_toolbar -v
```

Expected: `OK`, one test run.

- [ ] **Step 6: Run the complete smoke suite**

Run:

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 7: Verify the mobile lifecycle in Puppeteer**

Use the live CCC URL resolved from `~/.claude/command-center/port.txt`. In a Puppeteer page configured with `{ width: 390, height: 844, isMobile: true, hasTouch: true }`:

1. Load the dashboard with `waitUntil: 'load'`.
2. Click the first visible `.conv-item` and wait for `body.mobile-show-main`.
3. Assert `#mobileBackBtn.parentElement.id === 'convToolbar'` and that its bounding box has non-zero width and height.
4. Click `#mobileBackBtn`, open a different visible `.conv-item`, and repeat the parent and visibility assertions.
5. Save `snapshot-mobile-back.png` for visual inspection.

Use `await new Promise((resolve) => setTimeout(resolve, ms))` only if a selector/state wait cannot express the condition; Puppeteer 25 does not expose `page.waitForTimeout()`.

Expected: both assertions pass, Back occupies the leading position in the single-row reader toolbar, and the screenshot shows no overlap with the iPhone safe area.

- [ ] **Step 8: Add the user-visible changelog snippet**

Create `changelog.d/fixed-mobile-back-2026-07-13.md` with:

```markdown
Keep the mobile conversation Back button available when switching sessions after viewing subagent task tabs.
```

- [ ] **Step 9: Review and commit only this bug fix**

Run:

```bash
git diff --check -- static/app.js static/app.css tests/test_smoke.py changelog.d/fixed-mobile-back-2026-07-13.md
git diff -- static/app.js static/app.css tests/test_smoke.py changelog.d/fixed-mobile-back-2026-07-13.md
```

Because the three tracked files already contain unrelated uncommitted work, stage only the exact hunks from Steps 1, 3, and 4 plus the new changelog file. Confirm the cached diff contains no unrelated changes:

```bash
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Expected cached paths: `static/app.js`, `static/app.css`, `tests/test_smoke.py`, and `changelog.d/fixed-mobile-back-2026-07-13.md`; cached hunks contain only the mobile Back regression test, reparenting removal, and changelog entry.

Commit with:

```bash
git commit -m "fix(mobile): keep conversation back button stable"
```

Do not push.
