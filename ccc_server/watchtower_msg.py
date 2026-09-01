"""Extracted from server.py (originally lines 44790-48240).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from control_plane import ControlPlaneClient, socket_path, worker_pid_path
from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import ccc_peer_inbound
import ccc_peer_uds
import fcntl
import hashlib
import json
import math
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Stage 2 of the WatchTower messaging handover (docs/messaging-design.md in
# the watchtower repo, "CCC as client (staged, behind flags)"). Stage 1
# (CCC_CHAT_ORCHESTRATOR=wt, see _start_coordination_watcher) delegated
# group-chat auto-nudging. Stage 2 is narrower: for a session that CCC would
# already deliver to *headlessly* (dormant claude, no TTY, no CCC-owned FIFO
# spawn to reuse), optionally hand delivery to `wt send` / `wt ask` instead of
# CCC spawning its own `claude --resume`. Both sides of the swap are
# headless-only, so this never touches the live-TTY keystroke path, the
# bg-agent PTY socket path, FIFO spawns CCC already owns, or any non-claude
# engine — those keep their native CCC transport unconditionally. Default-on
# since 2026-08-29 (slice 3 landed; every failure mode still falls through
# to the pre-existing native path unchanged). Set CCC_MESSAGING_BACKEND to
# any value that does not contain "uds" (e.g. "legacy") to opt back out.
CCC_MESSAGING_BACKEND_ENV = "CCC_MESSAGING_BACKEND"


def _wt_messaging_enabled():
    """True when CCC_MESSAGING_BACKEND=wt opts into stage-2 delegation."""
    return os.environ.get(CCC_MESSAGING_BACKEND_ENV, "").strip().lower() == "wt"


def _uds_messaging_enabled():
    """True unless CCC_MESSAGING_BACKEND is explicitly set to something
    that does not list "uds" (e.g. "legacy" or "wt").

    Default-on: unset (the common case) enables it. Every failure mode
    (unresolvable target, held/unknown receipt, socket error) still falls
    through to the pre-existing native transports unchanged, and every
    attempt is logged to the activity log (category "inject", verb
    "UDS"/"UDS-SKIP"/"UDS-FAIL") regardless of outcome.
    """
    raw = os.environ.get(CCC_MESSAGING_BACKEND_ENV, "")
    if not raw.strip():
        return True
    return "uds" in {p.strip().lower() for p in raw.split(",") if p.strip()}


# Only agent-to-agent relays ride the peer socket. Anything a human typed in
# the dashboard keeps the legacy transports so it still lands as USER intent
# (peer-tagged text is "input, not authority" under Claude's own rules).
_UDS_ELIGIBLE_SOURCES = (
    "ask",
    "group-chat-coordinate",
    "group-chat-auto-nudge",
    "group-chat-manual-nudge",
    # "report_to" itself never reaches here as a literal source: a report-back
    # footer arrives at /api/inject-input with an announced_from field, so it
    # is classified as "announced_from" by _inject_source_for_request below.
    "announced_from",
    "wt",
    # group-chat-add-participant is intentionally absent: adding a
    # participant is a human action taken in the dashboard UI, not an
    # agent-to-agent relay.
)


def _uds_source_eligible(source):
    return str(source or "").strip() in _UDS_ELIGIBLE_SOURCES


def _inject_source_for_request(announced_from, wt_origin):
    """Classify an /api/inject-input request into an inject `source` value.

    announced_from wins: a report-back footer or an explicit announce both
    carry this field, and it's the more specific signal. wt_origin covers
    WatchTower-originated delivery. Anything else is a plain "api" call
    (dashboard UI, curl, etc.) and stays on the legacy transports.
    """
    if announced_from:
        return "announced_from"
    if wt_origin:
        return "wt"
    return "api"


def _find_wt_cli():
    """Uncached resolution of the `wt` binary's absolute path, or "" if not
    found. Most callers want `_wt_cli_path()` (cached); use this directly
    only when a fresh lookup matters (e.g. probing right after an install).

    `shutil.which("wt")` alone misses the common case: `pip install --user`
    (what CCC's own installer uses, see scripts/install-watchtower.sh) puts
    the console-script in the interpreter's user scripts dir, which is
    routinely off PATH — and launchd's PATH (what the CCC dashboard process
    itself runs under) essentially never includes it. Without this fallback,
    a machine where WatchTower installed cleanly still shows "WatchTower is
    not installed" in the dashboard, `wt` start/restart fails, and every
    queue affordance silently disables itself."""
    found = _core.shutil.which("wt") or ""
    if not found:
        try:
            scheme = "posix_user" if os.name == "posix" else "nt_user"
            candidate = os.path.join(sysconfig.get_path("scripts", scheme=scheme), "wt")
            if os.access(candidate, os.X_OK):
                found = candidate
        except Exception:
            pass
    return found


def _wt_cli_path():
    """Cached wrapper around `_find_wt_cli` — a single stat-ish lookup per
    process rather than a PATH search on every inject/ask call."""
    if _core._WT_CLI_PATH_CACHE is None:
        _core._WT_CLI_PATH_CACHE = _find_wt_cli()
    return _core._WT_CLI_PATH_CACHE


def _wt_cli_available():
    """Cached check for whether the `wt` CLI is usable — see `_wt_cli_path`."""
    return bool(_core._wt_cli_path())


# --- Plan-to-fleet: document -> Watchtower queue (W51) ------------------------
# CCC never hard-depends on Watchtower. The doc-import affordance shells to
# `wt import` (W43 `feat/doc-to-queue`) and is feature-flagged off whenever the
# installed `wt` predates the `import` subcommand or is absent entirely.
_IMPORT_DOC_EXTENSIONS = {".md", ".markdown", ".mdx", ".txt", ".text"}
_IMPORT_QUEUE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WT_IMPORT_TIMEOUT = 180  # one reasoning-model call; preview and apply each run once


def _wt_import_available():
    """True when the installed `wt` supports `wt import` (the W43 doc-to-queue
    engine). Cached: exactly one `wt import --help` subprocess per process.

    When False, the UI hides the import-doc affordance and the POST route
    refuses cleanly — CCC treats Watchtower as an optional tool, so a missing
    or older `wt` must degrade, never error the dashboard."""
    if _core._WT_IMPORT_AVAILABLE_CACHE is None:
        _core._WT_IMPORT_AVAILABLE_CACHE = False
        if _core._wt_cli_available():
            try:
                proc = _core.subprocess.run(
                    ["wt", "import", "--help"],
                    capture_output=True, text=True, timeout=10,
                )
                _core._WT_IMPORT_AVAILABLE_CACHE = proc.returncode == 0
            except (OSError, _core.subprocess.TimeoutExpired, ValueError):
                _core._WT_IMPORT_AVAILABLE_CACHE = False
    return _core._WT_IMPORT_AVAILABLE_CACHE


def _reset_wt_capability_caches():
    """Forget everything this process believes about the installed WatchTower.

    Both memos above are deliberately process-lifetime: `wt` does not appear
    or grow subcommands while CCC runs. `_self_update()` is the one moment
    that assumption breaks — it can install WatchTower where there was none,
    or upgrade one that predates `wt import`. Registered here, next to the
    caches, so a future probe cache is cleared by the same call."""
    _core._WT_CLI_PATH_CACHE = None
    _core._WT_IMPORT_AVAILABLE_CACHE = None


def _resolve_import_doc_path(raw):
    """Clamp an import-doc request to a real, user-reachable text/markdown file.

    Mirrors `_safe_local_file_open_path` (the Files-panel clamp): resolve
    symlinks strictly, require an existing regular file, and require a plain
    text/markdown extension. Never a script or binary. Repo-containment is not
    required because plan docs commonly live outside any repo (a Desktop brief).
    Returns the resolved `Path`, or None if the input is not an acceptable file.
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        target = Path(raw).expanduser().resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return None
    if not target.is_file():
        return None
    if target.suffix.lower() not in _IMPORT_DOC_EXTENSIONS:
        return None
    return target


def _parse_wt_import_output(text):
    """Parse `wt import` line-oriented stdout into (tickets, counts).

    Recognised lines (W43 contract):
      WOULD FILE: [feature] Short title (L12-L24)   -> status=new
      FILE: [feature] Short title (L12-L24)         -> status=new (during apply)
      EXISTS: [bug] Short title (L5)                -> status=exists
      FILED: WT-123  Short title                    -> status=filed (apply)
      IMPORT dry-run: candidates=N new=N existing=N; ...
      IMPORT applied: candidates=N created=N existing=N

    W43 shipped the anchor and the dependency edge as indented continuation
    lines under each verb line rather than inline in parentheses, so both
    shapes are accepted:
      WOULD FILE: [feature] Short title
        source: /abs/plan.md#L8-L12                 -> source_ref
        depends_on: Some earlier title              -> depends_on ("none" dropped)
    Bodies are intentionally not in the dry-run output, so a preview ticket
    carries only status, type, title, source anchor, and dependency."""
    tickets = []
    counts = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Continuation lines belong to the verb line above them. Anchors are
        # required to look like a line reference so a body sentence starting
        # with "source:" cannot be mistaken for one.
        if tickets:
            sc = re.match(r"^source:\s*(\S+#L\d[\w-]*)$", line)
            if sc:
                if not tickets[-1].get("source_ref"):
                    tickets[-1]["source_ref"] = sc.group(1)
                continue
            dc = re.match(r"^depends_on:\s*(.+)$", line)
            if dc and "depends_on" not in tickets[-1]:
                dep = dc.group(1).strip()
                if dep.lower() != "none":
                    tickets[-1]["depends_on"] = dep
                continue
        m = re.match(r"^(WOULD FILE|FILE|EXISTS|FILED):\s*(.*)$", line)
        if m:
            verb, rest = m.group(1), m.group(2)
            if verb == "FILED":
                parts = rest.split(None, 1)
                tickets.append({
                    "status": "filed",
                    "ref": parts[0] if parts else "",
                    "type": "",
                    "title": parts[1].strip() if len(parts) > 1 else "",
                    "source_ref": "",
                })
                continue
            status = "new" if verb in ("WOULD FILE", "FILE") else "exists"
            kind = ""
            title = rest
            km = re.match(r"^\[([^\]]*)\]\s*(.*)$", rest)
            if km:
                kind = km.group(1).strip()
                title = km.group(2)
            source_ref = ""
            sm = re.search(r"\s*\(([^()]*)\)\s*$", title)
            if sm:
                source_ref = sm.group(1).strip()
                title = title[:sm.start()].rstrip()
            tickets.append({
                "status": status,
                "type": kind,
                "title": title.strip(),
                "source_ref": source_ref,
            })
            continue
        cm = re.match(r"^IMPORT (?:dry-run|applied):\s*(.*)$", line)
        if cm:
            for key, val in re.findall(r"(\w+)=(\d+)", cm.group(1)):
                counts[key] = int(val)
    return tickets, counts


def _run_wt_import(doc_path, queue, *, apply=False, item_type=None):
    """Shell to `wt import` (dry-run by default) and return a CCC-shaped result.

    argv list only (never a shell string), so path/queue can't inject shell.
    Mirrors `_try_wt_send_for_headless_delivery`'s subprocess posture: bounded
    timeout, catch the OS/timeout family, degrade to a clear error dict."""
    cmd = ["wt", "import", str(doc_path), "-q", queue]
    if apply:
        cmd.append("--apply")
    if item_type in ("bug", "feature"):
        cmd += ["--type", item_type]
    try:
        proc = _core.subprocess.run(
            cmd, capture_output=True, text=True, timeout=_WT_IMPORT_TIMEOUT,
        )
    except _core.subprocess.TimeoutExpired:
        return {"ok": False, "available": True,
                "error": f"wt import timed out after {_WT_IMPORT_TIMEOUT}s"}
    except (OSError, ValueError) as e:
        return {"ok": False, "available": True, "error": f"wt import failed: {e}"}
    stdout = proc.stdout or ""
    if proc.returncode != 0:
        detail = (proc.stderr or stdout or "").strip().splitlines()
        return {
            "ok": False,
            "available": True,
            "error": detail[-1] if detail else f"wt import exited {proc.returncode}",
            "stdout_tail": "\n".join(stdout.strip().splitlines()[-20:]),
        }
    tickets, counts = _core._parse_wt_import_output(stdout)
    return {
        "ok": True,
        "available": True,
        "applied": bool(apply),
        "queue": queue,
        "tickets": tickets,
        "counts": counts,
        "stdout_tail": "\n".join(stdout.strip().splitlines()[-40:]),
    }


_CCC_PEER_STATE = {"sock": None, "token": "", "socket_path": "", "row_path": None, "key_path": None}
_CCC_PEER_LOCK = threading.Lock()
_CCC_ASK_CORRELATION = {}  # msg_id -> {"sender_sid": str|None, "target_sid": str|None, "created": float, "queue": queue.Queue}
_CCC_ASK_CORRELATION_LOCK = threading.Lock()
_CCC_ASK_CORRELATION_TTL_S = 600.0


def _ccc_peer_socket_dir():
    return Path("/tmp/cc-socks")


def _ccc_peer_server_start():
    """Bind CCC's own Claude-peer socket and publish its registry row.

    Dashboard-process-only (see main()): the worker never calls this, so
    exactly one process owns the listener, mirroring the has_tty split that
    already ensures exactly one process sends outbound UDS frames.

    Fails closed on registry-shape drift: if every locally observed real
    Claude registry row is missing a key CCC's own row (and the rest of this
    codebase) depends on, this refuses to publish and returns ok=False --
    CCC keeps running normally, it just isn't a dialable peer. A machine
    with no live Claude session to cross-check against is NOT a failure
    (see ccc_peer_uds.validate_registry_row_shape); it publishes unverified
    and logs that.
    """
    if not _core._uds_messaging_enabled():
        return {"ok": False, "reason": "gate_off"}
    with _CCC_PEER_LOCK:
        if _core._CCC_PEER_STATE["sock"] is not None:
            return {"ok": True, "reason": "already_running"}
        sock_dir = _core._ccc_peer_socket_dir()
        try:
            sock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            _core._log_activity("peer", "CCC-PEER-FAIL", f"could not create {sock_dir}: {e}")
            return {"ok": False, "reason": f"mkdir_failed: {e}"}
        pid = os.getpid()
        socket_path = str(sock_dir / f"{pid}.sock")
        try:
            if os.path.exists(socket_path):
                os.unlink(socket_path)
        except OSError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(socket_path)
            os.chmod(socket_path, 0o600)
            sock.listen(16)
        except OSError as e:
            _core._log_activity("peer", "CCC-PEER-FAIL", f"bind {socket_path} failed: {e}")
            try:
                sock.close()
            except OSError:
                pass
            return {"ok": False, "reason": f"bind_failed: {e}"}
        row = ccc_peer_uds.build_ccc_registry_row(pid, socket_path, str(_core.CCC_ROOT))
        # Cross-check against real rows already on disk (Claude sessions this
        # same user has running, if any) before publishing.
        real_rows = []
        try:
            for f in _core.SESSIONS_REGISTRY.iterdir():
                if f.name.endswith(".json") and f.is_file():
                    try:
                        real_rows.append(json.loads(f.read_text()))
                    except (OSError, json.JSONDecodeError):
                        pass
        except OSError:
            pass
        check = ccc_peer_uds.validate_registry_row_shape(row, real_rows)
        if not check["ok"]:
            _core._log_activity(
                "peer", "CCC-PEER-SHAPE-DRIFT",
                f"refusing to publish CCC's peer registry row: {check['reason']}",
            )
            sock.close()
            try:
                os.unlink(socket_path)
            except OSError:
                pass
            return {"ok": False, "reason": f"shape_drift: {check['reason']}"}
        if check["reason"] == "no_reference_rows":
            _core._log_activity(
                "peer", "CCC-PEER-UNVERIFIED",
                "no live Claude session on this machine to cross-check registry shape; publishing unverified",
            )
        token = str(uuid.uuid4())
        try:
            _core.SESSIONS_REGISTRY.mkdir(parents=True, exist_ok=True)
            row_path = _core.SESSIONS_REGISTRY / f"{pid}.json"
            row_path.write_text(json.dumps(row))
            key_path = ccc_peer_uds.key_path_for(_core.SESSIONS_REGISTRY, pid, socket_path)
            key_path.write_text(json.dumps(ccc_peer_uds.ccc_key_payload(token)))
            os.chmod(key_path, 0o600)
        except OSError as e:
            _core._log_activity("peer", "CCC-PEER-FAIL", f"could not publish registry row/key: {e}")
            sock.close()
            try:
                os.unlink(socket_path)
            except OSError:
                pass
            return {"ok": False, "reason": f"publish_failed: {e}"}
        _core._CCC_PEER_STATE.update(
            sock=sock, token=token, socket_path=socket_path,
            row_path=row_path, key_path=key_path,
        )
        threading.Thread(
            target=_ccc_peer_accept_loop, args=(sock,), daemon=True, name="ccc-peer-accept",
        ).start()
        _core._log_activity("peer", "CCC-PEER-START", f"socket={socket_path}")
        return {"ok": True, "socket_path": socket_path}


