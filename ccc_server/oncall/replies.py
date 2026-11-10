# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""A message from the owner becomes the answer it carries.

Which alert a reply answers, most trusted first: the private thread token
(email), the message it replies to (Telegram), a number or ticket ref at the
start of the text, then the only alert that is waiting. With several waiting
and no pointer the owner gets a short list back; nothing is guessed.

Email can be forged, so an email only answers an alert when it carries that
alert's token. Chat channels are trusted by the sender's address.
"""

from __future__ import annotations

import re
import time

from . import alerts, customers
from . import wt as wtmod
from .store import normalize_address

_NUM_RE = re.compile(r"^\s*#?(\d{1,6})\b[\s:.,)-]*(.*)$", re.S)
_WORD_NUM_RE = re.compile(r"^\s*(go|yes|no|ok|drop|send|skip)\s+#?(\d{1,6})\s*$", re.I)
_REF_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*-\d+)\b[\s:.,)-]*(.*)$", re.S)
RESTART_WORDS = ("continue", "restart", "resume", "go", "yes", "retry")
HELP = ("Reply to an alert with your answer. With several waiting, start with the number: "
        "4 keep it. Other words: status, pause, resume.")


def is_owner(entry, msg):
    """Whether this inbound message is from the person the channel alerts."""
    if entry.get("type") == "ntfy":
        return True   # the reply topic is the secret
    owner = normalize_address(entry.get("owner"))
    return bool(owner) and normalize_address(msg.sender) == owner


def handle(cfg, store, chans, wt, entry, msg, *, deps=None, now=None):
    """Apply one owner message. Returns a short note of what happened."""
    now = time.time() if now is None else now
    chan = chans.get(entry.get("id"))
    text = str(msg.text or "").strip()
    low = " ".join(text.lower().split()).strip(" .!")
    email = entry.get("type") == "email"

    def say(reply, alert=None):
        if chan is None or not cfg.get("ack", True):
            return
        alerts.say(store, chan, reply, subject=(msg.subject or "CCC On-call"),
                   thread=(alert or {}).get("token", "") or msg.thread,
                   reply_to=msg.external_id if email else "", now=now)

    by_token = store.alert_by_token(msg.thread) if msg.thread else None
    by_reply = store.delivery_alert(msg.channel, msg.reply_to) if msg.reply_to else None
    trusted = (not email) or by_token is not None

    if low in ("status", "help", "?"):
        say(HELP if low != "status" else status_text(cfg, store))
        return low
    if low in ("pause", "resume"):
        if not trusted:
            say("By email, pause and resume only work as a reply to one of my alerts.")
            return "refused: untrusted pause"
        store.kv_set("paused", low == "pause")
        store.log("owner", f"{low} from {entry.get('id')}", now=now)
        say("Paused. I will hold messages until you reply resume." if low == "pause"
            else "Resumed.")
        return low
    if not text:
        return "empty"

    alert, rest = by_token or by_reply, text
    if alert is None:
        if email:
            say("I could not match that to an alert. Reply inside the alert's own email thread.")
            return "refused: email without a thread token"
        alert, rest, problem = _resolve(store, text)
        if problem:
            say(problem)
            return "unmatched"
    if alert["state"] != "open":
        say(f"#{alert['id']} was already handled.", alert)
        return f"#{alert['id']} already handled"
    try:
        note, ack = _apply(cfg, store, chans, wt, alert, rest.strip(), deps, now)
    except wtmod.WTError as exc:
        say(f"That did not go through: {exc}", alert)
        store.log("owner", f"#{alert['id']} answer failed: {exc}", now=now)
        return f"#{alert['id']} failed"
    if ack:
        say(ack, alert)
    store.log("owner", f"#{alert['id']} {note}", now=now)
    return f"#{alert['id']} {note}"


def _resolve(store, text):
    """(alert, remaining text, problem). ``problem`` is a message for the owner."""
    waiting = [a for a in store.alerts(state="open", needs_reply=True) if a.get("sent_at")]
    m = _WORD_NUM_RE.match(text)
    if m:
        alert = store.alert(int(m.group(2)))
        if alert:
            return alert, m.group(1), ""
    m = _NUM_RE.match(text)
    if m and (m.group(2).strip() or len(waiting) != 1):
        alert = store.alert(int(m.group(1)))
        if alert and m.group(2).strip():
            return alert, m.group(2), ""
        if alert and not m.group(2).strip():
            return None, text, f"What is your answer for #{alert['id']}? Reply like: {alert['id']} your answer"
    m = _REF_RE.match(text)
    if m:
        hits = [a for a in waiting if a.get("ref") == m.group(1)]
        if len(hits) == 1 and m.group(2).strip():
            return hits[0], m.group(2), ""
    if len(waiting) == 1:
        return waiting[0], text, ""
    if not waiting:
        return None, text, "Nothing is waiting on you right now."
    lines = [f"Which one? Start your reply with its number, like: {waiting[-1]['id']} your answer"]
    for a in reversed(waiting[:12]):
        lines.append("#%d %s" % (a["id"], alerts.render(a)[0].replace(" (#%d)" % a["id"], "")))
    return None, text, "\n".join(lines)


def _is(word_list, text):
    return " ".join(text.lower().split()).strip(" .!") in word_list


def _apply(cfg, store, chans, wt, alert, text, deps, now):
    """Carry out the answer. Returns (log note, ack text or '')."""
    kind, ref, meta = alert["kind"], alert.get("ref") or "", alert.get("meta") or {}
    if kind == "decision":
        wt.answer(ref, text)
        store.alert_close(alert["id"], "answered", text, now=now)
        return f"answered {ref}", f"Sent to the agent on {ref}."
    if kind == "permission":
        if text.strip().lower() not in ("allow", "deny"):
            return "unclear", "Reply allow or deny."
        ok, detail = (deps.approve(alert["session_id"], text.strip().lower(), meta)
                      if deps is not None else (False, "CCC is not running"))
        if not ok:
            raise wtmod.WTError(detail)
        store.alert_close(alert["id"], "answered", text, now=now)
        return "permission answered", "Sent to the agent."
    if kind in ("question", "plain_question", "stopped"):
        body = text
        if kind == "stopped" and _is(RESTART_WORDS, text):
            body = "Continue where you left off."
        ok, detail = (deps.inject(alert.get("session_id") or "", body) if deps is not None
                      else (False, "CCC is not running"))
        if not ok:
            raise wtmod.WTError(detail or "the session could not be reached")
        store.alert_close(alert["id"], "answered", text, now=now)
        return "sent to the session", "Sent to the agent."
    if kind == "report":
        report = store.report(meta.get("report_id"))
        if report is None or report["state"] != "held":
            store.alert_close(alert["id"], "cleared", text, now=now)
            return "report already handled", "That report was already handled."
        if _is(alerts.GO_WORDS, text):
            new_ref = customers.file_report(cfg, store, wt, report, now=now)
            store.alert_close(alert["id"], "answered", text, now=now)
            return f"filed {new_ref}", f"Filed as {new_ref}. An agent will pick it up."
        if _is(alerts.NO_WORDS, text):
            customers.drop_report(store, report["id"], now=now)
            store.alert_close(alert["id"], "answered", text, now=now)
            return "dropped the report", "Dropped."
        return "unclear", f"Reply go or no for #{alert['id']}."
    if kind in ("send", "fixed"):
        message_id = meta.get("message_id")
        if kind == "fixed" and not meta.get("notify"):
            store.alert_close(alert["id"], "cleared", text, now=now)
            return "noted", ""
        if _is(alerts.NO_WORDS, text):
            customers.drop_message(store, message_id, now=now)
            store.alert_close(alert["id"], "answered", text, now=now)
            return "message dropped", "Not sent."
        wording = None if _is(alerts.GO_WORDS, text) else text
        receipt = customers.send_message(store, chans, wt, message_id, text=wording, now=now)
        if not receipt.ok:
            raise wtmod.WTError(receipt.error or "the message could not be sent")
        store.alert_close(alert["id"], "answered", text, now=now)
        who = meta.get("name") or meta.get("address") or "the reporter"
        return "message sent", f"Sent to {who}."
    store.alert_close(alert["id"], "cleared", text, now=now)
    return "noted", ""


def status_text(cfg, store):
    waiting = [a for a in store.alerts(state="open", needs_reply=True) if a.get("sent_at")]
    head = "On-call is paused." if store.kv_get("paused", False) else "On-call is on."
    if not waiting:
        return head + " Nothing is waiting on you."
    lines = [f"{head} {len(waiting)} waiting on you:"]
    for a in reversed(waiting[:12]):
        lines.append("#%d %s" % (a["id"], alerts.render(a)[0].replace(" (#%d)" % a["id"], "")))
    return "\n".join(lines)
