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
