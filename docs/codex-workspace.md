# Codex workspace

Open a Codex conversation in CCC and choose **Workspace** in its header. The
workspace has three views: Conversation, Workspace, and Settings.

The conversation renders Markdown, progress, final answers, tool results,
file diffs, images, questions, and approvals. Tool details start collapsed.
Earlier turns load on demand. A composer uses the connected model catalog for
model and reasoning options, with image input when supported.

Workspace and Settings expose the installed app-server's supported operations
as categorized actions with validated forms. These include conversation
lifecycle, queues, goals, reviews, files, terminal sessions, account/configuration,
skills, MCP, plugins, projects, environments, remote control, and realtime media.
Operations remain subject to the connected server, account, provider, platform,
and configured permissions.

## Connection requirements

Install a compatible Codex CLI and connect CCC's existing Codex app-server bridge.
The workspace uses that connection's owner; the browser does not launch a second
writer against Codex's shared state. Transcript ingestion and the older exec
fallback remain available separately.

A running Codex or ChatGPT desktop app does **not** necessarily expose a supported
external app-server endpoint. When CCC cannot attach to its owner, live workspace
actions are unavailable. Do not point CCC at private desktop IPC sockets or start
a competing writer against the same profile. A separately owned app-server needs
its own profile and authentication, or a supported owner-provided control endpoint.

## Capability discovery

CCC generates and caches the protocol schema from the installed Codex executable.
Changing the executable/version refreshes the catalog. Unsupported and
platform-specific actions explain their availability; internal and test-only
messages do not appear as product actions.

**Preview features** enables experimental protocol surfaces, including media,
process, plugin, and remote features when exposed by that version. Availability
in a schema does not guarantee provider or host support. Host attestation and
external token-refresh callbacks require real host adapters; CCC does not
manufacture credentials or attestations. Unknown dynamic tools return an
explicit unsupported response unless a host handler has been registered.

The initial compatibility audit used Codex CLI **0.153.4**: 155 client request
methods, 11 server request methods, and 82 notifications including `initialized`.
This is protocol coverage, not a claim that every method was exercised against a
live account. New versions may add or remove capabilities.

## Message queues

The footer identifies the message queue owner: **CCC** or **Codex**. Existing
queued messages stay with their current owner. Using a native queue action
selects the Codex queue only after CCC's queue, recovery, and pending handoffs
are clear. Inputs are never copied between the two queues.

Switch back to CCC only when the Codex queue is confirmed empty. Pending or
unconfirmed native queue writes keep ownership with Codex, including across a
worker restart. This prevents a second delivery system from sending an uncertain
message again. While Codex owns the queue, enqueue messages in its workspace;
legacy CCC enqueue attempts return an explicit ownership error.

## Terminal and media behavior

Sandboxed command sessions support output streaming, input, resize, and stop.
They have a maximum duration of 30 minutes. The separate **Host process (preview)**
backend can support longer processes where available; its execution boundary is
the host, and it must not be confused with a sandboxed command.

Realtime features depend on the connected provider. Microphone input needs
browser permission. Closing or replacing the workspace releases local audio
resources immediately and waits for native cleanup. Unconfirmed cleanup blocks
replacement media controls rather than assuming the previous session stopped.
Microphone capture currently uses Web Audio's compatibility processor API.

File paths and native process/watch handles are bound to the selected repository.
A remote environment selection does not redirect local filesystem calls: those
calls stay unavailable until an appropriate connection is available.

## Verification and deployment

The feature includes focused backend tests, browser interaction tests, and
isolated native app-server checks. Tests do not log in to a real account, start
billable model turns, or exercise a user's actual microphone.

Changes to the service require restarting both the CCC dashboard and control-plane
worker, then reloading the dashboard. WatchTower requires no restart.
