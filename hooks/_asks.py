"""Per-tool pending permission asks for one Claude session (CCC-245).

The Notification hook writes ONE session-level `_needs_approval` marker, and
PostToolUse used to delete it whenever any tool returned. With overlapping
asks (sub-agents sharing the session id, or a parallel tool that needed no
approval) a tool that finished cleared the badge while another tool was still
parked on its permission prompt.

So permission-request.py records each ask under
`live-state/asks/<session_id>/<key>.json`, PostToolUse resolves the finished
tool's own ask, and the session marker is cleared only once no recorded ask
is still open. An ask whose tool call already has a tool_result in the
transcript (denied, interrupted) is pruned as stale; stop.py clears the lot.
Sessions on an install without the PermissionRequest hook record nothing and
keep the old clear-on-any-tool behaviour. Stdlib only; callers swallow errors.
"""

import hashlib
import json
import os
import shutil
import time

TAIL_BYTES = 512 * 1024
STALE_S = 24 * 3600


def ask_key(tool, tin):
    canon = json.dumps({"tool": str(tool or ""), "input": tin},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20]


def asks_dir(live_dir, sid):
    return os.path.join(live_dir, "asks", sid)


def _transcript_calls(path):
    """{tool_use_id: (name, input)} plus the set of ids with a tool_result,
    read from the transcript tail. ({}, set()) when unreadable."""
    uses, results = {}, set()
    if not path:
        return uses, results
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            chunk = f.read()
    except OSError:
        return uses, results
    lines = chunk.split(b"\n")
    if size > TAIL_BYTES and lines:
        lines.pop(0)                    # partial line at the tail cut
    for raw in lines:
        try:
            content = (json.loads(raw).get("message") or {}).get("content")
        except (ValueError, AttributeError):
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("id"):
                uses[b["id"]] = (b.get("name"), b.get("input"))
            elif b.get("type") == "tool_result" and b.get("tool_use_id"):
                results.add(b["tool_use_id"])
    return uses, results


def record(live_dir, sid, tool, tin, transcript_path=""):
    """Note that `tool(tin)` is waiting on a permission prompt."""
    uses, results = _transcript_calls(transcript_path)
    tool_use_id = ""
    for uid, (name, inp) in uses.items():   # last unresolved identical call
        if name == tool and inp == tin and uid not in results:
            tool_use_id = uid
    d = asks_dir(live_dir, sid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, ask_key(tool, tin) + ".json")
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump({"tool": tool, "input": tin, "tool_use_id": tool_use_id,
                   "ts": time.time()}, f)
    os.replace(tmp, path)


def _load(path):
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def resolve(live_dir, sid, tool, tin, tool_use_id="", transcript_path=""):
    """Drop the finished tool's ask (and any stale ones); return how many
    recorded asks are still open. 0 means the session marker may be cleared."""
    d = asks_dir(live_dir, sid)
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return 0
    own = ask_key(tool, tin) + ".json"
    now = time.time()
    open_asks, scanned, uses, results = 0, False, {}, set()
    for name in names:
        path = os.path.join(d, name)
        doc = _load(path)
        drop = (name == own or doc is None
                or (tool_use_id and doc.get("tool_use_id") == tool_use_id)
                or now - float(doc.get("ts") or 0) > STALE_S)
        if not drop:
            if not scanned:
                uses, results = _transcript_calls(transcript_path)
                scanned = True
            uid = doc.get("tool_use_id")
            if uid:
                drop = uid in results
            else:   # recorded before the transcript showed the call
                ids = [u for u, (n, i) in uses.items()
                       if n == doc.get("tool") and i == doc.get("input")]
                drop = bool(ids) and all(u in results for u in ids)
        if drop:
            try:
                os.unlink(path)
            except OSError:
                pass
        else:
            open_asks += 1
    if not open_asks:
        clear(live_dir, sid)
    return open_asks


def clear(live_dir, sid):
    shutil.rmtree(asks_dir(live_dir, sid), ignore_errors=True)
