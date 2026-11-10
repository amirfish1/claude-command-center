# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""The WatchTower calls On-call makes, in one place.

On-call never imports WatchTower: it runs the ``wt`` command, so it works with
whatever version is installed and tests can swap this one class for a fake.
One process per call, never one per ticket.
"""

from __future__ import annotations

import json
import re
import subprocess

_FILED_RE = re.compile(r"^FILED:\s+(\S+)", re.M)
REF_RE = re.compile(r"\b([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*-\d+)\b")
MINOR_CLASS = "code-change-revertable"


class WTError(Exception):
    """A WatchTower call failed. The message is safe to show the person."""


def _argv():
    from ccc_server import assistant_actions
    return assistant_actions.wt_argv()


class WT:
    def __init__(self, argv=None, *, run=None, timeout=60, env=None, dispatch=True):
        self._argv = list(argv) if argv else None
        self._dispatch = dispatch
        self._run = run or subprocess.run
        self._timeout = timeout
        self._env = env

    # -- plumbing --
    def argv(self):
        if self._argv is None:
            self._argv = _argv() or []
        return self._argv

    def available(self):
        return bool(self.argv())

    def _call(self, *args, timeout=None):
        base = self.argv()
        if not base:
            raise WTError("WatchTower is not installed. Install it, then run ccc oncall setup again.")
        try:
            proc = self._run(base + [str(a) for a in args], capture_output=True, text=True,
                             timeout=timeout or self._timeout, env=self._env)
        except subprocess.TimeoutExpired:
            raise WTError(f"WatchTower did not answer in time (wt {args[0]}).")
        except OSError as exc:
            raise WTError(f"Could not run WatchTower: {exc}")
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _ok(self, *args, timeout=None):
        code, out, err = self._call(*args, timeout=timeout)
        if code != 0:
            raise WTError(_last_line(err) or _last_line(out) or f"wt {args[0]} failed")
        return out

    def _json(self, *args):
        out = self._ok(*args)
        try:
            return json.loads(out)
        except ValueError:
            raise WTError(f"wt {args[0]} returned something that is not JSON.")

    # -- tickets --
    def add(self, queue, *, title, text, dedupe_key="", url="", kind="bug"):
        """File a ticket (or reuse the one with this dedupe key). Returns its ref."""
        args = ["add", "-q", queue, "--title", title[:200], "--text", text, "--type", kind]
        if dedupe_key:
            args += ["--dedupe-key", dedupe_key]
        if url:
            args += ["--url", url]
        if not self._dispatch:
            args.append("--no-dispatch")
        out = self._ok(*args)
        m = _FILED_RE.search(out)
        if not m:
            raise WTError("WatchTower filed the ticket but did not print its ref.")
        return m.group(1)

    def blocked(self, queue):
        """Tickets in ``queue`` parked on a question."""
        data = self._json("blocked", "-q", queue, "--json")
        return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []

    def ls(self, queue, status="active", limit=200):
        data = self._json("ls", "-q", queue, "--status", status, "--limit", limit, "--json")
        return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []

    def find(self, ref):
        code, out, _err = self._call("find", ref, "--json")
        if code != 0:
            return None
        try:
            data = json.loads(out)
        except ValueError:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        return data if isinstance(data, dict) else None

    def answer(self, ref, text):
        """Answer a parked ticket as the human. The session resumes itself."""
        self._ok("answer", ref, text, "--by", "human", timeout=120)

    def comment(self, ref, text):
        self._ok("comment", ref, text, "--by", "system")

    # -- queue setup --
    def configure_queue(self, queue, repo_path="", workers=1):
        args = ["config", "-q", queue, "--auto-drain", "on", "--workers", workers, "--type", "bug"]
        if repo_path:
            args += ["--workers-local-path", repo_path]
        self._ok(*args)

    def queue_config(self, queue):
        try:
            data = self._json("config", "-q", queue, "--json")
        except WTError:
            return {}
        return data if isinstance(data, dict) else {}

    # -- minor decisions --
    def policy(self, queue):
        try:
            data = self._json("policy", "-q", queue, "show")
        except WTError:
            return {}
        return data if isinstance(data, dict) else {}

    def policy_set(self, queue, enabled):
        if enabled:
            self._ok("policy", "-q", queue, "set", "enabled=true", f"enabled_classes={MINOR_CLASS}")
        else:
            self._ok("policy", "-q", queue, "set", "enabled=false")

    def decisions(self, queue):
        """Decisions made without the owner: [{ref, state, reason}]."""
        try:
            out = self._ok("policy", "-q", queue, "decisions")
        except WTError:
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and REF_RE.fullmatch(parts[0].strip()):
                rows.append({"ref": parts[0].strip(), "state": parts[1].strip(),
                             "reason": parts[2].strip() if len(parts) > 2 else ""})
        return rows


def decision_details(item):
    """What an agent decided without the owner, oldest first: [{question, recommend, undo}]."""
    out, question = [], ""
    for event in (item or {}).get("history") or []:
        if event.get("event") == "block":
            question = str(event.get("question") or "")
        elif event.get("event") == "auto_decision":
            out.append({"question": question, "recommend": str(event.get("recommend") or ""),
                        "undo": str(event.get("undo") or "")})
    return out


def _last_line(text):
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    line = lines[-1] if lines else ""
    return re.sub(r"^error:\s*", "", line)[:300]


# -- reading a ticket -------------------------------------------------------

def ticket_queue(item):
    return str(item.get("project") or item.get("queue") or "")


def waits_on_owner(item):
    """True when a parked ticket needs the owner, not a customer or a timer."""
    if not item.get("needs_input"):
        return False
    if str(item.get("status") or "") == "closed":
        return False
    if str(item.get("block_kind") or "input") == "awaiting-client":
        return False
    wait = item.get("wait") if isinstance(item.get("wait"), dict) else {}
    if str(wait.get("on") or "owner") not in ("owner", ""):
        return False
    # WatchTower stamps an answer to the second and a park to the microsecond.
    # Compare whole seconds; a park in the same second as an answer came after it.
    answered, blocked = _second(item.get("answered_at")), _second(item.get("blocked_at"))
    return not (answered and blocked and answered > blocked)


def _second(stamp):
    """'2026-11-10T20:02:33.955390Z' -> '2026-11-10T20:02:33'."""
    return str(stamp or "")[:19]


def waits_on_customer(item):
    if not item.get("needs_input") or str(item.get("status") or "") == "closed":
        return False
    wait = item.get("wait") if isinstance(item.get("wait"), dict) else {}
    return str(item.get("block_kind") or "") == "awaiting-client" or str(wait.get("on") or "") == "external"


def recommendation(item):
    prop = item.get("block_proposal") if isinstance(item.get("block_proposal"), dict) else {}
    return str(prop.get("recommend") or "").strip()


def closed_with_fix(item):
    """True when the ticket closed with a commit, not as a duplicate or no-op."""
    res = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    if res.get("duplicate_of") or item.get("duplicate_of"):
        return False
    if res.get("commit") or item.get("close_commit"):
        return True
    for event in reversed(item.get("history") or []):
        if isinstance(event, dict) and event.get("event") == "close":
            return bool(event.get("commit")) and not event.get("duplicate_of")
    return False


def resolution_text(item):
    """The agent's closing summary, whatever shape the queue stored it in."""
    res = item.get("resolution")
    if isinstance(res, dict):
        for key in ("summary", "note", "text", "resolution", "message"):
            if res.get(key):
                return str(res[key]).strip()
        return ""
    if isinstance(res, str) and res.strip():
        return res.strip()
    for event in reversed(item.get("history") or []):
        if isinstance(event, dict) and event.get("event") == "close":
            for key in ("resolution", "summary", "note", "message"):
                if event.get(key):
                    return str(event[key]).strip()
    return ""
