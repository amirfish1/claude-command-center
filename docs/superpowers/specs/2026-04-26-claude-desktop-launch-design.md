# Open in Claude Desktop — design

Status: draft
Date: 2026-04-26
Owner: amirfish

## Problem

CCC's conversation toolbar already has two destination buttons for a selected
session:

- **Jump to terminal** — focuses the terminal tab/window currently running
  `claude --resume <session_id>` (when one exists).
- **Launch in terminal** — opens a new terminal window and runs the resume
  command (when no live process is attached).

Both end at a *terminal*. Some users prefer to read and continue the same
session inside the **Claude Desktop** GUI app. There is no in-product way to
get there today; the only option is to manually open Claude Desktop and find
the session in its picker.

We want a third button that lands the user inside Claude Desktop, on the same
session, in one click.

## Solution

Add an always-visible third button next to Jump/Launch — **"Open in Claude
Desktop"** — that opens the macOS Claude Desktop app (`/Applications/Claude.app`,
bundle id `com.anthropic.claudefordesktop`) and navigates it to the same
session via the app's `claude://resume?session=<uuid>` URL scheme.

### How the deep link works

The Claude Desktop app's main process registers `claude` as a default protocol
client and handles `claude://resume?session=<UUID>` by:

1. Validating the session ID against a UUID regex.
2. Calling `importCliSession(sessionId)` — the desktop app's mechanism for
   pulling a CLI-recorded session into its own database.
3. Navigating the desktop UI to the imported session's route.

This was confirmed by extracting `app.asar` from the installed Claude Desktop
build (`1.4758.0`) and reading the URL-handler routes in the main process JS.
The relevant case is the `Resume` host, value `"resume"`.

Invocation from CCC's backend is a single shell call:

```sh
open "claude://resume?session=<session_id>"
```

`open(1)` is macOS-only. Other platforms get a clear error response.

## Architecture

### Backend — `server.py`

**New helper (near `launch_terminal_for_session`):**

```python
def open_session_in_claude_desktop(session_id: str) -> dict:
    """Launch Claude Desktop and resume the given session.

    Returns {ok: bool, error?: str, url?: str}.
    """
```

Behaviour:

- Reject empty / non-UUID session IDs (the desktop app silently refuses
  invalid IDs; rejecting client-side gives a clearer error).
- Build URL `claude://resume?session=<session_id>`.
- On macOS only: `subprocess.Popen(["open", url], stdout=lf, stderr=lf)` where
  `lf` is `LOG_DIR/desktop-<sid8>.log` (mirrors the pattern used by
  `launch_terminal_for_session`).
- On other platforms: return `{ok: False, error: "Claude Desktop deep-link is macOS-only"}`.

**New endpoint:**

```
POST /api/open-in-desktop
Body: {"session_id": "<uuid>"}
Returns: {"ok": bool, "error"?: str}
```

- Goes through the existing `_check_same_origin` CSRF guard, identical to
  `/api/launch-terminal` and `/api/jump-terminal`.
- Stdlib-only — no new dependencies.
- Additive — does not change any existing endpoint's contract.

### Frontend — `static/index.html`

Single-file app convention is preserved (no new files, inline CSS/JS).

**New buttons:**

- `#desktopBtnConv` — beside `#jumpBtnConv` and `#launchBtnConv` in the main
  toolbar (around line 2218–2226 today).
- `#cpDesktopBtn` — beside `#cpJumpBtn` / `#cpLaunchBtn` in the conversation
  pane chrome (around line 2297).

Both use a new `.desktop-btn` CSS class colour-twinned to the existing
`.launch-btn` / `.jump-btn` block (around lines 233–257).

**Visibility rules:**

- Always shown when a session is selected (`sid` truthy). Unlike Jump/Launch,
  it is **not** mutually exclusive with the others — it is an alternate
  destination, so all three can coexist.
- Added to the existing remote-host hide rule at line 1817–1818
  (`#cpJumpBtn, #cpLaunchBtn, #jumpBtnConv, #launchBtnConv`) — same constraint
  applies (the backend uses local `open(1)`, useless when CCC is served
  remotely).

