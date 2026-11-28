- **Grok transcripts no longer go missing behind plumbing noise.** Grok
  Build >=1.0.92 leaves sessions that never completed a turn with an
  `updates.jsonl` of pure `_x.ai` status events; transcript source
  selection is now content-aware — it prefers whichever of
  `updates.jsonl` / `chat_history.jsonl` / the CCC ACP transcript
  actually carries conversation events, so cancelled or worker-driven
  sessions show their prompt instead of an empty pane. Injected context
  rows (`<user_info>`, `<system-reminder>`) are also filtered from
  chat-history transcripts and titles now mine the real `<user_query>`
  prompt.
