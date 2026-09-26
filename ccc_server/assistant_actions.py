# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Propose/confirm actions for the Ask agent (Mazkir's "hands").

The agent can never execute an outward-facing action. Its action tools only
*propose*: the ccc-state MCP POSTs to /api/assistant/actions/propose and gets
back an id. The store keeps a random confirm token next to the proposal; that
token travels only in the /api/assistant/ask response to the browser, never
back through the MCP, so the model has no way to confirm its own proposal.
The UI shows the server-written description (not model prose) on a Confirm
card, and only POST /api/assistant/actions/<id>/confirm with the token runs
it: once, within PROPOSAL_TTL_SEC.

Execution reuses CCC's own endpoints over loopback (spawn, inject) or the
`wt` CLI as a plain argv (no shell), so a confirmed action behaves exactly
like the same action taken from the UI or a terminal.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROPOSAL_TTL_SEC = 30 * 60
MAX_PROPOSALS = 200
EXEC_TIMEOUT_SEC = 60

_SID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_:.-]{4,127}$")
_QUEUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}-\d{1,7}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\[\]-]{0,63}$")
_ENGINES = ("claude", "codex", "kimi", "gemini", "antigravity", "grok", "devin")
_PRIORITIES = {"low": "p3", "normal": "p2", "high": "p1", "p1": "p1", "p2": "p2", "p3": "p3"}

KINDS = ("spawn_session", "inject", "wt_add", "wt_comment")


class ActionError(ValueError):
    pass


def _text(params, key, limit, required=True):
    v = str(params.get(key) or "").strip()
    if required and not v:
        raise ActionError(f"{key} is required")
    if len(v) > limit:
        raise ActionError(f"{key} is longer than {limit} characters")
    return v


def validate(kind: str, params: dict) -> dict:
    """Normalize params for `kind`; raise ActionError on anything off-contract."""
    if kind not in KINDS:
        raise ActionError(f"unknown action kind {kind!r}")
    params = params if isinstance(params, dict) else {}
    if kind == "spawn_session":
        cwd = os.path.expanduser(_text(params, "cwd", 1024))
        if not os.path.isabs(cwd) or not os.path.isdir(cwd):
            raise ActionError("cwd must be an existing absolute directory")
        out = {"cwd": os.path.realpath(cwd), "prompt": _text(params, "prompt", 8000)}
        engine = _text(params, "engine", 32, required=False).lower() or "claude"
        if engine not in _ENGINES:
            raise ActionError(f"engine must be one of {', '.join(_ENGINES)}")
        out["engine"] = engine
        model = _text(params, "model", 64, required=False)
        if model and not _MODEL_RE.match(model):
            raise ActionError("model has unexpected characters")
        if model:
            out["model"] = model
        name = _text(params, "name", 120, required=False)
        if name:
            out["name"] = name
        return out
    if kind == "inject":
        sid = _text(params, "session_id", 128)
        if not _SID_RE.match(sid):
            raise ActionError("session_id has unexpected characters")
        return {"session_id": sid, "text": _text(params, "text", 8000)}
    if kind == "wt_add":
        queue = _text(params, "queue", 64)
        if not _QUEUE_RE.match(queue):
            raise ActionError("queue must be a WatchTower queue name")
        prio = _text(params, "priority", 8, required=False).lower() or "normal"
        if prio not in _PRIORITIES:
            raise ActionError("priority must be low, normal, or high")
        return {"queue": queue, "title": _text(params, "title", 200),
                "text": _text(params, "text", 8000), "priority": _PRIORITIES[prio]}
    ref = _text(params, "ref", 72)
    if not _REF_RE.match(ref):
        raise ActionError("ref must look like QUEUE-123")
    return {"ref": ref, "text": _text(params, "text", 4000)}


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(kind: str, p: dict) -> dict:
    """Server-written Confirm-card copy: a label, a one-line effect, the detail."""
    if kind == "spawn_session":
        who = p["engine"] + (f" ({p['model']})" if p.get("model") else "")
        return {"label": "Start session",
                "effect": f"Start a new {who} session in {Path(p['cwd']).name}",
                "detail": p["prompt"], "target": p["cwd"]}
    if kind == "inject":
        return {"label": "Send to session",
                "effect": f"Send a message to session {p['session_id'][:8]}",
                "detail": p["text"], "target": p["session_id"]}
    if kind == "wt_add":
        return {"label": "File ticket",
                "effect": f"File a {p['priority']} ticket in {p['queue']}: {_clip(p['title'], 90)}",
                "detail": p["text"], "target": p["queue"]}
    return {"label": "Comment",
            "effect": f"Comment on {p['ref']}", "detail": p["text"], "target": p["ref"]}


