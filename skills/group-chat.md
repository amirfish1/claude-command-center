---
name: group-chat
description: Coordinate parallel sessions for discussion, task execution, and git commits.
allowed-tools: Read, Edit, Write, Bash
---

Coordinate with parallel sessions via a dedicated file per discussion, located in the `group-chats/` directory at the workspace root. Use this to ask questions, propose work division, or safely execute tasks.

## 1. Setup & Discovery
- **Find the File:** To ensure independent sessions find the same file, check `$ARGUMENTS` for a specific topic or file path. If none is provided, list the `group-chats/` directory and use the most recently modified active chat file. If you are initiating a new discussion, create a new file (e.g., `group-chats/chat_<YYYY-MM-DD>_<topic>.md`).
- **Identity:** Generate or retrieve your tag (e.g., hash of `$CLAUDE_SESSION_ID`, stored in `~/.claude/group-chat/sessions/<hash>.tag`).

## 2. Joining — Don't Leave a Quiet Chat
**Read this before you decide to leave.** You were explicitly invited to this chat by the user (or by another session). You do not get to bail because the chat looks quiet. Specifically:

- **The header has a topic.** The first lines of the chat file are `# Group Chat — <topic>`, `**Started:**`, `**Mode:**`, `**Participants:**`. That topic line **is** the topic. Do not say "no topic" or "empty chat" when a topic line is present.
- **Waiting is the default.** If you arrive and find only the header (no posts yet) or only your own prior posts, your job is to **wait**, not to leave. Other participants may not have woken up yet.
- **Introduce yourself once.** On your first arrival, post a single `💬` message acknowledging the topic — e.g., "On the topic of `<X>`: standing by, waiting for others." Then stop and exit this skill cycle. You will be re-injected when the file changes or when another session posts.
- **You may post `👋 Leave` in any of these cases — never before:**
  1. **Work resolved:** you've engaged with the topic (at least one substantive `💬`/`📝`/`▶`/`🏁`), there's been two-way activity from another participant, and the work is plainly done or you have nothing further to contribute.
  2. **Real-meeting timeout:** like a meeting where nobody shows up — if **10 minutes** have elapsed since the most recent post by anyone (your own posts count) AND no other participant has engaged with the topic, you may post `👋 Leave`. Compare timestamps in the chat file (and the header `**Started:**` if there are no posts yet) against the current time before deciding.
  3. **Plainly the wrong room:** if the chat header's topic is clearly outside your context and no one has addressed you, post one `💬` saying so, wait one re-injection cycle, then `👋 Leave`.
- **Never post a `👋` body that says "empty, leaving" on first read.** That is the failure mode this section exists to prevent. If less than 10 minutes have passed since the chat was started or last touched, you stay.

## 3. Interact (Append Only)
Read the chosen chat file to see the current state. **Append** your post. NEVER edit existing lines. 

**Format:** `## <timestamp> — <your-tag> <emoji>`
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
