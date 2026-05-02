**Subagent-worktree alert dot** on the topbar Worktrees button. When
superpowers / orchestration skills have spawned locked agent worktrees
the user may have forgotten about, an orange dot appears on the
button. Polls `/api/repo/worktrees` every 60s; the badge tracks
`agent_count > 0` and the button's tooltip surfaces the count.
