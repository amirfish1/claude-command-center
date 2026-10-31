# Stuck session triage (full reference)

A session that ended its last turn cleanly is **not** proven healthy. Do not
report "idle, not stuck" until you have ruled out an undelivered message.

When a user says a session "got stuck", "won't take input", or a message sits on
"sending…", treat the user's report as authoritative and check, in order:

0. **Ask CCC directly first.** `GET /api/session/<sid>/inject-receipt`
   (CCC-28) returns `{"outstanding": null}` when nothing is unproven, or
   `{"outstanding": {"inject_id", "text_preview", "sent_ts", "age_s", ...}}`
   when a queued inject has not been confirmed landed. This is the receipt
   opened at `/api/inject-input` time whenever the result is `queued=True`,
   closed the moment the queued row is committed as delivered (the Codex
   claim commit, the terminal-queue watcher, auto-recovery, force-restart)
   — it is what steps 1-2 below exist to reconstruct by hand when this field
   is unavailable. Its `state` says what to do: `queued` or `held` means the
   row is still in CCC's queue (`queue` names it; `delivery_id` =
   `inject_id`, so `POST /api/pending-input/cancel` can drop it by id);
   `unproven` means the row left the queue and nothing proved it landed —
   that is the case worth chasing. An `unproven` receipt expires after a day.
   A non-zero `stop_held` count is not a stuck delivery: an explicit Stop
   (or a stop steer) parked that queued input on purpose (CCC-237). It sends
   only when released (`ccc held <sid> --release all`, or the "Held after
   Stop" block in the conversation); `Q_STOP_HELD` in `activity.log`.
1. **Was something injected after the last turn?** Compare
   `~/.claude/command-center/last-interactions.json` for the sid against the
   transcript mtime. An interaction newer than the last transcript line means a
   message that never landed.
2. **What did CCC do with it?** `~/.claude/command-center/logs/activity.log`,
   filter the sid: `INJECT ... queued=True via=-` means it was parked in the
   terminal queue, not written to the child. Look for `Q_HELD`, `Q_DROP`,
   `RECOVER`, `RECOVER_GIVEUP`, `FORCE_RESTART`. No line at all used to mean it
   vanished silently with zero trail (CCC-28: a retried delivery that
   re-parked itself or failed outright logged nothing at all, unlike every
   named hold reason) — as of the CCC-28 fix this now also produces a
   throttled `Q_HELD reason=requeued_self_queued` or
   `Q_HELD reason=requeued_after_failed_delivery` line, so genuine silence
   after that fix shipped means something new, not the same old gap.
3. **Is the queue holding it?** `pending-inputs.json` (`terminal_queue`).
4. **Is the child reading?** Tail its spawn log; check `/proc/<pid>/fd/0` is
   the stdin FIFO and the process has no children.

Facts that trip people up:

- **Stop / Esc only interrupts a running turn.** `/api/inject-esc` is a no-op on
  an idle child, so it cannot un-stick this case.
- `/api/inject-input` stamps `last-interactions.json` *before* delivery
  (deliberately — it records that the user acted, independent of whether
  delivery succeeds), so a recorded interaction does not by itself mean the
  message was delivered. Read the inject-receipt field (step 0) or `INJECT`'s
  own `queued=` value for the delivery-proof signal instead of inferring it
  from interaction timing.
- Recovery: `POST /api/session/<sid>/force-restart` retires the live child and
  re-delivers a queued message via `--resume`. Auto-recovery (CCC-27) does the
  same for a child whose log is silent, bounded to one recovery per message and
  2 per session per 30 min, then marks the session `inject_stuck`
  (`inject-recovery.json`). Disable with `CCC_INJECT_AUTORECOVER=0`.
- Every automatic wake (this recovery, the approved limit-reset "continue")
  also goes through the wake ledger (`session-wakes.json`, `wake_guard.py`):
  at most 2 per session per 24h, and no second wake unless the first one made
  progress (transcript grew and the turn closed). A refusal logs
  `RECOVER_GUARDED` / `AUTO_RESUME_GUARDED` and asks the owner instead; Force
  restart clears it. Disable all automatic wakes with `CCC_AUTO_WAKE=0`.
- Off-dashboard alerts: set `CCC_ALERT_COMMAND` (e.g. in
  `~/.claude/command-center/config.local.env`) to a command that reads the
  message on stdin. CCC runs it for dead sessions and refused wakes, once per
  incident, at most 10 per hour (`alert-hook.json`; `ALERT_HOOK` in
  `activity.log`).
- Dead sessions are notify-only by default. A session can opt in to one
  automatic rerun (`POST /api/auto-rerun {"session_id", "enabled"}`); a
  scheduled job that starts a fresh session each run gets a host-only rule in
  `auto-rerun.json` (cwd + first-prompt prefix + the job's command). Every
  rerun goes through the wake ledger; a rule's second death inside 24h is
  refused as no progress until the rule is re-saved. `auto-rerun` lines in
  `activity.log` (`RERUN`, `RERUN_GUARDED`, `RERUN_FAIL`).
- Never kill a child that is mid-turn or running a tool: confirm the spawn log
  has been silent for minutes and it has no child process first.
