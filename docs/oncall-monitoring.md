# Local monitoring proof

Run from a Linux checkout with Docker, Python 3, WatchTower, and a logged-in
Codex CLI. No running CCC instance is used. Each run creates private state,
a temporary git repository, and local Docker services. Containers and processes
are removed on exit. An uncatchable kill or host crash cannot run cleanup.

```
python3 scripts/oncall-monitoring-proof.py --http --with-agent
python3 scripts/oncall-monitoring-proof.py --command --with-agent
python3 scripts/oncall-monitoring-proof.py --sentry --with-agent --backlog
python3 scripts/oncall-monitoring-proof.py --posthog
```

Select one source per run. `--with-agent` permits paid model requests using an
isolated copy of Codex `auth.json`. The model is `gpt-6.1-terra`, medium effort.
It does not copy Claude credentials or Codex configuration. A refreshed copied
login is discarded; Codex may later require a new login. Other traffic stays local, apart from image downloads.
Without that flag, ticket creation is checked but no agent runs.

## Source setup

The script exercises these CLI commands through the real On-call HTTP API.
Substitute your own local addresses and folder when setting up a source.

```
ccc oncall source add http --set url=http://localhost:8000/health
ccc oncall source add command --set 'cmd=python3 -m unittest -v' --set cwd=./sample-app
ccc oncall source add sentry --set base=http://localhost:8000 --set org=proof --set project=sample --secret-stdin token
ccc oncall source add posthog --set host=http://localhost:8000 --set project_id=1 --secret-stdin token
```

The last two read the token from stdin. Do not put a token in shell history.
An HTTP source must pass its setup check before it can be added. The proof
starts healthy, adds the source, and then introduces the failure.

## Observed results

On November 11, 2026, the following passed locally:

| Source | Server | Observation |
|---|---|---|
| HTTP | Python 3.12 slim Docker image | Health request throws an assertion, returns 500, and writes an error log. One ticket, no duplicate next poll, automatic fix, unchanged passing test, HTTP 200 after fix, closure with commit, local ntfy notice. |
| Command | Python health-check command | The command fetches the Docker health endpoint and prints its real exception on failure. One ticket, no duplicate next poll, automatic fix, unchanged passing test, closure with commit, local ntfy notice. The Docker service also returns 200 after the fix. |
| Sentry | GlitchTip 4.2.0 and PostgreSQL 16 with pgvector | 500 distinct errors ingested through the Sentry store endpoint. Zero tickets at setup. One new exception produces one ticket. Automatic fix, unchanged passing test, closure with commit, local ntfy notice. |
| PostHog | Not yet verified against a real server | Contract tests only. Do not treat them as evidence of PostHog ingestion or agent repair. |

Notifications use ntfy 2.17.0. The watcher runs in the foreground with a private
HOME and OS-assigned port. On-call files the ticket through its ordinary path;
the proof does not manually start a fixing worker. Source polling is forced
through the real engine, so this verifies routing, not the dashboard timer.
The proof adapter does not exercise dashboard routing or the preview gate.

A successful HTTP run took 34 seconds from report to fixed notice.
The health-command run took 95 seconds; Sentry took 102 seconds after ingestion. Image pulls,
GlitchTip migrations and the 500-error seed add setup time. Allow ten minutes
per command. These are tiny seeded bugs, not a reliability estimate.

## Calls, backlog and failures

| Check | Observed behavior |
|---|---|
| HTTP | One request per poll. |
| Command | One process and one service health request per poll, no monitoring API requests. |
| GlitchTip with a slug | Two list requests per poll: Sentry sort is rejected, then the compatible project route succeeds. One new issue adds one latest-event request. At most five events are enriched. |
| Sentry baseline | Reads the newest 25 issues. A full first page saves a time cutoff so older unseen issues do not get filed when they reach that page. Missing or invalid first-seen dates are ignored after such a baseline. |
| PostHog baseline contract | 500 groups take six queries, including the final empty page. A failed page saves no baseline. More than 2,000 groups stops setup polling with an error instead of filing part of the backlog. |
| PostHog regular contract | One query, up to 100 groups. |
| Wrong token | GlitchTip rejects it; no baseline is saved. |
| Rate limit | A local fault endpoint returns 429. One request, no baseline, retry on the configured interval. `Retry-After` is not honored. This does not prove GlitchTip's own rate limiter. |
| Server failure | Injected 503 and a closed local socket preserve the missing baseline and expose an error. |

Sentry does not page through new-issue bursts. More than 25 new issues between
polls can be missed. The cutoff uses the CCC host clock, so clocks should agree.
PostHog regular polling also does not page. Its baseline pages are not a
transactional snapshot of a busy project.

HTTP and command findings identify an outage episode. A successful poll ends
it; a later failure produces another finding. Sentry uses the issue ID, so a
new event on an already filed issue does not file again. PostHog deduplicates
by group and UTC day, so an active group can be filed again the next day. None
of these sources marks a monitoring issue resolved when an agent commits.

## Scope of the tests

```
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

The regression tests reproduce the old Sentry first-page leak, GlitchTip's
422 sort response, and PostHog's incomplete baseline. PostHog tests exercise
the packaged query response schema through a local fixture. HogQL execution
is not tested.
GitHub and external accounts are outside this proof. No phone delivery,
production deployment, worker restart recovery, or large-codebase repair is
claimed.

## PostHog boundary

The inspected PostHog image was
`posthog/posthog@sha256:4cfebf62adddd747c448d5d65e545a858d03ec76ba6a8d73aa9b67190f738eff`.
Its packaged `posthog/schema.py` defines `HogQLQuery` and
`HogQLQueryResponse` with `columns` and `results`. Its HogQL grammar supports
`LIMIT ... OFFSET ...`. These are the contracts tested here.

The image is not a standalone ingestion server. Its service requirements
include PostgreSQL 15 or later, Redis 7, and ClickHouse 24.8 through 25.12;
ingestion also has Kafka configuration. The image omits the compose files.
Its bundled hobby installer clones a separate source tree, performs external
connectivity checks, and sends install telemetry to a third-party account.
That installer was not run because this proof permits only package downloads
and fixing-agent model requests outside the machine. A separately prepared,
isolated PostHog stack is still needed to verify ingestion and HogQL execution.
This run did not establish that local PostHog hosting is impossible.

`--posthog` runs an explicitly labeled local HTTP contract fixture. It adds the
source through the real CLI, baselines 500 groups, finds a new group, files a
real isolated ticket, and checks no duplicate on the next poll. It tests
injected 429, 503, and socket failure. It exits 2 to keep the real-server
boundary visible and refuses `--with-agent`. It does not claim a real PostHog
exception, real query execution, or an automatic fix from PostHog ingestion.
