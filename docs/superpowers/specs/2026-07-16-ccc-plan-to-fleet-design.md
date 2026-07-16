# CCC plan-to-fleet: drop a document, get a running fleet (design)

Status: implemented (W51). Owner-story: a human writes a plan document, CCC
turns it into a supervised Watchtower queue, and (optionally) a working fleet.

## Goal

From the CCC dashboard, take a plan / spec / mission-brief markdown file and
turn it into Watchtower queue tickets:

    document (.md) -> preview extracted tickets -> confirm -> queue -> (optional) drain with N workers

The extraction engine is Watchtower's `wt import` (W43, `feat/doc-to-queue`),
which sends the whole document to one reasoning-model call and returns
high-confidence tickets. CCC owns only the pipeline: pick a file, preview, file,
and optionally spawn workers.

## Non-goals

- CCC does not re-implement extraction. All reasoning lives in `wt import`.
- No Kanban. The preview is a flat list, matching the existing queue panel.
- CCC never hard-depends on Watchtower. When `wt import` is unavailable the
  affordance is hidden, exactly like every other optional-tool integration.

## Watchtower contract (W43, `wt import`)

    wt import FILE -q QUEUE [--apply] [--type bug|feature]

- Preview is the default. `--apply` is the only mutation switch.
- Exactly one reasoning call per invocation. Preview and apply each run once.
- Dry-run stdout is line-oriented:
  - `WOULD FILE: [feature] Short title (L12-L24)` — a new ticket
  - `EXISTS: [bug] Short title (L5)` — already filed (idempotent import key)
  - `IMPORT dry-run: candidates=N new=N existing=N; pass --apply to file`
- Apply stdout:
  - `FILED: <ref>  <title>`
  - `IMPORT applied: candidates=N created=N existing=N`
- Bodies are not printed in dry-run, so the CCC preview shows status, type,
  title, and source anchor only. That is enough to decide whether to file.

## API

### `POST /api/queue/import-doc`

Body: `{ "path": "<doc path>", "queue": "<QUEUE>", "apply": false, "type": "bug"|"feature"|null }`

- Same-origin enforced by `do_POST`'s top-level `_check_same_origin` (every CCC
  POST). No per-route allow-list; loopback-only trust model.
- Path validation (`_resolve_import_doc_path`): resolve symlinks strictly,
  require an existing regular file with a text/markdown extension
  (`.md .markdown .mdx .txt .text`). This mirrors `_safe_local_file_open_path`
  (the Files-panel clamp): only real files the user can already reach, and only
  plain-text documents — never a script or binary. Repo-containment is NOT
  required because plan docs commonly live outside any repo (a Desktop brief).
- Queue name validation: `[A-Za-z0-9_-]{1,64}`.
- Shells to `wt import` via `subprocess.run([...], capture_output=True,
  text=True, timeout=...)`, mirroring `_try_wt_send_for_headless_delivery`.
  Dry-run uses a 180s timeout (one LLM call); apply also 180s.
- Returns `{ ok, available, applied, tickets: [{status,type,title,source_ref,ref?}],
  counts: {candidates,new,existing,created?}, stdout_tail, error? }`.

### `GET /api/queue/import-doc`

Availability probe for the UI feature-flag: `{ ok, available }` where
`available = _wt_import_available()`. O(1): one cached `wt import --help`
subprocess per process.

### Degradation

`_wt_import_available()` is cached (one `wt import --help` per process). It is
False when `wt` is not on PATH OR the installed `wt` predates the `import`
subcommand (the shipped `wt` 0.1.0 does not have it yet — W43 in flight). When
False:

- `GET /api/queue/import-doc` returns `available: false`.
- The UI hides the Import-doc button entirely (no dead control).
- A direct `POST` returns `{ ok: false, available: false }` with a clear
  message, so scripted callers also degrade cleanly.

## UI

A minimal affordance in the existing queue panel header (`#queuePanel`), next
to the ticket `+` add button:

- `Import doc` button (`#queueImportDoc`), shown only when the availability
  probe reports `available: true`.
- Click opens a small modal (`upd-overlay` / `upd-dialog` chrome, same as the
  ticket composer and queue manager) with a document-path field, a queue-name
  field (pre-filled from the panel's current scope), and a `Preview` action.
- Preview calls the dry-run endpoint and renders a flat list of would-file /
  exists rows (status chip, type, title, source anchor) plus the counts line.
- `File N tickets` confirms (apply) and repaints the queue panel.
- After filing, an optional `Drain with N workers` control appears, gated
  behind an explicit click. It reuses the existing spawn plumbing
  (`/api/ux-fixes/spawn-worker`) — never auto-spawns.

No new dependencies, single-file `static/index.html` + `static/app.js`
conventions, list-view aesthetics.

## Performance

Both routes are O(1): they touch only the requested file and one `wt`
subprocess. No scan of `~/.claude/projects` or all sessions/conversations. A
call-count perf-budget test asserts the endpoint never calls
`find_all_conversations` / `_q.list_items`.

## Security posture (SECURITY.md)

- Same-origin on every POST (inherited).
- Path clamp to an existing text/markdown regular file the user can already
  reach; symlinks resolved; no directory traversal past a real file.
- `subprocess.run` with an argv list (never a shell string), so the queue name
  and path cannot inject shell. Queue name also charset-validated.
- The doc contents are piped only to the locally authenticated Claude CLI that
  `wt import` already uses; CCC adds no new network egress.
