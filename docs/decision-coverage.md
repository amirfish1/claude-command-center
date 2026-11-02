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

## Delivery staging

The internal answer outbox is separate from the coverage endpoint. It records
an answer and pending delivery atomically against an expected request revision.
It requires a conditional, idempotent source adapter; legacy card actions, local
questions and review gates cannot enter this path. There is no public answer
endpoint or automatic dispatcher for these records yet.

A lost response leaves delivery unknown. Recovery first looks up the source's
receipt by answer ID. Only authoritative absence permits another attempt using
the same answer ID. An accepted receipt proves source acceptance, not resumed
work or completed work. Definitive source rejection preserves the original
answer and allows a new answer against the current request revision.
