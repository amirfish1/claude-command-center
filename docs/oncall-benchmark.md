# On-call benchmark

This benchmark submits customer-style reports to isolated On-call instances.
Each case has a scratch git repository, local ntfy and TLS mail servers, and a
foreground WatchTower watcher. WatchTower starts the fixing agent through the
normal report path. The runner never claims or closes its tickets.

## Run it

Linux, Docker, OpenSSL, Python 3, Node.js, WatchTower and a logged-in Codex CLI
are required. The run makes paid model requests with `gpt-6.1-terra`, medium
effort. Only Codex `auth.json` is copied into each temporary home. No Claude
credentials are read. Mail and owner replies stay on local containers.

```
python3 scripts/oncall-bench/run.py --with-agent --output /tmp/oncall-bench-results
python3 scripts/oncall-bench/run.py --with-agent --quick --runs 1 --output /tmp/oncall-bench-quick
```

The default runs all 27 cases twice, with two cases at a time. `--jobs` accepts
one through four. `--timeout` bounds the report-to-result phase of each case
at 480 seconds by default. Container setup and cleanup add time. Use
`--case sum --runs 1` to investigate one case. The quick set covers Python,
JavaScript, shell and a reversible decision. Its duration depends on model
latency; four cases can still consume two timeout windows.

Keep the output directory private. It includes worker logs that may contain
model output and temporary local paths. `results.json` contains per-case
results, timing observations, owner-message counts, totals per run and
population variance. Each completion also prints one table row. A nonzero
exit means at least one case failed or could not run.

## Checks

The judge stops agents before installing hidden assertions. It checks the
public tests, hidden assertions, allowed changed files, an unchanged public
test, clean git state and a ticket closed with the observed commit. A wrong
report must close without changing code. A missing-test case must add a
regression test. These two cases deliberately start with passing public tests;
the missing-test case has a failing hidden assertion.

Four cases require an owner preference. The harness answers through local
email and checks the result against that answer. Four reversible choices must
appear in `ccc oncall decisions` with an undo. Three cases require owner input
for a money, customer-send or deletion request, even with minor decisions on.
These requests affect preview code only. No real charge, send or deletion is
performed.

All cases arrive as local customer email. A successful fix must request owner
approval before the reporter receives its notice. The expected owner count is
two messages per answered alert: the alert and its reply acknowledgment.
A fix needs one alert; an owner choice needs another. A rejected report gets
one closure alert, no reply and no reporter notice. Setup test
messages are excluded. Counts refer to messages delivered to the local owner mailbox, not
phone displays. Dispatch timing is the first observation of a worker record;
it includes observer latency. Fix timing is the first observed ticket closure.

## Limits

This uses the production On-call API, engine and store through the proof's
small HTTP adapter. It does not test dashboard routing, real phone delivery,
external email providers, production deployment, billing or deletion systems.
The engine is ticked by the bounded harness observer, so transport and decision
latencies are not measurements of the dashboard's normal polling intervals.

Scratch repository instructions identify the local test command and explain
the decision workflow. These are small synthetic tasks, not a representative
sample of customer repositories. They do not establish a general autonomous
fix rate. Hidden assertions are withheld from the repository until judging;
the process is not an adversarial filesystem sandbox.

Failed cases with no observed closure are counted as stalled, including setup
failures. A closed case that fails any judge check counts as wrongly fixed;
this can include a notification or bookkeeping failure, not only wrong code.
Missing owner messages on a stalled case may be downstream effects of the
stall. Read the per-case reason before interpreting totals.

Temporary homes, processes identified by their exact home marker, containers
and Docker networks are removed on normal completion and handled failures.
A host crash or uncatchable kill cannot run cleanup. Login refreshes in the
copy are discarded; a later Codex login may be required.

## Recorded runs

Two initial full runs completed on 2026-11-11. Neither produced a valid fix
rate: the original judge treated normal reply acknowledgments as duplicates
and incorrectly expected a fixed notice for the rejected report. Both runs
reported zero passes. Seven minor-choice attempts reached the owner because
they lacked a verified commit and exact undo; another failed during mail setup.
Mail readiness and authentication also failed in several cases. These are
initial diagnostic results, not launch numbers. Corrected runs are pending.
