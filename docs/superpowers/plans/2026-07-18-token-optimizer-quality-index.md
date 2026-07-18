# Token Optimizer Quality Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Token Optimizer publish compact, atomic quality indexes and let CCC serve advisory quality pills only from an asynchronously refreshed in-memory map.

**Architecture:** Token Optimizer updates `quality-index.json` in its runtime directory immediately after it persists a quality result. CCC periodically stats only the two producer-owned index files, validates changed files into per-runtime maps, deterministically merges them, and swaps the combined map reference. All response construction remains a pure map lookup.

**Tech Stack:** Python standard library (`pathlib`, `json`, `tempfile`, `threading`), existing pytest suites.

## Global Constraints

- CCC must never scan a Token Optimizer directory or read a legacy `quality-cache-*.json` file on a request path.
- Invalid, missing, or stale producer indexes omit the advisory pill and preserve the last valid in-memory map.
- The producer writes `quality-index.json` with temp-file plus `os.replace`.
- Duplicate session IDs resolve by source-file mtime, then stable runtime/path ordering.

---

### Task 1: Token Optimizer producer index

**Files:**
- Modify: `skills/token-optimizer/scripts/measure.py`
- Create: `tests/test_quality_index.py`

**Interfaces:**
- Produces: `quality-index.json` with `{ "version": 1, "records": { sid: { score, grade, summary, timestamp, source_mtime, transcript_mtime } } }`.
- Consumes: `_write_quality_cache(cache_path, result)` and the evaluated transcript path.

- [ ] **Step 1: Write failing producer tests**

```python
def test_write_quality_index_preserves_existing_records_and_replaces_atomically(tmp_path):
    measure.QUALITY_CACHE_DIR = tmp_path
    assert measure._publish_quality_index(cache_path, result, transcript) is True
    assert json.loads((tmp_path / "quality-index.json").read_text())["records"][sid]["score"] == 79.2
```

- [ ] **Step 2: Run the producer test and verify it fails because `_publish_quality_index` is missing.**

Run: `python3 -m pytest tests/test_quality_index.py -q`

- [ ] **Step 3: Add atomic producer publication**

```python
def _publish_quality_index(cache_path, result, transcript_path):
    records = _read_quality_index()
    records[_quality_cache_session_id(cache_path)] = _quality_index_record(...)
    return _write_quality_index_atomic(records)
```

Call it only after each successful `_write_quality_cache`, including the clean-session result and later state persistence writes.

- [ ] **Step 4: Re-run producer tests and commit Token Optimizer separately.**

### Task 2: CCC background-only consumer

**Files:**
- Modify: `server.py`
- Modify: `tests/test_perf_budget.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: both producer `quality-index.json` files.
- Produces: `_token_optimizer_quality_for_session(session_id)` as a copy from the current in-memory map.

- [ ] **Step 1: Write failing CCC tests**

```python
def test_token_quality_index_refresh_uses_only_two_index_files(monkeypatch, tmp_path):
    _write_quality_index(tmp_path / ".claude" / "token-optimizer", records)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert server._refresh_token_optimizer_quality_index() is True
    assert server._token_optimizer_quality_for_session(sid)["quality_score"] == 95
```

Also cover malformed-index preservation, duplicate source-mtime tie breaking, and a lookup that fails if `Path.home`, `Path.iterdir`, `Path.glob`, `Path.read_text`, or `Path.stat` is invoked.

- [ ] **Step 2: Run focused tests and verify they fail because the refresher does not exist.**

Run: `python3 -m pytest tests/test_perf_budget.py -k token_quality -q`

- [ ] **Step 3: Implement the isolated refresher**

```python
def _refresh_token_optimizer_quality_index():
    # stat exactly the two index paths; parse only files whose mtime changed
    # build complete runtime maps, merge, then replace the map reference

def _token_optimizer_quality_for_session(session_id):
    return dict(_TOKEN_OPTIMIZER_QUALITY_INDEX.get(_safe_quality_sid(session_id), {}))
```

Start the low-priority daemon refresher at CCC startup. It is the only consumer code allowed to resolve the two paths or read index JSON.

- [ ] **Step 4: Re-run focused and full CCC tests, inspect the diff, and commit CCC separately.**
