# Background / harness runs

Headless `claude -p` runs launched with `cwd=os.tmpdir()` (eval harnesses, scripts)
write transcripts to `~/.claude/projects/-private-var-folders-...-T`, which no
CCC view reads. `GET /api/background-runs` (`ccc_server/background_runs.py`) closes that gap.

- **Ingest:** scan only temp-dir project folders (not the whole tree); tokens come from the
  per-file `(mtime, size)` cache in `usage_stats`.
- **Attribution:** spawn marker `spawned_via` or `caller`, plus `kind`; no marker means the `unknown` bucket.
- **Alert:** `alerts` lists spawners at or above `BACKGROUND_RUNS_HOURLY_ALERT` runs/hour (server.py).
- **No per-run notifications:** hooks skip temp cwds.

Not built yet: the bottom-left UI view, cost in USD, and pushing alerts to the notification center.
