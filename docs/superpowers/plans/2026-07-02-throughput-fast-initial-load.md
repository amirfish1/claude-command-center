# Throughput Fast Initial Load Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the throughput page's initial dashboard UI and graph in under 200 ms by serving a cache-only aggregate snapshot first, then refreshing the expensive aggregate in the background.

**Architecture:** Keep `/api/throughput` as the authoritative slow compute path. Add a cache-only `/api/throughput/initial` path backed by a persisted aggregate snapshot in the existing throughput cache directory. Update `static/throughput.html` so boot renders the initial payload first and replaces it with the full aggregate when the refresh completes.

**Tech Stack:** Python stdlib `http.server`, JSON files under `~/.cache/ccc-throughput-cache`, single-file frontend in `static/throughput.html`, pytest/unittest tests.

## Global Constraints

- `server.py` remains stdlib-only.
- `static/throughput.html` remains a single-file app.
- The initial endpoint must never call `find_all_conversations()` or parse transcript JSONL files.
- The full aggregate endpoint remains the authoritative source for fresh data.
- Browser/UI verification uses the repo Puppeteer harness or Chromium, not Playwright.
- Commit only touched paths with `git commit --only <paths>`.

---

### Task 1: Server Snapshot Contract

**Files:**
- Modify: `server.py`
- Modify: `tests/test_perf_budget.py`

**Interfaces:**
- Produces: `_throughput_initial_payload(session_id, repo_path=None, range_key=None) -> tuple[dict, int]`
- Produces: `_throughput_snapshot_path(session_id) -> pathlib.Path`
- Produces: `_throughput_persist_aggregate_snapshot(session_id, payload, status) -> None`

- [ ] **Step 1: Write failing tests**

Add these tests to `tests/test_perf_budget.py` near the existing throughput cache tests:

```python
def test_throughput_initial_payload_never_computes(monkeypatch, tmp_path):
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)

    def fail_find(*args, **kwargs):
        raise AssertionError("initial throughput payload must not discover conversations")

    monkeypatch.setattr(server, "find_all_conversations", fail_find)

    payload, status = server._throughput_initial_payload("all_7_days")

    assert status == 200
    assert payload["ok"] is True
    assert payload["session_id"] == "all_7_days"
    assert payload["scope"]["aggregate"] is True
    assert payload["summary"]["total_turns"] == 0
    assert payload["summary"]["hourly"] == []
    assert payload["turns"] == []
    assert payload["snapshot"]["state"] == "empty"
    assert payload["snapshot"]["cached"] is False
```

Add:

```python
def test_throughput_full_aggregate_persists_initial_snapshot(monkeypatch, tmp_path):
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(server, "find_all_conversations", lambda *a, **k: [])

    full_payload, full_status = server._throughput_payload("all_7_days")
    server._THROUGHPUT_AGG_CACHE.clear()
    initial_payload, initial_status = server._throughput_initial_payload("all_7_days")

    assert full_status == initial_status == 200
    assert initial_payload["ok"] is True
    assert initial_payload["session_id"] == "all_7_days"
    assert initial_payload["snapshot"]["state"] == "cached"
    assert initial_payload["snapshot"]["cached"] is True
    assert initial_payload["summary"] == full_payload["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_perf_budget.py::test_throughput_initial_payload_never_computes tests/test_perf_budget.py::test_throughput_full_aggregate_persists_initial_snapshot -q
```

Expected: fail because `_throughput_initial_payload` does not exist.

- [ ] **Step 3: Implement snapshot helpers**

In `server.py`, near the throughput cache constants, add snapshot helper functions:

