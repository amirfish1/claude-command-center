# Native macOS Install Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken AppleScript-to-Terminal first-launch handoff with an observable native installer and prove the built DMG can install and serve CCC from a clean isolated location.

**Architecture:** The macOS app starts its bundled `install.sh` as an owned child process, captures the real log, observes early process exit, and loads the dashboard only after its configured loopback port is ready. The shared shell installer gains an explicit app mode and safe path/repository overrides, while preserving curl, Homebrew, source, macOS, and Linux defaults. A macOS-only integration harness builds and mounts the actual DMG, launches its packaged executable against temporary state, probes the server, and verifies shutdown.

**Tech Stack:** Bash 3.2-compatible shell, Swift/Cocoa `Process`, Python `unittest`, Git, curl, `hdiutil`, ad-hoc codesigning, GitHub Actions macOS runner.

## Global Constraints

- Keep `server.py` stdlib-only and do not change the `/api/*` contract.
- Keep the default server bind on `127.0.0.1` and the default port at `8090`.
- Keep public installer defaults at `https://github.com/amirfish1/claude-command-center` and `~/.ccc/claude-command-center`.
- Do not request Apple Events, Automation, administrator, or privileged-helper permissions.
- Never delete or overwrite a pre-existing non-Git install destination.
- Do not push, release, tag, bump versions, or modify `docs/appcast.xml` in this implementation.
- Follow test-first red/green cycles and commit only the paths belonging to each completed slice.

---

## File map

- `scripts/install.sh`: app mode, environment overrides, atomic first clone.
- `tests/test_install_script.py`: behavioral coverage for app mode and safe cloning.
- `scripts/macapp/main.swift`: direct native install process, shared logging, readiness and recovery UI.
- `tests/test_smoke.py`: mac-app source contract regression coverage.
- `scripts/test-macapp-install.sh`: built-DMG first-launch integration harness.
- `.github/workflows/macapp-install-smoke.yml`: macOS CI coverage for the native boundary.
- `README.md`: accurate DMG first-launch documentation.
- `scripts/build-dmg.sh`: accurate README text embedded in the DMG.
- `changelog.d/fixed-native-macos-install-2026-07-15.md`: user-visible repair note.

### Task 1: Make the shared installer safe and app-aware

**Files:**
- Modify: `tests/test_install_script.py`
- Modify: `scripts/install.sh`

**Interfaces:**
- Consumes: `CCC_FROM`, `PORT`, and existing public installer invocation forms.
- Produces: `is_app_install() -> shell status`, `CCC_INSTALL_DIR`, `CCC_REPO_URL`, and atomic `sync_repo()` behavior used by the native app and integration harness.

- [ ] **Step 1: Write failing installer tests**

Extend `_run_install_script_function` so tests can supply an environment:

```python
def _run_install_script_function(function_call, prelude="", env_extra=None):
    """Source install.sh and invoke one function without running main."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    bash_program = (
        f'{prelude}\n'
        f'source "{INSTALL_SCRIPT}"; '
        f'{function_call}'
    )
    return subprocess.run(
        ["bash", "-c", bash_program],
        capture_output=True,
        text=True,
        env=env,
    )
```

Add tests using real temporary Git repositories:

