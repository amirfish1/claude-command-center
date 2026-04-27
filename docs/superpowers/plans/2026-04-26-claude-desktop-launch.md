# "Open in Claude Desktop" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third destination button beside the existing "Jump to terminal" / "Launch in terminal" buttons that resumes the current session inside the Claude Desktop GUI app via the `claude://resume?session=<uuid>` deep-link.

**Architecture:** A new stdlib-only backend helper `open_session_in_claude_desktop` validates the UUID and calls macOS `open(1)` with the deep-link URL. A new `POST /api/open-in-desktop` endpoint sits behind the existing `_check_same_origin` CSRF guard. The frontend adds two parallel buttons (`#desktopBtnConv` in the conversation toolbar, `#cpDesktopBtn` in the conversation pane chrome) wired to the endpoint and styled like the existing siblings.

**Tech Stack:** Python 3 stdlib (`subprocess`, `re`), `http.server` POST routing already in `server.py`. Single-file frontend in `static/index.html` (inline CSS/JS). Deep-link URL scheme `claude://resume?session=<uuid>` registered by the Claude Desktop Electron app (`com.anthropic.claudefordesktop`). macOS-only for v1 — uses `open(1)`.

**Spec:** [`docs/superpowers/specs/2026-04-26-claude-desktop-launch-design.md`](../specs/2026-04-26-claude-desktop-launch-design.md)

---

## File Map

- **Modify** `server.py` — add `open_session_in_claude_desktop()` near `launch_terminal_for_session` (around line 1926); add `POST /api/open-in-desktop` route in `do_POST` (around line 7180); bump `__version__`.
- **Modify** `static/index.html` — add `.desktop-btn` CSS (next to `.launch-btn`), HTML buttons (`#desktopBtnConv` next to `#launchBtnConv`, `#cpDesktopBtn` next to `#cpLaunchBtn`), wire-up JS (parallel to `launchTerminal` / `updateJumpButton` / `updateSplitToolbar`), and add the new IDs to the remote-host hide rule at line 1998–1999.
- **Modify** `pyproject.toml` — bump version.
- **Modify** `CHANGELOG.md` — append `Added` entry under `[Unreleased]`.
- **Modify** `tests/test_smoke.py` — add a smoke assertion that the helper exists and rejects bad input.

> **Conventions reminder.** This repo's CLAUDE.md requires explicit-path `git add` (never `-A`/`-a`/`.`), Conventional Commits with existing scopes (`feat(ui)`, `feat(api)`, `chore`), and lockstep version bumps in `pyproject.toml` + `server.py`.

---

## Task 1: Backend helper — `open_session_in_claude_desktop`

**Files:**
- Modify: `server.py` (insert new function immediately before `launch_terminal_for_session`, currently at line 1926; line numbers will drift — anchor by name)
- Test: `tests/test_smoke.py` (add a new assertion in the existing `TestServerImports` class)

- [ ] **Step 1: Write the failing smoke assertion**

Open `tests/test_smoke.py` and add this method to `TestServerImports`:

```python
    def test_open_session_in_claude_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-UUID session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_claude_desktop"))
        # Empty
        r = server.open_session_in_claude_desktop("")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # Not a UUID
        r = server.open_session_in_claude_desktop("not-a-uuid")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/amirfish/Apps/claude-command-center
python -m unittest tests.test_smoke.TestServerImports.test_open_session_in_claude_desktop_rejects_bad_input -v
```

Expected: FAIL with `AssertionError: False is not true : hasattr(server, "open_session_in_claude_desktop")` (the helper does not exist yet).

- [ ] **Step 3: Implement the helper**

Find `def launch_terminal_for_session(` in `server.py` (around line 1926). Insert the following function immediately above it:

