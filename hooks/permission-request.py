#!/usr/bin/env python3
"""PermissionRequest hook: lets a cloud agent answer ONE specific prompt.

Claude Code runs this when a permission dialog would open. The hook:

  1. exits at once (no output = normal prompt) unless the CCC cloud relay is
     running and asked for approvals (fresh `enabled` file);
  2. hashes the exact tool call it was given (same canonical JSON as
     ccc_server/relay_context.canonical_input);
  3. parks for a short window, watching for a decision file the relay writes
     after the cloud approved/denied that exact hash;
  4. returns allow/deny only when the decision's hash equals its own hash, it
     has not expired (120 s) and it can be claimed atomically (single use);
  5. stops parking as soon as the prompt is answered some other way (the
     transcript gets the tool_result for this call) or the session exits.

Anything else (no decision, wrong hash, expired, error) prints nothing, so the
normal prompt decides. Never prompts. Stdlib only. Keep the file-name scheme
and canonical form in lockstep with ccc_server/approval_hook.py.

While it parks it also raises the session's `_needs_approval` badge itself.
Claude Code fires the Notification hook (the usual badge source) only once a
dialog is actually shown, and a headless `claude -p` session (CCC worker
lanes) never shows one, so before this the session sat blocked for the whole
wait with no badge, and neither the dashboard nor the relay offered an answer.
A headless session parks even when the relay is off: there is no dialog for
the prompt to fall back to (it is simply denied), so the dashboard's
Approve/Deny delivered through this hook is the only way to answer it.
"""

import hashlib
import json
import os
import re
import sys
import time

ENABLED_FRESH_S = 90
POLL_S = 0.2
HEARTBEAT_S = 2.0
RESOLVED_CHECK_S = 1.0
TAIL_BYTES = 512 * 1024
SKIP_TOOLS = ("AskUserQuestion", "ExitPlanMode")
# Headless + relay off: only a person at the dashboard can answer, so hold the
# prompt for a while but not the relay's full 15 min; an unattended lane gets
# its denial and carries on.
HEADLESS_WAIT_S = 300.0
_SID_RE = re.compile(r"[A-Za-z0-9_\-]{1,100}")


def base_dir():
    return os.environ.get("CCC_APPROVAL_HOOK_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center", "cloud-approvals")


def max_wait():
    try:
        return max(0.0, float(os.environ.get("CCC_APPROVAL_HOOK_WAIT", "900")))
    except ValueError:
        return 900.0


def canonical_input(tool, tin):
    return json.dumps({"tool": str(tool or ""), "input": tin},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_sha256(tool, tin):
    return hashlib.sha256(canonical_input(tool, tin).encode("utf-8")).hexdigest()


def enabled(base, now):
    try:
        return now - os.stat(os.path.join(base, "enabled")).st_mtime <= ENABLED_FRESH_S
    except OSError:
        return False


def headless_parent(pid=None, depth=4):
    """True when an ancestor `claude` process runs in print mode (`-p`), i.e.
    no permission dialog will ever open. Linux /proc only; False elsewhere.
    CCC_APPROVAL_HOOK_HEADLESS=0/1 overrides (tests run under agent shells)."""
    forced = os.environ.get("CCC_APPROVAL_HOOK_HEADLESS")
    if forced in ("0", "1"):
        return forced == "1"
    pid = os.getppid() if pid is None else pid
    for _ in range(depth):
        if not pid or pid <= 1:
            return False
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                argv = [a.decode("utf-8", "replace")
                        for a in f.read().split(b"\0") if a]
            with open("/proc/%d/stat" % pid) as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if argv and os.path.basename(argv[0]) == "claude":
            return "-p" in argv or "--print" in argv
        pid = ppid
    return False


def live_dir():
    return os.environ.get("CCC_LIVE_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center", "live-state")


_SECRET_RE = re.compile(r"(?i)\b([A-Z0-9_]*(?:PASS(?:WORD)?|PW|PWD|TOKEN|SECRET|KEY|CODE)[A-Z0-9_]*)=\S+")


def describe_call(tool, tin, limit=240):
    """One-line, secret-scrubbed hint of what the tool call does, for the
    approval card. Never raises."""
    try:
        if not isinstance(tin, dict):
            return ""
        if tool == "Bash":
            cmd = str(tin.get("command") or "").strip()
            lines = [ln for ln in cmd.splitlines() if ln.strip()]
            if not lines:
                return ""
            first = _SECRET_RE.sub(r"\1=[redacted]", lines[0].strip())
            extra = len(lines) - 1
            text = first[:limit] + ("\u2026" if len(first) > limit else "")
            if extra > 0:
                text += " (+%d more line%s)" % (extra, "" if extra == 1 else "s")
            return text
        for k in ("file_path", "path", "url", "pattern", "query"):
            if tin.get(k):
                return str(tin[k])[:limit]
    except Exception:
        pass
    return ""


def raise_badge(sid, tool, tin=None):
    """Write the `_needs_approval` marker the dashboard and relay read, unless
    one is already there. Returns the marker path when this hook owns it."""
    path = os.path.join(live_dir(), "%s_needs_approval.json" % sid)
    if os.path.exists(path):
        return None
    try:
        os.makedirs(live_dir(), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"session_id": sid, "type": "permission_prompt",
                       "message": "Claude needs your permission to use %s%s" % (
                           tool, (": " + describe_call(tool, tin)) if describe_call(tool, tin) else ""),
                       "started_at": time.time(), "source": "permission-request",
                       "pid": os.getpid(), "sha256": input_sha256(tool, tin)}, f)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def drop_badge(path, base, sid):
    """Remove the marker this hook raised, unless another hook of the same
    session is still parked (its waiting file is fresh) or it was replaced."""
    if not path:
        return
    try:
        for name in os.listdir(os.path.join(base, "waiting")):
            if name.startswith(sid + "."):
                return
    except OSError:
        pass
    try:
        with open(path) as f:
            doc = json.load(f)
        if doc.get("source") == "permission-request" and doc.get("pid") == os.getpid():
            os.unlink(path)
    except (OSError, ValueError, AttributeError):
        pass


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def take_decision(path, want_sha, now):
    """Claim the decision file atomically. Returns "allow"/"deny" or None.
    A file whose hash differs from `want_sha` is left alone (it is not ours to
    consume); an expired or unreadable one is removed."""
    try:
        with open(path) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _unlink(path)
        return None
    if not isinstance(doc, dict):
        _unlink(path)
        return None
    try:
        expires = float(doc.get("expires"))
    except (TypeError, ValueError):
        expires = 0.0
    if expires <= now:
        _unlink(path)
        return None
    if doc.get("sha256") != want_sha or doc.get("decision") not in ("allow", "deny"):
        return None
    claim = "%s.claim.%d" % (path, os.getpid())
    try:
        os.rename(path, claim)          # atomic: exactly one claimant wins
    except OSError:
        return None
    _unlink(claim)
    return doc["decision"]


