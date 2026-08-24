# Linux support plan

Status: proposal, awaiting review. Scope decided by owner on 2026-06-17.

## Decision and scope

**Headless now, desktop later.** The Linux target is a remote dev box, VPS, or
container, accessed through the browser UI from another machine. Two goals:

1. The server plus browser UI must be genuinely solid on Linux. The core
   (kanban, `~/.claude` transcript ingestion, session spawn and drive) must
   just work.
2. The roughly ten macOS-gated desktop conveniences (notifications, native
   folder picker, screenshots, window control, reveal-in-Finder, desktop deep
   links, terminal jump, spawn-in-visible-terminal) are NOT reimplemented per
   desktop environment now. They are stubbed or no-op cleanly behind the
   existing platform check: no crashes, no broken buttons, graceful
   degradation, a clear log line when a feature is macOS-only.
3. `--install-service` gets a real systemd user-unit equivalent. That one is
   worth doing for headless.

**Explicitly out of scope (for now):** GNOME/KDE/Wayland/X11 parity, per-desktop
screenshot or window-control backends (grim/scrot/wmctrl/xdotool), zenity or
kdialog folder pickers, notify-send wiring, a Linux app shell or auto-update.
The product edge is simplicity; we keep it. Desktop parity is a documented
follow-on, not this slice.

**Stdlib-only invariant preserved.** Everything below is stdlib plus the same
`shutil.which` + `subprocess` shell-out pattern already in use. No pip
dependencies are added. The systemd unit is a plain text file we write; no new
runtime deps.

## What "first-class headless Linux" means here

- Start the server on a Linux box, open the browser UI from your laptop, and
  every core workflow works: see conversations, kanban, spawn a session, drive
  it, read transcripts, switch repos.
- No button in the UI does nothing or throws an opaque error. Desktop-only
  controls are hidden or disabled with a one-line reason, driven by a single
  server-reported capabilities flag (see "Dead-control mitigation").
- `python3 server.py --install-service` installs a systemd user service that
  survives logout (with a documented `loginctl enable-linger` step for headless
  boxes).

## Why the core already works (verified)

`spawn_session` (server.py:26558) creates sessions with `pty.openpty`,
`os.mkfifo`, and `subprocess.Popen`. It does not depend on AppleScript. Driving
a session is a FIFO write. So spawn, drive, ingest, and kanban are already
cross-platform. The osascript paths are purely the "open or focus a GUI
terminal window" affordances, which are desktop-only by nature.

The keystroke inject/interrupt code (server.py:9916, 10097) has a non-AppleScript
FIFO branch for CCC-spawned live sessions; only the "type into an external GUI
terminal tab" branch is AppleScript. Consequence: driving sessions CCC spawned
works headless. Driving a session that lives in a user's own GUI terminal does
not, and is a desktop-later feature.

## Feature inventory and per-feature decision

Legend: **keep** = already cross-platform, verify only; **systemd-port** = build
the Linux equivalent now; **clean-stub** = no-op behind platform check, graceful,
clear log line, hide any UI control.

### Core runtime (keep, verify)

| Site | Function | Capability | Decision |
|------|----------|-----------|----------|
| 26558, 26948, 29970 | `spawn_session`, pty/FIFO | Spawn and drive a session | keep (cross-platform) |
| 44462 | `_raise_open_file_limit` | Raise RLIMIT_NOFILE | keep (works on Linux; not Darwin-gated) |
| 43523, 40142 | service log paths | File logs under `~/.claude/command-center/logs` | keep (cross-platform path) |
| 20951 | `_get_cursor_app_support_dir` | Cursor data dir | keep (already has Linux/XDG branch) |
| 4621 | `_iter_common_cli_candidates` | Find CLI binaries off a sparse PATH | keep (the homebrew/.local search is harmless on Linux; systemd PATH is also sparse, so this stays useful) |

### Core runtime that needs a Linux fix (part of "just work")

