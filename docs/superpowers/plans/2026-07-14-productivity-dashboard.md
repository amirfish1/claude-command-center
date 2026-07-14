# Productivity Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separate, cached productivity dashboard that explains delivered work, project/day/week activity, time use, agent leverage, and WatchTower outcomes over 6–16 weeks.

**Architecture:** A new stdlib-only `productivity.py` module owns pure aggregation, Git collection, trend math, SQLite persistence, and computer-presence sampling. `server.py` adapts existing CCC repository, transcript, throughput, and WatchTower sources into that module and exposes one additive cached API. `static/productivity.html` renders the inspectable standalone dashboard without a bundler.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `subprocess`, `datetime`, `statistics`, `threading`), existing CCC HTTP server and transcript parsers, single-file HTML/CSS/JavaScript, pytest, Puppeteer 25.

## Global Constraints

- Keep `server.py` and `productivity.py` stdlib-only; add no runtime package.
- Cover all repositories CCC has observed and group duplicate clones/worktrees into one project.
- Persist only local data under `~/.claude/command-center/`; send no productivity data over the network.
- Never expose absolute repository paths or personal Git identities in the browser payload.
- Treat exact push time and observed work time as documented proxies, not measurements.
- Bound expensive collection to 16 weeks, reuse transcript `(path, mtime, size)` caches, and never fork a subprocess per conversation.
- Preserve the existing Throughput API and page semantics.
- Use repository Puppeteer, not Playwright, for visual verification.

---

### Task 1: Pure metrics and Git evidence model

**Files:**
- Create: `productivity.py`
- Create: `tests/test_productivity_core.py`

**Interfaces:**
- Produces: `classify_commit(subject: str) -> str`, `parse_git_log(text: str, identities: set[str], project: dict) -> list[dict]`, `union_seconds(intervals: list[tuple[datetime, datetime]]) -> float`, `estimate_work_intervals(prompt_times: list[datetime]) -> list[tuple[datetime, datetime]]`, `aggregate_productivity(...) -> dict`.
- Consumes: normalized dictionaries only; it must not import `server.py`.

- [ ] **Step 1: Write failing classification and Git parser tests**

```python
from productivity import classify_commit, parse_git_log


def test_classifies_conventional_outcomes():
    assert classify_commit("feat(ui): add trends") == "feature"
    assert classify_commit("fix: avoid duplicate ticket") == "fix"
    assert classify_commit("docs: explain cache") == "other"


def test_git_parser_filters_identity_and_sums_numstat():
    raw = (
        "\x1eabc\x1f2026-07-14T08:00:00+00:00\x1fMe\x1fme@example.test"
        "\x1ffeat(ui): add trends\n10\t2\tstatic/productivity.html\n"
        "\x1edef\x1f2026-07-14T09:00:00+00:00\x1fOther\x1fother@example.test"
        "\x1ffix: unrelated\n3\t1\tserver.py\n"
    )
    rows = parse_git_log(raw, {"me@example.test"}, {"id": "repo-a", "name": "Repo A"})
    assert [(r["sha"], r["kind"], r["lines_added"], r["lines_deleted"]) for r in rows] == [
        ("abc", "feature", 10, 2)
    ]
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run: `python3 -m pytest tests/test_productivity_core.py -q`  
Expected: FAIL during import because `productivity.py` does not exist.

- [ ] **Step 3: Implement classification, stable project IDs, Git identity discovery, batched Git collection, and parser**

```python
_CONVENTIONAL_RE = re.compile(r"^([a-zA-Z]+)(?:\([^)]*\))?!?:\s+(.+)$")


def classify_commit(subject):
    match = _CONVENTIONAL_RE.match(str(subject or "").strip())
    kind = (match.group(1).lower() if match else "")
    return "feature" if kind == "feat" else "fix" if kind == "fix" else "other"


def collect_git_commits(repo, cutoff, identities):
    proc = subprocess.run(
        ["git", "-C", repo["path"], "log", "--remotes", "--since", cutoff.isoformat(),
         "--date=iso-strict", "--pretty=format:%x1e%H%x1f%cI%x1f%an%x1f%ae%x1f%s", "--numstat"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or "git log failed").strip())
    return parse_git_log(proc.stdout, identities, repo)
