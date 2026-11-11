# Failed CI runs

Turn on the On-call preview and choose a code folder with `ccc oncall setup`.
From a GitHub checkout, run:

```
ccc oncall source add ci
```

The CLI reads the repository from `origin`. It asks for a repository only
when it cannot detect a GitHub origin. The default branch comes from the API.
No token question is required. The server uses its `gh auth token` login if
available, captured without printing it. Public repositories can work without
login. For a private repository, the credential needs Actions read access.
To use a saved Vault token instead:

```
ccc oncall source add ci --vault token=actions-read
```

To select branches, add `--set branches=main,release`. To use another Actions
server, supply its API root and repository explicitly:

```
ccc oncall source add ci --set repo=example/demo --set base=http://localhost:3000/api/v1 --vault token=local-actions
```

GitHub credentials are never forwarded to a custom server. The token remains
in the Vault. The source configuration contains only its entry name.

## What happens

Setup reads the current runs. Existing red runs are left alone until that
workflow becomes green. Later failures create a ticket for each workflow and
branch episode. The ticket includes the last observed green commit, first
observed failing commit, first failing job, failing step names, and up to 30
lines or 3,000 characters of its log. Unavailable logs do not prevent filing.

A consecutive red run does not create another ticket. A new red episode after
a green run can create another, but a failing commit already filed for that
workflow and branch is suppressed. Deduplication retains the last 2,000 IDs.
State survives a dashboard restart. Runs older than setup are ignored even
when they enter the visible page later.

A later observed green run marks the episode recovered. If its ticket is
still open, CCC attempts a normal `wt close --no-code` with the green run and
commit in its summary. It does not force another worker's claim. A rejected
close retries on the next successful poll. A green rerun alone is not called
a code fix. If a worker already closed the ticket with a fix commit, its
resolution is preserved. Owner closure notifications wait for observed green
CI, even when the worker closes its ticket early. Normal On-call channel and
notification settings apply.

The ticket tells the agent never to force-push, change workflow files to make
a run pass, or disable, delete, skip or weaken tests. These instructions are
not a sandbox. Repository protection remains the owner's responsibility.

## Bounds and limits

Each poll reads one page of at most 100 runs. It examines the newest completed
run per workflow and selected branch. It does not backfill older pages. A
busy repository can hide a failure or recovery between polls. Intermediate
red runs followed by green between polls are not filed. The source does not
verify commit ancestry through the API.

At most three new episodes get job details per poll. Each gets one jobs page
and one log request. With the cached default branch, this is at most seven
API requests, excluding HTTP redirects. The initial poll may also read the
repository. Polls use ETag on the runs page when the server supplies one.
Unprocessed episodes clear the ETag so enrichment can continue next poll.
The default interval is 300 seconds; the minimum is 30 seconds.

Responses are capped at 1 MiB. For larger logs the excerpt is from the end of
the fetched prefix, not necessarily the end of the full log. Known credentials,
common credential assignments, token formats, URL passwords and private-key
blocks are scrubbed before filing. Arbitrary secrets without recognizable
markers cannot be guaranteed absent. No raw logs are saved in source state.

## Reproduce the checks

