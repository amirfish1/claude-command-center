- Periodic reaper kills stray `server.py`/`ccc_worker.py` processes older than
  10 minutes that aren't recognized as the dashboard's or worker's own
  launchd-managed PID, reported on `/api/health` with an in-app toast — closes
  the hole where a leaked process could sit around for days running stale
  code with authority over live sessions.
- Unattended "continue" auto-resume pokes into a Codex session now require an
  explicit per-session opt-in (default off), settable via `"auto_resume": true`
  on `/api/sessions/spawn`, instead of being opt-out by default.
