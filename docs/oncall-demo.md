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

The default worker is scripted. The ticket backend is also a demo fixture,
not WatchTower: this version does not prove WatchTower worker dispatch,
worker markers or its minor-decision policy. Intake, alert rendering,
delivery recording, reply routing and reporter approval use production code.
A small local cache-ignore decision is recorded with its undo. The script
makes that decision; it does not exercise WatchTower's policy evaluator.

## Controls

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

The browser phone binds to `127.0.0.1` on a random port. Its URL contains a
random capability. Requests must carry that capability and the expected host;
foreign browser origins cannot submit replies. There is no external page asset.
The server closes when the walkthrough ends. The page can then be closed.
A browser reply times out after three minutes. Terminal input waits for you.

The scripted loop takes less than two seconds in the automated Linux check,
excluding human replies. There is no `/proc` dependency. macOS execution and
the paid Codex path still need platform verification.

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
both choices, withheld reporter send, local commit, no-state mode, recording,
HTTP capability and origin checks. The performance assertion measures the
whole scripted command against the two-second startup budget.
