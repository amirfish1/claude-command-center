# Lane C — Unified memory system + Ask tab backend (PLAN ONLY)

Updated: 2026-09-03 09:55
Status: measuring existing systems
Owner: CCC Lane C (dispatched by 25c52af6-c616-46a2-b47d-0f8a9e016055)

## Deliverables
- `~/MyOfficeMgr/projects/memory-system/plan-2026-09-03.md` (under 3 pages)
- `~/MyOfficeMgr/projects/memory-system/plan-2026-09-03.html` (decision page)
- Raw evidence: `~/MyOfficeMgr/projects/memory-system/evidence/`

## Found so far
- Total Recall v1.9.78: 29k sessions, 50k summary files, recall.db 6.5 GB.
  `brain search` preview timed out at 110 s on "Ask tab" (rc=124). Re-measuring
  with 300 s cap on 5 questions (evidence/run-tr.sh).
- Claude-Index: 25.7k sessions / 1.2M messages / FTS5 + 768-d vectors, fresh to
  the minute (CCC auto-ingests every 120 s). Claude-only. Homebrew CLI shim is
  broken (ModuleNotFoundError; editable install points at old path); the venv
  binary in dev/tools/indexing works and is what launchd runs.
- Hunch: MCP timed out at connect; CLI `why`/`query` return symbols +
  stale constraints, no decisions for ask.py.
- Ask tab: retrieval = recent scan (claude/codex/kimi/cursor, 30 d max) +
  claude-index (Claude only). No live-state (tokens, liveness, queue) source,
  so it cannot diagnose stuck/burning sessions.
- iMessage chat.db: TCC denies read (Operation not permitted). OPS-925 filed.

## Next
1. Finish 5-question runs: TR, Claude-Index, Ask tab, Hunch. Score hits.
2. Gmail MCP noise probe (search_threads) for the ingest filter.
3. Write plan.md + decision HTML; 3 name candidates.
4. Report to parent session.

## Decisions
- Test questions fixed in evidence/questions.txt (2 Claude, 1 Kimi, 1 Codex, 1 strategic).
