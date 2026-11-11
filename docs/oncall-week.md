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

## Cards and a page to share

```
ccc oncall week --card week.svg
ccc oncall week --card week.png --theme dark
ccc oncall week --card square.svg --square --theme light
ccc oncall week --page weeks.html
```

Cards are 1200 by 675 pixels, or 1080 by 1080 with `--square`. Light is the
default; `--theme dark` works for both cards and pages. SVG and HTML use only
Python's standard library. PNG needs Node and the repository's Puppeteer and
Chromium setup. If those are unavailable, the command says to export SVG.
No font download, external request, model call or hosted service is used.
System sans-serif fonts may look slightly different on different machines.

These exports request eight weeks, regardless of `--weeks`. The card leads
with the newest period and adds a report-count bar strip if an older period
has activity. `--since` can select a starting date; if it leaves fewer than
eight periods before now, only those periods appear. The page lists all
returned periods, newest first, and needs no scripts to read. It works at
390 pixels wide. Files are local exports; this command does not publish them.

The four numbers are reports received, tickets closed with a recorded commit,
distinct reply-required alerts successfully sent, and recorded automatic
decisions. “Questions that reached you” includes permission and reporter-send
approvals and other reply-required alerts. Sending one alert on two channels
counts once. It means provider acceptance, not proof that you read it.
“Fixed by agents” uses the queue's commit-bearing closure count; the store
does not prove the commit's author, deployment or correctness.

Dates on cards and pages are in UTC. The counts retain the API's interval
boundaries. Medians use the same recorded durations as the text export. Zero
means no matching stored records, including periods before On-call was set up;
it does not prove monitoring was running. A missing median says “not recorded”.

The renderer accepts only numeric fields and fixed labels. It ignores names,
addresses, source identifiers, ticket references and arbitrary metadata even
with `--detail`. Incomplete queue counts, unknown ticket outcomes, missing
fields or invalid numbers refuse the export. A failed render leaves an existing
destination unchanged. The output folder must already exist.

### WatchTower window reads

Newer WatchTower versions support `wt ls --closed-since`, `--event`, `--ref`
and a completeness envelope. Weekly counts use one union read per queue:
closures and decision events in the requested interval, plus tickets referenced
by reports in that interval. The response must be complete and contain at most
2,000 tickets. Older versions fall back to the existing capped all-ticket read.

These filters reduce returned data but currently filter in memory inside
WatchTower. The full-store scan is not removed. A persistent time index is still needed.
CCC does not pretend that a row cap bounds WatchTower's internal scan.

### Visual proof

```
python3 scripts/oncall-week-visual-proof.py
```

This uses temporary stores seeded with four weeks, an empty first week, and a
12,345-report week. It requires pytest for the shared fixture. It generates
both themes and shapes, an actual PNG export, and HTML pages. Puppeteer checks
all variants at 390 and 1200 pixels, records page errors and requests, checks
for overflow, and captures the results. The proof uses no live store or account.
Temporary databases and browser processes are removed. Exports and screenshots
are kept under the gitignored `.claude/screenshots/week/` directory for review.
