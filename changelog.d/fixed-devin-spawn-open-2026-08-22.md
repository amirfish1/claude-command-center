**Devin sessions now open in CCC.** Spawning Devin no longer leaves a stuck
placeholder: CCC matches the CLI session (including wrapper vs child pid and
newline-flattened prompts), attaches `spawn_pid` so the card swaps, shows the
spawn log while it materializes, and only treats a Devin session as live when
the lock file's process is actually running.
