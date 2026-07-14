# Productivity Dashboard Design

**Date:** 2026-07-14  
**Status:** Approved for implementation

## Goal

Add a separate Productivity dashboard that answers two related questions:

1. How much meaningful work did I deliver?
2. What did I work on, by day and by project?

The dashboard uses observable evidence rather than claiming to measure an
abstract personal-productivity score. It combines shipped work, Git activity,
agent activity, computer-presence samples, and WatchTower history. The first
view covers the most recent week, while the same persisted dataset supports
6-, 8-, 12-, and 16-week trend views.

## Product boundary

Productivity is a standalone `productivity.html` page. The existing Throughput
page remains focused on tokens and weekly-limit consumption. The two pages may
link to each other, but they do not share navigation state or overload the same
charts with unrelated meanings.

The dashboard covers every Git repository CCC has observed. Repositories come
from CCC's recent/custom repository lists, recent conversation metadata,
WatchTower queue configuration, and the server's current repository. Worktrees
and duplicate clones of the same remote repository are grouped into one
project. Every aggregate can be broken down by project.

## Metric semantics

### Delivered work

The primary outcome is **delivered work items**, split into features and fixes.

- A conventional `feat:` commit reachable from a remote-tracking ref is a
  shipped feature.
- A conventional `fix:` commit reachable from a remote-tracking ref is a
  shipped fix.
- A closed WatchTower feature or bug ticket is also a delivered item.
- A WatchTower ticket and commit that share the same ticket reference count as
  one delivered item, not two.
- Raw commit count remains visible independently. Other commit types contribute
  to commits and changed lines but not to features or fixes.

Local Git cannot recover the exact historical time at which a commit was
pushed. Therefore, "pushed" means a commit authored by the configured local Git
identity, now reachable from a remote-tracking ref, bucketed by its committer
date. The UI states this proxy explicitly.

Each delivered item retains a compact evidence record: project, date, kind,
title, commit SHA or WatchTower reference, and source. These records power the
"What I worked on" lists rather than reducing the page to counts.

### Code and agent activity

Per day and per project, the dashboard reports:

- commits present on remote-tracking refs;
- added, deleted, and total changed lines from Git numstat;
- assistant turns;
- raw context plus output tokens, using the existing throughput normalizer;
- gross agent runtime, the sum of all turn durations;
- net agent runtime, the union of overlapping turn intervals;
- parallel agent time, gross runtime minus net runtime;
- human-triggered prompts and an observed work-time estimate.

Observed work time clusters human prompt timestamps into activity sessions.
Prompts separated by at most 30 minutes belong to the same session; every
session receives a five-minute tail so a single prompt is not a zero-duration
work period. This is an estimate of CCC-observed work, not a claim about all
computer activity.

### Computer presence

CCC begins sampling operating-system idle time once per minute while the server
is running. A minute is active when idle time is below five minutes. Samples are
stored locally and never committed or transmitted.

The page reports:

- sampled computer-active minutes per day;
- sample coverage;
- focus hours, defined as clock-hour buckets containing at least 45 active
  sampled minutes.

macOS uses the built-in `ioreg` command. Unsupported systems show the CCC work
estimate and an explicit "presence sampling unavailable" coverage state. No
historical computer-presence values are invented for time before sampling
began.

### WatchTower

WatchTower items contribute daily opened and closed counts. Ticket project,
type, title, reference, creation time, and closure time are retained as evidence.
Queue items without a recognized feature/fix type still count toward opened and
closed totals, but not toward delivered features or fixes.

## Trend interpretation

The page offers 6-, 8-, 12-, and 16-week ranges. Weekly buckets show delivered
features, delivered fixes, commits, changed lines, tokens, work time, agent
runtime, and active projects.

"Trending up" is based on delivered work items per week. The dashboard compares
the average of the newest half of the selected range with the oldest half and
reports the relative change. It also reports the least-squares weekly slope so
the calculation is inspectable. A change within 10% is labeled flat.

The page does not claim agents caused a trend. It reports:

- agent leverage: gross agent minutes per observed work minute;
- parallelism saved: gross minus net agent minutes;
- the Pearson association between weekly agent runtime and delivered items,
  labeled as association rather than causation;
- delivered items per million tokens and per observed work hour.

Association is omitted when fewer than four non-empty weekly observations exist
or either series has no variance.

## Architecture

### `productivity.py`

A new stdlib-only module owns the isolated, testable data model:

- Git identity discovery and remote-reachable commit parsing;
- conventional-commit and WatchTower classification/deduplication;
- interval union, daily splitting, activity-session estimation, and focus-hour
  calculations;