def emit(decision):
    if decision == "allow":
        body = {"behavior": "allow"}
    else:
        body = {"behavior": "deny",
                "message": "Denied by an approved cloud agent via Claude Command Center."}
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": body}}))


class PromptWatch:
    """Tells when the prompt this hook parked on was answered elsewhere.

    Reads the session transcript incrementally. The pending call is the last
    tool_use with this exact name+input that has no tool_result yet; once a
    tool_result for its id is appended, the prompt is resolved. No transcript
    or no match means "never resolved" (the deadline still bounds the wait)."""

    def __init__(self, path, tool, tin):
        self.path, self.tool, self.tin = path, tool, tin
        self.pending = None
        self.rest = b""
        self.ppid = os.getppid()
        try:
            size = os.path.getsize(path) if path else 0
        except OSError:
            size = 0
        self.start = size
        self.offset = max(0, size - TAIL_BYTES)
        self.skip_first = self.offset > 0
        self._scan(initial=True)

    def _scan(self, initial=False):
        if not self.path:
            return False
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return False
        self.offset += len(chunk)
        lines = (self.rest + chunk).split(b"\n")
        self.rest = lines.pop()
        if self.skip_first and lines:
            lines.pop(0)                # partial line at the tail cut
            self.skip_first = False
        for raw in lines:
            if self._line(raw) and not initial:
                return True
        return False

    def _line(self, raw):
        try:
            content = (json.loads(raw).get("message") or {}).get("content")
        except (ValueError, AttributeError):
            return False
        if not isinstance(content, list):
            return False
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == self.tool \
                    and b.get("input") == self.tin:
                self.pending = b.get("id")
            elif b.get("type") == "tool_result" and self.pending \
                    and b.get("tool_use_id") == self.pending:
                self.pending = None     # an earlier identical call; or ours
                return True
        return False

    def resolved(self):
        if os.getppid() != self.ppid:
            return True                 # the session exited
        return self._scan()


def wait_for_decision(base, sid, sha, deadline, sleep=time.sleep, clock=time.time,
                      watch=None):
    name = "%s.%s.json" % (sid, sha[:16])
    wpath = os.path.join(base, "waiting", name)
    dpath = os.path.join(base, "decisions", name)
    try:
        os.makedirs(os.path.dirname(wpath), mode=0o700, exist_ok=True)
        tmp = "%s.%d.tmp" % (wpath, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"sha256": sha, "pid": os.getpid(), "ts": clock()}, f)
        os.replace(tmp, wpath)
    except OSError:
        return None
    last_beat = last_check = clock()
    try:
        while True:
            now = clock()
            decision = take_decision(dpath, sha, now)
            if decision:
                return decision
            if now >= deadline:
                return None
            if watch is not None and now - last_check >= RESOLVED_CHECK_S:
                last_check = now
                if watch.resolved():
                    return None
            if now - last_beat >= HEARTBEAT_S:
                try:
                    os.utime(wpath, None)
                except OSError:
                    pass
                last_beat = now
            sleep(POLL_S)
    finally:
        _unlink(wpath)


def record_ask(sid, tool, tin, transcript_path):
    """Note this pending prompt so PostToolUse of a DIFFERENT tool can't clear
    the session's needs-approval badge while this one still waits (CCC-245)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _asks
        _asks.record(os.path.join(os.path.expanduser("~"), ".claude",
                                  "command-center", "live-state"),
                     sid, tool, tin, transcript_path)
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read())
        sid = str(data.get("session_id") or "")
        tool = str(data.get("tool_name") or "")
        tin = data.get("tool_input")
        if not _SID_RE.fullmatch(sid) or not tool or not isinstance(tin, dict):
            return
        record_ask(sid, tool, tin, str(data.get("transcript_path") or ""))
        if tool in SKIP_TOOLS:
            return
        base = base_dir()
        now = time.time()
        relay = enabled(base, now)
        if not (relay or headless_parent()):
            return
        wait = max_wait() if relay else min(max_wait(), HEADLESS_WAIT_S)
        if wait <= 0:
            return
        watch = PromptWatch(str(data.get("transcript_path") or ""), tool, tin)
        badge = raise_badge(sid, tool, tin)
        try:
            decision = wait_for_decision(base, sid, input_sha256(tool, tin),
                                         now + wait, watch=watch)
        finally:
            drop_badge(badge, base, sid)
        if decision:
            emit(decision)
    except Exception:
        pass            # fall through to the normal prompt


if __name__ == "__main__":
    main()
