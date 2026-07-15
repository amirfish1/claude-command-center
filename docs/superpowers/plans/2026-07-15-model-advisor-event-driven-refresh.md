# Model Advisor Event-Driven Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed Model Advisor polling with cached, coalesced, event-driven refreshes while ensuring existing sessions trigger one bounded cold-start scan.

**Architecture:** `model_advisor.py` owns a thread-safe report cache; `server.py` exposes cache-only, cooldown-refresh, and forced-refresh reads through the existing endpoint. `static/app.js` observes the session list already fetched by the dashboard and schedules debounced refreshes, including one initial non-empty snapshot refresh. The Bash pre-push hook receives a separate compatibility fix.

**Tech Stack:** Python 3.10+ standard library, `http.server`, vanilla JavaScript, Bash, pytest/unittest, Puppeteer.

## Global Constraints

- `server.py` and `model_advisor.py` remain stdlib-only.
- `/api/model-advisor` keeps its existing response shape; query parameters are additive.
- Footer rendering performs no independent Model Advisor polling.
- Automatic scans are debounced by 30 seconds and limited to one per five minutes.
- Opening the modal forces one fresh report; its five-second timer reads cache only.
- No recommendation is automatically applied.
- Preserve unrelated shared-main changes and do not merge sibling dashboard-performance work.
- Use Conventional Commits and a `changelog.d/` snippet; do not edit `CHANGELOG.md`.

---

### Task 1: Thread-safe server report cache

**Files:**
- Modify: `model_advisor.py`
- Modify: `server.py`
- Modify: `tests/test_model_advisor.py`

**Interfaces:**
- Produces: `model_advisor.empty_report() -> dict`
- Produces: `model_advisor.AdvisorReportCache(min_refresh_seconds=300, clock=time.monotonic)` with `get_cached()` and `refresh(builder, force=False)`
- Produces: `server.get_model_advisor_report(fresh="") -> dict`

- [ ] **Step 1: Write failing cache and endpoint tests**

Add tests that assert cache-only reads never call the builder, concurrent callers share one build, normal refresh honors the cooldown, force bypasses it, and a failing refresh preserves the last report and releases waiting callers. Add server tests replacing `_model_advisor_report_cache` with an isolated cache and asserting default, `fresh=1`, and `fresh=force` behavior.

```python
def test_failed_refresh_preserves_last_report_and_can_retry(self):
    cache = ma.AdvisorReportCache(min_refresh_seconds=300, clock=lambda: 1000.0)
    first = cache.refresh(lambda: {"ok": True, "generation": 1})
    with self.assertRaisesRegex(RuntimeError, "boom"):
        cache.refresh(lambda: (_ for _ in ()).throw(RuntimeError("boom")), force=True)
    self.assertEqual(cache.get_cached(), first)
    self.assertEqual(cache.refresh(lambda: {"ok": True, "generation": 2}, force=True)["generation"], 2)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_model_advisor.py
```

Expected: failures because `AdvisorReportCache`, `empty_report`, and `get_model_advisor_report` do not exist.

- [ ] **Step 3: Implement the minimal cache**

Add a complete empty-report factory and a `threading.Condition`-protected cache. Mark `_refreshing` before calling the builder, notify waiters on both success and failure, update `_last_refresh` only after success, and return the prior report for cooldown hits.

In `server.py`, instantiate one five-minute cache beside `MODEL_ADVISOR_LOG_FILE`, route cache-only/default and explicit refresh modes through `get_model_advisor_report`, and parse `fresh` in the existing GET handler.

```python
def get_model_advisor_report(fresh=""):
    mode = str(fresh or "").lower()
    if mode not in ("1", "true", "force"):
        return _model_advisor_report_cache.get_cached()
    return _model_advisor_report_cache.refresh(
        build_model_advisor_report,
        force=mode == "force",
    )
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 pytest command. Expected: all Model Advisor tests pass.

- [ ] **Step 5: Commit the server cache slice**

```bash
git commit --only model_advisor.py server.py tests/test_model_advisor.py -m "perf(advisor): coalesce model scans"
```

### Task 2: Event-driven browser scheduling and cold-start refresh

**Files:**
- Modify: `static/app.js`
- Create: `tests/test_model_advisor_ui_static.py`

**Interfaces:**
- Consumes: `/api/model-advisor?fresh=1|force`
- Produces: `_observeAdvisorSessionChanges(rows)`, `_scheduleAdvisorRefresh()`, and `_requestScheduledAdvisorRefresh()`

- [ ] **Step 1: Write failing static behavior tests**

Create tests that require the footer block to contain no Model Advisor endpoint or 45-second timer; modal open to call `_pollModelAdvisor('force')`; normal modal polling to call the cache-only endpoint; meaningful session changes to schedule refresh; and the initial non-empty snapshot to call `_scheduleAdvisorRefresh()` exactly through the initial-snapshot branch.

```python
def test_initial_nonempty_snapshot_schedules_one_refresh(self):
    self.assertIn("if (Object.keys(next).length) _scheduleAdvisorRefresh();", self.source)
    self.assertIn("_advisorSessionSnapshot = next;", self.source)
