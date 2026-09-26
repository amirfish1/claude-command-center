"""One-click Claude Code re-authentication (preview flag ``claude_reauth``).

When a node's Claude Code login dies ("Failed to authenticate: OAuth session
expired and could not be refreshed", "Not logged in · Please run /login"),
every worker on that node stops. Copying ~/.claude/.credentials.json between
hosts does not fix it: OAuth refresh tokens rotate, so each machine needs its
own login. This module drives that login on the node that needs it:

1. ``start_login`` runs ``claude auth login`` inside a detached tmux session.
   tmux is required: a nohup / </dev/null login can never receive the pasted
   code, and TIOCSTI keystroke injection is disabled on hardened Linux hosts.
   ``BROWSER=true`` stops the node opening a browser of its own; the printed
   authorize URL goes back to the user's browser instead.
2. The user approves in their browser and gets a one-time ``<code>#<state>``.
3. ``submit_code`` pastes it into the SAME tmux session. The code is bound to
   that attempt's PKCE state, so an attempt is never restarted between showing
   the URL and submitting the code. The code travels to tmux over stdin
   (``load-buffer -``), never argv, and is never logged or echoed back.
4. ``claude auth status`` (and optionally ``claude -p``) proves it worked.

Federated peers run the same functions on their own loopback via the
federation route table (claude_auth_* actions), so no ssh for the user.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import federation
from ccc_server import core as _core

CLAUDE_AUTH_FLAG = "claude_reauth"
# Override only to keep test/second-instance logins apart on one tmux server.
CLAUDE_AUTH_TMUX_SESSION = (os.environ.get("CCC_CLAUDE_AUTH_TMUX_SESSION") or "").strip() or "ccc-claude-auth"
_CLAUDE_AUTH_TMUX_BUFFER = "ccc-claude-auth-code"
# An attempt older than this is treated as abandoned: the next Start replaces
# it instead of handing back a URL whose PKCE state may have expired.
_CLAUDE_AUTH_ATTEMPT_TTL_S = 15 * 60
_CLAUDE_AUTH_URL_WAIT_S = 25.0
_CLAUDE_AUTH_SUBMIT_WAIT_S = 45.0

# Synthetic API-error text Claude Code writes into the transcript when its
# credentials are gone. Matched only against SHORT assistant text (see
# claude_auth_failed_from_meta) so a normal answer that merely discusses
# auth errors does not light the Re-authenticate action.
CLAUDE_AUTH_FAILURE_RE = re.compile(
    r"(OAuth session expired|could not be refreshed|Failed to authenticate|"
    r"claude is not authenticated|Not logged in\W+Please run /login|"
    r"OAuth token (?:has )?expired|authentication_failed)",
    re.IGNORECASE,
)
_CLAUDE_AUTH_SHORT_TEXT = 600

_CLAUDE_AUTH_URL_RE = re.compile(r"https://claude\.(?:com|ai)/\S*oauth/authorize\?\S+")
# `<code>#<state>`: URL-safe base64-ish on both sides. Anything else (spaces,
# quotes, control characters) is rejected before it gets near the terminal.
_CLAUDE_AUTH_CODE_RE = re.compile(r"^[A-Za-z0-9._~\-]{8,1024}(?:#[A-Za-z0-9._~\-]{1,1024})?$")
_CLAUDE_AUTH_SUCCESS_RE = re.compile(r"(login successful|logged in as|successfully logged in)", re.IGNORECASE)
_CLAUDE_AUTH_ERROR_RE = re.compile(r"(invalid code|invalid_grant|error|failed|expired)", re.IGNORECASE)

_claude_auth_lock = threading.Lock()
_claude_auth_state = {
    "state": "idle",  # idle | awaiting_code | verifying | succeeded | failed
    "attempt_id": None,
    "url": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "logged_in": None,
    "email": None,
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def is_claude_auth_failure_text(text):
    """True when ``text`` reads like Claude Code's own auth-failure message."""
    if not text or not isinstance(text, str):
        return False
    return bool(CLAUDE_AUTH_FAILURE_RE.search(text[:2000]))


