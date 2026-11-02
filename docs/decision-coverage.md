# Decision request coverage (shadow mode)

`GET /api/decision-inbox/coverage` compares cached WatchTower owner questions,
rationale approvals and human review gates with retained Decision Inbox cards.
It does not change the inbox, its counts, ticket state, answer routing or existing
Q/D references. It does not read session transcripts, launch a scan or call a model.

The response includes:

- `requests`: current observed owner requests, their source episode and revision,
  stable `request_id` when an explicit source generation exists, and possible
  legacy-card links. A ticket-reference match is labelled `same_ticket_only`;
  it is not proof that an old card asks the current question.
- `counts`, `excluded`, `source_errors`: observed requests and exclusions for
  dependency, external, worker and verifier waits, plus malformed source records.
- `source`: local snapshot freshness. Display snapshots are never asserted to
  be complete authoritative reads. An unavailable source does not prove there
  are zero pending decisions; a stale snapshot remains explicitly stale.
- `mode: "shadow"`, `llm_used: false`, and an installation identity.

Requests lacking a source episode are reported with `missing_generation` and
no canonical source ID. Editing a question within an episode preserves identity;
a new source episode gets a different identity even if its wording is identical.
Q numbers are readable aliases, not database keys or cross-machine identities.
If registry persistence is busy, `ok: false` and `registry.status: unavailable`
report the problem while retaining source-comparison rows and counts. No IDs
are manufactured and missing identity persistence is not treated as an empty inbox.

The endpoint reads the local `wt_items_snapshot.json` only, so it does not block
on a live queue provider or run `wt`. It writes only CCC's shadow registry in
`decision-inbox/requests.sqlite3`: installation identity, idempotent legacy card
imports and observed source requests. It does not rewrite `cards.json`, run logs
or the WatchTower snapshot. Existing card IDs, D numbers and question tags are
retained. Historical decided cards have unknown delivery status; importing one
never replays an action or proves its answer reached a source.

Missing source rows do not resolve or withdraw registry records: that requires
an authoritative source receipt. The endpoint returns only requests observed in
the supplied snapshot; historical registry records are not an actionable inbox.

## Authoritative local source

`GET /api/decision-inbox/source-coverage` reads WatchTower's conditional owner
export. It covers only the named local file-backed store. It does not prove
coverage for remote providers, reviews, or session questions. Duplicate followers
and review-stage records are excluded; rationale approvals stay read-only.

A successful export binds that adapter to the source's durable store UUID.
Source changes require explicit migration; a different store cannot reuse the
binding. Missing requests are marked withdrawn only when previously bound to
that same scope and absent from a successful, ordered full-source receipt.
Withdrawal never claims an answer was delivered. Unscoped shadow records and
legacy cards are preserved. Older or conflicting receipts cannot roll back the
registry. After binding, display-cache reads cannot overwrite canonical content
or reopen withdrawn records.

Unavailable or incomplete sources return HTTP 503 and `requests: null`, preserving
the last registry state. There is no fallback from failure to an empty inbox.
Existing stores without a durable ID need WatchTower's metadata-only
`wt answer-store-init --json`; the read-only endpoint never initializes the source.

## Shared request view

The Decisions tab in the main session navigation and `/decision-inbox.html`
share one view. Its Owner questions table reads
`GET /api/decision-inbox/requests`, which refreshes authoritative local coverage
and reads the saved registry in a read-only transaction. Ordinary session
questions remain local; only explicit promotion events enter this table.
Legacy reports retain their existing references and actions in a separate
Reports and monitoring section. A matching ticket tag never merges identities.
The main tab stays within the ordinary sidebar; the conversation pane remains
visible. Queue questions include ticket chips opening the existing ticket
detail view, with `/?ticket=EXAMPLE-1` as a durable navigation link.

The table distinguishes needs-answer, saved delivery and source withdrawal.
It shows exact question text and Pacific timestamps. When a source cannot be
checked, saved rows remain visible but `needs_answer_count` is null. Missing
source episodes appear as `coverage_issues` without manufactured request IDs;
they prevent a verified current count. The view does not initialize source
identities or answer questions while checking coverage. Source answers remain
disabled by default; the [opt-in controls](decision-answers.md#dashboard-controls)
use the separate answer API. Promoted session questions link back to their
conversation.

An explicitly initialized legacy owner wait carries `episode_provenance`.
Its timestamp is the actual initialization time, labeled **Episode initialized
at this time; historical block time is unknown.** It is not a reconstruction
of the original block date. The view preserves this provenance, exact question
and canonical identity; reading coverage never performs initialization.

## Delivery staging

The internal answer outbox is separate from the coverage endpoint. It records
an answer and pending delivery atomically against an expected request revision.
It requires a conditional, idempotent source adapter; legacy card actions, local
questions and review gates cannot enter this path. The [explicit answer
API](decision-answers.md) is disabled by default; there is no automatic
dispatcher for these records.

A lost response leaves delivery unknown. Recovery first looks up the source's
receipt by answer ID. Only authoritative absence permits another attempt using
the same answer ID. An accepted receipt proves source acceptance, not resumed
work or completed work. Definitive source rejection preserves the original
answer and allows a new answer against the current request revision.

The staged WatchTower adapter uses `answer-conditional` and `answer-receipt`.
It freezes the original, untrimmed question and entire current wait record,
including its revision token, separately from display text. Receipts must match
the ticket, immutable answer ID and exact submitted payload. Unsupported sources
are refused; it never falls back to an ordinary unguarded `wt answer` call.
Routing details remain separate from source acceptance and work completion.

## Typed producer reports

An external card may include `source_request` to link its explanation to an
exact owner-question episode. Ticket tags alone remain possible links and
never become canonical bindings. The typed object has:

- `version: 1`, `adapter: "watchtower-block"`, `origin_id`, `generation`, and
  `source_revision` from the source receipt.
- `source_snapshot`: the exact `store_id`, `blocked_at`, untrimmed
  `block_question`, `wait_kind: "owner"`, and entire `wait` object or null.
- `delivery_owner: "routine"` or `"ccc-outbox"`, naming the single bridge
  responsible for delivery. Declaring an owner does not activate that bridge.

CCC validates the link against its bound, open canonical question and retains
it as `card.source.source_request`, enriched with `request_id` and
`request_revision`. Producers may supply those two identity assertions too;
they must match exactly. Unsupported fields, other stores, changed snapshots,
withdrawn questions, rationale approvals and review gates are rejected before
a card is created. Source validation reads only CCC's registry, never a source
or transcript. A source can change after validation; answer delivery still
requires the conditional source check.

The binding is frozen. Reposting a different episode, revision or delivery
owner under an existing producer source ID is refused. Use a new explicit
report revision for content changes; a new report cannot transfer delivery
ownership. CCC reserves one owner per canonical request in its registry. The
answer outbox reserves `ccc-outbox` in the same transaction as the answer and
refuses routine-owned requests. Concurrent reports cannot claim different
owners for one question. Reservations survive a card-write failure so retries
must retain that owner; a later coordinated handoff is required to change it.
An old tag-only card is not silently rebound to today's
question. Source-bound titles and choices are also frozen on a repost; direct
spawn/inject options cannot bypass conditional delivery. Date-family heuristics
never resolve source-bound questions. Existing Q/D references and card IDs remain intact. No semantic
merge occurs. Outbox-owned cards refuse legacy card actions until the
canonical answer workflow is enabled. Routine-owned cards require the
corresponding conditional bridge to be activated; this metadata alone does
not make an old unguarded routine safe.