| Site | What breaks on Linux | Decision |
|------|----------------------|----------|
| 3032-3037 | `_SYS_PS=/bin/ps` (ok), `_SYS_LSOF=/usr/sbin/lsof` (Linux: `/usr/bin/lsof` or absent), `_SYS_SYSCTL` and `_SYS_VM_STAT` (no Linux equivalent) | Resolve these via `shutil.which` with platform-aware fallbacks; on Linux, memory stats come from `/proc/meminfo` or are skipped. Guard every caller so a missing tool degrades, not crashes. |
| 16580 | `_codex_desktop_app_is_running` uses `ps -axo command` | `ps -axo` BSD syntax differs from Linux `ps`. Already Darwin-gated (returns False on Linux), so safe; no change needed beyond confirming the gate. |

### `--install-service` (systemd-port)

| Site | macOS mechanism | Linux plan |
|------|-----------------|-----------|
| launchd plist generation, install/uninstall, `launchctl` | LaunchAgent plist in `~/Library/LaunchAgents`, `launchctl load` | Write a systemd user unit to `~/.config/systemd/user/ccc.service`, run `systemctl --user daemon-reload` and `systemctl --user enable --now ccc`. Uninstall stops and disables, then removes the unit. Print a `loginctl enable-linger <user>` hint so the service runs on a headless box without an active login session. Detect systemd via `shutil.which("systemctl")`; if absent, print a clear message and the manual command to run the server under nohup/screen. Preserve the macOS launchd path exactly, gated by platform. |

### Desktop conveniences (clean-stub)

All already sit behind `platform.system() != "Darwin"` or `sys.platform !=
"darwin"` and return a structured error or no-op. Work here is: confirm the
stub is graceful, add a one-line log when a macOS-only feature is invoked on
Linux, and ensure the matching UI control is hidden (see next section).

| Site | Function | Capability | Dead UI control? | Decision |
|------|----------|-----------|------------------|----------|
| hooks/_notify.py | `notify` | Desktop notification | No (passive) | clean-stub: already a silent no-op when osascript absent. notify-send is a desktop-later one-liner. |
| 1774 | `_native_pick_folder` (`/api/fs/pick-folder`) | GUI folder chooser | Yes: "Browse" affordance | Done: native picker via zenity/kdialog/yad on Linux desktops; headless boxes fall back to the in-browser picker (`GET /api/fs/list` + folder-picker modal). |
| 4955, 5503, 5574 | screenshot capture | Screenshot for bug report / annotation | Yes: "Add screenshot" | clean-stub. Hide the screenshot button on non-Darwin. |
| 5441, 5653, 5693, 5473 | window id, minimize, restore, sips crop | Window-accurate capture helpers | No (internal to screenshot) | clean-stub with the parent feature. |
| 5313 (`/api/reveal-file` 41163) | `_reveal_bug_screenshot`, reveal-file | Reveal file in Finder | Maybe: any "reveal" link | clean-stub. Optional desktop-later: `xdg-open` the parent dir. |
| 9418 | `open_session_in_claude_desktop` | Open in Claude Desktop deep link | Yes: "Open in Claude Desktop" | clean-stub. Hide button. |
| 9447, 16598 | `open_session_in_codex_desktop`, workspace deeplink | Open in Codex deep link | Yes: "Open in Codex" | clean-stub. Hide button. |
| 16576 | `_codex_desktop_app_is_running` | Detect Codex.app | No (internal) | clean-stub: already returns False. |
| `/api/open-browser` 41189 | open URL in browser | Open a URL | Yes: links that call it | clean-stub on headless (no browser on server). Desktop-later: `xdg-open`. |
| 40275 | template-gallery open | Open templates.json in editor | Yes: gallery "open" | clean-stub. Desktop-later: `xdg-open`. |
| 9700 (`/api/launch-terminal` 42916) | `launch_terminal_for_session` | Spawn a visible terminal | Yes: "Launch" | clean-stub. Note: headless spawn still works via pty/FIFO; only the visible-terminal variant stubs. |
| `/api/jump-terminal` 42939, 10221 | `focus_terminal_by_tty` | Bring terminal tab to front | Yes: "Jump to terminal" | clean-stub. Hide button. tmux-based jump is a strong desktop-later candidate. |
| 9916, 10097 | keystroke inject/interrupt | Type into external GUI terminal | No (FIFO path covers CCC sessions) | clean-stub the AppleScript branch only. |
| 26148, 26206 | `launch_antigravity_terminal` | Antigravity TUI in terminal | Yes (if surfaced) | clean-stub. |
| 44051 | `_launch_login_terminal` | Onboarding terminal | Maybe (onboarding) | clean-stub. |