def _ccc_peer_server_stop():
    """Unbind and un-publish, mirroring _unregister_self()'s cleanup shape."""
    with _CCC_PEER_LOCK:
        sock = _core._CCC_PEER_STATE["sock"]
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass
        for p in (_core._CCC_PEER_STATE["socket_path"], _core._CCC_PEER_STATE["row_path"], _core._CCC_PEER_STATE["key_path"]):
            if not p:
                continue
            try:
                os.unlink(p)
            except OSError:
                pass
        _core._CCC_PEER_STATE.update(sock=None, token="", socket_path="", row_path=None, key_path=None)


def _ccc_ask_correlation_register(msg_id, sender_sid=None, target_sid=None):
    """Record that CCC sent an ask (msg_id) to target_sid on behalf of
    sender_sid, so a later inbound reply naming this msg_id (or, failing
    that, coming FROM target_sid with exactly one ask outstanding) can be
    resolved back to the original asker. See _try_uds_peer_delivery for the
    write side and _ccc_peer_handle_connection for the read side."""
    q = queue.Queue(maxsize=1)
    with _core._CCC_ASK_CORRELATION_LOCK:
        now = time.time()
        # Opportunistic sweep of stale entries every registration -- bounds
        # growth without a dedicated timer thread.
        for k in [
            k for k, v in _core._CCC_ASK_CORRELATION.items()
            if now - v["created"] > _CCC_ASK_CORRELATION_TTL_S
        ]:
            _core._CCC_ASK_CORRELATION.pop(k, None)
        _core._CCC_ASK_CORRELATION[msg_id] = {
            "sender_sid": sender_sid, "target_sid": target_sid, "created": now, "queue": q,
        }
    return q


def _ccc_ask_correlation_resolve(orig_msg_id, from_sid, reply_text):
    """Best-effort match of an inbound reply to a pending ask. Primary key is
    the orig_msg_id header CCC's own ask wrapper asks the replier to echo;
    fallback is "the oldest still-outstanding ask whose target session is
    from_sid" when no orig_msg_id was given and exactly one ask is
    outstanding to that target. Returns True if a wait was resolved."""
    entry = None
    with _core._CCC_ASK_CORRELATION_LOCK:
        if orig_msg_id:
            entry = _core._CCC_ASK_CORRELATION.pop(orig_msg_id, None)
        if entry is None and from_sid:
            candidates = [
                (k, v) for k, v in _core._CCC_ASK_CORRELATION.items()
                if v.get("target_sid") == from_sid
            ]
            if len(candidates) == 1:
                k, entry = candidates[0]
                _core._CCC_ASK_CORRELATION.pop(k, None)
    if entry is None:
        return False
    try:
        entry["queue"].put_nowait(reply_text)
    except queue.Full:
        pass
    return True


def _ccc_peer_route_report(envelope, from_addr):
    """A report_to report-back that arrived over SendMessage instead of
    curl. Same effect as the curl footer's POST to /api/inject-input: one
    inject into the dispatching session, through the same gated function
    (circuit breaker, activity log) that endpoint already uses -- this is
    the boundary that keeps inbound-frame authority equal to, not wider
    than, CCC's existing loopback trust model (see _check_same_origin)."""
    sid = str(envelope.get("session_id") or "").strip()
    text = str(envelope.get("text") or "")
    if not sid or not text:
        return
    mode = str(envelope.get("mode") or "steer")
    announced_from = str(envelope.get("announced_from") or from_addr or "")
    _core._inject_text_into_session(
        sid, text, mode=mode, source="announced_from", announced_from=announced_from,
    )


def _ccc_sid_for_socket_addr(from_addr):
    """Reverse-lookup a wrapper `from` address ("uds:/tmp/cc-socks/<pid>.sock")
    back to the session id that registry row belongs to. Returns "" if the
    address doesn't match any currently live row."""
    from_addr = str(from_addr or "")
    prefix = "uds:"
    if not from_addr.startswith(prefix):
        return ""
    sock_path = from_addr[len(prefix):]
    try:
        registry = _core._load_session_registry()
    except Exception:
        return ""
    for sid, meta in registry.items():
        if str(meta.get("messagingSocketPath") or "") == sock_path:
            return sid
    return ""


def _ccc_peer_handle_connection(conn):
    """Classify and route one inbound connection on CCC's own peer socket.

    Three buckets, checked in order, matching the spec: (1) a reply to an
    outstanding CCC-initiated ask (resolves the wait, no injection); (2) a
    report_to report-back JSON envelope (routes through
    _inject_text_into_session, same as the curl path); (3) otherwise, logged
    as unrouted -- CCC never guesses a destination for unaddressed text.
    """
    try:
        token = _core._CCC_PEER_STATE.get("token") or ""
        authed = not token  # if CCC somehow has no token, don't lock out every sender
        for frame in ccc_peer_inbound.read_frames(conn):
            ftype = frame.get("type")
            if ftype == "auth":
                authed = str(frame.get("token") or "") == token
                if not authed:
                    _core._log_activity("peer", "CCC-PEER-AUTH-FAIL", "inbound auth token mismatch")
                    return
                continue
            if ftype != "user":
                continue
            if not authed:
                _core._log_activity("peer", "CCC-PEER-AUTH-FAIL", "inbound frame before/without valid auth")
                return
            from_addr = str(frame.get("from") or "")
            content = ((frame.get("message") or {}).get("content") or "")
            body = ccc_peer_inbound.unwrap_message(content)
            orig_msg_id, remainder = ccc_peer_inbound.extract_orig_msg_id(body)
            from_sid = _ccc_sid_for_socket_addr(from_addr)
            if _ccc_ask_correlation_resolve(orig_msg_id, from_sid, remainder):
                _core._log_activity(
                    "peer", "CCC-PEER-ASK-REPLY",
                    f"from={from_addr} orig_msg_id={orig_msg_id or '-'}",
                )
                continue
            envelope = ccc_peer_inbound.parse_report_envelope(body)
            if envelope is not None:
                _ccc_peer_route_report(envelope, from_addr)
                _core._log_activity(
                    "peer", "CCC-PEER-REPORT",
                    f"from={from_addr} session_id={envelope.get('session_id')}",
                )
                continue
            _core._log_activity(
                "peer", "CCC-PEER-UNROUTED",
                f"from={from_addr} preview=\"{_core._activity_log_preview(body)}\"",
            )
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _ccc_peer_accept_loop(sock):
    while True:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return  # socket closed by _ccc_peer_server_stop
        threading.Thread(
            target=_ccc_peer_handle_connection, args=(conn,), daemon=True, name="ccc-peer-conn",
        ).start()


def _try_uds_peer_delivery(session_id, text, *, source, mode="send", peer_sender_sid=None):
    """Deliver agent-origin text over the target's Claude Code peer socket.

    Returns a CCC-shaped success dict ONLY when the post-send transcript
    scan confirms the frame is at least in the receiver's inbox: either
    "delivered" (msg_id echoed back) or "queued" (an enqueue row for this
    body landed, delivery pending the receiver's next tool boundary or turn
    end). Held, unknown, socket errors, ineligible sources, or a
    non-dialable target all return None so the caller falls through to the
    legacy transports unchanged.
    """
    if not _core._uds_messaging_enabled() or not _core._uds_source_eligible(source):
        return None
    # CCC-1000 Phase 4: a slash command cannot execute over UDS, in any framing.
    # Measured 2026-08-30 -- /compact was sent to a live session five times,
    # wrapped and raw, at priority now and next; every send transported cleanly
    # ({"ok": True}) and none executed. Claude's peer listener hands the frame's
    # message.content to the session as message *content* and never runs it
    # through the slash-command parser, so stripping the <cross-session-message>
    # wrapper changes nothing.
    #
    # Falling through to None routes the text to the FIFO path, where a leading
    # slash IS executed (see the /clear comment below). The alternative -- send
    # it anyway -- hands the caller a transcript-confirmed "delivered" receipt
    # for text that will sit inert in the target's context.
    #
    # /compact and /clear never reach here (intercepted above), so this guard
    # covers /model, /cost, /status, /resume, /code-review and custom skills.
    if _core._SLASH_COMMAND_TRIGGER_RE.match(str(text or "")):
        _core._log_activity(
            "inject", "UDS-SKIP",
            f"session={session_id} source={source} reason=slash_command_needs_fifo",
        )
        return None
    try:
        registry = _core._load_session_registry()
    except Exception:
        return None
    target = ccc_peer_uds.resolve_target(registry.get(session_id) or {})
    if not target["ok"]:
        _core._log_activity(
            "inject", "UDS-SKIP",
            f"session={session_id} source={source} reason={target.get('reason')}",
        )
        return None
    from_addr = ""
    from_name = ""
    from_mode = ""
    if peer_sender_sid:
        sender_row = registry.get(peer_sender_sid) or {}
        sender_sock = str(sender_row.get("messagingSocketPath") or "").strip()
        if sender_sock:
            from_addr = "uds:" + sender_sock
            from_name = str(sender_row.get("name") or "").strip()
        spawn = _core._find_live_spawn_entry_for_session(peer_sender_sid)
        if spawn is not None and (spawn.get("engine") or "claude") == "claude":
            # CCC spawns every headless Claude with
            # --dangerously-skip-permissions, so this attestation is true.
            from_mode = "bypass"
    if not from_addr and _core._CCC_PEER_STATE.get("socket_path"):
        # Sender has no Claude registry row of its own (a non-Claude engine
        # CCC is relaying for, or peer_sender_sid unresolved) -- point the
        # receiver back at CCC itself so it has SOMEWHERE to reply. This is
        # what makes CCC a dialable address instead of leaving from blank.
        from_addr = "uds:" + _core._CCC_PEER_STATE["socket_path"]
        from_name = from_name or "ccc"
    msg_id = str(uuid.uuid4())
    ask_text = text
    if source == "ask":
        _core._ccc_ask_correlation_register(msg_id, sender_sid=peer_sender_sid, target_sid=session_id)
        ask_text = (
            f"orig_msg_id: {msg_id}\n{text}\n\n"
            "(If you reply, please start your reply with the line above "
            "exactly as shown, so the reply routes back correctly.)"
        )
    content = ccc_peer_uds.wrap(ask_text, from_addr=from_addr, from_name=from_name, from_mode=from_mode)
    token = ccc_peer_uds.load_peer_token(_core.SESSIONS_REGISTRY, target["pid"], target["socket_path"])
    priority = "now" if str(mode or "") == "steer" else "next"
    try:
        lines = ccc_peer_uds.build_frame_lines(
            content, token=token, from_addr=from_addr, msg_id=msg_id, priority=priority,
        )
    except ValueError:
        return None
    try:
        start_offset = os.path.getsize(_core._resolve_conversation_path(session_id))
    except OSError:
        start_offset = 0
    sent = ccc_peer_uds.send_lines(target["socket_path"], lines)
    if not sent.get("ok"):
        _core._log_activity("inject", "UDS-FAIL", f"session={session_id} source={source} error={sent.get('error')}")
        return None
    receipt = _core._transcript_peer_receipt(session_id, msg_id, text, start_offset=start_offset)
    _core._log_activity(
        "inject", "UDS",
        f"session={session_id} source={source} msg_id={msg_id} receipt={receipt} "
        f"text=\"{_core._activity_log_preview(text)}\"",
    )
    if receipt not in ("delivered", "queued"):
        return None
    # "queued" means the frame is confirmed sitting in the receiver's inbox
    # (an enqueue row landed after we sent it) even though Claude has not
    # echoed our msg_id back yet. Claude delivers it at the next tool
    # boundary or turn end. Falling through here would duplicate it over a
    # legacy transport.
    return {"ok": True, "via": "uds", "source": "uds", "receipt_id": msg_id, "receipt": receipt}


def _try_wt_send_for_headless_delivery(session_id, text):
    """Stage-2 hook for `_inject_text_into_session`'s dormant-claude branch.

    Called ONLY at the exact point the router has already decided delivery
    would be `resume_session_headless` (session not live, or live with
    neither a tty nor a pid CCC can drive) — i.e. the one case where "run our
    own claude --resume" and "ask wt to deliver" are genuinely interchangeable.
    Returns a CCC-shaped success dict on `wt send` rc==0, or None to signal
    "fall through to the native resume_session_headless path unchanged"
    (flag off, wt missing, or wt itself failed/timed out).
    """
    if not _core._wt_messaging_enabled() or not _core._wt_cli_available():
        return None
    # --no-queue: a delivery wt can't complete NOW must fail (rc!=0) so we
    # fall through to the native resume path, which owns queueing/retry.
    # Without it wt parks the text in its outbox and exits 0 — CCC then
    # tells the UI "delivered" for a message that may never drain (the
    # 2026-07-02 silent text-loss incident: every dormant-session inject
    # died in wt's resume adapter while the UI showed "Waking up headless").
    # WATCHTOWER_DELEGATE_URL=off: CCC *is* wt's delegate — without this,
    # wt's delegate adapter POSTs back to /api/inject-input, which calls
    # `wt send` again, recursing until timeout.
    env = dict(os.environ)
    env["WATCHTOWER_DELEGATE_URL"] = "off"
    try:
        proc = _core.subprocess.run(
            ["wt", "send", session_id, text, "--no-queue", "--json"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, _core.subprocess.TimeoutExpired, ValueError):
        return None
    try:
        payload = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, TypeError):
        payload = None
    if proc.returncode != 0:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("queued"):
        result = {
            "ok": True,
            "queued": True,
            "source": "wt-send",
            "via": "wt-send-queued",
            "queued_reason": payload.get("error") or "WatchTower queued the message",
        }
        for key in ("id", "transport", "receipt_id", "log"):
            if payload.get(key):
                result[key] = str(payload[key])
        return result
    if not payload.get("ok"):
        return None
    result = {"ok": True, "source": "wt-send", "via": "wt-send", "resumed": True}
    # CCC-452: surface wt's delivery pipeline to the UI. `wt send --json`
    # reports the transport it used (fifo/tty/resume/codex/delegate) and, when
    # the transport records one, a receipt_id the client can poll via
    # /api/wt/receipt/<id> until the message is verified against the target
    # transcript (landed) or declared lost (native-resume fallback).
    for key in ("transport", "receipt_id", "log"):
        if payload.get(key):
            result[key] = str(payload[key])
    return result


