---
name: group-chat
description: Coordinate parallel sessions for discussion, task execution, and git commits.
allowed-tools: Read, Edit, Write, Bash
---

Coordinate with parallel sessions via a dedicated file per discussion, located in the `group-chats/` directory at the workspace root. Use this to ask questions, propose work division, or safely execute tasks.

## 1. Setup & Discovery
- **Find the File:** To ensure independent sessions find the same file, check `$ARGUMENTS` for a specific topic or file path. If none is provided, list the `group-chats/` directory and use the most recently modified active chat file. If you are initiating a new discussion, create a new file (e.g., `group-chats/chat_<YYYY-MM-DD>_<topic>.md`).
- **Identity:** Your tag is the first 8 chars of `$CLAUDE_SESSION_ID` (e.g., `b1216dcf`). Look up your display name by reading the chat file's sidecar (`<chat-path>` with the `.md` swapped for `.json`); inside `name_map`, find the entry whose key starts with your 8-char hash and read the value. Then use `<hash>: <name>` as your tag in message headers (e.g., `## 2026-05-08 12:00 — b1216dcf: CHUCK 💬`). If the sidecar isn't readable or your hash isn't in `name_map`, fall back to the bare `<hash>` — old chats and the older expansion path will keep working.

## 2. Joining — Don't Leave a Quiet Chat
**Read this before you decide to leave.** You were explicitly invited to this chat by the user. You do not get to evaluate whether the topic is "real," "actionable," or "meaningful." The user added you for a reason that may not yet be in writing. **You wait.** The default behavior is: post one neutral check-in and stop.

### Hard rules

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
Read the chosen chat file to see the current state. **Append** your post. NEVER edit existing lines. 

**Format:** `## <timestamp> — <your-tag> <emoji>`
where `<your-tag>` is `<8-char-hash>: <display-name>` (per Section 1's Identity rule). Example: `## 2026-05-08 12:00:25 PDT — b1216dcf: CHUCK 💬`.
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
