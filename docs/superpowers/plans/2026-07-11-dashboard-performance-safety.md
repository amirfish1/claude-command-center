# Dashboard Performance Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normal CCC dashboard use bounded background work and protect message typing from history scans.

**Architecture:** Add narrow server-side scopes for group-chat state, archive time windows/pages, shared live status, and advisor freshness. Change the dashboard to request only the data appropriate to the visible state. Keep all existing API shapes additive and preserve explicit access to historical data.

**Tech Stack:** Python stdlib HTTP server, inline browser JavaScript, unittest/pytest, Puppeteer snapshot harness.

## Global Constraints

- Keep `server.py` stdlib-only.
- Preserve existing `/api/*` response fields; add optional query parameters and fields only.
- Do not wake agents, send messages, or change a model automatically.
- Group-chat activity expires 15 minutes after create, message, or participant-add; opening does not wake it.
- Default archive scope is 1 day; All requires confirmation and returns pages.
- Commit each finished slice with `git commit --only <paths>`.

---

### Task 1: Make group-chat state and background work explicit

**Files:**
- Modify: `server.py:39619` (`_list_group_chats`) and group-chat create/post/participant handlers
- Modify: `static/app.js:41786` (`pollGcActive`) and `static/app.js:43430` (group-chat timer)
- Create: `tests/test_group_chat_activity.py`

**Interfaces:**
- Produces `group_chat_activity_state(meta, now) -> "active" | "inactive" | "paused" | "closed" | "archived"`.
- Produces `GET /api/group-chats/active` with only active chat summaries.
- Consumes `last_message_at`, `participant_changed_at`, `created_at`, `paused`, `closed_at`, and `archived` sidecar fields.

- [ ] **Step 1: Write failing state tests**

```python
def test_group_chat_becomes_inactive_after_fifteen_minutes(server):
    meta = {"created_at": 1_000, "last_message_at": 1_000}
    assert server.group_chat_activity_state(meta, now=1_900) == "active"
    assert server.group_chat_activity_state(meta, now=1_901) == "inactive"

def test_opening_inactive_chat_does_not_wake_it(server):
    meta = {"last_message_at": 1_000}
    assert server.group_chat_activity_state(meta, now=1_901) == "inactive"
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `pytest -q tests/test_group_chat_activity.py`

Expected: FAIL because `group_chat_activity_state` does not exist.

- [ ] **Step 3: Add the durable state helper and lightweight active listing**

```python
GROUP_CHAT_ACTIVE_WINDOW_S = 15 * 60

def group_chat_activity_state(meta, now=None):
    now = time.time() if now is None else now
    if meta.get("archived"): return "archived"
    if meta.get("paused"): return "paused"
    if meta.get("closed_at"): return "closed"
    touched = max(meta.get("created_at") or 0,
                  meta.get("last_message_at") or 0,
                  meta.get("participant_changed_at") or 0)
    return "active" if now - touched < GROUP_CHAT_ACTIVE_WINDOW_S else "inactive"
```

Use this helper in the list route. For its active-summary route, return only
state, topic, id, and timestamps; do not call participant probes, waiting
analysis, message counting, or read chat bodies. Update message and
participant-add handlers to set the corresponding timestamp. Explicit pause,
close, and archive always win over the time window.

- [ ] **Step 4: Make the browser timer conditional and single-flight**

```javascript
let _activeChatsPoll = null;
async function pollActiveChats() {
  if (_activeChatsPoll || document.hidden) return _activeChatsPoll;
  _activeChatsPoll = fetch('/api/group-chats/active').then(r => r.json())
    .finally(() => { _activeChatsPoll = null; });
  return _activeChatsPoll;
}
```

Poll again only while the previous response contains active chats. Load full
chat detail solely in the reader-opening path. Do not schedule a sidebar render
when the active-summary identity is unchanged.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q tests/test_group_chat_activity.py tests/test_smoke.py`

Run: `node snapshot.js`

Commit: `git commit --only server.py static/app.js tests/test_group_chat_activity.py -m "fix(group-chat): stop polling inactive chats"`