```python
import tempfile


class TestInstallBehavior(unittest.TestCase):
    def test_app_mode_is_explicit(self):
        result = _run_install_script_function(
            "is_app_install",
            env_extra={"CCC_INSTALL_MODE": "app"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_mode_is_not_app(self):
        result = _run_install_script_function("is_app_install")
        self.assertNotEqual(result.returncode, 0)

    def test_environment_overrides_install_location_and_repository(self):
        result = _run_install_script_function(
            'printf "%s\\n%s" "$INSTALL_DIR" "$REPO_URL"',
            env_extra={
                "CCC_INSTALL_DIR": "/tmp/ccc-test-install",
                "CCC_REPO_URL": "/tmp/ccc-test-repository",
            },
        )
        self.assertEqual(
            result.stdout,
            "/tmp/ccc-test-install\n/tmp/ccc-test-repository",
        )

    def _create_repository(self, root):
        repo = os.path.join(root, "source")
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "CCC Test"], check=True)
        with open(os.path.join(repo, "sentinel.txt"), "w", encoding="utf-8") as fh:
            fh.write("installed\n")
        subprocess.run(["git", "-C", repo, "add", "sentinel.txt"], check=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "test fixture"], check=True)
        return repo

    def test_new_clone_is_published_only_after_git_succeeds(self):
        with tempfile.TemporaryDirectory() as root:
            repo = self._create_repository(root)
            destination = os.path.join(root, "installed", "ccc")
            result = _run_install_script_function(
                "sync_repo",
                env_extra={"CCC_INSTALL_DIR": destination, "CCC_REPO_URL": repo},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isdir(os.path.join(destination, ".git")))
            self.assertEqual(
                open(os.path.join(destination, "sentinel.txt"), encoding="utf-8").read(),
                "installed\n",
            )
            self.assertFalse(os.path.exists(destination + ".installing"))

    def test_failed_clone_leaves_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "installed", "ccc")
            result = _run_install_script_function(
                "sync_repo",
                env_extra={
                    "CCC_INSTALL_DIR": destination,
                    "CCC_REPO_URL": os.path.join(root, "missing-repository"),
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(os.path.exists(destination))
            self.assertFalse(os.path.exists(destination + ".installing"))

    def test_non_git_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "installed", "ccc")
            os.makedirs(destination)
            sentinel = os.path.join(destination, "keep-me.txt")
            with open(sentinel, "w", encoding="utf-8") as fh:
                fh.write("preserve\n")
            result = _run_install_script_function(
                "sync_repo",
                env_extra={
                    "CCC_INSTALL_DIR": destination,
                    "CCC_REPO_URL": os.path.join(root, "unused"),
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(open(sentinel, encoding="utf-8").read(), "preserve\n")
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_install_script.TestInstallBehavior -v
```

Expected: failures because `is_app_install` does not exist, overrides are ignored, clone staging is absent, and a non-Git destination reaches `git clone`.

- [ ] **Step 3: Implement app mode, overrides, and atomic clone**

Replace the installer constants with:

```bash
REPO_URL="${CCC_REPO_URL:-https://github.com/amirfish1/claude-command-center}"
INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"
PORT="${PORT:-8090}"
DASHBOARD_URL="http://localhost:${PORT}"
SOURCE_FILE="$HOME/.claude/command-center/install-source"
```

Add:

```bash
is_app_install() {
  [ "${CCC_INSTALL_MODE:-}" = "app" ]
}
```

Replace `sync_repo()` with:

```bash
sync_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    printf 'install: updating existing checkout at %s\n' "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi

  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: ${INSTALL_DIR}. Move it aside or choose CCC_INSTALL_DIR, then retry. No files were changed."
    return 1
  fi

  local parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing"
  mkdir -p "$parent"
  if [ -e "$staging" ]; then
    err "stale install staging path exists: ${staging}. Remove it after confirming no install is running, then retry."
    return 1
  fi

  printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
  if ! git clone "$REPO_URL" "$staging"; then
    rm -rf "$staging"
    err "clone failed; no partial installation was published"
    return 1
  fi
  if ! mv "$staging" "$INSTALL_DIR"; then
    rm -rf "$staging"
    err "could not publish completed checkout at ${INSTALL_DIR}"
    return 1
  fi
}
```

Make `open_when_ready()` return immediately in app mode, and make
`launch_server()` take a direct app-mode path before prompting:

```bash
open_when_ready() {
  if is_app_install; then
    return 0
  fi
  # existing bounded browser-opening watcher follows
}

launch_server() {
  if is_app_install; then
    printf 'install: launching CCC for the native app on port %s\n' "$PORT"
    cd "$INSTALL_DIR"
    exec ./run.sh
  fi
  # existing interactive-service and foreground branches follow
}
```

