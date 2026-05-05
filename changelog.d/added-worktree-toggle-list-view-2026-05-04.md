**🌿 worktree toggle in list-view new-session bar.** The same `🌿 worktree`
checkbox that already lives in the kanban-toolbar new-session modal now also
appears in the input-context strip when the list-view "+ New session" button
puts the bar into new-session mode. Previously this entry point spawned via
`spawnFromInlineInput` with no `worktree` flag, so list-view users had no way
to launch an isolated `feat/<slug>` worktree without switching to the kanban
view first. When checked, the inline path POSTs `worktree: true` to
`/api/sessions/spawn` exactly like the modal does (codex spawns still ignore
the flag, matching the modal's precedent).
