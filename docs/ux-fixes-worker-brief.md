# UX-fixes queue worker brief

Operating manual for a **fixer** session — a session whose standing `/goal` is
to drain one project's slice of the shared UX-fixes queue and keep it empty.

A fixer is not a special CCC mode. It is an ordinary session (Claude or Codex)
running in a project repo, given a `/goal` that points it at this doc. The only
thing that differs per project is **which repo the session runs in** and
**which `PROJECT` code it drains** (`CCC`, `BYM`, `HERMES`, …).

> Spawning a fixer: open a session in the target repo and send it the
> canonical goal at the bottom of this file, with `<PROJECT>` filled in.

## The queue — what it is

- One shared JSON file for the whole machine:
  `~/.claude/command-center/ux-fixes-queue.json`
  (override with `CCC_STATE_DIR` / `UX_FIXES_QUEUE_FILE`).
- **Every project lives in that one file**, namespaced by an item's `project`
  field. A `BYM` fixer only ever touches `project == "BYM"`; it never sees or
  edits `CCC` items.
- Annotations route to a project automatically by repo basename (see
  `_REPO_PROJECT` in `ux_fixes_queue.py`) — e.g. `bookyourmat` / `bym+finie`
  → `BYM`, `claude-command-center` → `CCC`. So you do not "create" a queue;
  annotating a project's pages files tickets into it.

## The tooling lives in the CCC repo

The `ux_fixes_queue` Python module is **only** in the CCC checkout
(`/Users/amirfish/Apps/claude-command-center/ux_fixes_queue.py`), not in your
project repo. Drive it cross-repo by putting the CCC repo on `sys.path`:

```bash
python3 -c "import sys; sys.path.insert(0,'/Users/amirfish/Apps/claude-command-center'); \
  import ux_fixes_queue as q; print(q.list_items(status='open', project='<PROJECT>'))"
```

### API (the only functions you need)

`SID` = your own CCC session id (so claims are attributed to you).

| Call | Does |
|------|------|
| `q.list_items(status='open', project='<PROJECT>')` | List open tickets for your project |
| `q.claim_next(SID, project='<PROJECT>')` | Atomically claim the oldest open item → returns it (or `None`) |
| `q.update_status(ref, 'in_progress', SID)` | Claim a specific ref (e.g. `'BYM-87'`) |
| `q.close(ref, SID)` | Mark a ticket fixed |

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

Read the project's own `CLAUDE.md` (e.g.
`/Users/amirfish/Apps/BYM+Finie/CLAUDE.md`) for that repo's house rules —
deploy/CI, test commands, security-sensitive paths. House rules win.

## Canonical `/goal` (paste this to spawn a fixer)

Fill in `<PROJECT>` and the repo path:

```
/goal Drain the <PROJECT>-* UX-fixes queue and keep it empty. Read
docs/ux-fixes-worker-brief.md in the CCC repo
(/Users/amirfish/Apps/claude-command-center) for exactly where the queue is,
the ux_fixes_queue API (list_items / claim_next / update_status / close,
scoped to project='<PROJECT>'), and the claim → fix → verify → commit --only
→ close loop. Also read this repo's CLAUDE.md before touching deploy/CI.
Never busy-wait — idle-poll for new tickets. Don't push unless asked.
```