```python
def _throughput_snapshot_path(session_id):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(session_id or ""))
    return _THROUGHPUT_DISK_CACHE_DIR / f"aggregate-{safe or 'unknown'}.json"


def _throughput_empty_initial_payload(session_id, range_key=None):
    is_aggregate, cutoff_epoch, label = _throughput_scope(session_id, range_key)
    return {
        "ok": True,
        "session_id": session_id,
        "scope": {
            "aggregate": is_aggregate,
            "range": label,
            "cutoff_epoch": cutoff_epoch,
            "total_turns": 0,
        },
        "summary": _throughput_summary([], stat_cutoff_epoch=cutoff_epoch if session_id == "all_7_days" else None),
        "turns": [],
        "snapshot": {
            "state": "empty",
            "cached": False,
            "stale": True,
            "generated_at": None,
        },
    }


def _throughput_persist_aggregate_snapshot(session_id, payload, status):
    if status != 200 or not isinstance(payload, dict) or not payload.get("ok"):
        return
    try:
        _THROUGHPUT_DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body = {
            "generated_at": time.time(),
            "payload": payload,
            "status": status,
        }
        tmp = _throughput_snapshot_path(session_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _throughput_snapshot_path(session_id))
    except Exception:
        pass


def _throughput_initial_payload(session_id, repo_path=None, range_key=None):
    is_aggregate, _cutoff_epoch, _label = _throughput_scope(session_id, range_key)
    if not is_aggregate or repo_path:
        return _throughput_empty_initial_payload(session_id, range_key), 200
    try:
        raw = _throughput_snapshot_path(session_id).read_text(encoding="utf-8")
        stored = json.loads(raw)
        payload = stored.get("payload") or {}
        status = int(stored.get("status") or 200)
        if status == 200 and payload.get("ok"):
            payload = dict(payload)
            payload["snapshot"] = {
                "state": "cached",
                "cached": True,
                "stale": True,
                "generated_at": stored.get("generated_at"),
            }
            return payload, 200
    except Exception:
        pass
    return _throughput_empty_initial_payload(session_id, range_key), 200
```

Call `_throughput_persist_aggregate_snapshot(session_id, _payload, _status)` after `_THROUGHPUT_AGG_CACHE[session_id] = ...` in `_throughput_payload()`.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command from Step 2. Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git commit --only server.py tests/test_perf_budget.py -m "feat(throughput): add fast initial snapshot payload"
```

### Task 2: HTTP Route

**Files:**
- Modify: `server.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: `_throughput_initial_payload(session_id, repo_path=None, range_key=None)`
- Produces: `GET /api/throughput/initial`

- [ ] **Step 1: Write failing static route test**

Add a small assertion to an existing static server-source test in `tests/test_smoke.py`, or add:

```python
def test_throughput_initial_route_is_registered():
    text = pathlib.Path("server.py").read_text()
    assert 'elif path == "/api/throughput/initial":' in text
    assert "_throughput_initial_payload(" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_smoke.py::test_throughput_initial_route_is_registered -q
```

Expected: fail because the route is missing.

- [ ] **Step 3: Add the HTTP route**

In `server.py`, immediately before the existing `/api/throughput` route, add:

```python
        elif path == "/api/throughput/initial":
            qs = urllib.parse.parse_qs(parsed.query)
            session_id = (qs.get("session_id", [""])[0] or "").strip()
            if not session_id:
                self.send_json({"error": "Missing session_id"}, 400)
                return
            repo_path = (qs.get("repo_path", [""])[0] or "").strip() or None
            range_key = (qs.get("range", [""])[0] or "").strip() or None
            payload, status = _throughput_initial_payload(
                session_id,
                repo_path=repo_path,
                range_key=range_key,
            )
            self.send_json(payload, status)
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command from Step 2. Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git commit --only server.py tests/test_smoke.py -m "feat(throughput): expose initial snapshot endpoint"
```

### Task 3: Frontend Boot Flow

**Files:**
- Modify: `static/throughput.html`
- Modify: `tests/test_search_ui_static.py`

**Interfaces:**
- Consumes: `GET /api/throughput/initial?session_id=all_7_days`
- Consumes: existing `renderDashboard(session, data)`
- Produces: `loadInitialAggregate()` and `refreshAggregateInBackground()` in `static/throughput.html`

- [ ] **Step 1: Write failing static UI test**

Add to `tests/test_search_ui_static.py`:

