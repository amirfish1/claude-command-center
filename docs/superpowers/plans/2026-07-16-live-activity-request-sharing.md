# Live Activity Request Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicate live-activity fetches and skip invisible-page polling without importing the old worktree's backend attention memoization.

**Architecture:** Make `refreshLiveSessionsActivity()` the single browser-side owner for live-activity requests and retain the last successful response for concurrent/hidden consumers. Route the hero statistics loader through that owner, remove its expensive full-archive fallback, and skip attention/live polling while the document is hidden.

**Tech Stack:** Browser JavaScript in `static/app.js`, pytest source-contract tests, Node syntax checking, repository Puppeteer harness.

## Global Constraints

- Preserve dashboard rendering and existing `/api/*` response contracts.
- Selectively port only the frontend behavior from commit `91d519ae64cd4d195620b73503f487107dce1b63`.
- Do not import that commit's 10-second backend attention cache.
- Do not add npm dependencies or use Playwright; UI verification uses `node snapshot.js`.

---

### Task 1: Establish one visibility-aware live-activity request owner

**Files:**
- Create: `tests/test_live_activity_safety.py`
- Modify: `static/app.js:2567-2595`
- Modify: `static/app.js:11651-11694`
- Modify: `static/app.js:54660-54676`

**Interfaces:**
- Consumes: `/api/sessions/live-activity -> {sessions: object}`
- Produces: `refreshLiveSessionsActivity() -> Promise<{sessions: object}>` as the sole browser request owner

- [ ] **Step 1: Write failing source-contract tests**

Create `tests/test_live_activity_safety.py`:

```python
"""Focused browser-side performance contracts for live activity."""

from pathlib import Path


APP_JS = Path(__file__).parents[1] / "static" / "app.js"


def test_live_activity_browser_has_one_request_owner_and_no_full_scan_fallback():
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("fetch('/api/sessions/live-activity?") == 1
    assert "fetchJSON('/api/sessions/live-activity'" not in source
    assert "fetchJSON('/api/sessions?all=1'" not in source


def test_live_activity_owner_skips_hidden_pages():
    source = APP_JS.read_text(encoding="utf-8")
    owner = source[source.index("async function refreshLiveSessionsActivity()"):
                   source.index("const $jumpBtnConv")]
    assert "document.hidden" in owner


def test_attention_refresh_skips_hidden_pages():
    source = APP_JS.read_text(encoding="utf-8")
    owner = source[source.index("async function loadAttentionList()"):
                   source.index("function focusCardOnBoard")]
    assert "document.hidden" in owner
```

- [ ] **Step 2: Run the source-contract tests and verify failure**

Run:

```bash
python3 -m pytest tests/test_live_activity_safety.py -q
```

Expected: all three tests FAIL because the hero owns a second fetch, hidden pages keep polling, and the hero falls back to `/api/sessions?all=1`.

- [ ] **Step 3: Add last-response sharing and hidden-page suppression**

Beside `_liveSessionsActivityPromise`, add:

```javascript
  let _liveSessionsActivityLast = { sessions: {} };
```

In `refreshLiveSessionsActivity()`, return the retained response for reader popouts, hidden documents, HTTP failures, and exceptions; store each successful JSON response before applying overlays:

```javascript
    if (READER_ONLY_POPOUT || document.hidden) return _liveSessionsActivityLast;
    // existing single-flight check remains here
    // ...
      if (!res.ok) return _liveSessionsActivityLast;
      const data = await res.json();
      _liveSessionsActivityLast = data || { sessions: {} };
    // existing overlay updates remain here
      return _liveSessionsActivityLast;
    } catch (_) { return _liveSessionsActivityLast; }
```

- [ ] **Step 4: Skip hidden attention refreshes**

Change the first guard in `loadAttentionList()` to:

```javascript
    if (_shipLogActive || document.hidden) return;
```

- [ ] **Step 5: Route hero live statistics through the shared owner**

Replace `loadLive()` with:

```javascript
  function loadLive() {
    return refreshLiveSessionsActivity()
      .then(function (data) {
        var sessions = data && data.sessions && typeof data.sessions === 'object' ? data.sessions : {};
        var ids = Object.keys(sessions).filter(function (id) { return sessions[id] && sessions[id].is_live; });
        return { ids: ids, sessions: sessions };
      })
      .catch(function () { return { ids: [], sessions: {} }; });
  }
```

- [ ] **Step 6: Run focused tests and JavaScript syntax verification**

Run:

```bash
python3 -m pytest tests/test_live_activity_safety.py -q
node --check static/app.js
```

Expected: all three tests PASS and Node reports no syntax error.

- [ ] **Step 7: Verify the dashboard with the repository harness**

With CCC running on `127.0.0.1:8090`, run:

```bash
node snapshot.js
```

Expected: exit code 0 and `snapshot.png` shows the dashboard loads without a stuck archive overlay or broken hero statistics.

- [ ] **Step 8: Commit only the frontend slice**

```bash
git commit --only static/app.js tests/test_live_activity_safety.py \
  -m "perf(ui): share live activity requests"
```
