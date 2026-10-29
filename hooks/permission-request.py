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


def main():
    try:
        data = json.loads(sys.stdin.read())
        sid = str(data.get("session_id") or "")
        tool = str(data.get("tool_name") or "")
        tin = data.get("tool_input")
        if not _SID_RE.fullmatch(sid) or not tool or tool in SKIP_TOOLS \
                or not isinstance(tin, dict):
            return
        base = base_dir()
        now = time.time()
        if not enabled(base, now):
            return
        wait = max_wait()
        if wait <= 0:
            return
        watch = PromptWatch(str(data.get("transcript_path") or ""), tool, tin)
        decision = wait_for_decision(base, sid, input_sha256(tool, tin), now + wait,
                                     watch=watch)
        if decision:
            emit(decision)
    except Exception:
        pass            # fall through to the normal prompt


if __name__ == "__main__":
    main()
