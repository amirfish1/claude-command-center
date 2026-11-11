# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""What needs the owner becomes one numbered message per incident.

``collect_*`` turn facts (a parked ticket, a dead session, a held report)
into alert rows, once each. ``render`` is the only place message wording
lives. ``deliver`` sends what is unsent, within quiet hours and the hourly cap.
"""

from __future__ import annotations

import re
import time

from . import wt as wtmod
from .store import Outbound, Receipt

HOURLY_CAP = 12
RETRY_AFTER_S = 120
GO_WORDS = ("go", "yes", "y", "ok", "okay", "sure", "do it", "approve", "approved", "send", "send it")
NO_WORDS = ("no", "n", "drop", "drop it", "skip", "ignore", "spam", "stop", "cancel", "don't", "dont")


def _clip(text, limit):
    text = " ".join(str(text or "").split()) if limit <= 200 else str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# -- wording ----------------------------------------------------------------

def render(alert):
    """(subject, text, choices) for one alert. Plain text; works on any channel."""
    n, kind, ref = alert["id"], alert["kind"], alert.get("ref") or ""
    title, body, meta = alert.get("title") or "", alert.get("body") or "", alert.get("meta") or {}
    choices = ()
    if kind == "decision":
        head = f"{ref} needs a decision (#{n})"
        lines = [head, title, "", body]
        if meta.get("recommend"):
            lines.append(f"Agent recommends: {meta['recommend']}")
        if meta.get("why"):
            lines.append(f"Needs you because: {meta['why']}")
        lines += ["", "Reply with your answer."]
    elif kind in ("question", "plain_question"):
        head = f"Question from {title or 'an agent'} (#{n})"
        if kind == "plain_question":
            choices = tuple((c[0], c[1]) for c in meta.get("choices") or ()
                            if isinstance(c, (list, tuple)) and len(c) == 2)[:3]
        asks = choices or body.rstrip().endswith("?")
        lines = [head, body, "", "Reply with your answer." if asks
                 else "Reply here when done, or with a question."]
    elif kind == "permission":
        head = f"Permission needed: {title or 'an agent'} (#{n})"
        lines = [head, body, "", "Reply allow or deny. If unavailable, answer in the session."]
        choices = ("allow", "deny")
    elif kind == "stopped":
        head = f"An agent stopped (#{n})"
        lines = [head, title]
        if body:
            lines += ["", body]
        lines += ["", "Reply continue to restart it, or reply with instructions."]
        choices = ("continue",)
    elif kind == "stuck":
        head = f"{ref or 'A queue'} is not moving (#{n})"
        lines = [head, body]
    elif kind == "report":
        head = f"New report from {meta.get('sender') or 'an unknown sender'} (#{n})"
        lines = [head, title]
        if body and _clip(body, 200) != _clip(title, 200):
            lines += ["", _clip(body, 700)]
        lines += ["", "Reply go to have an agent work on it, or no to drop it."]
        choices = ("go", "no")
    elif kind == "send":
        who = meta.get("name") or meta.get("address") or "the reporter"
        addr = f" ({meta['address']})" if meta.get("name") and meta.get("address") else ""
        verb = "ask" if meta.get("message_kind") == "ask" else "tell"
        about = f" about {ref}" if ref else ""
        head = f"An agent wants to {verb} {who}{addr}{about} (#{n}):"
        lines = [head, "", body, "", "Reply yes to send, no to drop, or reply with your own wording."]
        choices = ("yes", "no")
    elif kind == "fixed":
        label = "Fixed" if meta.get("outcome", "fixed") == "fixed" else "Closed without a code change"
        name = _clip(" ".join(title.split()), 80) if title.strip() else ref
        head = f"{label}: {name} (#{n})"
        lines = [head]
        # Only the outcome paragraph: the per-file detail stays on the ticket.
        first = body.strip().split("\n\n")[0].strip()
        if first:
            lines.append(_clip(first, 300))
        if ref:
            lines.append(f"Ticket {ref}.")
        if not meta.get("notify"):
            lines += ["", "No action needed."]
        else:
            who = meta.get("name") or meta.get("address") or "the reporter"
            lines += ["", f"Reply yes to tell {who} it is fixed, or no to skip."]
            choices = ("yes", "no")
    else:
        head = f"{title or 'CCC On-call'} (#{n})"
        lines = [head, body]
    text = "\n".join(line for line in lines if line is not None).strip()
    return head, text, choices


# -- collecting -------------------------------------------------------------

def watched_queues(cfg):
    queues = [str(cfg.get("queue") or "")]
    queues += [str(q) for q in cfg.get("watch") or []]
    queues += [str(s.get("queue") or "") for s in cfg.get("sources") or [] if s.get("enabled", True)]
    out = []
    for name in queues:
        if name and name not in out:
            out.append(name)
    return out


_GATE_NOTE = re.compile(r"\s*\[auto-approval declined:\s*(?:needs you:\s*)?(.*?)\]\s*$", re.S)


def _gate_note(question):
    """Split WatchTower's "why this was not decided for you" note off a question."""
    text = str(question or "").strip()
    m = _GATE_NOTE.search(text)
    if not m:
        return text, ""
    return text[:m.start()].strip(), " ".join(m.group(1).split())


