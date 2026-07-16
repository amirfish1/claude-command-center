# Python 3.9 DMG Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v5.8.1 so the public macOS DMG installs and runs with Apple's `/usr/bin/python3` 3.9.6, and leave the permanent stable DMG link ready to share.

**Architecture:** Postpone `server.py` annotations so Python 3.9 does not evaluate 3.10-style union hints, then make `install.sh`, `run.sh`, package metadata, documentation, and CI agree on a Python 3.9 floor. Prove the fix with a real Python 3.9 import/boot test and a packaged native-app installation forced through `/usr/bin/python3`, then use the repository's release orchestrator to publish signed/notarized versioned and stable DMGs, Sparkle metadata, and Homebrew.

**Tech Stack:** Python 3.9–3.14 stdlib, Bash 3.2, Swift/Cocoa, pytest/unittest, GitHub Actions, `hdiutil`, codesign/Gatekeeper/notarytool, Sparkle, GitHub CLI, and Homebrew.

## Global Constraints

- Preserve the stdlib-only Python server and every existing `/api/*` contract.
- The runtime floor becomes Python 3.9 exactly; Python 3.8 and older remain rejected.
- Honor `CCC_PYTHON` consistently in installation and launch.
- Do not bundle, download, or install Python in v5.8.1.
- Preserve curl, Homebrew, Linux, Windows, and native macOS install paths.
- Preserve unrelated shared-worktree edits and commit only named files.
- The friend-facing link remains `https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg`.
- Public demo Kanban removal is a separate follow-up after this release.

---

### Task 1: Lock the Python 3.9 contract with failing tests

**Files:**
- Create: `tests/test_python39_compatibility.py`
- Modify: `tests/test_install_script.py`
- Modify: `tests/test_run_script_python_static.py`

**Interfaces:**
- Consumes: `scripts/install.sh`, `run.sh`, `server.py`, and a real Python 3.9 executable.
- Produces: `_find_python39()` plus install, launch, and import regression gates.

- [ ] **Step 1: Add installer and launcher source-contract tests**

Append to `TestInstallScript` in `tests/test_install_script.py`:

```python
    def test_python_gate_accepts_39_and_honors_override(self):
        script = Path(INSTALL_SCRIPT).read_text(encoding="utf-8")
        self.assertIn('PYTHON3="${CCC_PYTHON:-python3}"', script)
        self.assertIn("sys.version_info >= (3, 9)", script)
        self.assertIn("requires Python 3.9+", script)
```

Append to `tests/test_run_script_python_static.py`:

```python
def test_run_script_accepts_python39():
    script = (PROJECT_ROOT / "run.sh").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 9)" in script
    assert "requires Python 3.9+" in script
```

- [ ] **Step 2: Add a real Python 3.9 import test**

Create `tests/test_python39_compatibility.py`:

```python
"""The supported Python floor must import the production server."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

def _find_python39():
    candidates = [
        os.environ.get("CCC_TEST_PYTHON39"),
        sys.executable if sys.version_info[:2] == (3, 9) else None,
        "/usr/bin/python3" if sys.platform == "darwin" else None,
        shutil.which("python3.9"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        probe = subprocess.run(
            [candidate, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "3.9":
            return candidate
    raise unittest.SkipTest("Python 3.9 interpreter not available")

class TestPython39Compatibility(unittest.TestCase):
    def test_production_server_imports(self):
        python39 = _find_python39()
        result = subprocess.run(
            [python39, "-c", "import server; print(server.__version__)"],
            cwd=ROOT, capture_output=True, text=True,
            env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+$")
```

- [ ] **Step 3: Verify RED**

Run:

```bash
/usr/bin/python3 -m unittest tests.test_python39_compatibility -v
python3 -m pytest -q \
  tests/test_install_script.py::TestInstallScript::test_python_gate_accepts_39_and_honors_override \
  tests/test_run_script_python_static.py::test_run_script_accepts_python39
```

Expected: the import fails at an evaluated `float | None`, and both script tests fail on the 3.10 requirement.

---

### Task 2: Implement the compatibility fix and CI gate

