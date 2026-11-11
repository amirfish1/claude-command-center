# The week in On-call

```
ccc oncall week
ccc oncall week --json
ccc oncall week --since 2026-11-01 --weeks 4
ccc oncall week --detail
ccc oncall week --post
ccc oncall week --post markdown
```

The local-only `GET /api/oncall/week` endpoint accepts `since`, `weeks`, and
`detail` query parameters. It reads saved records even when On-call or its
preview is off. The dashboard must be running. It does not enable On-call.

By default, two consecutive seven-day windows end at the current time.
`--since` starts the first window at an ISO date or timestamp. `--weeks` caps
the number of windows, from 1 to 52. Windows include their start and exclude
their end. A date without an offset uses the server's local timezone, including
its daylight saving rules. An explicit offset stays fixed. Results are newest
first. JSON timestamps and durations are seconds.

Reports are grouped by their stored source and channel. Filed and still-open ticket counts describe
the reports received in each window and their current status. Fixed and closed
counts use closure time, including reports received in earlier windows. A ticket
is counted once per window even if several reports link to it. A fix means a
closed ticket with a commit recorded by WatchTower, excluding duplicates. It
does not prove deployment or that the fix worked. Fix times start at the first
stored report and end at the recorded closure. Missing timestamps are
excluded from the median and maximum, rather than treated as zero.

Held counts use report approval alerts. Released and dropped counts use answered
approval alerts. Owner messages count successful channel deliveries by alert
kind, plus direct replies recorded after this feature was installed. A message
sent on two channels counts twice. Provider acceptance does not prove receipt.
Older direct deliveries have no owner/reporter category and are excluded.
Replies count answered owner alerts, with time measured from first successful
send. Extra replies that did not answer an alert are not included.

Decisions and reversals use WatchTower history event times. Reversals are counted
in the window when undone, even if the decision was made earlier. Reporter asks
use message creation time; approval, rewording and refusal use the owner answer
time. New approvals are recorded even when delivery then fails. Older approvals
can only be counted when the stored alert was answered. A later owner edit or
retry replaces that message's earlier approval record. Failed deliveries count
attempts, including reporter sends.

Default output is plain text. `--json` returns structured counts. Neither
includes reporter addresses. `--detail` adds
reporter addresses. Both post formats contain only fixed labels, dates, counts
and times, even with `--detail`. They omit source identifiers, ticket references,
message text and addresses. Review the period and the definitions above before
using counts in a post. Outcomes can change when a ticket is later reopened.

Reads use indexed time ranges and a SQLite instruction budget. They stop with an
error above 20,000 records per table per window, 2,000 tickets per watched queue,
or 16 queues. WatchTower is read once per queue, with no per-ticket processes.
Its CLI may scan its own whole store before returning the bounded result.
Unavailable WatchTower is marked in `limits`; post export refuses incomplete
queue results. Retained records cannot recover deleted history.

## Daily summary

Settings > On-call has one **Daily summary** switch and one time field. It is
off by default. The time uses the server's local timezone. With On-call enabled,
the next tick at or after that time creates at most one summary for that date.
It gives yesterday's reports and fixes, what agents decided without you, the
current number of waiting questions and held reports, and messages sent to you
with failed deliveries, in at most five short lines.

It uses the existing alert channels, quiet hours, pause control, hourly cap and
retry rules. An unsent summary expires when the next day's summary is prepared.
It does not catch up missed dates. No message is created when there is no
activity and nothing waiting. A summary alone does not count as new activity.

The same settings can be patched through the local config API:
`{"daily_summary":{"enabled":true,"time":"09:00"}}`.

## Checks

`python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py` checks the
seeded counts and the existing On-call behavior. The weekly tests include a
100,000-report store, time boundaries, private exports, delivery limits and
the local-only handler with the preview off.

`node scripts/oncall-week-ui-proof.js` loads the real settings component with
a local fixture API in Puppeteer. It checks the default, switch and time field.
An optional file argument saves a screenshot. This checks component behavior,
not a live dashboard or delivery to an external account.
