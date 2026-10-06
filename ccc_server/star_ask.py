"""Star ask (Q17): a gentle, hard-capped "star us on GitHub" prompt.

Shown at success moments (first task done, PR merged, a savings milestone).
One click stars the repo through the local ``gh`` CLI
(``gh api -X PUT user/starred/<repo>``); when ``gh`` is missing, unsigned, or
errors, the UI falls back to a plain "open GitHub" link.

Rules that make this polite rather than naggy, all enforced server-side so
multiple open tabs cannot multiply the asks:

- at most one ask per ``MIN_ASK_INTERVAL_S`` (14 days),
- at most ``MAX_ASKS`` asks ever (3),
- "Don't ask again" is permanent,
- a confirmed star ends all asks.

State lives in ``~/.claude/command-center/star-ask.json`` (no secrets, only
timestamps and flags). Stdlib only; tests drive the ``_run_gh`` /
``_gh_bin`` seams -- no network, no real ``gh``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

REPO = "amirfish1/claude-command-center"
REPO_URL = "https://github.com/" + REPO

MAX_ASKS = 3
MIN_ASK_INTERVAL_S = 14 * 86400
# A "shown" POST arriving again within this window is the same prompt
# reported twice (two tabs, a double dispatch) -- not a new ask.
SHOWN_DEDUP_S = 3600
# How often a GET may spend one `gh api` call confirming the star state.
STARRED_CHECK_TTL_S = 86400
GH_TIMEOUT_S = 15

STATE_FILE = COMMAND_CENTER_STATE_DIR / "star-ask.json"

_ACTIONS = ("shown", "later", "never", "star")

_STATE_LOCK = threading.Lock()


def _state_path():
    return STATE_FILE


def _load_state():
    try:
        data = json.loads(_state_path().read_text())
        if isinstance(data, dict):
            asks = data.get("asks")
            data["asks"] = [float(t) for t in asks] if isinstance(asks, list) else []
            data["never"] = bool(data.get("never"))
            data["starred"] = bool(data.get("starred"))
            return data
    except (OSError, ValueError):
        pass
    return {"asks": [], "never": False, "starred": False}


def _save_state(state):
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _gh_bin():
    return shutil.which("gh")


def _run_gh(args, timeout=GH_TIMEOUT_S):
    return subprocess.run(
        ["gh"] + list(args), capture_output=True, text=True, timeout=timeout,
    )


def _remote_starred():
    """True/False when ``gh`` answered, None when the check could not run."""
    try:
        out = _run_gh(["api", f"user/starred/{REPO}"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode == 0:
        return True
    # `gh api` exits 1 with "HTTP 404" on the starred endpoint when the repo
    # is NOT starred. Any other failure (auth, network) stays unknown.
    blob = (out.stderr or "") + (out.stdout or "")
    return False if "404" in blob else None


def _should_ask(state, now):
    if state.get("never") or state.get("starred"):
        return False
    asks = state.get("asks") or []
    if len(asks) >= MAX_ASKS:
        return False
    if asks and now - asks[-1] < MIN_ASK_INTERVAL_S:
        return False
    return True


def status(now=None):
    """GET /api/star. Confirms the star remotely at most once a day so a
    user who starred via the fallback link stops being asked."""
    now = time.time() if now is None else now
    with _STATE_LOCK:
        state = _load_state()
        gh = bool(_gh_bin())
        checked = None
        if (not state["starred"] and gh
                and now - float(state.get("starred_checked_at") or 0) > STARRED_CHECK_TTL_S):
            checked = _remote_starred()
            state["starred_checked_at"] = now
            if checked is True:
                state["starred"] = True
                state["starred_at"] = now
            _save_state(state)
        asks = state.get("asks") or []
        last_ask = asks[-1] if asks else 0.0
        return {
            "ok": True,
            "repo_url": REPO_URL,
            "starred": bool(state.get("starred")),
            "gh_available": gh,
            "should_ask": _should_ask(state, now),
            "asks_used": len(asks),
            "asks_max": MAX_ASKS,
            "next_ask_at": (last_ask + MIN_ASK_INTERVAL_S) if asks else None,
        }


def _star_via_gh(state, now):
    """Attempt the one-click star. Mutates ``state`` on success."""
    if not _gh_bin():
        return {"ok": False, "code": "gh_missing",
                "error": "GitHub CLI (gh) is not installed",
                "fallback_url": REPO_URL}
    try:
        out = _run_gh(["api", "-X", "PUT", f"user/starred/{REPO}"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "code": "gh_error", "error": str(e)[:200],
                "fallback_url": REPO_URL}
    if out.returncode == 0:
        state["starred"] = True
        state["starred_at"] = now
        _save_state(state)
        return {"ok": True, "starred": True}
    detail = (out.stderr or out.stdout or "gh exited non-zero").strip()[:200]
    low = detail.lower()
    code = "gh_auth" if ("auth" in low or "login" in low or "401" in low) else "gh_error"
    return {"ok": False, "code": code, "error": detail, "fallback_url": REPO_URL}


def handle_action(action, now=None):
    """POST /api/star. Returns ``(payload, http_status)``."""
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        return {"ok": False, "error": f"unknown action: {action or '(missing)'}",
                "actions": list(_ACTIONS)}, 400
    now = time.time() if now is None else now
    with _STATE_LOCK:
        state = _load_state()
        if action == "shown":
            asks = state.setdefault("asks", [])
            if not asks or now - asks[-1] > SHOWN_DEDUP_S:
                asks.append(now)
                del asks[:-MAX_ASKS]
            _save_state(state)
            return {"ok": True, "asks_used": len(asks), "asks_max": MAX_ASKS}, 200
        if action == "later":
            state["dismissed_at"] = now
            _save_state(state)
            return {"ok": True}, 200
        if action == "never":
            state["never"] = True
            _save_state(state)
            return {"ok": True}, 200
        # action == "star"
        if state.get("starred"):
            return {"ok": True, "starred": True, "already": True}, 200
        return _star_via_gh(state, now), 200
