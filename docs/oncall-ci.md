# Failed CI runs

From a GitHub checkout with workflows and a saved `gh` login:

```
ccc oncall setup
ccc oncall source add ci
```

Interactive setup offers CI monitoring with one yes or no. If you accept,
there is no need to run the second command. Repeat setup does not add another
CI source. `source add ci` reads the repository from `origin` without asking
for repository or token. The server captures its `gh auth token` result without
printing it. It never starts a login flow for you.

To use the Vault instead:

```
ccc oncall source add ci --vault token=actions-read
```

Setup requires a login or Vault token. It reports missing workflows, invalid
login, an unreadable repository and disabled Actions in one line. GitHub's
Actions policy endpoint needs extra permission; if that check is forbidden,
setup checks the accessible workflow states instead. A masked private-repo
404 cannot distinguish a missing repository from denied access.

For another server, name the provider and its API root:

```
ccc oncall source add ci --set provider=gitea --set repo=example/demo --set base=http://localhost:3000/api/v1 --vault token=local-actions
ccc oncall source add ci --set provider=forgejo --set repo=example/demo --set base=http://localhost:3000/api/v1 --vault token=local-actions
```

Gitea required a token even for the public repository in the local proof.
GitHub credentials are not forwarded to custom servers. Configuration stores
only the Vault entry name. The default branch comes from the repository API.
Use `--set branches=main,release` to watch named branches instead.

## Findings and recovery

Runs already red at setup are left alone. Later failures create one ticket
per workflow, branch and failure episode. Consecutive red runs share that
episode. The ticket names the last observed green commit, failing commit,
first failing job, failing steps when supplied, and a scrubbed log excerpt.

A later green run ends the episode. A new failure after green can create
another ticket, but a failing commit already filed for that workflow and
branch is suppressed. Deduplication retains 2,000 fingerprints. A green rerun
alone does not prove that code was fixed.

When green appears, CCC closes an unclaimed open ticket with `--no-code` and
the green run in its summary. It leaves an active worker's claim alone so the
worker can record its fix and validation. Failed closes retry. A worker's
existing resolution is preserved. Owner closure notifications wait for
observed green CI even when a worker closes its ticket early. Normal On-call
channel settings still apply. CCC does not merge or close a pull request.

Tickets forbid force-pushes, workflow changes made to pass CI, and disabling,
deleting, skipping or weakening tests. These are agent instructions, not a
sandbox or a substitute for branch protection. Logs and job names are marked
as untrusted evidence.

## Pull requests

```
ccc oncall source add ci --set pull_requests=yes --set pr_allowlist=dependency-bot
```

The allow-list uses exact author logins, separated by commas. The run must
match the current head commit of an open PR in the same repository. Forks,
unknown authors, closed PRs, merge-only SHAs and `pull_request_target` runs
are excluded. Only the first 100 open PRs are checked. GitLab merge requests
are not supported.

PR tickets name the only branch the worker may push. They forbid pushes to
the default branch, merging and closing the PR. They require the worker to
check the PR again before pushing. Green on the default branch cannot close
a PR episode. If the PR closes before recovery is observed, its CI ticket
needs a separate resolution.

## Optional flaky-run check

Add `--set recheck=yes` to wait for a second failed run or reported rerun
attempt on the same commit. Polling the same failure twice does not count.
A green run on that commit clears the candidate. With no second run, no
ticket is filed. This option is off by default. Servers that do not report
attempt numbers need a new run ID for the second observation.

## Bursts, restarts and bounds

The source scans at most two 100-run pages per poll and saves its cursor.
New runs above the scan's starting ID wait for the next scan. Overlapping
pages are deduplicated. Completed scans drain in run-ID order, enriching at
most three findings per poll. Up to two unfinished runs are refreshed per
poll. A deleted unfinished run does not block intake of newer runs.

The bound is ten API calls per poll, or eleven with PR intake, excluding
HTTP redirects. The first poll can also read repository metadata. Setup has
separate login and workflow checks. The runs page uses ETag where supplied.
The default interval is 300 seconds; the minimum is 30 seconds.

A single journal commits the cursor, episode state and pending findings.
The engine acknowledges a finding only after it records a ticket reference.
A crash before acknowledgement replays the fingerprint. WatchTower's dedupe
key reuses a ticket created before the crash. The journal stores run metadata
and scrubbed pending excerpts; it never stores credentials or raw log bodies.

An arrival rate above scan and enrichment capacity delays intake. Deleted
runs, server retention limits and arbitrary server reordering cannot be recovered.
The source does not verify commit ancestry through the API. A live proof's
ancestor check is a separate observation.

Responses are capped at 1 MiB. Log excerpts contain at most 30 lines or 3,000
characters. For a larger log the excerpt comes from the end of the fetched
prefix, not necessarily the full log's end. Known credentials, common secret
assignments, token formats, URL passwords and private keys are scrubbed.
Arbitrary unmarked secrets cannot be guaranteed absent. Missing logs or steps
are stated rather than invented. Gitea and Forgejo job lists did not supply
step details in these checks.

