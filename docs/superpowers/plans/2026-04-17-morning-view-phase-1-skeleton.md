# Morning View — Phase 1 (Skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `/morning` page inside Claude Command Center with sample hardcoded data rendered in the UI shape from the spec. Prove the module + static + route structure works end-to-end. No ingestion, no session launching, no Notion migration yet.

**Architecture:** Add a new `morning.py` module to the CCC repo root. Wire four routes into the existing `LogViewerHandler` in `server.py` (two HTML, two JSON API). Add static assets under `static/morning/`. Sample data lives as module-level constants in `morning.py` so Phase 2 can swap them for real ingestion behind the same interface.

**Tech Stack:** Python 3 stdlib (`http.server`, `json`, `urllib`, `pathlib`) — matching CCC's stdlib-only stance. Vanilla HTML/CSS/JS (no framework, no build step) — matching CCC's existing front-end. `unittest` for tests (stdlib).

**Reference spec:** `docs/superpowers/specs/2026-04-17-morning-view-design.md`

---

## File structure produced by this plan

```
dev/claude-command-center/
  morning.py                          # NEW — module with sample data + public API
  server.py                           # MODIFY — import morning, dispatch 4 routes
  static/
    index.html                        # MODIFY — add "Morning" link in nav
    morning/                          # NEW
      index.html                      # NEW — morning landing page
      goal-detail.html                # NEW — goal detail page
      morning.js                      # NEW — renders /morning
      goal-detail.js                  # NEW — renders /morning/goals/<slug>
      morning.css                     # NEW — shared styles for both pages
  tests/                              # NEW — CCC's first tests
    __init__.py                       # NEW
    test_morning.py                   # NEW — unit tests for morning.py
```

**Responsibility boundaries:**
- `morning.py` — all morning-view domain logic. Pure Python; no HTTP, no filesystem reads (Phase 1 uses hardcoded constants).
- `server.py` — HTTP dispatch only. Thin `elif` branches that call into `morning.py`.
- `static/morning/*` — presentation. JS fetches from `/api/morning/*` endpoints; no business logic in the browser.

---

## Task 1: Bootstrap tests infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_morning.py`

- [ ] **Step 1: Create `tests/__init__.py`**

Empty file, lets `unittest` discover the package.

Create `tests/__init__.py` with the following exact content (zero bytes):

*(empty file)*

- [ ] **Step 2: Create `tests/test_morning.py` with one passing sanity test**

Create `tests/test_morning.py`:

```python
import unittest


class TestInfraSmoke(unittest.TestCase):
    """Sanity check: unittest discovery works from the repo root."""

    def test_one_plus_one(self):
        self.assertEqual(1 + 1, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify discovery works**

Run from the repo root:

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected output contains:
```
test_one_plus_one (tests.test_morning.TestInfraSmoke) ... ok
Ran 1 test in 0.000s
OK
```

- [ ] **Step 4: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add tests/__init__.py tests/test_morning.py
git commit -m "test: bootstrap unittest infrastructure under tests/"
```

---

## Task 2: `morning.py` — `get_morning_state()` with sample data