```python
# UUID-format check — Claude Desktop's deep-link handler validates the
# session ID against a UUID regex internally and silently drops anything
# else. We pre-check so the UI gets a clear error instead of an opaque
# "nothing happened".
_SESSION_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def open_session_in_claude_desktop(session_id):
    """Open the macOS Claude Desktop app and resume `session_id`.

    Uses the registered `claude://resume?session=<uuid>` deep-link, which
    the desktop app handles by importing the CLI session and navigating
    to it. macOS only — relies on `open(1)`.

    Returns {ok, error?, url?}.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    if not _SESSION_UUID_RE.match(session_id):
        return {"ok": False, "error": "invalid session_id (expected UUID)"}
    if sys.platform != "darwin":
        return {"ok": False, "error": "Claude Desktop deep-link is macOS-only"}
    url = f"claude://resume?session={session_id}"
    try:
        log_path = LOG_DIR / f"desktop-{session_id[:8]}.log"
        lf = open(log_path, "w")
        subprocess.Popen(["open", url], stdout=lf, stderr=lf)
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "error": str(e), "url": url}
    return {"ok": True, "url": url}
```

Verify `re` is already imported near the top of `server.py` (it is — `re` is used widely). If for some reason the file doesn't import `re`, add `import re` to the top imports block.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /Users/amirfish/Apps/claude-command-center
python -m unittest tests.test_smoke.TestServerImports.test_open_session_in_claude_desktop_rejects_bad_input -v
```

Expected: PASS.

Also re-run the full smoke suite to make sure nothing else broke:

```bash
python -m unittest tests.test_smoke -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add server.py tests/test_smoke.py
git commit -m "feat(api): add open_session_in_claude_desktop helper

Validates the session ID as a UUID and shells out to open(1) with
claude://resume?session=<uuid>. macOS-only by design — non-Darwin
hosts get a clear error response."
```

---

## Task 2: Backend route — `POST /api/open-in-desktop`

**Files:**
- Modify: `server.py` (insert new route handler in `do_POST`, immediately after the `/api/jump-terminal` block currently at lines 7180–7196 — anchor by string match)

- [ ] **Step 1: Locate the insertion point**

Open `server.py` and search for `elif path == "/api/jump-terminal":`. The new route goes immediately after that block ends (currently at line 7196 — just before `else: self.send_json({"error": "Not found"}, 404)`).

- [ ] **Step 2: Insert the new route**

Add this `elif` branch directly after the `/api/jump-terminal` block:

```python
        elif path == "/api/open-in-desktop":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id", "")
            self.send_json(open_session_in_claude_desktop(sid))
```

- [ ] **Step 3: Manually verify via curl**

Start the server (or restart if already running):

```bash
cd /Users/amirfish/Apps/claude-command-center
./run.sh &
sleep 2
```

Then probe the endpoint with a fake session ID — it should reject without launching anything:

```bash
curl -sS -X POST http://127.0.0.1:8088/api/open-in-desktop \
  -H 'Origin: http://127.0.0.1:8088' \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"not-a-uuid"}'
```

Expected JSON: `{"ok": false, "error": "invalid session_id (expected UUID)"}`

(If your server runs on a different port, adjust both URL and Origin header to match — the same-origin guard requires the Origin to match the bind address.)

Empty payload:

```bash
curl -sS -X POST http://127.0.0.1:8088/api/open-in-desktop \
  -H 'Origin: http://127.0.0.1:8088' \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Expected: `{"ok": false, "error": "missing session_id"}`

A real session ID (replace with one from your `~/.claude/projects/...`) on macOS will actually launch Claude Desktop — only run this once you're ready to see the GUI:

```bash
curl -sS -X POST http://127.0.0.1:8088/api/open-in-desktop \
  -H 'Origin: http://127.0.0.1:8088' \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<a-real-uuid>"}'
```

Expected: `{"ok": true, "url": "claude://resume?session=<...>"}` and Claude Desktop opens to that session.

- [ ] **Step 4: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add server.py
git commit -m "feat(api): add POST /api/open-in-desktop route

Same-origin guarded; calls open_session_in_claude_desktop and returns
its JSON result verbatim."
```

---

## Task 3: Frontend CSS — `.desktop-btn`

