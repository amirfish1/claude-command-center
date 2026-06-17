# Attention API — plumbing for the COO sweep

Infrastructure only. No opinion/recommendation/auto-answer layer — the live COO
agent consumes these feeds and makes the calls. This doc maps each deliverable
to its code site in `server.py` / `static/`.

## The bug this fixes

CCC's "Needs Your Attention" (NYA) keyed off `question_waiting` / `needs_approval`.
Those flags ONLY fire on the formal `AskUserQuestion` tool and permission prompts.
Real agents almost never use those — they **end a turn with a prose question**
("paused for your review", "want me to…", "pick one of these", "plan before I
write anything"). Ground truth on 14 live sessions: ~7-8 needed the human; the
flags caught ZERO. NYA is structurally blind to the most common block type.

## Deliverable 1 — soft-block detector

A transparent, tunable **scored heuristic** (`server.py`):

- `_strip_for_question_scan(text)` — drop the `<session-state>` block and the CCC
  prompt trailer, return the trailing human-facing prose.
- `_score_soft_block(text)` → `(score, reasons, question_text)`. Signals:
  trailing `?` (+3), any `?` in the tail (+1), closing-intent phrases
  (want me to / should I / for your review / please review / let me know / pick
  one / which option / your call / waiting on you / paused for / plan before /
  before I … / wdyt / confirm / approve …) (+2 each), an enumerated option set
  presented for a choice (+2). Threshold: **score ≥ 3**.
- `_detect_soft_block(c)` → applies the **terminal gate (a)** then the prose
  score **(b)**:
  - (a) terminal: `pending_tool` falsy, `subagent_in_flight_count == 0`,
    not `sidecar_in_flight`, `last_event_type != "user"`, and a non-empty
    `last_assistant_text`. This is what excludes a WORKING session whose
    sub-agent is still running.
  - (b) `_score_soft_block(last_assistant_text) ≥ 3`.
  - **Guaranteed hits:** `question_waiting` or `needs_approval` short-circuit to
    a max score — the formal flags are still honored.

Wired into `_classify_attention` (`server.py:~43555`) as two new kinds:
`question_blocked` (priority 1, formal flags) and `soft_block` (priority 2,
prose). The existing DONE/WAIT suppression at the top of `_classify_attention`
still applies, so shipped/blocked-on-external sessions are not re-flagged.

Archive rows (`find_all_conversations`, `server.py:~4051`) are enriched with the
fields the detector needs — `last_event_type`, `pending_tool`, `pending_file`,
`subagent_in_flight_count`, `session_state` — all already computed in
`_extract_tail_meta`, so the detector adds **zero** extra file reads.

## Deliverable 2 — `GET /api/attention?scope=all`

Cross-repo feed of ONLY attention sessions. `compute_attention_feed()` iterates
`find_all_conversations()` rows (the same cached archive source `?all=1` uses),
classifies each with `_classify_attention`, sorts priority→recency, and for the
**capped output set only** enriches each item with:

- `session_id`, `repo` / `folder_label`, `mtime`
- `question_text` — the detected question/checkpoint prose
- `turns` — last 2-3 message turns `{role, text}`, tool calls collapsed to
  `[tool:Name]` (via `_attention_read_turns`, reads only the file tail)

Cheap payload — replaces the COO pulling `/api/sessions?all=1` (1268 rows × ~60
fields) and parsing jsonl tails by hand. The existing `/api/attention` (repo
context) shape is unchanged and additive; it now also surfaces `soft_block`.

## Deliverable 3 — NYA visibility fix

`loadAttentionList()` (`static/app.js:~8679`) required `selectedRepoPath()` and
showed "Pick a repo" otherwise — but the repo dropdown was removed, so NYA
rendered nothing. Fix: when no repo is selected, drive NYA off
`/api/attention?scope=all` (all repos, no selection needed) and render the
per-item `repo` label.

## Deliverable 4 — perf params on `/api/sessions` + `/api/session/<id>`

`/api/sessions` gains optional, additive query params applied as a final
projection/filter pass (both `?all=1` and per-repo modes):

- `?since=<epoch>` — keep rows with `modified >= since`
- `?state=waiting|working|idle` — `_session_state_label(c)`:
  working = live and (`pending_tool` | `sidecar_in_flight` | sub-agent in flight);
  waiting = attention-flagged / soft-block / formal flag; idle = neither.
- `?limit=N` — cap row count (after sort)
- `?fields=a,b,c` — project to a CSV subset (`session_id` always kept)

`GET /api/session/<id>` — lightweight drill-in: `{ok, session_id, state,
last_assistant_text, question_text, turns}` from one file tail. No whole-list
rebuild.

## Perf / contract guardrails

- Detector runs on row dicts — **no per-row subprocess, no extra parse**.
- Turn enrichment reads tails for the **capped** output only, never all 1268.
- `/api/*` changes are additive (new endpoint, new params, new fields) — no
  rename/remove. `/api/attention` and `/api/sessions` keep their existing shapes.
- `tests/test_perf_budget.py` gains a call-count invariant for the cross-repo
  feed (turn reads ≤ output cap).
- GET-only, read-only; same-origin POST posture untouched.
