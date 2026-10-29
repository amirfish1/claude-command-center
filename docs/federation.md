# Federated CCC fleet

Run CCC on two (or more) machines and drive them as one control plane: see
every repository's real state everywhere, hand a conversation to another
machine, orchestrate sessions across machines, and clean up a whole fleet
from one reviewed plan.

## Principles

- **GitHub transports code; CCC transports session state and commands.**
  A handoff never copies repository files — the source pushes, the
  destination fetches. Dirty trees block the handoff (with the likely owning
  session identified) instead of being silently swept along.
- **Observe before acting.** Scans are read-only (plus an explicit
  `fetch=1` refresh of remote refs). Mutations happen only through a
  reviewed action plan you confirm once.
- **Separate state dimensions.** A clean tree, a pushed commit, a merged
  PR, and a green production deploy are different facts. The Fleet view
  shows each with its own observation time; a failed deploy of a pushed
  commit recommends deployment investigation, never another push.
- **Loopback stays loopback.** No listener is opened beyond CCC's default
  `127.0.0.1` bind. Peer traffic reaches the remote CCC *on its own
  loopback* — over SSH for remote machines, or direct loopback for a second
  CCC on the same machine. Pairing establishes a shared secret; every
  peer-facing call except the identity card validates it.

## Identities

| Thing | Identity | Where |
|---|---|---|
| Node (one CCC install) | opaque `node_id` (uuid) + display name | `~/.claude/command-center/node.json` |
| Peer registry | paired nodes + transports + secrets | `~/.claude/command-center/peers.json` |
| Repository | canonical `host/owner/repo` from the origin URL (local-only fallback: `local:<name>:<root-commit-12>`) | derived; per-node path mapping in `~/.claude/command-center/federation/repo-map.json` |
| Session | global reference `<node_id>:<session_id>`; bare ids stay local | leases in `~/.claude/command-center/federation/leases/` |

## Setup and pairing

1. Run CCC on both machines (any port; defaults are fine).
2. On machine A: **Settings → Nodes & peers… → Add peer**.
   - Same machine, second instance: transport *Loopback*, the other
     server's port.
   - Remote machine: transport *SSH*, `user@host` (key-based SSH must
     already work; CCC executes a small HTTP client on the remote host that
     talks to the remote CCC's loopback port).
3. Pairing exchanges the node identity cards and a shared secret in both
   directions. Test-connection, rename, repo-path mapping, and removal all
   live in the same modal.
4. Map repository identities to local clone paths per node (auto-mapped for
   repos CCC already knows; explicit mapping in the modal otherwise).

## Continue on another machine

Conversation menu → **Continue on…** → pick a destination node.

The preflight shows the exact plan: dirty files (blocker, with the likely
owning sessions), unpublished commits (will be pushed), the destination
prepare step (fetch + fast-forward, or an isolated worktree when the clone
is busy — never a reset), the transcript transfer with path rewriting, and
the ownership flip. One confirmation executes it. The transcript bundle is
hash-verified, staged, and activated atomically; a failed import leaves the
destination untouched. After the handoff the destination is self-sufficient
— the source machine can go offline.

Ownership leases prevent both nodes from resuming the same conversation
(inject/ask on the stale copy returns `not_owner` with the owner's id).
Handing back re-verifies checksums: if the stale copy changed since the
handoff, the return is blocked as divergent instead of overwritten.
`POST /api/federation/handoff/takeover` is the audited recovery path for a
lost node.

## Cross-machine orchestration

Anywhere a `session_id` is accepted, a global reference
`<node_id>:<session_id>` transparently proxies to the owning CCC:

- `POST /api/sessions/spawn` with `"node": "<peer name or id>"` runs the
  spawn there — your local `repo_path` is translated to the stable repo
  identity and re-resolved against the target node's own mapping.
  `report_to` is globalized so the child's completion report crosses back.
- `POST /api/inject-input` / `POST /api/ask` with a global ref route to the
  owner and relay the result.
- `GET /api/sessions?federated=1` returns one list across all nodes with
  per-node health; unreachable peers serve their last known rows **labeled
  stale**, never silently. Each peer entry in `nodes` also carries `web_url`
  (its browser-reachable address, from its Tailscale Serve entry) and
  `web_url_state` (`ok`, `none`, `pin`, or `unknown`); `peers_only=1` skips
  the local rows.
- **Sidebar**: peer sessions appear under "Other machines" in the Coding tab
  with a machine chip. Clicking one embeds that peer's own CCC
  (`<web_url>/?ccc_popout=conversation&conv=<id>`) over the main pane, so the
  peer needs a web address (Settings > Phone access, or a `tailscale serve`
  root that proxies to CCC). A peer with no address, or one with a Phone
  access PIN (its cookie cannot ride a cross-site frame), is listed but not
  openable.
- Group chats carry a `host_node`. Remote participants are nudged with the
  chat's uuid + host reference and read/post through their own CCC, which
  proxies to the host. If the host is unreachable, reads fail truthfully
  with `peer_offline`.

Typed failures everywhere: `peer_offline`, `timeout`, `unpaired_peer`,
`stale_mapping`, `unsupported_capability`, `routing_loop`, `not_owner`.

## Fleet view

**Fleet** in the navigation shows a repo × node matrix. Per node and repo:
worktrees (dirty counts, unpublished commits, head SHA, merged-ness proof),
the fetched view of origin's default branch, open PRs (checks, mergeability,
review state — with an explicit error channel when `gh` fails), production
deployment state, and the sessions associated with each checkout. Every
dimension carries `observed_at`; dead peers show stale badges.

**Resolve all…** builds a reviewed plan: push unpublished commits, pull
lagging clones (ff-only), mark ready / merge PRs whose objective gates pass
(green checks, mergeable, review satisfied — every unmet gate is listed),
ping owning sessions to commit or finish, and remove worktrees only when
clean **and** provably reachable from origin's default branch (branch
deletion stays a separate action). Deselect anything, confirm once. The job
persists under `~/.claude/command-center/fleet-jobs/` with step logs;
every step revalidates preconditions immediately before mutating, so stale
plans stop safely, and a CCC restart can resume the job without repeating
completed external mutations. Each confirmed step has a stable identity scoped
to its coordinator, plan, and action. The owning node reserves a receipt before
dispatch and saves the outcome under `fleet-jobs/receipts/`. A retry returns
that outcome with `replayed: true`, even after both nodes restart or the generic
peer request cache is evicted. These receipts also protect local plan steps.

