# Escalation

Escalation is opt-in. With no steps, On-call keeps its existing delivery behavior.
Only alerts with `needs_reply` use the steps. Information messages do not escalate.
The preview flag remains off by default. There is no new settings screen.

First add channels with `ccc oncall channel add`. Give each person separate
channels. Their configured owner address or private ntfy reply topic establishes
identity under the existing [security limits](oncall-security.md).

```
ccc oncall person add backup --channels backup-chat
ccc oncall escalate add --channels owner-push --after 0
ccc oncall escalate add --person backup --channels backup-chat --after 10
ccc oncall escalate list
ccc oncall escalate rm 2
ccc oncall escalate test
ccc oncall person list
```

`--after` is minutes since enrollment, not minutes since the previous step.
Steps must be in time order. At most 32 steps and 32 people are allowed.
A person can own several channels. A channel can belong to only one person.
Channels not assigned to a person belong to the first owner.

A backup may answer `decision` and `permission` alerts by default. Set
`--may-answer decision,permission,question,plain_question` when adding a person
to extend that list. Customer sends require explicit `send,fixed` permissions;
being on the rota does not grant them. Those permissions include rewording a
message. All answers still pass the channel's sender and thread checks.
The first accepted answer closes the alert. A late answer says already handled.
Other contacted channels get one queued line naming who answered. The line waits
for quiet hours, pause and the cap. It gets one attempt, including an uncertain
attempt. A failed notice is not retried.

`person rm NAME` removes that person's channels too, so they cannot silently
become first-owner channels. Remove steps and rota references first. Local
configuration changes are trusted operator actions, not remote reply commands.

## Rotation

```
ccc oncall rota weekly owner,backup --weekday 0 --time 09:00
ccc oncall rota hours owner --start 8 --end 20
ccc oncall rota hours backup --start 20 --end 24
ccc oncall rota override backup --for-hours 12
ccc oncall rota list
ccc oncall status
ccc oncall rota clear
```

Weekly handovers and fixed hours use UTC. Monday is weekday zero. Weekly order
is anchored to Monday January 5, 1970 at the selected time; `status` shows the
current person. Fixed hours cannot overlap. Gaps fall back to the first owner.
An override expires at its stored UTC deadline. `clear` removes the whole rota.
Use `--person oncall` on a step to select the current person's channels when a
new alert is enrolled. `--channels` remains required as the owner fallback plan.
Existing alerts retain their enrolled plan across handovers and step edits.
Removing all steps disables escalation, including pending jobs.
Removing a channel prevents subsequent delivery on it.

## Waiting and recovery

Add `--quiet 22:00-07:00` to a person. Quiet hours use the server's local timezone.
A person's quiet hours replace the global quiet hours for that person's channels.
A quiet or failing first step does not hold up a later due step. Once a later
step is due, earlier unsent steps are skipped. The final step waits for its
quiet hours to end or for its channel to return. Pause holds all escalation.
The cap counts provider attempts conservatively, including uncertain sends.

Use `ccc oncall escalate urgent 7` to skip the wait for open alert 7.
An alert producer may also set `meta.skip_wait=true` for a hard-stop decision.
This makes the first step due immediately and the next step due on the next tick.
It does not bypass quiet hours, pause, the cap, or answer permissions. This flag
is not inferred from free text.

Plans and attempts live in SQLite. Before a send, an uncertain attempt is
committed. A crash or unknown provider result does not cause that attempt to be
repeated. Explicit failures can retry after two minutes until a later step is
due. This is conservative recovery, not exactly-once delivery. A backward clock
jump rebases remaining delays; a forward jump selects the latest due step rather
than replaying every missed step. Future-dated send records conservatively hold
the cap after a backward clock jump. A tick examines at most 50 due jobs and 50
notices. History is retained; no retention policy is added.

## Reproduce and limits

```
python3 scripts/oncall-escalation-proof.py --quick
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

The proof reuses the local protocol proof's temporary HOME, isolated environment,
Docker ntfy and TLS mail servers, timeout and cleanup. It uses two ntfy topics
and a mailbox as three people. It does not copy model credentials or start agents.
Both modes use a fast clock; `--quick` is an explicit alias for that mode.
A new process delivers the second step from persisted state. The second person's
reply travels through the real ntfy adapter. A test approval dependency records
the answer; no real permission prompt is approved. The proof checks mailbox
counts, quiet hours for the first person, and suppression of step three.
It does not prove delivery to phones, real provider identity or OS suspend.

`escalate test` uses the scheduler and rendered messages with a no-network
channel wrapper. It checks required fields, timing, quiet hours and the cap.
It does not verify credentials, provider formatting, reachability or receipt.
The live plan and store are untouched by this rehearsal.

Reply acknowledgement messages retain the pre-existing direct-send behavior.
They are not covered by the escalation cap or quiet-hour queue. Handled notices are queued before applying an answer and released only after
the alert reaches the answered state. The existing durable answer reservation
prevents a second application if the outcome of the first is unknown. Multiple
writers sharing one store, abrupt host failure and disk exhaustion are not proven.
