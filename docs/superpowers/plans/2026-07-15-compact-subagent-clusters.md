# Compact Subagent Clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse subagent-heavy session trees into parent summaries while keeping active descendants as compact rows and completed descendants as clickable chips.

**Architecture:** Keep the existing flat session API and cycle-safe tree builders. Add one presentation helper inside the sidebar renderer that classifies each already-built cluster, renders a persisted parent disclosure, and is reused by the non-search Active and All paths.

**Tech Stack:** Browser JavaScript, CSS, Python `unittest`, Puppeteer 25.

## Global Constraints

- Parent clusters are collapsed by default and never auto-expand.
- Active descendants remain compact rows; completed descendants become chips.
- Completed ancestors of active descendants remain bridge rows.
- Search and Trash remain flat.
- Preserve parent lane inheritance, project grouping, pin ordering, and cycle/orphan fallbacks.

---

### Task 1: Cluster presentation and interaction

**Files:**
- Modify: `static/app.js`
- Modify: `static/app.css`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: existing depth-first cluster rows `{card, depth}` and `_renderRow()`.
- Produces: `_renderSubagentCluster(cluster, opts) -> string`, persisted expanded parent IDs, active bridge rows, and completed chip buttons.

- [ ] **Step 1: Write a failing static contract test**

Require the subagent expanded-state key, cluster classification helper, parent disclosure markup, compact active row option, completed chip strip, and delegated toggle/chip handlers.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_smoke.TestServerImports.test_subagent_clusters_collapse_active_rows_and_chip_completed_children
```

Expected: FAIL because the compact cluster renderer does not exist.

- [ ] **Step 3: Implement the minimal presentation helper**

Add local-storage helpers near the other persisted row disclosures. Extend
`_renderRow()` with additive cluster-summary and compact-child options. Render
active/bridge descendants as rows and completed descendants as chip buttons.
Reuse the helper in both non-search tree render paths.

- [ ] **Step 4: Add delegated interactions**

Toggle only the selected cluster body and persist its parent ID. A completed
chip calls `selectConversation(sessionId)` without also opening the parent.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Step 2 command plus `node --check static/app.js`.

- [ ] **Step 6: Commit the implementation slice**

```bash
git commit --only static/app.js static/app.css tests/test_smoke.py -m "feat(ui): compact completed subagent clusters"
```

### Task 2: User-visible note and runtime verification

**Files:**
- Create: `changelog.d/added-compact-subagent-clusters-2026-07-15.md`
- Verify: `static/app.js`, `static/app.css`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: the committed cluster renderer.
- Produces: changelog coverage and visual evidence against real Codex hierarchy data.

- [ ] **Step 1: Add the changelog snippet**

```markdown
- Subagent-heavy sessions now start collapsed, keep active children as compact rows, and collect completed children into clickable chips.
```

- [ ] **Step 2: Run full verification**

```bash
python3 -m unittest tests.test_smoke -v
node --check static/app.js
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Verify with Puppeteer**

Use `node snapshot.js` or an equivalent Puppeteer 25 script against the running
CCC server. Assert collapsed-by-default state, expand the reported Codex parent,
confirm compact active rows and completed chips, click a chip, and save a
screenshot for visual inspection.

- [ ] **Step 4: Commit the changelog**

```bash
git commit --only changelog.d/added-compact-subagent-clusters-2026-07-15.md -m "docs(changelog): note compact subagent clusters"
```

