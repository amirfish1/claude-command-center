# On-call (preview)

Bugs reported by your customers and by your monitoring go to coding agents
that fix them. CCC messages you only when an agent needs a real decision or
has stopped, and your reply from the same thread is the agent's answer.

On-call is a preview. It is off until you turn it on, and nothing is created
on disk before that.

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
- The On-call settings and its API answer only on the machine CCC runs on.
  Requests through phone access or a proxy are refused.

## Limits

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
- The GitHub comment round trip has local transport tests, but has not been
  verified against a live GitHub account.

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
- Session alerts cover every agent session on the machine, not only the
  On-call queue.
- Agents fix bugs in the code folder you point them at, the way your queue is
  set up to (a commit or a pull request). You decide what deploys.
- The local agent proof covers an explicit WatchTower drain-once launch with
  Codex exec. It does not verify automatic dispatch, worker recovery, or the
  default app-server transport.
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

On POSIX systems, opt in to one paid agent run with:

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

The extra stage sends an email reporting a seeded addition bug. It starts a
real WatchTower drain-once worker for the resulting ticket, with auto-drain off
and Codex's `exec` transport. This is an explicit worker start, not proof of
automatic dispatch or the default app-server transport. The worker has four
minutes to finish. The script checks the new commit, unchanged test file,
passing test and ticket closure with the same commit. It then approves the
fixed notice over local ntfy and checks the reporter mailbox before and after
approval. It prints the report-to-notice time. It does not force an artificial
code decision; the original protocol stage still checks decision replies.
Missing Codex, login or an importable WatchTower is reported as a boundary. A worker failure fails the
proof. Worker output is printed on failure; temporary state is removed.

`ccc oncall report` creates an owner report without a reporter address. The
proof checks that command separately, then uses incoming email for the report
that receives the fixed notice. It does not attach a fabricated reporter to an
owner report.

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