def _map_wt_ask_json_to_ccc_result(payload):
    """Pure mapping: parsed `wt ask --json` output -> ask_session_and_wait's
    return shape. No subprocess here on purpose, so this is unit-testable on
    synthetic payloads (see tests/test_smoke.py). Returns None if `payload`
    isn't a recognizable wt-ask response, signalling the caller to fall
    through to the native path.

    wt-ask shapes (watchtower/messages.py `ask()`):
      {"ok": true, "answer": "...", "source": "fifo"|"resume"|"delegate"}
      {"ok": false, "error": "timeout", "partial": "...", "source": ...}
      {"ok": false, "error": "<message>", "source": ...}
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("ok"):
        return {
            "ok": True,
            "text": payload.get("answer") or "",
            "cost_usd": None,
            "duration_ms": None,
            "num_turns": None,
            "source": "wt-ask",
        }
    result = {
        "ok": False,
        "error": payload.get("error") or "wt ask failed",
        "source": "wt-ask",
    }
    if payload.get("partial"):
        result["partial"] = payload["partial"]
    return result


def _try_wt_ask_for_headless_delivery(session_id, text, timeout_ms):
    """Stage-2 hook for `ask_session_and_wait`'s dormant-claude branch (no
    live resumed subprocess to reuse — the same "would spawn a fresh
    claude --resume" moment `_try_wt_send_for_headless_delivery` guards on
    the fire-and-forget side). Runs `wt ask ... --json`, parses it through
    `_map_wt_ask_json_to_ccc_result`, and returns that mapped result whenever
    wt produced a real (parseable) answer — including a wt-mediated timeout,
    since falling through there would spawn a second, duplicate resume for
    the same question. Returns None (fall through to native) only when wt
    itself could not be run or produced no parseable JSON at all.
    """
    if not _core._wt_messaging_enabled() or not _core._wt_cli_available():
        return None
    timeout_s = max(1, math.ceil(timeout_ms / 1000.0))
    try:
        proc = _core.subprocess.run(
            ["wt", "ask", session_id, text, "--timeout", str(timeout_s), "--json"],
            capture_output=True, text=True, timeout=timeout_s + 15,
        )
    except (OSError, _core.subprocess.TimeoutExpired, ValueError):
        return None
    try:
        payload = json.loads(proc.stdout or "")
    except (json.JSONDecodeError, TypeError):
        return None
    return _core._map_wt_ask_json_to_ccc_result(payload)


# ── Inject circuit breaker ──────────────────────────────────────────────────
# Blast-radius cap under the CCC-863 opt-in fix. That incident burned a Codex
# weekly quota by injecting the literal "continue" into ONE live session 118
# times in 114 minutes (02:28-04:22 UTC, roughly one poke a minute, 54.8M
# cumulative tokens). Making unattended auto-resume opt-in closed that
# specific vector; this caps the next one, whoever sends it.
#
# Two properties, both easy to get wrong:
#
#   1. THE COUNTER IS A FILE, NOT PROCESS MEMORY. The incident's injector was
#      a different process (a 5-day-old orphan running pre-fix code), which is
#      why CCC's own activity.log holds 1 of those 118 pokes. An in-process
#      counter would have counted to 1 and waved the other 117 through. Every
#      process running this code shares one flock'd ledger.
#   2. A TRIP IS TERMINAL, NOT A DELIVERY FAILURE. The terminal-queue watcher
#      requeues `ok:false` at the FRONT of the queue and retries every tick,
#      so returning a plain failure would convert a rate limit into a hot
#      loop. Blocked results carry `blocked: True`; every requeue site drops
#      them into the held bucket instead of retrying them.
#
# What this does NOT cover: a process running code from before the gate
# existed -- precisely the CCC-863 orphan. Nothing retrofits a gate into an
# already-running interpreter; that failure mode belongs to the stray reaper
# above. Reaper kills stale code, breaker caps live code.
if "pytest" in sys.modules:
    # Per-process and truncated at import: the ledger is deliberately durable
    # across restarts in production, which in a test process would mean one
    # suite's injects metering the next suite's -- a slow-building, order-
    # dependent flake. Tests that care monkeypatch this to a tmp_path.
    INJECT_BUDGET_FILE = (
        Path(tempfile.gettempdir()) / f"ccc-test-inject-budget-{os.getpid()}.json"
    )
    for _stale in (INJECT_BUDGET_FILE, INJECT_BUDGET_FILE.with_suffix(".lock")):
        try:
            _stale.unlink()
        except OSError:
            pass
else:
    INJECT_BUDGET_FILE = _core.COMMAND_CENTER_STATE_DIR / "inject-budget.json"

_INJECT_BUDGET_WINDOW_S = 3600
_INJECT_BUDGET_DAY_S = 86400
# Identical text to one session, ANY source. Twelve byte-identical messages in
# an hour is not a human changing their mind, it is something in a loop.
_INJECT_REPEAT_LIMIT = 12
# Unattended pokes only: nobody is watching these land, so tighter leash.
_INJECT_UNATTENDED_HOURLY_LIMIT = 6
_INJECT_UNATTENDED_DAILY_LIMIT = 40
_INJECT_BUDGET_MAX_EVENTS = 500
_INJECT_HELD_MAX = 50
_INJECT_BUDGET_LOCK_ATTEMPTS = 50
_INJECT_BUDGET_LOCK_DELAY_S = 0.01

# Sources with no human waiting on the result. `terminal-queue-watcher` is
# deliberately absent: it delivers text a human typed and queued, so ten of
# those in a minute is a person working, not a runaway. Those still get the
# identical-text cap above, which is what would have caught the incident.
_INJECT_UNATTENDED_SOURCES = frozenset({
    "usage-limit-watcher",
    "group-chat-auto-nudge",
    "fleet-ping",
    "fleet-step",
    "archive-bulk",
})

_inject_blocked_memo = {"mtime": None, "value": []}


def _inject_budget_text_key(text):
    """Stable short hash of normalised text.

    Hashed rather than stored raw: the ledger is bookkeeping, not a second
    copy of every message anyone sends. The held bucket keeps a short preview
    (same rule activity.log already follows) so a human can see what stopped.
    """
    norm = " ".join(str(text or "").split()).lower()
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()[:16]


def _inject_budget_acquire(lock_fh):
    """Bounded non-blocking flock. False when someone else holds it too long.

    Bounded on purpose: a wedged holder must not be able to stall every inject
    in the fleet. Giving up here means failing OPEN (see _inject_budget_check).
    """
    for _ in range(_INJECT_BUDGET_LOCK_ATTEMPTS):
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            time.sleep(_INJECT_BUDGET_LOCK_DELAY_S)
    return False


def _inject_budget_read():
    """Ledger contents, or {} for missing/corrupt. Never raises."""
    try:
        with open(_core.INJECT_BUDGET_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _inject_budget_write(data):
    """Atomically replace the ledger. False on any OS error (fail open)."""
    try:
        _core.INJECT_BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _core.INJECT_BUDGET_FILE.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.replace(_core.INJECT_BUDGET_FILE)
        return True
    except OSError:
        return False


def _inject_budget_check(session_id, text, source, now=None):
    """Record one inject attempt; return None to proceed, or a blocked result.

    Fails OPEN on a missing, corrupt, locked, or unwritable ledger: a broken
    counter must never wedge every message in the fleet. Only a real trip
    refuses, and it self-heals as the rolling window rolls off.

    Blocked attempts are recorded too, so an injector that keeps hammering
    keeps its own window full and stays blocked until it actually stops.
    """
    now_ts = time.time() if now is None else float(now)
    sid = str(session_id or "")
    if not sid:
        return None
    lock_path = _core.INJECT_BUDGET_FILE.with_suffix(".lock")
    try:
        _core.INJECT_BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "a+")
    except OSError:
        return None
    try:
        if not _inject_budget_acquire(lock_fh):
            return None
        data = _inject_budget_read()
        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
            data["sessions"] = sessions
        entry = sessions.get(sid)
        if not isinstance(entry, dict):
            entry = {}
            sessions[sid] = entry
        raw_events = entry.get("events")
        events = [
            e for e in (raw_events if isinstance(raw_events, list) else [])
            if isinstance(e, list) and len(e) == 3 and now_ts - e[0] <= _INJECT_BUDGET_DAY_S
        ]
        key = _core._inject_budget_text_key(text)
        unattended = str(source or "") in _core._INJECT_UNATTENDED_SOURCES
        hour = [e for e in events if now_ts - e[0] <= _core._INJECT_BUDGET_WINDOW_S]
        repeats = sum(1 for e in hour if e[1] == key)
        unattended_hour = sum(1 for e in hour if e[2])
        unattended_day = sum(1 for e in events if e[2])

        trip = None
        if repeats >= _core._INJECT_REPEAT_LIMIT:
            trip = ("repeat", repeats, _core._INJECT_REPEAT_LIMIT, _core._INJECT_BUDGET_WINDOW_S,
                    f"{repeats} identical messages to this session in the last "
                    f"{_core._INJECT_BUDGET_WINDOW_S // 60} min")
        elif unattended and unattended_hour >= _core._INJECT_UNATTENDED_HOURLY_LIMIT:
            trip = ("unattended_hourly", unattended_hour,
                    _core._INJECT_UNATTENDED_HOURLY_LIMIT, _core._INJECT_BUDGET_WINDOW_S,
                    f"{unattended_hour} unattended pokes to this session in the "
                    f"last {_core._INJECT_BUDGET_WINDOW_S // 60} min")
        elif unattended and unattended_day >= _INJECT_UNATTENDED_DAILY_LIMIT:
            trip = ("unattended_daily", unattended_day,
                    _INJECT_UNATTENDED_DAILY_LIMIT, _INJECT_BUDGET_DAY_S,
                    f"{unattended_day} unattended pokes to this session in the "
                    f"last 24h")

        events.append([now_ts, key, 1 if unattended else 0])
        entry["events"] = events[-_INJECT_BUDGET_MAX_EVENTS:]

        blocked = None
        if trip is not None:
            reason, count, limit, window_s, human = trip
            blocked = {
                "ok": False,
                "blocked": True,
                "code": "inject_rate_limit",
                "error": f"Inject blocked by circuit breaker: {human}.",
                "reason": reason,
                "count": count,
                "limit": limit,
                "window_s": window_s,
                "session_id": sid,
                "source": source,
            }
            held = data.get("held")
            if not isinstance(held, list):
                held = []
            held.append({
                "ts": now_ts,
                "session_id": sid,
                "source": source,
                "reason": reason,
                "count": count,
                "limit": limit,
                "preview": _core._activity_log_preview(text),
            })
            data["held"] = held[-_core._INJECT_HELD_MAX:]

        _inject_budget_write(data)
        return blocked
    except Exception:
        # Any unexpected failure in the meter allows the inject. The meter is
        # a safety net, not a gate the fleet's messaging depends on.
        return None
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()


def _inject_blocked_recent_entries(limit=20):
    """Recent circuit-breaker trips for /api/health. Never raises.

    Memoised on the ledger's mtime: this is on a polled dashboard path, so it
    must not re-read and re-parse the file on every tick.
    """
    try:
        mtime = _core.INJECT_BUDGET_FILE.stat().st_mtime
    except OSError:
        return []
    if _core._inject_blocked_memo["mtime"] != mtime:
        held = _inject_budget_read().get("held")
        _core._inject_blocked_memo["value"] = held if isinstance(held, list) else []
        _core._inject_blocked_memo["mtime"] = mtime
    return list(_core._inject_blocked_memo["value"])[-limit:]


# --- CCC-1000 Phase 1: the inject result contract ---------------------------
# Every inject result carries a uniform description of what actually happened,
# so a caller can distinguish "delivered into the running turn" from "queued
# until the turn ends" from "blocked" without pattern-matching on `via`. The
# contract is stamped once, in the wrapper below, instead of at the router's
# ~40 return sites. Purely additive: nothing existing is renamed or overwritten.
_INJECT_CONTRACT_VERSION = 1

# `via` values that mean the delivery cut into a turn in progress rather than
# waiting for a seam. Explicit on purpose -- guessing here would make the
# `aborted` field a lie, and the whole point of the contract is to be trusted.
_INJECT_ABORTING_VIA = frozenset({
    "spawn-sigint",
    "headless-sigint",
    "codex-app-interrupt",
    "codex-steer",
})

_INJECT_TRANSPORT_BY_VIA = {
    "uds": "uds",
    "terminal-queued": "queue",
    "spawn-fifo": "fifo",
    "live-spawn-clear": "fifo",
    "spawn-sigint": "fifo",
    "headless-sigint": "fifo",
    "codex-app-interrupt": "codex-app-server",
    "codex-steer": "codex-app-server",
    "terminal-control": "terminal",
    "tmux": "terminal",
    "hidden-pty": "terminal",
    "bg-agent-pty": "terminal",
}


# --- CCC-1000 Phase 2: the delivery-verb vocabulary -------------------------
# Callers may speak either the legacy `mode` vocabulary or the verb vocabulary
# from docs/ccc-1000-implementation-plan.md (watchtower repo). Verbs are
# translated DOWN to the legacy mode the router already implements, so the
# observable behaviour is bit-identical and #6 -- Claude's native, non-aborting
# composer semantics, which are correct today -- cannot regress.
_INJECT_LEGACY_MODES = ("answer", "send", "steer")

# verb -> (legacy mode, extra router options, contract fields)
_VERB_TO_LEGACY = {
    "engine_default": ("send", {}, {}),
    "queue": ("send", {"force_queue": True}, {"position": "back"}),
    "steer": ("steer", {}, {"abort_first": True}),
    # `abort` is a control verb: it delivers nothing and is handled by the
    # caller, not the router. Listed so validation accepts it.
    "abort": ("", {}, {}),
}

# legacy mode -> (verb, contract fields) -- how today's callers are reported
# back in the new vocabulary without changing what they do.
_LEGACY_TO_VERB = {
    "send": ("engine_default", {}),
    "steer": ("steer", {"abort_first": True}),
    "answer": ("engine_default", {"answers_pending_question": True}),
    "send_queue": ("queue", {"position": "back"}),
}


def _normalize_delivery_verb(mode):
    """Accept either vocabulary.

    Returns (legacy_mode, verb, router_options, contract_fields, error).
    """
    mode = str(mode or "send").strip().lower()
    if mode in _VERB_TO_LEGACY:
        legacy, options, fields = _VERB_TO_LEGACY[mode]
        return legacy, mode, dict(options), dict(fields), ""
    if mode in _INJECT_LEGACY_MODES:
        verb, fields = _LEGACY_TO_VERB[mode]
        return mode, verb, {}, dict(fields), ""
    return "", "", {}, {}, "invalid mode"


def _annotate_inject_result(result, *, requested, force_queue=False, fields=None):
    """Stamp the result contract onto whatever the router returned."""
    if not isinstance(result, dict):
        return result
    via = str(result.get("via") or "")
    if result.get("ok"):
        effect = "queued" if result.get("queued") else "delivered"
    elif result.get("blocked"):
        effect = "blocked"
    else:
        effect = "failed"
    aborted = bool(result.get("interrupted")) or via in _INJECT_ABORTING_VIA
    if effect == "queued":
        landed = "after_turn"
    elif effect != "delivered":
        landed = "none"
    elif aborted:
        landed = "now"
    elif via == "uds":
        # The UDS adapter sends priority=next, which Claude injects into the
        # running turn without aborting it (measured 2026-08-30, CCC-1000).
        landed = "next_seam"
    else:
        landed = "unknown"
    result.setdefault("contract", _INJECT_CONTRACT_VERSION)
    # `requested` is assigned, not setdefault-ed: the router delegates to other
    # entry points that annotate on the way out, and the OUTERMOST caller's
    # declared verb is the authoritative one. With setdefault an inner hop's
    # default ("engine_default") would win and mislabel every queue/steer call.
    result["requested"] = str(requested or "engine_default")
    result.setdefault("effect", effect)
    result.setdefault("aborted", aborted)
    result.setdefault("landed", landed)
    result.setdefault("transport", _INJECT_TRANSPORT_BY_VIA.get(via, via or "unknown"))
    for key, value in (fields or {}).items():
        result[key] = value
    if force_queue:
        result.setdefault("reason", "caller forced queueing")
    return result


def _inject_text_into_session(session_id, text, **kwargs):
    """Route `text` to a session, then stamp the CCC-1000 result contract.

    Every keyword of the router below is keyword-only, so forwarding **kwargs
    preserves the existing signature exactly.
    """
    requested = str(kwargs.pop("requested_verb", "") or "")
    mode = str(kwargs.get("mode", "send"))
    fields = dict(kwargs.pop("contract_fields", None) or {})
    if not requested:
        requested, legacy_fields = _LEGACY_TO_VERB.get(mode, (mode, {}))
        for key, value in legacy_fields.items():
            fields.setdefault(key, value)
    return _annotate_inject_result(
        _core._inject_text_into_session_router(session_id, text, **kwargs),
        requested=requested,
        force_queue=bool(kwargs.get("force_queue", False)),
        fields=fields,
    )


def _inject_text_into_session_router(
    session_id,
    text,
    *,
    _from_terminal_queue=False,
    mode="send",
    wt_origin=False,
    skip_wt=False,
    preserve_queued_steer=False,
    idempotency_key=None,
    source="api",
    force_terminal=False,
    force_headless=False,
    force_queue=False,
    peer_sender_sid=None,
):
    """Route `text` to a session using the same fall-through as /api/inject-input:
    terminal-control AppleScript when there's a TTY, FIFO write to a live spawn,
    else `claude --resume` headless. Returns a dict with at least
    {"ok": bool, "via": <route>}.
    """
    text = _core._strip_ccc_session_state_instruction(text)
    # Hard boundary: strip lone UTF-16 surrogates BEFORE the text reaches
    # any code path that will eventually JSON-serialise it for an
    # Anthropic API call. A pasted annotation or selected DOM text can
    # carry an unpaired surrogate from the browser's clipboard /
    # selection APIs; Anthropic's API rejects the resulting request body
    # with "no low surrogate in string". Belt-and-suspenders with
    # _annotation_text's own strip so we're covered regardless of which
    # entry point fed the text.
    if isinstance(text, str):
        text = _core._strip_lone_surrogates(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    # Global session reference ("<node>:<sid>")? Transparently proxy the
    # inject to the owning CCC — the caller never writes SSH. Bare local
    # ids keep today's behavior byte-for-byte.
    session_id, owner_node = _core._federation_resolve_target(session_id)
    if owner_node:
        return _core._federation_proxy_session_action(owner_node, "inject", {
            "session_id": session_id,
            "text": text,
            "mode": mode,
            "force_queue": bool(force_queue),
        })
    # Total Recall may return a Claude child transcript's bare ``agent-*``
    # id. It is searchable, but Claude cannot resume it independently; route
    # the message through the parent session that owns the child transcript.
    session_id = _core._claude_subagent_parent_session_id(session_id) or session_id
    # Kimi persists ACP sessions as ``session_<uuid>``, while older group-chat
    # sidecars can contain only the UUID shown in the UI. Resolve that alias
    # before engine detection so a Kimi nudge cannot fall through to a Claude
    # resume and fail with an unrelated ``repo_required`` error.
    session_id = _core._canonical_kimi_session_id(session_id)
    # idempotency_key differentiates two genuinely separate calls (real
    # double-send from the caller) from a single call whose log line simply
    # looks duplicated at second-resolution timestamps — CCC-736.
    _core._log_activity(
        "inject", "INJECT",
        f"session={session_id} mode={mode} source={source} "
        f"idem={idempotency_key or '-'} wt_origin={wt_origin} "
        f"text=\"{_core._activity_log_preview(text)}\"",
    )
    # Circuit breaker. Logged as an attempt above (so the log still shows what
    # was tried), refused here. See the _inject_budget_* block for why the
    # counter is a file and why a trip returns `blocked` and not just `ok:false`.
    _blocked = _core._inject_budget_check(session_id, text, source)
    if _blocked is not None:
        _core._log_activity(
            "inject", "BLOCKED",
            f"session={session_id} source={source} reason={_blocked['reason']} "
            f"count={_blocked['count']}/{_blocked['limit']} "
            f"text=\"{_core._activity_log_preview(text)}\"",
        )
        return _blocked
    is_codex = _core._is_codex_session(session_id)
    compact_command = bool(_core._COMPACT_TRIGGER_RE.match(text))
    clear_command = bool(_core._CLEAR_TRIGGER_RE.match(text))
    slash_command = bool(_core._SLASH_COMMAND_TRIGGER_RE.match(text))
    mode_value = str(mode or "").strip().lower()
    mode = mode_value if mode_value in ("answer", "steer", "send_queue") else "send"
    if compact_command and not is_codex:
        # Steered /compact: clear the wedge first, then compact. `mode` used to
        # be parsed BELOW this early return, so a steered /compact silently
        # dropped the steer and queued behind the very turn it was meant to
        # interrupt — the case that stacked five /compact entries on one
        # session. Compaction itself still goes through compact_session_context;
        # only the interrupt is added, because "/compact" written to a FIFO as
        # user text is prompt text, not a command.
        if mode == "steer":
            spawn = _core._find_live_spawn_entry_for_session(session_id)
            if spawn is not None and (spawn.get("engine") or "claude") == "claude":
                _core._write_stream_json_interrupt(spawn)
        return _core.compact_session_context(
            session_id,
            _from_terminal_queue=_from_terminal_queue,
        )
    if clear_command and not is_codex:
        # CCC-935: route "/clear" the same way as "/compact" above — through
        # clear_session_context, which re-keys the spawn entry/UI to the fresh
        # post-clear session — instead of writing it to the FIFO as literal
        # user text (Claude executes it, but CCC then has no idea the session
        # was reset and the transcript view goes stale).
        if mode == "steer":
            spawn = _core._find_live_spawn_entry_for_session(session_id)
            if spawn is not None and (spawn.get("engine") or "claude") == "claude":
                _core._write_stream_json_interrupt(spawn)
        return _core.clear_session_context(
            session_id,
            _from_terminal_queue=_from_terminal_queue,
        )
    cwd = _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, cwd)
    tty = status.get("tty")
    term_app = status.get("terminal_app")
    has_tty = _core._is_real_tty(tty)
    is_cursor = _core._is_cursor_session(session_id)
    is_hermes = _core._is_hermes_session(session_id)
    is_kimi = _core._is_kimi_session(session_id)
    is_grok = _core._session_acp_harness(session_id) == "grok"
    is_opencode = _core._is_opencode_session(session_id)
    is_devin_cli = _core._is_devin_cli_session(session_id)
    if (
        not is_codex
        and not is_kimi
        and not is_grok
        and not is_cursor
        and not is_hermes
        and not is_opencode
        and not _core._is_gemini_session(session_id)
        and not _core._is_antigravity_session(session_id)
        and not is_devin_cli
        and not has_tty
    ):
        routed = _core._control_plane_engine_call(
            "claude", "inject", {
                "session_id": session_id,
                "text": text,
                "from_terminal_queue": bool(_from_terminal_queue),
                "mode": mode,
                "wt_origin": bool(wt_origin),
                "skip_wt": bool(skip_wt),
                "preserve_queued_steer": bool(preserve_queued_steer),
                "force_queue": bool(force_queue),
                "source": source,
                "peer_sender_sid": peer_sender_sid,
            },
            idempotency_key=idempotency_key,
        )
        if routed is not None:
            return routed
    # Native Claude peer socket for agent-to-agent relays. Sits AFTER the
    # worker hand-off above and ahead of every legacy transport below: a
    # headless target that just handed off already sent once (the worker's
    # own copy of this router runs this same hook), so a dashboard-side send
    # here would be a second frame with a second msg_id. No terminal focus,
    # no FIFO ownership, reaches foreign headless sessions. Falls through on
    # anything short of a transcript-confirmed delivery.
    if not is_codex:
        uds_result = _core._try_uds_peer_delivery(
            session_id, text, source=source, mode=mode, peer_sender_sid=peer_sender_sid,
        )
        if uds_result is not None:
            return uds_result
    # Codex: only its OWN TUI commands need a live interactive terminal.
    # Slash-shaped text that isn't one (e.g. /group-chat-checkin from the
    # group-chat flow) is just prompt text — route it through the normal
    # resume path instead of bouncing "requires live TUI" (CCC-107).
    if is_codex and slash_command:
        _first_tok = text.strip().split(None, 1)[0]
        if _first_tok not in _core._CODEX_SLASH_NAME_SET:
            slash_command = False
    if is_codex and slash_command:
        if status.get("live") and has_tty:
            if not _from_terminal_queue and _core._terminal_input_queue_has_pending(session_id):
                return _core._queue_terminal_input(
                    session_id, text, status,
                    reason_hint=_core._TERMINAL_QUEUE_ORDER_REASON,
                )
            submit_key = "tab" if _core._session_status_is_busy(status) else "return"
            return _core.inject_input_via_keystroke(
                tty,
                term_app or "Terminal",
                text,
                submit_key=submit_key,
            )
        # Non-live fallback: `/goal` is codex's native goal command, but its
        # slash only runs in a live TUI. For a dormant thread, drive the same
        # native goal store directly — `/goal clear` clears, `/goal <objective>`
        # sets, and pause/resume update the goal status. `edit` still needs a
        # live TUI because the button form carries no replacement objective.
        if _first_tok == "/goal":
            _goal_rest = text.strip()[len("/goal"):].strip()
            _goal_sub = _goal_rest.split(None, 1)[0].lower() if _goal_rest else ""
            if _goal_sub == "clear":
                res = _core._codex_goal_via_app_server(session_id, "clear", cwd=cwd)
                res.setdefault("engine", "codex")
                return res
            if _goal_sub in ("pause", "resume"):
                res = _core._codex_goal_via_app_server(session_id, _goal_sub, cwd=cwd)
                res.setdefault("engine", "codex")
                return res
            if _goal_rest and _goal_sub not in ("edit", "pause", "resume"):
                res = _core._codex_goal_via_app_server(
                    session_id, "set", objective=_goal_rest, cwd=cwd
                )
                res.setdefault("engine", "codex")
                return res
        return {
            "ok": False,
            "code": "codex_slash_requires_live_tui",
            "engine": "codex",
            "error": (
                "Codex slash commands require a live interactive Codex "
                "terminal. Launch or focus the session, then retry."
            ),
        }
    # Defense-in-depth: never inject text while the session is parked on an
    # interactive PICKER — an AskUserQuestion prompt or a permission/approval
    # prompt. Typed input there doesn't reach the agent; it answers (or
    # mangles) the picker. THIS is the case that actually warrants queueing —
    # not a busy tool turn, which the TUI/stream-json both accept input during.
    # Queue it instead; it flushes once the user resolves the prompt in the UI
    # and the marker clears. Covers direct injects (annotation flows,
    # /api/inject-input) as well as the top-level queue watcher.
    answering_tty_question = False
    if not _from_terminal_queue:
        question_blocks_inject = _core._ask_question_blocking_inject(session_id, status)
        answering_tty_question = (
            has_tty
            and question_blocks_inject
            and (
                mode == "answer"
                or _core._pending_question_option_matches_text(session_id, text)
            )
        )
        if (
            (question_blocks_inject and not answering_tty_question)
            or (_core._notification_blocks_inject(session_id) and not answering_tty_question)
        ):
            return _core._queue_terminal_input(session_id, text, {"status": "busy"})
    if is_codex and mode == "steer":
        steer_kwargs = {"steer": True}
        if idempotency_key:
            steer_kwargs["idempotency_key"] = idempotency_key
        steer_kwargs["preserve_queued_steer"] = bool(preserve_queued_steer)
        steer_result = _core.resume_session_codex(
            session_id, text, **steer_kwargs
        )
        if preserve_queued_steer:
            return steer_result
        if steer_result.get("code") in (
            "codex_no_active_turn",
            "codex_steer_unavailable",
        ):
            if idempotency_key:
                steer_result = _core.resume_session_codex(
                    session_id, text,
                    idempotency_key=f"{idempotency_key}:steer-fallback",
                )
            else:
                steer_result = _core.resume_session_codex(session_id, text)
        if steer_result.get("ok") and not steer_result.get("queued"):
            _core._consume_matching_pending_input(session_id, text)
        return steer_result
    if (
        mode == "steer"
        and not is_codex
        and not is_cursor
        and not is_hermes
        and not has_tty
    ):
        # Claude headless steer. Without this, a turn wedged on a long-running
        # tool child (a `while true` poll loop, a slow build) never reaches a
        # turn boundary, so `_spawn_entry_active_tool_child` holds every queued
        # inject indefinitely — the UI sits on "sending…" for hours with no
        # failure to report, because nothing actually failed.
        #
        # The interrupt control request manufactures the missing boundary: it
        # aborts the in-flight tool, ends the turn, and leaves the process alive
        # to read the next stdin message (both verified against claude 2.1.216).
        # Order matters — write the interrupt first, then the text, so Claude
        # consumes the abort before it reads the follow-up as a fresh turn.
        spawn = _core._find_live_spawn_entry_for_session(session_id)
        if spawn is not None and (spawn.get("engine") or "claude") == "claude":
            if _core._write_stream_json_interrupt(spawn):
                delivered = _core._write_stream_json_user_message(spawn, text)
                if delivered:
                    # The text just landed straight on the live process's
                    # stdin, bypassing the durable FIFO queue entirely. A
                    # matching entry already parked in the terminal queue
                    # (e.g. from an earlier queued send of the same text)
                    # would otherwise sit there forever — never popped,
                    # since nothing routes back through the queue on this
                    # success path — leaving the UI showing "queued" for a
                    # message that was actually delivered.
                    _core._drop_matching_terminal_queue_entries(session_id, text)
                    return {
                        "ok": True,
                        "via": "claude-interrupt-steer",
                        "pid": spawn.get("pid"),
                    }
                # Interrupt landed but the text did not. The turn is now ending
                # anyway, so park the text for the drain loop rather than
                # reporting a failure the user can't act on.
                return _core._queue_terminal_input(session_id, text, status)
        # No live CCC-owned spawn to interrupt (foreign writer, or the process
        # is gone). Fall through to the normal send routing below — steering a
        # session we don't own isn't possible, but delivering to it may be.
    if status.get("live") and has_tty:
        if not _from_terminal_queue and not answering_tty_question:
            busy_or_pending = (
                _core._terminal_input_queue_has_pending(session_id)
                or _core._session_status_is_busy(status)
            )
            # Codex/Cursor keep their own busy-turn delivery (resume/steer) —
            # their TUIs aren't driven by raw keystrokes the way Claude's is.
            if busy_or_pending and is_codex:
                if idempotency_key:
                    return _core.resume_session_codex(
                        session_id, text, idempotency_key=idempotency_key,
                    )
                return _core.resume_session_codex(session_id, text)
            if busy_or_pending and is_cursor:
                return _core.resume_session_cursor(session_id, text)
            # Claude TTY: only queue to preserve ORDER when input is already
            # queued. A merely-busy turn no longer blocks — the Claude TUI
            # accepts typed input mid-turn and queues it itself (verified for
            # headless; same queued-messages behaviour in the TUI). Picker
            # states (question/approval), where keystrokes WOULD mangle, are
            # already caught by the guard above. The tty-unreachable fallback
            # below still queues if the keystroke can't reach the tab.
            # force_queue is the explicit "Send (queue if busy)" opt-in: skip
            # the TUI's own mid-turn keystroke acceptance and hold for the
            # next turn boundary even though nothing is queued yet.
            _already_pending = _core._terminal_input_queue_has_pending(session_id)
            if _already_pending or (force_queue and _core._session_status_is_busy(status)):
                return _core._queue_terminal_input(
                    session_id, text, status,
                    reason_hint=_core._TERMINAL_QUEUE_ORDER_REASON if _already_pending else None,
                )
        if answering_tty_question:
            _core._drop_matching_terminal_queue_entries(session_id, text)
        # tmux-hosted session: the peer registry carries Claude's own pane
        # target, and send-keys reaches it exactly — no terminal-app window
        # matching, no Automation permission, and detached sessions AppleScript
        # can't see at all. Fall through to keystrokes on any tmux failure.
        tmux_target = "" if (is_codex or is_cursor) else _core._registry_tmux_target_for_session(session_id)
        if tmux_target:
            tmux_result = _core.inject_input_via_tmux(tmux_target, text)
            if tmux_result.get("ok"):
                return tmux_result
        keystroke_result = _core.inject_input_via_keystroke(tty, term_app or "Terminal", text)
        # Codex/Cursor fallback: their TUIs accept input through their own
        # resume/steer delivery, so when keystroke injection ISN'T viable —
        # inject_input_via_keystroke is osascript-only, so it always fails on
        # Linux (no AppleScript driver, terminal_app is None), and can also fail
        # on macOS (permission denied / tab unreachable) — deliver via resume
        # instead of returning the failure. Without this, a terminal-queue drain
        # (`_from_terminal_queue=True`, which skips the tty-unreachable re-queue
        # below) re-parks the message every 60s forever ("Queued: the session is
        # busy" that never clears). Answering a native tty PICKER still needs the
        # keystroke, so that path is left alone.
        if (isinstance(keystroke_result, dict) and not keystroke_result.get("ok")
                and not answering_tty_question):
            if is_codex:
                if idempotency_key:
                    return _core.resume_session_codex(
                        session_id, text, idempotency_key=idempotency_key,
                    )
                return _core.resume_session_codex(session_id, text)
            if is_cursor:
                return _core.resume_session_cursor(session_id, text)
        # If the AppleScript can't find the terminal tab (user switched
        # apps, tab hidden, fullscreen-elsewhere, permission denied),
        # queue the text instead of bouncing the message. The queue
        # drains the next time the right terminal is focused OR the
        # user re-fronts it manually. Saves the user from retyping.
        if isinstance(keystroke_result, dict) and not keystroke_result.get("ok") and not _from_terminal_queue:
            queued_status = dict(status or {})
            queued_status["status"] = queued_status.get("status") or "tty-unreachable"
            queued = _core._queue_terminal_input(session_id, text, queued_status)
            queued["tty_unreachable"] = True
            queued["original_error"] = keystroke_result.get("error") or "Terminal tab not reachable"
            queued["note"] = (
                "Queued — your message will be sent the next time CCC can "
                "reach the terminal (re-front it, or it'll drain on the "
                "next inject)."
            )
            return queued
        return keystroke_result
    if status.get("live") and status.get("kind") == "bg":
        if not _from_terminal_queue:
            _already_pending = _core._terminal_input_queue_has_pending(session_id)
            if _already_pending or not _core._bg_agent_ready_for_input(session_id, status):
                queued_status = dict(status or {})
                queued_status["status"] = queued_status.get("status") or "busy"
                return _core._queue_terminal_input(
                    session_id, text, queued_status,
                    reason_hint=_core._TERMINAL_QUEUE_ORDER_REASON if _already_pending else None,
                )
        worker = _core._find_live_bg_agent_entry_for_session(session_id)
        result = _core._inject_bg_agent_via_pty_socket(worker, text, session_id=session_id)
        # Unconfirmed delivery (app-managed terminal that eats socket input):
        # park the message instead of dropping it — it drains when the bg
        # process exits (resume path) or a working channel appears.
        if isinstance(result, dict) and result.get("delivery_unconfirmed"):
            queued_status = dict(status or {})
            queued_status["status"] = "bg-undeliverable"
            queued = _core._queue_terminal_input(session_id, text, queued_status)
            queued["ok"] = True
            queued["delivery_unconfirmed"] = True
            queued["original_error"] = result.get("error")
            queued["note"] = (
                "CCC can't reach this Claude-app terminal — your message is "
                "parked and sends when the app session closes. To act on it "
                "now, type it in the Claude app window."
            )
            return queued
        return result
    if is_codex:
        if idempotency_key:
            return _core.resume_session_codex(
                session_id, text, idempotency_key=idempotency_key,
            )
        return _core.resume_session_codex(session_id, text)
    acp_harness = "kimi" if is_kimi else ("grok" if is_grok else None)
    if acp_harness:
        if (
            not _from_terminal_queue
            and mode != "steer"
            and _core._terminal_input_queue_has_pending(session_id)
        ):
            return _core._queue_terminal_input(session_id, text, {"status": "running"})
        prompt_kwargs = {
            "mode": mode,
            "from_queue": _from_terminal_queue,
        }
        if idempotency_key:
            prompt_kwargs["idempotency_key"] = idempotency_key
        result = _core._acp_prompt(acp_harness, session_id, text, **prompt_kwargs)
        if (
            result.get("code") == "grok_external_active"
            and not _from_terminal_queue
        ):
            # Mirrors Codex's write-gate (_codex_writer_gate_response): a live
            # `grok --resume` TUI and CCC's ACP connection can't safely write
            # the same session at once (CCC-884), but the user experience
            # should still match Claude's "just works" send -- queue instead
            # of surfacing the raw conflict, and the terminal-queue watcher
            # retries via this same path until the TUI closes and
            # _grok_external_writer_active() clears.
            queued = _core._queue_terminal_input(session_id, text, {"status": "busy"})
            queued["queued_reason"] = (
                "Grok is open in a terminal — your message is queued and will "
                "send once that terminal session closes"
            )
            return queued
        if (
            result.get("code") == "busy"
            and not _from_terminal_queue
        ):
            if mode == "steer":
                # ACP has no Codex-style mid-turn steer -- a session/prompt
                # sent while a turn is active is rejected outright by the
                # agent -- but session/cancel DOES interrupt the active turn:
                # it's the exact primitive the Esc button already uses
                # successfully for Kimi/Grok (see interrupt_session). Cancel,
                # wait briefly for the turn to actually end, then resend as a
                # fresh turn (mirrors Claude's interrupt-then-write steer).
                cancel_result = _core._acp_cancel(acp_harness, session_id)
                if cancel_result.get("ok"):
                    with _core._RECENT_INTERRUPT_LOCK:
                        _core._RECENT_INTERRUPT_BY_SID[session_id] = time.time()
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        snap = _core._acp_session_snapshot(acp_harness, session_id) or {}
                        if snap.get("status") != "active":
                            break
                        time.sleep(0.15)
                    # The resend MUST carry its own idempotency key. The
                    # pre-cancel attempt already consumed `idempotency_key`
                    # and is sitting in the worker's ledger as `failed`
                    # ("turn already in progress"); reusing the same key made
                    # WorkLedger.submit dedupe the retry straight back to that
                    # failed row, so the post-cancel prompt was never sent and
                    # every steer fell through to the durable queue — the
                    # cancel landed, the message did not (CCC-922 follow-up).
                    # Deriving the key keeps replay-safety: the same inject
                    # request always derives the same retry key.
                    retry_kwargs = dict(prompt_kwargs)
                    if idempotency_key:
                        retry_kwargs["idempotency_key"] = (
                            f"{idempotency_key}:steer-retry"
                        )
                    retry = _core._acp_prompt(
                        acp_harness, session_id, text, **retry_kwargs
                    )
                    if retry.get("code") != "busy":
                        return retry
                    result = retry
            # Turn couldn't be cancelled (or the retry above still lost the
            # race) -- fall back to the durable FIFO instead of surfacing
            # the transport's busy failure.
            return _core._queue_terminal_input(session_id, text, {"status": "running"})
        return result
    if _core._is_gemini_session(session_id):
        return _core.resume_session_gemini(session_id, text)
    if is_cursor:
        return _core.resume_session_cursor(session_id, text)
    if _core._is_antigravity_session(session_id):
        return _core.resume_session_antigravity(session_id, text)
    if is_hermes:
        return _core.resume_session_hermes(session_id, text)
    if is_opencode:
        return _core.resume_session_opencode(session_id, text)
    if _core._is_aider_session(session_id):
        return _core.resume_session_aider(session_id, text)
    if _core._is_devin_cli_session(session_id):
        # A Devin ACP process launched outside CCC owns its stdio connection.
        # `devin --resume` cannot attach to it; retrying only duplicates the
        # pending message when the CLI rejects the parallel session start.
        raw_id = _core._devin_cli_raw_id(session_id)
        live_spawn = _core._find_live_spawn_entry_for_session(session_id)
        external_owner = (
            _core._devin_cli_session_live(raw_id) and live_spawn is None
        )
        if mode == "steer":
            _core._queue_devin_steer(session_id, text)
        else:
            _core._queue_devin_resume_input(session_id, text)
        reason = (
            "Devin is working in another client; the latest steering message "
            "will send after that turn finishes."
            if external_owner and mode == "steer"
            else "Queued for Devin delivery"
        )
        _core._note_pending_queued(session_id, text, reason)
        if external_owner:
            return {
                "ok": True,
                "queued": True,
                "external_devin_owner": True,
                "via": "devin-external-owner-queued",
                "queued_reason": reason,
            }
        threading.Thread(
            target=_core._pump_devin_resume_queue,
            args=(session_id,),
            daemon=True,
            name=f"devin-pump-{session_id[-12:]}",
        ).start()
        return {
            "ok": True,
            "queued": True,
            "via": "devin-resume-queued",
            "queued_reason": reason,
        }
    # force_terminal: the user confirmed a terminal is running and wants the
    # text sent there. Find the terminal's TTY and inject via osascript even
    # though the initial status check didn't see it.
    if force_terminal and not has_tty:
        terminal_pids = _core._live_claude_terminal_pids_by_session().get(session_id, set())
        if terminal_pids:
            for tpid in terminal_pids:
                try:
                    ps_out = _core.subprocess.run(
                        ["ps", "-o", "tty=,command=", "-p", str(tpid)],
                        capture_output=True, text=True, timeout=2,
                    )
                    if ps_out.returncode == 0:
                        parts = ps_out.stdout.strip().split(None, 1)
                        if len(parts) >= 1 and _core._is_real_tty(parts[0]):
                            # inject_input_via_keystroke handles both Terminal
                            # and iTerm2 via osascript; "Terminal" is the
                            # default and it falls back gracefully.
                            return _core.inject_input_via_keystroke(
                                parts[0], "Terminal", text
                            )
                except Exception:
                    pass
        # No terminal found — fall through to headless delivery.
    if not status.get("live") or not has_tty:
        spawn = _core._find_live_spawn_entry_for_session(session_id)
        if spawn is not None:
            # Soft-block: if we're about to send to the headless but we detect
            # a live terminal on this session that we couldn't route to (TTY
            # detection missed it), ask the user before delivering. The user
            # might be typing in a terminal we can't see — sending to the
            # headless would fork the transcript. force_headless skips this
            # (the user already answered "No, send to headless").
            if (
                not force_headless
                and not _from_terminal_queue
                and spawn.get("engine") == "claude"
                and not force_terminal
            ):
                suspected_terminal = _core._session_has_live_terminal(
                    session_id, exclude_pid=spawn.get("pid")
                )
                if suspected_terminal:
                    return {
                        "ok": False,
                        "soft_block": True,
                        "code": "terminal_suspected",
                        "message": (
                            "CCC detected a terminal running on this session. "
                            "Do you want to send your text to the terminal "
                            "instead of the background session?"
                        ),
                        "session_id": session_id,
                    }
            active_child = _core._spawn_entry_active_tool_child(spawn)
            if not _from_terminal_queue:
                # mode="send" (default, e.g. the composer's Send button):
                # only queue when input is ALREADY queued, to preserve
                # delivery order — the write below still falls back to the
                # queue if the pipe genuinely rejects the message. This can
                # occasionally interrupt a busy turn (mid-turn stdin writes
                # aren't guaranteed to defer — see c115c5cc's history).
                #
                # force_queue is the composer's explicit "Send (queue if
                # busy)" opt-in. The browser intentionally keeps the public
                # mode as "send" and transmits this separate flag, so headless
                # routing must inspect the flag rather than the obsolete
                # internal mode="send_queue" spelling. Explicit result-target
                # state is authoritative; the log-tail fallback is used only
                # for legacy spawn records that predate that state.
                if force_queue:
                    is_busy = (
                        _core._session_status_is_busy(status)
                        or _core._headless_turn_in_progress(spawn)
                        or _core._terminal_input_queue_has_pending(session_id)
                    )
                else:
                    is_busy = _core._terminal_input_queue_has_pending(session_id)
                if is_busy:
                    queued_status = dict(status or {})
                    queued_status["status"] = queued_status.get("status") or "busy"
                    queued_status["pid"] = queued_status.get("pid") or spawn.get("pid")
                    if active_child:
                        queued_status["active_child_pid"] = active_child.get("pid")
                    return _core._queue_terminal_input(session_id, text, queued_status)
            # GH #71 — use-time staleness check (Claude headless only). If the
            # transcript advanced past what this headless last produced, a
            # different writer (a terminal, or another `claude --resume`) drove
            # the session — our headless's in-memory state is behind disk.
            # Writing to it now would answer from frozen memory (the "rollback").
            # Retire the stale headless and route through a fresh resume, which
            # reads current disk. An outstanding owned-input result target is
            # the authoritative mid-turn signal across thinking, text
            # streaming, and between-tool gaps; a tool child is additional
            # defense for legacy entries. Never retire while either is active.
            if (
                spawn.get("engine") == "claude"
                and not _core._headless_turn_in_progress(spawn)
                and not _core._spawn_entry_active_tool_child(spawn)
                and _core._headless_spawn_is_stale(spawn, session_id)
            ):
                _diag = spawn.get("_last_stale_diag") or {}
                _core._log_activity(
                    "stale", "STALE_RETIRE",
                    f"sid={session_id} pid={spawn.get('pid')} "
                    f"uuid_changed={_diag.get('uuid_changed')} "
                    f"size_delta={_diag.get('size_delta')}",
                )
                _core._resume_ledger_append(
                    "stale_retire", sid=session_id, pid=spawn.get("pid"),
                    uuid_changed=_diag.get("uuid_changed"),
                    size_delta=_diag.get("size_delta"),
                    cur_results=_diag.get("cur_results"),
                    prev_results=_diag.get("prev_results"),
                )
                _core._retire_unresponsive_spawn_entry(
                    spawn, terminate=True, reason="stale_transcript",
                    caller="inject-stale-detection",
                )
                return _core.resume_session_headless(session_id, text)
            ok = _core._write_stream_json_user_message(spawn, text)
            if ok:
                if spawn.get("engine") == "claude":
                    # Record where the transcript stands now that this headless
                    # has been driven, so the next inject can detect an
                    # external writer that arrived in between.
                    _core._update_spawn_transcript_watermark(spawn, session_id)
                return {"ok": True, "pid": spawn["pid"], "via": "spawn-fifo"}
            # A transient FIFO failure is not permission to kill an owned
            # active turn. Preserve the user's text in the durable queue and
            # let the watcher retry after the tracked boundary. A drained row
            # returns a failure so the watcher requeues it at the front.
            if (
                _core._headless_turn_in_progress(spawn)
                or _core._spawn_entry_active_tool_child(spawn)
            ):
                if not _from_terminal_queue:
                    queued_status = dict(status or {})
                    queued_status["status"] = (
                        queued_status.get("status") or "headless"
                    )
                    queued_status["pid"] = (
                        queued_status.get("pid") or spawn.get("pid")
                    )
                    return _core._queue_terminal_input(
                        session_id, text, queued_status,
                    )
                return {
                    "ok": False,
                    "pid": spawn["pid"],
                    "via": "spawn-fifo",
                    "error": "session input pipe is busy",
                }
            if not _core._spawn_entry_active_tool_child(spawn):
                _core._retire_unresponsive_spawn_entry(spawn, terminate=True, reason="write_failed")
                return _maybe_queue_on_invalid_cwd(
                    session_id, text, status, _core.resume_session_headless(session_id, text),
                )
            return {
                "ok": False,
                "pid": spawn["pid"],
                "via": "spawn-fifo",
                "error": "session input pipe is busy",
            }
        # WT-worker fifo fast path: a live WatchTower-tracked worker's FIFO is
        # a known, in-process-reachable channel even when CCC never spawned
        # it itself. Try this BEFORE the fork guard below concludes there's
        # no channel and parks the message -- "no channel" for a live WT
        # worker was exactly the incident that motivated this fast path.
        # Bypasses CCC_MESSAGING_BACKEND by design: fifo-only + WT-tracked-
        # only is a narrower, already-proven-safe risk class (zero fifo
        # losses recorded, vs. real losses on the resume/delegate transports
        # that flag also gates) than the general messaging-backend flag.
        if status.get("live") and status.get("pid") and not has_tty:
            wt_entry = _core._wt_worker_fifo_entry_for_session(session_id)
            if wt_entry is not None:
                if _core._write_fifo_line_once(str(wt_entry.get("fifo") or ""), text):
                    return {
                        "ok": True,
                        "pid": wt_entry.get("pid"),
                        "via": "wt-worker-fifo",
                    }
                # ENXIO / write failure (worker not listening right now)
                # falls through to the fork guard below -- never a parallel
                # resume.
        # Fork guard: a live claude we did NOT spawn and cannot drive (no
        # tty to keystroke, no FIFO, not the bg-pty shape handled above —
        # e.g. a daemon-hosted interactive session). Spawning a parallel
        # `claude --resume` here puts TWO writers on one JSONL and forks
        # the transcript — the root cause of the post-/compact amnesia
        # (resume/compact then follow the stale leaf). Queue instead; the
        # queue drains once the process exits or becomes reachable.
        if status.get("live") and status.get("pid") and not has_tty:
            queued_status = dict(status or {})
            queued_status["status"] = queued_status.get("status") or "foreign-live-writer"
            queued = _core._queue_terminal_input(session_id, text, queued_status)
            queued["foreign_live_writer"] = True
            queued["original_error"] = (
                "A live claude process (pid %s) already owns this session but "
                "CCC has no channel to it." % status.get("pid")
            )
            queued["note"] = (
                "Queued — another process is running this session. Your "
                "message sends when it finishes (resuming in parallel would "
                "fork the conversation history)."
            )
            return queued
        # Stage 2 WatchTower messaging handover: this is the dormant-claude
        # fallback, the sole place this function decides delivery would be
        # a headless resume. See _try_wt_send_for_headless_delivery above.
        # wt_origin=True means this request arrived FROM wt's delegate
        # adapter (WT-78 origin marker) — calling back into `wt send` here
        # would recurse CCC -> wt -> CCC, so wt-originated requests go
        # straight to the native resume. skip_wt=True is the client's
        # receipt-lost fallback (CCC-452): wt already accepted this text once
        # and the receipt verified it never landed, so retry natively.
        if not wt_origin and not skip_wt:
            wt_result = _core._try_wt_send_for_headless_delivery(session_id, text)
            if wt_result is not None:
                return wt_result
        return _maybe_queue_on_invalid_cwd(
            session_id, text, status, _core.resume_session_headless(session_id, text),
        )


def _maybe_queue_on_invalid_cwd(session_id, text, status, result):
    """If a resume returned invalid_cwd, queue the text so it isn't lost.

    The user's typed message would otherwise vanish into a toast and they'd
    have to retype after relocating the cwd. Queueing means the moment the
    user points CCC at the new directory (or restores it on disk), the
    next inject drains the queue and the message goes through. Adds a
    note to the response so the client can show a helpful toast.
    """
    if not isinstance(result, dict):
        return result
    if (result.get("code") or "") != "invalid_cwd":
        return result
    if not text:
        return result
    queued_status = dict(status or {})
    queued_status["status"] = queued_status.get("status") or "cwd-missing"
    queued = _core._queue_terminal_input(session_id, text, queued_status)
    queued["cwd_missing"] = True
    queued["missing_path"] = result.get("path") or ""
    queued["original_error"] = result.get("error") or "Session cwd is gone"
    queued["note"] = (
        "Queued — your message will be sent the moment the directory is "
        "restored or you point CCC at the new location."
    )
    return queued


def _set_session_model(session_id, model, context_1m, reasoning_effort=None, effort_only=False):
    """Apply a model+context choice to a session.

    Live Claude (TTY or spawned) gets a real `/model <alias>[1m]` slash
    command injected into the running process. Codex, Gemini, Cursor,
    Antigravity, and dormant Claude have no runtime-switch mechanism, so the choice is persisted
    to the session-overrides sidecar and applied on the next resume.

    Always writes the override regardless — that way a refresh shows the
    new value even when the inject succeeded, and the next resume picks
    it up if the live session ends before the user asks again.

    `reasoning_effort` is validated against the ladder the session's own
    engine accepts (Claude `--effort`, Codex `model_reasoning_effort`, Kimi
    thinking effort). It defaults to None, which preserves whatever was
    previously set rather than clearing it when the caller is only changing
    the model.

    Returns:
        {"ok": True, "applied": "live"|"queued", "model": ..., "context_1m": ...,
         "engine": ..., "reasoning_effort": ...}
    """
    if not session_id or not model:
        return {"ok": False, "error": "missing session_id or model"}
    engine = _core._detect_session_engine(session_id)
    if engine == "codex":
        model, model_error = _core._validate_codex_model(model, require_available=True)
        if model_error:
            return {
                "ok": False,
                "error": model_error,
                "code": "codex_model_unavailable",
                "known_codex_models": list(_core._ENGINE_KNOWN_MODELS["codex"]),
            }
    if reasoning_effort is None:
        reasoning_effort = (_core._get_session_override(session_id) or {}).get("reasoning_effort") or ""
    else:
        reasoning_effort = _core._validate_reasoning_effort(reasoning_effort, engine, strict=True)
        if reasoning_effort is None:
            return {
                "ok": False,
                "error": _core._reasoning_effort_error("reasoning_effort", engine),
                "engine": engine,
            }
    if engine == "kimi":
        # Kimi's ACP harness supports a live ``model`` config option. Unlike
        # the other non-Claude engines, it has no resume path that consumes a
        # deferred session override, so queuing here left the active session
        # permanently on its previous model.
        result = _core._acp_set_config("kimi", session_id, "model", model)
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error") or "Kimi rejected the model change",
                "code": result.get("code") or "kimi_model_switch_failed",
                "engine": engine,
            }
        _core._set_session_override(session_id, model, context_1m, engine, reasoning_effort)
        return {
            "ok": True,
            "model": model,
            "context_1m": bool(context_1m),
            "engine": engine,
            "reasoning_effort": reasoning_effort,
            "applied": "live",
            "via": "kimi-acp-config",
        }
    _core._set_session_override(session_id, model, context_1m, engine, reasoning_effort)
    payload = {
        "ok": True,
        "model": model,
        "context_1m": bool(context_1m),
        "engine": engine,
        "reasoning_effort": reasoning_effort,
    }
    if engine != "claude":
        # Non-Claude engines have no live-inject path. The next
        # resume_session_<eng> call reads the override and applies it through
        # that CLI's supported model-selection mechanism.
        payload["applied"] = "queued"
        return payload
    # Claude: try to inject /model X[1m] into a live TTY or spawn. If
    # there is no live process, fall through to queued — the next resume
    # picks the override up via cmd args.
    cwd = _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, cwd) or {}
    tty = status.get("tty")
    has_tty = _core._is_real_tty(tty)
    if has_tty and status.get("live"):
        slash = (f"/effort {reasoning_effort}" if effort_only and reasoning_effort
                 else _core._build_slash_model_command(model, context_1m))
        if not slash:
            payload["applied"] = "queued"
            return payload
        result = _core.inject_input_via_keystroke(tty, status.get("terminal_app") or "Terminal", slash)
        if result.get("ok"):
            payload["applied"] = "live"
            payload["via"] = "tty-keystroke"
            return payload
        payload["applied"] = "queued"
        payload["inject_error"] = result.get("error")
        return payload
    # Headless: the spawn entry lives in whichever process owns engine
    # execution (the worker), so route through the control plane before the
    # in-process fallback — same pattern as _interrupt_session's "claude",
    # "interrupt" call. Without this, the dashboard process's own (always
    # empty, for a worker-spawned session) _spawned_sessions list made this
    # silently no-op as "queued" instead of ever reaching the retire/approval
    # logic below.
    routed = _core._control_plane_engine_call(
        "claude", "model", {
            "session_id": session_id, "model": model,
            "context_1m": context_1m, "reasoning_effort": reasoning_effort,
            "effort_only": effort_only,
        },
    )
    local = routed if routed is not None else _set_session_model_headless_local(
        session_id, model, context_1m, reasoning_effort, effort_only,
    )
    if local.get("ok") and local.get("code") != "no_spawn":
        payload.update({k: v for k, v in local.items() if k not in ("ok",)})
        payload.setdefault("applied", "queued")
        return payload
    payload["applied"] = "queued"
    return payload


def _set_session_model_headless_local(session_id, model, context_1m, reasoning_effort, effort_only):
    """In-band model switch for a CCC-owned headless Claude spawn.

    Runs wherever the spawn entry actually lives (see _interrupt_claude_headless_local
    for the same routing shape). CCC-54 follow-up: a headless `claude -p` does
    NOT process slash commands — writing "/model X[1m]" over the stream-json
    FIFO just makes it answer "/model isn't available in this environment".
    A headless model is fixed at spawn via `--model`, so switching means
    respawning. The override is already persisted by the caller; this only
    retires the current headless (now if idle, deferred to idle if busy,
    approval-gated per _retire_idle_headless_for_session) so the next turn
    does a fresh `--resume` on the new model.
    """
    spawn = _core._find_live_spawn_entry_for_session(session_id)
    if spawn is None or (spawn.get("engine") or "claude") != "claude":
        return {"ok": False, "code": "no_spawn"}
    retired = _core._retire_idle_headless_for_session(
        session_id, reason="model-switch", defer_if_busy=True,
        require_approval=True)
    result = {"ok": True, "via": "respawn-on-next-turn"}
    if retired.get("retired"):
        result["retired_headless_pid"] = retired.get("pid")
    elif retired.get("deferred"):
        result["headless_retire_deferred"] = True
    elif retired.get("reason") == "pending_approval":
        result["headless_retire_ask_id"] = retired.get("ask_id")
    return result


def _interrupt_claude_headless_local(session_id):
    """In-band interrupt for a CCC-owned headless Claude spawn.

    Runs wherever the spawn entry actually lives — the worker owns engine
    execution, so the dashboard reaches this through the control plane.
    Writes the stream-json `interrupt` control request to the spawn's FIFO:
    it aborts the in-flight tool, ends the turn, and LEAVES THE PROCESS
    ALIVE, which is what "Esc" should mean. SIGINT (the old and now
    fallback-only route) kills `claude -p` outright — the session is over
    and every later message pays a cold `--resume`.
    """
    if not session_id:
        return {"ok": False, "code": "missing_session_id"}
    spawn = _core._find_live_spawn_entry_for_session(session_id)
    if spawn is None or (spawn.get("engine") or "claude") != "claude":
        return {"ok": False, "code": "no_spawn"}
    if not _core._write_stream_json_interrupt(spawn):
        return {"ok": False, "code": "write_failed", "pid": spawn.get("pid")}
    return {
        "ok": True,
        "via": "claude-stream-interrupt",
        "pid": spawn.get("pid"),
        "note": "turn aborted — session still live",
    }


def _interrupt_session(session_id):
    """Send an interrupt to a session using the same fall-through as
    `_inject_text_into_session`:

      * Codex app-server turn → `turn/interrupt`.
      * Live Codex process → SIGINT directly to the process.
      * Live TTY (non-Codex) → AppleScript Esc keystroke (cancels the in-flight stream
        when Claude is mid-response, clears the input buffer otherwise).
      * Live headless Claude spawn CCC owns → stream-json `interrupt` control
        request: aborts the turn, process survives.
      * Live headless session with no reachable FIFO (foreign writer, or the
        spawn entry is gone) → SIGINT to its identity-verified pid. NOTE: this
        terminates the headless `claude -p` subprocess — you cannot resume
        mid-conversation, the spawn is over.
      * Dormant session with no live spawn → no-op error; nothing is running
        to interrupt.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    cwd = _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, cwd)
    if _core._is_codex_session(session_id):
        app_interrupt = _core._codex_interrupt_via_app_server(session_id, cwd=cwd)
        if app_interrupt.get("ok"):
            return app_interrupt
        pid = status.get("pid")
        if not pid:
            spawn = _core._find_live_spawn_entry_for_session(session_id)
            if spawn:
                pid = spawn.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGINT)
            except (ProcessLookupError, PermissionError, OSError) as e:
                return {"ok": False, "via": "spawn-sigint", "pid": pid, "error": str(e)}
            return {
                "ok": True,
                "via": "spawn-sigint",
                "pid": pid,
                "note": "Codex process interrupted",
            }
        return {"ok": False, "error": "Codex session is not live — nothing to interrupt"}
    acp_harness = _core._session_acp_harness(session_id)
    if acp_harness:
        result = _core._acp_cancel(acp_harness, session_id)
        # Unlike Claude/Codex, an ACP cancel never appends a
        # request_interrupted event the transcript-scan path can pick up,
        # so mark the badge from the one place that knows CCC just asked
        # the harness to cancel.
        if result.get("ok"):
            with _core._RECENT_INTERRUPT_LOCK:
                _core._RECENT_INTERRUPT_BY_SID[session_id] = time.time()
        return result
    tty = status.get("tty")
    term_app = status.get("terminal_app")
    has_tty = _core._is_real_tty(tty)
    if status.get("live") and has_tty:
        result = _core.interrupt_input_via_keystroke(tty, term_app or "Terminal")
        result["via"] = "tty-esc"
        return result
    # Headless Claude: prefer the in-band control request over SIGINT so Esc
    # cancels the TURN instead of ending the SESSION. The spawn entry lives in
    # whichever process owns engine execution (the worker), hence the routed
    # call before the local attempt.
    if not _core._is_cursor_session(session_id) and not _core._is_hermes_session(session_id) \
            and not _core._is_opencode_session(session_id) \
            and not _core._is_gemini_session(session_id) \
            and not _core._is_antigravity_session(session_id):
        routed = _core._control_plane_engine_call(
            "claude", "interrupt", {"session_id": session_id},
        )
        if routed is not None and routed.get("ok"):
            return routed
        if routed is None:
            local = _interrupt_claude_headless_local(session_id)
            if local.get("ok"):
                return local
    if status.get("live") and status.get("pid"):
        pid = status["pid"]
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError) as e:
            return {"ok": False, "via": "headless-sigint", "pid": pid, "error": str(e)}
        return {
            "ok": True,
            "via": "headless-sigint",
            "pid": pid,
            "note": "headless process interrupted",
        }
    spawn = _core._find_live_spawn_entry_for_session(session_id)
    if spawn is not None:
        pid = spawn["pid"]
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError) as e:
            return {"ok": False, "via": "spawn-sigint", "pid": pid, "error": str(e)}
        return {
            "ok": True,
            "via": "spawn-sigint",
            "pid": pid,
            "note": "headless spawn terminated — start a new session to continue",
        }
    return {"ok": False, "error": "session is not live — nothing to interrupt"}


