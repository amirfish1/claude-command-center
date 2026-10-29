# Tap-backs on agent messages (design)

Goal: a one-tap reaction (thumbs up, etc.) on an agent message that reaches the
agent with the turn it refers to, without forcing a paid extra turn.

## Prior art
- Claude Code issues [#54019](https://claudeissues.com/issue/54019-add-a-quick-thumbs-up-down-reaction-to-responses-so-users-can-give-feedback-with)
  (reaction that does not trigger a new reply; "It worked" costs tokens) and
  [#4569](https://claudeissues.com/issue/4569-feature-request-add-thumbs-up-down-feedback-command)
  (Copilot-style feedback command). No shipped mechanism found.
- Cursor: thumbs up in the Agent Window skips follow-up questions (forum: "Agent Feedback Form logic").
- Amplitude, Dust, IBM watsonx: the rating is a metadata event keyed by message
  id, stored server-side; the model never sees it.
- Common pattern: reaction = event on a message id, not a new user message.

## Decision
Two tiers, both keyed by the assistant event's turn number:

1. **Deferred (default, zero extra tokens).** A tap-back is stored in CCC state
   (`session_id`, `turn`, `reaction`, `ts`) and shown on the message. It is NOT
   injected. When the user next sends a real message, pending reactions are
   prepended to it as one line each, e.g. `[158] Thumbs up`. The agent sees
   them in a turn that was happening anyway.
2. **Immediate (opt-in, per tap).** Long-press or a "send now" affordance
   injects `[158] Thumbs up` at once, accepting one extra turn. Only worth it
   for negative reactions or when the agent is waiting on the user.

Rules:
- A reaction on an idle session never wakes it by default (see inject-receipt
  and stuck-session rules; no FYI pings).
- Last reaction per (session, turn) wins; tapping again toggles it off.
- Turn label uses the transcript's existing event index so the agent can find it.
- Prefix is plain text, so it works for every engine (Claude, Codex, agy) with no protocol change.

## Implementation slices
1. Server: `POST /api/session/<sid>/tapback` + store + merge pending into the
   next send path (additive API only).
2. UI: hover/long-press reaction chip on `.event.assistant`, rendered state.
3. Immediate-send affordance.
