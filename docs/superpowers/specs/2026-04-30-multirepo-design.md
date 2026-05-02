# Multi-repo design

**Status:** draft, pre-implementation
**Branch:** `feat/multirepo`
**Worktree:** `~/Apps/claude-command-center-wt-multirepo-real`
**Predecessor:** `feat/multirepo-rail` — first attempt; misframed as cross-repo. Parked, not merged. Plan docs in that branch are useful context only.

## Goal

CCC today is a single-repo app with a switcher: one server, one `REPO_ROOT`, `/api/repo/switch` reassigns globals. To see another repo's state you must switch.

The target is genuinely multi-repo: every known repo has its data continuously available; the UI shows one or many repos at once; "switching" stops being a server reconfiguration.

## Non-goals

- Cross-server *actions* (e.g. clicking "merge PR" in repo A causing work in repo B).
- Cross-server search.
- Per-server auth — trust model stays loopback-only.
- Auto-spawn / auto-shutdown of CCC servers based on user actions. Starting a server is still a deliberate `cd repo && python3 server.py`.
- Removing `/api/repo/switch` endpoint. Stays as a deprecated no-op-when-already-pointing-there shim for tooling that depends on it.

## Architecture: peer registry (option 3b)

Each repo runs its own `python3 server.py`, on its own port. `REPO_ROOT` is fixed per server. Servers self-register in `~/.claude/command-center/registry.json`:

```json
[
  {
    "repo_path": "/Users/amirfish/Apps/foo",
    "label": "foo",
    "port": 49873,
    "pid": 88421,
    "started_at": "2026-04-30T13:21:00-07:00",
    "version": "0.x.y"
  }
]
```

- **Write on startup**, after the HTTP server is bound. Append to the array (or replace own entry if already present by `repo_path`).
- **Remove on graceful shutdown** (SIGTERM / SIGINT handler).
- **Stale entries** are pruned by *readers* via `os.kill(pid, 0)`-style alive check. No background reaper needed.

Every CCC's UI is symmetric: it reads the registry, fetches `/api/sessions` etc. from each peer's port, aggregates in the browser. No privileged primary server. A dead repo's CCC just disappears from the registry; the rest works.

### Why 3b over 3a / 2

- (3a — one privileged primary): re-introduces a single point of control, contradicts "all repos first-class."
- (2 — dedicated aggregator process): adds a SPOF and a new operational concern. CCC is meant to be runnable with one Python invocation.
- (3b): no new processes, failure-isolated, fits the existing single-file-stdlib ethos.

## Port allocation

**Random free port + registry.** Each server picks a free port at startup (`socket.bind(("127.0.0.1", 0))`-style), records it in the registry. The registry is the source of truth — no need for deterministic per-repo ports.

Existing `port.txt` becomes vestigial. New code reads the registry; legacy code that reads `port.txt` keeps working — last-writer-wins points to *some* server, which is good-enough fallback for tooling that doesn't yet know about the registry.

The existing `CCC_BIND_HOST` / `CCC_BIND_PORT` env-var overrides keep working: a user who pins a port gets that port. Two servers explicitly pinning the same port is the user's problem.

## Same-origin / CSRF model

The browser UI on server A (port 49873) makes XHR fetches to server B (port 49874). That's cross-origin from the browser's perspective. Today's `_check_same_origin()` would reject the POSTs.

**Decision: loosen `_check_same_origin()` to accept any `127.0.0.1:*` / `localhost:*` / `[::1]:*` Origin.**

Rationale:
- The trust model already assumes loopback. Anything reachable on loopback is code already running on the user's machine.
- A malicious external site cannot set a loopback Origin header; browsers set it from the page's actual URL.
- Risk is unchanged: a local rogue process could already access localhost:* directly via curl, no Origin header needed.

`SECURITY.md` gets an explicit update: "loopback-to-loopback fetches across CCC servers are allowed; the loopback assumption is the trust boundary."

The `CCC_ALLOWED_ORIGIN` env var (for trusted-network access via Tailscale/VPN) is unaffected.

## API surface

### New
- `GET /api/registry` — returns the live peer list, with stale entries pruned by `pid`-alive check. Each server serves its own copy of the registry; readers can ask any server.
- `GET /api/identity` — returns this server's `{repo_path, label, port, pid}`. Tiny endpoint used during peer discovery to verify a port still belongs to the expected repo (registry entries can grow stale between writes and reads).

### Unchanged
- `/api/sessions`, `/api/board`, `/api/conversations`, `/api/attention`, etc. — each server answers for its own `REPO_ROOT`. The UI calls them on each peer's port.
- All existing per-session endpoints (spawn, inject, archive, etc.).