If a step is still running, or execution stopped before the outcome could be
saved, a retry returns `execution_uncertain` and does not dispatch again.
Inspect the target before creating and confirming a new plan. Known failed
outcomes also replay; use a new plan after resolving their blockers. Changing
the intent of an existing step returns `idempotency_conflict`. Receipts are
retained without automatic expiry so old plans cannot repeat effects after a
cache timeout. Standalone `/api/fleet/step` calls without plan identity retain
their existing behavior; the executor still checks Git/PR end state before
mutating. Restart all participating CCC dashboard servers after upgrading to
enable receipt protection on every node.
Protection starts when an upgraded owner first dispatches a step; it cannot
reconstruct outcomes of steps executed before the upgrade.

## Memory across machines

`ccc recall`, `ccc shipped`, `ccc brief` and `ccc history` ask every paired
peer by default (`--scope local` for this machine only). Each machine indexes
only its own disks; nothing is copied between them. The requesting node runs
its own search and, at the same time, sends each peer the read-only
`memory_recall` / `memory_shipped` / `memory_brief` / `memory_file_history`
route action. A peer always answers from its own index, so fan-out is one hop
and never recursive.

- **Deadline.** 1.5 s per peer. A peer that misses it shows as
  `[hermes timed out]`; its call keeps running in the background, so a cold
  SSH connect still warms the link and fills the cache for the next query.
- **Circuit breaker.** A peer that is offline or whose transport times out is
  skipped for 60 s, doubling to 10 min (`skipped_backoff`). A late success
  closes it.
- **Last-answer cache.** 10 min, used only when a live call fails, and always
  labelled stale with its age.
- **Merge.** Per-node ranked lists are combined with RRF (k=60, equal node
  weight), ties broken by recency, then deduped by native session id. The
  copy owned by the handoff lease wins (else the most recently active one)
  and lists the others in `also_on`. If both copies were active after the
  last handoff, the session forked: both rows stay, labelled `fork_of`.
- **Shipped honesty.** With a peer unreachable the verdict is
  `NOT FOUND on reachable nodes (...; hermes unreachable since 14:02)`,
  never `NOT SHIPPED`.

The HTTP API (`/api/memory/*?scope=all`) defaults to `scope=local`, so a
caller that resumes by bare `session_id` never gets a peer's session. Every
merged row carries `node_id`, `node_name`, `ref` and `local`; the response
adds `nodes[]` (per-node status and latency), `peers_unreachable` and
`partial` (brief: `node_status`, since its `nodes` key is the lineage map).
The pre-spawn shipped check never fans out.

## Attribution

"Who owns this dirty file?" follows an evidence hierarchy — hook write
events (persisted in a per-node provenance index), worktree/session
ownership, transcript tool paths, then timestamp correlation. All plausible
candidates are returned with confidence and evidence; no evidence means an
honest `unknown`, never a fabricated owner. "Ping to commit / finish"
routes to the owning session through the federation layer, resuming dormant
sessions where needed.

## Testing

`tests/two_node_harness.py` boots two fully isolated CCC processes
(separate `HOME`s, loopback transport, temp bare git origin + per-node
clones) — the same peer protocol production uses over SSH. The
`tests/test_*_two_node.py` suites cover pairing, routing, handoff (including
divergence and source-death), fleet inventory, the executor's safety gates,
restart/resume, and the security boundary. `tests/fake_claude.py` is a
stream-json stand-in for the Claude CLI so spawn/inject/ask/resume run end
to end without an agent.
