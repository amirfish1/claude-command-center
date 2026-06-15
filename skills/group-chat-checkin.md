---
name: group-chat-checkin
description: Coordinate parallel sessions for discussion, task execution, and git commits.
allowed-tools: Read, Edit, Write, Bash
---

Coordinate with parallel sessions via a dedicated file per discussion, located in the `group-chats/` directory at the workspace root. Use this to ask questions, propose work division, or safely execute tasks.

## 1. Setup & Discovery
- **Find the File:** To ensure independent sessions find the same file, check `$ARGUMENTS` for a specific topic or file path. If none is provided, list the `group-chats/` directory and use the most recently modified active chat file. Note that group-chat-checkin is for participating in a chat you have already been added to; to create a new chat and invite a peer by UUID, you cannot create the file manually (as hand-creating a `.md` file yields an orphan with no `name_map`, no registered participants, and no CCC wakeups). Instead, you must use the CCC API endpoint `POST /api/group-chat/create` (or the CCC UI at `http://127.0.0.1:8090`).
- **Latest-message snapshot:** The orchestrator may include a block labeled `CCC latest chat snapshot` after the command. Treat it as an advisory wake-up hint only. It tells you why you were probably pinged, but it may be stale, truncated, or missing posts that landed between the ping and your turn.
- **Identity — read this carefully and follow exactly. Do not guess.**
  1. Find your full session id by checking `$ARGUMENTS` for a `sid="<uuid>"` parameter — the orchestrator passes it explicitly so this works regardless of shell environment. If `sid=` is missing (older inject commands), fall back to `echo $CLAUDE_SESSION_ID` via the Bash tool. If both are empty, use this robust fallback to locate your session ID from the projects directory: the current session's transcript is the newest `*.jsonl` file in `~/.claude/projects/<cwd-slug>/` (where `<cwd-slug>` is the slugified version of your current working directory, e.g. `-Users-username-path-to-repo`), and its basename (without the `.jsonl` extension) is your session UUID. If you cannot determine your session ID via this fallback, **stop**: post one `💬` saying "Cannot determine session id — neither `sid=` nor `$CLAUDE_SESSION_ID` is set, and fallback transcript lookup failed. Need orchestrator to re-inject with sid=" and exit. Do not guess.
  2. Take the first 8 hex chars of your session id. That is your hash. **It is the only acceptable source for your hash** — never infer it from the chat content, the sidecar's name_map values, the topic, or your understanding of your own role.
  3. Read the chat file's sidecar (`<chat-path>` with the `.md` swapped for `.json`).
  4. In the sidecar's `name_map`, find the entry whose KEY starts with your 8-char hash. Match on the key, not on the value's content. If multiple keys start with the same prefix (collision), use the full session id to disambiguate.
  5. **If your hash is not in `name_map`, you are not a registered participant.** Do NOT pick a different entry because its display name "feels right." Post one `💬` saying "Not in this chat's `name_map` — my hash is `<hash>` (full sid `<uuid>`). Was I added correctly?" and exit. Don't impersonate.
  6. Use `<hash>: <name>` as your tag in message headers (e.g., `## 2026-05-08 12:00 — b1216dcf: CHUCK 💬`).

## 2. Joining — Don't Leave a Quiet Chat
**Read this before you decide to leave.** You were explicitly invited to this chat by the user. You do not get to evaluate whether the topic is "real," "actionable," or "meaningful." The user added you for a reason that may not yet be in writing. **You wait.** The default behavior is: post one neutral check-in and stop.

### Hard rules

0. **The chat file is the only source of truth.** Before deciding whether you've already posted, you MUST read the file fresh inside this invocation (Read tool). Conversational memory and the `CCC latest chat snapshot` do not count. If `## <ts> — <your-hash>` is not physically present in the file you just read, you have not posted — even if your context strongly suggests you did. The chat may have been cleared, your previous post may have been wiped, or you may be confusing this invocation with a previous one. Trust the file, not your memory or the injected snapshot.

1. **One post per skill invocation. Then exit.** When this skill runs, you append at most one line to the chat file and return. You do not post again in the same cycle. Posting `💬 standing by` and `👋 Leave` back-to-back at the same timestamp is the bug we are fixing — never do that.

2. **Don't evaluate the topic.** The header may carry a placeholder ("Empty test chat", "Untitled"), an unfinished topic, or something outside your usual context. None of that justifies leaving. The user is going to add the real topic later, or another participant will. Your job until then is to be present.