**Files:**
- Modify: `static/index.html` (add CSS block after `.toolbar .launch-btn` styles, currently lines 212–222)

- [ ] **Step 1: Locate the insertion point**

In `static/index.html` find the `.toolbar .launch-btn.launching { opacity: 0.6; }` line (currently line 222). The new CSS goes directly after it.

- [ ] **Step 2: Add the CSS**

Insert this block right after the `.launch-btn` rules:

```css
  .toolbar .desktop-btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px; border-radius: 6px; cursor: pointer;
    background: rgba(57, 210, 192, 0.12); color: var(--cyan);
    border: 1px solid rgba(57, 210, 192, 0.35);
    font-size: 12px; font-weight: 600; font-family: inherit;
    transition: background 0.15s, transform 0.1s;
  }
  .toolbar .desktop-btn:hover { background: rgba(57, 210, 192, 0.22); }
  .toolbar .desktop-btn:active { transform: scale(0.97); }
  .toolbar .desktop-btn.opening { opacity: 0.6; }
```

(Cyan is the third colour in the existing palette — purple = jump, blue = launch, cyan = desktop. Distinct at a glance.)

- [ ] **Step 3: Visual sanity check**

Reload the dev server (or hard-refresh the page if running). The CSS will not affect anything yet because no element has the `desktop-btn` class — but DevTools should show the rule registered with no parse errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add static/index.html
git commit -m "feat(ui): add .desktop-btn style block

Cyan-tinted variant of the existing .jump-btn / .launch-btn pattern;
unused until the buttons land in the next commit."
```

---

## Task 4: Frontend HTML — buttons + remote-host hide rule

**Files:**
- Modify: `static/index.html`
  - Line ~1998–1999 (remote-host display:none rule)
  - Line ~2424–2427 (toolbar `#launchBtnConv` block — insert new button immediately after)
  - Line ~2473 (`#cpLaunchBtn` line — insert new button immediately after)

- [ ] **Step 1: Add IDs to the remote-host hide rule**

Find this block (currently lines 1998–1999, anchor by selector text):

```css
    #cpJumpBtn, #cpLaunchBtn,
    #jumpBtnConv, #launchBtnConv,
```

Replace it with:

```css
    #cpJumpBtn, #cpLaunchBtn, #cpDesktopBtn,
    #jumpBtnConv, #launchBtnConv, #desktopBtnConv,
```

- [ ] **Step 2: Add the toolbar button**

Find the `#launchBtnConv` element (currently lines 2424–2427):

```html
      <button class="launch-btn" id="launchBtnConv" style="display:none;" title="Open a new terminal and resume this session">
        <span class="jump-icon">&#43;</span>
        <span class="jump-label">Launch in terminal</span>
      </button>
```

Insert this immediately after the closing `</button>`:

```html
      <button class="desktop-btn" id="desktopBtnConv" style="display:none;" title="Resume this session in the Claude Desktop app">
        <span class="jump-icon">&#9636;</span>
        <span class="jump-label">Open in Claude Desktop</span>
      </button>
```

(`&#9636;` is `▤` — a small monitor/window glyph that reads as "desktop". Reuses the `.jump-icon` / `.jump-label` span class names so the existing show/hide and label-update code paths can address them without divergence.)

- [ ] **Step 3: Add the conversation-pane button**

Find the `#cpLaunchBtn` line (currently line 2473):

```html
        <button id="cpLaunchBtn" style="display:none;" title="Launch in terminal"><span>&#43;</span> Launch</button>
```

Insert this immediately after it:

```html
        <button id="cpDesktopBtn" style="display:none;" title="Open in Claude Desktop"><span>&#9636;</span> Desktop</button>
```

- [ ] **Step 4: Visual sanity check**

Reload the page. The two new buttons exist in the DOM but stay hidden (no JS wiring yet). DevTools → Elements should find them; styles tab should show `.desktop-btn` rules applied to `#desktopBtnConv`.

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add static/index.html
git commit -m "feat(ui): add Open-in-Claude-Desktop button HTML

