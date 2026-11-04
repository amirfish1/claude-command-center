# W3f — Fix the perf-budget test that fails on main

## Status: DONE (no code change needed)

## Summary
Lane goal: `python3 -m pytest tests/test_perf_budget.py` fully green on
origin/main. The original Codex lane was rejected on its first turn
(model_not_found), so this run started from scratch on the same worktree.

## Findings
- Ran the full file: **153 passed in ~53s** (see verification below).
- `tests/test_perf_budget.py` is byte-identical between `astra/W3f` HEAD
  and `origin/main` (`git diff --stat HEAD origin/main -- tests/` is empty).
- The failure lane L6 reported on the baseline is no longer reproducible —
  it was apparently fixed by already-merged work at this worktree's base
  (recent `perf(live)` / `fix(codex)` commits). Per repo rules the bound
  was never relaxed; no test edits were made.

## Verification
- `timeout 290 python3 -m pytest tests/test_perf_budget.py -x -q`
  → `153 passed in 53.08s` (2026-11-04 ~07:12 UTC, worktree W3f).

## Commits
- This file only; no source or test changes required.

## Left / owner decisions
- None. If the failure resurfaces it is environment- or timing-specific;
  re-run the file to capture the failing test name before changing code.

## Restarts
- CCC dashboard server: no restart needed (no code change)
- Worker: no restart needed
- WatchTower: no restart needed
