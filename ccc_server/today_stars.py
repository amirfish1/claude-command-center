"""Today-only stars: a lightweight "focus today" marker for sidebar rows.

Unlike pins, a star expires on its own. The server stores when each session
was starred; the dashboard treats a star as live only if it was set after the
viewer's local midnight, so the list empties itself overnight in the user's
own timezone. Entries older than ``KEEP_SECONDS`` are pruned on every write.
"""

import json
import os
import threading
import time

KEEP_SECONDS = 48 * 3600
MAX_STARS = 500

_lock = threading.Lock()


def _read(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for sid, ts in data.items():
        if isinstance(sid, str) and sid and isinstance(ts, (int, float)) and ts > 0:
            out[sid] = float(ts)
    return out


def _prune(stars, now):
    cutoff = now - KEEP_SECONDS
    kept = {sid: ts for sid, ts in stars.items() if ts >= cutoff}
    if len(kept) > MAX_STARS:
        newest = sorted(kept.items(), key=lambda kv: kv[1], reverse=True)[:MAX_STARS]
        kept = dict(newest)
    return kept


def load(path, now=None):
    """Return {session_id: starred_at_epoch_seconds} for recent stars."""
    return _prune(_read(path), time.time() if now is None else now)


def set_star(path, session_id, starred, now=None):
    """Star or unstar one session; returns the pruned star map."""
    now = time.time() if now is None else now
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("missing session_id")
    with _lock:
        stars = _prune(_read(path), now)
        if starred:
            stars[sid] = now
        else:
            stars.pop(sid, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(stars, indent=2, sort_keys=True))
        os.replace(tmp, path)
    return stars