Two parallel buttons (#desktopBtnConv in the toolbar, #cpDesktopBtn in
the conversation pane chrome). Both hidden until the wire-up commit."
```

---

## Task 5: Frontend wiring — toolbar button (`#desktopBtnConv`)

**Files:**
- Modify: `static/index.html`
  - Around line 2787 (`$jumpBtnConv` / `$launchBtnConv` consts — anchor by name)
  - Around line 2823–2837 (the `*ButtonsForActiveTab` / `all*Buttons` accessors)
  - Around line 2869–2906 (`updateJumpButton` body)
  - Around line 3082–3118 (`launchTerminal` function — new sibling goes right after)
  - Around line 3214–3215 (the click-listener loops)

- [ ] **Step 1: Add the DOM ref**

Find:

```javascript
  const $jumpBtnConv = document.getElementById('jumpBtnConv');
  const $launchBtnConv = document.getElementById('launchBtnConv');
```

Add immediately below:

```javascript
  const $desktopBtnConv = document.getElementById('desktopBtnConv');
```

- [ ] **Step 2: Add the active-tab and all-buttons accessors**

Find:

```javascript
  function launchButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$launchBtnConv];
    return [];
  }
  function allResumeButtons() { return [$resumeBtnConv].filter(Boolean); }
  function allJumpButtons() { return [$jumpBtnConv].filter(Boolean); }
  function allLaunchButtons() { return [$launchBtnConv].filter(Boolean); }
```

Insert these two new functions immediately after `launchButtonsForActiveTab` and add `allDesktopButtons` after `allLaunchButtons`:

```javascript
  function desktopButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$desktopBtnConv];
    return [];
  }
```

```javascript
  function allDesktopButtons() { return [$desktopBtnConv].filter(Boolean); }
```

So the final block reads:

```javascript
  function launchButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$launchBtnConv];
    return [];
  }
  function desktopButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$desktopBtnConv];
    return [];
  }
  function allResumeButtons() { return [$resumeBtnConv].filter(Boolean); }
  function allJumpButtons() { return [$jumpBtnConv].filter(Boolean); }
  function allLaunchButtons() { return [$launchBtnConv].filter(Boolean); }
  function allDesktopButtons() { return [$desktopBtnConv].filter(Boolean); }
```

- [ ] **Step 3: Update `updateJumpButton` to manage the desktop button**

Find the end of `updateJumpButton` — specifically the closing `}` of the function after the Launch buttons loop (currently around line 2906).

Replace the entire `updateJumpButton` function body with:

```javascript
  function updateJumpButton() {
    const live = liveStatus.live;
    const sid = currentSession.id;
    const canJump = live && liveStatus.tty && liveStatus.terminalApp;
    const canLaunch = !!sid && !canJump;  // only show Launch when Jump isn't available
    const canOpenDesktop = !!sid;  // always available when a session is selected

    // Jump buttons
    const activeJump = jumpButtonsForActiveTab();
    for (const btn of allJumpButtons()) {
      if (!activeJump.includes(btn)) btn.style.display = 'none';
    }
    for (const btn of activeJump) {
      if (!btn) continue;
      if (canJump) {
        btn.style.display = 'inline-flex';
        btn.title = 'Focus ' + liveStatus.terminalApp + ' (' + liveStatus.tty + ') running this session';
        btn.querySelector('.jump-label').textContent = 'Jump to terminal (' + liveStatus.terminalApp + ')';
      } else {
        btn.style.display = 'none';
      }
    }

    // Launch buttons (dormant sessions)
    const activeLaunch = launchButtonsForActiveTab();
    for (const btn of allLaunchButtons()) {
      if (!activeLaunch.includes(btn)) btn.style.display = 'none';
    }
    for (const btn of activeLaunch) {
      if (!btn) continue;
      if (canLaunch) {
        btn.style.display = 'inline-flex';
        btn.title = 'Open a new Terminal window and run claude --resume';
        btn.querySelector('.jump-label').textContent = 'Launch in terminal';
      } else {
        btn.style.display = 'none';
      }
    }

    // Desktop buttons (always-visible third destination)
    const activeDesktop = desktopButtonsForActiveTab();
    for (const btn of allDesktopButtons()) {
      if (!activeDesktop.includes(btn)) btn.style.display = 'none';
    }
    for (const btn of activeDesktop) {
      if (!btn) continue;
      if (canOpenDesktop) {
        btn.style.display = 'inline-flex';
        btn.title = 'Resume this session in the Claude Desktop app';
        btn.querySelector('.jump-label').textContent = 'Open in Claude Desktop';
      } else {
        btn.style.display = 'none';
      }
    }
  }
```

