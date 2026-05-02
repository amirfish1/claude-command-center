**Open-PR visibility in the Worktrees modal.** Each worktree row now
shows a `PR #N` badge (linked to GitHub, with `draft` flavour for draft
PRs) when its branch matches an open PR's head ref. A new "Open PRs
without a worktree" section lists open PRs whose branch has no local
worktree, so nothing is hidden. Powered by `gh pr list` cached for 30s
on the server, surfaced via the existing `/api/repo/worktrees`
endpoint (new fields: `open_prs_count`, `orphan_prs`, plus a `pr`
field per worktree entry).