def collect_decisions(cfg, store, wt, *, now=None, silent_before=""):
    """Parked tickets waiting on the owner -> alerts; answered ones are cleared.

    A ticket parked before On-call was turned on (``silent_before``, an ISO
    time) is registered without a message, so turning it on is quiet.
    Returns the number of new alerts.
    """
    if not (cfg.get("alerts") or {}).get("decisions", True):
        return 0
    now = time.time() if now is None else now
    created, live_keys, failed = 0, set(), set()
    for queue in watched_queues(cfg):
        try:
            items = wt.blocked(queue)
        except wtmod.WTError as exc:
            store.log("wt", f"blocked {queue}: {exc}", now=now)
            failed.add(queue)
            continue
        for item in items:
            if not wtmod.waits_on_owner(item):
                continue
            ref = str(item.get("ref") or "")
            blocked_at = str(item.get("blocked_at") or "")
            key = f"wt:{ref}:{blocked_at}"
            live_keys.add(key)
            question, why = _gate_note(item.get("block_question"))
            report = store.report_by_ref(ref)
            ci_source = bool(report and any(
                source.get('type') == 'ci' and report['source'] == 'source:' + source.get('id', '')
                for source in cfg.get('sources', [])))
            row, new = store.alert_upsert(
                key, kind="decision", ref=ref,
                title=_clip(item.get("title") or item.get("note") or ref, 160),
                body=_clip(question or "The agent needs a decision.", 1800),
                meta={"queue": queue, "call_kind": "ci_failed" if ci_source else "decision", "recommend": _clip(wtmod.recommendation(item), 400),
                      "why": _clip(why, 300)}, now=now)
            if new:
                created += 1
                if silent_before and blocked_at and blocked_at[:19] < silent_before[:19]:
                    store.alert_mark_sent(row["id"], now=now)
    for alert in store.alerts(state="open"):
        if alert["kind"] != "decision" or alert["key"] in live_keys:
            continue
        if (alert.get("meta") or {}).get("queue") in failed:
            continue
        if store.alert_close(alert["id"], "cleared", now=now):
            store.log("alert", f"#{alert['id']} {alert['ref']} no longer waiting", now=now)
    return created


def raise_event(store, *, kind, key, title, body="", ref="", session_id="", needs_reply=True,
                meta=None, now=None):
    """An alert pushed by CCC itself (a session question, a dead session...)."""
    row, new = store.alert_upsert(key, kind=kind, ref=ref, session_id=session_id,
                                  title=_clip(title, 160), body=_clip(body, 1800),
                                  needs_reply=needs_reply, meta=meta or {}, now=now)
    return row, new


def clear_event(store, key, now=None):
    alert = store.alert_by_key(key)
    if alert and alert["state"] == "open":
        store.alert_close(alert["id"], "cleared", now=now)


# -- delivering -------------------------------------------------------------

def in_quiet_hours(cfg, now=None):
    quiet = cfg.get("quiet_hours")
    if not isinstance(quiet, dict) or not quiet.get("start") or not quiet.get("end"):
        return False
    when = time.time() if now is None else now
    local = None
    zone = str(quiet.get("tz") or "").strip()
    if zone:
        try:
            import datetime
            import zoneinfo
            local = datetime.datetime.fromtimestamp(when, zoneinfo.ZoneInfo(zone)).timetuple()
        except Exception:
            local = None  # an unreadable zone falls back to the machine clock
    local = local or time.localtime(when)
    minute = local.tm_hour * 60 + local.tm_min
    start, end = _minutes(quiet["start"]), _minutes(quiet["end"])
    if start == end:
        return False
    return start <= minute < end if start < end else (minute >= start or minute < end)