- [ ] **Step 4: Add the `openInClaudeDesktop` click handler**

Find the end of `launchTerminal` (currently around line 3118). Insert this new function immediately after `launchTerminal`'s closing `}`:

```javascript
  async function openInClaudeDesktop(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !currentSession.id) return;
    const origLabel = btn.querySelector('.jump-label').textContent;
    btn.classList.add('opening');
    btn.querySelector('.jump-label').textContent = 'Opening…';
    try {
      const res = await fetch('/api/open-in-desktop', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: currentSession.id }),
      });
      const data = await res.json();
      if (data.ok) {
        btn.querySelector('.jump-label').textContent = 'Opened!';
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.querySelector('.jump-label').textContent = origLabel;
        }, 1500);
      } else {
        btn.querySelector('.jump-label').textContent = 'Failed: ' + (data.error || 'unknown');
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.querySelector('.jump-label').textContent = origLabel;
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('opening');
      btn.querySelector('.jump-label').textContent = 'Error';
      setTimeout(() => { btn.querySelector('.jump-label').textContent = origLabel; }, 2000);
    }
  }
```

- [ ] **Step 5: Wire the click listener**

Find:

```javascript
  for (const btn of allJumpButtons()) btn.addEventListener('click', jumpToTerminal);
  for (const btn of allLaunchButtons()) btn.addEventListener('click', launchTerminal);
```

Add immediately below:

```javascript
  for (const btn of allDesktopButtons()) btn.addEventListener('click', openInClaudeDesktop);
```

- [ ] **Step 6: Manual verification**

Restart the server. Hard-refresh the browser. Open a session in the conversation tab.

1. The new "Open in Claude Desktop" button should appear in the toolbar beside Jump/Launch.
2. Click it. Label should flip through "Opening…" → "Opened!" → back to "Open in Claude Desktop".
3. Claude Desktop should open and load the session.
4. Existing Jump and Launch buttons must still behave identically — verify by clicking each on appropriate sessions (live for Jump, dormant for Launch).
5. Deselect the session: the Desktop button should hide.

- [ ] **Step 7: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add static/index.html
git commit -m "feat(ui): wire Open-in-Claude-Desktop toolbar button

Always-visible alongside Jump/Launch. POSTs to /api/open-in-desktop;
shows the same Opening… → Opened!/Failed transient pattern as the
Launch button."
```

---

## Task 6: Frontend wiring — conversation-pane button (`#cpDesktopBtn`)

**Files:**
- Modify: `static/index.html` (`updateSplitToolbar` function, currently around line 6646–6695)

- [ ] **Step 1: Locate `updateSplitToolbar`**

Find `function updateSplitToolbar() {` — search by name.

- [ ] **Step 2: Add the desktop button reference and wiring**

At the top of the function body, beside the existing `$cpJumpBtn` / `$cpLaunchBtn` lookups, add `$cpDesktopBtn`:

```javascript
    const $cpJumpBtn = document.getElementById('cpJumpBtn');
    const $cpLaunchBtn = document.getElementById('cpLaunchBtn');
    const $cpDesktopBtn = document.getElementById('cpDesktopBtn');
    const $cpKillBtn = document.getElementById('cpKillBtn');
    if (!$cpJumpBtn) return;
```