- [ ] **Step 4: Run focused and full installer tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_install_script -v
```

Expected: all installer tests pass; shellcheck passes when installed.

- [ ] **Step 5: Commit the installer slice**

```bash
git commit --only scripts/install.sh tests/test_install_script.py -m "fix(install): make app bootstrap safe and observable"
```

### Task 2: Replace Terminal automation with an owned native process

**Files:**
- Modify: `tests/test_smoke.py`
- Modify: `scripts/macapp/main.swift`

**Interfaces:**
- Consumes: `CCC_INSTALL_MODE=app`, `CCC_INSTALL_DIR`, `CCC_REPO_URL`, `CCC_PORT`, and `CCC_LOG_DIR`.
- Produces: an app-owned installer/server `Process`, direct exit observation, shared log path, and Retry/Open Log/Quit recovery.

- [ ] **Step 1: Write failing mac-app contract tests**

Add this test beside the existing mac-app smoke tests:

```python
def test_macapp_first_launch_is_native_and_observable(self):
    macapp = pathlib.Path(
        PROJECT_ROOT, "scripts", "macapp", "main.swift"
    ).read_text(encoding="utf-8")
    self.assertNotIn("NSAppleScript", macapp)
    self.assertNotIn('tell application "Terminal"', macapp)
    self.assertNotIn("ccc-install-", macapp)
    self.assertIn('proc.arguments = [installScript, "--from=dmg"]', macapp)
    self.assertIn('env["CCC_INSTALL_MODE"] = "app"', macapp)
    self.assertIn("process.terminationStatus", macapp)
    self.assertIn('alert.addButton(withTitle: "Retry")', macapp)
    self.assertIn('alert.addButton(withTitle: "Open Log")', macapp)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_macapp_first_launch_is_native_and_observable -v
```

Expected: failure because AppleScript/Terminal code still exists and the native process contract is absent.

- [ ] **Step 3: Add environment-backed constants and shared logging**

Replace the fixed port/install constants and remove `runAppleScript`:

```swift
let CCC_ENV = ProcessInfo.processInfo.environment
let CCC_PORT = Int(CCC_ENV["CCC_PORT"] ?? "") ?? 8090
let CCC_INSTALL_DIR = CCC_ENV["CCC_INSTALL_DIR"]
    ?? NSString(string: "~/.ccc/claude-command-center").expandingTildeInPath
let CCC_LOG_DIR = CCC_ENV["CCC_LOG_DIR"]
    ?? NSString(string: "~/.claude/command-center/logs").expandingTildeInPath
let CCC_LOG_PATH = "\(CCC_LOG_DIR)/app-server.log"
let CCC_URL = URL(string: "http://localhost:\(CCC_PORT)")!
```

Add a process-log helper:

```swift
func attachProcessLog(_ process: Process) throws -> FileHandle {
    try FileManager.default.createDirectory(
        atPath: CCC_LOG_DIR,
        withIntermediateDirectories: true
    )
    if !FileManager.default.fileExists(atPath: CCC_LOG_PATH) {
        FileManager.default.createFile(atPath: CCC_LOG_PATH, contents: nil)
    }
    guard let handle = FileHandle(forWritingAtPath: CCC_LOG_PATH) else {
        throw NSError(
            domain: "CCCInstall",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Cannot open \(CCC_LOG_PATH) for writing"]
        )
    }
    handle.seekToEndOfFile()
    process.standardOutput = handle
    process.standardError = handle
    return handle
}
```

Retain the returned handle in `AppDelegate` as `var serverLogHandle: FileHandle?`
and close it in `applicationWillTerminate` after stopping the owned process.

- [ ] **Step 4: Implement direct first-launch execution**

Replace `runInstaller()` with:

```swift
func runInstaller() {
    guard let installScript = Bundle.main.path(forResource: "install", ofType: "sh") else {
        showFatal(
            "Install script missing",
            "The app bundle is incomplete. Re-download it from github.com/amirfish1/claude-command-center/releases."
        )
        return
    }

    loadingLabel.stringValue = "Installing Command Center…"
    let proc = Process()
    proc.launchPath = "/bin/bash"
    proc.arguments = [installScript, "--from=dmg"]
    proc.currentDirectoryPath = NSHomeDirectory()

    var env = CCC_ENV
    env["PATH"] = augmentedPath()
    env["PORT"] = "\(CCC_PORT)"
    env["CCC_FROM"] = "dmg"
    env["CCC_INSTALL_MODE"] = "app"
    env["CCC_INSTALL_DIR"] = CCC_INSTALL_DIR
    proc.environment = env

    do {
        serverLogHandle = try attachProcessLog(proc)
        try proc.run()
        serverProcess = proc
    } catch {
        showBootstrapFailure("Installation could not start", "\(error)")
        return
    }

    pollUntilReady(process: proc, operation: "installation")
}
```

Update `spawnServer()` to use `CCC_LOG_PATH` and `attachProcessLog`, then call
`pollUntilReady(process: proc, operation: "server startup")`.

- [ ] **Step 5: Observe process exit and add recovery actions**

Change readiness polling to:

```swift
func pollUntilReady(process: Process?, operation: String) {
    let start = Date()
    let timeout: TimeInterval = 60
    pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] timer in
        guard let self = self else { timer.invalidate(); return }
        if portIsBound(CCC_PORT) {
            timer.invalidate()
            self.pollTimer = nil
            self.loadDashboard()
            return
        }
        if let process = process, !process.isRunning {
            timer.invalidate()
            self.pollTimer = nil
            let tail = logTail(CCC_LOG_PATH)
            let detail = "The \(operation) exited with status \(process.terminationStatus)."
                + (tail.isEmpty ? "" : "\n\nLast log lines:\n\n\(tail)")
            self.showBootstrapFailure("Command Center could not start", detail)
            return
        }
        if FileManager.default.fileExists(atPath: "\(CCC_INSTALL_DIR)/run.sh") {
            self.loadingLabel.stringValue = "Starting CCC server…"
        }
        if Date().timeIntervalSince(start) > timeout {
            timer.invalidate()
            self.pollTimer = nil
            let tail = logTail(CCC_LOG_PATH)
            let detail = "The \(operation) did not bind port \(CCC_PORT) within \(Int(timeout)) seconds."
                + (tail.isEmpty ? "" : "\n\nLast log lines:\n\n\(tail)")
            self.showBootstrapFailure("Command Center could not start", detail)
        }
    }
}
```

Add process cleanup and recovery UI:

```swift
func stopOwnedProcess() {
    guard let proc = serverProcess, proc.isRunning else { return }
    proc.terminate()
    let deadline = Date().addingTimeInterval(2.0)
    while proc.isRunning && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.1)
    }
    if proc.isRunning { kill(proc.processIdentifier, SIGKILL) }
}

