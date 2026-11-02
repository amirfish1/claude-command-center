# Old-session recaps

When a session has been idle for at least 24 hours and currently needs its
owner, its row offers a Recap with the goal, reported delivery, recorded
request and last activity date. New sessions, working sessions, archived
sessions, superseded continuations and agent-owned next steps do not enroll.

The saved recap appears immediately. Only a visible row may request a paid,
read-only recheck via `POST /api/sessions/old-recaps` with a `session_ids`
array (1–20 local IDs). GETs, archive scans and elapsed time alone never
launch checks. The server resolves the IDs from its own cache and checks the
current transcript, lifecycle, live state and known continuation chain.

Paid rechecking is off by default. To opt in on this installation, save
`{"enabled":true}` to `session-rechecks/policy.json` under CCC's state directory.
Set it to false to stop new checks. Recaps still work with rechecking disabled.

Admission is persisted in the local `session-rechecks/checks.sqlite3` store:

- Once per continuation chain and request/evidence revision, including failed
  attempts. Refreshes, multiple tabs and server restarts reuse that record.
- Changed requests or linked-ticket evidence require a 24-hour cooldown from
  the chain's last admitted check. Unchanged requests never expire.
- One check at a time, with at most three admitted attempts per UTC day.
- Lost checks become unverified after three minutes and do not retry.

The checker is a fresh, tool-free Haiku invocation: at most 16,000 evidence
characters, 1,200 output tokens, one turn, a 60-second timeout and a CLI
budget of $0.08. It has no user hooks, MCP servers, repository instructions
or old conversation resume. Model policy still applies. No retry is made on
failure. Pending visible rows can poll admission/status; this does not bypass
any spending gate. Quota-deferred rows retain their saved recap.

Evidence consists of the saved goal/request, indexed recent user asks and
last reply, plus linked-ticket display snapshots. These are not production
verification. Recaps distinguish reported delivery from confirmed facts and
retain the original recorded request. A recheck never replies, executes an
option, edits a ticket, archives a session or changes its attention status.
Results that race a new answer or revision are discarded.
