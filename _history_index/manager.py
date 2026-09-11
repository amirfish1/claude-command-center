"""Drive ingest + embed in a background thread; expose freshness state.

Single instance per CCC server process. The lock-based singleton guards
against concurrent ingest attempts (a noisy click on "Enable" or two
peer servers triggering at once would otherwise stampede the SQLite WAL).

Read-paths (search) don't go through this — they open their own
mode=ro connection in server.py and never block on the indexer thread.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from . import backfill as _backfill
from . import config as _config
from . import db as _db
from . import embed as _embed
from . import ingest as _ingest


class IndexerManager:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (Path.home() / ".claude-index" / "index.db")
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._is_indexing = False
        self._is_embedding = False
        self._last_run_started = 0.0
        self._last_run_finished = 0.0
        self._last_stats: dict = {}
        self._last_error: Optional[str] = None
        self._embed_progress: dict = {"done": 0, "total": 0}
        self._status_db_cache: dict = {}
        self._status_db_cache_at = 0.0

    # ---- public API -----------------------------------------------------

    # DB-derived fields (message_count/latest_message_unix/semantic) require
    # COUNT(*) + MAX() scans over the messages table, which is not cheap once
    # the index grows past ~1M rows (~0.5s cold on an 892k-row/4GB db observed
    # in production, and multiple seconds under concurrent polling from
    # several browser tabs). This endpoint is polled every 4s while indexing
    # and was showing up as the single most frequent [SLOW] offender in
    # service.out.log. The in-memory flags (indexing/embedding progress) stay
    # live every call; only the expensive scan is throttled.
    _STATUS_DB_CACHE_TTL_S = 10.0

    def status(self) -> dict:
        """Snapshot of indexer state + index health. Cheap; safe to poll."""
        out: dict = {
            "db_path": str(self.db_path),
            "exists": self.db_path.is_file(),
            "indexing": self._is_indexing,
            "embedding": self._is_embedding,
            "embed_progress": dict(self._embed_progress),
            "last_run_started_unix": self._last_run_started or None,
            "last_run_finished_unix": self._last_run_finished or None,
            "last_stats": dict(self._last_stats),
            "last_error": self._last_error,
            "message_count": 0,
            "latest_message_unix": None,
            "semantic": {
                "available": False,
                "embedded_count": 0,
                "latest_embed_unix": None,
                "latest_embedded_message_unix": None,
            },
        }
        if not out["exists"]:
            return out
        now = time.time()
        if self._status_db_cache and (now - self._status_db_cache_at) < self._STATUS_DB_CACHE_TTL_S:
            out.update(self._status_db_cache)
            return out
        try:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*), MAX(ts_unix) FROM messages")
                row = cur.fetchone()
                out["message_count"] = row[0] or 0
                out["latest_message_unix"] = row[1]
                # Try to load sqlite-vec to read embeddings_state freshness;
                # silently no-op if extension unavailable.
                _db._try_load_vec(conn)
                try:
                    cur.execute(
                        "SELECT COUNT(*), MAX(embedded_at) FROM embeddings_state"
                    )
                    erow = cur.fetchone()
                    emb_count = erow[0] or 0
                    if emb_count > 0:
                        cur.execute(
                            """SELECT MAX(m.ts_unix) FROM messages m
                               JOIN embeddings_state e ON e.message_id = m.id"""
                        )
                        latest_emb_msg = cur.fetchone()[0]
                        out["semantic"] = {
                            "available": True,
                            "embedded_count": emb_count,
                            "latest_embed_unix": erow[1],
                            "latest_embedded_message_unix": latest_emb_msg,
                        }
                except sqlite3.OperationalError:
                    # No embeddings_state table yet — never backfilled
                    pass
            finally:
                conn.close()
            self._status_db_cache = {
                "message_count": out["message_count"],
                "latest_message_unix": out["latest_message_unix"],
                "semantic": dict(out["semantic"]),
            }
            self._status_db_cache_at = now
        except Exception as e:
            out["status_error"] = str(e)
        return out

    def maybe_ingest(self, *, min_gap_sec: float = 120.0, with_embed: bool = True) -> bool:
        """Search-triggered freshness: kick a background ingest only when none
        is running and the last one started more than min_gap_sec ago. The gap
        check is in-memory, so this is cheap enough to call on every search
        request."""
        with self._lock:
            if self._is_indexing or (self._thread and self._thread.is_alive()):
                return False
            ref = max(self._last_run_finished, self._last_run_started)
            if ref and (time.time() - ref) < min_gap_sec:
                return False
        return self.start_ingest(with_embed=with_embed)

    def start_ingest(self, *, with_embed: bool = True) -> bool:
        """Kick a background ingest pass. Returns False if one is already running."""
        with self._lock:
            if self._is_indexing or (self._thread and self._thread.is_alive()):
                return False
            self._is_indexing = True
            self._last_error = None
            self._last_run_started = time.time()
        t = threading.Thread(
            target=self._run_ingest, args=(with_embed,), daemon=True,
            name="ccc-history-indexer",
        )
        t.start()
        self._thread = t
        return True

    # ---- internal -------------------------------------------------------

    def _run_ingest(self, with_embed: bool) -> None:
        try:
            conn = _db.connect(self.db_path)
            try:
                stats = _ingest.ingest(conn, verbose=False)
                self._last_stats = stats
            finally:
                conn.close()
            # Embed pass — only if Ollama is up + model is pulled. Silently
            # skip on a fresh machine that hasn't installed Ollama yet; the
            # user can opt in to semantic later by pulling the model and
            # re-running ingest.
            if with_embed and _embed.is_available():
                cfg = _config.load()
                model = cfg.get("embedding_model", "nomic-embed-text")
                if _embed.model_present(model):
                    self._is_embedding = True
                    try:
                        embed_conn = _db.connect(self.db_path)
                        try:
                            def _on_progress(done, total, _stats):
                                self._embed_progress = {"done": done, "total": total}
                            _backfill.backfill(
                                embed_conn,
                                cfg=cfg,
                                on_progress=_on_progress,
                                workers=2,
                            )
                        finally:
                            embed_conn.close()
                    except Exception as e:
                        # Embed errors don't fail the whole run — lexical index is fine.
                        self._last_error = f"embed: {e}"
                    finally:
                        self._is_embedding = False
        except Exception as e:
            self._last_error = str(e)
        finally:
            self._is_indexing = False
            self._last_run_finished = time.time()


# Module-level singleton — server.py does `from _history_index.manager
# import indexer` and calls indexer.status() / indexer.start_ingest().
indexer = IndexerManager()