## Dead-control mitigation (the key deliverable)

A stub that returns an error is safe but still leaves a button that fails when
clicked. To avoid confusing dead controls without per-feature frontend edits
scattered everywhere, add one server-reported capabilities object and gate the
UI on it.

- Server: expose a small `capabilities` object (extend an existing bootstrap or
  status endpoint rather than adding a new one where possible). Shape:
  `{ "platform": "linux", "features": { "screenshots": false, "annotate":
  false, "terminalJump": false, "launchTerminal": false, "folderPicker": false,
  "desktopDeepLinks": false, "revealFile": false, "openBrowser": false,
  "notifications": false } }`. On macOS every flag is true, preserving current
  behavior exactly.
- Frontend: read the flags once at boot. For each false flag, hide (preferred)
  or disable-with-tooltip the matching control: Jump to terminal, Launch, Add
  screenshot, Flow Annotate, Open in Claude/Codex Desktop, Browse folder. The
  jump/launch buttons already default to `display:none` (index.html:555) and are
  shown conditionally, so the gate is one extra condition.
- Rule: one capability flag drives both the server stub and the UI visibility.
  No per-desktop-environment code. Fully in keeping with the simplicity edge.

### Controls to hide on non-Darwin (confirmed in frontend)

- `jumpBtnConv` Jump to terminal / Launch (static/index.html:555-562; handlers
  static/app.js:2208, 3626).
- Add screenshot (static/index.html:1231).
- Flow Annotate (static/app.js:9847, 12479).
- Open-in-Desktop deep-link buttons (locate exact markup during implementation).
- Open-browser-driven links (static/app.js:19908+) and template-gallery open.

## Docs and Docker

- Update the Dockerfile and docker-compose comments: they already note the
  macOS-only no-ops. Add the systemd path is host-only and that the container
  is the canonical headless deployment.
- Add a short "Running on Linux" section to README: install, run, optional
  `--install-service` with the `enable-linger` note, and a one-line statement
  that desktop conveniences are macOS-only today.

## Testing

- Extend `tests/test_smoke.py` so `server.py` imports cleanly under a simulated
  non-Darwin `platform.system()` and the stubbed functions return their
  structured no-op rather than raising.
- Add a capabilities-flag assertion: on non-Darwin the desktop feature flags are
  false; on Darwin they are true.
- Respect the performance gates: no new per-row subprocess work. The capability
  flags are computed once, not per session.
- Keep the smoke bar: do not mock `gh`, `claude`, or `tmux`.

## Implementation order (for the follow-on plan)

1. Capabilities object on the server plus the non-Darwin stub-graceful audit
   (confirm every gated function returns a clean structured no-op and logs once).
2. Frontend gating off the capabilities flags (hide dead controls).
3. Linux fixes for the absolute tool paths and memory stats (3032 block).
4. systemd `--install-service` port.
5. Docs (README Linux section, Dockerfile/compose comment refresh) and smoke
   tests.

Steps 1-2 remove every confusing dead control. Step 4 is the only net-new Linux
feature. Everything else is making existing gates graceful and documented.

## Open questions for review

- Capabilities transport: extend which existing endpoint, versus a tiny new
  `/api/capabilities`? Recommendation: extend an existing bootstrap/status
  response to avoid a new `/api/*` surface.
- For reveal-file, open-browser, and template-gallery open: strict clean-stub
  now, or allow the trivial `xdg-open` shim (one standard tool, not per-DE)
  since it makes desktop-Linux work for free without chasing parity?
  Recommendation: strict clean-stub now to honor "headless now"; revisit
  `xdg-open` in the desktop-later slice.
- systemd unit name: `ccc.service` versus `claude-command-center.service`.