**Files:**
- Modify: `server.py`
- Modify: `scripts/install.sh`
- Modify: `run.sh`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`
- Create: `changelog.d/fixed-python39-dmg-install-2026-07-16.md`
- Test: `tests/test_python39_compatibility.py`
- Test: `tests/test_install_script.py`
- Test: `tests/test_run_script_python_static.py`

**Interfaces:**
- Consumes: `CCC_PYTHON`, `PYTHON3`, and `sys.version_info`.
- Produces: a Python 3.9-compatible server import and matching install/launch/CI floors.

- [ ] **Step 1: Postpone annotations in `server.py`**

Insert after the module docstring and before `__version__`:

```python
from __future__ import annotations
```

- [ ] **Step 2: Select and validate the installer interpreter**

Add beside the installer constants in `scripts/install.sh`:

```bash
PYTHON3="${CCC_PYTHON:-python3}"
```

Use this implementation:

```bash
require_python3() {
  if ! command -v "$PYTHON3" >/dev/null 2>&1; then
    err "python3 not found on PATH. Install Python 3, then re-run this installer."
    exit 1
  fi
  if ! "$PYTHON3" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    got="$("$PYTHON3" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
    err "python3 ${got} found, but CCC requires Python 3.9+. Install a newer python3, then re-run this installer."
    exit 1
  fi
}
```

Change the optional WatchTower install to `"$PYTHON3" -m pip`.

- [ ] **Step 3: Lower the launcher floor**

In `run.sh`, change both interpreter probes to:

```bash
'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'
```

Change its error to:

```bash
echo "Error: CCC requires Python 3.9+. Set CCC_PYTHON to a compatible interpreter." >&2
```

Update the adjacent comment to Python 3.9+.

- [ ] **Step 4: Update metadata and documentation**

Set `requires-python = ">=3.9"` in `pyproject.toml` and change the README requirement to `Git and Python 3.9+`.

- [ ] **Step 5: Add Python 3.9 CI coverage**

Change the compile matrix to:

```yaml
python: ["3.9", "3.10", "3.11", "3.12", "3.13"]
```

Add this job to `.github/workflows/ci.yml`:

```yaml
python39-smoke:
  name: python 3.9 import + boot
  runs-on: macos-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.9"
    - name: Import and boot with Python 3.9
      run: |
        python -c 'import server; print(server.__version__)'
        PORT=18091 CCC_BIND_HOST=127.0.0.1 python server.py >/tmp/ccc-python39.log 2>&1 &
        SERVER_PID=$!
        trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
        for _ in $(seq 1 30); do
          curl -fsS http://127.0.0.1:18091/api/version && break
          sleep 1
        done
        ps -p "$SERVER_PID" >/dev/null
        curl -fsS http://127.0.0.1:18091/api/features >/dev/null
```

- [ ] **Step 6: Add the release fragment**

Create `changelog.d/fixed-python39-dmg-install-2026-07-16.md`:

```markdown
- Fixed macOS DMG first launch on Macs whose available Apple Python is 3.9.6 by making the stdlib server and both installer/launcher gates support Python 3.9+.
```

- [ ] **Step 7: Verify GREEN and commit**

Run:

```bash
/usr/bin/python3 -m unittest tests.test_python39_compatibility -v
python3 -m pytest -q tests/test_install_script.py \
  tests/test_run_script_python_static.py tests/test_python39_compatibility.py
bash -n scripts/install.sh run.sh
if command -v shellcheck >/dev/null 2>&1; then shellcheck scripts/install.sh run.sh; fi
git diff --check
```

Then commit only the named compatibility files:

```bash
git add -- tests/test_python39_compatibility.py \
  changelog.d/fixed-python39-dmg-install-2026-07-16.md
git commit --only server.py scripts/install.sh run.sh pyproject.toml README.md \
  .github/workflows/ci.yml tests/test_install_script.py \
  tests/test_run_script_python_static.py tests/test_python39_compatibility.py \
  changelog.d/fixed-python39-dmg-install-2026-07-16.md \
  -m "fix(install): support Python 3.9"
```

---

### Task 3: Prove the committed source and packaged app

**Files:**
- Verify: committed repository snapshot
- Verify: `scripts/test-macapp-install.sh`

**Interfaces:**
- Consumes: compatibility commit and `CCC_PYTHON=/usr/bin/python3`.
- Produces: full-suite and packaged-native-install evidence using Python 3.9.6.

- [ ] **Step 1: Run all tests from a clean snapshot**

```bash
tmp="$(mktemp -d /tmp/ccc-v581-tests.XXXXXX)"
git archive HEAD | tar -x -C "$tmp"
npm test --prefix "$tmp/infra/telemetry-worker"
python3 -m pytest -q "$tmp"
rm -rf "$tmp"
```

Expected: zero failures.

- [ ] **Step 2: Boot production with Apple's Python 3.9.6**

```bash
PORT=18092 CCC_BIND_HOST=127.0.0.1 PYTHONDONTWRITEBYTECODE=1 \
  /usr/bin/python3 server.py >/tmp/ccc-v581-python39.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18092/api/version && break
  sleep 1
done
curl -fsS http://127.0.0.1:18092/api/features >/dev/null
kill "$pid"; wait "$pid" 2>/dev/null || true; trap - EXIT
```

- [ ] **Step 3: Run the packaged install through Python 3.9**

```bash
CCC_PYTHON=/usr/bin/python3 ./scripts/test-macapp-install.sh
```

Expected: isolated clone, live `/api/version`, `dmg` attribution, no staging path, and app-owned server shutdown.

---

### Task 4: Prepare landing metadata and release scope

**Files:**
- Modify: `docs/index.html`
- Modify: `tests/test_landing_hero_static.py`
- Modify: `docs/superpowers/specs/2026-07-16-python39-dmg-compatibility-design.md`
- Create: `docs/superpowers/plans/2026-07-16-python39-dmg-compatibility.md`

**Interfaces:**
- Consumes: verified compatibility commit and the permanent DMG URL.
- Produces: v5.8.1 landing metadata and a reviewed shared-main release scope.

- [ ] **Step 1: Add a failing landing-version test**

```python
def test_landing_page_identifies_current_stable_release():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert "v5.8.1" in page
    assert "v5.8.0" not in page
