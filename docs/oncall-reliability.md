# On-call reliability checks

On-call stores send intent and incoming messages before applying side effects.
Owner alerts favor getting a decision through. Customer messages favor avoiding
a repeat. WatchTower still owns worker recovery; On-call reports a queue that
WatchTower marks stuck. It does not silently restart or reassign that work.

## Run the checks

```
python3 scripts/oncall-chaos-proof.py --quick
python3 scripts/oncall-chaos-proof.py
python3 scripts/oncall-chaos-proof.py --with-agent
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

Linux is required for process cleanup. Quick mode has a 55-second deadline.
It covers real dashboard crashes, CLI status, HTTP routing, a killed SQLite
write, reporter follow-ups, and local HTTP faults. The full run adds a real
foreground watcher restart and a fast-clock month. It has a ten-minute deadline.
The paid agent stage has a thirty-minute overall deadline and runs Codex
`gpt-6.1-terra` at medium effort in an isolated automatic WatchTower queue.

The agent first enters a local gate while working on a seeded addition bug.
One stage kills that worker and its descendants. Another kills and restarts
the watcher while the worker is still fixing. The observer checks ticket state,
passing tests and the unchanged test file. It distinguishes automatic recovery,
explicit parking, and an owner warning about work that is still stalled.
The paid runner snapshots the installed WatchTower Git revision into its
temporary home and prints that revision, so an upstream update cannot change
the running experiment. It requires an importable Git-backed WatchTower install.

All stages use a temporary HOME, state directory, queue store and allow-listed
executable path. The paid stage copies only Codex `auth.json`; it does not copy
configuration, sessions, skills or Claude credentials. A renewed login in that
copy is discarded, with a notice. Only the fixing agent makes external model
requests. Message transports are local. No real service or configuration is
restarted or changed. The HTTP fault servers implement the ntfy response shapes
needed by the transport adapter; they are not live provider tests.

Processes with the exact temporary HOME marker are killed on exit, failure,
timeout or handled termination. Temporary files are removed. A host crash or
SIGKILL of the proof's parent cannot run cleanup. The proof starts no containers.

## Delivery policy

| Outcome | Owner alert | Customer message |
|---|---|---|
| Confirmed success | Keep the receipt; do not repeat. | Keep the receipt; do not repeat. |
| Connection refused before acceptance | Retry after two minutes, including after a long outage. | Keep the failed send for owner review. |
| Unknown outcome after a crash, disconnect or timeout | Retry once after two minutes, with `This may be a repeat.` as the first line. Never make a third attempt after that uncertain outcome. | Never retry automatically. Raise an owner review alert. |
| Command sends, then exits nonzero | Treat as unknown, including the single warned retry. | Treat as unknown; no automatic retry. |
| Channel removed before approval | Keep the failure visible. | Do not send; raise an owner review alert. Restoring the channel does not authorize a retry. |

WhatsApp template fallback also reserves its attempt before calling the provider.
A restart after acceptance cannot repeat that fallback; an unknown result raises
an owner review alert.

The repeat reservation itself is durable. If the process dies after reserving
the warned retry but before sending it, that retry can be lost. The alert and
uncertain delivery remain visible. Exactly-once delivery and guaranteed delivery
cannot both be promised without a receipt lookup or idempotency key at the
provider. An owner channel that is unavailable cannot deliver its own warning;
local status and the activity log remain the fallback.

Each owner answer also has a durable reservation. A crash after applying an
answer cannot cause another reply ID to apply the same answer again. A failure
requires checking the ticket or session directly. An unclear answer that did
nothing releases the reservation so the owner can correct it.

Direct acknowledgments are tied to inbound provider IDs through the inbound
claim. Replaying the same provider ID after restart does not send a second
acknowledgment. Different IDs for separate `status` requests receive separate
responses. Direct acknowledgments have no automatic send retry.

## Incoming replies

Email, Telegram, ntfy, SMS and WhatsApp polling now commit full inbound payloads
and cursor changes in one SQLite transaction. A bounded durable inbox feeds the
engine after polling or restart. If the transaction fails, neither cursor nor
inbox advances. If the process dies after the commit, the next engine consumes
the saved message. If it dies after claiming the message but before confirming
the action, the reply is parked for owner review rather than applied twice.

GitHub comments already call the durable engine callback before saving their
page cursor. A crash regression checks that order and confirms replay does not
apply a comment twice. Each other listed transport has before-commit and
after-commit crash regressions through its actual poll implementation.

ntfy accepts an older reply arriving after a newer one when both are newer than
its initial baseline. Message IDs still deduplicate replay. It does not backfill
messages from before the baseline. A reporter's reply after ticket closure
creates a new held follow-up under the normal unknown-customer rules. The
process proof stages that reply while the dashboard is down, restarts the
dashboard, and checks that replay creates no second follow-up and leaves the
original ticket closed.

Provider history and page limits still apply. No test claims that a provider
will retain messages through an unlimited outage. The first poll intentionally
baselines existing history. After the retention horizon, an old provider ID is
no longer guaranteed to deduplicate if a provider replays it again.

## Time and scale

A backward wall-clock jump makes scheduled engine checks due once. The hourly
owner budget stores remaining time, rather than holding alerts until the old
wall time returns or granting a fresh budget immediately. Forward elapsed time
then expires that budget normally. A regression sleeps the engine clock eight
hours across the end of quiet hours and checks one delivery on wake, without
replaying missed intervals. Quiet hours and the hourly cap still hold hard-stop
decisions. Neither waiting condition grants approval.

The full simulation advances one hour at a time for thirty days, submitting
five reports per hour through the real engine and store. It then drains bounded
closure pages and deliveries. It uses deterministic queue and transport doubles,
not 3,600 paid fixing jobs. One observed run produced 3,600 reports, alerts and
deliveries in 26.81 seconds. Its maximum tick was 0.278 seconds, with at most two
queue calls per tick. Production health checks add one status call per watched
queue per minute. Sources add their own configured polling calls. A second run
on a busy host took 57.49 seconds, with a 1.142-second maximum tick; the call
bound stayed at two. These are observations, not wall-time guarantees.

Closed-report collection reads one page of at most 100 closed tickets per queue
per pass, saves the offset, and cycles back to the first page. It never calls
`wt find` per report. This requires WatchTower's `ls --offset` support. Queue
selection rotates through at most sixteen named queues per pass, plus legacy
reports with no saved queue. Queues without filed reports need no closure call. Tests
check an older report beyond a thousand rows and enforce indexed local lookups.
A new closure can wait for a page cycle; no fixed recovery latency is promised
for arbitrarily large queues. WatchTower's own backend work is outside the local
SQLite instruction budget.

## Thirteen-month retention

On-call inspects at most 100 rows per table per minute, using age indexes and
saved cursors. It removes completed rows older than thirteen calendar months
from alerts, deliveries, inbound messages, customer messages, reports and the
activity log. It also removes per-alert retry and answer reservations when
those closed alerts expire. Protected rows do not block later eligible rows.

Open alerts, unhandled replies, replies needing review, pending or uncertain
customer sends, and reporter questions still waiting for an answer do not
expire. Deliveries for open alerts are kept. Reports with customer messages
waiting for review are kept. Recent creation, send and owner-action timestamps
protect records across clock jumps. The full 52-week summary window remains
inside the retention horizon. The activity log also retains its existing row cap.

This bounds completed history by age, not by a fixed number of bytes. Unresolved
work is deliberately retained. SQLite reuses freed pages; pruning does not run a
blocking vacuum or promise to shrink the file immediately. Budget tests cover
100,000 historical alerts, other retained tables and a full engine tick without
scanning the whole history. Batch and instruction bounds are asserted, not
relaxed to fit slower queries.

## Worker recovery evidence and limits

The first paid worker-kill observation reached the fault gate in 14.98 seconds.
After SIGKILL, the ticket remained `in_progress` for the following 480 seconds,
without a fix or owner question. That result is recorded in upstream ticket
`WT-64`. Eight minutes alone does not establish permanent loss: WatchTower's
normal stuck-health threshold is ten minutes.

On-call now checks WatchTower health and creates one owner alert for a stuck
queue episode. Recovery clears that alert. A later stall creates a new episode.
It does not infer that a worker is dead merely because a long task is quiet,
and it does not park or reassign a live claim.

A longer paid run completed in 920.15 seconds. The killed worker's ticket closed
with passing tests after 737.99 seconds. The watcher-kill stage closed with
passing tests after 69.00 seconds. WatchTower's fix for stale hosted-worker
liveness records landed during that run. Those results do not isolate the fix's
effect. A later run pinned the fixed WatchTower revision, reached the worker
fault gate, and was stopped at the coordinator's request before recovery.
The fixed upstream revision therefore still lacks a completed isolated recovery
result from this proof.

The final quick proof passed in 28.23 seconds, including the direct
acknowledgment restart check. The final targeted regression run passed 25 tests
in 12.81 seconds. The demo's existing two-second
wall-time test failed on the busy host, most recently at 2.862 seconds. Its bound
was not relaxed. The coordinator stopped further broad test runs.

Other limits remain:

- An uncertain owner retry still measures its two-minute delay against wall
  time. A backward clock jump can postpone that retry until the clock catches
  up. The hourly cap has a separate rollback fix.
- Closing a report and creating its fixed notice are separate store writes.
  A crash between them can omit the notice; that window is not repaired here.
- WhatsApp's pending fallback checks expire after fifteen minutes. A long sleep
  can prevent that late fallback check; the crash reservation does not extend
  provider history or this window.
- No real phone, email account, SMS account or GitHub comment is contacted.
- Real Codex and Claude permission prompts are not manufactured. Approval
  crash behavior has a side-effect regression and real customer-send approval
  coverage, not a live tool-permission round trip.
- The HTTP timeout regression shortens the response deadline to 100 ms; the
  successful slow-response regression delays a real HTTP response by 100 ms.
  Production adapter timeouts are unchanged.
- Filesystem corruption, disk exhaustion and power loss are not tested.
  SIGKILL transaction rollback is tested, including SQLite integrity checks.
- Multiple independent dashboard writers sharing one state directory are not
  supported by these proofs.

Read PASS, FAIL and BOUNDARY rows separately. A warning delivered about a stalled
worker is not proof that WatchTower fixed or parked its ticket.