```
python3 scripts/oncall-ci-proof.py --quick
python3 scripts/oncall-ci-proof.py
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

Quick mode reads two pages from the public `actions/runner` GitHub repository
without credentials. It checks run fields, pagination, rate-limit headers and
conditional requests. On 2026-11-11 it returned HTTP 304 for the conditional
request. This is read-only; it does not prove private-token permissions.

Full mode requires Linux, Docker, WatchTower and a logged-in Codex CLI. It
uses Gitea 1.25.1, act_runner 0.2.13 and ntfy 2.17.0 in disposable containers.
Only the fixing worker uses a copy of Codex auth.json. It uses
`gpt-6.1-terra` at medium effort. State, git configuration and the foreground
WatchTower watcher are isolated in a temporary home. Cleanup kills workers
by that exact home marker and removes containers and temporary files.
A host crash or an uncatchable termination can prevent cleanup.

On 2026-11-11 the full automatic path passed. Times below are UTC. Elapsed
times start when the isolated watcher was ready, after container setup.

| Event | UTC | Elapsed |
|---|---|---|
| Initial green run | 00:14:19.237 | 5.65 s |
| Source configured | 00:14:20.770 | 7.19 s |
| Real failing test made CI red | 00:14:25.138 | 11.55 s |
| One ticket filed | 00:14:32.313 | 18.73 s |
| Real Terra worker observed | 00:14:32.313 | 18.73 s |
| Ticket closed after observed green | 00:16:27.837 | 134.25 s |
| Pushed fix, passing test and owner ntfy notice verified | 00:16:28.295 | 134.71 s |

The ticket contained the real AssertionError log excerpt. The worker fixed
sum.py and pushed to the local server. The remote matched its new commit;
the red commit remained its ancestor. The workflow and test file were byte
identical, and the one-test suite passed. Only one ticket existed at the end.
Containers and temporary state were removed. Container readiness took another
18.46 seconds; allow up to ten minutes for the full proof, plus image downloads.
Quick mode took under one second on this run.

Two earlier attempts exposed real differences: Gitea requires authentication
for Actions reads on this public repository, and job steps were null. The
proof now supplies a local token through Vault, and the source handles null
steps. Gitea's failing step name therefore remained unavailable; the job and
log excerpt were present. No step name is fabricated.

Forgejo, private GitHub permissions, GitHub log download redirects, worker
crash recovery and server restart during a fix were not verified live. The
runner used host execution inside its disposable container, without a host
Docker socket. The proof drives the real On-call API and engine through a
local HTTP adapter and bounded observer; it does not run the dashboard server
or verify its preview routing. It proves one seeded bug, not general agent
reliability. Instructions and an ancestor check do not prove the worker never
used a harmless force flag; repository branch protection is not tested.

## Durable paging and delivery

The source now scans at most two run pages per poll and saves its page cursor.
New runs above the scan's starting ID wait for the next scan. Overlapping
pages are deduplicated. Finished scans drain in run-ID order, with at most
three findings enriched per poll. Two unfinished runs can be refreshed per
poll. The bound is ten API calls, plus one initial repository lookup, excluding
redirects. A sustained arrival rate above scan capacity delays intake; deleted
runs and server retention limits cannot be recovered.

A single stored journal commits the scan, episode state and pending findings.
The engine acknowledges a finding only after it has a ticket reference.
A crash before acknowledgement replays the same fingerprint, so WatchTower's
dedupe key reuses a ticket filed before the crash. Pending excerpts are scrubbed
before they enter the journal. These rules replace the earlier one-page limit
and the statement that source state never contains an excerpt.

Tests cover a 370-run burst with arrivals during paging, restart, failed
journal writes, replay before acknowledgement, and a real local HTTP redirect
to a different host. The redirected request did not carry Authorization.

## Setup and optional recheck

Inside a GitHub checkout with workflow files and a saved gh login, interactive
`ccc oncall setup` offers CI monitoring with one yes or no. It does not add a
second CI source on repeat setup. `ccc oncall source add ci` detects origin
without asking for repository or token. Setup now requires a login or Vault
token and reports missing workflows, disabled workflows and unreadable
repositories in one line. This replaces anonymous setup described above.

Add `--set recheck=yes` to wait for a second failed run or rerun attempt on the
same commit before filing. Repeated polls of one failed run do not count.
A successful run on that commit clears the candidate. If no rerun arrives,
no ticket is filed. This option is off by default.

## GitLab pipelines

Use `--set provider=gitlab --set repo=group/project --vault token=gitlab-read`.
The default API root is https://gitlab.com/api/v4; `--set base=...` overrides it.
Nested groups are supported. The adapter maps the documented pipelines, jobs
and trace endpoints to the same episode and recovery logic. Jobs have no
Actions-style steps, so the step is reported unavailable.

This was tested with synthetic recorded-shape fixtures based on the official
[pipelines API](https://docs.gitlab.com/api/pipelines/) and
[jobs API](https://docs.gitlab.com/api/jobs/). It was **not run against a real
GitLab server**. GitLab merge requests are not supported.
