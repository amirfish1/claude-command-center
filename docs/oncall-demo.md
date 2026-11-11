# Try On-call without an account

Run `ccc oncall demo` after installing CCC. Python 3 and Git must be available.
No running dashboard, Docker, phone, message account or agent login is needed.
The preview can stay off.

A temporary project has a real failing Python test. A sample customer report
passes through the production On-call engine and SQLite store. The local
channel carries the actual alert text. Reply `zero` or `hide` to decide what
an empty cart displays.

Before the fix, the customer also requests a refund. With WatchTower installed,
its real policy rejects that money proposal even when minor decisions are on.
Reply `approve` or `decline`; only then does a simulated local refund ledger
change. No payment provider is contacted. Without WatchTower, this step is
labelled as a fixture, not a proof of its policy.

The worker applies your display choice, runs the test and commits the fix
locally. Reply `yes` or `no` to the reporter notice. Even a `yes` sends only to
the local demo channel. Nobody outside the demo is contacted.

The default worker is scripted. When WatchTower is installed, the demo uses
its real local store, CLI ticket filing, claim, needs-input marker, owner
answer and commit-verified close. Minor decisions use the same policy setting
as `ccc oncall minor on`, and the displayed decision comes from the real
On-call decisions listing. A persistent private CLI process avoids repeated
Python startup. No watcher or automatic worker scheduler is started. Closure
uses WatchTower's real commit verifier and queue API, skipping CLI housekeeping that can sync
the installed WatchTower checkout.

Without WatchTower, a built-in ticket fixture keeps the demo usable. The
terminal says which backend ran, and JSON includes `backend`. Intake, alert
rendering, delivery recording, reply routing and reporter approval always use
production On-call code. The small cache-ignore change is a separate local
commit; its displayed `git revert` command is tested as an actual undo.

## Choose a scene

A plain interactive `ccc oncall demo` starts with a one-key picker:
`1` customer, `2` CI, `3` monitoring, or `4` all. Without a TTY it defaults
to customer. You can choose directly:

```sh
ccc oncall demo --scene customer
ccc oncall demo --scene ci
ccc oncall demo --scene monitoring
ccc oncall demo --scene all
```

CI runs a tiny local Actions-shaped HTTP server. The production `CISource`
reads an empty baseline, then a red build with a real failing-test log, files
its ticket, and observes a green run after the committed fix passes the test.
This exercises the API protocol; it does not contact GitHub or push a commit.

Monitoring runs a local application health URL and writes the test failure
into `application.log`. The real `command` source reads that log, and the
real `http` source detects HTTP 503. Both file tickets. The same fix clears
the log and returns the health endpoint to HTTP 200 before the demo ends.

All scenes use their own workspace and ticket store. The source scenes do
not insert an artificial owner decision: the worker fixes the known bug.
Only the customer scene includes the product choice, refund hard stop,
reporter approval and minor decision. `all` runs the three scenes in sequence;
allow about two minutes with pauses and time to read and reply.

## Controls

- `--fast` skips the short pauses between steps. Pauses are also off with
  `--json` and whenever stdout is not a TTY, except a paced `--record` run.
- `--terminal` reads replies from stdin. This is also the default without
  terminal colour support, or when a browser cannot be opened.
- `--json` runs automatic `zero`, `decline` and `yes` replies and prints one JSON result.
  This is an explicit test mode, not approval to send a real message.
- `--keep` retains the workspace and prints its path. Otherwise the workspace
  is deleted, including on an error or interrupt.
- `--record [NAME]` runs the scripted customer scene with automatic demo
  replies: zero, decline the refund, and yes to the local reporter notice.
  It saves a transcript and an asciicast v2 file under
  `.claude/screenshots/demo/` in the current folder. No asciinema installation
  is required. With the repo's Puppeteer setup and ffmpeg available, it also
  records the real browser phone at 800 pixels wide as `customer.mp4` and
  `customer.gif`, each checked against a 4 MB limit. The decisions remain on
  screen for several seconds before the recording driver taps a reply.
  `--json --record`, `--fast --record` and `--terminal --record` save text
  and cast only. Encoding does not run in fast mode. Browser capture is
  optional; if the tools are absent, the terminal recording still works. Recordings
  and three review frames are intentionally retained. `recording.json`
  gives the paths, duration and byte counts. Delete that folder to remove them.
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

Each fast scene has a two-second full-command test budget. Busy shared
machines can exceed it. The paid Codex worker was run successfully with the
specified model and effort in 46.6 seconds. This is a measurement, not a timing
guarantee on every machine. There is no `/proc` dependency. Native macOS execution remains unverified; automated tests cover
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
both choices, withheld reporter send, refund hard stop, local commit, real
policy and undo, every scene with both ticket backends, fixture fallback,
no-state mode, recording,
HTTP capability and origin checks. The performance assertion measures the
whole scripted command against the two-second startup budget.
