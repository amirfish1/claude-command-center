# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""One tick runs On-call end to end; the loop calls it every few seconds.

A tick: read new messages on every channel and route them, poll the
monitoring sources that are due, turn parked tickets and closed tickets into
alerts, then send what is unsent. It needs no browser and no public URL.

Everything a test wants to replace is a constructor argument.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from . import alerts, customers, replies
from . import store as st
from . import wt as wtmod

POLL_EVERY_S = {"email": 30, "telegram": 8, "ntfy": 10, "imessage": 8, "sms": 30, "whatsapp": 30}
DECISIONS_EVERY_S = 30
CLOSED_EVERY_S = 60
RETRY_EVERY_S = 300
LOOP_SLEEP_S = 3
_REPORT_RE = re.compile(r"^\s*(?:report|bug)\b[:\s]+(.+)$", re.I | re.S)
_COMMANDS = ("status", "help", "?", "pause", "resume")


def _preview_flag():
    """The `oncall` preview flag. Off when it cannot be read."""
    try:
        from ccc_server.log_parse import _feature_flag
        return bool(_feature_flag("oncall"))
    except Exception:
        return False


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Engine:
    def __init__(self, root=None, *, wt=None, deps=None, build_channel=None, build_source=None,
                 clock=None, flag_on=None):
        self.root = Path(root) if root else st.default_root()
        self.wt = wt or wtmod.WT()
        self.deps = deps
        self.clock = clock or time.time
        self.flag_on = flag_on or _preview_flag
        self._build_channel = build_channel
        self._build_source = build_source
        self._lock = threading.RLock()
        self._store = None
        self._chan_cache = (None, {})
        self._src_cache = (None, {})
        self._cfg_cache = (None, None)
        self.last_tick = {}

    # -- pieces --
    @property
    def store(self):
        if self._store is None:
            self._store = st.Store(self.root)
        return self._store

    def config(self):
        path = self.root / st.CONFIG_NAME
        try:
            stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        except OSError:
            stamp = None
        if self._cfg_cache[0] != stamp or self._cfg_cache[1] is None:
            self._cfg_cache = (stamp, st.load_config(self.root))
        # A copy: callers edit what they get, and an edit that is then refused
        # must not leak into what the next caller reads.
        return copy.deepcopy(self._cfg_cache[1])

    def save_config(self, cfg):
        with self._lock:
            saved = st.save_config(self.root, cfg)
            self._cfg_cache = (None, None)
            return saved

    def channels(self, cfg=None):
        """Built channels by id. A channel that cannot be built is skipped and logged."""
        cfg = cfg or self.config()
        entries = [c for c in cfg.get("channels") or [] if c.get("enabled", True)]
        stamp = json.dumps(entries, sort_keys=True)
        if self._chan_cache[0] != stamp:
            build = self._build_channel
            if build is None:
                from . import channels as chmod
                build = chmod.build
            built = {}
            for entry in entries:
                try:
                    built[entry["id"]] = build(entry, kv=self.store)
                except Exception as exc:  # a bad entry must not stop the others
                    self._note(f"channel:{entry.get('id')}", str(exc))
            self._chan_cache = (stamp, built)
        return {**self._chan_cache[1], **{f"github:{sid}": source
                for sid, (entry, source) in self.sources(cfg).items()
                if entry.get("type") == "github"}}

    def sources(self, cfg=None):
        cfg = cfg or self.config()
        entries = [s for s in cfg.get("sources") or [] if s.get("enabled", True)]
        stamp = json.dumps(entries, sort_keys=True)
        if self._src_cache[0] != stamp:
            build = self._build_source
            if build is None:
                from . import sources as srcmod
                build = srcmod.build
            built = {}
            for entry in entries:
                try:
                    built[entry["id"]] = (entry, build(entry, kv=self.store))
                except Exception as exc:
                    self._note(f"source:{entry.get('id')}", str(exc))
            self._src_cache = (stamp, built)
        return self._src_cache[1]

    def _note(self, key, error):
        """Log a component's error once per distinct message, not every tick."""
        error = str(error or "")
        if self.store.kv_get(f"err:{key}", "") != error:
            self.store.kv_set(f"err:{key}", error)
            if error:
                self.store.log("error", f"{key}: {error}", now=self.clock())

    def _due(self, name, every, now, force=False):
        last = self.store.kv_get(f"due:{name}", 0) or 0
        if force or now < last or now - last >= every:
            self.store.kv_set(f"due:{name}", now)
            return True
        return False

    # -- the tick --
    def active(self):
        return bool(self.flag_on()) and bool(self.config().get("enabled"))

    def tick(self, force=False):
        with self._lock:
            now = self.clock()
            cfg = self.config()
            if not self.flag_on() or not cfg.get("enabled"):
                if self._store is not None and self.store.kv_get("enabled_at"):
                    self.store.kv_set("enabled_at", "")
                self.last_tick = {"active": False, "at": now}
                return self.last_tick
            store = self.store
            if not store.kv_get("enabled_at"):
                store.kv_set("enabled_at", _iso(now))
                store.log("oncall", "turned on", now=now)
            chans = self.channels(cfg)
            out = {"active": True, "at": now, "inbound": 0, "findings": 0, "new_alerts": 0, "sent": 0}

            for entry in cfg.get("channels") or []:
                chan = chans.get(entry.get("id"))
                if chan is None or not getattr(chan, "can_receive", False):
                    continue
                if not self._due(f"poll:{chan.id}", POLL_EVERY_S.get(entry.get("type"), 30), now, force):
                    continue
                messages = chan.poll()
                self._note(f"channel:{chan.id}", getattr(chan, "last_error", ""))
                for msg in messages:
                    if self._route(cfg, chans, entry, msg, now):
                        out["inbound"] += 1

            for sid, (entry, source) in self.sources(cfg).items():
                if not (force or source.due()):
                    continue
                findings = source.poll()
                self._note(f"source:{sid}", getattr(source, "last_error", ""))
                for finding in findings:
                    if customers.file_finding(cfg, store, self.wt, entry, finding, now=now):
                        out["findings"] += 1
                if entry.get("type") in ("ci", "alertmanager") and not source.last_error:
                    try:
                        source.reconcile(store, self.wt)
                    except Exception as exc:
                        self._note(f"source:{sid}", f"Monitoring recovery could not update its ticket ({type(exc).__name__}).")
                if entry.get("type") == "github" and not source.last_error:
                    source.receive_replies(lambda msg: self._github_reply(cfg, msg, now))
                    self._note(f"source:{sid}", source.last_error)
            if self._due("retry", RETRY_EVERY_S, now, force):
                customers.retry_unfiled(cfg, store, self.wt, now=now)

            if self._due("decisions", DECISIONS_EVERY_S, now, force):
                out["new_alerts"] += alerts.collect_decisions(
                    cfg, store, self.wt, now=now, silent_before=store.kv_get("enabled_at") or "")
            if self._due("closed", CLOSED_EVERY_S, now, force):
                customers.collect_closed(cfg, store, chans, self.wt, now=now)

            store.park_unhandled_inbound(now=now)
            customers.collect_delivery_problems(store, now=now)
            from .week import daily
            try:
                daily(self, now)
            except (ValueError, wtmod.WTError) as exc:
                self._note("summary", str(exc))
            out["sent"] = alerts.deliver(cfg, store, chans, now=now)
            self.last_tick = out
            return out

    def _github_reply(self, cfg, msg, now):
        report = self.store.report_by_thread(msg.thread)
        if not report or report["state"] == "dropped":
            return
        identity = (report.get("meta") or {}).get("github") or {}
        if (msg.sender.lower() != report["reporter_address"].lower()
                or (identity.get("author_id") is not None
                    and identity["author_id"] != msg.raw.get("author_id"))
                or identity.get("author_type") == "Bot"):
            return
        msg.raw["github"] = identity
        if self.store.inbound_claim(msg):
            try:
                how = customers.intake(cfg, self.store, self.wt, msg, now=now)
            except Exception as exc:  # one bad comment must not stop the rest of the page
                how = f"error: {type(exc).__name__}: {exc}"
                self.store.log("error", f"inbound on {msg.channel}: {how}", now=now)
            self.store.inbound_handled(msg, how)

    def _route(self, cfg, chans, entry, msg, now):
        """Handle one inbound message once. True when it was new."""
        store = self.store
        msg.channel = entry["id"]
        msg.sender = st.normalize_address(msg.sender) or msg.sender
        if not store.inbound_claim(msg):
            return False
        try:
            how = self._dispatch(cfg, chans, entry, msg, now)
        except Exception as exc:  # one bad message must not stop the loop
            how = f"error: {type(exc).__name__}: {exc}"
            store.log("error", f"inbound on {entry['id']}: {how}", now=now)
        store.inbound_handled(msg, how)
        return True

    def _dispatch(self, cfg, chans, entry, msg, now):
        if replies.is_owner(entry, msg):
            low = " ".join(str(msg.text or "").lower().split()).strip(" .!")
            if entry.get("type") == "email" and not msg.thread and low not in _COMMANDS:
                # Anyone can put the owner's address in From. A new email from
                # "the owner" is a report that waits for a go from a real alert.
                if customers.intake_enabled(entry):
                    return customers._new_report(dict(cfg, customers=dict(
                        cfg.get("customers") or {}, hold_unknown=True, known=[])),
                        self.store, self.wt, msg, now)
            m = _REPORT_RE.match(msg.text or "") if entry.get("type") != "email" else None
            if m and not msg.reply_to:
                ref = self.report(m.group(1).strip())
                chan = chans.get(entry["id"])
                if chan is not None:
                    alerts.say(self.store, chan, f"Filed as {ref}. An agent will pick it up.", now=now)
                return f"owner filed {ref}"
            return replies.handle(cfg, self.store, chans, self.wt, entry, msg, deps=self.deps, now=now)
        if customers.intake_enabled(entry):
            return customers.intake(cfg, self.store, self.wt, msg, now=now)
        return "ignored: not the owner"

    # -- things CCC and the CLI call --
    def inbound(self, channel_id, msg):
        """A message handed in by the local API (the command channel's inbound)."""
        with self._lock:
            if not self.active():
                return False
            cfg = self.config()
            entry = st.channel_by_id(cfg, channel_id)
            if entry is None:
                raise ValueError(f"No channel named {channel_id!r}.")
            if not msg.external_id:
                msg.external_id = hashlib.sha256(
                    f"{msg.sender}|{msg.text}|{int(self.clock())}".encode()).hexdigest()[:24]
            return self._route(cfg, self.channels(cfg), entry, msg, self.clock())

    def report(self, text, title="", queue=""):
        """File a report by hand (the owner's own words; no untrusted wrapper)."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("The report is empty.")
        with self._lock:
            now = self.clock()
            cfg = self.config()
            day = time.strftime("%Y%m%d", time.gmtime(now))
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "Report")
            report, _created = self.store.report_upsert(
                "manual", hashlib.sha256(text.encode()).hexdigest()[:16] + ":" + day,
                title=(title or first)[:120], text=text, queue=queue, state="held",
                meta={"auto": True, "ticket_text": "Reported by the owner through CCC On-call.\n\n" + text},
                now=now)
            if report.get("ticket_ref"):
                return report["ticket_ref"]
            return customers.file_report(cfg, self.store, self.wt, report, now=now)

    def request_message(self, ref, kind, text):
        with self._lock:
            cfg = self.config()
            return customers.request_message(cfg, self.store, self.channels(cfg), self.wt,
                                             ref=ref, kind=kind, text=text, now=self.clock())

    def raise_session(self, kind, session_id, title, body="", stamp=""):
        """CCC saw a session stop or ask something. Returns the alert or None."""
        if not self.active():
            return None
        wants = self.config().get("alerts") or {}
        if kind == "stopped" and not wants.get("stopped", True):
            return None
        if kind == "question" and not wants.get("decisions", True):
            return None
        with self._lock:
            row, _new = alerts.raise_event(
                self.store, kind=kind, key=f"session:{session_id}:{kind}:{stamp}",
                title=title or "A session", body=body, session_id=session_id, now=self.clock())
            return row

    def clear_session(self, kind, session_id, stamp=""):
        if self._store is None and not self.active():
            return
        with self._lock:
            alerts.clear_event(self.store, f"session:{session_id}:{kind}:{stamp}", now=self.clock())

    def raise_stuck(self, queue, detail, stamp=""):
        if not self.active() or not (self.config().get("alerts") or {}).get("stuck", True):
            return None
        if queue not in alerts.watched_queues(self.config()):
            return None
        with self._lock:
            row, _new = alerts.raise_event(
                self.store, kind="stuck", key=f"stuck:{queue}:{stamp}", ref=queue,
                title=f"{queue} is not moving", body=detail, needs_reply=False, now=self.clock())
            return row

    def test(self, channel_id=""):
        """Send a test message. Returns [{channel, ok, error}]."""
        with self._lock:
            cfg = self.config()
            chans = self.channels(cfg)
            results = []
            for entry in cfg.get("channels") or []:
                cid = entry.get("id")
                if channel_id and cid != channel_id:
                    continue
                chan = chans.get(cid)
                if chan is None:
                    results.append({"channel": cid, "ok": False,
                                    "error": self.store.kv_get(f"err:channel:{cid}", "") or "This channel is off."})
                    continue
                text = ("This is a test from CCC On-call. When an agent needs a decision, the "
                        "question arrives here." +
                        (" Reply to it and your reply becomes the answer."
                         if getattr(chan, "can_receive", False) else ""))
                receipt = alerts.say(self.store, chan, text, subject="CCC On-call test", now=self.clock())
                results.append({"channel": cid, "ok": receipt.ok, "error": receipt.error})
            return results

    def check(self, channel_id):
        chan = self.channels().get(channel_id)
        if chan is None:
            return False, self.store.kv_get(f"err:channel:{channel_id}", "") or "This channel is off."
        return chan.check()

    def status(self):
        cfg = self.config()
        store = self.store
        chans = self.channels(cfg)
        channel_rows = []
        for entry in cfg.get("channels") or []:
            cid = entry.get("id")
            chan = chans.get(cid)
            last = store.last_delivery(cid) or {}
            channel_rows.append({
                "id": cid, "type": entry.get("type"), "enabled": bool(entry.get("enabled", True)),
                "owner": entry.get("owner") or entry.get("topic") or "",
                "can_receive": bool(getattr(chan, "can_receive", False)),
                "intake": customers.intake_enabled(entry),
                "error": store.kv_get(f"err:channel:{cid}", "") or "",
                "last_sent_at": last.get("sent_at"), "last_ok": bool(last.get("ok")) if last else None,
            })
        source_rows = [{
            "id": s.get("id"), "type": s.get("type"), "enabled": bool(s.get("enabled", True)),
            "queue": s.get("queue") or cfg.get("queue") or "",
            "error": store.kv_get(f"err:source:{s.get('id')}", "") or "",
            "last_poll": store.kv_get(f"src:{s.get('id')}:last_poll"),
        } for s in cfg.get("sources") or []]
        waiting = [{"id": a["id"], "kind": a["kind"], "ref": a["ref"], "needs_reply": a["needs_reply"],
                    "line": alerts.render(a)[0], "created_at": a["created_at"], "sent_at": a["sent_at"]}
                   for a in store.alerts(state="open", limit=50)]
        from .escalation import who
        return {
            "oncall_now": who(cfg, self.clock()),
            "flag": bool(self.flag_on()), "enabled": bool(cfg.get("enabled")),
            "paused": bool(store.kv_get("paused", False)), "queue": cfg.get("queue") or "",
            "wt": self.wt.available(), "channels": channel_rows, "sources": source_rows,
            "waiting": waiting, "held_reports": len(store.reports(state="held")),
            "quiet_now": alerts.in_quiet_hours(cfg, self.clock()),
            "log": store.recent_log(30), "config": st.public_config(cfg),
        }

    # -- the loop --
    def run(self, stop=None):
        """Tick until ``stop`` is set. One failure never ends the loop."""
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                if self.active() or self._store is not None:
                    self.tick()
            except Exception as exc:
                try:
                    self._note("tick", f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            stop.wait(LOOP_SLEEP_S)


_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def get(**kwargs):
    """The process-wide engine for the live state directory."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = Engine(**kwargs)
        return _ENGINE
