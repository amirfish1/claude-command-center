# Handoff: Codex app-server liveness churn + observability

Written 2026-07-27, end of a long live-debugging session. Handing off to a
fresh session/model to keep digging on the root cause and build out proper
monitoring. Everything below is either a verified fact (with the evidence
that proves it) or clearly marked as a hypothesis.

## TL;DR for whoever picks this up

- The original incident (600+ orphaned `codex app-server` processes, 15h
  leak, full swap) is **fixed and confirmed stable**.
- A second, self-inflicted incident (a false-"wedged"-detection storm that
  spiked CPU to 24.5%) is **fixed and confirmed stable**.
- A third, calmer issue remains **open and unsolved**: CCC's worker process
  still periodically fails to observe a reply from an otherwise-healthy,
  idle Codex app-server within its 8s liveness timeout, roughly once a
  minute. This no longer causes a CPU storm (hysteresis absorbs single
  misses), but it's still happening and its root cause is not yet fixed —
  only strongly suspected. See "Prime suspect" below; it points at code
  this same session wrote earlier today.
- **You have working diagnostics now**: every miss captures an all-thread
  Python stack dump. Read `~/.claude/command-center/logs/python-stacks.log`
  for direct evidence instead of re-deriving hypotheses from scratch.

## Where the code lives

- Shared clone (always `main`, do not branch here):
  `/Users/amirfish/Apps/claude-command-center`
- Per CLAUDE.md hygiene, every fix this session was made in its own git
  worktree, tested, then merged into the shared clone's `main` and deployed.
  Worktrees from this session (safe to remove once you've read them, or
  keep for reference — none have uncommitted work):
  - `../claude-command-center-wt-codex-appserver-leak` (`fix/codex-appserver-orphan-reap`) — **has uncommitted frontend work**, see "Loose end" below
  - `../claude-command-center-wt-liveness-busy` (`fix/codex-appserver-liveness-busy`)
  - `../claude-command-center-wt-liveness-hysteresis` (`fix/codex-appserver-liveness-hysteresis`)
  - `../claude-command-center-wt-liveness-diag` (`fix/codex-appserver-liveness-diagnostics`)
  - `../claude-command-center-wt-worker-stackdump` (`fix/worker-stack-dump-handler`)
- All five branches above are merged into `main` and currently deployed.
  `main`'s HEAD is `4f702dfe` at the time of writing.

## Architecture you need to understand first

CCC has **two Python processes**, and this tripped me up for a while:

1. **`server.py`** — the dashboard. Run directly (`python3 server.py`),
   launchd label `com.github.claude-command-center`. Its `main()` installs
   an HTTP server, telemetry, and (relevantly) a SIGUSR2 all-thread stack
   dump handler (`_install_python_stack_dump_handler()`, writes to
   `~/.claude/command-center/logs/python-stacks.log`).
2. **`ccc_worker.py`** — the persistent worker that actually owns engine
   execution (spawning Claude/Codex/Kimi, subprocess lifecycles, the
   in-memory Codex app-server transport). Launchd label
   `com.github.claude-command-center.worker`.

`ccc_worker.py` does **not** import `server.py` at the top level. Instead,
`worker_engines.py`'s `EngineHost._legacy()` does a **lazy `import server`**
the first time any engine RPC needs it, sets `CCC_WORKER_PROCESS=1` first,
and calls straight into `server.py`'s functions (e.g.
`_ensure_codex_app_server`, `_log_activity`). Because it's an `import`, not
running `server.py` as `__main__`, **`main()` never runs in the worker
process** — so anything gated behind `if __name__ == "__main__"` (like the
stack dump handler) silently doesn't exist there unless separately wired up.

This mattered a lot: **all the actual Codex app-server churn (spawn,
liveness-check, replace) happens inside `ccc_worker.py`'s process**, using
its own independent copy of `server.py`'s module-level globals
(`_CODEX_APP_SERVER_TRANSPORT`, `_CODEX_APP_SERVER_LOCK`, etc. — each
process that imports `server` gets its own instance of these). The
dashboard process has a separate, mostly-idle copy of the same machinery.
Confirmed by tracing `ps -eo pid,ppid,command`: the churning
`codex -c model_context_window=1000000 app-server --listen stdio://` child
was parented to `ccc_worker.py`'s PID, not `server.py`'s.

**Lesson for next time**: if you're chasing something that seems to live in
`server.py`, check `ps` for which *process* actually owns the relevant
child/behavior before assuming it's the dashboard. It's very possibly the
worker running the exact same code by import.

