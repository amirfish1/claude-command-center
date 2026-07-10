# Fable implementation brief: federated CCC fleet

> **Fable: read this entire file carefully before planning or changing code.**
> This is an implementation assignment, not a request for ideas only. Audit the
> existing implementation, write a staged plan, then implement and verify the
> complete outcome. All four capability areas below are required. Staging is an
> execution order, not permission to drop later stages.

## Goal

Turn Claude Command Center (CCC) from a single-machine session dashboard into a
safe federated control plane for multiple CCC machines and multiple Git
repositories.

From one CCC, the user must be able to:

1. see the real Git, worktree, GitHub PR, deployment, and agent-session state of
   every configured repository on every paired machine;
2. continue a conversation on another machine without copying repository files;
3. spawn, inject, ask, and run group chats across machines as naturally as on
   one machine; and
4. review one generated action plan, confirm it once, and let CCC safely finish,
   publish, merge, deploy, or clean up the selected work.

The system should start with two machines but use stable identities and APIs
that naturally support more nodes.

## Product principles

- **GitHub transports code; CCC transports session state and commands.** Never
  copy a repository or dirty source files between machines. Commit and push on
  the source machine, then fetch/pull or create the corresponding worktree on
  the destination.
- **Observe before acting.** Scans are read-only apart from an explicit refresh
  fetching remote refs. Mutations happen only through a visible action plan.
- **One review, one confirmation.** “Resolve all” prepares an ordered plan with
  reasons, risks, blockers, and target machines. The user may remove actions and
  then confirms the remaining plan once.
- **Separate state dimensions.** A clean working tree, a pushed commit, a merged
  PR, and a successful production deployment are different facts. Never collapse
  them into one “done” flag.
- **Evidence over guesses.** Session attribution and cleanup recommendations
  include evidence and confidence. Unknown stays unknown.
- **Safe failure and resumption.** Every action is idempotent where possible,
  logged, attributable to a node, and safe to retry after CCC restarts or a peer
  disconnects.
- **General public feature.** Do not hardcode personal repository names, machine
  names, usernames, home directories, private hosts, or one-user paths.

## Existing foundation to reuse

Do not build a parallel product before inspecting what CCC already has. Reuse
and generalize these capabilities:

- repository selection and explicit `repo_path` validation;
- `ssh_multiplexer.py`, remote session discovery, and remote spawning;
- `/api/sessions/spawn`, `/api/inject-input`, `/api/ask`, and the spawned-session
  registry;
- group-chat create/add/read/post/nudge behavior and stable chat UUIDs;
- effective session CWD/worktree inference from transcript tool paths;
- `/api/repo/worktrees`, GitHub PR enrichment, draft/check/mergeability data,
  and orphan PR detection;
- the repo “Push all” pipeline and its persisted, resumable job log;
- Vercel project detection and production deployment status by commit SHA;
- installed hooks and sidecars that already record session activity and writes;
- the current multi-engine session model and capability detection.

Preserve stable `/api/*` contracts. Add fields and versioned endpoints rather
than breaking existing response shapes.

## Required capability 1: CCC federation

Implement a peer/node layer that lets one CCC aggregate and route work to other
CCC instances.

### Node and repository identity

- Give every CCC installation a stable opaque `node_id`, a user-editable display
  name, a capability manifest, version, last-seen time, and connection health.
- Introduce a peer registry with explicit pairing/removal and per-peer transport
  configuration. Secrets and machine-local paths belong under CCC’s local state,
  never in the repository.
- Identify a repository across machines by canonical Git remote identity
  (provider host plus owner/repository), not by an absolute path. Support a
  local-only fallback identity where no remote exists.
- Store a per-node mapping from stable repository identity to that node’s local
  clone path. This mapping is the basis for home-directory and folder-name
  normalization.
- Address sessions globally as a node plus native session ID. Do not assume a
  UUID is globally routable without its owning node.

### Transport and security

- Preserve CCC’s loopback-only default and current threat model. Do not expose
  the unauthenticated command API to a LAN or the internet, add wildcard CORS,
  or weaken same-origin checks.
- Prefer the existing authenticated SSH connection/multiplexer as the first peer
  transport, calling the remote CCC on its own loopback interface. A future
  direct HTTPS/Tailscale transport may be supported only with explicit pairing,
  authentication, and equivalent authorization.
- Add a small versioned peer protocol for health, capabilities, inventory,
  routing, and session import/export. Validate every requested repository and
  file path on the machine that owns it.
