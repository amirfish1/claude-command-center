# Pipeline Canvas — n8n-grade authoring UX over the WatchTower kernel

2026-09-15. A new CCC surface: a node-graph canvas where the nodes are real
agent-fleet primitives (queues, planners, reviewers, human gates, stream
filers) and the edges are ticket-filing conventions, not data pipes.

The positioning, in one paragraph: n8n proved the physics of this UX — pan,
zoom, drag, connect — but its nodes are deterministic integrations and its
edges are data pipes. The pipeline canvas keeps the *interaction physics*
and swaps the kernel: every node is a real WatchTower queue or fleet role
with live health, and every edge is a filing convention ("planner X files
build tickets to executor Y, under label L"). Drawing the graph is designing
a fleet; looking at the graph is reading the fleet's truth.

## Canvas model

Two node populations on one infinite canvas:

- **Runtime nodes** — every queue in WatchTower's `queue-config.json`,
  auto-placed, rendering live truth: engine/model/effort, auto_drain,
  desired vs live workers, open/claimable/in-progress counts, stuck and
  staffing alarms. A queue with `auto_drain: false` shows as a deliberate
  parking lot, not a fire — the canvas never flatters.
- **Designed nodes** — archetype instances the user drags from the
  components box. They are *design intent*: dashed border, a
  "not materialized" chip, and config fields the user can sketch (engine,
  model, desired workers). They touch nothing real.

Plus one always-present **human-gate node** (the Decision Inbox), the sink
for blocked work and design sign-offs.

## Archetype contract

Five first-class archetypes, each anchored in a pattern the fleet already
runs:

| Archetype | Anchor pattern | Truth source |
|---|---|---|
| Planner | design-only worker queue; deliverable is a spec; ends at a human gate | queue whose name carries the design suffix convention |
| Executor | claim-based drain queue: engine/model/effort per queue, reconcile loop | ordinary `queue-config.json` entry |
| Visual reviewer | independent lane that drives a real browser and verdicts VERIFIED / WRONG-STATE with a screenshot | queue named for verify/review |
| Human gate | option cards parked for the human | the Decision Inbox (`ccc_server/decision_inbox.py`) |
| Stream filer | scheduled producer turning signals into deduped tickets | external schedulers; designed-only in v1 (no config truth to read) |

Classification of runtime queues is a *presentation hint* derived from name
conventions (`X-DESIGN` → planner, verify/review names → reviewer, else
executor). The config file stays the only truth; the hint never writes back.

## Data sources (single source of truth)

- **Queue definitions**: `_wt_read_config()` — `queue-config.json`, read
  directly, no watchtower import (the established CCC pattern).
- **Live health**: `compute_queues_health()` (ccc_server/queue_events.py) —
  the same per-queue rollup that feeds the dashboard's Evergreen section:
  depth, claimable, in_progress, workers, effective/orphan counts, state
  (stuck/draining/backlog), staffing_alarm, worker_plan (effective
  engine/model/effort with source), repo_path, github_repo.
- **View state — the ONLY canvas-owned state**: node positions, viewport,
  designed nodes, user-drawn edges, persisted at
  `~/.claude/command-center/canvas-layout.json` (same directory and
  atomic-write pattern as `decision-inbox.json`). No parallel document of
  queue facts exists anywhere; a canvas node renders config + health or it
  does not render.

## API

- `GET /api/canvas/state` → `{ok, generated_at, nodes, edges}`.
  `nodes` merges config entries with health rows (a configured-but-idle
  queue still renders; a queue with tickets but no config renders as
  unconfigured). The human gate travels as a node with `kind: "gate"`
  (always present, carrying `open_cards` when cheaply available) — one
  uniform node list instead of a special top-level shape. `edges` are the
  derived conventions. Cache-friendly: composed from the already-memoized
  health payload; a poll never spawns, never shells out per queue.
- `GET /api/canvas/layout` → the validated view-state document (defaults
  when absent/corrupt).
- `POST /api/canvas/layout` → validate + atomic replace. Validated hard:
  node/edge caps, coordinate clamps, zoom clamp, unknown keys dropped. This
  is the only write endpoint, it writes only view state, and it gets the
  same `_check_same_origin` every POST gets.

## Edge semantics

- **Derived (runtime)**: planner convention — `X-DESIGN` files build
  tickets to `X` (edge label carries the filing contract, e.g. the
  watchtower label); gate convention — queues with `product_gate` edge to
  the human gate; planner sign-off — planner nodes edge to the human gate
  (the design queue's terminal state is a human decision).
- **User-drawn (design)**: stored in the layout document, rendered
  distinctly (accent dashed), tooltip shows the intended filing contract.
- Edges are directional bezier curves with an animated flow pulse; they
  declare *who files to whom*, never data flow.

## Templates

The template gallery ships ≥3 presets — "Feature factory" (planner →
executor → visual reviewer → human gate), "PostHog watchdog" (stream filer
→ executor → human gate), "Quality loop" (stream filer → executor →
planner escalation → human gate). One click lays the graph out as designed
nodes. **Preview materialization** renders the exact `queue-config.json`
entries and `wt` commands that would create the queues — as a read-only
preview. There is no apply in v1.

## Component taxonomy (phase 2)

The library is **data-driven from a registry**
(`static/canvas-components.js`, UMD so node tests can require it). Adding
a component is a one-line entry — library cards, node chrome, inspector
fields, port rules, and edge contracts all render from the data:

```
{ id, name, cat, letter, desc,           // identity + presentation
  anchor, pattern,                        // the proven real implementation
                                          // ("Pattern:" in the inspector)
  files: {what, label?}, consumes,        // edge contract out / in
  config: [{key, label, ph?, type?}],     // inspector sketch fields
  ports?: {inp, out} }                    // optional override of the
                                          // category default
```

Five categories, color-coded consistently across library, nodes, edges,
and minimap: **sources** (scheduled/event producers — PostHog watcher,
auditor sweep, ops digest, email parsers, webhooks, annotate widget,
scheduler), **workers** (claim-based consumers — planner, deep-design,
quick fixer, product builder, visual verifier, code reviewer, replay
simulator, DB investigator, docs writer, TDD worker), **gates** (human or
adversarial review — Decision Inbox, product_gate, wt block, senior
reviewer, closure verifier), **sinks** (outputs — GitHub issue, email
digest, SMS notify, status page, commit/PR, deploy), **utilities**
(inline plumbing — deduplicator, budget guard, rate limiter, queue
memory). Port rules are categorical (sources emit, sinks/gates terminate,
workers/utilities flow through) with per-component overrides for gates
that file (senior reviewer, closure verifier, product_gate).

Templates (`static/canvas-templates.js`) are graphs over registry ids —
nodes reference components by id, edges carry contract labels; the
registry test (`tests/canvas-component-registry.test.cjs`) validates every
template (components exist, edges are port-legal, every node reachable
from a root, terminal node or an intentional loop). Gallery: Feature
factory, PostHog watchdog, The quality loop, Payment watchdog, PR
gauntlet, Self-healing ops (intentional verifier→fixer loop), Research →
Spec → Build.

Runtime queue nodes keep their name-derived archetype hint, mapped onto
the same category palette (`ARCHETYPE_CATEGORY`) and onto registry
components for the inspector's Pattern section (`ARCHETYPE_COMPONENT`).

The canvas still owns nothing but the view document. Designed-node
component/category and generic config-sketch keys persist through
`canvas-layout.json` (server-validated: capped keys/values, typed
coercion only for `desired_workers`/`auto_drain`).

## What v1 explicitly does NOT do

- **No mutations.** No ticket ops, no queue-config writes, no `wt`
  mutations, no reconcile nudges. The daemon reads that config live; it is
  production. Materialization is a preview, period.
- **No per-node engine/model editing.** Engine/model/effort is shown as
  truth (with its source: queue pin vs default). A per-node picker
  (including upcoming engine options) is a v2 knob that will write through
  the existing `/api/queue/config` path, not around it.
- **No stream-filer runtime nodes.** Scheduled producers live outside
  queue-config (launchd, cron, external repos); v1 renders them as
  designed archetypes only. Auto-discovering them is v2.
- **No execution on the canvas.** Claiming, closing, answering, spawning —
  the Queues board (q2) already owns those. The canvas links out; it does
  not re-implement.

## Frontend

`static/canvas.html` + `canvas.css` + `canvas.js`, self-contained like q2
(no app.js/app.css, palette mirrored from it), reachable from the
Applications rail. Pan (background drag + space-drag), zoom-to-cursor
(wheel/pinch), fit-to-view, grid-snap dragging, dot grid, bezier edges with
direction pulse, hover states, minimap, keyboard shortcuts (Delete, ⌘Z/⇧⌘Z,
F, +/-), an empty state that teaches, a node inspector with the full truth
per queue, 30s health refresh, entrance animation with
`prefers-reduced-motion` respected.