## Timeline of what was found and fixed this session

### 1. Original leak (fixed, stable)

`_CodexAppServerTransport.close()` does terminate → wait(5s) → kill →
wait(5s). If the final wait still times out (e.g. child stuck in
uninterruptible sleep under memory pressure), `close()` gives up silently
and the caller spawns a replacement on top of the still-alive orphan.
Compounds over time: 600+ orphaned processes, 15h leak, full swap.

Fix: `_codex_app_server_reap_stray_children()` — before every stdio spawn,
lists direct children of the worker process matching the app-server command
line and `killpg`s any not matching the currently-tracked PID.
Commit `78f71e0d`, tests in `tests/test_codex_app_server_reap_strays.py`.

### 2. Busy-vs-wedged storm (fixed, stable)

The periodic liveness probe (`thread/list`) shares the same request/response
channel as real turn traffic. If a real turn was in flight, the probe would
queue behind it, time out, and CCC would conclude the *healthy, busy*
app-server was wedged and kill it — destroying the in-flight session.
Because the app-server is a *singleton* shared across all Codex sessions,
one active conversation was enough to trigger a continuous respawn storm:
observed live, WEDGED every 2-6 seconds for many minutes, CPU pinned at
24.5% on the worker process.

Fix: track real (non-probe) in-flight requests via
`_CODEX_APP_SERVER_INFLIGHT`; if the probe times out but a real request is
outstanding, log `BUSY` and leave the transport alone instead of tearing it
down. Also stamp `_CODEX_APP_SERVER_LAST_LIVE_CHECK` right after a
successful spawn+init so a freshly-started process isn't immediately
re-probed. Commit `e5ec6987`. **Verified live**: CPU dropped from 24.5% to
~1% immediately after deploy; WEDGED frequency dropped from continuous to
roughly 1/minute.

### 3. Single-miss over-eager replacement (fixed, stable)

Even after (2), a *calmer* WEDGED-and-replace kept recurring roughly once a
minute on a fully idle transport (confirmed zero in-flight requests at
those moments — no `BUSY` entries logged). A single missed liveness probe
was enough to tear down and respawn a process that might just have hit one
transient stall.

Fix: track `consecutive_liveness_misses` per transport. Require
`_CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD` (currently 2) consecutive
misses before actually replacing; a single miss just logs `MISS` (see below
for the rename) and gives it another liveness-check cycle. A successful
probe resets the streak. Commit `4d476dda`. **Verified live**: e.g. pid
9167 missed once (logged, deferred), missed again 61s later (logged,
replaced) — correctly distinguishing "genuinely stuck twice in a row" from
"one blip."

### 4. Stop guessing, start measuring (fixed, but revealed a gap — see #5)

At this point I had written the liveness-miss log line as `"SLOW"` with a
comment blaming "memory pressure" — **the user correctly called this out as
an unverified guess.** I went and actually measured it:

```
python3 /tmp/probe_thread_list.py    # tight loop, 4x back-to-back thread/list
initialize: 0.062s
thread/list #2-5: 0.163-0.176s each

python3 /tmp/probe_idle_gap.py       # spawn, then 25s idle gap, probe, repeat x6
probe#2-7 after 25s idle: 0.138s - 0.461s, zero timeouts
```

