# Worktree Init Hook

CCC can spawn a session inside a fresh git worktree. When the worktree toggle is on, CCC creates a sibling worktree at:

```text
<parent>/<repo>-wt/<slug>/
```

on a fresh branch:

```text
feat/<slug>
```

If that new worktree contains an executable `.ccc/worktree-init` script, CCC runs it once before the agent process starts. Use this for fast, repo-local setup that every isolated worktree needs: copying a local `.env`, installing dependencies, syncing a Python environment, or writing repo-specific bootstrap files.

## Enable It

Copy the example script and make it executable:

```bash
mkdir -p .ccc
cp .ccc/worktree-init.example .ccc/worktree-init
chmod +x .ccc/worktree-init
```

Commit `.ccc/worktree-init` if the setup is safe for every contributor. Keep secrets out of the script; copy them from ignored files such as `.env` instead.

## Environment

CCC runs the hook with the worktree as the current directory and provides:

| Variable | Value |
|---|---|
| `CCC_WORKTREE_PATH` | Absolute path to the newly-created worktree. Same as `$PWD`. |
| `CCC_PARENT_REPO` | Absolute path to the source repo's git toplevel. |
| `CCC_SESSION_NAME` | Slug used for the worktree directory and branch. |

Example:

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ -f "${CCC_PARENT_REPO}/.env" && ! -f "${CCC_WORKTREE_PATH}/.env" ]]; then
  cp "${CCC_PARENT_REPO}/.env" "${CCC_WORKTREE_PATH}/.env"
fi

if [[ -f package-lock.json ]]; then
  npm ci
fi
```

## Failure Behavior

The hook is intentionally non-blocking:

- Missing `.ccc/worktree-init`: silently skipped.
- Present but not executable: skipped.
- Non-zero exit: stdout/stderr and the exit code are written to the session spawn log, but the session still starts.
- Invocation error or timeout: logged, then the session still starts.

Keep the hook idempotent and fast. The agent waits for it to finish before receiving the initial prompt.

Spawn logs live under the repo log directory in `~/.claude/command-center/logs/`.