### Task 2: Bound archive work by time window and page All history

**Files:**
- Modify: `server.py:53297` (`/api/conversations/all`) and archive-cache helpers
- Modify: `static/app.js:42570` (`loadArchiveAll`) and archive-window controls
- Modify: `tests/test_conversation_history_paging_static.py`
- Create: `tests/test_archive_window_bounds.py`

**Interfaces:**
- Consumes `since`, `cursor`, `limit`, and `all_confirmed` query parameters.
- Produces `{conversations, count, next_cursor, total_count, window}`.
- `since=1d|7d` filters before transcript parsing; `all_confirmed=1` permits paged all-history access.

- [ ] **Step 1: Write failing server and static-contract tests**

```python
def test_archive_one_day_skips_old_transcript_before_parser(monkeypatch, server):
    monkeypatch.setattr(server, "_archive_candidate_paths", lambda **_: [NEW, OLD])
    seen = []
    monkeypatch.setattr(server, "_extract_tail_meta", lambda p: seen.append(p) or {})
    server._build_archive_conversations(since_epoch=NEW_MTIME - 1)
    assert seen == [NEW]

def test_static_default_archive_window_is_one_day():
    source = Path("static/app.js").read_text()
    assert "ARCHIVE_WINDOW_KEY" in source
    assert "'1d'" in source
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_archive_window_bounds.py tests/test_conversation_history_paging_static.py`

Expected: FAIL because archive building has no server-side `since_epoch` bound.

- [ ] **Step 3: Filter candidates before metadata extraction and page results**

Add a single candidate-path iterator that stats each path, rejects paths older
than `since_epoch`, and applies `cursor`/`limit` after reverse-time ordering.
Include `since`, `cursor`, and `limit` in the archive cache key. Return a
`next_cursor` only when more matching rows exist. Reject an all-history request
without `all_confirmed=1` with a normal JSON confirmation payload, never an
error page.

- [ ] **Step 4: Make the browser use safe windows**

Start with `1d`. Send `since=1d` or `since=7d` to the archive route. On All,
show the returned total before requesting `all_confirmed=1`; load the first
page and add an explicit Load more action that uses `next_cursor`. Do not make
background refreshes change a selected window or load another page.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q tests/test_archive_window_bounds.py tests/test_conversation_history_paging_static.py tests/test_smoke.py`

Run: `node snapshot.js`

Commit: `git commit --only server.py static/app.js tests/test_archive_window_bounds.py tests/test_conversation_history_paging_static.py -m "fix(archive): bound history loading by window"`

### Task 3: Consolidate and bound working-now status

**Files:**
- Modify: `server.py:5088` (`build_live_sessions_activity`)
- Modify: `static/app.js:2473` (`refreshLiveSessionsActivity`) and all other live-status callers
- Modify: `tests/test_perf_budget.py:692`
- Create: `tests/test_live_activity_safety.py`

**Interfaces:**
- Produces one browser-side `refreshLiveSessionsActivity()` owner.
- Uses one server-side snapshot no older than 10 seconds.
- Returns only candidates with fresh live evidence; no full `/api/sessions?all=1` fallback.

- [ ] **Step 1: Write failing dedupe and fallback tests**

```python
def test_live_activity_uses_one_build_for_concurrent_readers(server, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_build_live_sessions_activity_uncached",
                        lambda: calls.append(1) or {})
    assert server.build_live_sessions_activity() == {}
    assert server.build_live_sessions_activity() == {}
    assert calls == [1]