def _bridge_pending_inputs(session_id):
    """Public-safe snapshot of durable messages eligible for recovery retry."""
    rows = []
    with _core._pending_resume_lock:
        resume = list(_core._pending_resume_queue.get(session_id, []))
    with _core._pending_terminal_input_lock:
        terminal = list(_core._pending_terminal_input_queue.get(session_id, []))
    for queue_name, values in (("resume", resume), ("terminal", terminal)):
        for index, text in enumerate(values):
            rows.append({
                "id": f"{queue_name}:{index}",
                "queue": queue_name,
                "text": str(text or ""),
            })
    return rows


def _bridge_state_is_active(engine, state):
    if not isinstance(state, dict):
        return False
    if engine == "kimi":
        return str(state.get("status") or "").lower() == "active"
    status = str(state.get("status") or "").lower()
    return bool(
        state.get("active_turn_id")
        or state.get("ccc_turn_start_pending")
        or status == "active"
    )


def _engine_bridge_status_local(engine, session_id):
    """Describe one process-shared engine bridge without starting it."""
    engine = str(engine or "").strip().lower()
    sid = str(session_id or "").strip()
    if engine == "kimi":
        with _core._ACP_LOCK:
            _core._acp_load_state("kimi")
            conn = _core._ACP_CONNS.get("kimi") or {}
            transport = conn.get("transport")
            sessions = dict(_core._ACP_SESSION_STATE.get("kimi") or {})
            active = [
                other_sid for other_sid, state in sessions.items()
                if _bridge_state_is_active("kimi", state)
            ]
            proc = getattr(transport, "proc", None)
            pid = getattr(proc, "pid", None)
            live = bool(
                conn.get("initialized")
                and transport is not None
                and transport.alive()
            )
        return {
            "ok": True,
            "engine": "kimi",
            "bridge": "Kimi ACP",
            "transport": "acp",
            "live": live,
            "pid": pid,
            "active_session_ids": active,
            "other_active_session_ids": [value for value in active if value != sid],
            "shared": True,
            "owned": True,
        }
    if engine == "codex":
        # One thread/list call refreshes every thread known by the bridge.
        if _core._codex_app_server_is_live() and sid:
            _core._codex_app_server_refresh_thread_status(sid, max_age=0)
        with _core._CODEX_APP_SERVER_LOCK:
            transport = _core._CODEX_APP_SERVER_TRANSPORT
            sessions = dict(_core._CODEX_APP_SERVER_THREAD_STATE)
            active = [
                other_sid for other_sid, state in sessions.items()
                if _bridge_state_is_active("codex", state)
            ]
            active_sessions = []
            for other_sid in active:
                state = sessions.get(other_sid) or {}
                needs_approval = _core._codex_app_server_thread_needs_approval(state)
                active_sessions.append({
                    "session_id": other_sid,
                    "needs_approval": needs_approval,
                    "needs_approval_message": (
                        _core._codex_app_server_thread_approval_message(state)
                        if needs_approval else ""
                    ),
                })
            proc = getattr(transport, "proc", None)
            pid = getattr(proc, "pid", None)
            live = bool(
                transport is not None
                and transport.alive()
                and _core._CODEX_APP_SERVER_INITIALIZED
            )
            kind = _core._codex_app_server_transport_kind()
        return {
            "ok": True,
            "engine": "codex",
            "bridge": "Codex app-server",
            "transport": kind or "exec",
            "live": live,
            "pid": pid,
            "active_session_ids": active,
            "active_sessions": active_sessions,
            "other_active_session_ids": [value for value in active if value != sid],
            "shared": True,
            # Managed transport is an external daemon: CCC can reconnect its
            # client but must not kill a process it does not own.
            "owned": kind != "managed",
        }
    return {
        "ok": False,
        "code": "unsupported_engine",
        "error": "Bridge recovery is available for Codex and Kimi sessions",
    }


