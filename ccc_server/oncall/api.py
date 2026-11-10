# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""The HTTP surface of On-call, as plain functions the server routes to.

``handle(method, path, body)`` returns ``(status, payload)``. The server only
checks the preview flag and loopback rules, then hands the request here, so
the whole surface is testable without starting a server.

A secret typed into a form goes straight to the CCC Vault; the config file
only ever holds the Vault entry's name.
"""

from __future__ import annotations

import re
import time

from . import alerts
from . import engine as engine_mod
from . import store as st
from . import wt as wtmod

_CONFIG_KEYS = ("enabled", "queue", "repo", "quiet_hours", "alerts", "customers", "hourly_cap",
                "watch", "ack")
_ID_CLEAN = re.compile(r"[^a-z0-9_-]")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _engine(eng=None):
    return eng or engine_mod.get()


def handle(method, path, body=None, *, eng=None):
    """Route one /api/oncall/* request. Never raises."""
    body = body if isinstance(body, dict) else {}
    eng = _engine(eng)
    name = path.rstrip("/").rsplit("/", 1)[-1]
    try:
        if method == "GET" and name == "status":
            return 200, status(eng)
        if method == "GET" and name == "decisions":
            return 200, {"ok": True, "decisions": decisions(eng)}
        if method == "POST":
            fn = {"config": save_config, "channel": channel, "source": source, "test": test,
                  "customer": customer, "report": report, "inbound": inbound, "tick": tick}.get(name)
            if fn is not None:
                return 200, dict({"ok": True}, **(fn(eng, body) or {}))
        return 404, {"ok": False, "error": "Unknown On-call request."}
    except ApiError as exc:
        return exc.status, {"ok": False, "error": str(exc)}
    except (ValueError, wtmod.WTError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    except Exception as exc:  # never take the server down for a preview feature
        return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# -- reading ----------------------------------------------------------------

def status(eng):
    from . import channels as chmod
    from . import sources as srcmod
    out = eng.status()
    out["ok"] = True
    out["channel_types"] = {t: chmod.required_fields(t) for t in st.CHANNEL_TYPES}
    out["source_types"] = {t: srcmod.required_fields(t) for t in st.SOURCE_TYPES}
    out["minor_decisions"] = bool((out["config"].get("minor_decisions") or {}).get("enabled"))
    return out


def decisions(eng):
    cfg = eng.config()
    rows = []
    for queue in alerts.watched_queues(cfg):
        rows += [dict(row, queue=queue) for row in eng.wt.decisions(queue)]
    return rows


# -- config -----------------------------------------------------------------

def save_config(eng, body):
    """Merge a patch of top-level settings. Turning it on needs a queue and a channel."""
    patch = body.get("patch") if isinstance(body.get("patch"), dict) else body
    cfg = eng.config()
    before = dict(cfg)
    for key in _CONFIG_KEYS:
        if key in patch:
            if isinstance(cfg.get(key), dict) and isinstance(patch[key], dict):
                cfg[key] = dict(cfg[key], **patch[key])
            else:
                cfg[key] = patch[key]
    if "queue" in patch:
        cfg["queue"] = str(patch["queue"] or "").strip().upper()
    minor = patch.get("minor_decisions")
    if isinstance(minor, dict) and "enabled" in minor:
        cfg["minor_decisions"] = {"enabled": bool(minor["enabled"])}
    if cfg.get("enabled") and not before.get("enabled"):
        if not cfg.get("queue"):
            raise ApiError("Name a queue first.")
        if not [c for c in cfg.get("channels") or [] if c.get("enabled", True)]:
            raise ApiError("Add a message channel first.")
    saved = eng.save_config(cfg)
    notes = []
    queue = saved.get("queue")
    if queue and eng.wt.available():
        if queue != before.get("queue") or (saved.get("repo") and saved.get("repo") != before.get("repo")):
            try:
                eng.wt.configure_queue(queue, saved.get("repo") or "")
            except wtmod.WTError as exc:
                notes.append(f"Queue {queue} was not set up: {exc}")
        want = bool((saved.get("minor_decisions") or {}).get("enabled"))
        if want != bool((before.get("minor_decisions") or {}).get("enabled")) or queue != before.get("queue"):
            for name in alerts.watched_queues(saved):
                try:
                    eng.wt.policy_set(name, want)
                except wtmod.WTError as exc:
                    notes.append(f"Minor decisions on {name}: {exc}")
    elif queue and not eng.wt.available():
        notes.append("WatchTower is not installed, so no agent will pick tickets up yet.")
    return {"config": st.public_config(saved), "notes": notes}


# -- channels and sources ---------------------------------------------------

def _new_id(existing, wanted, kind):
    base = _ID_CLEAN.sub("", str(wanted or kind or "x").lower())[:24] or "x"
    if base not in existing:
        return base
    n = 2
    while f"{base}{n}" in existing:
        n += 1
    return f"{base}{n}"


def _store_secrets(entry, secrets, prefix):
    """Move typed secrets into the Vault; the entry keeps only entry names."""
    from ccc_server import vault
    for field, value in (secrets or {}).items():
        value = str(value or "").strip()
        if not value or not str(field).endswith("_vault"):
            continue
        name = f"oncall-{prefix}-{entry['id']}-{field[:-len('_vault')]}".replace("_", "-")[:64]
        try:
            if vault.vault_get_entry(name):
                vault.vault_update_entry(name, value=value)
            else:
                vault.vault_create_entry(name, value, kind="token", service="CCC On-call",
                                         notes=f"Used by the On-call {prefix} {entry['id']}.")
        except vault.VaultError as exc:
            raise ApiError(f"Could not save that to the Vault: {exc}")
        entry[field] = name


def _clean_entry(raw, fields):
    entry = {}
    known = {f["name"] for f in fields} | {"id", "type", "enabled", "alerts", "intake", "queue"}
    for key, value in (raw or {}).items():
        if key not in known:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value in ("", None):
            continue
        entry[key] = value
    return entry


def channel(eng, body):
    from . import channels as chmod
    action = str(body.get("action") or "add")
    cfg = eng.config()
    items = list(cfg.get("channels") or [])
    ids = [c.get("id") for c in items]
    raw = body.get("channel") if isinstance(body.get("channel"), dict) else {}
    if action == "remove":
        cfg["channels"] = [c for c in items if c.get("id") != raw.get("id")]
        eng.save_config(cfg)
        return {}
    if action == "toggle":
        for item in items:
            if item.get("id") == raw.get("id"):
                for key in ("enabled", "alerts", "intake"):
                    if key in raw:
                        item[key] = bool(raw[key])
        cfg["channels"] = items
        eng.save_config(cfg)
        return {}
    ctype = str(raw.get("type") or "")
    if ctype not in st.CHANNEL_TYPES:
        raise ApiError("Pick a channel type.")
    entry = _clean_entry(raw, chmod.required_fields(ctype))
    updating = action == "update" and entry.get("id") in ids
    if updating:
        entry = dict(next(c for c in items if c.get("id") == entry["id"]), **entry)
    else:
        entry["id"] = _new_id(ids, raw.get("id"), ctype)
    entry["type"] = ctype
    entry.setdefault("enabled", True)
    _store_secrets(entry, body.get("secrets"), "channel")
    if ctype == "email":
        for key, value in chmod.email_preset(entry.get("address", "")).items():
            entry.setdefault(key, value)
        if not entry.get("smtp_host") or not entry.get("imap_host"):
            raise ApiError("I do not know the mail servers for that address. Add the SMTP and IMAP server names.")
        entry.setdefault("owner", entry.get("address", ""))
    try:
        chan = chmod.build(entry, kv=eng.store)
    except ValueError as exc:
        raise ApiError(str(exc))
    note = ""
    if ctype == "telegram" and not entry.get("owner"):
        chat_id, name = chan.discover_chat()
        if not chat_id and chan.last_error:
            raise ApiError(chan.last_error, 422)
        if not chat_id:
            raise ApiError("Open your bot in Telegram, send it any message, then add it again.", 409)
        entry["owner"] = chat_id
        note = f"Found your chat{(' with ' + name) if name else ''}."
        chan = chmod.build(entry, kv=eng.store)
    ok, detail = chan.check()
    if not ok:
        raise ApiError(detail or "That did not connect.", 422)
    cfg["channels"] = [entry if c.get("id") == entry["id"] else c for c in items] if updating else items + [entry]
    eng.save_config(cfg)
    return {"channel": entry, "note": note or detail}


def source(eng, body):
    from . import sources as srcmod
    action = str(body.get("action") or "add")
    cfg = eng.config()
    items = list(cfg.get("sources") or [])
    ids = [s.get("id") for s in items]
    raw = body.get("source") if isinstance(body.get("source"), dict) else {}
    if action == "remove":
        cfg["sources"] = [s for s in items if s.get("id") != raw.get("id")]
        eng.save_config(cfg)
        return {}
    if action == "toggle":
        for item in items:
            if item.get("id") == raw.get("id"):
                item["enabled"] = bool(raw.get("enabled"))
        cfg["sources"] = items
        eng.save_config(cfg)
        return {}
    stype = str(raw.get("type") or "")
    if stype not in st.SOURCE_TYPES:
        raise ApiError("Pick a source type.")
    entry = _clean_entry(raw, srcmod.required_fields(stype))
    entry["id"] = _new_id(ids, raw.get("id"), stype)
    entry["type"] = stype
    entry.setdefault("enabled", True)
    if entry.get("queue"):
        entry["queue"] = str(entry["queue"]).upper()
    _store_secrets(entry, body.get("secrets"), "source")
    try:
        src = srcmod.build(entry, kv=eng.store)
    except ValueError as exc:
        raise ApiError(str(exc))
    ok, detail = src.check()
    if not ok:
        raise ApiError(detail or "That did not connect.", 422)
    cfg["sources"] = items + [entry]
    eng.save_config(cfg)
    return {"source": entry, "note": detail}


# -- actions ----------------------------------------------------------------

def test(eng, body):
    results = eng.test(str(body.get("channel") or ""))
    if not results:
        raise ApiError("Add a message channel first.")
    return {"results": results, "ok": all(r["ok"] for r in results)}


def customer(eng, body):
    action = str(body.get("action") or "")
    if action in ("add", "remove"):
        address = st.normalize_address(body.get("address"))
        if not address:
            raise ApiError("Give an email address, a phone number or @domain.")
        cfg = eng.config()
        known = [k for k in (cfg.get("customers") or {}).get("known") or []
                 if st.normalize_address(k.get("address")) != address]
        if action == "add":
            known.append({"address": address, "name": str(body.get("name") or "").strip()})
        cfg["customers"] = dict(cfg.get("customers") or {}, known=known)
        eng.save_config(cfg)
        return {"known": known}
    ref = str(body.get("ref") or "").strip().upper()
    if not ref:
        raise ApiError("Which ticket? Give its ref.")
    return eng.request_message(ref, str(body.get("kind") or "ask"), body.get("text"))


def report(eng, body):
    if not eng.config().get("queue"):
        raise ApiError("On-call has no queue yet. Run ccc oncall setup.")
    return {"ref": eng.report(body.get("text"), title=str(body.get("title") or ""),
                              queue=str(body.get("queue") or "").upper())}


def inbound(eng, body):
    """A message handed in by your own bridge, for a ``command`` channel."""
    entry = st.channel_by_id(eng.config(), str(body.get("channel") or ""))
    if entry is None or entry.get("type") != "command":
        raise ApiError("Inbound messages are accepted for command channels only.")
    msg = st.Inbound(channel=entry["id"], external_id=str(body.get("id") or ""),
                     sender=str(body.get("sender") or ""), text=str(body.get("text") or ""),
                     subject=str(body.get("subject") or ""), thread=str(body.get("thread") or ""),
                     sender_name=str(body.get("name") or ""), received_at=time.time())
    return {"new": eng.inbound(entry["id"], msg)}


def tick(eng, body):
    return {"tick": eng.tick(force=True)}


# -- what CCC tells On-call -------------------------------------------------

def observe_sessions(previous, current, describe=None, *, eng=None):
    """Session-state diff -> alerts for sessions that start waiting on a person.

    ``previous`` None is a baseline: nothing is raised. ``describe(sid)``
    returns (title, question). A session that stops waiting clears its alert.
    """
    eng = _engine(eng)
    if not eng.active():
        return 0
    raised = 0
    previous = previous if previous is not None else None
    for sid, cur in (current or {}).items():
        waiting = bool(cur.get("question_waiting"))
        was = bool(((previous or {}).get(sid) or {}).get("question_waiting"))
        if waiting and not was and previous is not None:
            title, question = describe(sid) if describe else ("", "")
            row = eng.raise_session("question", sid, title or "A session",
                                    question or "It asked a question. Open CCC to read it.",
                                    stamp=str(int(eng.clock())))
            raised += 1 if row else 0
        elif was and not waiting:
            close_session_alerts(eng, sid, "question")
    for sid in (previous or {}):
        if sid not in (current or {}) and (previous[sid] or {}).get("question_waiting"):
            close_session_alerts(eng, sid, "question")
    return raised


def close_session_alerts(eng, session_id, kind):
    for alert in eng.store.alerts(state="open"):
        if alert["kind"] == kind and alert.get("session_id") == session_id:
            eng.store.alert_close(alert["id"], "cleared", now=eng.clock())


def session_stopped(session_id, title, body, stamp="", *, eng=None):
    """CCC saw a session die mid-task."""
    eng = _engine(eng)
    return eng.raise_session("stopped", session_id, title, body, stamp=str(stamp))