```python
def test_throughput_boot_renders_initial_snapshot_before_refresh():
    html = pathlib.Path("static/throughput.html").read_text()
    initial_idx = html.index("/api/throughput/initial")
    refresh_idx = html.index("refreshAggregateInBackground")
    assert initial_idx < refresh_idx
    assert "loadInitialAggregate(_aggDefault)" in html
    assert "renderDashboard(_aggDefault, data)" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_search_ui_static.py::test_throughput_boot_renders_initial_snapshot_before_refresh -q
```

Expected: fail because the initial endpoint and boot helpers are missing.

- [ ] **Step 3: Update boot code**

Replace the existing boot prefetch block at the bottom of `static/throughput.html` with helpers that:

```javascript
    async function refreshAggregateInBackground(session) {
      if (!session) return null;
      setStatus('Refreshing throughput data...', true);
      try {
        const res = await fetch(`/api/throughput?session_id=${encodeURIComponent(session.session_id)}`);
        if (!res.ok) throw new Error('API failed');
        const data = await res.json();
        if (data && data.ok && activeSessionId === session.session_id) {
          renderDashboard(session, data);
          setStatus('Idle');
        }
        return data;
      } catch (err) {
        console.error(err);
        setStatus('Refresh failed; showing cached throughput');
        return null;
      }
    }

    async function loadInitialAggregate(session) {
      if (!session) return null;
      try {
        const res = await fetch(`/api/throughput/initial?session_id=${encodeURIComponent(session.session_id)}`);
        if (!res.ok) throw new Error('API failed');
        const data = await res.json();
        if (data && data.ok && !activeSessionId) {
          activeSessionId = session.session_id;
          renderDashboard(session, data);
          setStatus(data.snapshot && data.snapshot.cached ? 'Showing cached throughput' : 'Showing initial throughput');
        }
        refreshAggregateInBackground(session);
        return data;
      } catch (err) {
        console.error(err);
        refreshAggregateInBackground(session);
        return null;
      }
    }
```

Then boot with:

```javascript
    const _aggDefault = aggregateSessions.find(a => a.session_id === 'all_7_days') || aggregateSessions[0];
    const _initialAggregate = loadInitialAggregate(_aggDefault);
    toggleSidebar(localStorage.getItem('ccc-throughput-sidebar-collapsed') !== '0');
    loadSessions(_initialAggregate);
    loadWeeklyUsage();
    setInterval(loadWeeklyUsage, 120000);
```

Update `loadSessions(prefetchPromise)` so it does not wait for the initial aggregate promise before rendering sessions. It should fetch conversations and render the sidebar independently.

- [ ] **Step 4: Run test to verify it passes**

Run the same pytest command from Step 2. Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git commit --only static/throughput.html tests/test_search_ui_static.py -m "feat(throughput): render cached dashboard before refresh"
```

### Task 4: Verification

**Files:**
- Modify only if verification exposes a concrete bug.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_perf_budget.py::test_throughput_initial_payload_never_computes tests/test_perf_budget.py::test_throughput_full_aggregate_persists_initial_snapshot tests/test_smoke.py::test_throughput_initial_route_is_registered tests/test_search_ui_static.py::test_throughput_boot_renders_initial_snapshot_before_refresh -q
```

Expected: all pass.

- [ ] **Step 2: Run broader smoke/perf checks**

Run:

```bash
pytest tests/test_perf_budget.py tests/test_search_ui_static.py -q
```

Expected: all pass.

- [ ] **Step 3: Run browser timing probe**

Start the local server if needed:

```bash
CCC_PORT=8090 python3 server.py
```

In another command, run a Chromium/Puppeteer timing probe against
`http://127.0.0.1:8090/throughput.html` that records the time until
`#dashboard-content` is displayed and `#throughput-chart` exists. Expected:
under 200 ms on the initial snapshot path, while the full aggregate request may
continue in the background.

- [ ] **Step 4: Add changelog snippet**

Create `changelog.d/added-throughput-fast-initial-load-2026-07-02.md`:

```markdown
Throughput dashboard now renders a cached initial graph immediately and refreshes the expensive aggregate data in the background.
```

- [ ] **Step 5: Final commit**

Run:

```bash
git commit --only changelog.d/added-throughput-fast-initial-load-2026-07-02.md -m "docs(changelog): note throughput fast initial load"
```
