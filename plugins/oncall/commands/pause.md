---
description: Pause CCC On-call
disable-model-invocation: true
---
Run `ccc oncall agent-service`, then `ccc oncall off` with the user's supplied arguments: $ARGUMENTS.
If ccc is missing, say: Install CCC using https://github.com/amirfish1/claude-command-center#installation, then run ccc oncall setup.
Do not invent channel settings or send a test until the user requests setup or test.
Report the command output and any error plainly.