func showBootstrapFailure(_ title: String, _ message: String) {
    stopOwnedProcess()
    while true {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message + "\n\nLog: \(CCC_LOG_PATH)"
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Retry")
        alert.addButton(withTitle: "Open Log")
        alert.addButton(withTitle: "Quit")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            serverProcess = nil
            bootstrap()
            return
        case .alertSecondButtonReturn:
            NSWorkspace.shared.open(URL(fileURLWithPath: CCC_LOG_PATH))
        default:
            NSApp.terminate(nil)
            return
        }
    }
}
```

Use `stopOwnedProcess()` from `applicationWillTerminate` and close the retained
log handle there. Keep `showFatal` for unrecoverable corrupt-bundle errors.

- [ ] **Step 6: Run focused and full smoke tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_macapp_first_launch_is_native_and_observable -v
python3 -m unittest tests.test_smoke -v
```

Expected: all tests pass.

- [ ] **Step 7: Compile the Swift app source**

Run:

```bash
swiftc -typecheck -F scripts/macapp/vendor scripts/macapp/main.swift
```

Expected: exit 0 with no Swift errors.

- [ ] **Step 8: Commit the native app slice**

```bash
git commit --only scripts/macapp/main.swift tests/test_smoke.py -m "fix(macapp): run first install as an owned process"
```

### Task 3: Add a built-DMG first-launch integration test

**Files:**
- Create: `scripts/test-macapp-install.sh`
- Create: `.github/workflows/macapp-install-smoke.yml`

**Interfaces:**
- Consumes: `build-dmg.sh --fast`, the environment overrides from Tasks 1–2, `/api/version`, and dashboard HTML.
- Produces: a macOS-local and CI command that proves the packaged first-install boundary and owned-server shutdown.

- [ ] **Step 1: Create the failing macOS integration harness**

