# Codex Subagent Hierarchy and Task Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Codex-spawned agents directly beneath their parent sessions in the All view and replace opaque generated codenames with reliable task-oriented labels.

**Architecture:** Keep the `/api/*` response flat and additive. The backend derives a task label from Codex's clear-text `agent_path`, while the frontend applies the same cycle-safe `parent_session_id` tree rules within the All render branch and makes unoverridden children inherit their parent's lane.

**Tech Stack:** Python 3 standard library, SQLite, single-file browser JavaScript/CSS, `unittest`, Puppeteer 25.

## Global Constraints

- Preserve user renames as the highest-priority title source.
- Preserve explicit All-lane overrides even when they separate a child from its parent.
- Do not mutate Codex's SQLite data.
- Keep `/api/*` response compatibility; new fields must be additive.
- Keep `server.py` stdlib-only and `static/app.js` bundler-free.
- Use the repository's Puppeteer harness, not Playwright or the in-app browser.

---

### Task 1: Derive useful Codex subagent task labels

**Files:**
- Modify: `server.py:23045-23090`
- Modify: `server.py:25384-25530`
- Modify: `server.py:38884-38955`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: Codex thread row fields `agent_path`, `source`, `title`, `first_user_message`, and `agent_nickname`.
- Produces: `_codex_agent_task_label(row) -> str`, `_codex_display_name(row, override=None, title="", first_message="") -> str | None`, and additive row field `agent_task_name`.

- [ ] **Step 1: Write failing helper and precedence tests**

Add tests that require:

```python
def test_codex_agent_task_labels_humanize_cleartext_paths(self):
    server = self.server
    self.assertEqual(
        server._codex_agent_task_label({"agent_path": "/root/ccc_588_review"}),
        "CCC-588 review",
    )
    self.assertEqual(
        server._codex_agent_task_label({"agent_path": "/root/trash_fix_review"}),
        "Trash fix review",
    )
    source = json.dumps({
        "subagent": {"thread_spawn": {"agent_path": "/root/api_audit"}},
    })
    self.assertEqual(server._codex_agent_task_label({"source": source}), "Api audit")
    self.assertEqual(server._codex_agent_task_label({"source": "vscode"}), "")

def test_codex_display_name_prefers_task_label_over_generated_nickname(self):
    server = self.server
    row = {"agent_path": "/root/ccc_588_review", "agent_nickname": "Erdos"}
    self.assertEqual(server._codex_display_name(row), "CCC-588 review")
    self.assertEqual(server._codex_display_name(row, first_message="Review pagination"), "Review pagination")
    self.assertEqual(server._codex_display_name(row, title="Queue pagination review"), "Queue pagination review")
    self.assertEqual(server._codex_display_name(row, override="My reviewer"), "My reviewer")
    self.assertEqual(server._codex_display_name({"agent_nickname": "Erdos"}), "Erdos")
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_smoke.SmokeTest.test_codex_agent_task_labels_humanize_cleartext_paths tests.test_smoke.SmokeTest.test_codex_display_name_prefers_task_label_over_generated_nickname -v
```

Expected: both tests fail because `_codex_agent_task_label` and `_codex_display_name` do not exist.

- [ ] **Step 3: Implement the minimal task-label helpers**

Add `agent_path` to `_codex_fetch_threads()`'s optional column list. Add helpers near the Codex row readers that:

```python
def _codex_agent_path(row):
    raw = str((row or {}).get("agent_path") or "").strip()
    if raw:
        return raw
    source = (row or {}).get("source")
    if not isinstance(source, str) or not source.strip().startswith("{"):
        return ""
    try:
        data = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    spawn = ((data.get("subagent") or {}).get("thread_spawn") or {})
    return str(spawn.get("agent_path") or "").strip()


def _codex_agent_task_label(row):
    path = _codex_agent_path(row).rstrip("/")
    leaf = path.rsplit("/", 1)[-1].strip() if path else ""
    if not leaf or leaf.lower() in ("root", "agent", "subagent"):
        return ""
    label = re.sub(r"[_-]+", " ", leaf).strip()
    label = re.sub(
        r"^([A-Za-z][A-Za-z0-9]*)\s+(\d+)(?=\s|$)",
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
        label,
    )
    if label and not re.match(r"^[A-Z]+-\d+", label):
        label = label[:1].upper() + label[1:]
    return _truncate_session_name(label) or ""


def _codex_display_name(row, override=None, title="", first_message=""):
    return (
        _truncate_session_name(override)
        or _truncate_session_name(title)
        or _truncate_session_name(first_message)
        or _codex_agent_task_label(row)
        or _truncate_session_name((row or {}).get("agent_nickname"))
    )
```

Use `_codex_display_name()` in `find_codex_conversations()` and `_group_chat_resolve_session_display_name()`. Add `agent_task_name` to Codex session rows.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command again.

Expected: both tests pass.

- [ ] **Step 5: Verify against the current Codex rows**

Run:

```bash
python3 -c 'import server; rows=server.find_codex_conversations(repo_only=False); print([(r["display_name"], r["parent_session_id"]) for r in rows if r.get("agent_task_name")][:10])'
```

Expected: task labels such as `CCC-588 review` or `Trash fix review` appear instead of generated surnames, with non-empty parent IDs.

---

### Task 2: Nest All-view children and inherit parent lanes