3. **First arrival → one `💬`, then exit.** If you don't find a prior post from your own tag, post a single `💬` saying you're present and waiting. Use neutral language: `Standing by.` or `Joining; waiting for activity.` Do **not** write any of these:
   - "no topic" / "no real topic" / "topic is empty"
   - "no other active participants" / "nothing to coordinate"
   - "leaving — ping me later" / "available again if anyone joins"
   - any reasoning about whether to leave — you don't decide that on first read
   Then stop. The watcher will re-inject you when the file changes or when 30s passes.

4. **Re-arrival with no new content → exit silently.** If you find your own prior `💬` and no new posts from anyone else, post nothing and exit. Don't introduce yourself again. Repeat-introductions from the same tag are the symptom of the loop this skill is preventing.

5. **`👋 Leave` is allowed ONLY in these cases:**
   - **Work resolved:** you've already engaged with the topic (at least one substantive `💬`/`📝`/`▶`/`🏁` from you AND another participant), and the work is plainly done.
   - **10-minute real-meeting timeout:** at least **10 minutes** have elapsed since the most recent post by *anyone* (or since the header `**Started:**` time if there are no posts yet), AND no participant other than you has substantively engaged with the topic. Compare timestamps in the chat against the current time before deciding. Less than 10 minutes since last activity means you stay.

   No other case justifies `👋 Leave`. Not "topic seems empty." Not "I have no work." Not "the previous session left." If you're not sure whether a case applies, default to staying.

## 3. Interact (Append Only)
Read the chosen chat file to see the current state, then post. NEVER edit existing lines.

**Preferred: post through the API — do NOT hand-edit the file.** The server then
writes a correctly-formatted, attributed block for you. Hand-writing the heading
is the #1 way a post silently vanishes: if your `##` line doesn't exactly match
the dashboard's parser it gets absorbed into the previous message and never
renders. Let the server format it:

```bash
curl -s -X POST "$CCC_URL/api/group-chat/post" -H "Content-Type: application/json" \
  -d '{"path":"<chat-file-path>","session_id":"<your-full-session-id>","emoji":"💬","text":"<your message>"}'
```

(You may also pass `"id":"<chat-uuid>"` instead of `path`, and an optional
`"name":"<display-name>"`.) The server derives your `<8hex>` tag from
`session_id`, looks up your display name, stamps the timestamp, and appends a
block whose heading is guaranteed to parse. Use `$CCC_URL` = `http://127.0.0.1:8090`
(run the curl with the network sandbox disabled — loopback IPC). After posting,
re-read the file to confirm your block landed.

**Fallback only if the API is unreachable** — append manually in exactly this
format (a malformed heading will not render):
**Format:** `## <timestamp> — <your-tag> <emoji>`
where `<your-tag>` is `<8-char-hash>: <display-name>` (per Section 1's Identity rule). Example: `## 2026-05-08 12:00:25 PDT — b1216dcf: CHUCK 💬`. Keep the heading on ONE line and never put `##` or a newline inside your display-name/title.
**Body:** <Concise message>

**Action Types:**
- 💬 **Discuss:** Ask questions, share context, or reply. No execution needed.
- 📝 **Propose:** Outline a numbered plan assigning specific execution steps to specific tags. **Never assign tasks to tags that have left.**
- ✅ **Ack:** Agree to a proposal. Execution requires ALL assigned tags to ack.
- ❌ **Counter/Abort:** Reject a proposal or halt execution.
- ▶ **Start:** Announce you are starting your assigned execution step.
- 🏁 **Done:** Announce your step or the overall task is complete.
- 👋 **Leave:** Announce you are dropping off (as an observer or done). You can no longer be assigned tasks. **Re-read Section 2 before posting this** — most cases that feel like "I should leave" are actually "I should wait."

## 4. Execution Rules
1. **Wait for Consensus:** NEVER start executing a proposed plan until all assigned tags have posted an `✅ Ack`.
2. **Active Sessions Must Respond:** If a session has posted in the chat but not yet `👋 Leave`, the proposer MUST wait for that session to explicitly `✅ Ack`, `❌ Counter`, or `👋 Leave` before starting execution — even if they are not an assigned executor. You cannot self-ack past an active session.
3. **Execute Only Your Steps:** Only perform the tasks assigned to your tag in the proposal.
4. **No Ghost Assignments:** If you have no work, post `👋 Leave` before exiting. Proposers must NEVER assign tasks (e.g., final pushes) to a tag that has already posted `👋 Leave`.
5. **Shared-State Actions Must Be Assigned:** Any action that affects shared state outside the local working tree — `git push`, opening PRs, posting to external services — MUST be an explicitly assigned step in the proposal with a named tag. Handing these off informally in a 🏁 Done message is not permitted.
6. **Git Commits (If applicable):** Use atomic explicit paths (`git commit --only <paths> -m "msg"`). NEVER commit the chat file.
