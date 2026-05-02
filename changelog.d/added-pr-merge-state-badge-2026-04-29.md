**PR merge-state badge on kanban rows.** Sessions that ran `gh pr create`
now show a state-aware chip in the row's signal slot:

- `↗ PR #14` (cyan) — open
- `✓ PR #14` (purple) — merged
- `× PR #14` (muted) — closed without merge

State is fetched once per unique PR URL via `gh pr view <url> --json
state,mergedAt`, cached for 60s so the kanban refresh cadence (~10s)
doesn't shell out per row per poll. Cross-repo / fork PRs work because
gh resolves the repo from the URL itself. The chip now renders for *any*
session with a captured PR (previously gated to worktree rows only) —
which matches the actual user question of "did the PR I opened get
merged?".

Cache busts automatically when CCC's own merge button calls `gh pr
merge`, so the badge flips immediately. Web-UI merges still take up to
60s to surface (next cache expiry).

New fields on `/api/conversations` rows: `pr_state` ("OPEN"/"MERGED"/
"CLOSED"/""), `pr_merged` (bool), `pr_merged_at` (ISO 8601 string).
