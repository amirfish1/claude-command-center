# Attention Archive Cache Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `/api/attention` from launching an independent all-transcript archive scan when the dashboard already owns the same coalesced archive snapshot.

**Architecture:** Keep the attention classifier, filters, response shape, and bounded turn enrichment unchanged. Replace its direct `find_all_conversations()` acquisition with `_archive_all_rows_cached()` using the lightweight base-archive options, so attention and archive callers share the existing persisted cache, serve snapshot, build lock, and stale-while-revalidate refresh.

**Tech Stack:** Python 3 standard library, pytest, CCC's existing archive response cache.

## Global Constraints

- Preserve every public `/api/attention` response field and filtering rule.
- Add no runtime dependency; `server.py` remains standard-library-only.
- Preserve the existing uncommitted `/api/conversations/all?stale_ok=1` serve-cache change in `server.py` and its test.
- Do not merge or cherry-pick the old `perf/dashboard-live-attention` worktree's backend memoization.
- Add a `changelog.d/` snippet; do not edit `CHANGELOG.md`.

---

### Task 1: Route attention through the shared archive snapshot

**Files:**
- Modify: `tests/test_attention_detector.py:225-306`
- Modify: `server.py:62683-62722`

**Interfaces:**
- Consumes: `_archive_all_rows_cached(cache_options: dict) -> tuple[list[dict], bool]`
- Produces: `compute_attention_feed(...) -> dict` with its existing response contract and no direct call to `find_all_conversations()`

- [ ] **Step 1: Move the existing feed fixtures to the cache boundary**

Change `_stub_feed()` and `test_attention_feed_bounds_turn_reads()` to replace `_archive_all_rows_cached` instead of `find_all_conversations`:

```python
monkeypatch.setattr(
    server,
    "_archive_all_rows_cached",
    lambda options: (rows, True),
)
```

- [ ] **Step 2: Write the failing cache-reuse regression test**

Add this focused contract beside the feed tuning tests:

```python
def test_attention_feed_reuses_lightweight_archive_snapshot(monkeypatch):
    now = time.time()
    rows = [_conv("soft_block", 2, modified=now)]
    calls = []

    def cached_rows(options):
        calls.append(options)
        return rows, True

    monkeypatch.setattr(server, "_archive_all_rows_cached", cached_rows)
    monkeypatch.setattr(
        server,
        "find_all_conversations",
        lambda **kwargs: pytest.fail("attention bypassed the archive snapshot"),
    )
    monkeypatch.setattr(server, "_attention_read_turns", lambda *a, **k: [])
    monkeypatch.setattr(
        server,
        "_classify_attention",
        lambda c: {
            "kind": c["_kind"],
            "priority": c["_priority"],
            "session_id": c["session_id"],
            "name": "n",
            "where": "w",
        },
    )

    result = server.compute_attention_feed()

    assert result["shown"] == 1
    assert calls == [{
        "include_prs": False,
        "resolve_pr_states": False,
        "resolve_effective": False,
        "resolve_worktree_dirty": False,
    }]
```

- [ ] **Step 3: Run the regression test and verify the expected failure**

Run:

```bash
python3 -m pytest tests/test_attention_detector.py::test_attention_feed_reuses_lightweight_archive_snapshot -q
```

Expected: FAIL because `compute_attention_feed()` still calls the fail-fast `find_all_conversations` stub instead of `_archive_all_rows_cached`.

- [ ] **Step 4: Implement the minimal cache-boundary change**

Replace the direct discovery block in `compute_attention_feed()` with:

```python
    try:
        convs, _from_cache = _archive_all_rows_cached({
            "include_prs": False,
            "resolve_pr_states": False,
            "resolve_effective": False,
            "resolve_worktree_dirty": False,
        })
        convs = convs or []
    except Exception:
        convs = []
```

- [ ] **Step 5: Run the focused attention suite**

Run:

```bash
python3 -m pytest tests/test_attention_detector.py -q
```

Expected: all attention detector tests PASS, including the turn-read cap and the new cache-reuse contract.

- [ ] **Step 6: Commit the tested backend slice**

Review `git diff -- server.py tests/test_attention_detector.py tests/test_perf_budget.py` so the shared-checkout archive serve-cache hunk remains intact. Commit only the intended paths with a message that describes both compatible cache-sharing fixes if both are included:

```bash
git commit --only server.py tests/test_attention_detector.py tests/test_perf_budget.py \
  -m "fix(perf): share archive cache across dashboard feeds"
```

### Task 2: Document and verify the user-visible performance fix

**Files:**
- Create: `changelog.d/fixed-attention-archive-cache-2026-07-16.md`
- Verify: `server.py`
- Verify: `tests/test_attention_detector.py`
- Verify: `tests/test_perf_budget.py`
- Verify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: the cache-sharing behavior from Task 1
- Produces: a release-note fragment and verification evidence for the backend slice

- [ ] **Step 1: Add the changelog fragment**

Create the file with this single bullet:

```markdown
- Reduced dashboard CPU usage by sharing the cached conversation archive with the attention feed instead of rescanning every transcript.
```

- [ ] **Step 2: Run syntax and focused performance verification**

Run:

```bash
python3 -m py_compile server.py
python3 -m pytest tests/test_attention_detector.py tests/test_perf_budget.py -q
```

Expected: compilation succeeds and both suites PASS.

- [ ] **Step 3: Run repository smoke verification**

Run:

```bash
python3 -m pytest tests/test_smoke.py -q
git diff --check
```

Expected: smoke tests PASS and `git diff --check` prints no errors.

- [ ] **Step 4: Commit the completion slice**

```bash
git commit --only changelog.d/fixed-attention-archive-cache-2026-07-16.md \
  -m "docs(changelog): note attention CPU fix"
```

