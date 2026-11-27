#!/usr/bin/env python3
"""Do hour-old sidebar list tokens still get a small delta? (CCC-709)

A reloaded page sends the delta token it saved earlier. When the server had
forgotten that token's context it answered with the whole list (633 KB, 7.9 s
on a phone). Each run of this check saves the live list reader's current 7d
token, then replays older saved tokens (one about an hour old, one 4-20 hours
old) against the reader and records whether it answered with a delta.

Only tokens whose context actually changed since they were minted, from the
same reader process or one whose lineage it adopted, are informative. A full
body for a token from another process is recorded but not counted: a deploy
that changes the row projection invalidates old tokens on purpose.

Prints {"status": pass|fail|pending, "real_data": bool, "evidence": {...}}.
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WINDOW = "7d"
NEAR_AGE_S = 3600
MIN_AGE_S = 45 * 60
OLD_AGE_S = (4 * 3600, 20 * 3600)
LONG_EVERY_S = 6 * 3600  # one saved token per period is kept for the old replay
KEEP_TOKENS = 48
HISTORY_S = 7 * 86400
MINIMUM = 3


def _state_path():
    base = os.environ.get("CCC_STATE_DIR") or str(Path.home() / ".claude" / "command-center")
    return Path(base) / "check-sidebar-old-token.json"


def _load(path):
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return {"tokens": list(data.get("tokens") or []),
                    "results": list(data.get("results") or [])}
    except (OSError, ValueError):
        pass
    return {"tokens": [], "results": []}


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, path)


def _read(base_url, since=None, timeout=60):
    url = f"{base_url}/api/conversations/list?window={WINDOW}&stale_ok=1"
    if since:
        url += "&since=" + urllib.parse.quote(since)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, None, 0
        raise
    payload = json.loads(body) if body else None
    return status, payload, len(body)


def _parts(token):
    try:
        nonce, ver, ctx, _ovl = str(token).split(".")
        return nonce, int(ver), ctx
    except (TypeError, ValueError):
        return None


def _probe(base_url, saved, now):
    old = _parts(saved["token"])
    status, payload, size = _read(base_url, since=saved["token"])
    result = {"at": now, "age_s": round(now - saved["at"]), "token": saved["token"],
              "status": status, "bytes": size}
    if status == 304:
        result["kind"] = "current"
        return result, None
    new_token = (payload or {}).get("delta_token")
    new = _parts(new_token)
    result["kind"] = "delta" if (payload or {}).get("delta") is True else "full"
    if old and new:
        result["same_process"] = old[0] == new[0]
        result["ctx_changed"] = old[2] != new[2]
    return result, new_token


def _informative(result):
    # A token from another process counts only when it was answered with a
    # delta (its lineage was adopted); a full body there may be a deploy.
    if result.get("kind") not in ("delta", "full") or not result.get("ctx_changed"):
        return False
    return result.get("same_process") or result["kind"] == "delta"


def run(base_url, path, now=None):
    now = time.time() if now is None else now
    state = _load(path)
    tokens = [t for t in state["tokens"] if now - t.get("at", 0) < OLD_AGE_S[1] + 3600]
    picks = []
    near = [t for t in tokens if not t.get("long") and now - t["at"] >= MIN_AGE_S]
    if near:
        picks.append(min(near, key=lambda t: abs(now - t["at"] - NEAR_AGE_S)))
    old = [t for t in tokens if t.get("long") and OLD_AGE_S[0] <= now - t["at"] <= OLD_AGE_S[1]]
    if old:
        picks.append(min(old, key=lambda t: t["at"]))
    fresh = None
    errors = []
    for saved in picks:
        try:
            result, new_token = _probe(base_url, saved, now)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(repr(exc)[:200])
            continue
        tokens.remove(saved)
        state["results"].append(result)
        fresh = new_token or fresh
    if fresh is None:
        try:
            status, payload, _ = _read(base_url)
            fresh = (payload or {}).get("delta_token") if status == 200 else None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(repr(exc)[:200])
    if fresh:
        if not any(t["token"] == fresh and not t.get("long") for t in tokens):
            tokens.append({"at": now, "token": fresh, "long": False})
        # A copy set aside from the hourly replay, so some tokens grow old.
        if not any(t.get("long") and now - t["at"] < LONG_EVERY_S for t in tokens):
            tokens.append({"at": now, "token": fresh, "long": True})
    state["tokens"] = tokens[-KEEP_TOKENS:]
    state["results"] = [r for r in state["results"] if now - r.get("at", 0) < HISTORY_S]
    _save(path, state)

    samples = [r for r in state["results"] if _informative(r)]
    full = [r for r in samples if r["kind"] == "full"]
    if full:
        status = "fail"
    elif len(samples) < MINIMUM:
        status = "pending"
    else:
        status = "pass"
    return {"status": status, "real_data": bool(samples), "evidence": {
        "window": WINDOW, "state": str(path), "samples": len(samples), "minimum": MINIMUM,
        "full_bodies": [{k: r.get(k) for k in ("at", "age_s", "bytes", "same_process")} for r in full],
        "deltas_bytes_max": max((r["bytes"] for r in samples if r["kind"] == "delta"), default=None),
        "ages_s": sorted(r["age_s"] for r in samples),
        "probed_now": len(picks), "errors": errors,
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:18091",
                        help="list reader base URL (dashboard port + 10000)")
    parser.add_argument("--state", default=None, help="state file (default under CCC_STATE_DIR)")
    args = parser.parse_args()
    path = Path(args.state) if args.state else _state_path()
    print(json.dumps(run(args.url.rstrip("/"), path)))


if __name__ == "__main__":
    main()
