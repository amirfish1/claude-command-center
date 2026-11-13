# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""The On-call home screen: what needs you, what is being worked on, what got fixed.

``summary`` is the whole screen as data, read from On-call's own store only:
no session scan and no WatchTower call, so the dashboard can ask every few
seconds. ``answer`` applies an owner's answer exactly as a phone reply does.

A permission prompt is listed but cannot be answered here: approving one needs
the request it belongs to, and only the session shows that (docs/oncall-security.md).
"""

from __future__ import annotations

from . import alerts
from . import replies
from . import secret_filter
from . import wt as wtmod

WEEK = 7 * 86400
SHOWN = 8                    # rows per list; the counts are always whole
_FREE_TEXT = ("decision", "question", "plain_question", "stopped", "send")
_LABELS = {
    "send": {"yes": "Send", "no": "Don't send"},
    "fixed": {"yes": "Tell them", "no": "Skip"},
    "report": {"go": "Work on it", "no": "Drop it"},
    "stopped": {"continue": "Continue"},
}
_SOURCE_WORDS = {"github": "From a GitHub issue", "ci": "From a failed build"}


def _plain(alert):
    """(headline, detail) with the alert number and the reply instructions removed."""
    head, text, _choices = alerts.render(alert)
    headline = head.replace(" (#%d)" % alert["id"], "").rstrip(":")
    parts = [p.strip() for p in text.split("\n", 1)[-1].strip().split("\n\n")] if "\n" in text else []
    if parts and parts[-1].startswith(("Reply ", "No reply action", "Answer it in the session.", "Answer in the session.")):
        parts.pop()
    return headline, alerts._clip("\n\n".join(p for p in parts if p), 1800)


def _choices(alert):
    out = []
    for choice in alerts.render(alert)[2]:
        label, reply = choice if isinstance(choice, (list, tuple)) else (choice, choice)
        label = _LABELS.get(alert["kind"], {}).get(str(reply).lower(), str(label))
        out.append({"label": label[:1].upper() + label[1:], "reply": str(reply)})
    return out


def _origin(cfg, report):
    who = report.get("reporter_name") or report.get("reporter_address")
    if who:
        return f"Reported by {who}"
    source = str(report.get("source") or "")
    if source == "manual":
        return "Reported by you"
    if source.startswith("source:"):
        kind = next((str(s.get("type") or "") for s in cfg.get("sources") or []
                     if s.get("id") == source[7:]), "")
        return _SOURCE_WORDS.get(kind, "Found by a check")
    return "Reported"


def summary(eng):
    cfg = eng.config()
    if not eng.flag_on() or not cfg.get("enabled"):
        return {"enabled": False}
    store, now = eng.store, eng.clock()
    waiting = [a for a in store.alerts(state="open", limit=50)
               if (a["needs_reply"] or a["kind"] == "stuck" or (a.get("meta") or {}).get("at_keyboard"))
               and not (a["kind"] == "fixed" and not (a.get("meta") or {}).get("notify"))]
    needs = []
    for alert in sorted(waiting, key=lambda a: a["created_at"]):   # oldest first: answer in the order asked
        session_only = alert['kind'] == 'permission' or bool((alert.get('meta') or {}).get('at_keyboard'))
        home_alert = alert
        if session_only:
            home_alert = {**alert, 'meta': {**(alert.get('meta') or {}), 'at_keyboard': True}}
        headline, detail = _plain(home_alert)
        _head, message, _options = alerts.render(home_alert)
        reply_help = ('Open this session in CCC to read the prompt and answer there.' if session_only
                      else message.rsplit('\n\n', 1)[-1])
        headline, detail, message, reply_help, _hit = secret_filter.mask(headline, detail, message, reply_help)
        safe_choices = []
        for option in ([] if session_only else _choices(alert)):
            name, reply, hit = secret_filter.mask(option['label'], option['reply'])
            if not hit:
                safe_choices.append({'label': name, 'reply': reply})
        needs.append({
            "id": alert["id"], "kind": alert["kind"], "headline": headline, "detail": detail,
            "choices": safe_choices, "message": message,
            "reply_help": reply_help,
            "free_text": not session_only and alert["kind"] in _FREE_TEXT, "session_only": session_only,
            "session_id": alert.get("session_id") or "", "ref": alert.get("ref") or "",
            "created_at": alert["created_at"],
        })
    asked = {a.get("ref") for a in waiting if a.get("ref")}
    filed = sorted(store.reports(state="filed", limit=200), key=lambda r: r["created_at"], reverse=True)
    working = [{"title": r.get("title") or r.get("ticket_ref") or "A report", "ref": r.get("ticket_ref") or "",
                "origin": _origin(cfg, r), "created_at": r["created_at"],
                "waiting_on_you": bool(r.get("ticket_ref")) and r.get("ticket_ref") in asked}
               for r in filed[:SHOWN]]
    done = sorted((a for a in store.alerts(limit=200)
                   if a["kind"] == "fixed" and a["created_at"] >= now - WEEK),
                  key=lambda a: a["created_at"], reverse=True)
    fixed = []
    for alert in done[:SHOWN]:
        report = store.report_by_ref(alert["ref"]) if alert.get("ref") else None
        took = alert["created_at"] - report["created_at"] if report else None
        fixed.append({
            "title": alerts._clip(" ".join(str(alert.get("title") or "").split()), 80) or alert.get("ref") or "A report",
            "ref": alert.get("ref") or "", "at": alert["created_at"],
            "took": took if took is not None and took >= 0 else None,
            "code_change": (alert.get("meta") or {}).get("outcome", "fixed") == "fixed",
        })
    quiet = cfg.get("quiet_hours") if isinstance(cfg.get("quiet_hours"), dict) else {}
    return {
        "enabled": True, "paused": bool(store.kv_get("paused", False)), "now": now,
        "needs_you": needs, "working": working, "working_count": len(filed),
        "fixed": fixed, "fixed_count": len(done),
        "channels": sorted({str(c.get("type")) for c in cfg.get("channels") or [] if c.get("enabled", True)}),
        "sources": sorted({str(s.get("type")) for s in cfg.get("sources") or [] if s.get("enabled", True)}),
        "quiet": {"start": quiet["start"], "end": quiet["end"]} if quiet.get("start") and quiet.get("end") else None,
        "quiet_now": alerts.in_quiet_hours(cfg, now),
    }


def answer(eng, body):
    """Apply the owner's answer to one waiting alert. Raises ValueError with a sentence."""
    try:
        alert_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise ValueError("Say which alert this answers.") from None
    text = str(body.get("text") or "").strip()
    if not text:
        raise ValueError("The answer is empty.")
    with eng._lock:
        if not eng.active():
            raise ValueError("On-call is off.")
        cfg, store, now = eng.config(), eng.store, eng.clock()
        alert = store.alert(alert_id)
        if alert is None:
            raise ValueError("That alert was not found.")
        if alert["state"] != "open" or not alert["needs_reply"]:
            raise ValueError("That one was already handled.")
        if alert["kind"] == "permission":
            raise ValueError("Answer a permission prompt in its session.")
        if (alert.get("meta") or {}).get("at_keyboard"):
            raise ValueError("Answer this prompt in its session.")
        if not store.reply_action_claim(alert_id):
            raise ValueError("An earlier answer may have been applied. Check the ticket or session directly.")
        from . import escalation
        chans = eng.channels(cfg)
        try:
            escalation.answered(cfg, store, chans, alert, "", now)
            note, ack = replies._apply(cfg, store, chans, eng.wt, alert, text, eng.deps, now)
        except wtmod.WTError as exc:
            store.log("owner", f"#{alert_id} answer from the dashboard failed: {exc}", now=now)
            raise ValueError(f"That did not go through: {exc}") from None
        if note == "unclear":
            store.reply_action_release(alert_id)
            raise ValueError(ack or "That answer was not understood.")
        store.log("owner", f"#{alert_id} {note} (dashboard)", now=now)
        return {"note": note, "message": ack}
