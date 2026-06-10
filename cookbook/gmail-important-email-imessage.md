# Gmail hourly triage → iMessage alert

Build a personal automation that checks your Gmail once an hour, asks Claude
to judge whether anything *actually important* arrived, and pings you on
iMessage only when something clears the bar. No dashboard to watch, no
notification fatigue — silence means nothing needs you.

**Platform:** macOS (iMessage send uses Messages.app via AppleScript).
**Cost:** one short headless Claude run per hour (~a few cents/day).

## How it works

```
launchd (hourly)
  └─ run.sh
      ├─ fetch unread mail since last check   (Gmail access, see options)
      ├─ claude -p "triage these messages…"   (headless, JSON verdict)
      └─ if important → osascript → Messages.app → your phone
```

## Gmail access — pick what's available

The setup session should detect and use the first option that works:

1. **Gmail MCP server** already configured for Claude Code (check
   `claude mcp list` for a gmail/google server) — the headless run can call
   its search tools directly.
2. **`gog` CLI** (`brew install gog`) or **himalaya** — IMAP/API mail readers
   with OAuth flows.
3. **Gmail API + OAuth app** — classic `credentials.json` +
   `token.json` flow with a small Python fetch script
   (`google-api-python-client`); scope `gmail.readonly` is enough.

Whichever path: the fetcher should produce a compact text digest of unread
(or since-last-run) messages — sender, subject, first ~200 chars — and cap at
~30 messages per run.

## The triage prompt

The headless run gets the digest and must answer with strict JSON:

```
claude -p --output-format json <<'EOF'
You are an email triage filter. Below are emails from the last hour.
Flag ONLY things a busy person must see today: real humans waiting on them,
money/legal/deadline items, travel changes, security alerts. NOT newsletters,
receipts, promotions, social notifications, or FYI threads.

Reply with JSON only: {"important": [{"from": "...", "subject": "...",
"why": "one short clause"}]} — empty array if nothing qualifies.

<digest here>
EOF
```

## The iMessage send

```bash
osascript -e 'tell application "Messages"
  set targetService to 1st account whose service type = iMessage
  set targetBuddy to participant "+1XXXXXXXXXX" of targetService
  send "📬 Important: <from> — <subject> (<why>)" to targetBuddy
end tell'
```

Sending to your own number delivers to all your devices. First run may need
Automation permission for Messages (System Settings → Privacy & Security).

## Scheduling — launchd, not cron

`~/Library/LaunchAgents/com.user.gmail-triage.plist` with
`StartInterval: 3600` (or `StartCalendarInterval` for on-the-hour), pointing
at the run script. `launchctl load` it once. launchd survives reboots and
doesn't need the terminal open.

## State + safety rails

- Persist `last_checked_ts` and the ids of already-alerted messages in a
  small JSON state file (`~/.gmail-triage/state.json`) — never alert twice
  for the same email.
- Cap alerts at ~3 per run; if more qualify, send one summary message.
- On fetch/auth failure, fail silent (log to the state dir) — never page
  the user about plumbing.
- Keep credentials OUT of the repo: state dir only, chmod 600.

## Verify

1. Run the script by hand with a seeded "important" test email → iMessage
   arrives.
2. Run again → no duplicate alert (state file dedupe works).
3. `launchctl kickstart` the job → same behavior under launchd.
