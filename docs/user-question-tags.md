# Default user question tags

CCC bundles the [exact instruction text](../resources/owner-questions.md). It
asks agents to run `q-next` before a necessary user question, put a tag such as
`[Q<n>:install-scope]` in its first explanatory heading, reuse the tag for
reminders, and acknowledge `[Q<n>] answered` once. It explicitly says that a
number does **not** publish a question to an inbox or justify asking for approval.

## Installation

Standard `scripts/install.sh` and `run.sh` entry paths install the executable
through `scripts/link-ccc-cli.sh`. Native Windows `scripts/install.ps1` reaches
`run.ps1`, which performs the same setup. Direct `server.py` startup also installs
it, so a fresh server does not depend on an optional skill.

The bundled stdlib Python counter is copied into
`~/.claude/command-center/bin/q-next.py`. On Linux/macOS a symlink at
`~/.local/bin/q-next` exposes it; on native Windows the wrapper is
`~/.local/bin/q-next.cmd`. Put that bin directory on PATH if your shell does not
already include it. Python 3 must be available (`python3` for the Unix wrapper).
Any existing custom `q-next` executable or destination wrapper is kept, including
one elsewhere on PATH. The owned copy is updated, but never changes that custom
helper or its state. The Windows wrapper uses the setup interpreter's absolute
path. Native Windows and macOS execution are not verified by Linux-only tests.

CCC session prompts for Claude, Codex, and other engines using the common CCC
session guidance include the same text by default. This changes no global agent
configuration and does not enroll questions in any inbox. Existing sessions
receive it only when the relevant engine rebuilds its prompt; start a new session
for a consistent result.

For sessions launched outside CCC, standard setup **offers** two consent items:
`claude-question-guidance` and `codex-question-guidance`. Approve them in Agent
config access or run `ccc consent approve claude-question-guidance
codex-question-guidance` (on one command line). They insert a marked block in
`~/.claude/CLAUDE.md` and `$CODEX_HOME/AGENTS.md` (default `~/.codex/AGENTS.md`).
Other users' global configuration is never silently enrolled. Review the exact
diff with `ccc consent show <item>`. Existing unmarked instructions containing
both `q-next` and `[Q` are preserved without adding a competing block.

Writes preserve surrounding text and symlinks, back up previous bytes through
the [config consent system](agent-config-consent.md), and are idempotent.
`ccc consent revoke claude-question-guidance codex-question-guidance` removes
only the marked blocks and leaves unmarked custom instructions intact. Revoke
of global guidance does not remove CCC's session prompt guidance or the helper;
neither helper installation nor revoke deletes or resets the counter.

## Counter state and failure handling

The counter remains at `$Q_NEXT_DIR/counter`, or
`~/.local/state/q-next/counter` when unset. A new counter begins at 1; an existing
nonnegative decimal counter continues from its saved value. There is no
platform-specific starting offset. All processes sharing that directory use
`lock.d` with atomic directory creation and atomic counter replacement.

Malformed or unreadable state fails without printing a number or resetting the
counter. An `initialized` marker detects removal of a counter previously used
by this implementation. Lock contention times out after five seconds; locks are
never removed automatically. After a crash, inspect the state and confirm no
process holds the lock before manually removing a stale `lock.d`. Restore
corrupt or missing state from a known value before resuming. Do not run old
custom helpers concurrently if they remove locks or reset malformed state:
CCC preserves custom behavior, so its safety guarantees apply to the bundled
helper only. Sharing state over network filesystems depends on their atomic
mkdir/replace semantics; separate machines are not assigned disjoint ranges.
