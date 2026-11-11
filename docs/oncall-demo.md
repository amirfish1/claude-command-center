# Try On-call without an account

Run `ccc oncall demo` after installing CCC. Python 3 and Git must be available.
No running dashboard, Docker, phone, message account or agent login is needed.
The preview can stay off.

A temporary project has a real failing Python test. A sample customer report
passes through the production On-call engine and SQLite store. The local
channel carries the actual alert text. Reply `zero` or `hide` to decide what
an empty cart displays. The worker applies that choice, runs the test and
commits the fix locally. Reply `yes` or `no` to the reporter notice. Even a
`yes` sends only to the local demo channel. Nobody outside the demo is contacted.

The default worker is scripted. When WatchTower is installed, the demo uses
its real local store, CLI ticket filing, claim, needs-input marker, owner
answer and commit-verified close. Minor decisions use the same policy setting
as `ccc oncall minor on`, and the displayed decision comes from the real
On-call decisions listing. A persistent private CLI process avoids repeated
Python startup. No watcher or automatic worker scheduler is started.

Without WatchTower, a built-in ticket fixture keeps the demo usable. The
terminal says which backend ran, and JSON includes `backend`. Intake, alert
rendering, delivery recording, reply routing and reporter approval always use
production On-call code. The small cache-ignore change is a separate local
commit; its displayed `git revert` command is tested as an actual undo.

## Controls

- `--fast` skips the short pauses between steps. Pauses are also off with
  `--json` and whenever stdout is not a TTY. A paced scripted run was measured
  at 14.1 seconds before time spent answering.
- `--terminal` reads replies from stdin. This is also the default without
  terminal colour support, or when a browser cannot be opened.
- `--json` runs automatic `zero` and `yes` replies and prints one JSON result.
  This is an explicit test mode, not approval to send a real message.
- `--keep` retains the workspace and prints its path. Otherwise the workspace
  is deleted, including on an error or interrupt.
- `--record [FILE]` saves a plain transcript (default `oncall-demo.txt`). If
  `asciinema` is installed, it also saves a version 2 cast of demo output.
  The cast records emitted text, not raw terminal input or an agent session.
  Recordings are intentional retained files; delete them when finished.
- `--agent` runs installed Codex with a temporary copy of its file login,
  model `gpt-6.1-terra` and medium effort. It can incur model charges and take
  up to three minutes. Claude Code is not supported by this demo option.
  No Claude credentials are read. The copied Codex login is removed even
  with `--keep`. The script still controls tickets, decisions and closure.

The browser phone shows message bubbles, suggested reply buttons, a text box
and a live ticket-state strip. Terminal messages appear in a phone box; colour
is used only on a colour-capable terminal.

The browser phone binds to `127.0.0.1` on a random port. Its URL contains a
random capability. Requests must carry that capability and the expected host;
foreign browser origins cannot submit replies. There is no external page asset.
The server closes when the walkthrough ends. The page can then be closed.
A browser reply times out after three minutes. Terminal input waits for you.

The real-WatchTower scripted loop was measured at 0.74 seconds with `--json`
and 0.66 seconds with `--terminal --fast` on Linux. The paid Codex worker was
run successfully with the specified model and effort in 46.6 seconds. These
are measurements, not timing guarantees on every machine. There is no `/proc`
dependency. Native macOS execution remains unverified; automated tests cover
the portable code with both Linux and macOS platform values.

The final commands are:

```sh
ccc oncall setup
ccc oncall minor on
```

Those commands configure the real service, unlike the demo. They require a
running CCC and WatchTower. Setup asks which repository and message channel
to use. See [On-call setup](oncall.md).

## Contributor checks

```sh
python3 ccc oncall demo --terminal
python3 ccc oncall demo --json
python3 -m pytest tests/test_oncall_demo.py -q
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

The end-to-end tests assert the failing and passing test, delivered decision,
both choices, withheld reporter send, local commit, real policy and undo,
fixture fallback, no-state mode, recording,
HTTP capability and origin checks. The performance assertion measures the
whole scripted command against the two-second startup budget.
