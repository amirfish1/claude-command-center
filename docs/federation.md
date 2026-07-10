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
  stale**, never silently.
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
completed external mutations (each executor checks whether the end state
already holds).

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
