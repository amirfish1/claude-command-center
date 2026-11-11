---
description: Turn bounded phone waiting on or off
disable-model-invocation: true
---
Run `ccc oncall phone-wait $ARGUMENTS`. Arguments are on or off, with optional
`--seconds N` from 1 to 540. Do not guess a mode if the user omitted it.
Report the result. This is local opt-in configuration, not a channel send.
