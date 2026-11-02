<!-- CCC:question-guidance -->
## User question tags

Before asking the user a question or decision, run `q-next` to reserve the next
number from the shared machine counter. Tag the question `[Q<n>:short-kebab-slug]`
(replace `<n>` with the reserved number). Put the tag in the heading of the first
message that gives the full explanation. Reuse that exact tag in every reminder;
never renumber an existing question. Once answered, say `[Q<n>] answered` once.
If `q-next` fails, report the failure; do not invent a number or reset the counter.

Numbering alone does not publish a question to an inbox. Use an explicitly
supported question/inbox tool separately when appropriate. Do not manufacture
questions or approval requests: carry out already authorized work and ask only
when information, a consequential choice, or required approval is missing.

## Explicitly promoted session questions

Keep ordinary interactive questions in their originating conversation. When a
question explicitly needs the owner's attention outside that conversation,
register it locally with `ccc question create`, then explicitly promote that
exact request and revision with `ccc question promote`. Preserve its original
question tag as an alias; numbering alone never publishes it.

When notified that an answer is saved, run `ccc question inbox --session-id`
with your own session UUID. Verify the answer's session, question and generation,
read its exact text, and acknowledge it with `ccc question consume` and the
actual message identifier. Polling does not acknowledge receipt. Never repeat
downstream work for an already consumed answer. Check this inbox when resuming
a session with promoted questions; a missed advisory wake does not lose its
durable answer. A saved answer, session receipt and completed work are separate.
