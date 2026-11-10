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

Setup turns the preview on, asks for a queue name and the code folder agents
work in, adds one message channel, and sends you a test message. If the test
message does not get through, On-call stays off.

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
read access to issues.

## What you get messaged about

One numbered message when:

- an agent asks a question it should not decide by itself
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

Quiet hours hold messages until the window ends. At most 12 messages go out
per hour.

## Small decisions do not reach you

```
ccc oncall minor on
ccc oncall decisions
```

With this on, an agent that hits a small, reversible choice states what it
recommends and how to undo it, and carries on. `ccc oncall decisions` lists
each one with its undo.

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

When a reported bug is fixed, you are asked whether to tell the reporter.

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

- It is a preview. It is off until you turn it on.
- Alerts cover questions an agent raises through its question tool, parked
  tickets, stopped sessions and stuck queues. A permission prompt, or a
  question an agent writes as plain text at the end of a turn, is not an
  alert yet.
- Session alerts cover every agent session on the machine, not only the
  On-call queue.
- Agents fix bugs in the code folder you point them at, the way your queue is
  set up to (a commit or a pull request). You decide what deploys.
- WhatsApp and SMS need your own Twilio number. iMessage needs a Mac.

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