Create executable `scripts/test-macapp-install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
VERSION="0.0.0-install-smoke"
DMG_PATH="$REPO_ROOT/ccc-v${VERSION}.dmg"
TEST_ROOT="$(mktemp -d -t ccc-macapp-install)"
MOUNT_DIR="$TEST_ROOT/mount"
TEST_HOME="$TEST_ROOT/home"
INSTALL_DIR="$TEST_HOME/.ccc/claude-command-center"
LOG_DIR="$TEST_HOME/.claude/command-center/logs"
APP_PID=""
MOUNTED=0

cleanup() {
  if [ -n "$APP_PID" ] && kill -0 "$APP_PID" 2>/dev/null; then
    kill -TERM "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  if [ "$MOUNTED" -eq 1 ]; then
    hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
  fi
  rm -rf "$TEST_ROOT"
  rm -f "$DMG_PATH"
}
trap cleanup EXIT

if [ "$(uname -s)" != "Darwin" ]; then
  echo "test-macapp-install: macOS-only" >&2
  exit 2
fi
if [ -e "$DMG_PATH" ]; then
  echo "test-macapp-install: refusing to overwrite $DMG_PATH" >&2
  exit 1
fi

PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

mkdir -p "$MOUNT_DIR" "$TEST_HOME" "$LOG_DIR"
"$HERE/build-dmg.sh" --fast "$VERSION"
hdiutil attach -readonly -nobrowse -mountpoint "$MOUNT_DIR" "$DMG_PATH" >/dev/null
MOUNTED=1
APP="$(find "$MOUNT_DIR" -maxdepth 1 -name '*.app' -print -quit)"
if [ -z "$APP" ]; then
  echo "test-macapp-install: app bundle missing from DMG" >&2
  exit 1
fi

HOME="$TEST_HOME" \
CCC_INSTALL_DIR="$INSTALL_DIR" \
CCC_REPO_URL="$REPO_ROOT" \
CCC_LOG_DIR="$LOG_DIR" \
CCC_PORT="$PORT" \
"$APP/Contents/MacOS/CCC" >"$TEST_ROOT/app-stdio.log" 2>&1 &
APP_PID=$!

ready=0
for second in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/version" >"$TEST_ROOT/version.json"; then
    ready=1
    echo "test-macapp-install: server ready after ${second}s"
    break
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "test-macapp-install: app exited before readiness" >&2
    cat "$TEST_ROOT/app-stdio.log" >&2
    cat "$LOG_DIR/app-server.log" >&2 2>/dev/null || true
    exit 1
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "test-macapp-install: server did not become ready" >&2
  cat "$LOG_DIR/app-server.log" >&2 2>/dev/null || true
  exit 1
fi

curl -fsS "http://127.0.0.1:${PORT}/" >"$TEST_ROOT/dashboard.html"
rg -q '<title>Command Center' "$TEST_ROOT/dashboard.html"
python3 -m json.tool "$TEST_ROOT/version.json" >/dev/null
test -d "$INSTALL_DIR/.git"
grep -qx dmg "$TEST_HOME/.claude/command-center/install-source"

kill -TERM "$APP_PID"
wait "$APP_PID" 2>/dev/null || true
APP_PID=""
for _ in $(seq 1 20); do
  if ! nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
    echo "test-macapp-install: OK"
    exit 0
  fi
  sleep 0.25
done
echo "test-macapp-install: app-owned server survived app shutdown" >&2
exit 1
```

Mark it executable with `chmod +x scripts/test-macapp-install.sh`.

- [ ] **Step 2: Run the harness against the pre-fix app and verify RED if done before Task 2, otherwise validate coverage by reverting the app change temporarily**

Run:

```bash
./scripts/test-macapp-install.sh
```

Expected against the original app: timeout because it ignores overrides and attempts Terminal automation. If Task 2 is already committed, use verification-before-completion's regression check: temporarily restore the original `runInstaller` in a throwaway patch, observe failure, then restore the fixed source before continuing.

- [ ] **Step 3: Run the harness with the native implementation and verify GREEN**

Run:

```bash
./scripts/test-macapp-install.sh
```

Expected: the DMG builds and mounts; its packaged app clones into the temporary destination, returns live API/dashboard responses, and its owned server exits after app termination.

- [ ] **Step 4: Add macOS CI coverage**

Create `.github/workflows/macapp-install-smoke.yml`:

```yaml
name: macapp-install-smoke

on:
  push:
    branches: [main]
    paths:
      - scripts/macapp/**
      - scripts/install.sh
      - scripts/build-dmg.sh
      - scripts/test-macapp-install.sh
      - .github/workflows/macapp-install-smoke.yml
  pull_request:
    paths:
      - scripts/macapp/**
      - scripts/install.sh
      - scripts/build-dmg.sh
      - scripts/test-macapp-install.sh
      - .github/workflows/macapp-install-smoke.yml

concurrency:
  group: macapp-install-smoke-${{ github.ref }}
  cancel-in-progress: true

jobs:
  smoke:
    name: built DMG first launch
    runs-on: macos-14
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - name: Build DMG and verify isolated first launch
        run: ./scripts/test-macapp-install.sh
```