After the existing `$cpLaunchBtn` block (which currently ends with the `};` closing the `$cpLaunchBtn.onclick` handler, around line 6692), and **before** the `$cpKillBtn.style.display` line (line 6694), insert this Desktop button block:

```javascript
    // Desktop: always show when a session is selected (not for pkood)
    $cpDesktopBtn.style.display = (sid && !isPkood) ? '' : 'none';
    $cpDesktopBtn.onclick = async () => {
      if (!sid) return;
      const origHTML = $cpDesktopBtn.innerHTML;
      $cpDesktopBtn.textContent = 'Opening…';
      $cpDesktopBtn.disabled = true;
      try {
        const res = await fetch('/api/open-in-desktop', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ session_id: sid }),
        });
        const data = await res.json();
        if (data.ok) {
          $cpDesktopBtn.textContent = 'Opened!';
        } else {
          $cpDesktopBtn.textContent = 'Failed: ' + (data.error || 'unknown');
        }
      } catch (_) {
        $cpDesktopBtn.textContent = 'Error';
      }
      setTimeout(() => {
        $cpDesktopBtn.innerHTML = origHTML;
        $cpDesktopBtn.disabled = false;
      }, 1800);
    };
```

- [ ] **Step 3: Manual verification**

Restart the server. Open the conversation pane (the split-pane view).

1. The cyan "Desktop" button should be visible beside Jump/Launch in the conversation pane chrome.
2. Click it — label flips to "Opening…" then back; Claude Desktop opens to the session.
3. For pkood sessions, the Desktop button must stay hidden (mirrors Launch's `!isPkood` guard).

- [ ] **Step 4: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add static/index.html
git commit -m "feat(ui): wire #cpDesktopBtn in updateSplitToolbar

Conversation-pane chrome gets the same Open-in-Claude-Desktop button
as the main toolbar. Hidden for pkood sessions, same as Launch."
```

---

## Task 7: Bookkeeping — version + CHANGELOG

**Files:**
- Modify: `pyproject.toml` (line 3)
- Modify: `server.py` (line 15)
- Modify: `CHANGELOG.md` (under `## [Unreleased]`)

> Note: there are pre-existing unstaged edits in `CHANGELOG.md`, `server.py`, and `static/index.html` from prior work in the shared clone. **Do not bundle them with this PR.** Always stage by explicit path (`git add server.py CHANGELOG.md pyproject.toml`) and check `git diff --cached` before each commit to make sure only this feature's lines are included.

- [ ] **Step 1: Bump `pyproject.toml`**

Find line 3:

```toml
version = "0.1.3"
```

Change to:

```toml
version = "0.2.0"
```

(Minor bump — new user-visible feature, no breaking API change.)

- [ ] **Step 2: Bump `server.py`**

Find line 15:

```python
__version__ = "0.1.3"
```

Change to:

```python
__version__ = "0.2.0"
```

- [ ] **Step 3: Add CHANGELOG entry**

In `CHANGELOG.md` find the `## [Unreleased]` section and add a new `### Added` block above any existing `### Changed` block (or extend the existing `### Added` if one already exists in `[Unreleased]`):

```markdown
### Added
- **"Open in Claude Desktop" button** beside Jump/Launch in the
  conversation toolbar (and the conversation-pane chrome). Resumes the
  current session inside the Claude Desktop GUI app via the
  `claude://resume?session=<uuid>` deep-link — the desktop app imports
  the CLI session and navigates to it. macOS only for now (relies on
  `open(1)`).
```

- [ ] **Step 4: Verify the smoke test still passes**

```bash
cd /Users/amirfish/Apps/claude-command-center
python -m unittest tests.test_smoke -v
```

The version-format assertion (`r"^\d+\.\d+\.\d+"`) covers `0.2.0` fine. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/amirfish/Apps/claude-command-center
git add pyproject.toml server.py CHANGELOG.md
git commit -m "chore: bump version to 0.2.0 for desktop-launch feature

CHANGELOG entry under [Unreleased] / Added covers the user-visible
button. pyproject.toml and server.py __version__ bumped in lockstep
per house rules."
```

---

## Task 8: End-to-end manual verification

> No code changes — this is the gate before claiming the feature done.

- [ ] **Step 1: Run the full smoke suite**

```bash
cd /Users/amirfish/Apps/claude-command-center
python -m unittest tests.test_smoke -v
```

Expected: all tests pass.

- [ ] **Step 2: Restart the server cleanly**

```bash
cd /Users/amirfish/Apps/claude-command-center
pkill -f 'python.*server.py' 2>/dev/null; sleep 1
./run.sh &
sleep 2
```

- [ ] **Step 3: Verify the buttons in the UI**

Open the dev URL in a browser. For a **live** session (Claude actively running in a terminal):

1. Toolbar shows: **Resume in CLI**, **Jump to terminal (...)**, **Open in Claude Desktop**.
2. Click "Open in Claude Desktop" → Claude Desktop opens to that session.
3. Click "Jump to terminal" → existing terminal focuses (no regression).

For a **dormant** session:

1. Toolbar shows: **Resume in CLI**, **Launch in terminal**, **Open in Claude Desktop**.
2. Click "Launch in terminal" → new terminal opens (no regression).
3. Click "Open in Claude Desktop" → Claude Desktop opens to that session.

For a **pkood** session:

1. Resume / Jump / Launch / Desktop are all hidden (existing behaviour preserved).

For **no session selected**:

1. Desktop button hides along with the others.

- [ ] **Step 4: Verify the conversation-pane chrome**

Open the split-pane conversation view. Same matrix as Step 3 should hold — the cyan Desktop pill appears beside Jump/Launch and behaves identically.

- [ ] **Step 5: Verify the macOS-only error path**

If you have a non-macOS host available, restart the server there and click the button. Expected: `Failed: Claude Desktop deep-link is macOS-only`. If no non-macOS host is available, force the path locally by temporarily editing `server.py`'s sys.platform check (but don't commit that change):

