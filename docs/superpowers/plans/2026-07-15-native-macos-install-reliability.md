# Native macOS Install Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failing AppleScript-to-Terminal first-launch path with a directly observed native installer process and prove the packaged DMG installs and serves CCC end to end.

**Architecture:** The app launches its bundled `install.sh` as an owned `Process`, sends output to the CCC app log, and watches process exit plus loopback readiness. The shell installer gains an app mode, isolated overrides, and atomic cloning. A macOS smoke harness builds and mounts the DMG, launches the packaged executable against temporary state, probes the dashboard, and verifies shutdown.

**Tech Stack:** Bash 3.2, Swift/Cocoa, Python `unittest`, GitHub Actions macOS runner, `hdiutil`, and `curl`.

## Global Constraints

- Preserve public defaults: GitHub repository, `~/.ccc/claude-command-center`, and port 8090.
- Preserve curl, Homebrew, Linux, Windows, and source-install behavior.
- Keep the default server bind at `127.0.0.1`.
- The DMG app must not control Terminal or request Apple Events permission.
- New clones must never expose a partial checkout at the final path.
- Follow red-green-refactor for each production change.

---

### Task 1: Harden and isolate the shell installer

**Files:**
- Modify: `tests/test_install_script.py`
- Modify: `scripts/install.sh`

**Interfaces:**
- Consumes: existing `CCC_FROM`, `PORT`, and source guard.
- Produces: `CCC_INSTALL_MODE=app`, `CCC_INSTALL_DIR`, `CCC_REPO_URL`, `is_app_install()`, and atomic `sync_repo()`.

- [ ] **Step 1: Write failing configuration tests**

Extend `_run_install_script_function` to accept `env_extra`, then add:

```python
def test_environment_overrides_install_destination_and_repo(self):
    result = _run_install_script_function(
        'printf "%s\\n%s\\n" "$INSTALL_DIR" "$REPO_URL"',
        env_extra={
            "CCC_INSTALL_DIR": "/tmp/ccc-test-install",
            "CCC_REPO_URL": "/tmp/ccc-test-repo",
        },
    )
    self.assertEqual(
        result.stdout.splitlines(),
        ["/tmp/ccc-test-install", "/tmp/ccc-test-repo"],
    )

def test_app_install_mode_is_explicit(self):
    result = _run_install_script_function(
        "is_app_install", env_extra={"CCC_INSTALL_MODE": "app"}
    )
    self.assertEqual(result.returncode, 0)

def test_default_install_mode_is_not_app(self):
    self.assertNotEqual(
        _run_install_script_function("is_app_install").returncode, 0
    )
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_install_script -v
```

Expected: override and mode tests fail because the interfaces do not exist.

- [ ] **Step 3: Implement configuration and app mode**

```bash
REPO_URL="${CCC_REPO_URL:-https://github.com/amirfish1/claude-command-center}"
INSTALL_DIR="${CCC_INSTALL_DIR:-$HOME/.ccc/claude-command-center}"

is_app_install() {
  [ "${CCC_INSTALL_MODE:-}" = "app" ]
}
```

Put this branch first in `launch_server()`:

```bash
if is_app_install; then
  printf 'install: launching app-owned server on port %s\n' "$PORT"
  cd "$INSTALL_DIR"
  exec ./run.sh
fi
```

This branch must not call `ask_install_service` or `open_when_ready`.

- [ ] **Step 4: Write failing atomic-clone tests**

Using `tempfile.TemporaryDirectory` and a real local Git repository, add tests
that prove:

```python
self.assertEqual(
    pathlib.Path(destination, "marker.txt").read_text(), "complete\n"
)
self.assertEqual(list(pathlib.Path(root).glob("installed.installing.*")), [])
self.assertFalse(os.path.exists(destination))  # after a failed clone
self.assertEqual(sentinel.read_text(), "user data\n")  # unexpected dir preserved
```

- [ ] **Step 5: Verify RED**

```bash
python3 -m unittest tests.test_install_script -v
```

Expected: failure-safety assertions fail against direct-to-destination clone.

- [ ] **Step 6: Implement atomic publication**

Replace `sync_repo()` with:

