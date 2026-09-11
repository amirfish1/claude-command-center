# UX-fixes queue worker brief

Operating manual for a **fixer** session — a session whose standing `/goal` is
to drain one project's slice of the shared UX-fixes queue and keep it empty.

A fixer is not a special CCC mode. It is an ordinary session (Claude or Codex)
running in a project repo, given a `/goal` that points it at this doc. The only
thing that differs per project is **which repo the session runs in** and
**which `PROJECT` code it drains** (`CCC`, `BYMPROD`, `HERMES`, …).

> Spawning a fixer: open a session in the target repo and send it the
> canonical goal at the bottom of this file, with `<PROJECT>` filled in.

## The queue — what it is

- A single durable store for the whole machine (SQLite; a frozen legacy JSON
  file may still exist alongside it, see `watchtower.queue`'s module
  docstring), resolved by `watchtower.queue._resolve_store_path()`
  (override with `$WATCHTOWER_STORE`).
- **Every project lives in that one store**, namespaced by an item's `project`
  field. A `BYMPROD` fixer only ever touches `project == "BYMPROD"`; it never sees or
  edits `CCC` items.
- Annotations route to a project automatically by repo basename / configured
  repo_path (see `_project_for` in `watchtower/queue.py`) — e.g.
  `claude-command-center` → `CCC`. So you do not "create" a queue; annotating
  a project's pages files tickets into it. (Note: as of 2026-08-21, this
  basename routing has no special-case alias table — a repo like
  `bookyourmat` or `BYM+Finie` routes by its literal basename unless its
  `repo_path` is registered in `queue-config.json` or the caller passes an
  explicit `project=`.)

## The tooling lives in the WatchTower repo

CCC's queue engine is the `watchtower` Python package (`watchtower.queue`) —
a **hard dependency**, installed as an editable install
(`github.com/amirfish1/watchtower`), not a module inside the CCC checkout.
Drive it cross-repo by putting the WatchTower repo on `sys.path` (or simply
`import watchtower.queue` if it's already installed, e.g. via the editable
`.pth` finder):

```bash
python3 -c "import watchtower.queue as q; print(q.list_items(status='open', project='<PROJECT>'))"
```

### API (the only functions you need)

`SID` = your own CCC session id (so claims are attributed to you). Prefer your
raw session UUID. If you attribute claims with a human *label* instead, also
pass your real UUID via `session_uuid=` (HTTP) so the queue-health watcher can
still reach you if your project's queue stalls — a label alone is not a
reachable session id.

| Call | Does |
|------|------|
| `q.enqueue(project='<PROJECT>', note=..., text=..., ...)` | File a NEW ticket → returns it (with assigned ref) |
| `q.list_items(status='open', project='<PROJECT>')` | List open tickets for your project |
| `q.claim_next(SID, project='<PROJECT>')` | Atomically claim the oldest open item → returns it (or `None`) |
| `q.update_status(ref, 'in_progress', SID)` | Claim a specific ref (e.g. `'BYM-87'`) |
| `q.close(ref, SID)` | Mark a ticket fixed |

### Filing a ticket (`enqueue`)

A fixer drains; any session can **file**. Pass `project=` explicitly to pick the
lane (`OPS`, `BYM`, `CCC`, …) — an unknown code like `OPS` is accepted as-is and
gets its own `OPS-N` ref series. If you omit `project`, it routes by `repo_path`
basename, then `source`, else `GEN`. `note` (short, ≤4000 chars) or `text`
(detailed, ≤24000) is required; everything else is optional.

```bash
python3 -c "import watchtower.queue as q; \
item = q.enqueue( \
    project='OPS', \
    title='Short headline', \
    note='One-line summary of the problem.', \
    text='Full detail: problem, impact, a concrete instance, suggested fix.', \
    url='https://example.com',           # optional: where it shows up \
    selector='',                          # optional: CSS path to the element \
    screenshot_path='',                   # optional: evidence image \
    repo_path='',                         # optional: routes project if project= omitted \
    lane='normal'); \
print('FILED:', item['ref'], '-', item['title'])"
```

`enqueue` is append-only and file-locked, so it is safe to call from any session
without colliding with a fixer that is draining the same file.

### Ticket shape

Each item carries: `ref` (e.g. `BYM-87`), `note` / `text` (the annotation),
`selector` (CSS path to the element), `url`, `screenshot_path`, `repo_path`.
Open the screenshot and use the selector to locate exactly what the user meant.

## The loop

Never busy-wait. Poll, act, then idle and re-check on a wakeup.

1. **Claim** — `claim_next(SID, project='<PROJECT>')` (or `update_status` a
   specific ref). Nothing open → idle and re-poll later.
2. **Understand** — read `note`/`text`, open `screenshot_path`, resolve
   `selector`.
3. **Fix** — make the change in the project repo. Verify in a browser against
   the project's dev server (file a screenshot/diff as evidence).
4. **Commit** — `git commit --only <paths> -m "type(scope): subject"`.
   Never `git add -A` / `.` / `-a` (shared checkout — see below).
5. **Close** — `q.close(ref, SID)`.
6. **Re-poll**; if empty, idle.

## Git hygiene (shared `main`, parallel sessions)

- Commit with `git commit --only <your-paths>` — the index is shared; plain
  `git commit -m` can sweep in a sibling session's staged work.
- Never `git add -A`, `git add .`, or `git commit -a`.
- Never branch in the shared clone unless asked; use `git worktree add` for
  branch-isolated work.
- **Do not push** unless the user says push/ship.

## Before you touch anything

Read the target project's own `CLAUDE.md` for that repo's house rules:
deploy/CI, test commands, security-sensitive paths. House rules win.

## Canonical `/goal` (paste this to spawn a fixer)

Fill in `<PROJECT>` and the repo path:

```
/goal Drain the <PROJECT>-* UX-fixes queue and keep it empty. Read
docs/ux-fixes-worker-brief.md in the CCC repo
for exactly where the queue is, the watchtower.queue API (list_items /
claim_next / update_status / close, scoped to project='<PROJECT>'), and the
claim → fix → verify → commit --only → close loop. Also read this repo's
CLAUDE.md before touching deploy/CI. Never busy-wait — idle-poll for new
tickets. Don't push unless asked.
```