def claude_auth_failed_from_meta(meta):
    """Row flag: did this session's last assistant turn die on auth?

    Prefers the structured ``last_api_error`` the tail parser records from
    Claude Code's synthetic ``isApiErrorMessage`` turn; falls back to short
    last-assistant text for transcripts parsed before that field existed.
    """
    if not isinstance(meta, dict):
        return False
    api_error = meta.get("last_api_error")
    if api_error:
        return str(api_error).lower() in ("authentication_failed", "oauth_expired")
    text = meta.get("last_assistant_text")
    if not isinstance(text, str) or len(text) > _CLAUDE_AUTH_SHORT_TEXT:
        return False
    return is_claude_auth_failure_text(text)


# ---------------------------------------------------------------------------
# Process plumbing
# ---------------------------------------------------------------------------


def _claude_auth_tmux_bin():
    found = shutil.which("tmux")
    if found:
        return found
    # LaunchAgent / systemd PATHs are sparse; look where tmux actually lives.
    for cand in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux", "/bin/tmux"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _claude_auth_claude_bin():
    try:
        info = _core._resolve_claude_bin()
    except Exception:
        info = None
    if isinstance(info, dict) and info.get("available") and info.get("bin"):
        return info["bin"]
    return shutil.which("claude")


def _claude_auth_run_as_prefix():
    """``sudo -n -u <user> -H`` when workers run as a different account.

    CCC_CLAUDE_AUTH_USER names the account whose ~/.claude the workers read
    (e.g. a dedicated worker user on a server). Unset or equal to the current
    user means "log in as whoever runs this CCC", the common case.
    """
    user = (os.environ.get("CCC_CLAUDE_AUTH_USER") or "").strip()
    if not user or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", user):
        return []
    try:
        import pwd
        if pwd.getpwuid(os.getuid()).pw_name == user:
            return []
    except (ImportError, KeyError):
        pass
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    return [sudo, "-n", "-u", user, "-H"]


def _claude_auth_tmux(*args, input_text=None, timeout=10):
    tmux = _claude_auth_tmux_bin()
    if not tmux:
        raise FileNotFoundError("tmux")
    return subprocess.run(
        [tmux, *args], input=input_text, capture_output=True, text=True, timeout=timeout,
    )