**Click handler:**

- New function `openInClaudeDesktop(ev)` parallel to `launchTerminal` (line
  2816) and `jumpToTerminal` (line 2715).
- POSTs `/api/open-in-desktop` with `{session_id}`.
- Idle label: **"Open in Claude Desktop"** (imperative, matches the
  "Jump to terminal" / "Launch in terminal" siblings).
- During request: label changes to `"Opening…"`.
- On success: brief `"Opened!"` confirmation, then revert.
- On failure: `"Failed: <error>"` for ~3s, then revert.

**Wiring:**

- Mirror the `allLaunchButtons()` / `allJumpButtons()` accessor pattern with
  `allDesktopButtons()`.
- Add a click listener loop alongside lines 2935–2936.
- Include the new buttons in `updateJumpButton()` so they show/hide correctly
  with session selection (rename to `updateSessionDestinationButtons` only if
  the existing function grows hard to read — otherwise leave the name and add
  a small section).

### What is *not* changed

- `launch_terminal_for_session`, `focus_terminal_by_tty`,
  `session_live_status` — untouched.
- `/api/launch-terminal`, `/api/jump-terminal` — untouched.
- The Jump button's "focus existing terminal" idempotency — untouched.

## Error handling

- **Invalid session ID** (not UUID): backend returns 400-style JSON error;
  button shows `"Failed: invalid session id"`. (Should not happen in practice
  — CCC only surfaces real session IDs from the conversation list.)
- **Claude Desktop not installed**: `open` exits non-zero and macOS shows a
  system dialog ("There is no application set to open the URL …"). The button
  still reports `Opened!` because we fire-and-forget; for v1 we accept this —
  the system dialog is self-explanatory. A follow-up could `LSCopyApplication
  URLsForBundleIdentifier` lookup before firing.
- **Non-macOS host**: button hidden via the existing remote-host rule when
  CCC is served remotely; if a non-macOS user runs CCC locally, the endpoint
  responds with the macOS-only error and the button surfaces it.

## Security

- Endpoint sits behind the existing `_check_same_origin` guard — same posture
  as every other POST in the server.
- `session_id` is validated as a UUID before being interpolated into the URL,
  so `subprocess.Popen(["open", url])` cannot be tricked into running an
  arbitrary command.
- We pass arguments as a list (no shell), so no shell-quoting concerns.
- No new bind-host or network change. `SECURITY.md` does not need updates.

## Bookkeeping

- `CHANGELOG.md` — append under `## [Unreleased]` / `### Added`:
  > "Opening Claude Desktop" button beside Jump/Launch — resumes the current
  > session in the Claude Desktop app via the `claude://resume` deep link
  > (macOS only).
- Version bump (minor — new user-visible feature) in lockstep:
  - `pyproject.toml` `version`
  - `server.py` `__version__`
- `README.md` — one-line addition under the relevant feature list.

## Out of scope (deferred)

- Windows / Linux launchers. The deep link itself works on those platforms
  if Claude Desktop is installed, but `open(1)` is macOS-specific. Easy
  follow-up via `start` / `xdg-open` once a user asks.
- Pre-flight check for whether Claude Desktop is installed (using
  `mdfind kMDItemCFBundleIdentifier == 'com.anthropic.claudefordesktop'`).
- A user setting to *replace* "Launch in terminal" with the desktop button
  rather than show three buttons.

## Testing

Existing `tests/test_smoke.py` only checks import-time correctness. We will
keep that bar — no new test is required, but a smoke-level assertion that
`open_session_in_claude_desktop` exists and rejects empty input is cheap and
worth adding.

Manual verification before claiming completion:

1. Click the new button on a known live session — Claude Desktop opens and
   the session loads.
2. Click on a dormant session — same result.
3. Click on a session whose UUID is malformed (forced via devtools) —
   button shows the error and does not open the app.
4. Existing Jump and Launch buttons still behave identically.