### Deprecated (still works)
- `POST /api/repo/switch` — kept as a no-op when the target is already this server's `REPO_ROOT`; otherwise returns a 410 Gone with a hint to start a CCC server in the target directory. Eventually removed in a future major.
- `GET /api/repo/list` — kept; returns the legacy single-server view (this server's `REPO_ROOT` + the home-scan list). UI migrates to `/api/registry`.

### Removed (none in v1)

## UI changes

The browser UI is the place real "multi-repo" appears.

- Page bootstraps by fetching `/api/registry` on whatever server it landed on.
- For each peer in the registry, fetch `/api/sessions`, `/api/board`, etc., from `http://127.0.0.1:<peer.port>/...`.
- Aggregation happens in the browser. No new server-side aggregation code.
- The user can choose **focus mode** (one repo at a time — looks like today's UI) or **all-repos mode** (cards from all repos in one board, repo-tagged). Focus mode is the v1 default; all-repos mode can ship in a follow-up.

This means each CCC's `static/index.html` becomes the multi-repo UI by default. Whichever server you opened the browser against, you see them all.

The existing topbar repo picker is replaced by a peer-list selector. "Switch repo" semantically becomes "focus on this peer's data" — no server reconfiguration involved.

## Migration

Today's user has one server. After upgrading:

1. They run `git pull` and restart their CCC. The new server, on startup, writes its registry entry, picks a (possibly random) free port, continues serving its `REPO_ROOT` like before.
2. Single-server UX is unchanged: the browser shows one repo because the registry has one entry.
3. To go multi-repo: `cd /other/repo && python3 server.py` (or `./run.sh` from that repo's clone). The second server starts, registers itself, picks its own port. Both servers appear in the registry. The browser UI on either auto-discovers the other and shows both.

Stopping a server (Ctrl-C) removes its registry entry. Force-killed servers leave a stale entry; readers prune via `pid`-alive check.

This means there's no "upgrade button" or "configure multi-repo" UX. Multi-repo is an emergent property of running multiple servers.

## Risks / open questions to decide during implementation

- **Browser ergonomics: which port do you bookmark?** If servers pick random ports, the user can't bookmark `localhost:8090` reliably. Mitigation: keep one server on a deterministic port (env or config) as the "front door" for bookmarks; that server's UI still shows everything via the registry. Accept that this server has to be running for the bookmark to work.
- **Hot reload of the registry.** When a new server starts, existing browsers should pick it up. Polling `/api/registry` every 10s in the UI is good enough for v1.
- **Per-peer fetch failures.** If peer B is alive in the registry but its port is unresponsive (firewall, partial crash), the UI must surface that without breaking the page. Treat each per-peer fetch as best-effort with a per-peer error state.
- **Hooks pipeline.** Hooks today write to `~/.claude/command-center/live-state/<sid>.json` keyed by session, with `cwd` indicating the repo. This already works for multi-repo with no changes. Verify during implementation that the hook scripts don't reference `port.txt` or `last-repo.txt` in any load-bearing way.
- **Spawned headless `claude -p` subprocesses.** Currently each server tracks its own spawn list. Cross-server visibility of a spawn (e.g. server A spawns a session whose cwd is in server B's repo) needs a decision: does server A keep ownership, or does server B "claim" it? Default: spawning server keeps ownership. Cross-repo spawns are an existing edge case (`7958281 Add cwd support to spawn API`).

## Phasing (high-level — not the implementation plan)

1. **Phase 1: Registry + identity.** Add `~/.claude/command-center/registry.json` self-registration on startup, `/api/registry`, `/api/identity`. Existing single-server UX unchanged. Tested with one server, then with two manually.
2. **Phase 2: Loosen same-origin to loopback-wildcard.** Update `SECURITY.md`. Verify no regressions.
3. **Phase 3: Browser-side peer discovery.** UI fetches registry, fetches per-peer data, displays focus mode (one peer at a time, peer-selector replaces existing repo picker).
4. **Phase 4: All-repos mode.** Aggregated board view, repo-tagged cards.
5. **Phase 5: Deprecate `/api/repo/switch` + retire vestigial state files.** New major version bump.

Phases 1–3 are v1 of multi-repo. Phase 4 is the "see all tasks across repos" view the user described as a follow-up. Phase 5 is post-v1 cleanup.

## Out of scope explicitly

- Aggregator process (option 2). Settled.
- Privileged primary server (option 3a). Settled.
- Per-repo deterministic ports. Random + registry, settled.
- Browser-side auth / per-peer credentials. Loopback trust holds.
- Cross-machine multi-repo. CCC remains single-machine.
- Any change to the hooks pipeline. Already multi-repo-compatible.

## Decisions to lock before implementation starts

All locked above. The spec stands as the agreement.

## Reading list (for future-me / a reviewer)

- `server.py:38–143` — existing repo-list helpers (`_load_recent_repos`, `_load_custom_repos`, `_load_persisted_repo`).
- `server.py:1228–1267` — `switch_repo_root()`, the function this design retires.
- `server.py:1355` — `SIDECAR_STATE_DIR`. Confirms hooks pipeline is already global / multi-repo-ready.
- `server.py:8536` — `_check_same_origin()`, where the loopback-wildcard loosening lands.
- `SECURITY.md` — full security posture; this design changes the same-origin section.
- Parked branch `feat/multirepo-rail` — reference for the wrong-direction attempt.