```bash
sync_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    printf 'install: updating existing checkout at %s\n' "$INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi
  if [ -e "$INSTALL_DIR" ]; then
    err "install destination exists but is not a Git checkout: $INSTALL_DIR"
    err "Move it aside or remove it, then retry. No files were changed."
    return 1
  fi
  local parent staging
  parent="$(dirname "$INSTALL_DIR")"
  staging="${INSTALL_DIR}.installing.$$"
  printf 'install: cloning %s to %s\n' "$REPO_URL" "$INSTALL_DIR"
  mkdir -p "$parent"
  if ! git clone "$REPO_URL" "$staging"; then
    rm -rf "$staging"
    err "clone failed; no partial installation was published"
    return 1
  fi
  if ! mv "$staging" "$INSTALL_DIR"; then
    rm -rf "$staging"
    err "could not publish the completed checkout at $INSTALL_DIR"
    return 1
  fi
}
```

- [ ] **Step 7: Verify GREEN and commit**

```bash
python3 -m unittest tests.test_install_script -v
bash -n scripts/install.sh
if command -v shellcheck >/dev/null 2>&1; then shellcheck scripts/install.sh; fi
git commit --only tests/test_install_script.py scripts/install.sh \
  -m "fix(install): make first-launch bootstrap atomic"
```

---

### Task 2: Run first installation as an observed native process

**Files:**
- Modify: `tests/test_smoke.py`
- Modify: `scripts/macapp/main.swift`

**Interfaces:**
- Consumes: bundled installer and Task 1 app mode.
- Produces: environment-aware constants, direct installer process, correct log capture, early-exit detection, and Retry/Open Log/Quit recovery.

- [ ] **Step 1: Write a failing regression assertion**

```python
def test_macapp_first_launch_uses_observed_process_not_terminal(self):
    macapp = pathlib.Path(
        PROJECT_ROOT, "scripts", "macapp", "main.swift"
    ).read_text(encoding="utf-8")
    self.assertNotIn("NSAppleScript", macapp)
    self.assertNotIn('tell application "Terminal"', macapp)
    self.assertIn('proc.arguments = [installScript, "--from=dmg"]', macapp)
    self.assertIn('env["CCC_INSTALL_MODE"] = "app"', macapp)
    self.assertIn("pollUntilReady(process: proc", macapp)
    self.assertIn("!process.isRunning", macapp)
    self.assertIn('alert.addButton(withTitle: "Retry")', macapp)
    self.assertIn('alert.addButton(withTitle: "Open Log")', macapp)
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_smoke -k macapp_first_launch -v
```

Expected: current `NSAppleScript` violates the test.

- [ ] **Step 3: Add environment-aware constants and shared logging**

```swift
let CCC_ENV = ProcessInfo.processInfo.environment
let CCC_PORT = Int(CCC_ENV["CCC_PORT"] ?? "") ?? 8090
let CCC_INSTALL_DIR = CCC_ENV["CCC_INSTALL_DIR"]
    ?? NSString(string: "~/.ccc/claude-command-center").expandingTildeInPath
let CCC_LOG_DIR = CCC_ENV["CCC_LOG_DIR"]
    ?? NSString(string: "~/.claude/command-center/logs").expandingTildeInPath
let CCC_APP_LOG = "\(CCC_LOG_DIR)/app-server.log"
```

Add `processLogHandle: FileHandle?` to `AppDelegate`. Add
`attachAppLog(to:)`, which creates `CCC_LOG_DIR`, opens `CCC_APP_LOG` for
append, assigns the same handle to stdout/stderr, and returns the handle.

- [ ] **Step 4: Replace `runInstaller()`**

```swift
func runInstaller() {
    guard let installScript = Bundle.main.path(
        forResource: "install", ofType: "sh"
    ) else {
        showFatal("Install script missing", "The app bundle is incomplete.")
        return
    }
    loadingLabel.stringValue = "Installing Command Center…"
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/bin/bash")
    proc.arguments = [installScript, "--from=dmg"]
    var env = CCC_ENV
    env["PATH"] = augmentedPath()
    env["PORT"] = "\(CCC_PORT)"
    env["CCC_INSTALL_MODE"] = "app"
    env["CCC_INSTALL_DIR"] = CCC_INSTALL_DIR
    proc.environment = env
    do {
        processLogHandle = try attachAppLog(to: proc)
        try proc.run()
        serverProcess = proc
        pollUntilReady(process: proc, operation: "installation")
    } catch {
        showInstallFailure("Installation could not start", "\(error)")
    }
}
```

