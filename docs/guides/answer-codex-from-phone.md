# Answer a Codex question from your phone

Use an On-call channel that can receive replies. Subscribe to its ntfy topic. When a numbered question arrives, publish the number and your answer to the topic with `-reply` appended. The following command runs that round trip locally with a real Codex session.

```console checked slow
$ python3 scripts/oncall-session-proof.py --with-agent
plain_question deduplication | PASS | 20s, one ntfy message
```

![Checked terminal output](img/answer-codex-from-phone.png)

Terminal capture rendered from the checked output above, with lines wrapped.

## What you will see

The agent asks whether to use blue. The proof waits through the unchanged two-minute idle grace, receives one notification, replies, and checks that the answer becomes a user turn in the same session.

## Limits

This checks local ntfy, not a phone display. It covers Codex only. Claude Code question detection has tests but was not run with a real Claude session. Indirect questions can be missed. Old questions from before enablement are ignored.

[Guide setup and verification](README.md)
