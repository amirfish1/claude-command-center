# First-run measurements

Status: partial, stopped at coordinator request to reduce machine load.
This is not a launch readiness claim.

The target is an install line to a message visible in a phone-style client in
under 120 seconds and fewer than ten typed commands. A server accepting an
ntfy publish does not prove that a phone displayed it.

## Confirmed fixes

| Finding | Before | Change | Checked after |
| --- | --- | --- | --- |
| The preview wheel does not provide `ccc` | Built wheel has only the `claude-command-center` entry point and no `ccc` client file | Include the client and add its console entry point; use the Python launcher when the shell launcher is absent | Install the rebuilt wheel into a fresh temporary environment and run `ccc oncall --help`: exit 0, 0.348 seconds |
| Installer cannot write its settings | A failed `mkdir` or redirection exposes a shell diagnostic without a recovery step | Stop with one sentence asking for a writable home directory | Regression test uses a file in place of the settings directory; this fails even under root |
| WatchTower setup script missing | Installer promises a built-in queue fallback that no longer exists | Stop and ask for a fresh checkout | Regression test sources an isolated installer copy with no helper script |

The wheel check verifies the CLI entry point, not dashboard startup, delivery,
or an agent fixing a ticket. The settings test does not simulate every
read-only filesystem layout.

## Clean minimal-image baseline

These are minimal distribution container images, not desktop installations.
All runs used root and no TTY. Times include fixture copying and container
startup and may include an image pull. They are failure timings, not time to
a usable product. Curl uses a local copy of this checkout's installer so the
test does not silently fetch a different CCC revision. Every route has zero
answered prompts because it stops before setup.

| Distribution | Route | Seconds to exit | Result |
| --- | --- | ---: | --- |
| ubuntu:24.04 | curl | 4.920 | curl missing |
| ubuntu:24.04 | uvx | 6.271 | uv missing |
| ubuntu:24.04 | pipx | 4.879 | uv missing |
| ubuntu:24.04 | brew | 5.543 | brew missing |
| ubuntu:22.04 | curl | 15.766 | curl missing |
| ubuntu:22.04 | uvx | 10.803 | uv missing |
| ubuntu:22.04 | pipx | 5.981 | uv missing |
| ubuntu:22.04 | brew | 6.186 | brew missing |
| debian:12 | curl | 5.834 | curl missing |
| debian:12 | uvx | 8.276 | uv missing |
| debian:12 | pipx | 4.761 | uv missing |
| debian:12 | brew | 5.549 | brew missing |
| fedora:latest | curl | 19.163 | git missing |
| fedora:latest | uvx | 5.915 | uv missing |
| fedora:latest | pipx | 5.797 | uv missing |
| fedora:latest | brew | 6.159 | brew missing |

The Ubuntu/Debian prerequisites are documented, but a person who pastes only
the leading README command cannot run it in these images. Python runner
tools and Homebrew also require separate installation. This baseline does
not show that those routes fail once their prerequisites are present.

The first remediation experiment installed prerequisites then disabled all
network access. Curl failed at WatchTower bootstrap after 61.188 seconds;
uvx with a prebuilt wheel failed after 4.938 seconds; pipx timed out after
76.265 seconds. These are deliberately offline failures, not online route
measurements. Package prerequisite installation took 83.002 seconds for the
curl experiment and is additional to its runtime.

That experiment found a real error path: failed WatchTower bootstrap claimed
a built-in queue fallback, returned success and reached a startup traceback.
The helper now returns failure and a connection/retry instruction. `run.sh`
also checks the dependency before starting its worker, including when
bootstrap is explicitly skipped. A private venv without WatchTower and a
copy of `run.sh` exited 1 in 0.069 seconds with that instruction and no
traceback. The later online bootstrap succeeded; full install, first message and browser
checks were not completed.

## Test evidence

Before the changes:

```bash
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
```

416 passed in 80.85 seconds. After the first three fixes, the same suite plus
four new regression checks passed: 420 tests in 140.13 seconds. No smoke test was run and no performance bound
was changed.

## Additional checks

A real `python:3.8-slim` container reported Python 3.8.20 and exited 1 with:

> Install a newer python3, then re-run this installer.

This ran the installer's Python preflight, not the full install. The image
was downloaded first; image download time is not a product timing.

The port preflight was given an occupied loopback port. It returned `busy`
in 2.415 seconds. This verifies detection only, not the full launcher's
next-step message or automatic port selection.

## Online bootstrap and stop point

A later Ubuntu 24.04 experiment allowed package downloads during prerequisite
setup and WatchTower bootstrap, then used an internal Docker network for
runtime. Prerequisites took 109.249 seconds. Product WatchTower bootstrap
succeeded in 14.082 seconds. Docker did not publish the app ports on that
internal network on this host, so the harness stopped before dashboard
readiness. The fixture cleanup then caused secondary copy errors in app logs.
Neither error establishes a dashboard product failure. The unfinished online
runner is excluded; its sanitized evidence remains in `online-results.json`.

The coordinator stopped further work because the machine was overloaded.
All first-run containers, networks, temporary image tags and harness processes
were absent in the cleanup check. Temporary wheel environments were removed.
Shared Docker download and build caches remain.

## Reproduce the delivered checks

From this branch, with Docker and host Python available:

```bash
python3 scripts/first-run-matrix/run.py --quick
python3 scripts/first-run-matrix/run.py
python3 -m pytest -q tests/test_first_run_matrix.py tests/test_first_run_wheel_cli.py tests/test_first_run_installer_errors.py
```

Quick mode measured 10.265 seconds with the image cached. The 16 baseline
attempts took about two minutes in total; downloads and machine load vary.
Both commands return nonzero for the observed prerequisite failures.
The eight focused tests passed in 1.51 seconds. The WatchTower installer suite
plus installer regression tests passed 36 tests with one skip in 22.81 seconds.

The harness README describes the optional, explicitly offline remediation
runs. They are not end-to-end launch proofs. There is no successful terminal
cast to replay, and no clean first-message run under 90 seconds to publish.

## Coverage still missing

- Online completion for all distributions and routes, including a real
  non-root Homebrew installation and documented wheel build in each image.
- Full before-and-after matrix rerun after the final bootstrap error fix.
- Dashboard captures at 1440 and 390 pixels and a phone-style ntfy capture.
- On-call setup, first received test message and first bug report in the same
  clean container, with step timings, prompt counts and typed-command counts.
- Logged-out agent behavior, no-Docker behavior, proxy behavior and full
  non-root read-only-home behavior.
- Full launch behavior on an occupied port. Only its detection helper was
  checked. No-agent, root and no-TTY states occurred in the matrix, but were
  not followed through a working dashboard.
- Plain, actionable last messages for every requested unhappy path. The
  delivered tests cover the fixed installer errors and the old-Python gate,
  not the entire requested set.

No under-two-minute result has been established. On-call stays off by default.
The experiments explicitly enabled preview only in disposable state.

## Restart impact

CCC dashboard: no running service was changed; new installer and launcher
behavior applies on the next invocation from this branch. Worker: no restart
needed. WatchTower: no restart needed. Nothing was pushed or merged.