```

Run that test and expect failure.

- [ ] **Step 2: Change both `v5.8.0` strings in `docs/index.html` to `v5.8.1`**

Run:

```bash
python3 -m pytest -q tests/test_landing_hero_static.py
git diff --check -- docs/index.html tests/test_landing_hero_static.py
```

- [ ] **Step 3: Commit planning and landing files only**

```bash
git commit --only docs/index.html tests/test_landing_hero_static.py \
  docs/superpowers/specs/2026-07-16-python39-dmg-compatibility-design.md \
  docs/superpowers/plans/2026-07-16-python39-dmg-compatibility.md \
  -m "docs(release): prepare v5.8.1 compatibility launch"
```

- [ ] **Step 4: Audit release scope and dry-run**

```bash
git fetch origin main
git rev-list --left-right --count origin/main...HEAD
git status --short
git log --oneline origin/main..HEAD
find changelog.d -maxdepth 1 -type f ! -name README.md -print | sort
./scripts/cut-release.sh 5.8.1 --dry-run
```

Stop if another session has unfinished release input. Expected previous tag: v5.8.0 and nine dry-run stages.

---

### Task 5: Cut and publish v5.8.1

**Files:**
- Modify via release tool: `CHANGELOG.md`, `pyproject.toml`, `server.py`, `docs/appcast.xml`
- Remove via release tool: released `changelog.d/*.md`
- Modify: `/Users/amirfish/Apps/homebrew-ccc/Formula/ccc.rb`
- Create: `ccc-v5.8.1.dmg`

**Interfaces:**
- Consumes: release scope, Developer ID, `ccc-notary`, Sparkle key, authenticated `gh`, Homebrew tap.
- Produces: tag/release v5.8.1, two DMG assets, appcast, and Homebrew formula.

- [ ] **Step 1: Run the official release**

```bash
./scripts/cut-release.sh 5.8.1
```

Expected: all nine stages succeed, including both `ccc-v5.8.1.dmg` and stable `ccc.dmg`.

- [ ] **Step 2: Inspect authoritative release metadata**

```bash
gh release view v5.8.1 --json url,tagName,isDraft,isPrerelease,publishedAt,assets
git ls-remote origin 'refs/tags/v5.8.1' 'refs/tags/v5.8.1^{}'
```

---

### Task 6: Verify the public DMG and hand off the link

**Files:**
- Verify: public stable/versioned DMGs, appcast, Homebrew formula, CI, Pages, Git state.

**Interfaces:**
- Consumes: `https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg`.
- Produces: cryptographic, installation, distribution, and remote-parity evidence.

- [ ] **Step 1: Verify bytes and trust chain**

Download both public assets. Assert equal SHA-256 and `cmp`, then run `hdiutil verify`, `xcrun stapler validate` on the DMG, `codesign --verify --deep --strict`, and `spctl -a -vv -t install` on the mounted app.

Expected: identical bytes, valid staple/signature, and `source=Notarized Developer ID`.

- [ ] **Step 2: Clean-install the public stable DMG with Python 3.9.6**

Launch its executable under a temporary home and unused port with:

```bash
CCC_PYTHON=/usr/bin/python3
CCC_REPO_URL=https://github.com/amirfish1/claude-command-center.git
```

Poll `/api/version` for 5.8.1, fetch the dashboard, verify `dmg` attribution, clean `main` origin, no staging path, and app-owned server shutdown. Expected: `public_python39_clean_install=PASS`.

- [ ] **Step 3: Verify distribution metadata and workflows**

```bash
curl -fsSL 'https://ccc.amirfish.ai/appcast.xml?release=5.8.1' >/tmp/ccc-v581-appcast.xml
curl -fsSL 'https://raw.githubusercontent.com/amirfish1/homebrew-ccc/main/Formula/ccc.rb?release=5.8.1' >/tmp/ccc-v581-formula.rb
gh run list --branch main --limit 10 --json databaseId,workflowName,status,conclusion,headSha,url
```

Assert the first appcast item is 5.8.1 with the correct URL, length, and EdDSA signature; assert Homebrew targets v5.8.1 with the actual tarball SHA; wait for CI, install-smoke, and Pages on final `main`.

- [ ] **Step 4: Verify parity and report the permanent link**

```bash
git fetch origin main
git rev-list --left-right --count origin/main...HEAD
git status --short
```

Expected: `0 0`. Preserve/report unrelated dirty files. Give the friend this exact link:

```text
https://github.com/amirfish1/claude-command-center/releases/latest/download/ccc.dmg
```
