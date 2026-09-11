# Lane W7-4 - Spawn Ledger Scorecard

Updated: 2026-09-04
Status: DONE - implemented, verified, ready for restart
Owner: CCC lane W7-4 (dispatched by 25c52af6-c616-46a2-b47d-0f8a9e016055)

## Scope
- Add a read-only `GET /api/spawn-ledger` endpoint backed by
  `SPAWN_LEDGER_PATH` or `/Users/amirfish/MyOfficeMgr/projects/spawn-ledger/ledger.jsonl`.
- Add `/spawn-ledger` as a standalone CCC page showing scorecard averages and
  newest-first graded rows.
- Keep all ledger handling in `ccc_server/`, following the Decision Inbox
  module + server adoption pattern.

## Decisions
- Normalize malformed, missing, or out-of-range grades to `null`; aggregates
  count only numeric grades from 1 through 5.
- Sort API rows newest first so the UI can render raw graded rows directly.
- Scorecard rows are grouped by `engine` + `model`; columns are `task_type`.

## Next
- Commit only the W7-4 files with `git commit --only`.
- Report back to dispatch session 25c52af6-c616-46a2-b47d-0f8a9e016055.

## Done
- Added `ccc_server/spawn_ledger.py`: read-only JSONL parser, env/default path
  resolution, grade normalization, newest-first row ordering, and scorecard
  aggregates.
- Added server adoption plus `GET /api/spawn-ledger` and `/spawn-ledger`
  (`/spawn-ledger.html` alias) routes.
- Added Apps rail entry "Spawn Ledger".
- Added `static/spawn-ledger.html`, rendering the engine/model x task_type
  scorecard and raw graded rows newest first.
- Added fixture-ledger tests in `tests/test_spawn_ledger.py`.

## Verification
- RED: `python3 -m pytest tests/test_spawn_ledger.py` failed with
  `ModuleNotFoundError: No module named 'ccc_server.spawn_ledger'`.
- GREEN: `python3 -m pytest tests/test_spawn_ledger.py` passed: 5 passed.
- Live API check against ephemeral server:
  `curl -s --max-time 10 http://127.0.0.1:18097/api/spawn-ledger` returned
  6 rows, 4 graded rows, 1 ignored line, and expected scorecard averages.
- UI screenshot check against ephemeral server:
  `SNAPSHOT_URL=http://127.0.0.1:18097/spawn-ledger SNAPSHOT_OUT=.sprint/W7-4-spawn-ledger.png node snapshot.js`
  rendered the scorecard and graded rows correctly.
- Broader smoke note: `python3 -m pytest tests/test_smoke.py -x -vv` hit an
  existing sidebar CSS assertion in `static/app.css`, which W7-4 did not touch.

## Restart matrix
- CCC dashboard server: needs restart.
- CCC worker / control-plane worker: needs restart because `server.py` module
  adoption changed.
- WatchTower: no restart needed.