Remove `runAppleScript`, temporary script copying, and all Terminal script
source.

- [ ] **Step 5: Observe exit and add recovery**

Change `pollUntilReady` to
`pollUntilReady(process: Process, operation: String)`. Check the port first,
then:

```swift
if !process.isRunning {
    timer.invalidate()
    self.pollTimer = nil
    let tail = logTail(CCC_APP_LOG)
    let detail = "The \(operation) process exited with status " +
        "\(process.terminationStatus).\n\n" +
        (tail.isEmpty ? "No log output was captured." : tail)
    self.showInstallFailure("Command Center could not start", detail)
    return
}
```

Use `CCC_APP_LOG` for timeouts. Add `stopOwnedProcess()` and:

```swift
func showInstallFailure(_ title: String, _ message: String) {
    stopOwnedProcess()
    while true {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message + "\n\nLog: \(CCC_APP_LOG)"
        alert.alertStyle = .critical
        alert.addButton(withTitle: "Retry")
        alert.addButton(withTitle: "Open Log")
        alert.addButton(withTitle: "Quit")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            bootstrap()
            return
        case .alertSecondButtonReturn:
            NSWorkspace.shared.open(URL(fileURLWithPath: CCC_APP_LOG))
        default:
            NSApp.terminate(nil)
            return
        }
    }
}
```

Reuse `attachAppLog`, process-aware polling, and `stopOwnedProcess` in
`spawnServer` and `applicationWillTerminate`.

- [ ] **Step 6: Verify GREEN, compile, build, and commit**

```bash
python3 -m unittest tests.test_smoke -k macapp_first_launch -v
swiftc -typecheck -F scripts/macapp/vendor scripts/macapp/main.swift
./scripts/build-dmg.sh --fast 0.0.0-native-install-test
git commit --only tests/test_smoke.py scripts/macapp/main.swift \
  -m "fix(macapp): run first install as an owned process"
```

Remove only the generated test DMG after recording its successful build.

---

### Task 3: Test the packaged DMG first-launch boundary

**Files:**
- Create: `scripts/test-macapp-install.sh`
- Create: `.github/workflows/macapp-install-smoke.yml`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: Tasks 1–2 overrides and `build-dmg.sh --fast`.
- Produces: one macOS command proving build, mount, clone, launch, HTTP readiness, and shutdown.

- [ ] **Step 1: Write and verify a failing harness assertion**

```python
def test_macapp_install_smoke_harness_exists_and_is_executable(self):
    harness = pathlib.Path(PROJECT_ROOT, "scripts", "test-macapp-install.sh")
    self.assertTrue(harness.is_file())
    self.assertTrue(harness.stat().st_mode & stat.S_IXUSR)
    text = harness.read_text(encoding="utf-8")
    for marker in (
        "build-dmg.sh", "hdiutil attach", "CCC_INSTALL_DIR",
        "/api/version", "server still running after app exit",
    ):
        self.assertIn(marker, text)
```

Run the test and confirm it fails because the script is absent.

- [ ] **Step 2: Create `scripts/test-macapp-install.sh`**

The strict Bash script must:

1. refuse non-macOS and refuse to overwrite its uniquely named test DMG;
2. allocate a temporary home, install path, log path, mount path, and free
   loopback port;
3. build `0.0.0-native-install-test` with `build-dmg.sh --fast`;
4. mount read-only and launch `Contents/MacOS/CCC` with
   `HOME`, `CCC_INSTALL_DIR`, `CCC_REPO_URL=$REPO_ROOT`,
   `CCC_LOG_DIR`, and `CCC_PORT`;
5. poll `/api/version` for at most 60 seconds and fail early if the app exits;
6. verify dashboard title, `install-source=dmg`, and a complete `.git`;
7. terminate the app, wait up to five seconds for the server to disappear, and
   emit exactly `test-macapp-install: packaged first launch passed`;