**Files:**
- Create: `morning.py`
- Modify: `tests/test_morning.py` (add test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_morning.py` below the `TestInfraSmoke` class (before the `if __name__ == "__main__"` block):

```python
class TestGetMorningState(unittest.TestCase):
    def test_returns_expected_top_level_keys(self):
        from morning import get_morning_state
        state = get_morning_state()
        self.assertIn("goals", state)
        self.assertIn("strategic", state)
        self.assertIn("tactical", state)
        self.assertIn("inbox", state)
        self.assertIn("last_refreshed", state)

    def test_goals_have_required_fields(self):
        from morning import get_morning_state
        state = get_morning_state()
        self.assertGreaterEqual(len(state["goals"]), 1)
        goal = state["goals"][0]
        for field in ("slug", "name", "life_area", "ribbon"):
            self.assertIn(field, goal, f"goal missing '{field}': {goal}")

    def test_tactical_items_reference_goal_slugs(self):
        from morning import get_morning_state
        state = get_morning_state()
        goal_slugs = {g["slug"] for g in state["goals"]}
        for t in state["tactical"]:
            if t.get("goal_slug") is not None:
                self.assertIn(t["goal_slug"], goal_slugs,
                              f"tactical item references unknown goal: {t}")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected: 3 failures on `TestGetMorningState` tests with `ModuleNotFoundError: No module named 'morning'`.

- [ ] **Step 3: Create `morning.py` with sample data**

Create `morning.py` at the repo root:

```python
"""Morning view module for Claude Command Center.

Phase 1: sample data only. No filesystem reads, no MCP calls.
The public API here is what server.py wires to HTTP routes; later phases
will swap the constant-backed implementations for real ingestion without
changing these signatures.
"""

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Sample data (Phase 1 only — replaced by real ingestion in Phase 2+)
# ---------------------------------------------------------------------------

_SAMPLE_GOALS = [
    {
        "slug": "bym-growth",
        "name": "BYM growth",
        "life_area": "The Initiatives",
        "accent": "#27ae60",
        "ribbon": {
            "date": "Apr 17",
            "text": "5 commits · 3 issues closed · demo mode shipped",
            "source": "auto",
        },
    },
    {
        "slug": "nvidia-course",
        "name": "Nvidia course",
        "life_area": "The Initiatives",
        "accent": "#f39c12",
        "ribbon": {
            "date": "Apr 17",
            "text": "3 commits · spec draft landed · Eran aligned",
            "source": "auto",
        },
    },
    {
        "slug": "ai-forms",
        "name": "AI forms",
        "life_area": "The Initiatives",
        "accent": "#3498db",
        "ribbon": {
            "date": "Apr 17",
            "text": "no activity 4 days · \"$5 MCP\" still parked",
            "source": "auto",
        },
    },
    {
        "slug": "taxes",
        "name": "Taxes",
        "life_area": "HOME/FAMILY",
        "accent": "#9b59b6",
        "ribbon": {
            "date": "Apr 17",
            "text": "URGENT — deadline Apr 15 passed",
            "source": "manual",
        },
    },
]

_SAMPLE_STRATEGIC = [
    {"priority": "P0", "goal_slug": "nvidia-course",
     "text": "Come up with structure: raw material + workshop",
     "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Push/promote BYM (advance growth)",
     "source": "Notion", "age_days": 3},
    {"priority": "P0", "goal_slug": "taxes",
     "text": "Taxes",
     "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "ai-forms",
     "text": "Push AI forms (decide: launch / marketing / sales)",
     "source": "Notion", "age_days": 3},
]

_SAMPLE_TACTICAL = [
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "Re-run migration for Joyce after fixes",
     "source": "TODO.md", "age_days": 2},
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "#114 — same-day swap instructors fails",
     "source": "GH", "age_days": 0},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "ICS email invitations instead of GCal invites",
     "source": "TODO.md", "age_days": 5},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Verify new Calendly token holds all 7 scopes",
     "source": "TODO.md", "age_days": 4},
    {"priority": "P2", "goal_slug": "bym-growth",
     "text": "Test passkey auth end-to-end on production",
     "source": "PARKING", "age_days": 13},
]

_SAMPLE_INBOX = [
    {"source": "Apple Notes", "age_days": 2,
     "text": "Try using a local LLM for the Wispr transcription cleanup instead of sending to cloud",
     "suggested_goal": None},
    {"source": "Google Doc", "age_days": 1,
     "text": "Should explore putting the command center behind proper auth so I can share it with Eran",
     "suggested_goal": None},
    {"source": "Wispr", "age_days": 0,
     "text": "Idea: morning dashboard that aggregates all my todos so I don't have to remember where things are",
     "suggested_goal": None},
    {"source": "Apple Notes", "age_days": 4,
     "text": "Find a decent mattress finally",
     "suggested_goal": None},
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_morning_state():
    """Return the full state needed to render /morning.

    Phase 1: sample data. Phase 2 will replace with real aggregation from
    ingestion workers without changing this shape.
    """
    return {
        "goals": list(_SAMPLE_GOALS),
        "strategic": list(_SAMPLE_STRATEGIC),
        "tactical": list(_SAMPLE_TACTICAL),
        "inbox": list(_SAMPLE_INBOX),
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected: all 4 tests pass (1 smoke + 3 new).

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add morning.py tests/test_morning.py
git commit -m "feat(morning): add get_morning_state() with sample data"
```

---

## Task 3: `morning.py` — `get_goal_detail(slug)` with sample data

**Files:**
- Modify: `morning.py` (append sample goal-detail data + function)
- Modify: `tests/test_morning.py` (append test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_morning.py` (before the `if __name__ == "__main__"` line):

```python
class TestGetGoalDetail(unittest.TestCase):
    def test_returns_none_for_unknown_slug(self):
        from morning import get_goal_detail
        self.assertIsNone(get_goal_detail("does-not-exist"))

    def test_returns_expected_shape_for_known_slug(self):
        from morning import get_goal_detail
        detail = get_goal_detail("bym-growth")
        self.assertIsNotNone(detail)
        for key in ("slug", "name", "life_area", "intent_markdown",
                    "strategies", "tactical_tagged", "deliverables",
                    "context_library", "recent_sessions"):
            self.assertIn(key, detail, f"missing key {key!r}")

    def test_strategies_have_session_state(self):
        from morning import get_goal_detail
        detail = get_goal_detail("bym-growth")
        self.assertGreaterEqual(len(detail["strategies"]), 1)
        for s in detail["strategies"]:
            self.assertIn("id", s)
            self.assertIn("text", s)
            self.assertIn("status", s)
            self.assertIn("session_state", s)
            self.assertIn(s["session_state"], ("alive", "dormant", "never", "dropped"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected: 3 failures on `TestGetGoalDetail` with `ImportError` on `get_goal_detail`.

- [ ] **Step 3: Append to `morning.py`**

Append at the end of `morning.py`:

```python
# ---------------------------------------------------------------------------
# Sample goal-detail data (Phase 1 only)
# ---------------------------------------------------------------------------

_SAMPLE_GOAL_DETAILS = {
    "bym-growth": {
        "slug": "bym-growth",
        "name": "BYM growth",
        "life_area": "The Initiatives",
        "accent": "#27ae60",
        "intent_markdown": (
            "1 paying studio (Joyce, LCPP) \u2192 10 by end of Q2. "
            "Growth is the gating constraint on proving BYM is a real business "
            "vs. a one-customer project.\n\n"
            "**Success:** 10 active paying studios \u00b7 $5k MRR \u00b7 3 referrals from existing customers."
        ),
        "strategies": [
            {"id": "demo-mode", "text": "Ship demo mode (anonymized data for prospect tours)",
             "status": "done", "session_state": "dormant",
             "session_summary": "session 01HK...7fA2 \u00b7 last active Apr 17 \u00b7 2h, 12 commits, 14 files touched"},
            {"id": "affiliates", "text": "Find 3 pilates-studio affiliates",
             "status": "active", "session_state": "alive",
             "session_summary": "session 01HK...B9C1 \u00b7 alive in iTerm tab \"affiliates\" \u00b7 last input 14m ago"},
            {"id": "fb-groups", "text": "Post in 5 Facebook pilates-instructor groups (one per week)",
             "status": "active", "session_state": "dormant",
             "session_summary": "session 01HK...D4E7 \u00b7 dormant since Apr 10 \u00b7 0/5 posts"},
            {"id": "video-ad", "text": "Create 60s demo video walking through booking flow",
             "status": "active", "session_state": "never",
             "session_summary": "no session yet \u00b7 click Start to spawn"},
            {"id": "linkedin", "text": "LinkedIn post series: \"I built a pilates booking system\" (3 posts)",
             "status": "active", "session_state": "alive",
             "session_summary": "session 01HK...F1B3 \u00b7 headless (pid 48721) \u00b7 1/3 drafted"},
            {"id": "youtube-ad", "text": "YouTube ad buy ($500)",
             "status": "dropped", "session_state": "dropped",
             "session_summary": "claude: dropped Apr 13, too early"},
        ],
        "tactical_tagged": [
            {"text": "Push/promote BYM", "source": "Notion P1", "strategy_id": "affiliates"},
            {"text": "ICS email invitations", "source": "TODO.md", "strategy_id": None},
            {"text": "#114 instructor swap bug", "source": "GH", "strategy_id": None},
        ],
        "deliverables": [
            {"type": "COMMIT", "label": "demo mode shipped \u00b7 a3f8c21", "source": "demo-mode session"},
            {"type": "FILE", "label": "apps/bookyourmat/.../DemoModeProvider.tsx", "source": "Write \u00b7 demo-mode"},
            {"type": "DRAFT", "label": "~/Drive/BYM/linkedin/post-1.md", "source": "Write \u00b7 linkedin"},
            {"type": "LIST", "label": "~/Drive/BYM/fb-groups.md (12 groups)", "source": "Write \u00b7 fb-groups"},
        ],
        "context_library": [],  # populated in Phase 4
        "recent_sessions": [
            {"summary": "Ship demo mode", "when": "Apr 17 \u00b7 2h \u00b7 12 commits"},
            {"summary": "Fix Joyce swap-instructor bug", "when": "Apr 17 \u00b7 45m"},
            {"summary": "Reach out to pilates studios (affiliates)", "when": "Apr 17 \u00b7 still alive"},
            {"summary": "LinkedIn post #1 draft", "when": "Apr 14 \u00b7 30m"},
        ],
    },
}


def get_goal_detail(slug):
    """Return the full detail for a single goal, or None if slug is unknown.

    Phase 1: sample data only.
    """
    data = _SAMPLE_GOAL_DETAILS.get(slug)
    if data is None:
        return None
    # Shallow copy so callers can't mutate our constant.
    return dict(data)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add morning.py tests/test_morning.py
git commit -m "feat(morning): add get_goal_detail() with sample data"
```

---

## Task 4: Wire morning routes into `server.py`

**Files:**
- Modify: `server.py` (add import + 4 route branches)

The existing dispatch pattern is a chain of `elif` branches in `do_GET` on `LogViewerHandler`. Each branch calls a helper and either `send_html()` or `send_json()`. See existing examples at `server.py:3241-3317`.

Static HTML files for Phase 1 live at `static/morning/index.html` and `static/morning/goal-detail.html`. We read them on every request (matching how `index.html` is reloaded at `server.py:3228-3233`) so edits are visible without a server restart.

- [ ] **Step 1: Add `morning` import near the top of `server.py`**

Find the block of stdlib imports near the top of `server.py` (they start around line 1). Find the existing line (look for: `from pathlib import Path`). Add `import morning` immediately after the last `import ...` of a local module, or alongside the stdlib imports if no local imports exist yet. A safe landing spot is immediately after `STATIC_DIR = CCC_ROOT / "static"` (around line 37):

Locate:
```python
STATIC_DIR = CCC_ROOT / "static"
```

Add on the next line:
```python
MORNING_STATIC_DIR = STATIC_DIR / "morning"

import morning  # morning.py — goals/tasks/inbox API (Phase 1 sample data)
```

- [ ] **Step 2: Add the morning GET routes**

Find `class LogViewerHandler` and its `do_GET` method (at `server.py:3236`). The existing dispatch chain ends with:

```python
        else:
            self.send_json({"error": "Not found"}, 404)
```

(at `server.py:3316-3317`).

Add the four new branches immediately before that final `else:`. Insert:

```python
        elif path == "/morning":
            try:
                self.send_html((MORNING_STATIC_DIR / "index.html").read_text())
            except OSError as e:
                self.send_json({"error": "morning/index.html missing", "detail": str(e)}, 500)
        elif re.match(r"^/morning/goals/[A-Za-z0-9_-]+$", path):
            try:
                self.send_html((MORNING_STATIC_DIR / "goal-detail.html").read_text())
            except OSError as e:
                self.send_json({"error": "morning/goal-detail.html missing", "detail": str(e)}, 500)
        elif path == "/api/morning/state":
            self.send_json(morning.get_morning_state())
        elif re.match(r"^/api/morning/goals/[A-Za-z0-9_-]+$", path):
            slug = path.rsplit("/", 1)[-1]
            detail = morning.get_goal_detail(slug)
            if detail is None:
                self.send_json({"error": f"unknown goal: {slug}"}, 404)
            else:
                self.send_json(detail)
```

- [ ] **Step 3: Serve CSS/JS static files under /static/morning/**

Static assets (`morning.js`, `morning.css`, `goal-detail.js`) need to be reachable from the HTML pages. CCC does not currently serve `/static/*` generically (the only static resource exposed today is `static/index.html` served at `/`). So add this branch right above the four morning branches (inside `do_GET`):

```python
        elif path.startswith("/static/morning/"):
            rel = path[len("/static/morning/"):]
            target = MORNING_STATIC_DIR / rel
            if not target.is_file():
                self.send_json({"error": f"not found: {path}"}, 404)
            else:
                try:
                    body = target.read_bytes()
                except OSError as e:
                    self.send_json({"error": str(e)}, 500)
                    return
                # Content-Type from extension
                ct = "text/plain"
                if rel.endswith(".js"):
                    ct = "application/javascript"
                elif rel.endswith(".css"):
                    ct = "text/css"
                elif rel.endswith(".html"):
                    ct = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(body)
```

If CCC already serves `/static/...`, skip this step — existing handling applies.

- [ ] **Step 4: Manual verification — server starts, routes return sensible responses**

Start the server:

```bash
cd /Users/amirfish/dev/claude-command-center
./run.sh &
sleep 2
```

From another shell, curl the routes:

```bash
curl -s http://localhost:8090/api/morning/state | python3 -c "import sys,json; d=json.load(sys.stdin); print('goals:', len(d['goals']), 'tactical:', len(d['tactical']))"
curl -s http://localhost:8090/api/morning/goals/bym-growth | python3 -c "import sys,json; d=json.load(sys.stdin); print('strategies:', len(d['strategies']))"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/api/morning/goals/does-not-exist
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8090/morning
```

Expected output:
```
goals: 4 tactical: 5
strategies: 6
404
200
```

(The `/morning` HTML will 500 until Task 5 creates the file — that's fine for this task. Verify only the JSON endpoints return 200 and the content above.)

Stop the server:

```bash
pkill -f "python.*server.py" || true
```

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add server.py
git commit -m "feat(morning): wire /morning routes + JSON API into server.py"
```

---

## Task 5: Morning landing page — HTML scaffold

**Files:**
- Create: `static/morning/index.html`

- [ ] **Step 1: Create the HTML file**

Create `static/morning/index.html` with this exact content:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CCC · Morning</title>
  <link rel="stylesheet" href="/static/morning/morning.css">
</head>
<body>
  <div class="mv-wrap">
    <nav class="mv-nav">
      <a class="active">Morning</a>
      <a href="/">Kanban</a>
      <span class="mv-nav-meta" id="refresh-meta">loading…</span>
      <button id="scan-now" title="re-scan all sources">Scan now</button>
    </nav>

    <section class="mv-section">
      <h3>Goals · Q2 2026</h3>
      <div id="goals-row" class="mv-goals"></div>
    </section>

    <section class="mv-section">
      <h3>This week · strategic</h3>
      <div id="strategic-list"></div>
    </section>

    <section class="mv-section">
      <h3>Today · tactical (auto-aggregated)</h3>
      <div id="tactical-list"></div>
    </section>

    <section class="mv-section">
      <h3>Inbox · needs triage <span id="inbox-count" class="muted"></span></h3>
      <div id="inbox-list"></div>
    </section>
  </div>

  <script src="/static/morning/morning.js"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add static/morning/index.html
git commit -m "feat(morning): add HTML scaffold for /morning landing page"
```

---

## Task 6: Morning landing page — CSS

**Files:**
- Create: `static/morning/morning.css`

- [ ] **Step 1: Create the CSS file**

Create `static/morning/morning.css` with this exact content:

```css
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #14171c;
  color: #ddd;
  margin: 0;
  padding: 0;
}

.mv-wrap { max-width: 1200px; margin: 0 auto; padding: 20px; }

.mv-nav {
  display: flex; align-items: center; gap: 16px;
  padding: 10px 12px;
  background: #1a1d23; border-radius: 8px;
  font-size: 13px; color: #9aa;
  margin-bottom: 20px;
}
.mv-nav a { color: #5ac8fa; text-decoration: none; cursor: pointer; }
.mv-nav .active { color: #fff; font-weight: 600; cursor: default; }
.mv-nav-meta { margin-left: auto; font-size: 11px; color: #666; }
.mv-nav button {
  background: #2b3038; border: 1px solid #444; color: #9aa;
  padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
}

.mv-section { margin-bottom: 24px; }
.mv-section h3 {
  font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px;
  color: #aaa; border-bottom: 1px solid #333;
  padding-bottom: 6px; margin-bottom: 10px;
}

.muted { color: #888; font-weight: 400; font-size: 11px; }

/* Goals */
.mv-goals {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
}
.mv-goal {
  background: #23272e; border-radius: 10px; padding: 14px;
  border-left: 4px solid var(--accent, #5ac8fa);
  cursor: pointer; transition: background 0.1s;
}
.mv-goal:hover { background: #2a2f37; }
.mv-goal .cat { font-size: 10px; text-transform: uppercase; color: #888; letter-spacing: 1px; }
.mv-goal .name { font-weight: 600; font-size: 15px; margin: 4px 0 10px; }
.mv-goal .ribbon {
  font-size: 12px; color: #ccc; background: #2b3038;
  padding: 6px 8px; border-radius: 4px; border-left: 3px solid var(--accent, #5ac8fa);
}
.mv-goal .ribbon .date { color: #888; font-size: 10px; display: block; margin-bottom: 2px; }

/* Task rows (strategic + tactical share this) */
.mv-task {
  display: flex; gap: 10px; padding: 8px 0;
  border-bottom: 1px dashed #2a2e35; font-size: 14px; align-items: center;
}
.mv-task .pri {
  font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 3px;
  min-width: 22px; text-align: center;
}
.mv-task .pri.P0 { background: #c0392b; color: #fff; }
.mv-task .pri.P1 { background: #e67e22; color: #fff; }
.mv-task .pri.P2 { background: #555; color: #eee; }
.mv-task .src { font-size: 10px; color: #888; background: #2a2e35; padding: 2px 6px; border-radius: 3px; }
.mv-task .goal-chip { font-size: 10px; color: #5ac8fa; }
.mv-task .text { flex: 1; color: #ddd; }
.mv-task .ago { font-size: 10px; color: #666; }

/* Inbox */
.mv-inbox-item {
  background: #2a2e35; padding: 10px 12px; border-radius: 6px;
  margin-bottom: 8px; font-size: 13px; color: #ccc;
  display: flex; gap: 10px; align-items: flex-start;
}
.mv-inbox-item .src { font-size: 10px; color: #888; white-space: nowrap; }
.mv-inbox-item .actions { display: flex; gap: 6px; }
.mv-inbox-item button {
  font-size: 11px; padding: 3px 8px; border-radius: 3px;
  border: 1px solid #444; background: #1a1d23; color: #9aa; cursor: pointer;
}
.mv-inbox-item button.promote { border-color: #3a7; color: #3a7; }
```

- [ ] **Step 2: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add static/morning/morning.css
git commit -m "feat(morning): add CSS for /morning landing page"
```

---

## Task 7: Morning landing page — JS render logic

**Files:**
- Create: `static/morning/morning.js`

- [ ] **Step 1: Create the JS file**

Create `static/morning/morning.js` with this exact content:

```javascript
(function () {
  "use strict";

  function age(days) {
    if (days === 0) return "today";
    if (days === 1) return "1d";
    return days + "d";
  }

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") e.className = attrs[k];
      else if (k === "style" && typeof attrs[k] === "object") Object.assign(e.style, attrs[k]);
      else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  function renderGoal(goal) {
    const card = el("div", {
      class: "mv-goal",
      style: { "--accent": goal.accent || "#5ac8fa" },
      onclick: () => { window.location.href = "/morning/goals/" + encodeURIComponent(goal.slug); }
    },
      el("div", { class: "cat" }, goal.life_area),
      el("div", { class: "name" }, goal.name),
      el("div", { class: "ribbon", style: { "border-left-color": goal.accent || "#5ac8fa" } },
        el("span", { class: "date" }, (goal.ribbon && goal.ribbon.date || "") + (goal.ribbon && goal.ribbon.source ? " (" + goal.ribbon.source + ")" : "")),
        (goal.ribbon && goal.ribbon.text) || ""
      )
    );
    // Inline color set via style object above isn't picking up CSS variable on all browsers
    // for border-left. Set the CSS variable directly:
    card.style.setProperty("--accent", goal.accent || "#5ac8fa");
    return card;
  }

  function renderTaskRow(task) {
    return el("div", { class: "mv-task" },
      el("span", { class: "pri " + task.priority }, task.priority),
      task.goal_slug ? el("span", { class: "goal-chip" }, task.goal_slug + " ›") : null,
      el("span", { class: "text" }, task.text),
      el("span", { class: "src" }, task.source),
      el("span", { class: "ago" }, age(task.age_days))
    );
  }

  function renderInboxItem(item) {
    return el("div", { class: "mv-inbox-item" },
      el("span", { class: "src" }, item.source + "\n" + age(item.age_days) + " ago"),
      el("span", { style: { flex: "1" } }, item.text),
      el("span", { class: "actions" },
        el("button", { class: "promote" }, "promote →"),
        el("button", {}, "dismiss")
      )
    );
  }

  async function load() {
    let state;
    try {
      const r = await fetch("/api/morning/state");
      if (!r.ok) throw new Error("HTTP " + r.status);
      state = await r.json();
    } catch (e) {
      document.getElementById("refresh-meta").textContent = "load failed: " + e.message;
      return;
    }

    const goalsRow = document.getElementById("goals-row");
    goalsRow.innerHTML = "";
    for (const g of state.goals) goalsRow.appendChild(renderGoal(g));

    const strat = document.getElementById("strategic-list");
    strat.innerHTML = "";
    for (const t of state.strategic) strat.appendChild(renderTaskRow(t));

    const tact = document.getElementById("tactical-list");
    tact.innerHTML = "";
    for (const t of state.tactical) tact.appendChild(renderTaskRow(t));

    const inb = document.getElementById("inbox-list");
    inb.innerHTML = "";
    for (const i of state.inbox) inb.appendChild(renderInboxItem(i));
    document.getElementById("inbox-count").textContent =
      "— " + state.inbox.length + " candidates from free-form sources";

    const ts = state.last_refreshed ? new Date(state.last_refreshed) : null;
    document.getElementById("refresh-meta").textContent =
      ts ? ("last refreshed " + ts.toLocaleTimeString()) : "";
  }

  document.getElementById("scan-now").addEventListener("click", load);
  load();
})();
```

- [ ] **Step 2: Manual verification — full morning page renders**

```bash
cd /Users/amirfish/dev/claude-command-center
./run.sh &
sleep 2
```

Open `http://localhost:8090/morning` in a browser.

Expected:
- Nav bar at top with "Morning" active, "Kanban" clickable, "Scan now" button, last-refreshed timestamp on the right.
- Goals row shows 4 cards (BYM growth / Nvidia course / AI forms / Taxes) with colored left border and ribbon text.
- This-week section shows 4 strategic rows.
- Today section shows 5 tactical rows.
- Inbox section shows 4 candidates with promote/dismiss buttons.

Click a goal card → URL should change to `/morning/goals/bym-growth` and return whatever the HTML file renders (Task 8 makes this meaningful).

Stop the server:
```bash
pkill -f "python.*server.py" || true
```

- [ ] **Step 3: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add static/morning/morning.js
git commit -m "feat(morning): render /morning page from /api/morning/state"
```

---

## Task 8: Goal detail page — HTML + JS

**Files:**
- Create: `static/morning/goal-detail.html`
- Create: `static/morning/goal-detail.js`
- Modify: `static/morning/morning.css` (append goal-detail styles)

- [ ] **Step 1: Create `static/morning/goal-detail.html`**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CCC · Goal</title>
  <link rel="stylesheet" href="/static/morning/morning.css">
</head>
<body>
  <div class="mv-wrap">
    <div class="gd-header" id="gd-header">
      <span class="cat" id="gd-life-area"></span>
      <span class="name" id="gd-name"></span>
      <div class="actions">
        <a class="mv-nav-link" href="/morning">← Morning</a>
      </div>
    </div>

    <div class="gd-grid">
      <div>
        <section class="gd-panel">
          <h4>Intent</h4>
          <div id="gd-intent"></div>
        </section>
        <section class="gd-panel">
          <h4>Strategies <span class="muted">— each owns a persistent Claude session</span></h4>
          <div id="gd-strategies"></div>
        </section>
        <section class="gd-panel">
          <h4>Tactical items tagged to this goal</h4>
          <div id="gd-tactical"></div>
        </section>
      </div>

      <div>
        <section class="gd-panel">
          <h4>Deliverables <span class="muted">— derived from session transcripts</span></h4>
          <div id="gd-deliverables"></div>
        </section>
        <section class="gd-panel">
          <h4>Context library</h4>
          <div id="gd-context"></div>
        </section>
        <section class="gd-panel">
          <h4>Recent Claude sessions</h4>
          <div id="gd-sessions"></div>
        </section>
      </div>
    </div>

    <div id="gd-error" class="gd-error" hidden></div>
  </div>

  <script src="/static/morning/goal-detail.js"></script>
</body>
</html>
```

- [ ] **Step 2: Append goal-detail styles to `static/morning/morning.css`**

Append the following at the end of `static/morning/morning.css`:

```css
/* Goal detail */
.gd-header {
  display: flex; align-items: center; gap: 12px;
  padding: 14px; background: #23272e; border-radius: 10px;
  border-left: 4px solid #27ae60; margin-bottom: 18px;
}
.gd-header .cat { font-size: 10px; color: #27ae60; text-transform: uppercase; letter-spacing: 1px; }
.gd-header .name { font-size: 18px; font-weight: 600; }
.gd-header .actions { margin-left: auto; }
.gd-header .mv-nav-link {
  color: #5ac8fa; text-decoration: none; font-size: 12px;
  background: #2b3038; border: 1px solid #444; padding: 6px 12px; border-radius: 4px;
}

.gd-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; }
.gd-panel { background: #23272e; border-radius: 8px; padding: 14px; margin-bottom: 14px; }
.gd-panel h4 {
  margin: 0 0 10px; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #aaa;
}

.strat-row {
  padding: 12px; background: #2b3038; border-radius: 6px; margin-bottom: 8px;
  display: flex; gap: 12px; align-items: flex-start;
}
.strat-row.done { opacity: 0.55; }
.strat-row.dropped { opacity: 0.4; }
.strat-row .sess-dot {
  width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex-shrink: 0;
}
.sess-dot.alive { background: #27ae60; box-shadow: 0 0 6px #27ae60; }
.sess-dot.dormant { background: #e67e22; }
.sess-dot.never, .sess-dot.dropped { background: #555; }
.strat-row .body { flex: 1; }
.strat-row .text { font-size: 13px; color: #ddd; }
.strat-row.done .text { text-decoration: line-through; }
.strat-row .sum { font-family: monospace; font-size: 10px; color: #888; margin-top: 4px; }
.strat-row .launch {
  background: #27ae60; border: none; color: #fff; padding: 6px 12px;
  border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: 600;
}
.strat-row .launch.dormant { background: #e67e22; }
.strat-row .launch.new { background: #5ac8fa; color: #1a1d23; }

.deliv-row {
  display: flex; gap: 8px; padding: 7px 0;
  border-bottom: 1px dashed #333; font-size: 12px; align-items: center;
}
.deliv-row .type {
  font-size: 9px; background: #2b3038; padding: 2px 5px; border-radius: 3px;
  color: #888; min-width: 48px; text-align: center;
}
.deliv-row .label { flex: 1; color: #ddd; font-family: monospace; font-size: 11px; }
.deliv-row .src { font-size: 9px; color: #666; }

.sess-summary { font-size: 12px; color: #ccc; padding: 8px 0; border-bottom: 1px dashed #333; }
.sess-summary .ago { font-size: 10px; color: #666; }

.gd-error {
  padding: 14px; background: #3a1a1a; border: 1px solid #c0392b;
  border-radius: 6px; color: #f2d2d2;
}
```

- [ ] **Step 3: Create `static/morning/goal-detail.js`**

```javascript
(function () {
  "use strict";

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const k in (attrs || {})) {
      if (k === "class") e.className = attrs[k];
      else if (k === "style" && typeof attrs[k] === "object") Object.assign(e.style, attrs[k]);
      else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    for (const c of children) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  function slugFromPath() {
    // /morning/goals/<slug>
    const parts = window.location.pathname.split("/").filter(Boolean);
    return parts.length >= 3 ? decodeURIComponent(parts[2]) : "";
  }

  function launchLabel(state) {
    if (state === "alive") return "▶ Inject";
    if (state === "dormant") return "▶ Resume";
    if (state === "never") return "▶ Start";
    return "—";
  }

  function renderStrategy(s) {
    const statusClass = s.status === "done" ? " done" : (s.status === "dropped" ? " dropped" : "");
    const dot = el("span", { class: "sess-dot " + s.session_state });
    const btn = s.session_state === "dropped"
      ? null
      : el("button", { class: "launch " + (s.session_state === "dormant" ? "dormant" : s.session_state === "never" ? "new" : "") }, launchLabel(s.session_state));
    return el("div", { class: "strat-row" + statusClass },
      dot,
      el("div", { class: "body" },
        el("div", { class: "text" }, s.text),
        el("div", { class: "sum" }, s.session_summary || "")
      ),
      btn
    );
  }

  async function load() {
    const slug = slugFromPath();
    if (!slug) {
      showError("No goal slug in URL.");
      return;
    }

    let detail;
    try {
      const r = await fetch("/api/morning/goals/" + encodeURIComponent(slug));
      if (r.status === 404) { showError("Goal not found: " + slug); return; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      detail = await r.json();
    } catch (e) {
      showError("Load failed: " + e.message);
      return;
    }

    document.getElementById("gd-life-area").textContent = detail.life_area || "";
    document.getElementById("gd-name").textContent = detail.name || slug;
    document.getElementById("gd-intent").textContent = detail.intent_markdown || "";
    document.title = "CCC · " + (detail.name || slug);

    const header = document.getElementById("gd-header");
    if (detail.accent) header.style.borderLeftColor = detail.accent;

    const strats = document.getElementById("gd-strategies");
    strats.innerHTML = "";
    for (const s of (detail.strategies || [])) strats.appendChild(renderStrategy(s));

    const tact = document.getElementById("gd-tactical");
    tact.innerHTML = "";
    for (const t of (detail.tactical_tagged || [])) {
      tact.appendChild(el("div", { class: "mv-task" },
        el("span", { class: "text" }, t.text),
        el("span", { class: "src" }, t.source),
        t.strategy_id
          ? el("span", { class: "goal-chip" }, "→ " + t.strategy_id)
          : el("span", { class: "muted" }, "untagged")
      ));
    }

    const deliv = document.getElementById("gd-deliverables");
    deliv.innerHTML = "";
    for (const d of (detail.deliverables || [])) {
      deliv.appendChild(el("div", { class: "deliv-row" },
        el("span", { class: "type" }, d.type),
        el("span", { class: "label" }, d.label),
        el("span", { class: "src" }, d.source || "")
      ));
    }

    const ctx = document.getElementById("gd-context");
    ctx.innerHTML = "";
    if (!(detail.context_library || []).length) {
      ctx.appendChild(el("div", { class: "muted" }, "No attachments yet — Phase 4 wires this up."));
    } else {
      for (const c of detail.context_library) {
        ctx.appendChild(el("div", { class: "deliv-row" },
          el("span", { class: "type" }, c.type || "DOC"),
          el("span", { class: "label" }, c.label || c.path || ""),
          el("span", { class: "src" }, c.source || "")
        ));
      }
    }

    const sess = document.getElementById("gd-sessions");
    sess.innerHTML = "";
    for (const s of (detail.recent_sessions || [])) {
      sess.appendChild(el("div", { class: "sess-summary" },
        el("span", {}, s.summary),
        el("span", { class: "ago" }, " · " + s.when)
      ));
    }
  }

  function showError(msg) {
    const e = document.getElementById("gd-error");
    e.hidden = false;
    e.textContent = msg;
  }

  load();
})();
```

- [ ] **Step 4: Manual verification**

```bash
cd /Users/amirfish/dev/claude-command-center
./run.sh &
sleep 2
```

Navigate to `http://localhost:8090/morning`, click the "BYM growth" card. Expected URL: `/morning/goals/bym-growth`.

Verify:
- Header shows "THE INITIATIVES" + "BYM growth".
- Intent panel shows the paragraph about Joyce → 10 studios.
- Strategies panel shows 6 rows with colored dots (green=alive, orange=dormant, gray=never/dropped). Launch button labels match state ("Inject" / "Resume" / "Start").
- Deliverables panel shows 4 rows (COMMIT, FILE, DRAFT, LIST).
- Context library panel shows "No attachments yet — Phase 4 wires this up."
- Recent sessions shows 4 items.

Try an unknown slug: `http://localhost:8090/morning/goals/does-not-exist`. Error banner should say "Goal not found: does-not-exist".

Stop:
```bash
pkill -f "python.*server.py" || true
```

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add static/morning/goal-detail.html static/morning/goal-detail.js static/morning/morning.css
git commit -m "feat(morning): add goal-detail page rendered from /api/morning/goals/<slug>"
```

---

## Task 9: Add "Morning" nav link to the existing kanban page

**Files:**
- Modify: `static/index.html`

The existing kanban page (`static/index.html`) doesn't currently link to `/morning`. Add a small link in its nav / header so the two pages can navigate to each other.

- [ ] **Step 1: Find the kanban nav element**

```bash
cd /Users/amirfish/dev/claude-command-center
grep -n 'class="nav"\|<header\|<nav' static/index.html | head -10
```

Note the first result — typically a `<nav>` or a `<div class="topbar">`-style container near the top of the `<body>`.

- [ ] **Step 2: Insert a Morning link**

Open `static/index.html` and find the top-of-page nav region identified in Step 1. Insert a link to `/morning` as the first child of that nav element. Use the same markup style as other links in that nav (the exact element type will depend on what you find).

If there is no existing nav element in `static/index.html`, add a minimal one immediately after `<body>`:

```html
<div class="ccc-top-nav" style="position:fixed; top:8px; right:12px; z-index:1000; font-size:12px;">
  <a href="/morning" style="color:#5ac8fa; text-decoration:none; background:#23272e; padding:6px 10px; border-radius:4px;">→ Morning</a>
</div>
```

- [ ] **Step 3: Manual verification**

```bash
cd /Users/amirfish/dev/claude-command-center
./run.sh &
sleep 2
```

Open `http://localhost:8090/` (kanban). Verify the Morning link is visible and clicking it goes to `/morning`.

From `/morning`, click "Kanban" in the nav bar, verify you land back on `/`.

Stop:
```bash
pkill -f "python.*server.py" || true
```

- [ ] **Step 4: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add static/index.html
git commit -m "feat(morning): add nav link to /morning from kanban page"
```

---

## Task 10: README — document Phase 1 and run instructions

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Morning view (Phase 1)" section**

Add a new section to `README.md` immediately after the existing "Features" section. Exact content to insert:

```markdown
## Morning view (Phase 1 — skeleton)

A second page at `/morning` that will become the single morning landing spot
for goals, strategic priorities, today's tactical queue, and an inbox of
LLM-extracted captures from free-form sources.

Phase 1 ships the UI shell with sample hardcoded data — no ingestion, no
session launching, no Notion migration yet. Those land in Phase 2+.

- Landing page: `http://localhost:8090/morning`
- Goal detail: `http://localhost:8090/morning/goals/bym-growth`
- JSON: `http://localhost:8090/api/morning/state`, `/api/morning/goals/<slug>`

Design spec: [`docs/superpowers/specs/2026-04-17-morning-view-design.md`](docs/superpowers/specs/2026-04-17-morning-view-design.md)
```

- [ ] **Step 2: Commit**

```bash
cd /Users/amirfish/dev/claude-command-center
git add README.md
git commit -m "docs: document morning view Phase 1 in README"
```

---

## Task 11: Final verification

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/amirfish/dev/claude-command-center
python3 -m unittest discover -s tests -v
```

Expected: all tests pass (at least 7 tests across 3 classes).

- [ ] **Step 2: Run the server and click through both pages**

```bash
cd /Users/amirfish/dev/claude-command-center
./run.sh &
sleep 2
```

Manual checklist in the browser:

1. Open `http://localhost:8090/` — see kanban with "Morning" link in nav.
2. Click the Morning link → lands on `/morning` with 4 goal cards, 4 strategic rows, 5 tactical rows, 4 inbox candidates.
3. Click "BYM growth" card → lands on `/morning/goals/bym-growth` with strategies, deliverables, and recent sessions sections populated.
4. Click "Kanban" in the morning nav → lands back on `/`.
5. Open `http://localhost:8090/morning/goals/does-not-exist` → shows error banner.
6. Click "Scan now" on the morning page → the last-refreshed timestamp updates.

Stop:
```bash
pkill -f "python.*server.py" || true
```

- [ ] **Step 3: Confirm Phase 1 scope is complete**

Cross-reference against the file structure section at the top of this plan. All seven new files should exist; `server.py` and `static/index.html` should have the modifications described; `README.md` should have the new section.

```bash
cd /Users/amirfish/dev/claude-command-center
ls -1 morning.py tests/__init__.py tests/test_morning.py \
  static/morning/index.html static/morning/goal-detail.html \
  static/morning/morning.js static/morning/goal-detail.js static/morning/morning.css
```

Expected: all 9 files exist (no "No such file" errors).

---

## Not in this plan (Phase 2+)

Explicitly deferred — do **not** build these in Phase 1:

- Reading real `goal.md` files from `~/.claude/log-viewer/morning/goals/`.
- Any ingestion (TODO.md, PARKING_LOT.md, updates log, GitHub, Notion, Gmail, free-form sources).
- Launch button wiring to `resume_session_headless` / `inject_input_via_keystroke` / `spawn_session`.
- Promote / dismiss actions on inbox items.
- Notion migration script.
- Ribbon auto-computation from git + updates log.
- Context library attachments.
- Transcript-derived deliverables.

Each of these is a Phase 2+ task that will ship on its own, reusing the UI shell from this plan.