```

The parser must skip binary `-` numstat values, deduplicate SHA values, retain
`project_id`, `project_name`, `committed_at`, `subject`, `kind`, additions,
deletions, and changed lines, and filter exact lowercase author email matches.

- [ ] **Step 4: Write failing interval, work-time, delivery-deduplication, and trend tests**

```python
def test_union_removes_parallel_agent_overlap():
    start = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
    intervals = [(start, start + timedelta(minutes=20)),
                 (start + timedelta(minutes=10), start + timedelta(minutes=30))]
    assert union_seconds(intervals) == 30 * 60


def test_prompt_sessions_use_thirty_minute_gap_and_five_minute_tail():
    start = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
    intervals = estimate_work_intervals([start, start + timedelta(minutes=20),
                                         start + timedelta(minutes=60)])
    assert [(b - a).total_seconds() for a, b in intervals] == [25 * 60, 5 * 60]


def test_linked_watchtower_ticket_and_commit_are_one_delivery():
    payload = aggregate_productivity(
        commits=[commit("abc", "feature", "feat: add trends PRODUCTIVITY-7")],
        turns=[],
        tickets=[ticket("PRODUCTIVITY-7", "feature", "closed")],
        presence=[],
        start_date=date(2026, 7, 14), end_date=date(2026, 7, 14),
    )
    assert payload["summary"]["features"] == 1
    assert len(payload["deliveries"]) == 1
```

- [ ] **Step 5: Implement interval splitting/union, activity estimation, daily/project aggregation, delivery evidence, weekly buckets, and trend math**

```python
def estimate_work_intervals(prompt_times, gap_minutes=30, tail_minutes=5):
    times = sorted(set(prompt_times))
    if not times:
        return []
    out, start, last = [], times[0], times[0]
    for current in times[1:]:
        if current - last > timedelta(minutes=gap_minutes):
            out.append((start, last + timedelta(minutes=tail_minutes)))
            start = current
        last = current
    out.append((start, last + timedelta(minutes=tail_minutes)))
    return out
```

`aggregate_productivity` must emit all dates in the requested range, all
project totals, compact delivery evidence, weekly Monday buckets, gross/net/
parallel agent seconds, turns, tokens, prompt/work minutes, Git lines and
commits, WatchTower opened/closed counts, focus-hour counts, delivery-per-token
and delivery-per-work-hour ratios, newest-half versus oldest-half delivery
change, least-squares slope, and Pearson association only with at least four
non-empty observations and non-zero variance.

- [ ] **Step 6: Run the core suite and commit**

Run: `python3 -m pytest tests/test_productivity_core.py -q`  
Expected: PASS.

```bash
git add productivity.py tests/test_productivity_core.py
git commit -m "feat(productivity): add activity metrics core"
```

---

### Task 2: SQLite cache and computer-presence sampling

**Files:**
- Modify: `productivity.py`
- Create: `tests/test_productivity_store.py`

**Interfaces:**
- Produces: `ProductivityStore(path: Path)`, `read_macos_idle_seconds(output: str) -> float | None`, `sample_presence(store, now=None) -> dict`, `run_presence_sampler(store, stop_event=None, interval=60) -> None`.
- Consumes: the payload produced by `aggregate_productivity`.

- [ ] **Step 1: Write failing cache round-trip, schema reset, pruning, and idle parsing tests**

```python
def test_payload_round_trip(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    store.save_payload({"ok": True, "summary": {"features": 2}}, generated_at=100.0)
    assert store.load_payload()["payload"]["summary"]["features"] == 2


def test_idle_parser_reads_nanoseconds():
    assert read_macos_idle_seconds('"HIDIdleTime" = 1500000000') == 1.5


def test_focus_hour_requires_45_active_minutes(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    base = datetime(2026, 7, 14, 8, tzinfo=timezone.utc)
    for minute in range(45):
        store.record_presence(base + timedelta(minutes=minute), active=True, idle_seconds=0)
    rows = store.load_presence(base.date(), base.date())
    assert presence_summary(rows)["focus_hours"] == 1
```

- [ ] **Step 2: Run the store suite and confirm it fails**

Run: `python3 -m pytest tests/test_productivity_store.py -q`  
Expected: FAIL because the store interfaces are missing.

- [ ] **Step 3: Implement the versioned SQLite store**

```sql
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  generated_at REAL NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
  minute_epoch INTEGER PRIMARY KEY,
  active INTEGER NOT NULL,
  idle_seconds REAL,
  sampled_at REAL NOT NULL
);
```

`ProductivityStore` must create parent directories, serialize access with a
lock, replace the database on an incompatible schema version, save the full
payload transactionally under key `full-16-weeks`, return `None` for malformed
cache JSON, and prune presence samples older than 18 weeks.

- [ ] **Step 4: Implement macOS idle sampling and cross-platform coverage**

```python
def sample_presence(store, now=None):
    now = now or datetime.now(timezone.utc)
    if sys.platform != "darwin":
        return {"available": False, "reason": "unsupported_platform"}
    proc = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True,
                          text=True, timeout=3)
    idle = read_macos_idle_seconds(proc.stdout) if proc.returncode == 0 else None
    if idle is None:
        return {"available": False, "reason": "idle_time_unavailable"}
    store.record_presence(now, active=idle < 300, idle_seconds=idle)
    return {"available": True, "active": idle < 300, "idle_seconds": idle}