- Make unavailable peers explicit. Cached state must be labeled stale with its
  observation time; it must never look live.
- Prevent routing loops with request IDs, hop limits, and idempotency keys.

### Federation UI

- Add node health and location badges wherever a session, repo clone, or
  worktree is shown.
- Provide peer setup, test-connection, rename, repository-path mapping, and
  removal controls without requiring users to hand-edit configuration files.

## Required capability 2: “Continue on another machine”

Implement a first-class session handoff flow. “Continue on VM” is the primary
two-node use case, but the UI and data model should say “Continue on…” and work
between any paired nodes.

### Handoff behavior

1. The user chooses a destination node from a conversation toolbar action.
2. CCC runs a preflight and shows the exact plan:
   - identify the stable repository and destination path;
   - detect dirty files, unpublished commits, branch/upstream state, and active
     source processes;
   - ensure required code is committed and pushed on the source;
   - fetch and check out/pull the same commit or branch on the destination,
     creating an isolated destination worktree when appropriate;
   - export the conversation and only the CCC/agent metadata required to resume;
   - rewrite source-machine CWD/project-folder references to the destination
     repository mapping;
   - atomically import and verify the session on the destination; and
   - resume it there with the same conversation identity and visible history.
3. If preflight is safe, one confirmation executes the plan. If work is dirty,
   CCC may ask the attributed session to commit and wait, but it must not invent
   a commit or silently copy dirty files.
4. After successful handoff, the source marks the conversation as owned by the
   destination. The destination can continue after the source CCC and source
   machine are offline.

### Session bundle and path normalization

- Define a versioned session-transfer manifest with source/destination node,
  engine, session ID, repository identity, source/destination CWD, branch and
  commit, included metadata, byte counts, hashes, and timestamps.
- Transfer the native transcript plus the minimum sidecars needed for titles,
  model choice, hierarchy, provenance, and resumption. Do not transfer repository
  contents, environment files, credentials, caches, or arbitrary files merely
  referenced by the transcript.
- Rewrite both the destination storage folder and path-bearing transcript
  metadata when the native engine requires it. Keep an audit record of every
  rewrite. Warn about attachments or absolute paths that cannot exist on the
  destination.
- Import through a staging path, validate the manifest and hashes, then rename
  atomically. A failed import must leave the existing destination session intact.
- Claude Code handoff must work end to end. For another engine whose native
  store cannot yet be migrated safely, expose a truthful capability error rather
  than claiming success; cross-machine orchestration for that engine should
  still work through its owning CCC.

### Ownership, split brain, and return handoff

- Add a session-ownership/lease record so two nodes do not independently resume
  the same transferred session. Require the source process to stop or explicit
  takeover before activating the destination.
- Provide an explicit, audited force-takeover recovery path for a lost node.
- Support handing the updated conversation back in the reverse direction. Use
  checksums/checkpoints and the ownership record to avoid overwriting divergent
  histories.
- The completed handoff must not depend on the source node staying online.

## Required capability 3: cross-machine orchestration

Make the current CCC orchestration surface location-aware without forcing the
caller to manually SSH or construct remote URLs.

- A unified session list returns owning node, stable global reference, engine,
  repo identity, mapped path, liveness, and stale/offline state.
- Spawn accepts a target node or a placement policy. The result preserves node
  identity in `spawn_id`, `session_id`, `parent_session_id`, and return routing.
- Inject and ask accept global session references and transparently proxy to the
  owning CCC. Existing local session-ID calls remain compatible.
- Completion reports and `report_to` work across machines, including a remote
  child reporting to a local parent or the reverse.
- Update the installed `ccc-orchestration` skill so agents can discover and use
  global session references without writing SSH commands themselves.
- Make failures distinguishable: peer offline, target session unavailable,
  timeout while work continues, authorization failure, stale mapping, and
  unsupported capability.

### Cross-machine group chats

- A group chat may contain participants owned by different CCC nodes.
- Store stable global participant references and route each nudge/injection
  through that participant’s owner.
- Give every chat an explicit host node. Reads, posts, sidecar updates, and
  watcher behavior proxy to the host rather than assuming a local file path.
- Support moving chat ownership when needed; do not let an opaque machine-local
  path become the chat’s identity.
- The existing local-only group-chat flow must continue to work unchanged.

## Required capability 4: repository fleet operations

Add a Fleet view that answers, at a glance and on refresh, what exists, what is
unpublished, what is under review, what is deployed, what is forgotten, who
worked on it, and what CCC recommends doing next.