```

- [ ] **Step 2: Run UI static tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_model_advisor_ui_static.py
```

Expected: failure because event-driven scheduling and the static test file are absent.

- [ ] **Step 3: Implement the browser scheduler**

Change `_pollModelAdvisor` to accept an optional freshness mode, force once on modal open, and leave its five-second timer cache-only. Add these constants and state:

```javascript
const _ADVISOR_DEBOUNCE_MS = 30000;
const _ADVISOR_REFRESH_MIN_MS = 300000;
const _ADVISOR_SUBSTANTIAL_BYTES = 32768;
let _advisorSessionSnapshot = null;
let _advisorRefreshTimer = null;
let _advisorLastRefreshRequest = 0;
```

Observe `session_id`, live state, model, and size. On the first snapshot, store it and schedule once only when non-empty. On later snapshots, schedule for appearance/disappearance, live/model transitions, or at least 32 KiB growth. Remove the footer-owned 45-second network timer.

- [ ] **Step 4: Run UI and Model Advisor tests and verify GREEN**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_model_advisor_ui_static.py tests/test_model_advisor.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the UI scheduling slice**

```bash
git add -- tests/test_model_advisor_ui_static.py
git commit --only static/app.js tests/test_model_advisor_ui_static.py -m "perf(advisor): schedule scans from session changes"
```

### Task 3: Bash-compatible pre-push interpreter discovery

**Files:**
- Modify: `scripts/pre-push.sh`
- Modify: `tests/test_install_script.py`

**Interfaces:**
- Produces: Bash-compatible enumeration of PATH `python3` executables using `type -aP python3`

- [ ] **Step 1: Write the failing Bash regression test**

Add a test that copies `scripts/pre-push.sh` into a temporary repository, places a synthetic pytest-capable `python3` first on PATH, executes the hook with Bash, and asserts it runs and passes the performance gate.

- [ ] **Step 2: Run the regression test and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_install_script.py -k PrePush
```

Expected: failure because Bash does not support the current zsh-style `command -v -a` lookup.

- [ ] **Step 3: Implement the one-line Bash fix**

```bash
for candidate in "$REPO_ROOT/.venv/bin/python3" $(type -aP python3 2>/dev/null); do
```

- [ ] **Step 4: Run the install/pre-push tests and verify GREEN**

Run all of `tests/test_install_script.py`. Expected: all tests and subtests pass.

- [ ] **Step 5: Commit the hook fix**

```bash
git commit --only scripts/pre-push.sh tests/test_install_script.py -m "fix(hooks): discover pytest interpreter in bash"
```

### Task 4: Changelog and complete verification

**Files:**
- Create: `changelog.d/changed-model-advisor-scheduling-2026-07-15.md`
- Verify: all modified production and test files

**Interfaces:**
- Produces: user-visible changelog entry and release-ready verified branch

- [ ] **Step 1: Add the changelog snippet**

```markdown
Model Advisor now refreshes after meaningful session changes, coalesces scans, and avoids continuous footer polling.
```

- [ ] **Step 2: Run focused verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tests/test_model_advisor.py tests/test_model_advisor_ui_static.py tests/test_install_script.py
python3 -m compileall -q model_advisor.py server.py
git diff --check
```

Expected: all selected tests pass, compilation succeeds, and diff check is silent.

- [ ] **Step 3: Run the full test suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/
```

Expected: full suite passes with no failures.

- [ ] **Step 4: Verify the dashboard visually**

Start the isolated CCC server on port 8090, run `node snapshot.js`, and inspect `snapshot.png`. Confirm the dashboard loads, the footer remains intact, and the Model Advisor modal opens and renders after its forced refresh.

- [ ] **Step 5: Commit the completion slice**

```bash
git add -- changelog.d/changed-model-advisor-scheduling-2026-07-15.md
git commit --only changelog.d/changed-model-advisor-scheduling-2026-07-15.md \
  -m "docs(changelog): note advisor scheduling"
```

- [ ] **Step 6: Audit and publish**

Confirm the branch contains only the design, plan, advisor implementation, tests, hook fix, and changelog. Fast-forward or merge it into `main` without overwriting unrelated dirty files, then push `main` to `origin` as explicitly authorized by the user.
