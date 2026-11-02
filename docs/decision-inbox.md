# Decision Inbox

[Back to the README](../README.md) · [Queues](QUEUES.md)

Once an hour, CCC checks for stalled work and uses an analyst model to prepare
three-option cards. You choose an option; CCC spawns or steers the follow-up.
Open **Decisions** from the app rail or `/decision-inbox.html`.

## Browsing decisions

The inbox shows ten compact rows per page. Expand a row to read its context,
created/updated timestamps, and options. Browse numbers reflect the current
filtered order; question references (such as Q123) and card IDs stay attached
to their original items. Search accepts those references, ticket names, and
text from the title or context. Filter by status or source and sort by creation
or update time. History includes all retained closed items.

Dates use the browser's local timezone, named above the list. Feed refresh and
scanner time are shown separately: a successful scan does not prove that every
older decision still applies. Connection failures and overdue scans are visible.

## Why an item appeared

**Sweep history** is a table with exact start/end and decision creation times
in Pacific time (`America/Los_Angeles`, automatically PST or PDT). Each row
lists added decision references, titles and source IDs, with an explicit **LLM used** column.
Click a reference to open that exact inbox item. Existing Q-references stay the
same; cards without one get a persistent local D-number, shared with the inbox
list and searchable there. These numbers remain fixed when sorting or filtering.
Empty script-only sweeps are hidden; runs that attempted an LLM request remain
visible even when they added no decision. Pagination applies after filtering.

New runs record request counts, including retries and failed requests; a missing
executable does not count as a request. Older usage is inferred from retained
analyst records and labeled historical; unavailable usage is **Unknown**.
Sources and creation times for older decisions come from their original retained
card when available. Reading history never starts work or chooses an option.

`GET /api/decision-inbox/runs?offset=0&limit=10` keeps the original unfiltered
`runs` and `total` fields and adds filtered `activity_runs`, `activity_total`,
and `hidden_count`. Run records include `llm_used` and `llm_usage_basis`;
new records also include `llm_calls`. Limits are clamped to 1–100.
Malformed log lines are counted in `invalid_records` and skipped.

On mobile, open **More → Decisions** in CCC. The More menu includes the same
app catalog as the desktop rail. Apps keep the bottom bar visible so you can
switch apps or return to sessions. Direct app links also open in this shell.

For remote mobile access, open the same remotely accessible inbox URL on the phone.
If that URL uses a private network connection, connect the phone to that network
first. Bookmark the page or add it to the phone's home screen. A localhost URL
points to the phone itself and cannot reach a dashboard on another computer.

## What it checks

- **Strategy board, optional:** a Markdown table with `Task | Status | ETA`
  columns. Blocked rows and open rows past their ETA become candidates.
- **WatchTower queues:** open tickets older than `wt_age_days`, with one
  `wt status --json` per run and one `wt ls` per surfaced queue.
- **Idle sessions:** live sessions idle longer than `idle_hours` with unfinished
  work, such as a pending tool or open goal.

The token governor checks live sessions in the same pass. It flags repeated
tool errors, 45 minutes of work without file edits, or 85% context usage with
no compaction. Choose **Nudge**, **Pause**, or **Kill** for a flagged session.

There are at most five new cards per run. A source with an open card, or one
decided or dismissed in the last week, is skipped.

![Decision Inbox with token-governor controls and three-option cards](images/decision-inbox.png)

## Configuration

Save settings in `~/.claude/command-center/decision-inbox.json`:

```json
{
  "strategy_board": "~/notes/Strategy Board.md",
  "interval_s": 3600,
  "max_cards_per_run": 5,
  "idle_hours": 2,
  "wt_age_days": 3,
  "model": "claude-sonnet-5",
  "spawn_cwd": "~/projects"
}
```

Set `CCC_DECISION_INBOX_DISABLED=1` to disable the loop. Cards and run history
live in `~/.claude/command-center/decision-inbox/`.

## File cards from another tool

A digest, monitor, or scheduled script can post to `POST /api/decision-inbox`
(alias `/api/decision-inbox/cards`):

```json
{
  "source": "digest",
  "source_id": "example:check",
  "title": "Review the stalled task",
  "detail": "The task needs a decision before it can continue.",
  "severity": "warn"
}
```

The reply is `{ok, card_id, deduped}`. Cards deduplicate by `source:source_id`,
so repeats keep one open card and count repeated reports. Pass `options` for
custom choices; omit them for acknowledge, spawn an investigator, or snooze
for 24 hours.

Read with `GET /api/decision-inbox`; actions use
`POST /api/decision-inbox/{run,decide,dismiss,governor}`.