```

Add a static test that forbids the `/api/sessions?all=1` fallback from live
activity UI code.

- [ ] **Step 2: Run tests and confirm the static test fails**

Run: `pytest -q tests/test_live_activity_safety.py tests/test_perf_budget.py`

- [ ] **Step 3: Use one browser owner and preserve the 10-second server snapshot**

Route hero, archive chips, and sidebar updates through the existing shared
promise. Replace the full-session fallback with last-known empty/live state.
Keep the server candidacy gate and snapshot; add an explicit test that stale
sidecars never become candidates.

- [ ] **Step 4: Verify and commit**

Run: `pytest -q tests/test_live_activity_safety.py tests/test_perf_budget.py tests/test_smoke.py`

Commit: `git commit --only server.py static/app.js tests/test_live_activity_safety.py tests/test_perf_budget.py -m "fix(activity): share bounded live status"`

### Task 4: Make Model Advisor demand-led

**Files:**
- Modify: `server.py:13708` (`_recent_session_ids`) and advisor report cache
- Modify: `static/app.js:656` (`_pollModelAdvisor`) and footer pill setup
- Modify: `tests/test_model_advisor.py`
- Create: `tests/test_model_advisor_schedule.py`

**Interfaces:**
- Produces `get_model_advisor_report(force=False)` with a 5-minute cache.
- Invalidates after a qualifying session lifecycle/model/content event, debounced 30 seconds.
- The modal calls `force=True`; the footer reads cached advice only.

- [ ] **Step 1: Write failing cache and static-schedule tests**

```python
def test_advisor_reuses_report_inside_five_minute_window(server, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "build_model_advisor_report",
                        lambda **_: calls.append(1) or {"ok": True})
    assert server.get_model_advisor_report(now=1_000) == {"ok": True}
    assert server.get_model_advisor_report(now=1_100) == {"ok": True}
    assert calls == [1]
```

Add a static assertion that the 45-second `setInterval` is absent and that the
footer does not call `/api/model-advisor` itself.

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest -q tests/test_model_advisor_schedule.py tests/test_model_advisor.py`

- [ ] **Step 3: Add cache, event invalidation, and modal-only forced refresh**

Keep the existing report builder. Wrap it in a lock-protected cache that serves
the last report for five minutes. Lifecycle/model/content event paths mark the
cache dirty after a 30-second debounce. Add `/api/model-advisor?fresh=1` for
the modal; the footer gets `/api/model-advisor` and receives cached data.

- [ ] **Step 4: Verify and commit**

Run: `pytest -q tests/test_model_advisor_schedule.py tests/test_model_advisor.py tests/test_smoke.py`

Run: `node snapshot.js`

Commit: `git commit --only server.py static/app.js tests/test_model_advisor_schedule.py tests/test_model_advisor.py -m "perf(advisor): schedule model scans by demand"`

### Task 5: Verify the user-facing performance contract

**Files:**
- Modify: `tests/test_perf_budget.py`
- Modify: `tests/test_sidebar_window_invariants.py`
- Create: `changelog.d/changed-dashboard-performance-safety-2026-07-11.md`

**Interfaces:**
- Consumes the completed APIs and browser contracts from Tasks 1–4.
- Produces stable tests for background-work ownership and visible-window bounds.

- [ ] **Step 1: Add contract-level tests**

```python
def test_no_active_group_chat_has_no_participant_probe(monkeypatch, server):
    monkeypatch.setattr(server, "_group_chat_participant_meta",
                        lambda sid: (_ for _ in ()).throw(AssertionError(sid)))
    assert server._list_active_group_chat_summaries(now=NOW) == []
```

Add static checks for the one-day archive default, confirmation-gated All,
single live-status owner, and absence of the 45-second Advisor timer.

- [ ] **Step 2: Run focused checks**

Run: `pytest -q tests/test_group_chat_activity.py tests/test_archive_window_bounds.py tests/test_live_activity_safety.py tests/test_model_advisor_schedule.py tests/test_perf_budget.py tests/test_sidebar_window_invariants.py`

- [ ] **Step 3: Run project verification and visual smoke check**

Run: `pytest -q`

Run: `node snapshot.js`

Expected: all tests pass and `snapshot.png` shows a usable 1-day conversation list.

- [ ] **Step 4: Add changelog and commit**

Write: `- Improved dashboard responsiveness by limiting invisible background scans and making conversation history load in safe, explicit scopes.`

Commit: `git commit --only tests/test_perf_budget.py tests/test_sidebar_window_invariants.py changelog.d/changed-dashboard-performance-safety-2026-07-11.md -m "perf(ui): protect dashboard interaction latency"`