- daily/project aggregation and weekly trend calculations;
- a local SQLite store for cached payloads and computer-presence samples.

The module never imports `server.py`. It accepts normalized repository,
conversation-turn, and WatchTower inputs so its calculations can be tested with
small fixtures.

### Server integration

`server.py` owns integration with existing CCC sources:

- discover known repositories and normalize project identities;
- reuse the throughput transcript cache and parsers for Claude and Codex turns;
- read WatchTower items through the existing backend abstraction;
- call the productivity aggregator and publish its payload;
- start the lightweight presence sampler with the other server background
  services.

`GET /api/productivity` returns the persisted 16-week dataset and refresh
metadata. An optional `refresh=1` requests a rebuild. When a cached payload
exists it is returned immediately, marked stale while a background refresh is
running. The first uncached request starts a refresh and returns a building
state that the page polls. Concurrent requests share one refresh job.

The additive response contains:

- `range`: available and selected week ranges plus local date boundaries;
- `summary`: selected-range totals and derived ratios;
- `daily`: one row per local calendar day;
- `weekly`: one row per local calendar week;
- `projects`: project totals and evidence summaries;
- `deliveries`: compact feature/fix evidence;
- `trends`: delivery change, slope, and agent association;
- `coverage`: Git identities, transcript coverage, presence-sample coverage,
  unavailable sources, and proxy explanations;
- `refresh`: cache generation time and current refresh state.

### Persistence and refresh

The SQLite database lives under CCC's per-user command-center state directory.
It contains a schema version, the latest full payload, source fingerprints, and
minute-level presence samples. Presence samples older than 18 weeks are pruned.

The expensive rebuild is bounded to 16 weeks and runs off the request thread.
Git repositories are processed once each, transcript parses reuse the existing
`(path, mtime, size)` throughput cache, and WatchTower items are read once per
rebuild. The final payload is written atomically in one transaction. A refresh
failure leaves the last successful payload intact and exposes the failure in
refresh metadata.

## User interface

The standalone page prioritizes inspectable data over decorative polish:

1. A range selector and cache/refresh status.
2. Outcome cards for features, fixes, commits, and WatchTower closures.
3. Time and agent cards for observed work, computer presence, focus hours,
   gross/net agent runtime, turns, and tokens.
4. A weekly trend table/chart with an explicit up/flat/down explanation.
5. A project table answering what was worked on and showing project-level
   features, fixes, commits, lines, turns, tokens, and time.
6. A daily evidence table with expandable work-item titles.
7. A coverage panel documenting missing data and every important proxy.

The main dashboard and Throughput page link to Productivity. The page works at
desktop and mobile widths, uses semantic tables/buttons, and does not require a
bundler or third-party JavaScript.

## Error handling and privacy

- One unreadable repository does not fail the whole dashboard; it appears in
  coverage warnings.
- A malformed transcript, Git record, or WatchTower item is skipped and counted
  in coverage.
- Missing Git identities produce repository activity without falsely assigning
  other authors' commits; the repository is marked unavailable for authored
  commit metrics.
- Absolute repository paths and personal Git identities are not rendered in the
  browser payload. Stable project identifiers are derived locally.
- The cache contains local activity metadata and follows CCC's existing
  single-user plaintext-at-rest posture.
- No data is sent over the network and no external analytics service is added.

## Verification

Automated tests cover:

- conventional-commit classification and WatchTower/commit deduplication;
- Git-log numstat parsing, author filtering, and duplicate commit removal;
- interval union across parallel agents and local-day boundaries;
- observed work sessions, presence coverage, and 45-minute focus hours;
- daily/project/weekly aggregation and trend/association calculations;
- SQLite cache persistence and schema migration behavior;
- the additive `/api/productivity` route and static page wiring.

Run the focused productivity tests, the full smoke suite, `node --check` for any
modified JavaScript, and the repository's Puppeteer harness against the new
page. Visual verification covers populated, empty, building, stale, partial-
coverage, desktop, and mobile states.

## Acceptance criteria

The dashboard is complete when a user can select any supported range and answer:

1. How many features and fixes did I deliver each day and week?
2. Which projects and named work items produced those totals?
3. Are delivered work items trending up, flat, or down?
4. How do commits, changed lines, turns, tokens, work time, and agent runtime
   move alongside that trend?
5. How much agent runtime overlapped, and how much gross agent time ran in
   parallel?
6. How much sampled computer-active time and how many 45-minute focus hours are
   available, with honest coverage?
7. How many WatchTower tickets were opened and closed?
8. Which values are measured, estimated, unavailable, or based on a proxy?