**A freshly-spawned, isolated Codex app-server answers in well under half a
second every time, idle gap or not.** This conclusively rules out "Codex
app-server itself is slow" as an explanation. The scripts are at
`/tmp/probe_thread_list.py` and `/tmp/probe_idle_gap.py` if useful (note:
`/tmp` clears on reboot per this machine's CLAUDE.md — recreate if gone).

Given that, a miss means CCC's own process failed to *notice* a reply that
was very likely already sitting in the OS pipe — not that Codex was slow.
Renamed the log verb `SLOW` → `MISS`, reworded messages to say "no reply
*observed*" rather than implying the app-server was slow, and added
`_codex_app_server_dump_stacks_on_liveness_miss()`: on every miss, write a
marker into `python-stacks.log` and raise `SIGUSR2` against our own process
to trigger the existing all-thread dump handler
(`_install_python_stack_dump_handler`, already used for manual debugging).
Commit `ce187169`.

### 5. The diagnostic was a no-op in the one process that mattered (fixed)

Deployed #4, watched for a miss — activity.log showed `MISS`/`WEDGED` firing
correctly, but **`python-stacks.log` got nothing**. Traced why (see
Architecture section above): `_install_python_stack_dump_handler()` only
runs in `server.py`'s `main()`, which the worker process never calls.

Fix: call `server._install_python_stack_dump_handler()` from
`EngineHost._legacy()` in `worker_engines.py`, right after the lazy
`import server`. Commit `04e4fdbc`. **Verified live**: next miss produced a
real dump.

## Prime suspect for the actual root cause (fixed — see update below)

**Update, same day, after this doc was first written:** both suggested fixes
below landed in commit `bfb37e1e` — `_log_activity`'s `mkdir` is now
cached behind a one-time `_ACTIVITY_LOG_DIR_READY` flag, and
`_ensure_codex_app_server` defers its `_log_activity(*pending_log)` call
until after `_CODEX_APP_SERVER_LOCK` releases. The lock-holder no longer
blocks on file I/O. Leaving the original investigation notes below for
context/history.

With diagnostics finally landing in the right process, two samples were
captured. The first showed the reader thread simply blocked on a normal
pipe read (uninteresting — that's its correct idle state) and a concurrent
`process_request_thread` mid-`pathlib.Path.__new__` with a truncated
(`<invalid frame>`) stack, so the caller wasn't identifiable.

**The second sample has a complete, unambiguous stack** (from
`~/.claude/command-center/logs/python-stacks.log`, worker pid 84380,
2026-07-27 19:28:33 UTC):

```
Thread-1051 (process_request_thread):
  pathlib/__init__.py:1020 in mkdir
  server.py:5799 in _log_activity
  server.py:24122 in _ensure_codex_app_server
  server.py:23893 in _codex_app_server_request
  server.py:24551 in _codex_app_server_thread_is_active
  worker_engines.py:458 in _call
  worker_engines.py:308 in query
  ccc_worker.py:158 in dispatch
  ccc_worker.py:186 in handle
```

Decoded: an RPC handler thread (servicing a `thread_is_active` query) was
**inside `_ensure_codex_app_server`**, which holds `_CODEX_APP_SERVER_LOCK`
for its entire liveness-check-and-log sequence, and at that exact moment it
was blocked in `_log_activity`'s unconditional
`ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)` call
(`server.py:5798` — every single `_log_activity()` invocation does this
mkdir, even though the directory obviously already exists after the first
call).

**Hypothesis** (high confidence, not yet proven with a second independent
repro): while that thread holds `_CODEX_APP_SERVER_LOCK` and is stalled in
the `mkdir` syscall (this machine has genuine memory pressure — ~424MB free
at last check — so an otherwise-trivial syscall can stall for multiple
seconds), the `codex-app-server-reader-stdio` thread cannot acquire the
*same lock* to record an incoming reply in `_CODEX_APP_SERVER_RESPONSES`
and `notify_all()` — even though Codex already replied. That produces
exactly the observed symptom: "no reply observed within 8s" on an app-server
that was never actually slow.

If true, this is **self-inflicted by this session's own earlier work** —
the unified activity-log feature (`_log_activity`, built earlier this same
session for a different ask) is itself contributing to the very liveness
problem being chased. That would be a good story to confirm before fixing.

**Suggested next steps, in order:**
1. Confirm the hypothesis: does `_log_activity` get called from *inside*
   `with _CODEX_APP_SERVER_LOCK:` on the WEDGED/MISS/BUSY code paths in
   `_ensure_codex_app_server`? (Yes — verified by reading the function; the
   log calls are textually inside the `with` block.) Confirm no other
   `_ensure_codex_app_server` caller holds the lock during other slow I/O.
2. Cheap, low-risk fix if confirmed: cache "directory already exists" so
   `mkdir` only runs once (e.g. a module-level flag set after the first
   successful call, or just call `ACTIVITY_LOG_FILE.parent.mkdir(...)` once
   at startup instead of per-log-line). That alone would remove the
   syscall from the hot path entirely.
3. More thorough fix: don't call `_log_activity` (or any file I/O) while
   holding `_CODEX_APP_SERVER_LOCK`. Move the logging calls in
   `_ensure_codex_app_server` to after the `with` block releases (they
   already have all the data they need computed by that point), or hand
   the write off to a separate thread/queue so the lock-holder never blocks
   on file I/O.
4. After deploying, watch `python-stacks.log` for a few more misses to see
   if the same `_log_activity`/`mkdir` pattern keeps showing up on the
   blocking thread. If it stops recurring, that's strong confirmation.
5. If it turns out NOT to be this, the dumps are still there — look at
   whatever thread the "Current thread" or other active threads are stuck
   in at the next miss.

## What's now instrumented (the "monitoring" ask)

This was explicitly asked for, so listing it clearly:

- **`~/.claude/command-center/logs/activity.log`** — unified, human-readable,
  fixed-width, append-only log (`_log_activity(category, verb, detail)` in
  `server.py`). Verbs currently emitted: `SPAWN`/`REQUEST`/`REJECT`/`FAILED`
  (session spawns), `INJECT` (text injection), `KILL` (session teardown),
  `REAP` (orphan cleanup), `MISS`/`BUSY`/`WEDGED`/`DEAD` (app-server
  liveness). Format matches WatchTower's `~/.watchtower/activity.log` style
  so the two can be tailed side by side. Readable via `GET
  /api/activity-log?session_id=&limit=` (`_read_activity_log`).
  **Gotcha**: category is truncated to 14 chars, verb to 9 — an overlong
  value silently truncates rather than corrupting the fixed-width parse (this
  bit us once already with `"codex-app-server"` overflowing 14 chars;
  shortened to `"app-server"` and added the truncation guard).
- **`~/.claude/command-center/logs/python-stacks.log`** — all-thread Python
  stack dumps. Two trigger paths now: (a) manual, send `SIGUSR2` to either
  process's PID any time; (b) automatic, on every Codex app-server liveness
  miss (`_codex_app_server_dump_stacks_on_liveness_miss`, fires in *both*
  the dashboard and worker processes as of commit `04e4fdbc`).
- **Not yet built** (explicitly asked about, worth doing):
  - **Alerting**: nothing currently pages/notifies on a WEDGED storm or a
    sustained high-CPU condition — it was only caught this time because a
    human happened to notice 80% CPU on the machine. A cheap addition:
    track a rolling WEDGED-per-minute counter and surface it somewhere
    loud (a desktop notification, a Slack ping, whatever channel this user
    prefers) if it exceeds a threshold for N consecutive minutes — that
    would have caught incident #2 (the storm) within a minute instead of
    requiring a human to notice CPU%.
  - **UI surfacing**: see "Loose end" below — there's an uncommitted
    activity-log viewer button/modal already built for the dashboard UI
    that would show this data in-app instead of requiring a terminal
    `tail`. Finishing and shipping that directly serves the "UI indication"
    part of the ask.
  - **Structured/queryable history**: `activity.log` is append-only text,
    fine for tailing but not for e.g. "show me WEDGED-per-hour over the
    last week" trend analysis. If this becomes a recurring concern, worth
    considering: periodic rollup into the existing SQLite work ledger
    (`control_plane.py`, `~/.claude/command-center/control-plane.sqlite3`),
    or at minimum a small `/api/activity-log/stats` endpoint that
    aggregates verb counts over a time window.

## Loose end from earlier in this session (unrelated to the above, still open)

Before this liveness investigation, this session built a full UI feature
for viewing the activity log from within the dashboard (a button in the
conversation sticky header opening a modal, with a This-session/All-sessions
toggle). **The backend (`/api/activity-log` endpoint) is merged and live.
The frontend is not**: `static/app.js` and `static/app.css` in
`../claude-command-center-wt-codex-appserver-leak` have the UI code but it
was never committed or merged, and was never visually verified in a
browser (browser-automation session ran out of time/attempts). If you pick
this up: read `docs/visual-verify-app.md` in that worktree first — it's a
short, tested recipe for visually verifying this app's frontend without
burning 10 minutes on the same gotchas (dismissing first-load banners,
avoiding `.wt-worker-session-card` rows which use a different render path
without the sticky header).

## Practical notes for continuing

- Restart both processes after any `server.py`/`worker_engines.py` change:
  ```bash
  launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center.worker
  launchctl kickstart -k gui/$(id -u)/com.github.claude-command-center
  ```
- 8 pre-existing test failures are unrelated to this whole investigation
  (confirmed identical on `main` before any of these fixes touched
  anything): `test_headless_staleness.py` (2), `test_productivity_core.py`
  (1), `test_smoke.py` (4), `test_throughput_weekly_banner_static.py` (1).
  Don't chase these as part of this work.
- This machine genuinely runs low on free RAM (~424MB free observed at one
  point, mostly Chrome Beta + WebKit + WhatsApp, not CCC). That's real
  background pressure worth keeping in mind as a contributing factor to
  syscall latency, but per the investigation above it's very likely not the
  *primary* cause of the liveness misses — self-inflicted lock contention
  is the leading suspect.