```

The loop catches failures, samples once per minute, and responds to a stop event
without delaying server shutdown. Tests call the parser/store directly and do
not run `ioreg`.

- [ ] **Step 5: Run the store and core suites and commit**

Run: `python3 -m pytest tests/test_productivity_core.py tests/test_productivity_store.py -q`  
Expected: PASS.

```bash
git add productivity.py tests/test_productivity_store.py
git commit -m "feat(productivity): persist cache and presence samples"
```

---

### Task 3: CCC source integration and public API

**Files:**
- Modify: `server.py`
- Create: `tests/test_productivity_server.py`
- Modify: `tests/test_perf_budget.py`

**Interfaces:**
- Consumes: `ProductivityStore`, `collect_git_commits`, and `aggregate_productivity` from `productivity.py`; existing `_load_recent_repos`, `_load_custom_repos`, `_wt_read_config`, `_q.list_items`, `find_all_conversations`, `_throughput_file_turns`, `_throughput_turns_from_events`, and `_throughput_codex_turns_from_file`.
- Produces: `_productivity_payload(force_refresh=False) -> tuple[dict, int]`, `_productivity_refresh_start() -> dict`, `_productivity_collect_inputs(start, end) -> dict`, and `GET /api/productivity`.

- [ ] **Step 1: Write failing discovery, cached-response, single-refresh, and API-route tests**

```python
def test_productivity_repo_discovery_deduplicates_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [str(tmp_path / "a")])
    monkeypatch.setattr(server, "_load_custom_repos", lambda: [str(tmp_path / "b")])
    monkeypatch.setattr(server, "_productivity_describe_repo",
                        lambda p: {"path": p, "id": "same", "name": "Project"})
    assert len(server._productivity_known_repos([])) == 1


def test_productivity_payload_returns_cache_before_refresh(monkeypatch):
    monkeypatch.setattr(server._PRODUCTIVITY_STORE, "load_payload",
                        lambda: {"generated_at": 100, "payload": {"ok": True}})
    payload, status = server._productivity_payload()
    assert status == 200
    assert payload["ok"] is True
    assert "refresh" in payload
```

Add a route-level test using the repository's handler helper pattern to prove
`/api/productivity?weeks=8` returns JSON and rejects unsupported week values by
falling back to 8 only when 8 is explicitly selected; allowed values are exactly
`6`, `8`, `12`, and `16`.

- [ ] **Step 2: Run the server tests and confirm they fail**

Run: `python3 -m pytest tests/test_productivity_server.py -q`  
Expected: FAIL because the server integration functions do not exist.

- [ ] **Step 3: Add imports, state, repository discovery, and normalized source adapters**

```python
from productivity import (ProductivityStore, aggregate_productivity,
                          collect_git_commits, run_presence_sampler)

_PRODUCTIVITY_STORE = ProductivityStore(COMMAND_CENTER_STATE_DIR / "productivity.db")
_PRODUCTIVITY_REFRESH_LOCK = threading.Lock()
_PRODUCTIVITY_REFRESH = {"state": "idle", "started_at": None,
                         "completed_at": None, "error": None}
