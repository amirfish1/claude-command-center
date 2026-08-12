# Q2 Status Brief — phase 1 design

2026-08-12. Approved direction: mock option B (brief band in column 2, above the
ticket list, below the flow diagram). Phase 2 (interactive analyst chat, possible
promotion of the brief to own a column) is explicitly out of scope here.

## Problem

The q2 board shows a queue's mechanical state (backlog/watcher/worker/closed) but
nothing about the *content* of its tickets. On a queue like BYM-GH-FINIE (0 open,
14 needs-input), the owner has to open 14 tickets one by one to discover that ~8
of them are symptoms of one root cause (Arketa package-import credit ledgers).
The brief's primary value is identifying commonalities across tickets, then the
decisions the owner must make, then next steps. Reading order matters: the brief
must appear *before* the ticket list, not beside it.

## Backend (server.py)

**Cache.** One JSON file per queue: `COMMAND_CENTER_STATE_DIR / "queue-briefs" /
"<QUEUE>.json"` (queue validated by the same `_QUEUE_CONFIG_NAME_RE` used for
learnings). Contents:

```json
{
  "queue": "BYM-GH-FINIE",
  "generated_at": "2026-08-12T09:02:11Z",
  "model": "claude-sonnet-5",
  "duration_s": 41.2,
  "ticket_signatures": {"BYM-GH-FINIE-674": "needs_input|<updated-ts>", ...},
  "brief": {
    "headline": "≤120 chars",
    "summary": "2–4 sentences",
    "clusters": [{"title": "...", "refs": ["..-674"], "note": "..."}],
    "decisions": [{"refs": ["..-655"], "text": "..."}],
    "next_steps": ["..."]
  }
}
```

**Ticket selection.** Reuse `_ux_fixes_list_items_cached(None, None)`, filter to
the queue by the same project normalization q2.js uses (`_norm_project(item.project)
== _norm_project(queue)`), keep non-closed items. Signature per ticket:
`status|needs_input|<updated_at or created_at or "">`. Staleness = set/value diff
between cached `ticket_signatures` and current — the count of added, removed, or
changed refs is `stale_count` (drives "You had X ticket updates since").

**GET `/api/queue/brief?queue=X`** → `{ok, queue, exists, generating, brief,
generated_at, model, stale_count, ticket_count}`. Cheap: reads cache file +
computes signatures from the memoized item list. Never triggers generation.

**POST `/api/queue/brief/refresh`** body `{queue, force}` → starts generation on
a daemon thread, returns immediately `{ok: true, started: true}` (or `{ok: false,
error}`). Guards: per-queue in-flight flag (409-style error if already
generating); non-forced requests refused if `generated_at` is younger than 15
minutes ("cooldown") or if there are zero analyzable tickets. Manual ↻ sends
`force: true`.

**Generation.** Follow the existing `summarize_session_title` pattern:
`_resolve_claude_bin()`, `subprocess.run([bin, "-p", "--model",
"claude-sonnet-5", prompt], cwd=_SCRATCH_DIR, timeout=180)`. Prompt = queue name
+ per ticket: ref, title, status (+needs_input), age, body excerpt (~700 chars),
capped at ~24k chars total, with instructions to (1) lead with commonalities /
shared root causes across tickets, (2) list concrete decisions the owner must
make, (3) short next steps; output ONLY a JSON object matching the `brief`
schema above. Parse defensively (first `{` to last `}`), validate the shape,
write the cache file atomically. Failures are recorded in the cache file as
`{"error": ...}` alongside any previous good brief (an old brief + new error
must not lose the old brief).

## Frontend (static/q2.html, q2.css, q2.js)

**Placement.** New `<section class="q2-brief" id="q2Brief" hidden></section>`
between `#q2Diagram` and the `.q2-col-sub` search row.

**States.**
- *No queue / All-queues view / 0 analyzable tickets*: band hidden.
- *Generating*: "Analyzing N tickets…" single quiet line (no spinner theatrics);
  poll GET every 3s until done.
- *Brief present*: header row ("Status brief", "Last updated 12m ago", stale
  suffix "· 3 ticket updates since", ↻ button, collapse chevron), then headline
  (bold), summary, clusters (title + note, refs rendered as clickable chips that
  call `selectTicket(ref)`), decisions as cards with an orange left border,
  next steps as a compact ordered list. Height-capped (~40% of the column) with
  its own scroll.
- *Error*: one dim line with the error + retry link. If a stale-but-good brief
  exists, keep showing it with the error line beneath the header.

**Auto-generate policy** (err toward manual): on queue select, after GET —
auto-POST refresh only when (a) no cached brief exists, or (b) `stale_count >=
3`; at most once per queue per page load (JS `Set`); server cooldown backstops
runaway spend. Everything else is the manual ↻ (force).

**Collapse** state persisted per queue in `localStorage`
(`q2.brief.collapsed.<QUEUE>`), same idiom as the logbar height. Both themes via
existing CSS vars only — no hard-coded colors.

## Non-goals (phase 1)

Chat with the analyst, auto-regeneration timers, cross-queue briefs, mobile
polish beyond "doesn't break the existing mobile panel flow", tests beyond a
smoke check (single-user product; correctness of the brief itself is the
model's job, not tested code).