```python
# Temporary: force the non-macOS path for testing.
if sys.platform != "darwin" or True:  # remove `or True` after testing
```

Click the button → label shows the error message. Revert the edit.

- [ ] **Step 6: Decide branch strategy and open a PR**

Per the global CLAUDE.md, do not create branches in this shared clone. Either:

- (a) Cherry-pick the seven feature commits onto a new worktree (`git worktree add ../claude-command-center-wt-desktop-launch -b feat/open-in-claude-desktop main`, then `git cherry-pick <sha1>..<sha7>`), push from there, and open the PR.
- (b) If the user explicitly wants the commits to land on `main` directly (this repo's flow has historically allowed that for small features — see recent commits like `feat(ui): chat pane styled like Claude Desktop`), confirm with the user before pushing.

Either way: do **not** force-push, do **not** push without explicit user approval, and verify `git log --oneline -7` shows only the seven new commits and nothing else.

---

## Self-review notes (kept for reviewer context)

- **Spec coverage:** every spec section maps to a task — backend helper (T1), endpoint (T2), CSS (T3), HTML + remote-host hide rule (T4), toolbar wiring (T5), conversation-pane wiring (T6), version/CHANGELOG (T7). Manual verification matrix (T8) covers the spec's testing section.
- **Naming consistency:** the helper is `open_session_in_claude_desktop` (T1, T2). Frontend handler is `openInClaudeDesktop` (T5). DOM IDs are `desktopBtnConv` / `cpDesktopBtn` (T4–T6). Class is `desktop-btn` (T3, T4). All references match.
- **House-rule guards:** every commit step uses `git add <explicit paths>` (no `-A`). Every commit message uses an existing scope (`feat(api)`, `feat(ui)`, `chore`).
- **Backwards compatibility:** the new endpoint is additive; no existing endpoint shape changes. `updateJumpButton`'s pre-existing Jump/Launch behaviour is preserved verbatim — only an additive Desktop block is appended.