**Files:**
- Modify: `static/app.js:25440-25535`
- Modify: `static/app.js:26180-26430`
- Modify: `static/app.css:4650-4690`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: flat session rows with `session_id`, `parent_session_id`, lifecycle state, lane override, and project metadata.
- Produces: All-view lane arrays where unoverridden children share the parent's lane, plus tree-ordered `{card, depth}` rows rendered with `currentChildDepth`.

- [ ] **Step 1: Write a failing static UI contract test**

Add a test that reads `static/app.js` and `static/app.css` and requires:

```python
def test_all_view_nests_subagents_and_inherits_parent_lane(self):
    app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
    app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
    self.assertIn("const _allTabById = new Map();", app_js)
    self.assertIn("const _allTabLaneFor = (c, seen = new Set()) =>", app_js)
    self.assertIn("const parent = _allTabById.get(_currentSessionParentId(c));", app_js)
    self.assertIn("if (parent) return _allTabLaneFor(parent, seen);", app_js)
    self.assertIn("const _allTabTreeRows = _currentSessionsTreeRows(_allTabMainConvs);", app_js)
    self.assertIn("currentChildDepth: item.depth", app_js)
    self.assertIn(".conv-archived-list .conv-item.is-current-child-row", app_css)
```

- [ ] **Step 2: Run the focused UI test and verify RED**

Run:

```bash
python3 -m unittest tests.test_smoke.SmokeTest.test_all_view_nests_subagents_and_inherits_parent_lane -v
```

Expected: failure because the All lane is flat and has no parent-inheritance map.

- [ ] **Step 3: Add cycle-safe parent lane inheritance**

Build `_allTabById` from `_allTabConvs`. Replace the one-line lane resolver with:

```javascript
const _allTabLaneFor = (c, seen = new Set()) => {
  const override = _allTabLaneOverride(c);
  if (override) return override;
  const id = _currentSessionId(c);
  if (id && seen.has(id)) return _allTabNaturalLane(c);
  if (id) seen.add(id);
  const parent = _allTabById.get(_currentSessionParentId(c));
  if (parent) return _allTabLaneFor(parent, seen);
  return _allTabNaturalLane(c);
};
```

This makes generated Codex children follow WatchTower parents into Workers while preserving explicit overrides.

- [ ] **Step 4: Render All session rows as parent/child clusters**

Build `_allTabTreeRows` with an All-branch cycle-safe tree helper. In
project-grouped mode, assign each complete tree cluster to its root parent's
project and pass `currentChildDepth: item.depth` to `_renderRow()`.

In time-grouped mode, convert `_allTabTreeRows` into clusters: each depth-0 row starts a cluster and following depth>0 rows join it. Sort clusters against group-chat rows using the cluster's newest activity time, render every cluster contiguously, and keep singleton root rows eligible for `_renderRowsWithRepeatGroups()`.

Do not tree-order active search results; they remain relevance ordered and flat.

- [ ] **Step 5: Extend child indentation to the All list**

Combine the existing current-session selectors with All-list selectors:

```css
.conv-current-sessions-scroll .conv-item.is-current-child-row,
.conv-archived-list .conv-item.is-current-child-row {
  --current-child-indent: calc(var(--current-child-depth, 1) * 14px);
  --conv-icon-left: calc(10px + var(--current-child-indent));
  border-left-color: rgba(154, 173, 194, 0.24);
}
```

Apply the same selector pairing to the lighter italic child title rules.

- [ ] **Step 6: Run focused UI tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_smoke.SmokeTest.test_by_objects_current_sessions_nest_parented_sessions tests.test_smoke.SmokeTest.test_all_view_nests_subagents_and_inherits_parent_lane -v
```

Expected: both tests pass.

---

### Task 3: Changelog, verification, and review

**Files:**
- Create: `changelog.d/fixed-codex-subagent-hierarchy-2026-07-15.md`
- Verify: `server.py`, `static/app.js`, `static/app.css`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: completed backend and UI changes.
- Produces: user-visible changelog entry, regression evidence, and a reviewed commit.

- [ ] **Step 1: Add the changelog snippet**

Create:

```markdown
- Codex-spawned agents now stay nested under their parent session in the All view and use task-oriented labels instead of opaque generated codenames when Codex exposes a task path.
```

- [ ] **Step 2: Run syntax and focused checks**

Run:

```bash
python3 -m py_compile server.py
node --check static/app.js
git diff --check
```

Expected: all commands exit 0 with no errors.

- [ ] **Step 3: Run the complete smoke suite**

Run:

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: all tests pass.

- [ ] **Step 4: Verify the rendered hierarchy with Puppeteer**

Open the running dashboard with the All tab and Workers lane selected, then capture it with Puppeteer 25. Wait for the All lane rows, assert a representative task-labeled child follows its parent and has `is-current-child-row`, and save `snapshot.png` for visual inspection.

Expected: the child is directly beneath and indented under the parent; the visible label is task-oriented rather than a generated surname.

- [ ] **Step 5: Review the diff and commit the user-visible slice**

Inspect only the intended files, request a focused code review, address Critical or Important findings, then commit with explicit paths:

```bash
git commit --only server.py static/app.js static/app.css tests/test_smoke.py changelog.d/fixed-codex-subagent-hierarchy-2026-07-15.md -m "fix(titles): group Codex subagents under parents"
```
