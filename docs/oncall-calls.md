# On-call phone calls

Available behind the `oncall` preview flag.

## Setup

A call channel references an existing SMS channel and reuses its Twilio
credentials, sending number, and recipient number. It needs no separate secrets
or addresses. With an SMS channel named `owner-sms`, use:

```sh
ccc oncall channel add call --set sms_channel=owner-sms
```

Optional settings, passed as additional `--set` arguments:

| Setting | Default | Meaning |
| --- | --- | --- |
| `kinds` | `stopped,stuck,ci_failed` | Alert kinds eligible for calls. |
| `after_minutes` | `10` | Minimum minutes since escalation enrollment before a call. |
| `hourly_cap` | `2` | Global call-attempt limit per hour; the lowest configured call-channel cap wins. |
| `during_quiet` | `false` | Respect quiet hours unless explicitly set to `true`. |

## When calls happen

Calls are escalation only, never initial delivery. An alert must
match an eligible kind, and have a successful earlier SMS step through the
referenced channel. The minimum delay since enrollment applies even to urgent
alerts. Every call attempt counts toward the global cap. Quiet hours apply
unless `during_quiet=true` is explicit.

Stuck queue notices can escalate even though they normally need no reply.
Answer with the alert number from the SMS to acknowledge them. This clears the
notice; it does not repair the queue. `ci_failed` matches parked decisions on
tickets filed by a configured CI source, using the stored source and ticket
link. It does not ring for every failed build while an agent is still working.
CI sources must remain configured for newly collected decisions to be classified.

Add SMS first in your escalation plan, then the generated call channel ID
shown by `ccc oncall channel list`, then any backup channel. For example:

```sh
ccc oncall escalate add --channels owner-sms --after 0
ccc oncall escalate add --channels call --after 10
ccc oncall escalate add --channels backup-chat --after 15
```

## What the call does

Twilio receives inline TwiML with `Say` and `Hangup`. The spoken message gives
numbered choices when the alert supplies choices, but the recipient answers in the existing SMS conversation
using the choice words. Keypad input and numeric choice replies are not answers.
There is no public webhook and no recording.

A failed call is not retried. The next scheduled escalation takes over
regardless of call status. API acceptance does not prove a person heard the
message: voicemail may answer. A call already in flight cannot be recalled.

## Testing and evidence

The general `ccc oncall test` skips calls. The explicit command
`ccc oncall test --call` rings the phone and costs money.

No actual provider test has been performed. The following documentation was
checked during implementation; no live provider behavior is claimed here:

- [Twilio Call resource](https://www.twilio.com/docs/voice/api/call-resource)
  supports inline TwiML.
- [Twilio Gather](https://www.twilio.com/docs/voice/twiml/gather) submits input
  to an HTTP action; with inline TwiML, that action requires an absolute URL.
  This contract uses `Say`/`Hangup` and SMS replies instead of `Gather`.

## Limits

The SMS provider accepting a message does not prove that the phone received it.
Calls wait for provider acceptance of the earlier SMS, not a delivery receipt.
A busy, unanswered or completed call never counts as an answer. Call status is
not polled; the next step runs at its configured time in all these cases.
The normal On-call tick must be running. There is no new daemon.
The cap covers escalation attempts, including unknown outcomes. Explicit test
calls bypass escalation policy. Multiple processes writing one store are not
supported. Existing SMS sender checks and durable answer reservations apply.
