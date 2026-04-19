## Launch button races keystrokes into zsh (slash commands leak, `/cd` mystery)

When clicking **Launch** on a kanban card, the AppleScript in `launch_terminal_for_session` (server.py:1111) runs `do script "cd '…' && claude --resume …"`, waits 2.0s, then `keystroke`s `/rename …` + Enter and `/color green` + Enter on the assumption that Claude's TUI is ready.

### Symptoms observed 2026-04-19
```
Last login: Sun Apr 19 11:24:33 on ttys039
/cd '/Users/amirfish/my-finance-app' && claude --resume 98e06d0f-…
rename #169: [BYM Problem] Add the year-to-date option so you have
amirfish@… % /cd '…' && claude --resume …
zsh: no such file or directory: /cd
amirfish@… % rename #169: [BYM Problem]…
zsh: bad pattern: [BYM
amirfish@… % /color green
zsh: no such file or directory: /color
```

### Root causes
1. **Known race** — if Claude's TUI hasn't taken over stdin within 2s (slow `.zshrc`, first-run, etc.), the `/rename` and `/color` keystrokes land in zsh, which treats `/…` as an absolute path and errors.
2. **Unexplained `/cd`** — no `/cd` keystroke exists anywhere in the AppleScript. `_build_resume_command` produces `cd '…' && claude --resume …`. Current working theories:
   - `do script` fired twice (macOS retry) and a `/` from a racing `/rename` keystroke got prepended to the echoed cd-line.
   - Terminal profile / `.zshrc` emitted a `/` during init.
   - Neither is confirmed — need a fresh repro with `osascript` Console logs + the session's jsonl.

### Candidate fixes
1. Drop `/rename` + `/color` entirely; rely on the server writing session name to metadata. Loses colored tabs but kills the class of failure.
2. **Preferred:** poll the new tab's tty for a `claude` process (`ps -t ttysNN | grep -q claude`) before keystroking. Only inject slash-commands when TUI is confirmed alive.
3. Bump delay to 4–5s and guard with AppleScript `busy of window` — cheapest, still racy.

### Next step when revived
Grab a fresh repro:
- `~/.claude/projects/-Users-amirfish-my-finance-app/<sid>.jsonl` around the failed launch
- `Console.app` filter for `osascript` and `Terminal` at launch time
- Confirm whether `do script` actually fired once or twice

Then implement fix #2.
