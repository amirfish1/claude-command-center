**Worktree-per-spawn checkbox.** A new `🌿 worktree` toggle next to the
existing `pkood spawn` toggle (in both the inline new-session row and the
new-session modal) lets you launch the session in a fresh git worktree on
a `feat/<slug>` branch, isolated from main. When enabled, CCC runs `git
worktree add <repo-parent>/<repo-name>-wt-<slug> -b feat/<slug>` against
the source repo before spawning Claude there — so the agent literally
cannot accidentally commit to main even if it ignores the multi-agent
git-hygiene rules. Path collisions get a numeric suffix (`...-wt-foo-2`),
branch collisions get the same suffix on the branch name. New optional
`worktree: bool` field on `POST /api/sessions/spawn`; response gains
`worktree_path` and `worktree_branch` when applicable. `pkood` spawns
ignore the flag (out of scope).