8. trap EXIT/INT/TERM to stop processes, detach the DMG, and delete only its
   temporary state and uniquely named artifact.

- [ ] **Step 3: Verify the real harness**

```bash
bash -n scripts/test-macapp-install.sh
./scripts/test-macapp-install.sh
```

Expected: packaged first launch passes and no test DMG or temporary mount
remains.

- [ ] **Step 4: Add macOS CI**

Create a `macapp-install-smoke` workflow, scoped to mac-app/installer/build
paths, with:

```yaml
jobs:
  smoke:
    runs-on: macos-14
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - name: Build DMG and verify packaged first launch
        run: ./scripts/test-macapp-install.sh
```

- [ ] **Step 5: Commit the harness**

```bash
git add scripts/test-macapp-install.sh .github/workflows/macapp-install-smoke.yml
git commit --only tests/test_smoke.py scripts/test-macapp-install.sh \
  .github/workflows/macapp-install-smoke.yml \
  -m "test(macapp): verify packaged first-launch install"
```

---

### Task 4: Correct install guidance and record the repair

**Files:**
- Modify: `README.md`
- Modify: `scripts/build-dmg.sh`
- Create: `changelog.d/fixed-native-dmg-install-2026-07-15.md`

**Interfaces:**
- Consumes: final native behavior.
- Produces: accurate public DMG instructions and release-note input.

- [ ] **Step 1: Update current DMG wording**

State that first launch installs under `~/.ccc`, shows progress and recovery in
the app, and requires no Terminal or Automation permission. Remove current
claims that a Terminal window appears from README and generated DMG copy.

- [ ] **Step 2: Add changelog snippet**

```markdown
- Fixed first-time DMG installation silently failing when macOS blocked Terminal automation; the app now runs and observes its installer natively with actionable retry and log recovery.
```

- [ ] **Step 3: Check and commit**

```bash
rg -n "first launch|Terminal|Automation|native" README.md scripts/build-dmg.sh
git diff --check -- README.md scripts/build-dmg.sh \
  changelog.d/fixed-native-dmg-install-2026-07-15.md
git add changelog.d/fixed-native-dmg-install-2026-07-15.md
git commit --only README.md scripts/build-dmg.sh \
  changelog.d/fixed-native-dmg-install-2026-07-15.md \
  -m "docs(install): describe native DMG bootstrap"
```

---

### Task 5: Verification and completion audit

**Files:**
- Verify only; fix only failures caused or exposed by this change.

**Interfaces:**
- Consumes: all prior outputs.
- Produces: fresh requirement-by-requirement completion evidence.

- [ ] **Step 1: Run tests and static checks**

```bash
python3 -m unittest tests.test_install_script -v
python3 -m unittest tests.test_smoke -v
bash -n scripts/install.sh scripts/test-macapp-install.sh scripts/build-dmg.sh
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/install.sh scripts/test-macapp-install.sh scripts/build-dmg.sh
fi
git diff --check
```

- [ ] **Step 2: Run both end-to-end install paths**

Run `install.sh` with a temporary home, local repository URL, isolated install
path, app mode, and free port. Probe `/api/version` and dashboard HTML, stop
the server, and verify no staging directory remains. Then run:

```bash
./scripts/test-macapp-install.sh
```

- [ ] **Step 3: Inspect the built app**

Mount a retained fast-build DMG and verify:

```bash
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d --entitlements :- "$APP" 2>&1
if strings "$APP/Contents/MacOS/CCC" |
   rg "tell application.*Terminal|NSAppleScript"; then
  exit 1
fi
```

- [ ] **Step 4: Audit every reported or discovered failure**

Confirm with current evidence:

- the original Terminal automation boundary is absent from source and binary;
- direct installer behavior still succeeds;
- native app surfaces early exit and the correct log;
- retry cannot publish a partial clone;
- app mode does not open an external browser;
- packaged first launch serves CCC and app exit stops its server;
- Linux Docker smoke remains green in CI; local absence is tracked as
  `OPS-231`;
- no unrelated workspace changes were committed.

- [ ] **Step 5: Report the release obligation**

App-shell fixes reach current DMG users only through a patch release. Do not
bump versions, push, notarize, create a release, or modify the appcast unless
the user explicitly asks to ship.