class ActionStore:
    def __init__(self, ttl: float = PROPOSAL_TTL_SEC, cap: int = MAX_PROPOSALS, clock=time.time):
        self.ttl, self.cap, self.clock = ttl, cap, clock
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _prune_locked(self):
        now = self.clock()
        for aid in [a for a, v in self._items.items() if now - v["created"] > self.ttl * 4]:
            del self._items[aid]
        while len(self._items) > self.cap:
            del self._items[min(self._items, key=lambda a: self._items[a]["created"])]

    def propose(self, kind: str, params: dict, reason: str = "") -> dict:
        p = validate(kind, params)
        aid = "act_" + secrets.token_hex(6)
        item = {"id": aid, "kind": kind, "params": p, "reason": _clip(reason or "", 300),
                "created": self.clock(), "token": secrets.token_urlsafe(18),
                "status": "proposed", "result": None, **describe(kind, p)}
        with self._lock:
            self._items[aid] = item
            self._prune_locked()
        return {"ok": True, "action_id": aid, "status": "proposed",
                "effect": item["effect"],
                "note": "Proposed only. Cite it as [[action:confirm:" + aid + "]] on its own line; "
                        "the user must click Confirm in CCC before anything happens."}

    def get(self, aid: str) -> dict | None:
        with self._lock:
            it = self._items.get(aid)
            return dict(it) if it else None

    def since(self, t0: float) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._items.values() if v["created"] >= t0]

    def public(self, it: dict, with_token: bool = False) -> dict:
        out = {k: it[k] for k in ("id", "kind", "label", "effect", "detail", "target", "status", "reason")}
        out["params"] = dict(it["params"])
        out["expires_in_s"] = max(0, int(it["created"] + self.ttl - self.clock()))
        if it.get("result") is not None:
            out["result"] = it["result"]
        if with_token and it["status"] == "proposed":
            out["confirm_token"] = it["token"]
        return out

    def _claim(self, aid: str, token: str, decision: str) -> dict:
        with self._lock:
            it = self._items.get(aid)
            if it is None:
                raise ActionError("unknown or expired action")
            if not token or not secrets.compare_digest(str(token), it["token"]):
                raise PermissionError("bad confirm token")
            if it["status"] != "proposed":
                raise ActionError(f"action already {it['status']}")
            if self.clock() - it["created"] > self.ttl:
                it["status"] = "expired"
                raise ActionError("action expired; ask again")
            it["status"] = decision
            return dict(it)

    def dismiss(self, aid: str, token: str) -> dict:
        return self.public(self._claim(aid, token, "dismissed"))

    def confirm(self, aid: str, token: str, executor) -> dict:
        it = self._claim(aid, token, "running")
        try:
            result = executor(it["kind"], it["params"])
            status = "done" if result.get("ok") else "failed"
        except Exception as e:  # executor failures are reported, not raised
            result, status = {"ok": False, "error": str(e)[:300]}, "failed"
        with self._lock:
            live = self._items.get(aid)
            if live is not None:
                live["status"], live["result"] = status, result
                it = dict(live)
        return self.public(it)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def wt_argv() -> list[str] | None:
    """`CCC_WT_CMD` (shell-split) wins; else a `wt` on PATH or a usual prefix."""
    override = os.environ.get("CCC_WT_CMD", "").strip()
    if override:
        return shlex.split(override)
    for cand in ("wt", "/opt/homebrew/bin/wt", "/usr/local/bin/wt", str(Path.home() / ".local" / "bin" / "wt")):
        found = shutil.which(cand) if "/" not in cand else (cand if os.access(cand, os.X_OK) else None)
        if found:
            return [found]
    return None


def _post_json(base: str, path: str, body: dict, timeout: float = EXEC_TIMEOUT_SEC) -> dict:
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        data.setdefault("ok", False)
        data.setdefault("error", f"HTTP {e.code}")
    return data if isinstance(data, dict) else {"ok": False, "error": "non-object response"}


def make_executor(base: str, run=subprocess.run, post=_post_json):
    def execute(kind: str, p: dict) -> dict:
        if kind == "spawn_session":
            body = {k: p[k] for k in ("cwd", "prompt", "engine", "model", "name") if p.get(k)}
            data = post(base, "/api/sessions/spawn", body)
            ok = bool(data.get("ok", True)) and not data.get("error")
            return {"ok": ok, "session_id": data.get("session_id") or data.get("sid"),
                    "error": data.get("error")}
        if kind == "inject":
            data = post(base, "/api/inject-input", {"session_id": p["session_id"], "text": p["text"],
                                                    "announced_from": "mazkir"})
            return {"ok": bool(data.get("ok")), "queued": data.get("queued"), "error": data.get("error")}
        wt = wt_argv()
        if not wt:
            return {"ok": False, "error": "wt CLI not found (set CCC_WT_CMD)"}
        if kind == "wt_add":
            argv = wt + ["add", "-q", p["queue"], "--title", p["title"], "--text", p["text"],
                         "--priority", p["priority"]]
        else:
            argv = wt + ["comment", p["ref"], p["text"], "--by", "human"]
        proc = run(argv, capture_output=True, text=True, timeout=EXEC_TIMEOUT_SEC)
        out = (proc.stdout or "").strip()
        ref = None
        m = re.search(r"\b([A-Z][A-Z0-9_-]{0,63}-\d{1,7})\b", out)
        if m:
            ref = m.group(1)
        return {"ok": proc.returncode == 0, "ref": ref, "output": out[-300:],
                "error": None if proc.returncode == 0 else (proc.stderr or out or "wt failed")[-300:]}
    return execute


_STORE: ActionStore | None = None
_STORE_LOCK = threading.Lock()


def store() -> ActionStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ActionStore()
        return _STORE
