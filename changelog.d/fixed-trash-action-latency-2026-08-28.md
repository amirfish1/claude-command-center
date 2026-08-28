- **Trash from the conversation list** no longer waits on a Devin CLI
  `sessions.db` rebuild (tens of seconds against a multi-GB DB). The sidebar
  `/list` overlay serves a cached snapshot instead of blocking every poll —
  which had filled the browser's HTTP/1.1 slots and left the trash POST queued
  for >10s. Trashing a session with no child lanes also skips the Codex sqlite
  fallback that used to run on every leaf.
