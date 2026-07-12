# Session Cost Orbit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace process-liveness icon styling with a unified Claude/Codex cost orbit and a separate actively-working indicator across sidebar and pane-header session icons.

**Architecture:** Add pure classification helpers and a shared icon renderer inside the existing browser application, keeping `static/app.js` as the single behavior source. The helpers derive engine, unified cost tier, and active-work state from existing session fields; `static/app.css` renders the orbit geometry and status dot without API changes or new runtime dependencies.

**Tech Stack:** Browser JavaScript, CSS, Python `unittest`/`pytest` static-and-runtime tests, Node syntax validation, Puppeteer snapshot harness.

## Global Constraints

- Preserve the existing 13px engine glyphs and engine hues.
- Premium = FABLE/SOL, High = OPUS, Medium = SONNET/TERRA, Low = HAIKU/LUNA.
- Cost must remain legible when a session is not working.
- A green dot means actively executing a turn, never merely process-attached.
- Do not change `/api/*` response shapes or add runtime dependencies.
- Do not change row height or sidebar width.
- Do not animate filters, orbit geometry, or the whole engine glyph.
- Respect `prefers-reduced-motion`.

---

### Task 1: Pure Cost and Activity Classification

**Files:**
- Create: `tests/test_session_cost_orbit_static.py`
- Modify: `static/app.js` near the session-row rendering helpers

**Interfaces:**
- Produces: `sessionIconEngine(row) -> string`
- Produces: `sessionCostTier(engine, model) -> "premium" | "high" | "medium" | "low" | ""`
- Produces: `sessionIsActivelyWorking(row, optimistic) -> boolean`
- Produces: `sessionIconPresentation(row, optimistic) -> {engine, engineLabel, tier, tierLabel, working, activityLabel, title}`

- [ ] **Step 1: Write failing classification tests**

Create a test that extracts the pure helper block from `static/app.js`, evaluates it with Node, and asserts the actual production functions against a JSON case table. Cover versioned and short identifiers, engine-scoped rejection, missing/unknown models, canonical Claude state, canonical Codex state, pending spawn, optimistic send, waiting, idle, stuck, and ended states.

```python
def test_session_icon_classifiers_execute_expected_matrix():
    cases = [
        ["claude", "claude-fable-5", "premium"],
        ["claude", "opus-4-8", "high"],
        ["claude", "sonnet-5", "medium"],
        ["claude", "haiku-4-5", "low"],
        ["codex", "gpt-5.6-sol", "premium"],
        ["codex", "gpt-5.6-terra", "medium"],
        ["codex", "gpt-5.6-luna", "low"],
        ["codex", "claude-opus-4-8", ""],
        ["claude", "gpt-5.6-sol", ""],
        ["codex", "gpt-5.5", ""],
    ]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `./.venv/bin/pytest -q tests/test_session_cost_orbit_static.py`

Expected: failure because `sessionCostTier` and `sessionIsActivelyWorking` do not exist.

- [ ] **Step 3: Implement the pure helpers**

Add a marker-delimited helper block to `static/app.js`. Normalize engine and model with lowercase strings. Match only approved families for the matching engine. Treat `pending_spawn` or the passed optimistic flag as working; otherwise use `codex_state === "working"` for Codex and `state === "working"` for Claude. Build a human-readable title containing engine, exact model when available, tier/unknown, and Working now/Not working.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `./.venv/bin/pytest -q tests/test_session_cost_orbit_static.py`

Expected: all classifier tests pass.

- [ ] **Step 5: Commit the classifier slice**

```bash
git commit --only static/app.js tests/test_session_cost_orbit_static.py \
  -m "feat(ui): classify session icon cost and activity"
