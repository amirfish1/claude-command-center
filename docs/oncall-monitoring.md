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
python3 scripts/oncall-monitoring-proof.py --logs --with-agent
python3 scripts/oncall-monitoring-proof.py --logs --logs-container --with-agent
python3 scripts/oncall-monitoring-proof.py --docker --with-agent
python3 scripts/oncall-monitoring-proof.py --alertmanager --with-agent
python3 scripts/oncall-monitoring-proof.py --sentry --backlog --burst 125 --clock-skew 86400
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
ccc oncall source add logs --path ./sample-app/error.log
ccc oncall source add logs --unit sample-app.service
ccc oncall source add logs --container sample-app
ccc oncall source add docker --set container=sample-app
ccc oncall source add alertmanager --set url=http://localhost:9093
```

Sentry and PostHog read the token from stdin. Do not put a token in shell history.
An HTTP source must pass its setup check before it can be added. The proof
starts healthy, adds the source, and then introduces the failure.

For logs, choose exactly one target. The CCC process needs read access to the
file, journal unit, or Docker daemon. Files must be regular files. Journal and
container names are passed as command arguments, not shell text. Docker health
monitoring needs a container health check, a restart policy, or a nonzero exit.
Alertmanager needs its HTTP API reachable from CCC. Authentication is optional;
use `--secret-stdin token` for a bearer token. The proof starts Prometheus and
Alertmanager on a private Docker network and loads a real `sample_ok == 0` rule.
No monitoring account is needed for these sources.

## Observed results

On November 11, 2026, the following passed locally:

| Source | Server | Observation |
|---|---|---|
| HTTP | Python 3.12 slim Docker image | Health request throws an assertion, returns 500, and writes an error log. One ticket, no duplicate next poll, automatic fix, unchanged passing test, HTTP 200 after fix, closure with commit, local ntfy notice. |
| Command | Python health-check command | The command fetches the Docker health endpoint and prints its real exception on failure. One ticket, no duplicate next poll, automatic fix, unchanged passing test, closure with commit, local ntfy notice. The Docker service also returns 200 after the fix. |
| Sentry | GlitchTip 4.2.0 and PostgreSQL 16 with pgvector | 500 distinct errors ingested through the Sentry store endpoint. Zero tickets at setup. One new exception produces one ticket. Automatic fix, unchanged passing test, closure with commit, local ntfy notice. |
| Logs, file | Python 3.12 slim Docker image | Existing log entry ignored. A real assertion traceback produces one grouped finding and one ticket. Automatic fix, unchanged passing test, no new error after a healthy request, closure with commit, local ntfy notice. |
| Logs, container | Docker logs from the same sample service | Real traceback intake, one ticket, no duplicate next poll, automatic fix, passing test, healthy service, closure with commit, local ntfy notice. |
| Docker | Docker health check on the sample service | Container becomes unhealthy. One ticket, automatic fix, healthy container, passing test, closure with commit, local ntfy notice. Restart loops and nonzero exits have regression tests, not separate real worker runs. |
| Alertmanager | Prometheus 3.2.1 and Alertmanager 0.28.1 | Real scrape and rule fire. One group produces one ticket. Automatic fix, passing test, rule resolves, closure with commit, local ntfy notice. |
| PostHog | Not yet verified against a real server | Contract tests only. Do not treat them as evidence of PostHog ingestion or agent repair. |

A separate Sentry run left 500 old issues alone and filed exactly 125 new
issues with the CCC source clock one day ahead. It took 26 HTTP calls across
bounded intake polls; the next poll used eight calls and filed no duplicates.
That burst run did not start agents.

Notifications use ntfy 2.17.0. The watcher runs in the foreground with a private
HOME and OS-assigned port. On-call files the ticket through its ordinary path;
the proof does not manually start a fixing worker. Source polling is forced
through the real engine, so this verifies routing, not the dashboard timer.
The proof adapter does not exercise dashboard routing or the preview gate.

A successful HTTP run took 34 seconds from report to fixed notice.
The health-command run took 95 seconds; Sentry took 102 seconds after ingestion. Image pulls,
GlitchTip migrations and the 500-error seed add setup time. Allow ten minutes
per command, or fifteen minutes for a seeded Sentry burst on a busy host.
File logs took 37 seconds from report to fixed notice; container
logs took 44 seconds, Docker health 86 seconds, and Alertmanager 89 seconds.
These are tiny seeded bugs, not a reliability estimate.

## Calls, backlog and failures

| Check | Observed behavior |
|---|---|
| HTTP | One request per poll. |
| Command | One process and one service health request per poll, no monitoring API requests. |
| GlitchTip with a slug | Two requests per list page: Sentry sort is rejected, then the compatible project route succeeds. At most four pages and five latest-event requests per poll, or 13 calls. The next poll resumes a remaining cursor. |
| Sentry baseline | Reads the newest 25 issues. A full first page saves a cutoff from the server Date header and issue timestamps so older unseen issues do not get filed when they reach later pages. Missing or invalid first-seen dates are ignored after such a baseline. |
| PostHog baseline contract | 500 groups take six queries, including the final empty page. A failed page saves no baseline. More than 2,000 groups stops setup polling with an error instead of filing part of the backlog. |
| PostHog regular contract | Up to four queries of 100 groups per poll, ordered by group key. A remaining offset resumes next poll. |
| Logs | File reads at most 256 KiB and consumes 64 complete lines per poll. Journal and Docker consume at most 64 records per poll. At most three grouped findings are emitted per poll; pending findings drain first. No HTTP calls. |
| Docker | One inspect command per poll. One bounded log command for a new incident only. No HTTP calls. |
| Alertmanager | One groups API request per poll; at most three new findings and three recovery checks. The API has no pagination here: over 100 groups, 1,000 alerts, or 1 MiB is a visible error. |
| Wrong token | GlitchTip rejects it; no baseline is saved. |
| Rate limit | A local fault endpoint returns 429. One request, no baseline, numeric and HTTP-date `Retry-After` delay further requests, including forced polls. A server Date header anchors HTTP dates despite host clock disagreement. This does not prove GlitchTip's own rate limiter. |
| Server failure | Injected 503 and a closed local socket preserve the missing baseline and expose an error. |

Sentry keeps a page cursor and PostHog keeps an offset to bound each poll.
PostHog pages are not a transactional snapshot: concurrent insertion or removal
can shift offsets. A later scan can recover a group still inside the query
window. Neither source guarantees capture after the provider removes evidence.
A full Sentry baseline without usable server or issue time fails safely.

HTTP and command findings identify an outage episode. A successful poll ends
it; a later failure produces another finding. Sentry uses the issue ID, so a
new event on an already filed issue does not file again. PostHog deduplicates
by group and UTC day, so an active group can be filed again the next day. None
of these sources marks a monitoring issue resolved when an agent commits.

Logs deduplicate by normalized error signature across future polls, including
after a fix. Traceback signatures include frames and the final exception.
Counts describe occurrences in the grouped batch, not a lifetime total. Excerpts
are scrubbed and limited to 3,000 UTF-8 bytes. Distinct errors with numbers that
normalize to the same signature can be grouped together. Rename rotation is
followed while the old inode remains in the same directory. Deleted or moved
rotated files cannot be recovered. Copytruncate followed by regrowth beyond the
previous offset between polls cannot be detected reliably.

Docker log replay uses timestamps and hashes because Docker provides no stable
record cursor. Setup rejects more than 1,000 records at the latest timestamp.
Same-timestamp replay is capped at 32 MiB; exceeding it reports an error without
advancing the cursor. This bound can require operator action. Command reads
also fail visibly on oversized or incomplete records and timeouts. The real
journal unit backend has not been exercised here; its cursors and bursts are
covered by local process fixtures.

Docker emits once while an incident remains bad. A healthy poll allows a new
incident. Alertmanager leaves existing groups alone at setup and groups new
alerts by receiver and group labels. Silenced or inhibited groups do not count
as recovery. When a group disappears, a ticket comment records recovery; it
does not close the ticket or claim a code fix. The normal worker path verifies
and closes the ticket. A later firing episode can produce another ticket.
This avoids racing a claimed worker with an unconditional WatchTower close.

## Scope of the tests

```
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

The regression tests reproduce the old Sentry first-page leak, GlitchTip's
422 sort response, and PostHog's incomplete baseline. They also check bounded
burst draining, clock skew, Retry-After, log rotation, secret scrubbing,
connection rechecks, and recovery semantics. PostHog tests exercise
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
