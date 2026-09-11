Codex resumes no longer hang indefinitely. CCC's own dashboard and worker are
no longer mistaken for a foreign Codex writer holding the shared state DB (they
were blocking each other's app-server), a managed daemon that fails `initialize`
twice is skipped for 60s instead of costing 10s on every retry, and a wake that
makes no progress for 60s now reports the actual blocker instead of spinning on
"Thinking…".
