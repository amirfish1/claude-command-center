# Restart matrix (always on)

A committed fix is not a live fix. Python loads code once at process start, so
a change sits inert until the process running it restarts. **End every fix with
these three lines:**

```
Dashboard server restart needed:  Y/N
Worker restart needed:            Y/N
WatchTower server restart needed: Y/N
```

Quick rules (full table in `CLAUDE.md` § Restart matrix):

- `server.py` (or a module it imports) → **Dashboard Y**, and **Worker Y too** —
  `worker_engines.py` lazily `import server`, so the worker runs its own copy of
  that module state. Restarting only the dashboard looks exactly like "the fix
  didn't work".
- `ccc_worker.py`, `worker_engines.py`, `control_plane.py` → **Worker Y**.
- `static/*`, `docs/`, `changelog.d/`, `tests/`, markdown → **N/N/N** (static is
  served from disk per request; a browser reload is enough).
- WatchTower (`ai.watchtower.watcher`, `:8787`) lives in its own repo — CCC
  changes are **N** unless you edited WatchTower itself.

```bash
launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center.worker
launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center
```

Worker first. Restarting it marks running queue items "needs reconciliation",
so only restart when the change actually requires it.