### Inventory and independent state dimensions

For every configured repository, aggregate across every node:

- clone and worktree paths, current branches, detached/locked state, dirty files,
  staged/untracked counts, and ahead/behind information;
- unpublished commits on each node/branch, meaning commits not reachable from
  the configured upstream/remote refs;
- origin default-branch SHA and each clone’s fetched view of it;
- open/draft PRs, checks, review state, mergeability, merge state, head SHA, and
  branches/PRs with no worktree on any node;
- production deployment state as a separate dimension, tied to provider,
  environment, commit SHA, URL, time, and error details;
- sessions associated with each dirty clone, worktree, branch, commit, or PR;
  and
- freshness and errors for every contributing source.

The default view should emphasize primary repositories but support a configured
set of adjacent repositories without hardcoding either category.

### Recommendations

Build deterministic recommendations with reasons and blockers, including:

- ask the likely owning session to commit;
- push unpublished commits;
- pull/fetch another node after a remote push;
- open a PR, keep it draft, mark it ready, or merge it;
- investigate failed checks or deployment independently of Git state;
- remove a clean merged worktree;
- finish an unmerged worktree by waking its owning session or spawning a focused
  finishing session; and
- flag ambiguous or risky work for human review.

“Mark ready” and “merge” must require objective evidence such as a clean tree,
published head, mergeability, and required checks. Explain every unmet gate.

### Reviewed execution plan

- “Resolve all” creates a dependency-ordered plan grouped by repository and
  machine. Show command-level intent, external effects, destructive steps,
  blockers, and rollback/retry behavior.
- The user can deselect actions, then confirm the remaining plan once.
- Run the plan as a persisted job with step logs and resumable state. Revalidate
  preconditions immediately before every mutation so stale plans stop safely.
- Never force-push. Never merge a PR with failing required checks. Never delete
  a dirty or unproven worktree.
- A worktree is auto-removable only when it is clean and its head is provably
  reachable from the intended merged/default branch or its PR is confirmed
  merged. Branch deletion is a separate explicit plan action.
- If a worktree is not safely removable, preserve it and offer a finish path.

### Session attribution and “ping the owner”

- Add a per-node provenance index under CCC local state. Update it from existing
  hooks/sidecars whenever a session edits/writes a path, changes effective CWD,
  creates a worktree, or produces a commit-related event.
- Backfill best-effort evidence from existing transcripts and spawn/session
  registries without blocking normal dashboard use.
- Attribute a dirty path or unpublished commit using an evidence hierarchy:
  explicit hook write event, worktree/session ownership, transcript tool path,
  then timestamp correlation. Return all plausible sessions when work is shared.
- Show confidence, last event time, owning node, and evidence. Never fabricate a
  single owner when evidence is ambiguous.
- Provide “Ping session” and “Ask to finish/commit” actions. They must route to
  the correct machine through the federation layer and work for dormant sessions
  using CCC’s existing resume behavior.

## Suggested execution order

This ordering reduces rework; it does **not** reduce required scope.

1. **Audit and contracts:** map current remote/session/worktree/ship/deploy/chat
   code, write the additive peer, identity, transfer, inventory, and job schemas,
   and add contract tests.
2. **Federation foundation:** node identity, peer registry, SSH-backed peer
   transport, capability/health discovery, repository mapping, and node-aware
   session references.
3. **Session handoff:** export/import manifest, path normalization, Git preflight,
   ownership lease, Continue-on UI, remote resume, and reverse handoff.
4. **Orchestration routing:** federated list/spawn/inject/ask/report routing,
   skill update, then cross-machine group-chat hosting and participants.
5. **Fleet inventory:** multi-node Git/worktree/PR/deploy/provenance collection
   with freshness, caching, partial failure, and the Fleet UI.
6. **Recommendations and executor:** deterministic rules, reviewed action plan,
   persisted multi-node jobs, cleanup safety, and session ping/finish actions.
7. **Hardening:** two-node end-to-end tests, restart/offline cases, security and
   path tests, performance budgets, docs, changelog snippet, and visual QA.

Work in coherent, reviewable increments and small conventional commits. Do not
push unless the user explicitly asks. If the full program cannot fit in one
turn, leave a durable checked plan and continue from it; do not redefine later
stages as optional follow-up work.

## Required end-to-end acceptance scenarios

The work is not complete until these scenarios have evidence:

