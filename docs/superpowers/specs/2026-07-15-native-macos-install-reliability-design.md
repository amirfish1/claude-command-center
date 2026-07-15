# Native macOS Install Reliability Design

## Problem

The v5.6.0 DMG opens Terminal on first launch but, on affected Macs, never
runs the bundled installer command. The app then waits 60 seconds and reports
that port 8090 never bound.

The failure is at the app-to-Terminal boundary, not in `install.sh`:

- the released app uses `NSAppleScript` to send `activate` and `do script`
  events to Terminal;
- the notarized app has neither the Apple Events hardened-runtime entitlement
  nor an `NSAppleEventsUsageDescription`;
- the helper discards the AppleScript error dictionary, so the app cannot
  distinguish denied automation from a slow server;
- the timeout reads `app-server.log`, although the installer never started and
  therefore never wrote that log.

The direct installer succeeds in an isolated macOS home and the existing
installer unit tests pass. The current Linux container smoke test deliberately
bypasses the native app boundary, which is why it did not catch this failure.

## Goals

- First DMG launch must install and start CCC without controlling Terminal or
  requiring Automation permission.
- The app must show the real failure immediately when prerequisites, cloning,
  installation, or server startup fail.
- First launch must not also open an external browser.
- A retry must be safe after a partial or failed install.
- Curl, Homebrew, Linux, and source-install behavior must remain compatible.
- A repeatable test must exercise the built DMG from first launch through a
  live dashboard response on an isolated path and port.

## Non-goals

- Bundling Python, Git, or the repository inside the app.
- Installing a persistent launchd service from the DMG flow. The native app
  owns the server it starts and terminates it on explicit app quit.
- Changing the server's loopback-only security boundary.

## Considered approaches

### 1. Native child-process installer — selected

Run the bundled `install.sh` directly with `Process`, an augmented `PATH`, and
an explicit app-install mode. Capture stdout and stderr in the existing CCC log
directory and retain the process as the app-owned server after `install.sh`
execs `run.sh`.

This removes the failing system boundary, gives the app exact process status,
and requires no new macOS permission.

### 2. Open a generated `.command` file

LaunchServices can ask Terminal to open an executable `.command` without Apple
Events. This is less fragile than AppleScript but still leaves progress and
errors in another app, complicates retries, and preserves a needless Terminal
dependency.

### 3. Add Apple Events entitlement and usage text

This is the smallest patch, but first launch would gain another permission
prompt and would still fail when permission is denied or Terminal automation is
restricted. It does not meet the reliability or diagnostic goals.

## Architecture

### Native bootstrap

`AppDelegate.runInstaller()` starts `/bin/bash <bundled install.sh>
--from=dmg` directly. Its environment includes:

- the existing augmented executable search path;
- `CCC_INSTALL_MODE=app`, which disables Terminal prompts and external browser
  opening;
- the selected `CCC_PORT`, install directory, repository URL, and log directory
  when overrides are present.

The process writes both stdout and stderr to `app-server.log`. Because
`install.sh` ends by `exec`-ing `run.sh` in app mode, the same `Process` becomes
the server process and remains under the app's existing shutdown ownership.

The app no longer contains `NSAppleScript`, creates temporary installer files,
or sends events to Terminal.

### Readiness and failure states

The bootstrap state is observable at two boundaries:

1. **Process state:** if the installer/server process exits before the port is
   ready, the app stops waiting immediately and displays its exit status plus
   the tail of the correct log.
2. **Service state:** while the process remains alive, the app polls the chosen
   loopback port. Binding loads the dashboard; exceeding the bounded timeout
   reports the current log tail.

The loading label advances from “Installing Command Center…” to “Starting CCC
server…” once a complete checkout is visible.

Install failures present three actions:

- **Retry** starts a new preflight/bootstrap attempt;
- **Open Log** reveals the captured log and keeps the recovery dialog available;
- **Quit** terminates the app.

### Installer modes and isolation

`scripts/install.sh` keeps its public defaults but accepts namespaced overrides:

- `CCC_INSTALL_DIR` (default `~/.ccc/claude-command-center`);
- `CCC_REPO_URL` (default public GitHub repository);
- `CCC_INSTALL_MODE=app` (no service question and no browser opener).

New clones are staged in a sibling temporary directory and renamed into place
only after Git completes. A trap removes an incomplete staging directory. An
existing valid Git checkout continues to use `pull --ff-only`; an unexpected
non-Git destination fails without deleting user files.

The app reads matching optional `CCC_INSTALL_DIR`, `CCC_REPO_URL`, `CCC_PORT`,
and `CCC_LOG_DIR` environment overrides. Defaults remain unchanged. These
overrides make the real app boundary testable without touching a developer's
normal CCC installation or port.

## Testing

### Regression tests first

Installer unit tests will cover:

- app mode bypasses the service prompt and external browser opener;
- environment overrides select an isolated repository and destination;
- a new clone is published atomically;
- a failed clone leaves no partial destination and preserves a pre-existing
  non-Git directory.

Mac-app smoke assertions will fail against the current implementation by
requiring direct `Process` execution, process-exit observation, real-log error
reporting, and absence of `NSAppleScript`/Terminal automation.

### Native DMG end-to-end test

A macOS-only script will:

1. build an ad-hoc signed DMG from the current checkout;
2. mount it read-only;
3. launch the packaged app executable with a temporary home, install directory,
   local repository URL, log directory, and unused loopback port;
4. wait for `/api/version` and the dashboard HTML;
5. verify the install attribution and absence of an external-browser handoff;
6. terminate the app and confirm the app-owned server exits;
7. unmount the DMG and remove all temporary state.

The existing Linux clean-install workflow remains as independent coverage for
the curl path. A macOS CI workflow runs the native smoke test when app,
installer, build, or test-harness files change.

## Documentation and release

README and DMG copy will describe a native first-launch installation rather
than a Terminal window. A `fixed` changelog snippet will record the repair.

Because `scripts/macapp/main.swift` changes, source changes alone do not reach
existing DMG users. Shipping the fix requires the repository's normal patch
release path: version bump, notarized DMG, GitHub release asset, Sparkle
signature/appcast update, commit, and push. Release execution is outside this
implementation unless explicitly requested.

## Security

The installer continues to clone only the configured Git repository and starts
the server on loopback. Environment overrides are local process configuration,
not network inputs. No privileged helper, elevated permission, Apple Events
entitlement, or broader bind address is introduced.
