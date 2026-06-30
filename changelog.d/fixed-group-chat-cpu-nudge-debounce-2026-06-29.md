Fixed CPU spike on group-chat creation: added server-side 60 s debounce to `/api/group-chat/nudge` so the reader's 3 s poll no longer triggers back-to-back LLM calls per participant.
