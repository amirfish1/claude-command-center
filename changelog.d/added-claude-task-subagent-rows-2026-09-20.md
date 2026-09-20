Claude Task-tool subagents now appear in the session list as nested children
of their parent session. The archive scan picks up
`<session>/subagents/agent-*.jsonl` transcripts alongside top-level JSONLs,
emits each as a row with `parent_session_id` set, and opens it through the
existing `<parent>:agent-<id>` composite id — so clicking a child plays back
its own transcript. Liveness comes from the transcript tail (tool_use in
flight = live), matching the family-tree lane, and subagent files are folded
into the corpus signature so new/changed/removed agents refresh through the
normal incremental path.
