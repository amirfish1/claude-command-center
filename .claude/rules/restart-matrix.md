# Restart matrix (always on)

A committed fix isn't live until the process running it restarts. End every fix with:

```
Dashboard server restart needed:  Y/N
Worker restart needed:            Y/N
WatchTower server restart needed: Y/N
```

- `server.py` or any module it imports → Dashboard **Y** and Worker **Y** (`worker_engines.py` lazily imports `server`, so the worker runs its own copy).
- `ccc_worker.py`, `worker_engines.py`, `control_plane.py` → Worker **Y**.
- `static/*`, `docs/`, `changelog.d/`, `tests/`, markdown → N/N/N (browser reload is enough).
- WatchTower (`ai.watchtower.watcher`, `:8787`) is a separate repo → N unless you edited it.

Restart with `ccc restart` (default `all`; or `ccc restart dashboard` / `ccc restart worker`). Never run
`systemctl --user restart ccc*` or `launchctl kickstart` yourself: each restart costs a ~30s cold warmup, and
agents restarting after their own commits caused most restarts on a busy host (OPS-150). `ccc restart` goes
through the drain-aware `/api/restart*` endpoints, merges requests within 5 min of the last restart into one
deferred restart, and retries a restart the server refuses as unsafe. `--now` only when a human asks for it now.
Worker restart marks running queue items "needs reconciliation". Details: `docs/agent-rules.md`.
