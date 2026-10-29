# Codex conversation controls

Open a Codex conversation normally in CCC. It renders through the same
transcript view as every other engine (Claude Code, Kimi, Grok, Devin). The
header, draft, composer, model controls, queued messages, and status rail
stay in place. There is no separate workspace screen, launch button, or
Files & terminal / Settings / Tools tab.

When a native app-server connection is available for that thread, CCC polls
it for near-real-time progress and merges the result straight into the same
transcript: in-progress turns, tool calls, file diffs, generated images, and
pending approval/question/permission/elicitation requests appear inline,
reconciled against the durable rollout as soon as it catches up. There is
nothing to open or switch to for this — it is always on for a Codex thread
with a live connection.

The existing Send and Escape controls use the same selected task and desktop
owner as the transcript. Busy desktop tasks use CCC's existing message queue;
a delivery error never falls through to a second transport.

The conversation renders Markdown, progress, final answers, tool results,
file diffs, images, questions, and approvals. Tool details start collapsed.
Earlier turns load on demand. A composer uses the connected model catalog for
model and reasoning options, with image input when supported.

## Connection requirements

Install a compatible Codex CLI and connect CCC's existing Codex app-server bridge.
The conversation uses that connection's owner; the browser does not launch a second
writer against Codex's shared state. Transcript ingestion and the older exec
fallback remain available separately.

CCC can also follow conversations already owned by the running Codex/ChatGPT
desktop app. Its separate desktop adapter discovers the owner, subscribes to
versioned history updates, and routes supported replies back to that owner. It
never starts a second writer against the same profile. The footer says
**Desktop connected** when this connection is active.

The desktop adapter supports conversation history, sending a new turn when idle,
stopping the expected active turn, compaction, questions, and supported approvals.
In-flight steering is unavailable because the desktop follower interface does not
expose the native API's atomic expected-turn guard. Queues stay with the desktop.

This desktop follower protocol is an installed-app integration, distinct from the
public app-server protocol, and can change between desktop releases. Unknown
stream versions and changed owners fail closed. The selected conversation must
have an available desktop owner; opening it in the desktop app establishes one.

## Message queues

CCC owns queued messages in the dashboard. The native Codex queue is not
exposed as a footer control because switching delivery systems is an advanced
recovery operation and risks sending an uncertain message twice. Existing
queued messages stay with their current owner and inputs are never copied
between queues.

When an internal recovery operation uses the Codex queue, it returns to CCC
only after the native queue is confirmed empty. Pending or unconfirmed native
queue writes keep ownership with Codex, including across a worker restart.
This prevents a second delivery system from sending an uncertain message again.

## Verification and deployment

The feature includes focused backend tests and browser interaction tests for
the shared transcript renderer and its live overlay. Tests do not log in to a
real account or start billable model turns.

Changes to the service require restarting both the CCC dashboard and control-plane
worker, then reloading the dashboard. WatchTower requires no restart.

## Cloud (dot / aeon) threads

Codex Desktop dots are persistent cloud agents. A dot delegates work to
threads — `aeon` root threads, `aeon_child` worker threads, plus `dreaming`
and plain `user` threads. Those threads live in OpenAI's cloud: even when a
child thread "runs on your computer", its record and transcript never touch
`~/.codex/sessions/**/rollout-*.jsonl` or Codex's local state databases, so
a file scanner cannot see them.

CCC discovers cloud threads through the cloud backend the desktop app itself
talks to, reusing your existing Codex login (`~/.codex/auth.json`). Cloud
threads appear in the session list and archive alongside local Codex
conversations, carry a `cloud` badge, and nest under their dot's root
thread. Opening one renders the full turn history in the same transcript
view: messages, reasoning, command executions with output, tool calls, file
diffs, compaction markers, and placeholders for images and other large
attachments.

Cloud threads are **read-only** in CCC. The composer is disabled with a
"read-only" note; input injection, interrupt, force-restart, and terminal
launch all refuse cloud threads. To reply, open the thread in the Codex app
(the "Open in Codex" action resolves cloud threads too).

### Transport caveat

The cloud backend protocol is undocumented and internal to OpenAI. It may
change without notice. CCC sends read-only requests only (thread catalog,
metadata, turn lists); it never sends a mutating method. Large attachments
inside turn payloads are replaced with byte-size placeholders before
anything is cached or served.

### Degraded modes

Everything fails soft — the session list never blocks on the network:

- **No login or expired token** — rows still appear from the desktop app's
  own local sidebar cache; the badge and a typed API field explain that the
  transcript is unavailable until you open the Codex app or run
  `codex login`. CCC never stores your token, never logs it, and never
  attempts to refresh it.
- **Backend unreachable** — the last fetched catalog is served from disk
  and retries back off exponentially.
- **Protocol change** — schema-level errors are detected and reported as a
  typed reason instead of a hard failure; cached data keeps rendering.

Turn bodies are fetched only when a thread is opened and are cached on disk
in CCC's state directory (outside the repo), keyed by the thread's cloud
`updatedAt`; repeat opens serve the cache in well under 100 ms.
