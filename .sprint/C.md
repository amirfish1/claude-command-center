# Lane C — Unified memory system + Ask tab backend (PLAN ONLY)

Updated: 2026-09-03 10:30
Status: DONE — plan + HTML delivered, report sent to parent
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

## Measured (evidence/)
- TR brain search: 5/5 questions timed out at 300 s (backfill --cowork running,
  load avg 45-85); TR's own degraded.log: 10,031/10,031 proactive-search
  rc=142 at 5 s cap since 2026-07-31 (1,041 today). 63% of summaries
  (18,198/29,087) are CCC scratch one-shots. Direct FTS on recall.db: 0.5 s.
- Claude-Index NL query 83-159 s (OR-rewrite -> 650k-hit BM25); raw topic
  query 1-7.5 s; GT hit 4/5 (miss = Kimi not indexed). Codex IS indexed.
- Ask tab: 4/4 timed out (>150 s) under load; server restarted by sibling at
  10:00 (Q5 conn refused). Retrieval endpoints re-timed after restart.
- Gmail: ~200 inbound threads/7d, 45 sent threads/30d, >=200 starred.
  Result bodies archived by the MCP wrapper; counts only.
- Hunch: MCP timeout + 'database is locked' (3 hunch mcp procs) -> OPS-926.
- iMessage: TCC denied -> OPS-925.

## Delivered
- plan-2026-09-03.md (1,711 words, scoreboard + options A/B/C + rec B + 8-task
  one-day plan across Sonnet/Kimi/Gemini/Grok + names Mazkir/Radar/Scout).
- plan-2026-09-03.html (decision page with approve checklist).
- Memory note: claude_index_vs_total_recall.md in the CCC project memory dir.
- Tickets: OPS-925 (iMessage FDA), OPS-926 (hunch lock/MCP timeout).

## Next (for whoever builds)
- Start with T0/T1 (Sonnet): shim fix, stopword/AND query, scratch purge.
- Then T5 (Ask agent v2) once T4 lands; run T6 eval before/after.

## Decisions
- Test questions fixed in evidence/questions.txt (2 Claude, 1 Kimi, 1 Codex, 1 strategic).