- [ ] **Step 5: Validate workflow syntax and rerun the local integration test**

Run:

```bash
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/macapp-install-smoke.yml")'
./scripts/test-macapp-install.sh
```

Expected: YAML parses and the integration test prints `test-macapp-install: OK`.

- [ ] **Step 6: Commit the integration slice**

```bash
git add scripts/test-macapp-install.sh .github/workflows/macapp-install-smoke.yml
git commit --only scripts/test-macapp-install.sh .github/workflows/macapp-install-smoke.yml -m "test(macapp): verify built DMG first launch"
```

### Task 4: Correct the public installation story

**Files:**
- Modify: `README.md`
- Modify: `scripts/build-dmg.sh`
- Create: `changelog.d/fixed-native-macos-install-2026-07-15.md`

**Interfaces:**
- Consumes: the native behavior completed in Tasks 1–3.
- Produces: accurate public and in-DMG guidance plus a release-rollup snippet.

- [ ] **Step 1: Update README DMG copy**

Replace the DMG paragraph with:

```markdown
**DMG** — drag `CCC.app` to Applications and double-click to launch. On first
launch, the app installs its local source into `~/.ccc/claude-command-center`,
shows installation progress, and opens the dashboard when its loopback server
is ready. Installation errors include the real log and recovery actions; CCC
does not automate Terminal or request macOS Automation access. Download the
[latest release](https://github.com/amirfish1/claude-command-center/releases/latest).
```

- [ ] **Step 2: Update the README embedded in the DMG**

Replace the first-launch section in `scripts/build-dmg.sh` with:

```text
First launch only: CCC installs its local source into ~/.ccc and starts
the loopback dashboard server. Progress and recovery actions appear in
the app itself; Terminal and macOS Automation permission are not needed.
```

- [ ] **Step 3: Add the changelog snippet**

Create `changelog.d/fixed-native-macos-install-2026-07-15.md` containing:

```markdown
- Fixed first-launch installation from the macOS DMG by replacing fragile Terminal automation with an observable native installer, actionable recovery, and a built-DMG integration test.
```

- [ ] **Step 4: Check documentation and build-script formatting**

Run:

```bash
git diff --check -- README.md scripts/build-dmg.sh changelog.d/fixed-native-macos-install-2026-07-15.md
bash -n scripts/build-dmg.sh
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit the documentation slice**

```bash
git add changelog.d/fixed-native-macos-install-2026-07-15.md
git commit --only README.md scripts/build-dmg.sh changelog.d/fixed-native-macos-install-2026-07-15.md -m "docs(install): document native DMG bootstrap"
```

### Task 5: Full verification and completion audit

**Files:**
- Verify only; do not change release metadata.

**Interfaces:**
- Consumes: every deliverable from Tasks 1–4.
- Produces: fresh evidence for installer correctness, packaged behavior, regression coverage, security invariants, and a precise failure report.

- [ ] **Step 1: Run shell and installer verification**

```bash
bash -n scripts/install.sh scripts/build-dmg.sh scripts/test-macapp-install.sh
python3 -m unittest tests.test_install_script -v
```

Expected: syntax exits 0 and every installer test passes.

- [ ] **Step 2: Run the repository smoke suite**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: all smoke tests pass with zero failures or errors.

- [ ] **Step 3: Run native compile and packaged first-launch verification**

```bash
swiftc -typecheck -F scripts/macapp/vendor scripts/macapp/main.swift
./scripts/test-macapp-install.sh
```

Expected: Swift typecheck exits 0 and the integration harness prints `test-macapp-install: OK`.

- [ ] **Step 4: Inspect the produced app security and automation posture**

Build a disposable security-inspection DMG, mount it, and run:

```bash
SECURITY_VERSION=0.0.0-security-smoke
SECURITY_DMG="ccc-v${SECURITY_VERSION}.dmg"
SECURITY_MOUNT="$(mktemp -d -t ccc-security-mount)"
./scripts/build-dmg.sh --fast "$SECURITY_VERSION"
hdiutil attach -readonly -nobrowse -mountpoint "$SECURITY_MOUNT" "$SECURITY_DMG" >/dev/null
SECURITY_APP="$(find "$SECURITY_MOUNT" -maxdepth 1 -name '*.app' -print -quit)"
codesign --verify --deep --strict --verbose=2 "$SECURITY_APP"
codesign -d --entitlements :- "$SECURITY_APP"
if rg -n "NSAppleScript|tell application \"Terminal\"|NSAppleEventsUsageDescription|automation.apple-events" scripts/macapp/main.swift scripts/build-dmg.sh; then
  echo "unexpected Terminal automation or Apple Events declaration" >&2
  exit 1
