# Stop coding agents asking about small reversible decisions

Turn on minor decisions, then inspect the decision list:

```console checked
$ ccc oncall minor on
Agents now decide small reversible things and keep going. Money, messages to customers, public posts and anything that cannot be undone still come to you.
```
```console checked
$ ccc oncall decisions
Agents have not decided anything without you.
```

![Checked terminal output](img/small-decisions.png)

Terminal capture rendered from the checked output above, with lines wrapped.

## What you will see

The isolated queue starts with no decisions. On a working queue, the list includes the recommendation, undo instructions and follow-through state.

## Limits

This run verifies the policy switch and empty list. It does not demonstrate an agent making and reversing a decision. There is no On-call undo command: follow the recorded undo instructions. Money, customer messages, public posts and irreversible work still require approval. Production changes are also outside minor decisions.

[Guide setup and verification](README.md)