def _claude_auth_session_alive():
    try:
        r = _claude_auth_tmux("has-session", "-t", CLAUDE_AUTH_TMUX_SESSION, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False
    return r.returncode == 0


def _claude_auth_capture():
    try:
        r = _claude_auth_tmux("capture-pane", "-p", "-J", "-t", CLAUDE_AUTH_TMUX_SESSION, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _claude_auth_kill_session():
    try:
        _claude_auth_tmux("kill-session", "-t", CLAUDE_AUTH_TMUX_SESSION, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass


def _claude_auth_redact(text, secret=None):
    """Last few non-empty pane lines, with the code and any URL scrubbed."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    tail = " | ".join(lines[-3:])
    if secret:
        tail = tail.replace(secret, "[redacted]")
        head = secret.split("#", 1)[0]
        if head:
            tail = tail.replace(head, "[redacted]")
    tail = re.sub(r"https?://\S+", "[url]", tail)
    # Anything code-shaped (long URL-safe run) is scrubbed as a last resort.
    tail = re.sub(r"[A-Za-z0-9._~\-]{32,}(?:#[A-Za-z0-9._~\-]+)?", "[redacted]", tail)
    return tail[:300]


def _claude_auth_set(**fields):
    with _claude_auth_lock:
        _claude_auth_state.update(fields)
        return dict(_claude_auth_state)


def claude_auth_public_state():
    with _claude_auth_lock:
        return dict(_claude_auth_state)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def claude_auth_status(timeout=20):
    """Run ``claude auth status`` and return only non-secret fields."""
    claude = _claude_auth_claude_bin()
    if not claude:
        return {"ok": False, "error": "claude_unavailable",
                "detail": "Claude Code CLI not found on this node"}
    cmd = [*_claude_auth_run_as_prefix(), claude, "auth", "status"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": "claude auth status timed out"}
    except OSError as e:
        return {"ok": False, "error": "spawn_failed", "detail": str(e)[:200]}
    out = r.stdout or ""
    start = out.find("{")
    data = None
    if start >= 0:
        try:
            data = json.loads(out[start:out.rfind("}") + 1])
        except ValueError:
            data = None
    if not isinstance(data, dict):
        # Older CLIs exit non-zero with prose when logged out.
        return {"ok": True, "logged_in": False, "email": None, "auth_method": None}
    return {
        "ok": True,
        "logged_in": bool(data.get("loggedIn")),
        "email": data.get("email") if isinstance(data.get("email"), str) else None,
        "auth_method": data.get("authMethod") if isinstance(data.get("authMethod"), str) else None,
        "subscription": data.get("subscriptionType") if isinstance(data.get("subscriptionType"), str) else None,
    }


def claude_auth_smoke(timeout=90):
    """``claude -p "reply ok"`` — proves inference works, not just the token."""
    claude = _claude_auth_claude_bin()
    if not claude:
        return {"ok": False, "detail": "claude CLI not found"}
    cmd = [*_claude_auth_run_as_prefix(), claude, "-p", "reply with exactly: ok"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"claude -p timed out after {timeout}s"}
    except OSError as e:
        return {"ok": False, "detail": str(e)[:200]}
    text = (r.stdout or "").strip()
    if r.returncode == 0 and "ok" in text.lower():
        return {"ok": True, "detail": "claude -p replied"}
    detail = (text or (r.stderr or "").strip() or f"exit {r.returncode}")
    return {"ok": False, "detail": _claude_auth_redact(detail)}


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


def claude_auth_start(force=False):
    """Start (or return the in-flight) login attempt. Returns the authorize URL.

    Re-clicking Start while an attempt is waiting for its code hands back the
    SAME attempt: restarting would mint new PKCE state and silently invalidate
    the code the user is about to paste.
    """
    with _claude_auth_lock:
        cur = dict(_claude_auth_state)
    fresh_enough = (
        cur.get("state") == "awaiting_code"
        and cur.get("url")
        and time.time() - (cur.get("started_at") or 0) < _CLAUDE_AUTH_ATTEMPT_TTL_S
    )
    if fresh_enough and not force and _claude_auth_session_alive():
        return {"ok": True, "attempt_id": cur["attempt_id"], "url": cur["url"],
                "state": "awaiting_code", "reused": True}

    if not _claude_auth_tmux_bin():
        return {"ok": False, "error": "tmux_missing",
                "detail": "tmux is required on this node for the login prompt "
                          "to receive the code (install tmux)"}
    claude = _claude_auth_claude_bin()
    if not claude:
        return {"ok": False, "error": "claude_unavailable",
                "detail": "Claude Code CLI not found on this node"}

    _claude_auth_kill_session()
    attempt_id = uuid.uuid4().hex[:12]
    _claude_auth_set(state="starting", attempt_id=attempt_id, url=None,
                     started_at=time.time(), finished_at=None, error=None,
                     logged_in=None, email=None)
    cmd = [*_claude_auth_run_as_prefix(), "env", "BROWSER=true", claude, "auth", "login"]
    # Keep the pane around after the CLI exits so a failure can be read.
    shell_cmd = shlex.join(cmd) + "; sleep 120"
    try:
        r = _claude_auth_tmux("new-session", "-d", "-s", CLAUDE_AUTH_TMUX_SESSION,
                              "-x", "250", "-y", "50", shell_cmd)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        _claude_auth_set(state="failed", error=f"tmux failed: {e}"[:200], finished_at=time.time())
        return {"ok": False, "error": "tmux_failed", "detail": str(e)[:200]}
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "tmux new-session failed").strip()[:200]
        _claude_auth_set(state="failed", error=detail, finished_at=time.time())
        return {"ok": False, "error": "tmux_failed", "detail": detail}

    deadline = time.time() + _CLAUDE_AUTH_URL_WAIT_S
    pane = ""
    while time.time() < deadline:
        pane = _claude_auth_capture()
        m = _CLAUDE_AUTH_URL_RE.search(pane)
        if m:
            url = m.group(0)
            _claude_auth_set(state="awaiting_code", url=url)
            return {"ok": True, "attempt_id": attempt_id, "url": url,
                    "state": "awaiting_code", "reused": False}
        if not _claude_auth_session_alive():
            break
        time.sleep(0.4)
    detail = _claude_auth_redact(pane) or "claude auth login printed no sign-in URL"
    _claude_auth_kill_session()
    _claude_auth_set(state="failed", error=detail, finished_at=time.time())
    return {"ok": False, "error": "no_login_url", "detail": detail}


def claude_auth_submit(attempt_id, code, smoke=True):
    """Paste the one-time code into the waiting login and verify the result."""
    code = (code or "").strip()
    with _claude_auth_lock:
        cur = dict(_claude_auth_state)
    if not attempt_id or attempt_id != cur.get("attempt_id") or cur.get("state") != "awaiting_code":
        return {"ok": False, "error": "stale_attempt",
                "detail": "That sign-in attempt is no longer waiting for a code. "
                          "Start again and use the code from the new page."}
    if not _claude_auth_session_alive():
        _claude_auth_set(state="failed", error="login prompt exited", finished_at=time.time())
        return {"ok": False, "error": "stale_attempt",
                "detail": "The login prompt exited before the code arrived. Start again."}
    if not _CLAUDE_AUTH_CODE_RE.match(code):
        # Stay in awaiting_code: a mistyped paste should not burn the attempt.
        return {"ok": False, "error": "bad_code",
                "detail": "That does not look like a sign-in code (expected <code>#<state>)."}

    _claude_auth_set(state="verifying")
    try:
        # stdin, not argv: the code must not show up in `ps` or any log.
        r = _claude_auth_tmux("load-buffer", "-b", _CLAUDE_AUTH_TMUX_BUFFER, "-", input_text=code)
        if r.returncode == 0:
            r = _claude_auth_tmux("paste-buffer", "-d", "-b", _CLAUDE_AUTH_TMUX_BUFFER,
                                  "-t", CLAUDE_AUTH_TMUX_SESSION)
        if r.returncode == 0:
            r = _claude_auth_tmux("send-keys", "-t", CLAUDE_AUTH_TMUX_SESSION, "Enter")
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        r = None
        err = str(e)[:200]
    else:
        err = (r.stderr or "").strip()[:200] if r.returncode != 0 else ""
    if r is None or r.returncode != 0:
        _claude_auth_set(state="failed", error=f"could not send code: {err}", finished_at=time.time())
        return {"ok": False, "error": "send_failed", "detail": err}

    deadline = time.time() + _CLAUDE_AUTH_SUBMIT_WAIT_S
    pane = ""
    pane_error = None
    while time.time() < deadline:
        pane = _claude_auth_capture()
        after = pane.split("Paste code here", 1)[-1].replace(code, "")
        if _CLAUDE_AUTH_SUCCESS_RE.search(after):
            break
        if _CLAUDE_AUTH_ERROR_RE.search(after):
            pane_error = _claude_auth_redact(after, secret=code)
            break
        if not _claude_auth_session_alive():
            break
        time.sleep(0.5)

    status = claude_auth_status()
    _claude_auth_kill_session()
    if not status.get("ok") or not status.get("logged_in"):
        detail = pane_error or status.get("detail") or "claude auth status still reports logged out"
        _claude_auth_set(state="failed", error=detail, finished_at=time.time(),
                         logged_in=False)
        return {"ok": False, "error": "login_failed", "detail": detail, "status": status}

    smoke_result = claude_auth_smoke() if smoke else None
    _claude_auth_set(state="succeeded", error=None, finished_at=time.time(),
                     logged_in=True, email=status.get("email"))
    try:
        _core._log_activity("CLAUDE_AUTH", "reauth",
                            f"logged_in=True smoke={'-' if smoke_result is None else smoke_result.get('ok')}")
    except Exception:
        pass
    return {"ok": True, "logged_in": True, "email": status.get("email"),
            "auth_method": status.get("auth_method"), "smoke": smoke_result}


def claude_auth_cancel():
    _claude_auth_kill_session()
    return {"ok": True, **_claude_auth_set(state="idle", attempt_id=None, url=None,
                                           error=None, finished_at=time.time())}


def claude_auth_overview():
    """Node-level card: login state, the in-flight attempt, tmux availability."""
    st = claude_auth_public_state()
    return {
        "ok": True,
        "node_id": federation.node_id(),
        "status": claude_auth_status(),
        "attempt": {k: st.get(k) for k in ("state", "attempt_id", "url", "error", "started_at", "finished_at")},
        "tmux": bool(_claude_auth_tmux_bin()),
        "run_as": (os.environ.get("CCC_CLAUDE_AUTH_USER") or "").strip() or None,
    }


CLAUDE_AUTH_NUDGE_TEXT = (
    "Claude Code was re-authenticated on this machine (CCC Re-authenticate). "
    "The earlier \"Failed to authenticate\" error is resolved: retry the step "
    "that failed. If you were working a WatchTower ticket, re-run `wt claim` "
    "for it (or continue it) instead of starting over."
)


def claude_auth_nudge(session_ids, inject_fn):
    """Tell each stuck session to retry. ``inject_fn(sid, text) -> dict``."""
    results = []
    seen = set()
    for sid in session_ids or []:
        sid = str(sid or "").strip()
        if not sid or sid in seen or not re.fullmatch(r"[A-Za-z0-9._:-]{4,128}", sid):
            continue
        seen.add(sid)
        if len(seen) > 50:
            break
        try:
            res = inject_fn(sid, CLAUDE_AUTH_NUDGE_TEXT) or {}
        except Exception as e:  # one bad session must not stop the rest
            res = {"ok": False, "error": str(e)[:200]}
        results.append({"session_id": sid, "ok": bool(res.get("ok", True)) and not res.get("error"),
                        "error": res.get("error")})
    return {"ok": True, "nudged": sum(1 for r in results if r["ok"]), "results": results}


# ---------------------------------------------------------------------------
# HTTP surface: /api/claude-auth/<sub>
# ---------------------------------------------------------------------------

# sub -> federation route action (see _FEDERATION_ROUTE_ACTIONS in fleet.py)
CLAUDE_AUTH_ROUTE_ACTIONS = {
    "status": "claude_auth_status",
    "start": "claude_auth_start",
    "submit": "claude_auth_submit",
    "cancel": "claude_auth_cancel",
    "nudge": "claude_auth_nudge",
}


def _claude_auth_local_inject(sid, text):
    return _core._federation_self_api("POST", "/api/inject-input", body={
        "session_id": sid,
        "text": text,
        "announced_from": "CCC re-authenticate",
    }, timeout=30.0)


def claude_auth_handle(sub, data):
    """Dispatch one /api/claude-auth/<sub> call. Returns (payload, status).

    ``node_id`` naming a paired peer proxies the call to that peer's own
    loopback over the federation route envelope; the peer then runs the flow
    as its own user. ``via_route`` is stamped by the route executor so a
    peer honours a request from a node that has the preview enabled even
    when the peer's own Settings toggle is off (the pairing secret, not the
    UI flag, is what authorises a routed call).
    """
    if not isinstance(data, dict):
        data = {}
    if sub not in CLAUDE_AUTH_ROUTE_ACTIONS:
        return {"ok": False, "error": "not_found"}, 404
    if not data.get("via_route") and not _core._feature_flag(CLAUDE_AUTH_FLAG):
        return {"ok": False, "error": "feature_disabled",
                "detail": "Turn on \"Claude re-authenticate\" in Settings > Experimental"}, 403
    node = str(data.get("node_id") or "").strip()
    if node and node != federation.node_id():
        args = {k: v for k, v in data.items() if k not in ("node_id", "via_route")}
        timeout = 240.0 if sub == "submit" else 90.0
        result = _core._federation_proxy_session_action(
            node, CLAUDE_AUTH_ROUTE_ACTIONS[sub], args, timeout=timeout)
        return result, 200
    if sub == "status":
        return claude_auth_overview(), 200
    if sub == "start":
        return claude_auth_start(force=bool(data.get("force"))), 200
    if sub == "submit":
        result = claude_auth_submit(str(data.get("attempt_id") or ""),
                                    str(data.get("code") or ""),
                                    smoke=data.get("smoke", True) is not False)
        return result, 200
    if sub == "cancel":
        return claude_auth_cancel(), 200
    ids = data.get("session_ids")
    if not isinstance(ids, list):
        return {"ok": False, "error": "bad_request", "detail": "session_ids must be a list"}, 400
    return claude_auth_nudge(ids, _claude_auth_local_inject), 200