def _minutes(hhmm):
    hours, _, mins = str(hhmm).partition(":")
    return int(hours) * 60 + int(mins or 0)


def alert_channels(cfg, chans, *, include_calls=False):
    """Channels that carry owner alerts: enabled, built, not marked intake-only."""
    out = []
    for entry in cfg.get("channels") or []:
        if entry.get("type") == "call" and not include_calls:
            continue
        if not entry.get("enabled", True) or entry.get("alerts") is False:
            continue
        # A channel set to read customer reports, with no address for the owner,
        # is a line to customers only. An owner alert never goes out on it.
        if entry.get("intake") is True and not str(entry.get("owner") or "").strip():
            continue
        chan = chans.get(entry.get("id"))
        if chan is not None:
            out.append(chan)
    return out


def deliver(cfg, store, chans, *, now=None):
    """Send every open alert that has not gone out. Returns how many were sent."""
    now = time.time() if now is None else now
    pending = store.unsent_alerts()
    from . import escalation
    escalated = escalation.deliver(cfg, store, chans, pending, now)
    if cfg.get("escalation"):
        from .calls import escalates
        pending = [a for a in pending if not escalates(cfg, a)]
    if not pending or store.kv_get("paused", False):
        return escalated
    targets = alert_channels(cfg, chans)
    if not targets or in_quiet_hours(cfg, now):
        return escalated
    cap = int(cfg.get("hourly_cap") or HOURLY_CAP)
    from . import rate_limit
    budget = rate_limit.load(store, now, cap)
    recent = {int(key) for key in budget["entries"]}
    sent = escalated
    for alert in pending:
        if len(recent) >= cap and alert["id"] not in recent:
            if not store.kv_get("cap_noted", 0) or now - store.kv_get("cap_noted", 0) > 3600:
                store.kv_set("cap_noted", now)
                store.log("alert", f"hourly cap of {cap} reached; the rest wait", now=now)
            break
        tries = store.kv_get(f"try:{alert['id']}", {"n": 0, "at": 0})
        if tries["n"] and 0 <= now - tries["at"] < RETRY_AFTER_S:
            continue
        if send_alert(store, targets, alert, now=now):
            store.alert_mark_sent(alert["id"], now=now)
            recent.add(alert["id"])
            rate_limit.record(store, budget, alert["id"])
            sent += 1
        else:
            store.kv_set(f"try:{alert['id']}", {"n": tries["n"] + 1, "at": now})
    return sent


def send_alert(store, targets, alert, *, now=None):
    now = time.time() if now is None else now
    subject, text, choices = render(alert)
    ok_any = False
    for chan in targets:
        previous = store.delivery_latest(alert["id"], chan.id)
        if previous and previous["ok"]:
            ok_any = True
            continue
        uncertain = "Delivery interrupted; outcome unknown."
        if previous and store.kv_get(f"owner-repeat:{alert['id']}:{chan.id}", False):
            continue
        repeat = bool(previous and previous["error"] in (
            uncertain, "Delivery interrupted; outcome unknown. No automatic retry."))
        if repeat:
            if now - previous["sent_at"] < RETRY_AFTER_S:
                continue
            if not store.owner_repeat_claim(alert["id"], chan.id):
                continue
        # Commit the intent before the provider can accept the message.
        store.delivery_add(alert["id"], chan.id, getattr(chan, "owner", "") or "",
                           Receipt(ok=False, error=uncertain), now=now)
        attempt = store.delivery_latest(alert["id"], chan.id)
        receipt = chan.send(Outbound(text=("This may be a repeat.\n\n" if repeat else "") + text, subject=subject, thread=alert["token"],
                                     choices=choices if alert.get("needs_reply") else ()))
        if receipt.uncertain:
            receipt = Receipt(ok=False, error=uncertain, uncertain=True)
        store.delivery_finish(attempt["id"], receipt)
        if receipt.ok:
            ok_any = True
        else:
            store.log("send", f"#{alert['id']} via {chan.id} failed: {receipt.error}", now=now)
    return ok_any


def say(store, chan, text, *, to="", subject="", thread="", reply_to="", now=None):
    """A short direct reply on one channel (an ack, a list, a status)."""
    receipt = chan.send(Outbound(text=text, to=to, subject=subject or text.splitlines()[0][:80],
                                 thread=thread, reply_to=reply_to))
    store.delivery_add(None, chan.id, to or getattr(chan, "owner", "") or "", receipt, now=now)
    return receipt
