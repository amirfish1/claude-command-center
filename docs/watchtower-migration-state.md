# WatchTower migration state

Status: audit + gap-closing refactor, 2026-07-17. Companion to
`ccc-watchtower-boundary.md` in the WatchTower repo (the canonical ownership
contract: WT owns durable queue/worker/delivery semantics; CCC owns desktop UI
surfaces and local transports).

## Question

Is WatchTower (`wt`) fully integrated as the core infrastructure driving CCC's
queue/fleet features — and has the migration of CCC's own queue/fleet/worker
logic onto it been executed?

## Verdict

**Substantially executed, not total — and total was never the design.** The
ticket lifecycle, document import, worker dispatch, and delivery receipts all
run on WatchTower code today when the package is present. CCC deliberately
keeps a stdlib-only fallback layer so the dashboard still works on installs
without WatchTower; that layer is a feature, not unfinished migration. The
audit found four places where CCC still re-implemented WT-owned behavior even
when WT was available; three were closed by this change, one is documented
below as an accepted follow-up.

## What drives CCC's queues today

| Domain | Engine when WT is installed | Fallback without WT |
|---|---|---|
| Ticket lifecycle (claim/close/edit/answer/comment/reopen) | `watchtower.queue` (in-process import, `_q` indirection; "WT-32 Phase 2") | `ux_fixes_queue.py` (CCC's stdlib engine, same store shape) |
| Doc → tickets (plan-to-fleet import) | `wt import` CLI subprocess; availability probed via `wt import --help` | feature hidden (`available: false`) |
| Queue config writes (`/api/queue/config`, `/drain`, claim-types) | `watchtower.config` setters — same code path as `wt config` / `wt drain` *(closed by this change; previously direct file writes)* | direct atomic write of `queue-config.json` |
| Worker dispatch after enqueue / run-once | `watchtower.workers.dispatch_after_enqueue` / `spawn_run_once_worker` | no dispatch (tickets wait) |
| Dashboard "drain with N workers" spawn | `watchtower.workers.spawn_workers` — WT-tracked, in `workers.json` + WT logs *(closed by this change; previously untracked CCC shadow sessions)* | CCC `spawn_session()` worker polling CCC's HTTP API |
| Delivery receipts | `wt receipts get` subprocess (transcript-verified) | n/a |
| Message send/ask | CCC-native transports (AppleScript/FIFO/resume) by default; `wt send`/`wt ask` behind `CCC_MESSAGING_BACKEND=wt` | CCC-native |
| Health strip / queue analytics | read-only reads of `~/.watchtower` JSON + `activity.log`, plus `_q.list_items()` | same reads; `ux_fixes_queue` store |

## What this change closed (2026-07-17)

1. **Queue config writes now go through WatchTower.** `/api/queue/config` and
   `/api/queue/drain` previously always rewrote `queue-config.json` directly,
   even when `watchtower.config` was importable (only claim-types delegated).
   Both now prefer the WT setters — the exact functions `wt config -q` and
   `wt drain on/off` call — so semantics like "drain on restores
   `desired_workers >= 1`" hold identically from the UI and the CLI. The
   direct-write branch remains as the no-WT fallback and for renaming legacy
   case-mismatched keys (WT has no delete/rename API).
2. **Fleet spawns are WT-tracked.** `/api/ux-fixes/spawn-worker` (the
   plan-to-fleet "drain with N workers" button) previously spawned CCC-owned
   sessions that polled CCC's HTTP API — invisible to `wt workers`, the
   reconciler, and WT's release/reap logic, so a queue could carry shadow
   workers WT knew nothing about. It now delegates to
   `watchtower.workers.spawn_workers()` (worker record, WT log file,
   reconciler ownership). The CCC-native spawn remains for installs without WT
   and for callers passing a custom name/model/extra instructions.
3. **CCC's reapers stand down for WT workers.** Both the automatic idle
   reaper and the System Health reap treated a WT queue worker as just another
   idle `claude` process and could SIGTERM it, bypassing WT's own
   release/reap policy. Both paths now consult live `workers.json` records
   (`_wt_live_worker_guard()`) and never target them; System Health rows carry
   a `wt_worker` flag.

## Feature-by-feature (per the audit scope)

- **W51 plan-to-fleet UI** — extraction fully delegated to `wt import` (dry-run
  preview + apply); CCC parses the CLI's line output for display only. The
  fleet half now spawns WT-tracked workers (item 2 above).
- **W43 doc-to-queue** — lives in WatchTower (`document_import.py`, `wt
  import`), merged to WT main on 2026-07-17. CCC is purely a consumer. Note:
  the `wt import --help` availability probe is cached for the CCC process
  lifetime — restart CCC after upgrading `wt`.
- **W55** — no commit, doc, or code in either repo references W55; treated as
  a mis-cited number (the doc-to-queue work is W43/W63).
- **W22 queue replay** — replay events are derived on the fly from ticket
  timestamps out of the shared WT store via `_q.list_items()`; no parallel
  event store exists. wt-backed by construction.
- **W39 fleet skills** — documentation artifacts (CCC dispatch pattern + WT
  triage skill); nothing to migrate.
- **Health strip** — read-only presentation over WT's on-disk state (config,
  workers, activity log). Reads, never writes — consistent with the boundary
  contract's "CCC owns UI surfaces" clause. The coupling is to file schemas,
  not logic.

## What remains CCC-side, and why

- **`ux_fixes_queue.py` (the full fallback ticket engine).** Kept so CCC works
  standalone (public OSS install without WatchTower). It mirrors WT's store
  shape; the known risk is silent drift between the two writers. Follow-up
  worth considering: a shared schema fixture test, or demoting the fallback to
  read-only display.
- **Messaging default (`CCC_MESSAGING_BACKEND` unset → CCC-native).** This is
  contract-compliant, not a gap: the boundary doc assigns live local
  transports to CCC and reserves `wt send`/`wt ask` for durable delivery.
  Flipping the default is a product decision about delivery guarantees, not a
  mechanical migration.
- **`queue-deletions.json` tombstones.** CCC-owned presentation state (stops
  the health strip resurrecting deleted queues) written into `~/.watchtower`.
  Harmless but misplaced; candidates: move under `~/.claude/command-center`,
  or give WT a real queue-delete API and delegate.
- **Queue delete (`/api/queue/delete`).** Still a direct `queue-config.json`
  edit because WT exposes no delete/rename operation. If WT grows one, this
  route should delegate like config/drain now do.
- **Read-only `~/.watchtower` file reads** (health strip, worker lists,
  activity log). Deliberate: `server.py` is stdlib-only and must render state
  even when `import watchtower` fails. These never mutate WT ledgers.

## How to keep the boundary honest

New CCC code that files, claims, closes, configures, spawns, or messages must
go through `_q` / `watchtower.config` / `watchtower.workers` / the `wt` CLI
when available — never write WT's JSON directly except in an explicit
no-WT fallback branch, and never kill a process that `workers.json` says is a
live WT worker.
