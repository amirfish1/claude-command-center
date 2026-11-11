# On-call (preview)

Bugs reported by your customers and by your monitoring go to coding agents
that fix them. CCC messages you only when an agent needs a real decision or
has stopped, and your reply from the same thread is the agent's answer.

On-call is a preview. It is off until you turn it on, and nothing is created
on disk before that.

[Planned call escalation contract](oncall-calls.md).

## What it needs

- CCC running on a machine that stays on: your Mac, a Linux box or a small VM.
  Nothing is hosted for you.
- WatchTower, the queue engine that CCC's installer sets up
  ([requirements](install.md#watchtower-queue-engine)). Agents pick bug
  tickets up from a WatchTower queue.
- One way to reach you: Telegram, email, ntfy, iMessage, SMS, WhatsApp, or
  your own command.

No channel needs a public URL or a webhook. CCC polls from the machine it
runs on.

## Turn it on

```
ccc oncall setup
```

Setup asks these questions on a fresh install:

```
On-call is a preview. Turn it on for this machine? (Y/n):
Folder of the code agents should fix [/your/current/folder]:
Pick a number [1]:
```

Press Enter for each default. The channel choices are printed before the last
prompt, in this order:

```
How should CCC reach you?
  1. ntfy (phone push, no account)
  2. Telegram
  3. Email
  4. iMessage (this Mac)
  5. SMS (your Twilio number)
  6. WhatsApp (your Twilio sender)
  7. Your own command
```

The queue name comes from the folder name. Use `--queue NAME` or `--repo PATH`
to choose something else. Setup generates a random ntfy topic and prints:

```
Subscribe in the ntfy app: https://ntfy.sh/ccc-oncall-<random-topic>
```

Subscribe to that URL in the ntfy app to read the test message. A successful
send means the server accepted it; CCC cannot confirm that your phone displayed
it. Keep the topic private. Self-hosted ntfy works with `--set server=URL`.
The preview question is skipped when the preview is already on. Existing folder,
queue and channel settings are reused. Piped answers work too.

When a test send succeeds, the final line for ntfy is:

```
On-call is on: alerts and replies are enabled. Turn it off: ccc oncall off
```

For channels that cannot receive replies, the final line is:

```
On-call is on: alerts are enabled; channels are send only. Turn it off: ccc oncall off
```

If every test send fails, setup turns On-call off, including on a rerun:

```
ccc oncall: No test message got through. On-call is off. Fix the channel, then run ccc oncall setup again.
```

Without the questions:

```
ccc oncall setup --yes --queue shop --repo ~/projects/shop \
  --channel ntfy --set topic=shop-oncall-4f9a1c
```

A secret is never a command argument. Setup asks for it with a hidden prompt,
reads it from standard input (`--secret-stdin FIELD`), or uses a CCC Vault
entry you already have (`--vault FIELD=ENTRY`).

The same settings live in **Settings > On-call**. To turn only the preview on
and set the rest up there: **Settings > Advanced > Preview features >
On-call**, or start CCC with `CCC_FF_ONCALL=1`.

## The home screen

With On-call on, the dashboard opens on one quiet screen instead of the full
session list. It has three lists:

- **Needs you**: the questions and decisions waiting for you, oldest first,
  with the same choices your phone shows. Press a choice or type an answer.
- **Being worked on**: reports an agent has picked up, and where each came from.
- **Fixed this week**: what got closed, and how long after the report.

**Open the full dashboard** takes you to everything else for that browser tab.
The **On-call** button at the top of the left rail brings the home screen back.
To stop it opening by itself in this browser, switch off **Settings > On-call >
Open on the On-call home**.

It opens by itself only on a plain dashboard address in a wide window. A link
to a session or to Settings still goes where it points, and a phone or narrow
window keeps its own layout. A permission prompt is listed but is answered in
its session, not here.

## Automatic workers

Setup enables WatchTower auto-drain for bug tickets, with one worker in the
chosen folder. WatchTower must keep running. Enabling auto-drain starts its
watcher if one is not already running. On a normal install, `wt start
--auto-spawn` starts the managed service. For a foreground process under your
own supervisor, use:

```
wt start --foreground --auto-spawn --host 127.0.0.1 --port 8787
```

Choose an unused port when running an isolated instance. Start the foreground
watcher before setup to prevent setup from starting a managed service.
`--auto-spawn` also enables stage supervision; omitting it does not disable
configured queues' auto-drain.

Choose the fixing agent with WatchTower's queue settings, for example:

```
wt config -q SHOP --engine codex --model gpt-6.1-terra --effort medium
```

The Codex CLI must be installed and logged in. Current WatchTower defaults to
Codex's app-server transport. On-call asks WatchTower to start work as it
files a ticket; in the local proof below the worker started about seven
seconds after the report. A ticket the watcher finds on its own waits out
WatchTower's grace period first (three minutes by default), plus the watcher
interval. A queued report is not proof that an agent has started.

## Where bugs come from

| Source | Add it with | What gets filed |
|---|---|---|
| You | `ccc oncall report "Checkout button does nothing on Safari"` | One ticket |
| A customer's email, SMS or WhatsApp message | A channel that reads customer reports | One ticket per conversation |
| GitHub issues | `ccc oncall source add github` | One ticket per new open issue |
| Sentry | `ccc oncall source add sentry` | One ticket per new unresolved issue |
| PostHog | `ccc oncall source add posthog` | One ticket per new exception group |
| A URL that must stay up | `ccc oncall source add http` | One ticket per outage |
| A command that must keep passing | `ccc oncall source add command` | One ticket per failing run of your test suite or health script |

Each problem is filed once. What is already open when you add a source is
left alone unless you ask for it to be filed.

A public GitHub repository needs no token. A private one needs a token with
read access to issues. To post comments and read reporter replies, save a user
token in that same source's `token_vault` entry with write access to issues.
CCC uses the GitHub API, not `gh`, and adds no separate credential.

## What you get messaged about

One numbered message when:

- an agent asks a question it should not decide by itself
- a Claude Code or Codex agent ends its turn with an explicit question to you
  and then sits idle for two minutes
- an agent is waiting for tool permission
- an agent session stopped in the middle of its work
- a queue stopped moving
- a bug was fixed (you can switch this one off)

It works with no browser open.

```
SHOP-12 needs a decision (#4)
Checkout button does nothing on Safari

Keep the old redirect as a fallback for one release, or remove it now?
Agent recommends: keep it for one release.

Reply with your answer.
```

Reply in plain words, from the same thread:

```
keep it
```

The reply becomes the agent's answer and the agent carries on. With several
messages waiting, start with the number: `4 keep it`. Other words that work
on any channel: `status`, `pause`, `resume`.

For a permission alert, reply with exactly `allow` or `deny` (after the
number, if several messages are waiting: `4 allow`). Capitalization and
surrounding spaces do not matter. Other replies do not answer the prompt. CCC uses its existing approval handler and checks that the
same prompt is still pending. If the handler cannot answer it, use the
session itself. Claude supports its permission hook, or its existing macOS
live-terminal path. Codex needs an actionable app-server approval request.

Quiet hours hold messages until the window ends. At most 12 messages go out
per hour.

```
ccc oncall quiet 23:00 07:00
ccc oncall quiet 23:00 07:00 --tz America/Los_Angeles
ccc oncall quiet off
```

Without `--tz` the window follows the machine's clock, which is UTC on most
servers. `--tz` takes an IANA zone name and follows that zone's daylight
saving changes. `ccc oncall status` prints the window and the zone it is in.

## Small decisions do not reach you

```
ccc oncall minor on
ccc oncall decisions
```

With this on, an agent that hits a small, reversible choice states what it
recommends and how to undo it, and carries on. `ccc oncall decisions` lists
each one with its undo:

```
SHOP-12  Name the new helper bindCheckout or attachCheckoutHandler?
  Decided: bindCheckout. It matches the other helpers in this file.
  Undo: git revert 4ff30e8de6
  Applied.
```

These always come to you, whatever the switch says: sending to customers, a
production change, money or prices, ad spend or a public post, and anything
that cannot be undone.

## Channels

| Channel | Needs | Notes |
|---|---|---|
| Telegram | A bot token | Message your bot once so CCC can find your chat. |
| Email | An address and an app password | Mail servers are found from the address for the common providers. |
| ntfy | Nothing | Phone push with answer buttons. The topic name is the secret. |
| iMessage | CCC running on a Mac signed in to Messages | |
| SMS | Your own Twilio number | |
| WhatsApp | Your own Twilio WhatsApp sender | WhatsApp delivers free text only within 24 hours of your last message to the number. |
| Your own command | Any script | For Slack, Discord or anything else you can script. Send only. |

```
ccc oncall channel add telegram
ccc oncall channel list
ccc oncall channel rm ID
ccc oncall test
```

Your own command receives the message on standard input, with
`CCC_ONCALL_TO`, `CCC_ONCALL_SUBJECT` and `CCC_ONCALL_THREAD` in its
environment.

A command channel added with `--intake` and no recipient of its own is a line
to customers only. Your alerts do not go out on it.

Email uses verified TLS. SMTP defaults to implicit TLS on port 465 and
STARTTLS on other ports. For a custom implicit TLS port, use
`--set smtp_security=tls --set smtp_port=PORT`. IMAP always uses implicit TLS.

Secrets are saved in the CCC Vault. The On-call settings file holds only the
name of the Vault entry.

## Customers

An email, SMS or WhatsApp channel can also read reports from your customers.

- A report from someone on your customer list goes straight to an agent.
- A report from anyone else waits. You get a message and reply `go` or `no`.
  The same holds for a GitHub issue opened by someone who is not an owner,
  member or collaborator of the repository.
- CCC does not send an automatic reply to the reporter.

```
ccc oncall customer add dana@example.com --name Dana
ccc oncall customer add @example.org
ccc oncall customer list
```

An agent, or you, can ask the reporter a question or tell them something, on
the channel they wrote in on:

```
ccc oncall ask SHOP-12 "Which Safari version are you on?"
ccc oncall tell SHOP-12 "This is fixed. Reload the page."
```

You see each message first and reply `yes`, `no`, or your own wording. The
reporter's reply goes back onto the ticket and the agent resumes. You can
turn the approval step off in Settings > On-call.

For a ticket filed from a GitHub issue, `ask` and `tell` post comments on that
issue, under the account the token belongs to. A comment is public, so you
always see it first: turning the approval step off does not apply to GitHub
comments.

Later comments by the issue author go onto the ticket. They answer a ticket
waiting on the reporter, or add a comment if it is still active. Comments by
other people, bots and the account used by CCC are ignored. If the ticket is
closed, an author reply follows the same new-report and unknown-customer
approval rules as an email follow-up.

When a reported bug is fixed, you are asked whether to tell the reporter.
For GitHub reports, that notice is another comment, and it also always waits
for your go. CCC does not close the GitHub issue.

## For agents

A ticket filed from a report carries these lines, so an agent knows how to
reach the reporter:

```
ccc oncall ask <ref> "your question"
ccc oncall tell <ref> "your message"
wt block <ref> --kind awaiting-client --wait-on external --question "what you asked"
```

After asking, the agent parks the ticket with `wt block`. The reporter's
reply releases it.

## Trust rules

- Report text from a customer, a GitHub issue or a monitoring tool is passed
  to the agent as a quoted report, with a note that it is not instructions.
- An email reply answers an alert only when it carries that alert's private
  thread token. Other mail from your own address is held as a report.
- Chat channels trust the sender address you configured.
- The On-call settings and its setup API answer only on the machine CCC runs
  on. Requests through phone access or a proxy are refused.
- The home screen and its answers follow the dashboard's own access rules, the
  same as reading and answering a session. A trusted remote view of the
  dashboard can use them. It cannot approve a permission prompt or change setup.

## Limits

- It is a preview. It is off until you turn it on. The first-run welcome
  sets up an agent; it does not enable On-call or configure message channels.
- Plain-text questions are found in idle Claude Code and Codex sessions only.
  A question is sent once the session has been idle for two minutes, so you
  are not messaged while you are answering it at the keyboard. Questions
  written before On-call was turned on are not sent.
- Two things count as a question: the step CCC's own session footer names
  for you (anything other than "None"), or, without a footer, a closing
  paragraph ending in a question mark with explicit wording such as "Should
  I" or "Would you like". Quoted examples and code are excluded. Indirect
  requests, long messages and other engines can be missed. No model call is
  used; CCC reads at most the last 64 KiB of the transcript and caches the
  result on disk by modification time and size.
- A permission alert needs the prompt's identity and its tool or command
  text. Where CCC has no way to answer a prompt (for Claude Code that means
  no permission hook and not on a Mac terminal), `allow` and `deny` are
  refused with the reason. Answer those prompts in the session.
- The real-session proof covers Codex plain questions through the dashboard
  and local ntfy. It does not verify Claude Code session alerts. The normal
  Codex spawn path sets `approvalPolicy=never` and `dangerFullAccess`, so the
  proof reports permission allow and deny as boundaries. It does not inject
  a fake approval or change that policy to manufacture one.
- Session alerts cover every agent session on the machine, not only the
  On-call queue.
- Agents fix bugs in the code folder you point them at, the way your queue is
  set up to (a commit or a pull request). You decide what deploys.
- The local agent proof covers automatic WatchTower dispatch with the default
  Codex transport, on Linux, for one small seeded bug. It does not cover
  worker recovery, a watcher restart, or other agents.
- GitHub messaging needs a source that is still enabled and a user token that
  can post issue comments. Reply checks must be able to identify that token's
  account through `/user`; tokens that cannot do this cannot receive replies.
  Public issue intake still works without a token, but messaging does not.
- GitHub reply checks share the source timer (300 seconds by default). Each
  check reads one repository-wide page of up to 100 comments, plus the posting
  account identity. Larger backlogs take several checks. The cursor survives
  restarts. There is no per-ticket comment polling. Failed requests wait until
  the next source check; failed sends are recorded without an automatic retry.
- GitHub reply checks start about two minutes before the first successful
  check, without backfilling older comment history. A later edit can bring an
  older comment into the feed; each comment ID is routed at most once. Only
  issues filed after this feature arrived can be messaged; older tickets are
  not migrated. Issue intake still reads only the newest 25 open issues per
  check, with the configured label filter.
- If the token belongs to the same account that opened an issue, that
  account's later comments on it are ignored, like every comment CCC posts.
- Reading issue comments was checked against a live public repository.
  Posting a comment, and the full round trip with a reporter's reply, have
  local transport tests only.
- Status shows configured channels and their last delivery result. It does not
  prove that a worker is running or that a phone displayed a message.
- ntfy decision replies use the separate `<topic>-reply` topic. Publish
  `NUMBER your answer` there; fixed notices also offer answer buttons.
- WhatsApp and SMS need your own Twilio number. iMessage needs a Mac.

## Local protocol proof

Run `python3 scripts/oncall-proof.py` with Docker, OpenSSL and Python 3.
WatchTower is optional; without it the ticket check stops at that boundary.
The script downloads pinned ntfy and GreenMail images, uses temporary state and
a local TLS certificate, and removes its containers and network when done.
It does not use your running CCC server or send to external accounts.

It checks ntfy delivery and replies, SMTP delivery, IMAP replies, and, when
WatchTower is installed, a real isolated ticket decision and a fixed notice to
a local reporter mailbox. The HTTP adapter uses the real On-call API, engine
and store. It does not exercise the dashboard server's routing or preview gate.
By default no agent session runs. Closing a fixture ticket proves notification
routing, not that an agent fixed a bug.

On Linux, opt in to one paid agent run with:

```
python3 scripts/oncall-proof.py --with-agent
```

This requires WatchTower and a logged-in Codex CLI. It copies only `auth.json`
from your Codex home into a temporary home. It does not copy your configuration,
sessions or skills. Queue state, worker records, git configuration and the code
repository are temporary. The agent uses `gpt-6.1-terra` at medium effort;
`--model` picks another. The model connection uses your Codex account; mail
and ntfy stay local. If Codex renews its login during the run, the renewed
login is discarded with the temporary home and the script says so; Codex may
then ask you to sign in again.

The agent stage uses a real foreground WatchTower watcher, started before
setup with a temporary HOME and an OS-assigned local port. It uses the queue
created by setup and the documented model selection command above. It leaves
auto-drain, the grace period and the Codex transport at their defaults. An email
reports a seeded addition bug. In automatic mode the script never starts a worker itself. The
watcher and normal report dispatch are responsible for starting it.

One bounded observer allows eight minutes for dispatch and the fix. It prints
the report-to-observed-worker-start time (up to one second of observation
latency) and the worker identity. It checks a new commit, an unchanged test,
a passing test and ticket closure with that same commit. It then approves the
fixed notice over local ntfy and checks the mailbox before and after approval.
It also prints report-to-notice time. Protocol fixture tickets run only without
`--with-agent`, so they cannot be picked up by the fixing worker.

The previous explicit-start comparison remains available with
`--with-agent --explicit-start`. It disables auto-drain and starts a single
Codex exec worker directly. That comparison does not prove automatic dispatch.

Missing Codex, login or an importable WatchTower is a boundary. A failed watcher,
automatic dispatch timeout or failed fix fails the proof and prints local logs.
The script stops its watcher and kills processes with its unique temporary HOME,
including detached workers, on normal exit and parent-observed child failure or
timeout. Containers and the local network are removed in the parent's cleanup.
A host crash or uncatchable termination of the parent cannot run cleanup.

The automatic stage needs Linux: workers detach from the watcher, and the
script finds them through `/proc` to stop them. Elsewhere it says so and runs
the protocol checks only; `--explicit-start` still works there. Running the
agent stage sends model requests through the copied Codex login.

`ccc oncall report` creates an owner report without a reporter address. The
proof checks that command separately, then uses incoming email for the report
that receives the fixed notice. It does not attach a fabricated reporter to an
owner report.

### Real dashboard session proof

On Linux, with Docker and a logged-in Codex CLI:

```
python3 scripts/oncall-session-proof.py --with-agent
```

This is a separate, paid model run. It starts the real dashboard and its
control-plane worker on private state, plus a pinned local ntfy container.
It copies only Codex `auth.json`, uses an allow-listed environment and tool
path, and creates a temporary git repository. It does not use the running
install's port, settings, sessions or queues. Telemetry is disabled for this
run and background engine maintenance is deferred beyond the proof deadline.
Only the Codex model connection uses the copied account; ntfy stays local.

The script configures On-call through HTTP and spawns `gpt-6.1-terra` at
medium effort through `/api/sessions/spawn`. It waits for an explicit closing
question that also names the needed answer in any session footer, then checks
delivery after the unchanged 120-second idle grace. A footer saying no answer
is needed fails the proof before the grace wait.
It reports time from the final transcript message and from first observed
idle. ntfy timestamps have one-second precision. It checks for one message,
observes another 20 seconds without a duplicate, publishes a numbered reply
to the reply topic, and checks the spawned session's transcript for that
answer as a user turn and the API for alert closure. This is a bounded
observation of deduplication, not proof that a duplicate can never occur.
No On-call internal method or forced tick is called.

Ephemeral dashboards normally skip the On-call loop entirely. This script
sets `CCC_ONCALL_EPHEMERAL=1` alongside `CCC_EPHEMERAL=1` to run that loop.
The preview flag and On-call enabled setting are still required.
`CCC_ONCALL_DISABLED` still takes precedence.

The output lists pass, fail and boundary rows with timings. Permission allow
and deny are boundaries for the reason above. Readiness, idle, notification
and reply waits have deadlines, with a ten-minute overall limit. On failure
or interruption, the script kills
its processes (including detached children identified by the exact temporary
HOME), removes its named container and deletes temporary state. Linux `/proc`
is required for that cleanup. If the copied login renews, it is discarded;
a later Codex sign-in may be needed.

## Commands

```
ccc oncall setup                 ask a few questions, send a test message, turn it on
ccc oncall status                what is on and what is waiting for you
ccc oncall test [CHANNEL]        send a test message
ccc oncall on | off
ccc oncall channel add TYPE | list | rm ID
ccc oncall source add TYPE | list | rm ID
ccc oncall customer add ADDRESS | list | rm ADDRESS
ccc oncall report "TEXT"         report a bug yourself
ccc oncall ask REF "TEXT"        ask the person who reported a ticket
ccc oncall tell REF "TEXT"       tell them something
ccc oncall minor on | off        let agents decide small reversible things
ccc oncall decisions             what agents decided, each with its undo
```

## More

- [Try it without an account](oncall-demo.md): `ccc oncall demo`.
- [Failed CI runs as a source](oncall-ci.md).
- [Monitoring sources checked against real servers](oncall-monitoring.md).
- [Weekly counts and the daily summary](oncall-week.md): `ccc oncall week`.
- [What On-call does when things break](oncall-reliability.md).
- [Security review and limits](oncall-security.md).

For an always-on Linux machine, see [On-call on a server](oncall-server.md).
- [Escalation steps, people and rotations](oncall-escalation.md).
- [Team chat alerts and reply checks](oncall-team-chat.md).
- [Step-by-step guides with checked terminal output](guides/README.md).