fi
hdiutil detach "$SECURITY_MOUNT" >/dev/null
rm -rf "$SECURITY_MOUNT"
rm -f "$SECURITY_DMG"
```

Expected: signature verification succeeds; no Apple Events entitlement, usage text, or Terminal automation exists.

- [ ] **Step 5: Re-run the direct isolated installer check**

Run the current installer against a temporary home, the local repository, and
an unused loopback port:

```bash
set -euo pipefail
TEST_ROOT="$(mktemp -d -t ccc-direct-install)"
TEST_HOME="$TEST_ROOT/home"
TEST_INSTALL="$TEST_HOME/.ccc/claude-command-center"
TEST_LOG="$TEST_ROOT/install.log"
OPEN_MARKER="$TEST_ROOT/external-browser-opened"
INSTALL_PID=""
cleanup_direct_install() {
  if [ -n "$INSTALL_PID" ] && kill -0 "$INSTALL_PID" 2>/dev/null; then
    kill -TERM "$INSTALL_PID" 2>/dev/null || true
    wait "$INSTALL_PID" 2>/dev/null || true
  fi
  rm -rf "$TEST_ROOT"
}
trap cleanup_direct_install EXIT
TEST_PORT="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
open() { : >"$OPEN_MARKER"; }
export -f open
mkdir -p "$TEST_HOME"
HOME="$TEST_HOME" \
CCC_INSTALL_DIR="$TEST_INSTALL" \
CCC_REPO_URL="$PWD" \
CCC_INSTALL_MODE=app \
PORT="$TEST_PORT" \
bash scripts/install.sh --from=dmg >"$TEST_LOG" 2>&1 &
INSTALL_PID=$!
for second in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${TEST_PORT}/api/version" >"$TEST_ROOT/version.json"; then
    echo "direct installer ready after ${second}s"
    break
  fi
  kill -0 "$INSTALL_PID"
  sleep 1
done
curl -fsS "http://127.0.0.1:${TEST_PORT}/" >"$TEST_ROOT/dashboard.html"
rg -q '<title>Command Center' "$TEST_ROOT/dashboard.html"
python3 -m json.tool "$TEST_ROOT/version.json" >/dev/null
grep -qx dmg "$TEST_HOME/.claude/command-center/install-source"
test ! -e "$OPEN_MARKER"
if rg -q 'Install CCC as a background service' "$TEST_LOG"; then
  echo "app mode unexpectedly prompted for a service install" >&2
  exit 1
fi
kill -TERM "$INSTALL_PID"
wait "$INSTALL_PID" 2>/dev/null || true
INSTALL_PID=""
```

Expected: the API and dashboard respond, attribution is `dmg`, no external
browser marker is created, and no service-install prompt is logged. This
independently proves the shared script path beneath the app.

- [ ] **Step 6: Audit every design requirement against fresh evidence**

Confirm:

- native first launch uses no Terminal automation;
- process exit produces immediate correct-log failure reporting;
- app mode opens no external browser and asks no service question;
- failed clone publishes no partial install;
- non-Git destinations are preserved;
- default install/repository/port/security behavior is unchanged;
- built DMG installs, serves, and shuts down from isolated state;
- public and embedded documentation match behavior;
- a macOS CI workflow covers the previously untested boundary;
- local Docker absence remains reported as OPS-231 rather than misrepresented as passing.

- [ ] **Step 7: Report completion without shipping**

Report commits, exact verification commands/results, all discovered failures,
and the remaining release obligation. Do not push or cut the Sparkle patch
release unless the user explicitly authorizes shipping.
