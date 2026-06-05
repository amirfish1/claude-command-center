"Launch in Terminal" no longer hallucinates a deleted-worktree cwd and drops the user in their home dir. Two-layer fix:

1. **Server (`find_codex_conversations`)** — `effective_cwd` used to be `tail_worktree_path or cwd`, surfacing whatever path the JSONL tail extracted from an old `cd <…>` Bash command. If that worktree was since deleted, the row carried a non-existent path. Now picks the first cwd candidate that still exists on disk via the new `_first_existing_dir` helper (tail → cwd → pinned), falling back to the literal worktree path only when nothing exists.

2. **Client (`buildResumeCommand`)** — for missing cwds that don't match the `.claude/worktrees/<branch>` recreation pattern (e.g. ad-hoc `BYM-Finie-push-reschedule-sGH1nB`), `cd '/...' && resume` would fail (no such dir) and the `&&` would block the resume. Now falls back to `currentSession.repoPath` when known; drops the `cd` entirely (runs resume from the user's terminal pwd) when no repo path is available.