def _engine_bridge_status(session_id):
    sid = str(session_id or "").strip()
    engine = _core._detect_session_engine(sid)
    if engine not in ("codex", "kimi"):
        return _core._engine_bridge_status_local(engine, sid)
    routed = _core._control_plane_engine_call(
        engine, "bridge_status", {"session_id": sid}, mutate=False,
    )
    if isinstance(routed, dict) and routed.get("engine"):
        status = routed
    elif isinstance(routed, dict) and (
        routed.get("available") or routed.get("ambiguous")
    ):
        status = routed
    else:
        worker_health = _core._control_plane_request("health")
        worker_caps = ((worker_health.get("worker") or {}).get("capabilities") or [])
        if worker_health.get("ok") and "engine-execution-v1" in worker_caps:
            status = {
                "ok": False,
                "code": "worker_upgrade_required",
                "error": "The running CCC worker must be restarted before bridge recovery is available",
            }
        else:
            status = _core._engine_bridge_status_local(engine, sid)
    status = dict(status or {})
    status["queued_messages"] = _bridge_pending_inputs(sid)
    status["session_id"] = sid
    status["can_restart"] = not bool(status.get("other_active_session_ids"))
    if not status["can_restart"]:
        status["blocked_reason"] = "Other sessions are actively using this shared bridge"
    elif status.get("transport") == "managed":
        status["restart_note"] = (
            "CCC will reconnect to the managed Codex app-server; it will not "
            "terminate the externally owned daemon."
        )
    return status


