# Replay closed bug reports

`ccc oncall replay` tries closed issues from a public GitHub repository. It
runs locally. It does not need a running dashboard, a queue, or On-call setup.
The preview must be enabled for this invocation:

```sh
CCC_FF_ONCALL=1 ccc oncall replay OWNER/REPO --days 30 --max 20 --output results
```

You need Git, Python 3.12 or later, a signed-in Codex CLI, and the tools needed
by the target project. The default fixing model is `gpt-6.1-terra` with medium
effort. `--model` changes it for this run only. Model use consumes your model
allowance. Cases run one at a time. `--timeout` bounds each agent run in seconds
(default 600). Setup commands and test runs each have a 120-second limit.
Use a new or empty output directory.

## What is measured

Selection uses closed issues in the last N days, their closing commit events,
and merged pull requests associated with those commits. When REST omits the
closing commit, a read-only GraphQL timeline query through a signed-in `gh`
identifies the closing pull request. Without `gh`, those cases are skipped. The closing commit
must be the pull request's merge commit. At least one added or modified path
must match common test naming conventions. Duplicate merge commits count once.
This can miss real fixes, especially issues closed manually or without a closing
commit event. It does not classify issue labels as proof of a bug.

The checkout starts at the merge commit's first parent. The runner exports
that tree and creates a new local git history. There are no later objects,
refs, reflogs or remotes in the agent checkout. Only the issue title and body
are passed as a quoted report, using On-call's report quoting and trust guard.
No pull request title, body, number or diff is passed to the agent.

Before the agent starts, the runner checks that the selected maintainer tests
fail on the old code and pass on the merged code. After the agent stops, it
replaces those test files with the exact maintainer versions and runs them.
Only test files are transferred to the agent checkout, never the fix code.
Python uses pytest on the selected Python test files. npm projects use their
full `npm test` command. The recorded diff is captured before test replacement,
including changes committed by the agent. File overlap is a comparison of
paths, not a measure of correctness.

Outcomes are `fixed`, `wrong fix`, `no attempt or asked a question`, and
`could not set up`. A fixed result means these maintainer tests passed, not
that the change is safe to merge. No changed files or a structured response saying a question is needed
counts as no attempt. An unsuccessful agent exit with edits counts as wrong fix. Failed dependency installation, unsupported test
runners, missing tests, collection errors, timeouts in tests, or failure to
establish the before/after test check count as could not set up. Once that
check passes, errors or timeouts after agent edits count as wrong fix. Setup cases
are visible but excluded from the evaluated count. Minutes measure the agent
process, not dependency installation; total minutes are also in JSON.

Python setup creates a venv and installs the project plus pytest. It reads
`test`, `tests` or `testing` extras and dependency groups, and common test
requirements files. npm setup uses
`npm install --ignore-scripts`. Projects needing other extras, build steps,
services or package managers can fail setup. Selection is capped at 1,000
issue/event rows per endpoint and 3,000 changed files per pull request. A cap
error is reported, not silently treated as a complete sample.

## Isolation and limits

The temporary HOME and allowlisted environment follow the isolation approach
in the On-call benchmark. Only Codex `auth.json` is copied from the user's
configuration. Credentials and temporary trees are removed at the end. A crash
or uncatchable kill can leave temporary files. Login refreshes in the copy are
not copied back. No user model defaults or telemetry configuration are changed.

The agent uses Codex's workspace-write sandbox with web search disabled. The
prompt prohibits online fix lookup and access outside the checkout. **This is
not a security boundary against reading the real fix.** Public source and
issue bodies can identify the repository or contain solution hints. Codex
sandbox restrictions depend on the host, and files outside the checkout,
including the judge's ground truth, may remain readable. This runner does not
provide a network namespace, filesystem jail or proof that no lookup happened.
Do not present these results as a contamination-free benchmark. Package setup
and tests execute repository code; use a disposable machine for untrusted repos.

GitHub REST access uses GET only, with a one-hour disk cache under
`~/.cache/ccc/oncall-replay`. The GraphQL fallback uses a read-only query sent by `gh api graphql`, also
cached for one hour. Git fetch is read-only and its objects are cached there too. When installed,
`gh api --method GET` supplies GitHub access; otherwise HTTPS GET is used. Rate-limit and access-denial
responses stop selection; the runner does not retry in a loop. It never forks,
pushes, comments, opens an issue or opens a pull request. Package installation
and Codex sign-in/model traffic are separate from GitHub access.

`results.json` and a self-contained `report.html` are saved after each case.
Nothing is published. Results include issue text, diffs, local paths and command
logs; review them before sharing. A selection failure is recorded in JSON.
The sample contains only qualifying closed issues, not all reported bugs, and
cannot establish how On-call would perform on every report.
