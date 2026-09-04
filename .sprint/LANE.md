# Lane W2-1 — Decision Inbox daemon + idle-session token governor

Updated: 2026-09-03 18:05
Status: IN PROGRESS — code committed (9ef722b2, 55ab70d2, 7953b9df); verifying live + screenshot
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
- ccc_server/decision_inbox.py, static/decision-inbox.html, tests (28 pass),
  server.py wiring (adopt + GET/POST routes + rail + loop), README section.
- Personal config written: ~/.claude/command-center/decision-inbox.json
  (board path, spawn_cwd=~/MyOfficeMgr, ignore "Sleep").
- Public names prefixed decision_inbox_* / _di_* (adoption binds module
  globals onto server; _iso/_lock/_parse_iso/_wt_run collided with siblings).
- tests/test_perf_budget.py: 1 pre-existing failure
  (test_system_services_no_subprocess_on_warm_cache, service id set), not ours.

## Status: DONE
- Shipped ccc_server/decision_inbox.py, static/decision-inbox.html, tests
  (28 pass), server wiring, README section + screenshot
  (docs/images/decision-inbox.png).
- Restarted worker+dashboard. Live scan produced 10 real Sonnet cards (3
  options each, 1 recommended) + 1 governor context-high card. Cap 5/run and
  source-id dedupe both verified across two runs.
- Fixed a real bug found in verification: `--disallowedTools` is variadic in
  current Claude Code and swallowed the analyst prompt; switched to the `=`
  form (commit cbdf559d). Same class as the auto-titler --mcp-config break.
- Infra note: a stray orphan server.py (pid 67616, port 8099) was tripping
  the dashboard duplicate-repo guard and blocking 8090; killed it per the
  guard's own docstring, dashboard rebound immediately.

## Restart matrix
Dashboard server restart needed:  Y (done)
Worker restart needed:            Y (done)
WatchTower server restart needed: N

## Pre-existing, not this lane
- tests/test_perf_budget.py::test_system_services_no_subprocess_on_warm_cache
  fails on a service id-set mismatch (app_server/worker), unrelated to
  decision_inbox. All other 103 perf-budget tests pass.

## Commits
9ef722b2, 55ab70d2, 7953b9df, 54346e4d, cbdf559d, 816ecf5e (not pushed).
