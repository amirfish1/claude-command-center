# Lane W2-1 — Decision Inbox daemon + idle-session token governor

Updated: 2026-09-03 17:20
Status: IN PROGRESS — design fixed, building module
Owner: CCC lane W2-1 (dispatched by 25c52af6-c616-46a2-b47d-0f8a9e016055)

## Design (decided)
- `ccc_server/decision_inbox.py` (stdlib-only, `_core` proxy). Hourly daemon
  thread started from server.py startup (gated by CCC_EPHEMERAL /
  CCC_DECISION_INBOX_DISABLED). No launchd: server already owns loops.
- Config `~/.claude/command-center/decision-inbox.json` (personal paths live
  here, never in repo): strategy_board path, interval_s, max_cards_per_run=5,
  idle_hours=2, wt_age_days=3, model=claude-sonnet-5, spawn_cwd.
- State `~/.claude/command-center/decision-inbox/cards.json` + `runs.jsonl`.
- Sources: (1) Strategy Board table rows: ⚠️ Blocked, or 🔵 Open with ETA in
  past (ISO date, or relative word anchored to file mtime + 2d). (2) `wt status
  --json` once; stuck queues with oldest_open_age > wt_age_days; top-N get one
  `wt ls --json`. (3) archive cached rows: is_live, mtime idle >= 2h, unfinished
  (pending_tool / goal not done / session_state working).
- Analyst: headless `claude -p --model sonnet` (same shape as queue brief),
  returns JSON {title, context, options[<=3]{label,cost,recommended,action}}.
  Governor cards need no analyst (fixed pause/nudge/kill options).
- Governor detectors on live candidate sessions only (tail bytes of JSONL):
  repeated identical tool errors (>=3 same hash), no edit >45m while working
  (had edits earlier), context >=85%.
- Dedupe by source_id (open or decided/dismissed within 7d = skip). Cap 5/run.
- Page `static/decision-inbox.html` at `/decision-inbox.html`; rail entry
  "Decisions" via _resolve_apps. Endpoints: GET /api/decision-inbox,
  POST /api/decision-inbox/{run,decide,dismiss,governor}.
- Follow-through: option.action.kind spawn -> _core.spawn_session; inject ->
  _core._inject_text_into_session(sid, text, mode=steer); governor pause ->
  _interrupt_session, nudge -> inject steer, kill -> system_process_kill(pid).

## Done
- Orientation (app rail, queue-brief claude -p pattern, archive rows, wt CLI).

## Next
- Write module + tests + page + server wiring + README + screenshot.
- Write personal config file, restart dashboard+worker, run once, screenshot.
