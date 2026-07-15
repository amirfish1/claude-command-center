# Archive Search Scope Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent archive rendering from aborting with `_ipSearchActive is not defined`, so the loading placeholder reliably clears after archive data arrives.

**Architecture:** Keep the existing archive renderer and search semantics. Compute the search-active flag once in `renderConversationList()` before its mutually exclusive grouping branches, then reuse it in both the object-grouping and archived-section paths.

**Tech Stack:** Vanilla JavaScript in `static/app.js`, Python `unittest` static regression coverage in `tests/test_smoke.py`, Puppeteer UI verification through `snapshot.js`.

## Global Constraints

- Do not change API response shapes, storage formats, search semantics, or visual design.
- Preserve all unrelated dirty changes in the shared checkout, especially `server.py` and `tests/test_perf_budget.py`.
- Keep `static/index.html` and `static/app.js` dependency-free and bundler-free.

---

### Task 1: Make archive search state available to every render branch

**Files:**
- Modify: `tests/test_smoke.py`
- Modify: `static/app.js`
- Create: `changelog.d/fixed-archive-search-scope-2026-07-15.md`

**Interfaces:**
- Consumes: `renderConversationList(filter, opts)` and the existing `#convSearch` input.
- Produces: one function-scoped boolean named `_ipSearchActive`, preserving the existing non-empty trimmed search-input definition.

- [ ] **Step 1: Write the failing scope regression test**

Add this method to `TestServerImports` in `tests/test_smoke.py` near the existing archive and search UI assertions:

```python
def test_archive_search_flag_is_scoped_for_all_render_branches(self):
    app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
    renderer = app_js[
        app_js.index("function renderConversationList(filter, opts) {"):
        app_js.index("async function setArchiveMode()")
    ]
    declaration = (
        "const _ipSearchActive = "
        "!!(document.getElementById('convSearch')?.value || '').trim();"
    )

    self.assertEqual(renderer.count(declaration), 1)
    self.assertLess(
        renderer.index(declaration),
        renderer.index("if (_shouldGroupByObjects) {")
    )
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
python3 -m pytest tests/test_smoke.py::TestServerImports::test_archive_search_flag_is_scoped_for_all_render_branches -q
```

Expected: FAIL because the declaration currently appears after `if (_shouldGroupByObjects) {`.

- [ ] **Step 3: Hoist the existing declaration**

In `renderConversationList()`, place the existing declaration immediately after `q` is computed:

```javascript
const q = (filter || '').trim().toLowerCase();
const _ipSearchActive = !!(document.getElementById('convSearch')?.value || '').trim();
```

Remove the identical declaration from inside the `_shouldGroupByObjects` branch. Do not change any use of `_ipSearchActive`.

- [ ] **Step 4: Add the user-visible changelog snippet**

Create `changelog.d/fixed-archive-search-scope-2026-07-15.md` with:

```markdown
- Fixed the conversation archive remaining on its loading screen when search-state rendering crossed sidebar grouping modes.
```

- [ ] **Step 5: Run focused and related tests**

Run:

```bash
python3 -m pytest \
  tests/test_smoke.py::TestServerImports::test_archive_search_flag_is_scoped_for_all_render_branches \
  tests/test_search_ui_static.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 6: Restart and verify the real UI**

Restart through CCC's same-origin endpoint, run the Puppeteer harness, and confirm the browser has archive rows and no `_ipSearchActive` page exception:

```bash
curl -sS -X POST \
  -H 'Origin: http://127.0.0.1:8090' \
  http://127.0.0.1:8090/api/restart
node snapshot.js
```

Expected: `/api/conversations/all?stale_ok=1` returns successfully, `#convList` no longer contains `Loading archive`, and the page console has no `_ipSearchActive is not defined` exception.

- [ ] **Step 7: Run repository verification**

Run:

```bash
python3 -m pytest tests/test_smoke.py tests/test_search_ui_static.py -q
git diff --check -- static/app.js tests/test_smoke.py changelog.d/fixed-archive-search-scope-2026-07-15.md
```

Expected: all tests PASS and `git diff --check` produces no output.

- [ ] **Step 8: Commit only this fix**

```bash
git add static/app.js tests/test_smoke.py changelog.d/fixed-archive-search-scope-2026-07-15.md
git commit --only static/app.js tests/test_smoke.py changelog.d/fixed-archive-search-scope-2026-07-15.md \
  -m "fix(ui): unblock archive rendering"
```
