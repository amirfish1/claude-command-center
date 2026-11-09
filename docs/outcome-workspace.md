# Outcome workspace (preview)

The outcome workspace is an optional way to organize CCC around intended results
rather than individual sessions. **It is off by default.** Enable it explicitly
in Settings to pilot it on an installation with a compatible WatchTower version.
The same persisted setting controls the interface and new outcome-agent dispatch.

When enabled, the main workspace has three destinations:

- **Today:** selected commitments, their finish lines, what changed, the next
  responsible action, and any verified interruption that displaced planned work.
- **Needs you:** prepared decisions requiring the owner's judgment. Technical
  research and failed handoffs remain agent work; missing context is not a reason
  to turn them into owner questions.
- **Everything:** supporting work, sessions, evidence, and the existing detailed
  views used for investigation. Existing history is preserved.

Turning the setting off restores the existing workspace and prevents new
outcome-agent dispatch. It does not undo completed work or terminate unrelated
queue workers. Outcome records remain available when the feature is enabled
again.

## Responsibility survives a session

WatchTower stores the outcome's stable aim, acceptance criteria, responsibility,
next action, links, attempts, and evidence. Outcome records are not claimable
queue tickets, including in a queue with auto-drain enabled.

A lead is a temporary attempt, not a permanently running conversation. It wakes
for meaningful changes such as a completed child task, a failed check, an
uncertain handoff, or a due event. Existing workers execute scoped work; a
separate verification attempt or an appropriate deterministic check establishes
whether acceptance criteria passed. The CCC control-plane worker executes agent
processes. The browser renders state and does not supervise agents.

An outcome can be healthy while waiting for a named external event. If its next
action has no valid owner or recovery path, CCC shows an ownership gap rather
than claiming that work is running.

## Completion and recovery

A closed ticket, a successful routine, an approval, or a published change is a
milestone. It does not by itself establish that the parent outcome was delivered.
Acceptance requires evidence tied to the relevant criteria and artifact. A
failed or inconclusive experiment retains its evidence and leaves a next action.
Session archive readiness remains separate from outcome acceptance.

Recovery first checks receipts and existing execution. An unknown delivery is
not permission to replay a potentially completed operation. Attempts and retries
are bounded; exhausting them leaves an explicit recovery state. State-generation
checks do not guarantee exactly-once external side effects from an agent with
unrestricted credentials.

Money, customer sends, public publication, irreversible operations, and existing
repository approval boundaries remain in force. Enabling this workspace is not
a blanket authorization for those actions.

## Session association

`ccc spawn --outcome-id OUTCOME_ID` supplies an explicit outcome context. The
context explains the finish line without adding authority. A child can inherit a
single explicit parent association; ambiguous or inferred relationships do not
automatically establish contribution. Manual filing or dragging is not required.

While the new workspace is enabled, ordinary UI sessions no longer automatically
create a separate idea ticket after their first reply. Existing tickets and
explicit manual filing remain intact.

## API

These endpoints are additive:

| Endpoint | Purpose |
|---|---|
| `GET /api/outcomes/settings` | Read effective enablement and backend availability |
| `POST /api/outcomes/settings` | Persist an explicit boolean `enabled` value |
| `GET /api/outcomes` | Read outcomes, optionally filtered by `repo_path` |
| `GET /api/outcomes/ID` | Read one outcome |
| `POST /api/outcomes/create` | Create an outcome with a goal and acceptance criteria |
| `POST /api/outcomes/update` | Edit using `id`, `expected_revision`, and `patch` |
| `POST /api/outcomes/link` | Explicitly associate a supporting ticket |
| `POST /api/outcomes/event` | Request an owner review with type `owner_review` |

All POSTs use CCC's existing same-origin checks. Outcome operations are refused
when disabled or unavailable; the settings endpoint remains reachable. Browser
editing cannot write trusted verification receipts or directly mark delivery.
Revision conflicts must be refreshed and reconciled rather than overwritten.

This preview is intended to be evaluated against real follow-through and owner
orientation effort. Counts of closed tickets, reviews, or agent messages alone
are not evidence that the workflow produces useful results.