```

### Task 2: Shared Icon Rendering and Cost-Orbit CSS

**Files:**
- Modify: `tests/test_session_cost_orbit_static.py`
- Modify: `tests/test_smoke.py` existing sidebar-icon animation assertion
- Modify: `static/app.js` session row and `paneEngineIconHtml`
- Modify: `static/app.css` session-icon and pane-header icon rules

**Interfaces:**
- Consumes: Task 1 presentation helpers
- Produces: `sessionEngineIconHtml(row, options) -> string`
- Produces CSS classes: `.cost-premium`, `.cost-high`, `.cost-medium`, `.cost-low`, `.is-working`, `.is-not-working`

- [ ] **Step 1: Write failing rendering and CSS tests**

Assert that sidebar and pane-header rendering both call `sessionEngineIconHtml`, the legacy `is-fable5` and `ccc-icon-pulse` paths are absent, all four orbit classes exist, the premium `$` annotation exists, the activity dot has solid/hollow states, and reduced-motion disables only the status-dot pulse. Update the old smoke assertion so it rejects whole-icon scaling instead of requiring it.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `./.venv/bin/pytest -q tests/test_session_cost_orbit_static.py tests/test_smoke.py -k 'session_cost_orbit or sidebar_left_model_icon'`

Expected: failure because the shared renderer and orbit CSS are absent and the legacy pulse remains.

- [ ] **Step 3: Implement shared rendering**

Create `sessionEngineIconHtml(row, options)` that chooses the existing engine SVG through `getEngineSvg`, applies the presentation classes, inserts `<span class="session-activity-dot">`, adds the premium `$` annotation, and emits the semantic tooltip/ARIA label. Replace the sidebar's duplicated engine SVG branch and pane header's FABLE-only branch with this renderer. Non-session issue/backlog/PR icons keep their existing paths.

- [ ] **Step 4: Implement orbit and activity styling**

Replace `.is-live`/`.is-dead`, `.is-fable5`, and `@keyframes ccc-icon-pulse` with stationary pseudo-element orbits:

- Low: dotted inner gray orbit.
- Medium: broken blue orbit.
- High: complete amber orbit and one satellite.
- Premium: double gold orbit, restrained stationary glow, and `$`.
- Working: solid green pulsing dot.
- Not working: hollow gray dot.

Keep the engine glyph hue for every tier, soften non-working glyph opacity without grayscaling the orbit, and disable the dot pulse under reduced motion.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `./.venv/bin/pytest -q tests/test_session_cost_orbit_static.py tests/test_smoke.py -k 'session_cost_orbit or sidebar_left_model_icon'`

Expected: all selected tests pass.

- [ ] **Step 6: Run syntax validation**

Run: `node --check static/app.js`

Expected: exit 0 with no output.

- [ ] **Step 7: Commit the visual slice**

```bash
git commit --only static/app.js static/app.css tests/test_session_cost_orbit_static.py tests/test_smoke.py \
  -m "feat(ui): show unified cost orbits on sessions"
```

### Task 3: Visual QA, Changelog, and Full Verification

**Files:**
- Create: `changelog.d/added-session-cost-orbits-2026-07-12.md`
- Modify only if verification finds a defect: `static/app.js`, `static/app.css`, `tests/test_session_cost_orbit_static.py`

**Interfaces:**
- Consumes: completed session cost-orbit UI
- Produces: verified `snapshot.png` and a user-visible changelog entry

- [ ] **Step 1: Add the changelog snippet**

```markdown
- Session icons now preserve engine identity while showing a unified Claude/Codex cost orbit and a separate actively-working indicator.
```

- [ ] **Step 2: Start CCC for browser verification**

Run the repository server on `127.0.0.1:8090` using the existing `run.sh` flow, preserving any already-running instance.

- [ ] **Step 3: Capture the supported browser snapshot**

Run: `node snapshot.js`

Expected: exit 0 and a newly written `snapshot.png` showing the dashboard.

- [ ] **Step 4: Inspect the rendered image**

Open `snapshot.png` and verify crisp orbit geometry, no title collision, unchanged row height, balanced premium emphasis, visible idle tiers, and no bright vertical stripe.

- [ ] **Step 5: Run the focused feature suite**

Run: `./.venv/bin/pytest -q tests/test_session_cost_orbit_static.py`

Expected: all tests pass.

- [ ] **Step 6: Run full syntax and smoke verification**

Run: `node --check static/app.js && ./.venv/bin/pytest -q tests/test_smoke.py`

Expected: exit 0 and zero failures.

- [ ] **Step 7: Run diff hygiene checks**

Run: `git diff --check -- static/app.js static/app.css tests/test_session_cost_orbit_static.py tests/test_smoke.py changelog.d/added-session-cost-orbits-2026-07-12.md`

Expected: exit 0 with no output.

- [ ] **Step 8: Commit the completed user-visible slice**

```bash
git commit --only static/app.js static/app.css tests/test_session_cost_orbit_static.py tests/test_smoke.py changelog.d/added-session-cost-orbits-2026-07-12.md \
  -m "feat(ui): finish session cost orbit redesign"
```

- [ ] **Step 9: Review the final range against the design**

Compare the implementation commits with `docs/superpowers/specs/2026-07-12-session-cost-orbit-design.md`. Confirm every acceptance criterion has direct code, test, and rendered evidence before declaring completion.