def _engine_bridge_approval_blockers():
    """Return formal approvals held by worker-owned shared engine bridges."""
    blockers = []
    for engine in ("codex", "kimi"):
        routed = _core._control_plane_engine_call(
            engine, "bridge_status", {"session_id": ""}, mutate=False,
        )
        status = routed if isinstance(routed, dict) and routed.get("engine") else None
        if status is None:
            status = _core._engine_bridge_status_local(engine, "")
        for row in status.get("active_sessions") or []:
            if not isinstance(row, dict) or not row.get("needs_approval"):
                continue
            sid = str(row.get("session_id") or "").strip()
            if not sid:
                continue
            blockers.append({
                "session_id": sid,
                "engine": engine,
                "needs_approval": True,
                "needs_approval_message": str(
                    row.get("needs_approval_message")
                    or f"{engine.title()} is waiting for approval"
                ),
            })
    return {"ok": True, "sessions": blockers}


def _wait_then_kill_process(proc, timeout=2.0):
    """Wait for a bridge child to exit, then force only that known child."""
    if proc is None:
        return {"exited": True, "forced": False}
    deadline = time.time() + max(0.0, float(timeout))
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    forced = False
    if proc.poll() is None:
        forced = True
        try:
            proc.kill()
        except OSError:
            return {"exited": False, "forced": True}
        try:
            proc.wait(timeout=1)
        except (_core.subprocess.TimeoutExpired, OSError):
            pass
    return {"exited": proc.poll() is not None, "forced": forced}