```

Repository discovery must gather recent/custom/current/WatchTower paths plus
conversation folder paths, call `git -C <path> rev-parse --show-toplevel` once
per candidate, group by sanitized remote identity, and keep only names/IDs in
the response. Git identity discovery runs once per unique repository and
collects exact lowercase emails.

Conversation collection must call `find_all_conversations` once with all cheap
flags, keep only rows touched in 16 weeks, reuse throughput file caches, annotate
each turn with project ID/name, and retain `t_start`, `t_end`, `dur_sec`,
`trigger_type`, and normalized tokens. WatchTower collection calls
`_q.list_items()` once.

- [ ] **Step 4: Implement background refresh and additive GET route**

```python
elif path == "/api/productivity":
    qs = urllib.parse.parse_qs(parsed.query)
    try:
        weeks = int((qs.get("weeks", ["8"])[0] or "8"))
    except ValueError:
        weeks = 8
    if weeks not in (6, 8, 12, 16):
        weeks = 8
    force = (qs.get("refresh", ["0"])[0] or "0").lower() in ("1", "true", "yes")
    payload, status = _productivity_payload(force_refresh=force, weeks=weeks)
    self.send_json(payload, status)
```

No-cache requests return HTTP 202 with `state=building`; cached requests return
HTTP 200 immediately and attach current refresh state. Refresh jobs share one
thread, preserve the last successful payload after failure, and write coverage
warnings rather than aborting when individual repositories/transcripts fail.

- [ ] **Step 5: Start presence sampling in `main()` and add the performance call-count gate**

Start one daemon thread named `ccc-productivity-presence` immediately after the
plan-usage poller. The performance test must assert one invocation each of
`find_all_conversations` and `_q.list_items` per refresh and at most one Git log
subprocess per unique repository, never per conversation or commit.

- [ ] **Step 6: Run focused, smoke, and performance tests and commit**

Run: `python3 -m pytest tests/test_productivity_server.py tests/test_perf_budget.py tests/test_smoke.py -q`  
Expected: PASS.

```bash
git add server.py tests/test_productivity_server.py tests/test_perf_budget.py
git commit -m "feat(productivity): expose cached activity API"
```

---

### Task 4: Standalone dashboard and discoverability

**Files:**
- Create: `static/productivity.html`
- Modify: `static/throughput.html`
- Modify: `static/app.js`
- Create: `tests/test_productivity_static.py`

**Interfaces:**
- Consumes: `GET /api/productivity?weeks=<6|8|12|16>&refresh=<0|1>`.
- Produces: desktop/mobile Productivity page with stable element IDs used by the static and Puppeteer tests.

- [ ] **Step 1: Write failing static contract tests**

```python
def test_productivity_page_exposes_ranges_and_data_sections():
    html = Path("static/productivity.html").read_text()
    for token in ('data-weeks="6"', 'data-weeks="8"', 'data-weeks="12"',
                  'data-weeks="16"', 'id="summaryCards"', 'id="weeklyTrend"',
                  'id="projectTable"', 'id="dailyTable"', 'id="coveragePanel"'):
        assert token in html
    assert "/api/productivity" in html


def test_existing_surfaces_link_to_productivity():
    assert "/productivity.html" in Path("static/throughput.html").read_text()
    assert "/productivity.html" in Path("static/app.js").read_text()
```

- [ ] **Step 2: Run the static test and confirm it fails**

Run: `python3 -m pytest tests/test_productivity_static.py -q`  
Expected: FAIL because the page and links do not exist.

- [ ] **Step 3: Build the semantic single-file page**

The page must include:

```html
<nav class="range-tabs" aria-label="Trend range">
  <button data-weeks="6">6 weeks</button><button data-weeks="8">8 weeks</button>
  <button data-weeks="12">12 weeks</button><button data-weeks="16">16 weeks</button>
