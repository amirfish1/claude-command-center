# On-call fault checks

On-call is still a preview. These checks cover specific crash windows. They do
not establish weeks of unattended recovery. Worker death, durable provider
inbox handoff, and bounded history retention remain open.

## Reproduce

Run from the checkout on Linux:

```
python3 scripts/oncall-chaos-proof.py --quick
python3 scripts/oncall-chaos-proof.py
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

Quick mode loads 3,000 historical alerts. The other mode loads 30,000. Both
start the real dashboard, call its HTTP API and CLI, and kill isolated
processes. Neither starts a fixing agent or uses a model account. WatchTower
is optional. When installed, the proof starts a foreground watcher on port
zero, with auto-drain disabled for its fixture queue, kills it and starts it
again. This proves a parked ticket survives, not that a dead worker recovers.

The proof uses a temporary HOME, state directory, queue store and allow-listed
executable path. It disables telemetry and updates for that process only.
Detached children are cleaned up by the exact temporary HOME marker. No real
service is restarted. No provider credentials are copied. HTTP fault servers
bind to loopback and implement only the ntfy response shapes needed by the
transport tests. These are not live ntfy, email or SMS service tests. No Docker
container is needed for this proof. The existing protocol proof covers local
ntfy and email servers separately.

The parent has a four-minute deadline and removes temporary processes and
files on completion, failure, timeout or a handled termination signal. A host
crash or SIGKILL of the parent cannot run cleanup.

## What was broken

| Fault | Behavior now | Evidence and limit |
|---|---|---|
| Kill during a SQLite write | The uncommitted write rolls back. | Real SIGKILL; integrity check passes. This is not a power-loss or disk-full test. |
| Restart after an owner alert | The persisted receipt prevents another send. | Real dashboard restart, HTTP tick and CLI status. |
| Kill after an owner transport accepts but before returning | The durable send intent remains uncertain. It is not sent again. | Real dashboard and blocked command process; one acceptance in the local sink. Status retains the open alert and failed delivery; the log explains the uncertainty. |
| Kill after a customer transport accepts | The message remains uncertain and an owner review alert is raised. | Real dashboard kill during an HTTP approval; both replayed and distinct duplicate reply IDs leave one customer acceptance. |
| Kill after applying an approval but before closing its alert | A durable per-alert reservation prevents a second application. | Regression with an approval dependency that exits after its side effect. This does not prove a real Codex or Claude permission round trip. |
| Reply claimed before delivery completes | An unhandled or failed reply becomes an owner review alert. Its text remains in SQLite. | Restart regression plus the real interrupted approval request. The action is not automatically replayed. |
| Watcher killed and restarted | A fixture ticket remains parked. | Real isolated WatchTower process, CLI and queue store. No worker was active. |
| Channel refuses connections for ten simulated minutes | Owner delivery continues retrying at the existing two-minute interval and succeeds after the server returns. | Real HTTP adapter and loopback connection refusals, with a fast retry clock. Previously the fifth failure exhausted all attempts. |
| Provider accepts then drops the connection | The result is uncertain and is not automatically retried. | Real HTTP server records exactly one acceptance. |
| Half-open response | Timeout leaves an uncertain attempt. No replay follows. | Real socket with an accelerated 100 ms test deadline; production adapter deadline remains unchanged. |
| Customer channel removed before approval | The send fails without touching a provider and becomes an owner review alert. | Store and customer-send regression. Restoring a channel does not authorize an automatic resend. |
| Clock moves backward | Scheduled engine checks run once at the new time and then use their normal interval. | Constructor-clock regression. Source clocks, quiet-hour timezone changes and the hourly cap after a backward jump are not covered. |
| Laptop sleeps eight hours | The engine scheduler performs one due check on wake, without replaying missed intervals. | Clock regression only; no real suspend/resume test. |
| Old unsent alert behind thousands of sent alerts | An indexed, rotating batch selects unsent alerts. | SQLite VM instruction budget with 10,000 historical alerts. The budget was not relaxed. |
| Month-shaped history | Delivery uses batches of at most 50 alerts. Hourly-cap lookup returns at most the cap in distinct alert IDs. | Proof loads historical alerts and measures one tick and database size. This is not a month of all event types or live operation. |

The existing On-call suite also checks quiet hours, the hourly cap, duplicate
inbound IDs, replies to closed alerts, and customer follow-ups. Those tests
are not substitutes for restart faults at each of those boundaries.

## Conservative recovery

An unknown transport result is not evidence that nothing was sent. On-call
now records intent before a send. A process crash or transport exception can
leave that intent uncertain. Customer messages are never automatically retried
after a failed attempt. The owner gets a review alert on the next active tick.
If there is no working owner channel, review is visible in local status only.

Owner connection refusals can safely retry because the connection was not
accepted. Explicit negative provider receipts retain retry behavior. Other
transport exceptions are treated as uncertain. Command failures reported as
nonzero exit codes are still explicit failures: a command that sends and then
exits nonzero can therefore duplicate an owner message. A custom command must
report its delivery outcome truthfully. This proof does not give arbitrary
commands or provider error responses exactly-once semantics.

An owner answer also has a durable reservation. If applying it fails, another
message does not retry that same alert. Check the ticket or session directly.
This can hold an action that never reached its destination, but avoids applying
an approval twice when the result is unknown. An unclear answer that performs
no action releases its reservation so a corrected answer can be used.

The alternative is automatic retry after uncertain outcomes. That improves
availability but can duplicate messages or actions. Safe automatic recovery
needs an idempotency key or a receipt lookup at the destination, including
WatchTower answer/comment delivery and session injection. This change does not
add those destination contracts.

## Still not survived or verified

- A fixing worker killed mid-fix has not been exercised. The watcher restart
  proof has no active worker. Neither worker reclaim nor automatic repair is
  established here.
- Providers can advance their polling cursor before the engine durably claims
  every returned message. A crash in that interval can still lose an inbound
  reply. The new review alert only covers replies already recorded in SQLite.
- Direct acknowledgments and status replies do not use the alert-send intent
  record. Distinct inbound IDs can still produce repeated acknowledgments.
- Out-of-order provider pages and successful but slow responses have not been
  fault-tested. No assertion is made about delivery ordering.
- Quiet-hours ending and a hard-stop decision waiting behind the hourly cap
  have ordinary regression coverage, not process-fault coverage. Neither
  condition grants approval or bypasses the cap.
- Customer follow-ups after closure, and during a restart before intake, have
  not been process-fault tested. A close collected between report-state update
  and fixed-notice creation can still omit that notice.
- The proof has no live provider, email container, real permission prompt or
  model worker. It makes no claim that a phone displayed a message or an agent
  fixed a ticket.
- Alert, report, inbound, delivery and reservation history has no retention
  bound. A month-shaped alert load measures growth; it does not fix it. Deleting
  deduplication history without a provider replay horizon could permit repeats.
- Closed-report collection still limits itself to 500 filed reports and can
  query WatchTower per missing active ticket. A larger backlog can starve older
  reports and make many CLI calls. This needs a separate pagination and batch
  lookup change. The new delivery query budget does not cover that path.
- Atomic file replacement and SQLite transactions have not been tested against
  disk exhaustion, filesystem corruption, power loss, or multiple dashboard
  writers sharing the same state directory.

These limits prevent calling the full reliability task complete. The proof
prints BOUNDARY rows rather than presenting omitted cases as passes.