def _restart_engine_bridge_local(engine, session_id):
    """Restart/reconnect one shared bridge after a same-process safety check."""
    engine = str(engine or "").strip().lower()
    sid = str(session_id or "").strip()
    lock = _core._ACP_RECOVERY_LOCKS.setdefault(engine, threading.Lock())
    if not lock.acquire(blocking=False):
        return {
            "ok": False,
            "code": "recovery_in_progress",
            "error": f"{engine} bridge recovery is already in progress",
        }
    try:
        before = _core._engine_bridge_status_local(engine, sid)
        other_active = list(before.get("other_active_session_ids") or [])
        if other_active:
            return {
                "ok": False,
                "code": "bridge_in_use",
                "error": "Other sessions are actively using this shared bridge",
                "other_active_session_ids": other_active,
                "bridge": before.get("bridge"),
            }
        if engine == "kimi":
            # Ask nicely first. The recovery path remains useful precisely when
            # the notification is ignored, so process shutdown is still bounded.
            _core._acp_cancel("kimi", sid)
            with _core._ACP_LOCK:
                conn = _core._ACP_CONNS.pop("kimi", None)
                pending = _core._ACP_PENDING.pop("kimi", {})
                target = (_core._ACP_SESSION_STATE.get("kimi") or {}).get(sid)
                if target is not None:
                    target["status"] = "idle"
                    target["active_turn"] = None
                    target["loaded_conn"] = None
                    target["pending_permissions"] = {}
                    _core._acp_save_state_unlocked("kimi")
                for entry in pending.values():
                    entry["response"] = {
                        "error": {
                            "code": -32003,
                            "message": "Kimi ACP restarted by operator",
                        },
                    }
                    entry["event"].set()
                _core._ACP_LOCK.notify_all()
            transport = (conn or {}).get("transport")
            proc = getattr(transport, "proc", None)
            if transport is not None:
                transport.close()
            stop = _wait_then_kill_process(proc)
            forced = bool(stop.get("forced"))
            old_reader = (conn or {}).get("reader")
            if old_reader is not None and old_reader is not threading.current_thread():
                old_reader.join(timeout=1)
            _core._KIMI_WIRE_BUSY_SUPPRESS_UNTIL[sid] = time.time() + 60
            if _core._acp_ensure("kimi") is None:
                return {
                    "ok": False,
                    "code": "bridge_restart_failed",
                    "error": _core._acp_conn_error("kimi"),
                }
            attach_error = _core._acp_ensure_session_loaded("kimi", sid)
            if attach_error is not None:
                return {
                    "ok": False,
                    "code": "bridge_reattach_failed",
                    "error": attach_error.get("error") or "Could not reattach Kimi session",
                }
            after = _core._engine_bridge_status_local("kimi", sid)
            return {
                "ok": True,
                "engine": "kimi",
                "bridge": "Kimi ACP",
                "restarted": True,
                "reattached": True,
                "old_pid": before.get("pid"),
                "pid": after.get("pid"),
                "forced": forced,
                "transport": "acp",
            }
        if engine == "codex":
            # Interrupt the selected thread before dropping our transport. This
            # is especially important for the managed daemon, which CCC does
            # not own and therefore only reconnects.
            _core._codex_interrupt_via_app_server(sid)
            with _core._CODEX_APP_SERVER_LOCK:
                transport = _core._CODEX_APP_SERVER_TRANSPORT
                proc = getattr(transport, "proc", None)
            _core._codex_app_server_shutdown()
            if proc is not None:
                _wait_then_kill_process(proc)
            if _core._ensure_codex_app_server() is None:
                return {
                    "ok": False,
                    "code": "bridge_restart_failed",
                    "error": "Codex app-server did not restart",
                }
            # Authoritative resume reconciles stale local active markers and
            # reattaches this exact thread to the fresh client connection.
            still_active = _core._codex_app_server_thread_is_active(
                sid, start_if_needed=True,
            )
            after = _core._engine_bridge_status_local("codex", sid)
            return {
                "ok": True,
                "engine": "codex",
                "bridge": "Codex app-server",
                "restarted": True,
                "reattached": True,
                "old_pid": before.get("pid"),
                "pid": after.get("pid"),
                "transport": after.get("transport"),
                "reconnected": before.get("transport") == "managed",
                "target_still_active": bool(still_active),
            }
        return before
    finally:
        lock.release()


def _recover_engine_bridge(session_id, selected_text="", idempotency_key=None):
    """Restart the owning bridge and optionally retry one durable queue row."""
    sid = str(session_id or "").strip()
    text = str(selected_text or "")
    engine = _core._detect_session_engine(sid)
    if engine not in ("codex", "kimi"):
        return _core._engine_bridge_status_local(engine, sid)
    if text and not any(row["text"] == text for row in _bridge_pending_inputs(sid)):
        return {
            "ok": False,
            "code": "queued_message_missing",
            "error": "The selected queued message no longer exists",
        }
    restarted = _core._control_plane_engine_call(
        engine,
        "bridge_restart",
        {"session_id": sid},
        idempotency_key=idempotency_key,
    )
    if not isinstance(restarted, dict) or not restarted.get("engine"):
        if isinstance(restarted, dict) and (
            restarted.get("available") or restarted.get("ambiguous")
        ):
            return restarted
        worker_health = _core._control_plane_request("health")
        worker_caps = ((worker_health.get("worker") or {}).get("capabilities") or [])
        if worker_health.get("ok") and "engine-execution-v1" in worker_caps:
            return {
                "ok": False,
                "code": "worker_upgrade_required",
                "error": "Restart the CCC worker before using bridge recovery",
            }
        restarted = _core._restart_engine_bridge_local(engine, sid)
    if not restarted.get("ok"):
        return restarted
    result = {
        "ok": True,
        "session_id": sid,
        "engine": engine,
        "restart": restarted,
        "retried": False,
    }
    if not text:
        return result
    if not _core._consume_matching_pending_input(sid, text):
        return {
            "ok": False,
            "code": "queued_message_missing",
            "error": "The selected queued message was already consumed",
            "restart": restarted,
        }
    _core._pending_terminal_retry_after.pop(sid, None)
    retry = _core._inject_text_into_session(
        sid,
        text,
        _from_terminal_queue=True,
        skip_wt=True,
        source="engine-bridge-recover",
    )
    if not isinstance(retry, dict):
        retry = {"ok": False, "error": "Retry returned no result"}
    if not retry.get("ok") and not retry.get("queued") and not retry.get("blocked"):
        _core._requeue_terminal_input_front(sid, text)
        _core._mark_terminal_queue_retry(sid, delay=5.0)
        retry["requeued"] = True
    result["retry"] = retry
    result["retried"] = bool(retry.get("ok"))
    result["queued"] = bool(retry.get("queued"))
    if not retry.get("ok"):
        result["ok"] = False
        result["error"] = retry.get("error") or "Bridge restarted but message retry failed"
    return result


