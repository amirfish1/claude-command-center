"""Compute and store embeddings for all messages that don't yet have one."""
from __future__ import annotations

import concurrent.futures
import queue
import sqlite3
import time
from typing import Iterator

from . import config as config_mod
from . import db as db_mod
from . import embed as embed_mod


BATCH_SIZE = 64  # Ollama processes each /api/embed call as one inference pass


def _iter_pending(conn: sqlite3.Connection, limit: int | None) -> Iterator[tuple[int, str]]:
    """Yield (message_id, content) for messages without an embedding yet."""
    sql = """
        SELECT m.id, m.content
        FROM messages m
        LEFT JOIN embeddings_state es ON es.message_id = m.id
        WHERE es.message_id IS NULL
          AND m.content IS NOT NULL
          AND length(m.content) > 0
        ORDER BY m.id
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql)
    for row in cur:
        yield row["id"], row["content"]


def _embed_with_split(
    ids: list[int],
    texts: list[str],
    *,
    model: str,
    url: str,
    max_chars: int,
) -> list[tuple[int, list[float] | None]]:
    """Embed a batch; on per-item content errors, halve and retry. A single text
    that still fails returns (id, None) so the caller can mark it permanently skipped.
    Connection-level errors propagate up unchanged.
    """
    if not ids:
        return []
    try:
        embs = embed_mod.embed_batch(
            [embed_mod.truncate_for_embedding(t, max_chars) for t in texts],
            model=model,
            url=url,
        )
        return list(zip(ids, embs))
    except embed_mod.OllamaUnavailable as e:
        msg = str(e).lower()
        if "unreachable" in msg or "isn't pulled" in msg or "not pulled" in msg:
            raise
        if len(ids) > 1:
            mid = len(ids) // 2
            return _embed_with_split(
                ids[:mid], texts[:mid], model=model, url=url, max_chars=max_chars
            ) + _embed_with_split(
                ids[mid:], texts[mid:], model=model, url=url, max_chars=max_chars
            )
        return [(ids[0], None)]


def _write_results(
    cur: sqlite3.Cursor,
    conn: sqlite3.Connection,
    results: list[tuple[int, list[float] | None]],
    *,
    model: str,
    dim: int,
    stats: dict,
) -> None:
    cur.execute("BEGIN")
    try:
        for mid, emb in results:
            if not emb:
                cur.execute(
                    """INSERT OR REPLACE INTO embeddings_state
                           (message_id, model, dim, embedded_at)
                       VALUES (?,?,?,?)""",
                    (mid, f"{model}:skip-too-long", 0, time.time()),
                )
                stats["errors"] += 1
                continue
            blob = embed_mod.to_blob(emb)
            cur.execute("DELETE FROM messages_vec WHERE rowid = ?", (mid,))
            cur.execute(
                "INSERT INTO messages_vec(rowid, embedding) VALUES (?, ?)",
                (mid, blob),
            )
            cur.execute(
                """INSERT OR REPLACE INTO embeddings_state
                       (message_id, model, dim, embedded_at)
                   VALUES (?,?,?,?)""",
                (mid, model, dim, time.time()),
            )
            stats["embedded"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        stats["errors"] += len(results)


def backfill(
    conn: sqlite3.Connection,
    limit: int | None = None,
    cfg: dict | None = None,
    progress_every: int = 200,
    on_progress=None,
    workers: int = 1,
    batch_size: int = BATCH_SIZE,
) -> dict:
    """Embed every un-embedded message and write to messages_vec.

    workers: number of concurrent in-flight Ollama requests. Speedup is real
        but sub-linear (2-3x at workers=4 typical) — GPU is the bottleneck.
        Set OLLAMA_NUM_PARALLEL=<workers> in your Ollama env to let Ollama
        actually process them concurrently rather than queuing them.
    """
    cfg = cfg or config_mod.load()
    model = cfg["embedding_model"]
    dim = cfg["embedding_dim"]
    url = cfg["ollama_url"]
    max_chars = cfg["max_embed_chars"]

    if not embed_mod.is_available(url):
        raise embed_mod.OllamaUnavailable(
            f"Ollama not reachable at {url}. Start it with `ollama serve` "
            f"(or `brew services start ollama`)."
        )
    if not embed_mod.model_present(model, url):
        raise embed_mod.OllamaUnavailable(
            f"Ollama is up at {url} but the `{model}` model isn't pulled. "
            f"Run:  ollama pull {model}"
        )

    db_mod.ensure_vec_schema(conn, dim)

    stats: dict = {"embedded": 0, "skipped": 0, "errors": 0, "elapsed_s": 0.0}
    started = time.time()

    pending = list(_iter_pending(conn, limit))
    total = len(pending)
    if not total:
        stats["elapsed_s"] = round(time.time() - started, 2)
        return stats

    # Build batches up front so we can dispatch them concurrently if requested.
    batches: list[tuple[list[int], list[str]]] = []
    cur_ids: list[int] = []
    cur_txts: list[str] = []
    for mid, content in pending:
        cur_ids.append(mid)
        cur_txts.append(content)
        if len(cur_ids) >= batch_size:
            batches.append((cur_ids, cur_txts))
            cur_ids, cur_txts = [], []
    if cur_ids:
        batches.append((cur_ids, cur_txts))

    cur = conn.cursor()
    processed = 0

    def maybe_progress() -> None:
        if on_progress and processed % progress_every < batch_size:
            on_progress(min(processed, total), total, stats)

    if workers <= 1:
        for ids, texts in batches:
            results = _embed_with_split(
                ids, texts, model=model, url=url, max_chars=max_chars
            )
            _write_results(cur, conn, results, model=model, dim=dim, stats=stats)
            processed += len(ids)
            maybe_progress()
    else:
        # Producer-consumer: workers embed (slow, parallel), main thread writes
        # to SQLite (fast, serial). Avoids cross-thread cursor sharing.
        results_q: queue.Queue = queue.Queue(maxsize=workers * 2)
        SENTINEL = object()

        def producer(b: tuple[list[int], list[str]]) -> None:
            ids, texts = b
            try:
                r = _embed_with_split(
                    ids, texts, model=model, url=url, max_chars=max_chars
                )
                results_q.put(("ok", ids, r))
            except embed_mod.OllamaUnavailable as e:
                results_q.put(("fatal", ids, e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for b in batches:
                ex.submit(producer, b)

            for _ in range(len(batches)):
                kind, ids, payload = results_q.get()
                if kind == "fatal":
                    ex.shutdown(wait=False, cancel_futures=True)
                    stats["errors"] += len(ids)
                    raise payload  # type: ignore[misc]
                _write_results(
                    cur, conn, payload, model=model, dim=dim, stats=stats
                )
                processed += len(ids)
                maybe_progress()

    if on_progress:
        on_progress(total, total, stats)
    stats["elapsed_s"] = round(time.time() - started, 2)
    return stats


def coverage(conn: sqlite3.Connection) -> dict:
    """Return how many messages have embeddings vs total."""
    n_total = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE content IS NOT NULL AND length(content) > 0"
    ).fetchone()[0]
    n_emb = 0
    if db_mod.has_vec_extension(conn):
        try:
            n_emb = conn.execute("SELECT COUNT(*) FROM embeddings_state").fetchone()[0]
        except sqlite3.OperationalError:
            n_emb = 0
    return {"total": n_total, "embedded": n_emb, "missing": n_total - n_emb}
