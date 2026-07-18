# Token Optimizer quality index

## Problem

CCC enriches conversation and usage payloads with Token Optimizer (TO) quality
metadata: score, grade, timestamp, and a short summary. That metadata is
advisory UI chrome. It must never affect the latency or correctness of a
conversation, archive, attention, or usage request.

The original integration searched TO cache directories while building rows. A
recent mitigation shares one directory listing for a short interval, which
removes the per-row glob storm. It is still request-triggered, however, and
still makes optional metadata dependent on filesystem work.

## Goal

Move quality discovery entirely off CCC request paths. CCC should read a
precomputed quality map from memory; TO should publish the map whenever it
writes a quality result.

## Design

TO publishes one small, atomically-written index per runtime:

```
~/.claude/token-optimizer/quality-index.json
~/.codex/token-optimizer/quality-index.json
```

The index is keyed by session ID. Each record includes the quality score,
grade, summary, quality-result timestamp, source-file mtime, and the transcript
mtime used to calculate the score.

TO owns index updates because it already knows which session it evaluated and
which result it wrote. A TO result is valid for its recorded transcript mtime;
an unchanged transcript retains its prior quality result. CCC does not search
for individual quality-cache files to establish this relationship.

CCC runs a low-priority refresher that stats only the two index files. When an
index file changed, CCC reads and validates it, merges the two runtime maps,
then publishes a complete replacement map with a single reference swap. API
handlers only perform a dictionary lookup. A missing, stale, or invalid index
is represented by an absent quality pill, never by synchronous fallback I/O.

When both runtime indexes contain the same session ID, the record with the
newest source-file mtime wins. Equal mtimes use a stable runtime/path
tie-breaker so the result does not flicker.

## Lifecycle and failure handling

- On CCC startup, index hydration runs in the background. Quality pills may be
  absent until it completes.
- TO writes each index atomically (temporary file followed by replacement), so
  CCC does not observe a partial index.
- CCC preserves its last known-good map if an index read or JSON validation
  fails, and retries at the next refresh interval.
- No request handler may scan a TO directory, parse a TO file, wait for the
  refresher, or initiate a TO refresh.
- Existing per-session quality-cache files remain TO's detailed artifact; the
  index is the sole CCC integration surface.

## Verification

1. Concurrent API reads during an index replacement always see either the old
   complete map or the new complete map.
2. A malformed or temporarily unreadable index preserves the prior map and
   does not affect endpoint responses.
3. An updated TO result with a newer transcript mtime appears after the next
   background refresh without any request-time directory scan.
4. Unchanged session transcripts do not cause additional TO result reads.
5. Duplicate session IDs across the two indexes resolve by mtime, then the
   documented stable tie-breaker.
6. Static/performance tests assert that conversation, archive, attention, and
   usage handlers consult only the in-memory quality map.

## Non-goals

- Regrading sessions inside CCC.
- Making quality metadata a health, scheduling, or correctness input.
- Retrofitting historical TO cache files synchronously during a request.