def _iso_to_epoch(ts):
    """Parse a Claude-style ISO-8601 timestamp ("2026-04-26T23:22:56.738Z")
    to an epoch float. Returns None on parse failure — callers treat that
    as "skip the timestamp gate" rather than failing the request."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        # Numeric epoch (Kimi state.json writes int ms): anything past 1e12
        # is milliseconds (1e12 s would be ~year 33658).
        v = float(ts)
        return v / 1000.0 if v > 1e12 else v
    try:
        # datetime.fromisoformat handles "+00:00" but not "Z" before py3.11.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def ask_session_via_live_tail(session_id, text, timeout_ms, status, peer_sender_sid=None):
    """Inject into a LIVE session via the existing TTY-keystroke path
    (same as /api/inject-input takes), then tail the session's .jsonl
    transcript for the assistant's reply. No `claude --resume` subprocess
    is spawned — the live session does the work, so cost and tokens are
    a fraction of the resume-headless path.

    `status` is the dict returned by `session_live_status()` and must
    have `live=True` plus a `tty`. Caller is expected to have checked.

    Tries the target's Claude Code peer socket (`_try_uds_peer_delivery`)
    before falling back to keystroke injection: a transcript-confirmed UDS
    delivery skips the keystroke entirely. `peer_sender_sid` is the CCC
    session id of the asker, if any, so the peer message can be attributed.

    Returns the same shape as `ask_session_via_resume`, with
    `source="live-tail"` and `cost_usd=None` (the live process doesn't
    expose its API cost back to us — that's the price of skipping the
    resume). Success results also carry `"via"`, `"uds"` or `"keystroke"`,
    naming which transport carried the ask.
    """
    tty = status.get("tty")
    term_app = status.get("terminal_app") or "Terminal"
    if not tty:
        return {"ok": False, "error": "live session has no tty", "source": "live-tail"}

    jsonl_path = _core._find_session_jsonl(session_id)
    if jsonl_path is None:
        return {
            "ok": False,
            "error": "no transcript .jsonl on disk for this session_id",
            "source": "live-tail",
        }

    # Snapshot file size BEFORE inject so we only scan bytes the live session
    # writes in response to this ask. Capture inject epoch with a 1s back-buffer
    # to absorb any clock skew between this process and Claude's writer.
    try:
        start_offset = jsonl_path.stat().st_size
    except OSError as e:
        return {
            "ok": False,
            "error": f"could not stat jsonl: {e}",
            "source": "live-tail",
        }
    inject_epoch = time.time() - 1.0

    via = "keystroke"
    inject = _core._try_uds_peer_delivery(session_id, text, source="ask", peer_sender_sid=peer_sender_sid)
    if inject is not None:
        via = "uds"
    else:
        inject = _core.inject_input_via_keystroke(tty, term_app, text)
    if not inject.get("ok"):
        return {
            "ok": False,
            "error": f"keystroke inject failed: {inject.get('error')}",
            "source": "live-tail",
        }

    result = _core._ask_live_tail_wait_for_reply(session_id, jsonl_path, start_offset, inject_epoch, timeout_ms)
    if result.get("ok"):
        result["via"] = via
    return result


def _ask_live_tail_wait_for_reply(session_id, jsonl_path, start_offset, inject_epoch, timeout_ms):
    """Tail `jsonl_path` from `start_offset` for the assistant's reply to an
    ask that was just injected into `session_id`. Extracted from
    `ask_session_via_live_tail` so the wait loop can be stubbed independently
    of how the ask was injected (keystroke vs peer socket).

    Returns the same success/timeout/error dict shapes the inline loop in
    `ask_session_via_live_tail` used to return directly.
    """
    started = time.monotonic()
    deadline = started + max(0.5, timeout_ms / 1000.0)
    text_blocks = []
    last_event_at = started
    last_block_was_tool_use = False
    saw_text = False
    pending = b""
    # Idle fallback: if stop_reason is never written (older Claude Code
    # versions log assistant records with stop_reason=None), accept silence
    # as "turn over" once we've already collected some text AND the most
    # recent record wasn't a tool_use (which would mean the assistant is
    # waiting on a tool_result).
    IDLE_DONE_SECS = 3.0

    fh = None
    try:
        try:
            fh = open(jsonl_path, "rb")
        except OSError as e:
            return {
                "ok": False,
                "error": f"could not open jsonl: {e}",
                "source": "live-tail",
            }
        fh.seek(start_offset)

        while time.monotonic() < deadline:
            chunk = fh.read()
            if chunk:
                last_event_at = time.monotonic()
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(ev, dict) or ev.get("type") != "assistant":
                        continue
                    ts_epoch = _iso_to_epoch(ev.get("timestamp"))
                    if ts_epoch is not None and ts_epoch < inject_epoch:
                        # Old turn — pre-inject record the writer flushed late.
                        continue
                    msg = ev.get("message") or {}
                    blocks = msg.get("content") or []
                    record_has_tool_use = False
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            t = block.get("text") or ""
                            if t:
                                text_blocks.append(t)
                                saw_text = True
                        elif btype == "tool_use":
                            record_has_tool_use = True
                    last_block_was_tool_use = record_has_tool_use
                    # Definitive "turn done" signal in newer Claude Code versions.
                    stop_reason = msg.get("stop_reason")
                    if stop_reason in ("end_turn", "stop_sequence", "max_tokens"):
                        return {
                            "ok": True,
                            "text": "".join(text_blocks),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "num_turns": 1,
                            "cost_usd": None,
                            "source": "live-tail",
                        }
            else:
                # Idle fallback — only kicks in once we've seen text and the
                # last record wasn't a tool_use waiting on a result.
                if (
                    saw_text
                    and not last_block_was_tool_use
                    and (time.monotonic() - last_event_at) > IDLE_DONE_SECS
                ):
                    return {
                        "ok": True,
                        "text": "".join(text_blocks),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "num_turns": 1,
                        "cost_usd": None,
                        "source": "live-tail",
                    }
                time.sleep(0.1)

        return {
            "ok": False,
            "error": "timeout",
            "partial": "".join(text_blocks),
            "source": "live-tail",
        }
    finally:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


def _text_from_engine_stream_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(_text_from_engine_stream_value(
                    item.get("text") or item.get("content") or item.get("message")
                ))
        return "".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in ("text", "content", "message", "output", "result", "last_agent_message"):
            text = _text_from_engine_stream_value(value.get(key))
            if text:
                return text
    return ""


def _engine_stream_event_text(engine, ev):
    if not isinstance(ev, dict):
        return ""
    if engine == "codex":
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if ev.get("type") == "event_msg" and payload.get("type") == "agent_message":
            return (payload.get("message") or "").strip()
        if ev.get("type") == "event_msg" and payload.get("type") == "task_complete":
            return (payload.get("last_agent_message") or payload.get("message") or "").strip()
        return ""
    if engine == "gemini":
        if ev.get("type") == "message" and ev.get("role") == "assistant":
            return _text_from_engine_stream_value(
                ev.get("content") or ev.get("message") or ev.get("text")
            ).strip()
        return ""
    if engine == "antigravity":
        if ev.get("type") == "PLANNER_RESPONSE":
            return (ev.get("content") or "").strip()
        return _text_from_engine_stream_value(
            ev.get("content") or ev.get("message") or ev.get("text")
        ).strip()
    if engine == "hermes":
        return _text_from_engine_stream_value(
            ev.get("content") or ev.get("message") or ev.get("text") or ev.get("response")
        ).strip()
    return ""


def _engine_stream_event_done(engine, ev):
    if not isinstance(ev, dict):
        return False
    if engine == "codex":
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        return ev.get("type") == "event_msg" and payload.get("type") == "task_complete"
    if engine == "gemini":
        return ev.get("type") == "result"
    return False


def _latest_resume_spawn_entry(session_id, engine):
    for s in reversed(_core._spawned_sessions):
        if s.get("engine") != engine:
            continue
        if s.get("resumed_sid") == session_id or s.get("session_id") == session_id:
            return s
    return None


def _codex_ask_wait_rollout(session_id, rollout_path, start_offset, timeout_ms, source):
    """Tail a codex thread rollout for the reply to an app-server-driven ask.

    The app-server `turn/start` runs the turn *inside* the codex thread, so
    there is no spawn subprocess / log to tail (the old code bailed here with
    "lost track of it", breaking session-to-session /ask). The assistant reply
    instead appends to the thread's rollout JSONL as an `agent_message` /
    `task_complete` event — wait for the first such event past `start_offset`
    and return its text.
    """
    if rollout_path is None:
        rollout_path = _core._resolve_codex_rollout_path(session_id)
    if rollout_path is None:
        return {
            "ok": False,
            "error": "codex turn started via app-server but no rollout file was found to read the reply",
            "source": source,
        }
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    started = time.monotonic()
    pos = max(0, int(start_offset or 0))
    pending = b""
    text_chunks = []
    while time.monotonic() < deadline:
        chunk = b""
        try:
            with open(rollout_path, "rb") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
        except OSError:
            chunk = b""
        if not chunk:
            time.sleep(0.15)
            continue
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ev = json.loads(stripped)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            piece = _engine_stream_event_text("codex", ev)
            if piece:
                text_chunks.append(piece)
                return {
                    "ok": True,
                    "text": "\n".join(text_chunks).strip(),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "num_turns": 1,
                    "cost_usd": None,
                    "source": source,
                }
    return {
        "ok": False,
        "error": "timeout",
        "partial": "\n".join(text_chunks).strip(),
        "source": source,
    }


def ask_engine_session_and_wait(session_id, text, timeout_ms, engine):
    # Codex resumes can complete via the app-server RPC, which runs the turn
    # inside the codex thread — no tracked subprocess / log to tail. Capture the
    # thread rollout position BEFORE resuming so, on that path, we can tail the
    # rollout for the fresh reply instead of bailing with "lost track of it".
    codex_rollout_path = None
    codex_rollout_start = 0
    if engine == "codex":
        codex_rollout_path = _core._resolve_codex_rollout_path(session_id)
        if codex_rollout_path is not None:
            try:
                codex_rollout_start = codex_rollout_path.stat().st_size
            except OSError:
                codex_rollout_start = 0
    if engine == "codex":
        spawn_result = _core.resume_session_codex(session_id, text)
    elif engine == "gemini":
        spawn_result = _core.resume_session_gemini(session_id, text)
    elif engine == "cursor":
        spawn_result = _core.resume_session_cursor(session_id, text)
    elif engine == "antigravity":
        spawn_result = _core.resume_session_antigravity(session_id, text)
    elif engine == "hermes":
        spawn_result = _core.resume_session_hermes(session_id, text)
    elif engine == "opencode":
        spawn_result = _core.resume_session_opencode(session_id, text)
    elif engine == "devin":
        spawn_result = _core.resume_session_devin(session_id, text)
    else:
        return {"ok": False, "error": f"unsupported ask engine: {engine}", "source": "engine-resume"}
    source = f"{engine}-resume"
    if not spawn_result.get("ok"):
        spawn_result.setdefault("source", source)
        return spawn_result
    if spawn_result.get("queued"):
        # Busy codex target: the message was queued/steered into the thread, and
        # its reply still lands in the thread rollout once the active turn
        # yields. We bookmarked the rollout offset above, so tail for the reply
        # instead of bailing — otherwise the answer is produced but has no path
        # back to the asker (the "both sessions struggling with /ask" bug: the
        # target replied, but this call had already returned "queued"). Degrades
        # to a timeout if the turn runs past the window. NOTE: if the target is
        # mid a long UNRELATED turn, the next rollout message may be that turn's
        # output rather than the answer — the asker can re-ask.
        if engine == "codex" and codex_rollout_path is not None:
            result = _codex_ask_wait_rollout(
                session_id, codex_rollout_path, codex_rollout_start, timeout_ms, source
            )
            # On timeout/failure, surface that it was a busy/queued target so the
            # asker can distinguish "no reply yet" from a real answer.
            if isinstance(result, dict) and not result.get("ok"):
                result.setdefault("queued", True)
            return result
        return {
            "ok": False,
            "error": "session is busy; message was queued but no synchronous reply is available yet",
            "queued": True,
            "pid": spawn_result.get("pid"),
            "source": source,
        }
    if spawn_result.get("via") == "antigravity-app":
        return {
            "ok": False,
            "error": "Antigravity app resume is asynchronous; synchronous /api/ask requires an AGY CLI conversation.",
            "source": source,
            "via": "antigravity-app",
        }
    entry = _latest_resume_spawn_entry(session_id, engine)
    if entry is None:
        if engine == "codex":
            # App-server path: the turn runs inside the codex thread, no spawn
            # log. The reply lands in the thread rollout — tail that instead.
            return _codex_ask_wait_rollout(
                session_id, codex_rollout_path, codex_rollout_start, timeout_ms, source
            )
        return {"ok": False, "error": "spawned subprocess but lost track of it", "source": source}

    log_path = entry.get("log")
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    text_chunks = []
    raw_chunks = []
    pending = b""
    fh = None
    started = time.monotonic()
    try:
        wait_until = time.monotonic() + 2.0
        while log_path and not os.path.exists(log_path) and time.monotonic() < wait_until:
            time.sleep(0.05)
        try:
            fh = open(log_path, "rb")
        except OSError as e:
            return {"ok": False, "error": f"could not open log: {e}", "source": source}
        while time.monotonic() < deadline:
            chunk = fh.read()
            if chunk:
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        ev = json.loads(stripped)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        raw = stripped.decode("utf-8", "replace")
                        if engine in ("antigravity", "hermes", "opencode"):
                            raw_chunks.append(raw)
                        continue
                    text_piece = _engine_stream_event_text(engine, ev)
                    if text_piece:
                        text_chunks.append(text_piece)
                    if _engine_stream_event_done(engine, ev):
                        return {
                            "ok": True,
                            "text": "\n".join(text_chunks).strip(),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "num_turns": 1,
                            "cost_usd": None,
                            "source": source,
                        }
            else:
                poll = _core._poll_spawn_entry(entry)
                if poll is not None:
                    final = fh.read()
                    if final:
                        pending += final
                    for raw_line in pending.split(b"\n"):
                        stripped = raw_line.strip()
                        if not stripped:
                            continue
                        try:
                            ev = json.loads(stripped)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            if engine in ("antigravity", "hermes", "opencode"):
                                raw_chunks.append(stripped.decode("utf-8", "replace"))
                            continue
                        text_piece = _engine_stream_event_text(engine, ev)
                        if text_piece:
                            text_chunks.append(text_piece)
                    text_out = "\n".join(text_chunks).strip()
                    if not text_out and engine in ("antigravity", "hermes", "opencode"):
                        text_out = "\n".join(raw_chunks).strip()
                    if poll == 0:
                        return {
                            "ok": True,
                            "text": text_out,
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "num_turns": 1,
                            "cost_usd": None,
                            "source": source,
                        }
                    return {
                        "ok": False,
                        "error": f"subprocess exited (code {poll}) before reply completed",
                        "partial": text_out,
                        "source": source,
                    }
                time.sleep(0.1)
        return {
            "ok": False,
            "error": "timeout",
            "partial": "\n".join(text_chunks or raw_chunks).strip(),
            "source": source,
        }
    finally:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


def ask_session_and_wait(session_id, text, timeout_ms=30000, cwd=None, peer_sender_sid=None):
    """Synchronously inject `text` into a session and wait for its reply.

    Non-claude engines route first: codex/gemini/antigravity/hermes/opencode
    go to ask_engine_session_and_wait (engine resume + stream tail); Kimi and
    Grok go to _acp_ask_and_wait (ACP session/prompt, blocks for the turn-end
    response).
    Claude sessions then pick between two paths, chosen from live status:

    - **Live target** (the user has `claude` open in a terminal for this
      session_id): inject via `inject_input_via_keystroke()` and tail the
      `.jsonl` transcript. Spawns NO `claude --resume` subprocess.
      Returns `source="live-tail"`. Cost is `None` because the live
      process doesn't expose its API cost back to us.
    - **Dormant target**: spawn `claude --resume` headlessly, write the
      user message, and tail its stream-json output for the next
      `{"type":"result",...}` event. Returns `source="resume-headless"`.

    Same return-shape contract for both:
      {"ok": True, "text": <result>, "cost_usd": <float|None>,
       "duration_ms": <int>, "num_turns": <int>, "source": <str>}
      {"ok": False, "error": "timeout", "partial": <best-effort text>, "source": <str>}
      {"ok": False, "error": <message>, "source": <str>}
    """
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}

    # Global session reference — proxy the whole ask to the owning CCC and
    # relay its reply. Bare local ids keep today's behavior.
    session_id, owner_node = _core._federation_resolve_target(session_id)
    if owner_node:
        return _core._federation_proxy_session_action(owner_node, "ask", {
            "session_id": session_id, "text": text, "timeout_ms": timeout_ms,
        }, timeout=min(660.0, max(60.0, timeout_ms / 1000.0 + 45.0)))

    session_id = _core._resolve_local_spawn_session_prefix(session_id)
    engine = _core._detect_session_engine(session_id)
    # The persistent worker owns Claude headless subprocesses, their FIFOs,
    # and the in-memory entries used to tail a result.  A dashboard process
    # cannot safely send the input there and then wait locally: its own spawn
    # list is intentionally empty after worker handoff.  Keep the entire
    # synchronous exchange in the owning process instead.
    if engine == "claude":
        routed = _core._control_plane_engine_call(
            "claude",
            "ask",
            {
                "session_id": session_id,
                "text": text,
                "timeout_ms": timeout_ms,
                "cwd": cwd,
                "peer_sender_sid": peer_sender_sid,
            },
            timeout_ms=timeout_ms,
        )
        if routed is not None:
            return routed
    if engine in ("codex", "gemini", "antigravity", "hermes", "opencode"):
        return _core.ask_engine_session_and_wait(session_id, text, timeout_ms, engine)
    if engine in ("kimi", "grok"):
        return _core._acp_ask_and_wait(engine, session_id, text, timeout_ms)

    # Live-tail short-circuit: if the target session has a running `claude`
    # process with a usable tty, drive it via keystroke + jsonl tail. This
    # skips the ~1M-token cache re-read a fresh `claude --resume` would do.
    resolved_cwd = cwd or _core.find_session_cwd(session_id)
    status = _core.session_live_status(session_id, resolved_cwd)
    if status.get("live") and status.get("tty"):
        return _core.ask_session_via_live_tail(session_id, text, timeout_ms, status, peer_sender_sid=peer_sender_sid)

    # Resume-headless path (original behaviour). Reuse an existing live
    # resume if we already have one (same path resume_session_headless takes).
    entry = None
    expected_command_uuid = None
    for s in _core._spawned_sessions:
        if (
            (s.get("resumed_sid") == session_id or s.get("session_id") == session_id)
            and _core._poll_spawn_entry(s) is None
        ):
            entry = s
            break

    if entry is None:
        # Stage 2 WatchTower messaging handover: this is the same "would
        # spawn a fresh claude --resume" decision point
        # _try_wt_send_for_headless_delivery guards on the fire-and-forget
        # side. `engine` narrowed to codex/gemini/antigravity above, but
        # cursor/hermes/kilo sessions can still reach this branch — gate
        # explicitly on "claude" rather than assume by elimination.
        if engine == "claude":
            wt_result = _core._try_wt_ask_for_headless_delivery(session_id, text, timeout_ms)
            if wt_result is not None:
                return wt_result
        # No live subprocess — spawn one. resume_session_headless writes
        # the user message itself and appends the entry to _spawned_sessions.
        spawn_result = _core.resume_session_headless(session_id, text, cwd=cwd)
        if not spawn_result.get("ok"):
            spawn_result.setdefault("source", "resume-headless")
            return spawn_result
        # The brand new entry is the last one matching this sid.
        for s in reversed(_core._spawned_sessions):
            if s.get("resumed_sid") == session_id or s.get("session_id") == session_id:
                entry = s
                break
        if entry is None:
            return {
                "ok": False,
                "error": "spawned subprocess but lost track of it",
                "source": "resume-headless",
            }
        # Fresh spawn — start scanning from byte 0 since the only output
        # in this log will be from this ask.
        start_offset = 0
    else:
        # Live subprocess — record where the log is *now* before writing
        # so we don't pick up a previous turn's result event.
        try:
            start_offset = os.path.getsize(entry["log"])
        except OSError:
            start_offset = 0
        prior_command_uuids = set(_core._valid_input_command_uuids(entry) or [])
        ok = _core._write_stream_json_user_message(entry, text)
        if not ok:
            return {
                "ok": False,
                "error": "failed to write user message (broken pipe?)",
                "source": "resume-headless",
            }
        written_command_uuids = _core._valid_input_command_uuids(entry) or []
        new_command_uuids = [
            value for value in written_command_uuids
            if value not in prior_command_uuids
        ]
        if len(new_command_uuids) == 1:
            expected_command_uuid = new_command_uuids[0]

    log_path = entry["log"]
    proc = entry["proc"]
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    partial_chunks = []
    pending = b""
    fh = None
    try:
        # The log file may not exist yet for a brand-new spawn (race with
        # the subprocess opening its stdout). Wait briefly for it.
        wait_until = time.monotonic() + 2.0
        while not os.path.exists(log_path) and time.monotonic() < wait_until:
            time.sleep(0.05)
        try:
            fh = open(log_path, "rb")
        except OSError as e:
            return {
                "ok": False,
                "error": f"could not open log: {e}",
                "source": "resume-headless",
            }
        fh.seek(start_offset)
        while time.monotonic() < deadline:
            chunk = fh.read()
            if chunk:
                pending += chunk
                # Process complete lines; keep any trailing partial in `pending`.
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(ev, dict):
                        continue
                    ev_type = ev.get("type")
                    if ev_type == "assistant":
                        # Best-effort partial text accumulation for timeouts.
                        msg = ev.get("message") or {}
                        for block in msg.get("content") or []:
                            if isinstance(block, dict) and block.get("type") == "text":
                                t = block.get("text") or ""
                                if t:
                                    partial_chunks.append(t)
                    elif ev_type == "result":
                        # A reused headless can finish an older active turn
                        # after we append this ask. Its result is not an
                        # answer to the new request: Claude supplies the
                        # accepted input UUID on the matching terminal event.
                        # Older stream-json versions omit that field, so keep
                        # the historical next-result fallback for those logs.
                        result_command_uuid = ev.get("user_message_uuid")
                        if (
                            expected_command_uuid
                            and result_command_uuid
                            and result_command_uuid != expected_command_uuid
                        ):
                            continue
                        return {
                            "ok": True,
                            "text": ev.get("result") or "",
                            "cost_usd": ev.get("total_cost_usd"),
                            "duration_ms": ev.get("duration_ms"),
                            "num_turns": ev.get("num_turns"),
                            "is_error": bool(ev.get("is_error")),
                            "source": "resume-headless",
                        }
            else:
                # No new data — short sleep, then check if subprocess died.
                poll = _core._poll_spawn_entry(entry)
                if poll is not None:
                    # Drain anything left and bail.
                    final = fh.read()
                    if final:
                        pending += final
                        # Try to parse one more time
                        for raw in pending.split(b"\n"):
                            raw = raw.strip()
                            if not raw:
                                continue
                            try:
                                ev = json.loads(raw)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue
                            if isinstance(ev, dict) and ev.get("type") == "result":
                                result_command_uuid = ev.get("user_message_uuid")
                                if (
                                    expected_command_uuid
                                    and result_command_uuid
                                    and result_command_uuid != expected_command_uuid
                                ):
                                    continue
                                return {
                                    "ok": True,
                                    "text": ev.get("result") or "",
                                    "cost_usd": ev.get("total_cost_usd"),
                                    "duration_ms": ev.get("duration_ms"),
                                    "num_turns": ev.get("num_turns"),
                                    "is_error": bool(ev.get("is_error")),
                                    "source": "resume-headless",
                                }
                    return {
                        "ok": False,
                        "error": f"subprocess exited (code {poll}) before result event",
                        "partial": "".join(partial_chunks),
                        "source": "resume-headless",
                    }
                time.sleep(0.1)
        return {
            "ok": False,
            "error": "timeout",
            "partial": "".join(partial_chunks),
            "source": "resume-headless",
        }
    finally:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