## GitLab pipelines

```
ccc oncall source add ci --set provider=gitlab --set repo=group/project --vault token=gitlab-read
```

The default API root is https://gitlab.com/api/v4; `--set base=...` overrides
it. Nested groups are supported. Pipelines, jobs and traces use the same
episode and recovery logic. There are no Actions-style steps.

Tests use synthetic recorded-shape fixtures based on the official
[pipelines API](https://docs.gitlab.com/api/pipelines/) and
[jobs API](https://docs.gitlab.com/api/jobs/). This adapter was **not run against
a real GitLab server**. Private GitHub permissions were not tested live.

## Reproduce the proofs

```
python3 scripts/oncall-ci-proof.py --quick
python3 scripts/oncall-ci-crash-proof.py
python3 scripts/oncall-ci-proof.py --pull-request
python3 scripts/oncall-ci-proof.py --provider forgejo
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

Quick mode reads two pages from the public `actions/runner` repository without
credentials. It checks run fields, pagination, rate headers and conditional
requests. It took under one second and received HTTP 304 on 2026-11-11.
A separate test downloads a log through two real local HTTP servers and
verifies that a redirect to another host does not carry Authorization.

The crash proof uses a real isolated WatchTower store without an agent. It
kills intake immediately after `wt add` returns but before the ticket reference
is saved. A fresh process recovers one ticket, records its reference and
acknowledges the finding. This passed. Tests also cover a 370-run burst with
new arrivals during paging, restart, failed journal writes and repeat delivery.

Full proofs require Linux, Docker, WatchTower and a logged-in Codex CLI. Each
runs one paid `gpt-6.1-terra` worker at medium effort. Only Codex auth.json is
copied. Containers, HOME, state, git configuration, watcher, dashboard and
control-plane process are temporary. The runner executes inside its own
container without a host Docker socket. The server's temporary data mount
allows execution because Git hooks need it.

The real dashboard HTTP routes configure On-call. Its normal timer detects
the red run and sends the notice; the script never calls tick or substitutes
an API adapter. The proof restarts that dashboard between red and green.
A bounded observer checks one ticket, the real worker, the pushed commit,
green CI, ticket closure, ntfy delivery, unchanged workflow and test bytes,
and a passing one-test suite. PR mode also checks that the default branch
is unchanged and the PR is still open with the fix on its head branch.

The child deadline is ten minutes, plus image downloads and server startup.
Cleanup stops processes by their exact temporary HOME, removes containers and
deletes temporary files, including on failure. A host crash or uncatchable
termination can prevent cleanup. A renewed temporary Codex login is discarded;
a later sign-in may be needed. No real dashboard or watcher is restarted.

## Observed results

Both automatic paths passed on 2026-11-11. Times below are UTC; elapsed times
start after the isolated dashboard became ready.

| Event | Gitea 1.25.1 PR, runner 0.2.13 | Forgejo 17.0.1, runner 13.2.1 |
|---|---|---|
| Initial green | 01:02:10.573, 6.78 s | 00:59:20.818, 13.46 s |
| Source configured | 01:02:17.776, 13.98 s | 00:59:28.277, 20.92 s |
| Red run | 01:02:23.224, 19.43 s | 00:59:43.792, 36.44 s |
| Ticket reference recorded | 01:03:00.803, 57.01 s | 01:00:26.061, 78.71 s |
| Dashboard restarted | 01:03:09.683, 65.89 s | 01:00:50.371, 103.02 s |
| Worker observed | 01:03:09.726, 65.93 s | 01:00:25.055, 77.70 s |
| Ticket closed after green | 01:04:21.717, 137.92 s | 01:02:04.659, 177.30 s |
| Fix and owner notice verified | 01:04:24.291, 140.50 s | 01:02:05.897, 178.54 s |

The Gitea fix was `36b473676f47304c29a14944b00ce1834d078e08` on
`deps/update`. The default branch stayed unchanged and the PR stayed open.
The Forgejo fix was `1a556b8ffa9ba7ea4151dc633a46dd5bd9abdd3d` on main.
Both preserved the workflow and test file and delivered a local ntfy notice.
All proof containers and homes were removed.

An earlier Forgejo observer labeled a persisted report as filed before its
ticket reference appeared. The observer was corrected and the full proof was
rerun; the table above records that final run.
Earlier attempts also exposed null Gitea steps, a forbidden config-field name,
Forgejo's different response shapes, slow server initialization and a noexec
temporary mount. The implementation or proof was corrected for each.

These runs prove one seeded bug per server. They do not establish general
agent reliability, fork support, branch protection, or that every worker
command avoided a harmless force flag. The proof verifies normal commit
ancestry and unchanged protected files. GitLab remains fixture-only.