</nav>
<section id="summaryCards" aria-label="Productivity summary"></section>
<section id="weeklyTrend" aria-label="Weekly trend"></section>
<table id="projectTable"><tbody></tbody></table>
<table id="dailyTable"><tbody></tbody></table>
<aside id="coveragePanel"></aside>
```

JavaScript must fetch the selected range, poll 202 building responses, render
cached/stale/failure states without clearing the last good data, format duration
and token/line counts, escape all evidence titles, expand project/day work-item
lists, persist the selected range in localStorage, and expose an explicit refresh
button. Empty and partial-coverage states must explain what collection source is
missing.

- [ ] **Step 4: Add navigation links without changing Throughput semantics**

Add a compact `Productivity` link to the Throughput top bar and a sibling
Productivity pill beside the existing Throughput pill in `static/app.js`. The
new pill opens `/productivity.html` and has no background polling on the main
dashboard.

- [ ] **Step 5: Run static and JavaScript checks and commit**

Run: `python3 -m pytest tests/test_productivity_static.py -q && node --check static/app.js`  
Expected: PASS with no JavaScript syntax output.

```bash
git add static/productivity.html static/throughput.html static/app.js tests/test_productivity_static.py
git commit -m "feat(productivity): add project activity dashboard"
```

---

### Task 5: Real-data verification, visual QA, and changelog

**Files:**
- Create: `scripts/verify-productivity.js`
- Create: `changelog.d/added-productivity-dashboard-2026-07-14.md`
- Modify when verification finds defects: `productivity.py`, `server.py`, `static/productivity.html`, and focused tests.

**Interfaces:**
- Consumes: a running local CCC server on `http://127.0.0.1:8090`.
- Produces: deterministic browser assertions and a user-visible changelog snippet.

- [ ] **Step 1: Add the Puppeteer verifier**

```javascript
const puppeteer = require('puppeteer');
(async () => {
  const browser = await puppeteer.launch({headless: true});
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 1000});
  await page.goto('http://127.0.0.1:8090/productivity.html', {waitUntil: 'networkidle2'});
  await page.waitForSelector('#projectTable');
  await page.waitForFunction(() => !document.body.classList.contains('is-loading'), {timeout: 120000});
  const result = await page.evaluate(() => ({
    ranges: document.querySelectorAll('[data-weeks]').length,
    cards: document.querySelectorAll('#summaryCards .metric-card').length,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  if (result.ranges !== 4 || result.cards < 8 || result.overflow) throw new Error(JSON.stringify(result));
  await page.setViewport({width: 390, height: 844});
  await page.screenshot({path: 'productivity-mobile.png', fullPage: true});
  await browser.close();
})().catch(err => { console.error(err); process.exit(1); });
```

- [ ] **Step 2: Run all automated checks**

Run: `python3 -m pytest tests/test_productivity_core.py tests/test_productivity_store.py tests/test_productivity_server.py tests/test_productivity_static.py tests/test_perf_budget.py tests/test_smoke.py -q`  
Expected: PASS.

Run: `node --check static/app.js && node scripts/verify-productivity.js`  
Expected: exit 0, four ranges, at least eight metric cards, and no horizontal overflow.

- [ ] **Step 3: Inspect real 16-week data for semantic invariants**

Run: `curl -s 'http://127.0.0.1:8090/api/productivity?weeks=16&refresh=1'` and poll without `refresh=1` until the refresh state is complete. Confirm:

- every daily date is in the selected local range;
- overall totals equal the sum of project/day rows where additivity applies;
- `agent_parallel_seconds = agent_gross_seconds - agent_net_seconds` within rounding tolerance;
- no path or Git email is present in the JSON;
- delivery evidence contains titles and source IDs;
- presence coverage clearly reports the first sampled minute and uncovered history;
- project rows answer what was worked on rather than only showing quantities.

- [ ] **Step 4: Add the changelog snippet and commit verification artifacts**

`changelog.d/added-productivity-dashboard-2026-07-14.md` contains:

```markdown
- Added a separate Productivity dashboard with project and daily delivery evidence, 6–16 week trends, Git/agent/token/time metrics, computer-presence coverage, and WatchTower activity.
```

```bash
git add scripts/verify-productivity.js changelog.d/added-productivity-dashboard-2026-07-14.md
git commit -m "test(productivity): verify dashboard data and layout"
```

- [ ] **Step 5: Final completion audit**

Map each of the eight acceptance criteria in
`docs/superpowers/specs/2026-07-14-productivity-dashboard-design.md` to the API
payload, rendered page, and automated assertion that proves it. Run
`git status --short` and ensure only unrelated user/session files remain. Do not
push because the user did not request a push.