1. **Fleet truth:** two CCC nodes and several repositories appear in one Fleet
   view. Dirty, unpublished, PR, merge, and deployment states are independently
   correct and carry observation times.
2. **Remote unpublished commit:** a commit made only on the second node is shown
   as unpublished. The reviewed plan pushes it, then offers or performs the
   appropriate fetch/pull on the first node without copying files directly.
3. **Continue while source is off:** a conversation associated with a clean,
   pushed commit is handed to the second node, paths are normalized, it resumes
   with history intact, the first CCC is stopped, and the conversation continues
   successfully on the second node.
4. **Dirty handoff guard:** handoff with dirty source code does not copy the
   changes or claim success. It identifies/pings the likely owning session and
   proceeds only after the commit is published or the user resolves the blocker.
5. **Cross-machine orchestration:** a session on node A spawns or addresses a
   session on node B, injects text, receives an ask response, and receives a
   completion report through global references without caller-authored SSH.
6. **Cross-machine group chat:** participants on both nodes receive nudges, read
   and post to one stable chat, and show truthful failure state if the chat host
   becomes unreachable.
7. **Safe merged cleanup:** a clean, provably merged worktree is removed only
   after appearing in and confirming the action plan.
8. **Unmerged preservation:** an unmerged or dirty worktree is never deleted;
   CCC identifies the likely session and offers a routed finish action.
9. **Deployment separation:** a pushed/merged commit with a failed Vercel
   production deployment shows “Git complete, deployment failed” and recommends
   deployment investigation rather than another push.
10. **Attribution honesty:** a dirty file with strong evidence links to its
    session and node; ambiguous shared edits list multiple candidates; missing
    evidence is labeled unknown.
11. **Restart and retry:** interrupt a multi-node plan, restart CCC, and resume or
    safely retry without repeating an already completed external mutation.
12. **Security boundary:** the feature works without changing the default
    loopback bind, exposing an unauthenticated remote command API, accepting an
    unpaired peer, or allowing an imported bundle/path to escape approved roots.

## Verification requirements

- Add focused unit/contract tests for identities, path mappings, capability
  negotiation, global session routing, transfer manifests, provenance scoring,
  recommendation gates, and persisted job resumption.
- Add a two-node integration harness using separate temporary homes, two local
  CCC processes, two clones/worktrees, and a temporary bare Git origin. Exercise
  handoff and cross-node routing without touching real user data.
- Use real temporary Git repositories for Git behavior. Do not turn the smoke
  test into a mock of `gh`, Vercel, or agent CLIs.
- Test peer-offline, stale-cache, timeout, duplicate request, failed import,
  divergent session history, dirty worktree, failed checks, failed deployment,
  and server-restart paths.
- Run the repository’s relevant automated tests and `tests/test_smoke.py`.
- Verify UI changes with the repository’s Puppeteer harness (`node snapshot.js`),
  not Playwright or the in-app-browser backend.
- Report the exact commands and results. Do not claim success from inspection
  alone.

## Repository constraints

- Read and obey `AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, and the focused rules
  for any touched area before editing.
- Keep `server.py` standard-library only and preserve the single-file static app
  architecture unless the project rules themselves change.
- Use list-form subprocess arguments; never use `shell=True` or interpolate
  untrusted values into commands.
- Validate all node-supplied repository, worktree, transcript, import, and chat
  paths on the owning node.
- Do not weaken current origin, CORS, binding, or path protections.
- Keep the feature generic and public-safe. Fixtures must use obvious fake
  hosts, repositories, tokens, paths, and session IDs.
- Add a `changelog.d/` entry for user-visible functionality; do not edit
  `CHANGELOG.md` directly.

## Expected final output from Fable

Fable’s completion report should contain:

1. **Outcome:** a short statement of what a user can now do.
2. **Delivered by capability:** concrete implementation status for federation,
   session handoff, cross-machine orchestration/group chat, and fleet operations.
3. **Architecture:** stable identities, peer transport/security, ownership,
   transfer, inventory, provenance, and job-execution decisions.
4. **User flow:** exact setup, pairing, Continue-on, Fleet refresh, Resolve-all,
   and cleanup steps.
5. **Files and commits:** important files changed and conventional commit IDs.
6. **Verification evidence:** tests, two-node scenarios, UI snapshot, and their
   results.
7. **Known limitations:** only truthful residual limitations that do not negate
   the twelve required acceptance scenarios.

Do not stop with a proposal or architecture document. The requested output is a
working, verified implementation of all four capability areas, delivered in
safe stages and summarized in the format above.
