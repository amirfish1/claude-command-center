# Q2 Queue Attendant — pivot from the status brief

2026-08-13. Owner verdict on the phase-1 status brief after one day of live use:
not useful — "general commentary that disappears; I need less to read and more
work getting done." This spec replaces the passive analyzer with an active
**queue attendant**. Owner's four requirements, verbatim in spirit:

1. **Manually activated** (the auto-generate policy dies entirely).
2. **Expert model** — claude `opus-5`, reasoning effort `high`.
3. **Runs in the repo of the queue** so it knows the code.
4. **Cleans the queue**: closes bugs that are already resolved (client
   reporting follows the close-summary protocol automatically), answers
   needs-input tickets autonomously where it can, and otherwise brings the
   gist of ONE question at a time to the owner — ideally proposing a
   direction for approval.

## Why this is mostly assembly

- `spawn_session(prompt, name=…, repo_path=…, model=…, reasoning_effort=…)`
  (server.py ~45338) spawns a headless Claude session; queue → repo comes from
  `_wt_read_config()[queue]["repo_path"]` (~617).
- The `wt` CLI already implements every attendant action: `wt close` (skimmable
  close summaries, auto-reported), `wt answer` (resumes the blocked worker),
  `wt dedup`, `wt release` — see the `watchtower` and `wt-triage-queue` skills.
- ~~CCC's AskUserQuestion relay~~ **Correction (found in the smoke test):
  headless spawned sessions do not expose the AskUserQuestion tool at all**,
  so the relay never fires. The shipped mechanism instead: the attendant
  POSTs its ONE pending question to `/api/queue/attend/question`
  `{queue, ref, question, options}` and ends its turn; the owner answers via
  `/api/queue/attend/answer {queue, text}`, which clears the question and
  resumes the session by injecting the answer (prefixed "Owner answer:") as
  its next message. One-at-a-time is enforced by the prompt contract plus
  the single pending_question slot. GET reports `phase`
  (working/waiting/done/gone) because a live pid cannot distinguish working
  from turn-over-idling-on-the-stdin-FIFO.

## Backend (server.py)

State file per queue: `COMMAND_CENTER_STATE_DIR / "queue-attendants" /
"<QUEUE>.json"` → `{queue, session_id, spawn_id, started_at, repo_path, model,
last_report: {summary, at} | absent}`.

**POST `/api/queue/attend`** body `{queue}`:
- validate with `_QUEUE_CONFIG_NAME_RE`; resolve `repo_path` from
  `_wt_read_config()` (400 with a "configure the queue's repo path first"
  error when missing or not a directory);
- 409 if the recorded attendant session is still live;
- `spawn_session(prompt, name="attendant: <QUEUE>", repo_path=…,
  model="opus-5", reasoning_effort="high")`, persist state, return
  `{ok, session_id}`.

**GET `/api/queue/attend?queue=X`** → `{ok, exists, running, session_id,
started_at, running_for_s, last_report, question}` where `question` proxies
`_read_question_request(session_id)` (null when none pending) and `running`
checks the spawn registry / pid. Cheap; never spawns.

**POST `/api/queue/attend/report`** body `{queue, summary}` → stamps
`last_report` into the state file. Called by the attendant itself as its last
act (curl to localhost; tolerated if it never arrives — the band falls back to
"session ended").

Answering goes straight to the existing `/api/answer-question` from the
frontend (it has the session_id from GET) — no new endpoint.

The `/api/queue/brief*` endpoints stay in place but nothing calls them.

## The attendant prompt (embedded in the POST handler)

> You are the queue attendant for WatchTower queue {Q}, running inside its
> repo at {repo}. Your job is to CLEAN this queue, not to summarize it. Use
> the `wt` CLI (the `watchtower` and `wt-triage-queue` skills document it).
> Survey with `wt ls -q {Q} --status all --json` and `wt blocked -q {Q}
> --json`, then work oldest-first:
> 1. VERIFY-AND-CLOSE: for each open bug, check the code and git history for
>    whether it is already fixed or obsolete. Close only what you can verify,
>    citing the commit or observed behavior in the close summary (`wt close`);
>    client reporting follows automatically. Never fabricate a fix.
> 2. DEDUP: `wt dedup -q {Q}` dry-run first, `--apply` only for true dupes.
> 3. NEEDS INPUT: try to answer autonomously from the repo, ticket history,
>    and queue learnings (`wt answer <ref> "…"`). When you lack high
>    confidence, distill the decision to its gist and use the AskUserQuestion
>    tool: ONE question at a time, at most 3 sentences of context, your
>    recommended direction as the FIRST option. Apply the owner's answer via
>    `wt answer`.
> 4. STALE CLAIMS: `wt release <ref> --worker <id>` when the claiming worker
>    is dead (`wt workers --json`).
> 5. Do NOT implement fixes for open tickets — that is the workers' job. You
>    only close, answer, release, dedup, and escalate.
> When the queue is as clean as you can make it, POST
> `{"queue": "{Q}", "summary": "closed N · answered N · released N ·
> escalated N — <one skimmable sentence>"}` to
> `http://127.0.0.1:{port}/api/queue/attend/report`, then stop.

## Frontend (static/q2.*)

The brief band becomes the attendant band. Remove: auto-generate policy, the
brief renderer and its loaders/pollers (the `q2-brief` CSS shell, tint,
resizer, and collapse stay — the band chrome is reused). Keep `fmtElapsed`.

States (poll GET every 3s only while `running`):
- **idle**: header "Queue attendant" + one `Tend queue` button; a muted
  `last_report` line when one exists ("Last tended 2h ago — closed 3 ·
  answered 2").
- **running**: pulsing dot + "Attendant working… 4m 12s" (elapsed from
  `running_for_s`, ticked client-side like the old analyze timer) + an
  "open session ↗" link to the spawned session.
- **question pending**: card titled "Attendant asks:" with the question text
  and one button per option (recommended direction arrives first from the
  prompt contract) plus a free-text input; POST `/api/answer-question`
  `{session_id, answers: [{index: <question index>, text: <choice>}]}`, then
  resume polling.
- **ended**: `last_report` summary, or "Attendant finished — open session ↗"
  if it never reported.

## Non-goals

Scheduling/recurring runs, multi-queue sweeps, attendant implementing fixes,
any automatic trigger. The synthetic-queue smoke test (file backend, throwaway
repo, one pre-resolved bug + one needs-input ticket) is the only test.
