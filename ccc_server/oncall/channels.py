# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""On-call transports: send one message, poll for new inbound messages.

A channel knows how to reach one provider and nothing about what a message
means. Routing (which alert a reply answers, who may answer) lives elsewhere.

    email      SMTP out, IMAP in
    telegram   Bot API
    ntfy       publish to a topic, replies arrive on a second topic
    imessage   osascript out, chat.db in (macOS only)
    sms        Twilio Messages REST
    whatsapp   Twilio Messages REST with the whatsapp: address prefix
    command    any program that reads the message on stdin (send only)

Rules every channel keeps:

* ``poll()`` never raises and never returns history: the first poll only
  records where the provider's stream is now.
* A secret value is read from the Vault at call time, never stored on disk by
  this module, and scrubbed from every error string.
* ``poll()`` returns every new message on the channel, whoever sent it. The
  caller decides whether the sender may answer anything.

Stdlib-only. Nothing runs at import.
"""

from __future__ import annotations

import base64
import copy
import email
import email.policy
import email.utils
import hashlib
import html as _html
import http.client
import imaplib
import json
import os
import platform
import re
import secrets as _secrets
import shlex
import shutil
import smtplib
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from ccc_server.dbutil import connect_readonly
from ccc_server.oncall.store import Inbound, Receipt, normalize_address, vault_secrets

THREAD_RE = re.compile(r"\[#([0-9a-f]{16})\]")

ELLIPSIS = "…"
TELEGRAM_TEXT_LIMIT = 4096      # UTF-16 code units, per the Bot API
TWILIO_BODY_LIMIT = 1600
NTFY_MESSAGE_BYTES = 4000       # the server turns anything over 4096 bytes into an attachment
EMAIL_POLL_CAP = 50
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01
WHATSAPP_WINDOW_ERROR = ("WhatsApp only delivers free text within 24 hours of your last message "
                         "to this number. Message the number, or set a template.")
_TWILIO_WINDOW_CODE = 63016
_NO_THREAD = "0" * 16
_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
_OUR_MESSAGE_ID_RE = re.compile(r"ccc-oncall\.([0-9a-f]{16})\.")


# -- small helpers ----------------------------------------------------------

def _response_body(response):
    body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise OSError("Channel response is too large.")
    return body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A channel endpoint must not forward credentials or message text elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_request(method, url, *, headers=None, data=None, timeout=20):
    """The default HTTP seam: ``(status, body)``, also for 4xx and 5xx.

    Only a network failure raises, always as an ``OSError``.
    """
    if not str(url).lower().startswith(("http://", "https://")):
        raise OSError("Only http and https addresses are supported.")
    try:
        req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as resp:
            return resp.status, _response_body(resp)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, _response_body(exc)
        finally:
            exc.close()
    except (http.client.HTTPException, ValueError) as exc:
        raise OSError(type(exc).__name__) from None


def _clip(text, limit):
    """At most ``limit`` UTF-16 code units, never cutting a character in half."""
    text = str(text or "")
    if len(text) * 2 <= limit:
        return text
    units = [2 if ord(ch) > 0xFFFF else 1 for ch in text]
    if sum(units) <= limit:
        return text
    used, cut = 0, 0
    for index, size in enumerate(units):
        if used + size > limit - 1:
            break
        used, cut = used + size, index + 1
    return text[:cut].rstrip() + ELLIPSIS


def _clip_bytes(text, limit):
    """At most ``limit`` UTF-8 bytes, never cutting a character in half."""
    raw = str(text or "").encode("utf-8")
    if len(raw) <= limit:
        return str(text or "")
    tail = ELLIPSIS.encode("utf-8")
    return raw[:limit - len(tail)].decode("utf-8", "ignore").rstrip() + ELLIPSIS


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _json(body):
    """A JSON object from a response body, or {} when it is anything else."""
    try:
        data = json.loads(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
    except (ValueError, AttributeError):
        return {}
    return data if isinstance(data, dict) else {}


def _describe(exc):
    """A short plain reason for an exception. Never includes command arguments."""
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, Exception):
        exc = exc.reason
    if isinstance(exc, (socket.timeout, TimeoutError, subprocess.TimeoutExpired)):
        return "Timed out."
    if isinstance(exc, socket.gaierror):
        return "Server name not found."
    if isinstance(exc, ConnectionRefusedError):
        return "Connection refused."
    if isinstance(exc, ssl.SSLError):
        return "Secure connection failed."
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Login was rejected. Check the address and password."
    if isinstance(exc, imaplib.IMAP4.error):
        arg = exc.args[0] if exc.args else ""
        text = arg.decode("utf-8", "replace") if isinstance(arg, bytes) else str(arg)
        if re.search(r"auth|credential|login|password", text, re.I):
            return "Login was rejected. Check the address and password."
        return text or "Mail server error."
    if isinstance(exc, smtplib.SMTPResponseException):
        detail = exc.smtp_error
        detail = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
        return f"Mail server answered {exc.smtp_code}: {detail}"
    if isinstance(exc, FileNotFoundError):
        return "Program not found."
    return str(exc) or type(exc).__name__


def _text_hash(text):
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()


# -- reply stripping --------------------------------------------------------

_WROTE_RE = re.compile(
    r"^.{0,400}\b(?:wrote|schrieb|a écrit|a ecrit|escribió|escribio|ha scritto|schreef"
    r"|escreveu|skrev)\b.{0,160}:\s*$", re.I | re.S)
_LEAD_RE = re.compile(r"^(?:On|Am|Le|El|Il|Op|Em|Den)\s", re.I)
_ORIGINAL_RE = re.compile(
    r"^-{2,}\s*(?:Original Message|Forwarded message|Ursprüngliche Nachricht"
    r"|Weitergeleitete Nachricht|Mensaje original|Message d'origine|Message original"
    r"|Messaggio originale|Oorspronkelijk bericht)\s*-{2,}$", re.I)
_OUTLOOK_FROM_RE = re.compile(r"^\*{0,2}(?:From|Von|De|Da|Van)\*{0,2}\s*:\*{0,2}\s*\S", re.I)
_OUTLOOK_SENT_RE = re.compile(
    r"^\*{0,2}(?:Sent|Date|Gesendet|Datum|Envoyé|Enviado|Fecha|Inviato|Verzonden)\*{0,2}\s*:", re.I)
_OUTLOOK_ONE_LINE_RE = re.compile(r"^From:\s.+\bSent:\s", re.I)
_MOBILE_SIG_RE = re.compile(
    r"^(?:Sent from (?:my|Mail for|Outlook for|Yahoo Mail|Proton Mail|Gmail)\b.{0,40}"
    r"|Get Outlook for (?:iOS|Android).*"
    r"|Von meinem .{1,30} gesendet"
    r"|Envoyé (?:de|depuis) mon .{1,30}"
    r"|Enviado desde mi .{1,30}"
    r"|Inviato da(?:l mio)? .{1,30})\.?$", re.I)
_SEPARATOR_RE = re.compile(r"^[_\-=*]{5,}$")


def _hard_cut(lines, i):
    """True when line ``i`` starts a signature or a block of earlier mail."""
    line = lines[i].strip()
    if lines[i].rstrip("\r\n") in ("--", "-- "):
        return True
    if _ORIGINAL_RE.match(line) or _MOBILE_SIG_RE.match(line) or _OUTLOOK_ONE_LINE_RE.match(line):
        return True
    if _OUTLOOK_FROM_RE.match(line):
        return any(_OUTLOOK_SENT_RE.match(nxt.strip()) for nxt in lines[i + 1:i + 5])
    return False


def _quote_header(lines, i):
    """How many lines the "On <date> <person> wrote:" header at ``i`` spans, or 0."""
    first = lines[i].strip()
    if not first:
        return 0
    lead = bool(_LEAD_RE.match(first))
    if _WROTE_RE.match(first) and ("@" in first or (lead and re.search(r"\d", first))):
        return 1
    # Mail programs wrap a long header onto a second (rarely a third) short line.
    if not (lead or "@" in first) or first[-1] in ".!?":
        return 0
    joined = first
    for extra in (1, 2):
        if i + extra >= len(lines):
            return 0
        nxt = lines[i + extra].strip()
        if not nxt or len(nxt) > 78 or nxt.startswith(">"):
            return 0
        joined += " " + nxt
        if len(joined) > 400:
            return 0
        if _WROTE_RE.match(joined) and re.search(r"\d", joined):
            return extra + 1
    return 0


def _clean_lines(lines):
    kept = [ln.rstrip() for ln in lines if not ln.lstrip().startswith(">")]
    while kept and (not kept[-1].strip() or _SEPARATOR_RE.match(kept[-1].strip())):
        kept.pop()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def strip_reply(text):
    """The new part of a reply: quoted history and signatures removed.

    When nothing is left, the trimmed original is returned so a message is
    never silently emptied.
    """
    original = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not original:
        return ""
    lines = original.split("\n")
    header = None
    for i in range(len(lines)):
        if _hard_cut(lines, i):
            lines = lines[:i]
            break
        if header is None:
            span = _quote_header(lines, i)
            if span:
                header = (i, span)
    if header is not None and header[0] < len(lines):
        top = _clean_lines(lines[:header[0]])
        if top:
            return top
        # Nothing above the header: the person answered between the quoted lines.
        inline = _clean_lines(lines[:header[0]] + lines[header[0] + header[1]:])
        return inline or original
    return _clean_lines(lines) or original


def _html_to_text(markup):
    """A crude plain-text view of an HTML body, with quoted blocks dropped."""
    text = str(markup or "")[:200000]
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", "", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"(?is)<blockquote\b[^>]*>(?:(?!<blockquote\b).)*?</blockquote\s*>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|tr|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


# -- email presets ----------------------------------------------------------

def _preset(smtp_host, smtp_port, imap_host):
    return {"smtp_host": smtp_host, "smtp_port": smtp_port, "imap_host": imap_host, "imap_port": 993}


_EMAIL_PRESETS = {
    "gmail.com": _preset("smtp.gmail.com", 465, "imap.gmail.com"),
    "googlemail.com": _preset("smtp.gmail.com", 465, "imap.gmail.com"),
    "outlook.com": _preset("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "hotmail.com": _preset("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "live.com": _preset("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "office365.com": _preset("smtp.office365.com", 587, "outlook.office365.com"),
    "icloud.com": _preset("smtp.mail.me.com", 587, "imap.mail.me.com"),
    "me.com": _preset("smtp.mail.me.com", 587, "imap.mail.me.com"),
    "mac.com": _preset("smtp.mail.me.com", 587, "imap.mail.me.com"),
    "yahoo.com": _preset("smtp.mail.yahoo.com", 465, "imap.mail.yahoo.com"),
    "fastmail.com": _preset("smtp.fastmail.com", 465, "imap.fastmail.com"),
    "fastmail.fm": _preset("smtp.fastmail.com", 465, "imap.fastmail.com"),
    "zoho.com": _preset("smtp.zoho.com", 465, "imap.zoho.com"),
    "aol.com": _preset("smtp.aol.com", 465, "imap.aol.com"),
    "gmx.com": _preset("mail.gmx.com", 587, "imap.gmx.com"),
    "gmx.net": _preset("mail.gmx.net", 587, "imap.gmx.net"),
}


def email_preset(address):
    """Mail server settings guessed from the address's domain, or {}."""
    domain = normalize_address(address).rpartition("@")[2]
    return dict(_EMAIL_PRESETS.get(domain) or {})


# -- setup form fields ------------------------------------------------------

def _field(name, label, *, secret=False, optional=False, placeholder=""):
    return {"name": name, "label": label, "secret": secret, "optional": optional,
            "placeholder": placeholder}


_TWILIO_FIELDS = [
    _field("account_sid", "Account SID", placeholder="ACXXXXXXXXXXXXXXXX"),
    _field("auth_token_vault", "Auth token", secret=True),
    _field("from", "Twilio number", placeholder="+15550100"),
    _field("owner", "Your number", placeholder="+15550101"),
]

_FIELDS = {
    "email": [
        _field("address", "Mailbox address", placeholder="oncall@example.com"),
        _field("password_vault", "App password", secret=True),
        _field("owner", "Your email", placeholder="you@example.com"),
        _field("smtp_host", "SMTP server", optional=True, placeholder="smtp.example.com"),
        _field("smtp_port", "SMTP port", optional=True, placeholder="465"),
        _field("smtp_security", "SMTP security", optional=True, placeholder="tls"),
        _field("imap_host", "IMAP server", optional=True, placeholder="imap.example.com"),
        _field("imap_port", "IMAP port", optional=True, placeholder="993"),
        _field("username", "Login name", optional=True, placeholder="oncall@example.com"),
        _field("from_name", "Sender name", optional=True, placeholder="CCC On-call"),
        _field("folder", "Folder", optional=True, placeholder="INBOX"),
    ],
    "telegram": [
        _field("token_vault", "Bot token", secret=True),
        _field("owner", "Chat ID", optional=True, placeholder="123456789"),
        _field("api_base", "API address", optional=True, placeholder="https://api.telegram.org"),
    ],
    "ntfy": [
        _field("topic", "Topic", placeholder="my-oncall-topic"),
        _field("server", "Server", optional=True, placeholder="https://ntfy.sh"),
        _field("reply_topic", "Reply topic", optional=True, placeholder="my-oncall-topic-reply"),
        _field("token_vault", "Access token", secret=True, optional=True),
    ],
    "imessage": [
        _field("owner", "Phone or email", placeholder="+15550100"),
        _field("db_path", "Messages database", optional=True,
               placeholder="~/Library/Messages/chat.db"),
    ],
    "sms": _TWILIO_FIELDS + [
        _field("api_base", "API address", optional=True, placeholder="https://api.twilio.com"),
    ],
    "whatsapp": _TWILIO_FIELDS + [
        _field("template_sid", "Template SID", optional=True, placeholder="HXXXXXXXXXXXXXXXXX"),
        _field("api_base", "API address", optional=True, placeholder="https://api.twilio.com"),
    ],
    "command": [
        _field("send", "Command", placeholder="/usr/local/bin/notify-me"),
        _field("owner", "Recipient", optional=True, placeholder="you@example.com"),
    ],
}


for _kind in ('slack', 'discord', 'matrix', 'mattermost'):
    _FIELDS[_kind] = [
        _field('token_vault', 'Bot access token', secret=True),
        _field('owner', 'Your user ID'),
    ]
    if _kind != 'slack':
        _FIELDS[_kind].append(_field('channel_id', 'Channel ID'))
    _FIELDS[_kind].append(_field('api_base', 'Server URL', optional=_kind in ('slack', 'discord')))


def required_fields(channel_type):
    """The fields a setup form asks for, in order. Unknown type: []."""
    return copy.deepcopy(_FIELDS.get(str(channel_type or ""), []))


# -- base class -------------------------------------------------------------

class Channel:
    """One configured transport. Subclasses fill in _send, _poll and _check."""

    type = ""
    can_receive = False

    def __init__(self, cfg, *, secret=None, kv=None, http=None, clock=None, run=None):
        self.cfg = dict(cfg or {})
        self.secret = secret or vault_secrets
        self.kv = kv
        self.http = http or http_request
        self.clock = clock or time.time
        self.run = run or subprocess.run
        self.last_error = ""
        self._mem = {}
        self._poll_updates = None
        self._secret_values = set()
        self._note = ""

    @property
    def id(self):
        return str(self.cfg.get("id") or "")

    @property
    def owner(self):
        return normalize_address(self.cfg.get("owner"))

    @classmethod
    def missing(cls, cfg):
        """Names of required config fields that are empty."""
        return [f["name"] for f in _FIELDS.get(str(cfg.get("type") or cls.type), [])
                if not f["optional"] and not str(cfg.get(f["name"]) or "").strip()]

    # -- public surface --
    def check(self):
        """(ok, detail). Verifies credentials and reachability, sends nothing."""
        try:
            ok, detail = self._check()
        except Exception as exc:
            ok, detail = False, _describe(exc)
        detail = self._scrub(detail)
        self.last_error = "" if ok else (detail or "Check failed.")
        return bool(ok), detail

    def send(self, msg):
        try:
            receipt = self._send(msg)
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            receipt = Receipt(ok=False, error=_describe(exc),
                              uncertain=not isinstance(reason, ConnectionRefusedError))
        receipt.error = "" if receipt.ok else (self._scrub(receipt.error) or "Send failed.")
        self.last_error = receipt.error
        return receipt

    @property
    def durable_poll(self):
        return self.kv is not None and hasattr(self.kv, "stage_poll")

    def poll(self):
        """New inbound messages since the last poll. Never raises."""
        self._note = ""
        durable = self.durable_poll
        self._poll_updates = {} if durable else None
        try:
            out = list(self._poll() or [])
            if durable:
                self.kv.stage_poll(out, self._poll_updates)
            self.last_error = self._scrub(self._note)
            return out
        except Exception as exc:
            self.last_error = self._scrub(_describe(exc)) or "Poll failed."
            return []
        finally:
            self._poll_updates = None

    # -- subclass hooks --
    def _check(self):
        return True, ""

    def _send(self, msg):
        return Receipt(ok=False, error="This channel cannot send.")

    def _poll(self):
        return []

    # -- shared plumbing --
    def _opt(self, name, default=""):
        value = self.cfg.get(name)
        return str(value).strip() if value not in (None, "") else default

    def _secret(self, field):
        """The secret named by cfg[field], remembered so errors can be scrubbed."""
        name = self._opt(field)
        if not name:
            return ""
        try:
            value = str(self.secret(name) or "")
        except Exception:
            value = ""
        if value:
            self._secret_values.add(value)
        return value

    def _scrub(self, text):
        text = " ".join(str(text or "").split())
        for value in sorted(self._secret_values, key=len, reverse=True):
            if len(value) < 4:
                continue
            for form in (value, urllib.parse.quote(value, safe=""),
                         base64.b64encode(value.encode("utf-8")).decode("ascii")):
                text = text.replace(form, "***")
        return text[:300]

    def _key(self, name):
        return f"ch:{self.id}:{name}"

    def _get(self, name, default=None):
        key = self._key(name)
        if self._poll_updates is not None and key in self._poll_updates:
            return self._poll_updates[key]
        if self.kv is None:
            return self._mem.get(key, default)
        return self.kv.kv_get(key, default)

    def _set(self, name, value):
        key = self._key(name)
        if self._poll_updates is not None:
            self._poll_updates[key] = value
        elif self.kv is None:
            self._mem[key] = value
        else:
            self.kv.kv_set(key, value)

    def _remember(self, name, item, keep):
        """Append to a bounded list in kv and return the list."""
        items = [x for x in (self._get(name, []) or []) if x != item]
        items.append(item)
        items = items[-keep:]
        self._set(name, items)
        return items


# -- email ------------------------------------------------------------------

_AUTO_LOCAL_RE = re.compile(
    r"^(?:mailer-daemon|postmaster|no[-_.]?reply|do[-_.]?not[-_.]?reply)", re.I)


def _header(msg, name):
    try:
        return " ".join(str(msg.get(name) or "").split())
    except Exception:
        return ""


def _is_automatic(msg, sender):
    """True for mail no person typed: auto-replies, bounces, lists, robots."""
    auto = _header(msg, "Auto-Submitted").lower()
    if auto and not auto.startswith("no"):
        return True
    if _header(msg, "Precedence").lower() in ("bulk", "junk", "list", "auto_reply"):
        return True
    if any(msg.get(name) is not None for name in ("X-Autoreply", "X-Autorespond", "List-Unsubscribe")):
        return True
    if _header(msg, "Return-Path") == "<>":
        return True
    return bool(_AUTO_LOCAL_RE.match(sender.partition("@")[0]))


def _email_body(msg):
    """The plain-text body, falling back to a crude view of the HTML part."""
    for kind in ("plain", "html"):
        try:
            part = msg.get_body(preferencelist=(kind,))
        except Exception:
            part = None
        if part is None:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        content = str(content or "")
        if content.strip():
            return content if kind == "plain" else _html_to_text(content)
    return ""


class EmailChannel(Channel):
    type = "email"
    can_receive = True

    def __init__(self, cfg, **kw):
        super().__init__(cfg, **kw)
        preset = email_preset(self.cfg.get("address"))
        self.address = normalize_address(self.cfg.get("address"))
        self.username = self._opt("username", self.address)
        self.smtp_host = self._opt("smtp_host", preset.get("smtp_host", ""))
        self.smtp_port = _int(self.cfg.get("smtp_port"), preset.get("smtp_port", 587))
        self.smtp_security = self._opt("smtp_security", "tls" if self.smtp_port == 465 else "starttls")
        if self.smtp_security not in ("tls", "starttls"):
            raise ValueError("SMTP security must be tls or starttls. Set smtp_security to one of these.")
        self.imap_host = self._opt("imap_host", preset.get("imap_host", ""))
        self.imap_port = _int(self.cfg.get("imap_port"), preset.get("imap_port", 993))
        self.from_name = self._opt("from_name", "CCC On-call")
        self.folder = self._opt("folder", "INBOX")

    @classmethod
    def missing(cls, cfg):
        out = super().missing(cfg)
        preset = email_preset(cfg.get("address"))
        for name in ("smtp_host", "imap_host"):
            if not str(cfg.get(name) or "").strip() and not preset.get(name):
                out.append(name)
        return out

    # -- connections --
    def _smtp(self):
        password = self._secret("password_vault")
        if not password:
            raise OSError("No password is saved for this mailbox.")
        context = ssl.create_default_context()
        if self.smtp_security == "tls":
            conn = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20, context=context)
        else:
            conn = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20)
        try:
            if self.smtp_security == "starttls":
                conn.ehlo()
                conn.starttls(context=context)
                conn.ehlo()
            conn.login(self.username, password)
        except Exception:
            _quiet(conn.close)
            raise
        return conn

    def _imap(self):
        password = self._secret("password_vault")
        if not password:
            raise OSError("No password is saved for this mailbox.")
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port,
                                 ssl_context=ssl.create_default_context(), timeout=20)
        try:
            conn.login(self.username, password)
            folder = self.folder
            if re.search(r'[\s"\\]', folder) and not folder.startswith('"'):
                folder = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
            typ, data = conn.select(folder, readonly=True)
            if typ != "OK":
                raise OSError(f"Cannot open the folder {self.folder}.")
        except Exception:
            _quiet(conn.logout)
            raise
        return conn

    def _check(self):
        try:
            _quiet(self._smtp().quit)
        except Exception as exc:
            return False, "Sending (SMTP) failed: " + _describe(exc)
        try:
            _quiet(self._imap().logout)
        except Exception as exc:
            return False, "Receiving (IMAP) failed: " + _describe(exc)
        return True, "Sending and receiving both work."

    # -- send --
    def _send(self, msg):
        to = normalize_address(msg.to) or self.owner
        if not to:
            return Receipt(ok=False, error="No recipient address.")
        thread = str(msg.thread or "").strip()
        subject = " ".join(str(msg.subject or "").split())
        if not subject:
            first = next((ln.strip() for ln in str(msg.text or "").splitlines() if ln.strip()), "")
            subject = first[:80] or "CCC On-call"
        if thread and f"[#{thread}]" not in subject:
            subject = f"{subject} [#{thread}]"
        domain = self.address.rpartition("@")[2] or "localhost"
        token = thread if _TOKEN_RE.match(thread) else _NO_THREAD
        message_id = f"<ccc-oncall.{token}.{_secrets.token_hex(8)}@{domain}>"

        mail = EmailMessage()
        mail["From"] = email.utils.formataddr((self.from_name, self.address))
        mail["To"] = to
        mail["Subject"] = subject
        mail["Date"] = email.utils.formatdate(self.clock(), localtime=False)
        mail["Message-ID"] = message_id
        reply_to = " ".join(str(msg.reply_to or "").split())
        if reply_to:
            if not reply_to.startswith("<"):
                reply_to = f"<{reply_to}>"
            mail["In-Reply-To"] = reply_to
            mail["References"] = reply_to
        mail["X-CCC-Oncall"] = "out"
        mail.set_content(str(msg.text or ""))

        conn = self._smtp()
        try:
            conn.send_message(mail, from_addr=self.address, to_addrs=[to])
        finally:
            _quiet(conn.quit)
        return Receipt(ok=True, external_id=message_id)

    # -- poll --
    def _uidvalidity(self, conn):
        typ, data = conn.response("UIDVALIDITY")
        value = data[0] if data else None
        return _int(value.decode() if isinstance(value, bytes) else value, 0)

    def _highest_uid(self, conn):
        typ, data = conn.response("UIDNEXT")
        value = data[0] if data else None
        nxt = _int(value.decode() if isinstance(value, bytes) else value, 0)
        if nxt > 0:
            return nxt - 1
        uids = self._search(conn, "ALL")
        return max(uids) if uids else 0

    def _search(self, conn, *criteria):
        typ, data = conn.uid("SEARCH", None, *criteria)
        if typ != "OK":
            raise OSError("The mail server refused the search.")
        blob = b" ".join(part for part in (data or []) if isinstance(part, bytes))
        return sorted({int(tok) for tok in blob.split() if tok.isdigit()})

    def _poll(self):
        conn = self._imap()
        try:
            uidvalidity = self._uidvalidity(conn)
            cursor = self._get("cursor") or {}
            if not cursor or _int(cursor.get("uidvalidity"), -1) != uidvalidity:
                # First poll, or the server renumbered the folder: start from now.
                self._set("cursor", {"uidvalidity": uidvalidity, "last_uid": self._highest_uid(conn)})
                return []
            last = _int(cursor.get("last_uid"), 0)
            # "n:*" always includes the newest message, even when its UID is below n.
            uids = [u for u in self._search(conn, "UID", f"{last + 1}:*") if u > last][:EMAIL_POLL_CAP]
            out = []
            for uid in uids:
                try:
                    typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                except Exception as exc:
                    self._note = "Receiving (IMAP) failed: " + _describe(exc)
                    break
                raw = next((part[1] for part in (data or [])
                            if isinstance(part, tuple) and len(part) > 1), None)
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        inbound = self._inbound(bytes(raw), uidvalidity, uid)
                    except Exception:
                        inbound = None
                    if inbound is not None:
                        out.append(inbound)
                last = uid
            self._set("cursor", {"uidvalidity": uidvalidity, "last_uid": last})
            return out
        finally:
            _quiet(conn.close)
            _quiet(conn.logout)

    def _inbound(self, raw, uidvalidity, uid):
        """The Inbound for one raw message, or None when it must be ignored."""
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        message_id = _header(msg, "Message-ID")
        in_reply_to = _header(msg, "In-Reply-To")
        references = _header(msg, "References")
        if _OUR_MESSAGE_ID_RE.search(message_id):
            return None                       # a copy of something we sent
        name, addr = email.utils.parseaddr(_header(msg, "From"))
        sender = normalize_address(addr)
        if msg.get("X-CCC-Oncall") is not None:
            # Ours, even when the provider rewrote the Message-ID. Only a reply
            # from someone else that kept the header still counts.
            if sender == self.address or not (in_reply_to or references):
                return None
        if not sender or _is_automatic(msg, sender):
            return None
        text = strip_reply(_email_body(msg))
        if not text:
            return None
        subject = _header(msg, "Subject")
        found = THREAD_RE.search(subject)
        thread = found.group(1) if found else ""
        if not thread:
            for token in _OUR_MESSAGE_ID_RE.findall(in_reply_to + " " + references):
                if token != _NO_THREAD:
                    thread = token
                    break
        try:
            received = email.utils.parsedate_to_datetime(_header(msg, "Date")).timestamp()
        except Exception:
            received = self.clock()
        first_ref = re.search(r"<[^<>\s]+>", in_reply_to)
        return Inbound(
            channel=self.id, external_id=message_id or f"uid:{uidvalidity}:{uid}", sender=sender,
            text=text, subject=subject, thread=thread,
            reply_to=first_ref.group(0) if first_ref else in_reply_to,
            received_at=received, sender_name=name, raw={"uid": uid})


def _quiet(fn):
    try:
        fn()
    except Exception:
        pass


# -- telegram ---------------------------------------------------------------

def _telegram_name(chat, user=None):
    for source in (user or {}, chat or {}):
        full = " ".join(str(source.get(k) or "") for k in ("first_name", "last_name")).strip()
        label = full or str(source.get("title") or source.get("username") or "")
        if label:
            return label
    return ""


class TelegramChannel(Channel):
    type = "telegram"
    can_receive = True

    @property
    def owner(self):
        return str(self.cfg.get("owner") or "").strip()

    def _call(self, method, payload=None):
        """(ok, result, error) for one Bot API call."""
        token = self._secret("token_vault")
        if not token:
            return False, None, "No bot token is saved."
        base = self._opt("api_base", "https://api.telegram.org").rstrip("/")
        status, body = self.http(
            "POST", f"{base}/bot{token}/{method}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload or {}).encode("utf-8"), timeout=20)
        data = _json(body)
        if data.get("ok") is True:
            return True, data.get("result"), ""
        return False, None, "Telegram: " + str(data.get("description") or f"HTTP {status}")

    def _check(self):
        ok, result, error = self._call("getMe")
        if not ok:
            return False, error
        username = str((result or {}).get("username") or "")
        return True, f"Connected as @{username}." if username else "Connected."

    def _send(self, msg):
        chat = str(msg.to or "").strip() or self.owner
        if not chat:
            return Receipt(ok=False, error="No chat yet. Send the bot a message first.")
        payload = {
            "chat_id": int(chat) if re.fullmatch(r"-?\d+", chat) else chat,
            "text": _clip(msg.text, TELEGRAM_TEXT_LIMIT),
            "link_preview_options": {"is_disabled": True},
        }
        reply_to = str(msg.reply_to or "").strip()
        if reply_to.isdigit():
            payload["reply_parameters"] = {"message_id": int(reply_to),
                                           "allow_sending_without_reply": True}
        ok, result, error = self._call("sendMessage", payload)
        if not ok:
            return Receipt(ok=False, error=error)
        return Receipt(ok=True, external_id=str((result or {}).get("message_id") or ""))

    def _poll(self):
        cursor = self._get("update_id")
        if cursor is None:
            # offset=-1 returns only the newest update, so history is never read.
            ok, result, error = self._call(
                "getUpdates", {"offset": -1, "timeout": 0, "allowed_updates": ["message"]})
            if not ok:
                raise OSError(error)
            newest = max([_int(u.get("update_id")) for u in result or [] if isinstance(u, dict)] or [0])
            self._set("update_id", newest)
            return []
        ok, result, error = self._call(
            "getUpdates", {"offset": _int(cursor) + 1, "timeout": 0, "allowed_updates": ["message"]})
        if not ok:
            raise OSError(error)
        out, newest = [], _int(cursor)
        for update in result or []:
            if not isinstance(update, dict):
                continue
            update_id = _int(update.get("update_id"))
            newest = max(newest, update_id)
            message = update.get("message")
            if not isinstance(message, dict) or not str(message.get("text") or "").strip():
                continue
            chat = message.get("chat") or {}
            replied = message.get("reply_to_message") or {}
            out.append(Inbound(
                channel=self.id, external_id=str(update_id), sender=str(chat.get("id") or ""),
                text=str(message["text"]).strip(),
                reply_to=str(replied.get("message_id") or "") if replied else "",
                received_at=float(message.get("date") or self.clock()),
                sender_name=_telegram_name(chat, message.get("from")),
                raw={"message_id": message.get("message_id"), "chat_type": chat.get("type")}))
        if newest != _int(cursor):
            self._set("update_id", newest)
        return out

    def discover_chat(self):
        """(chat_id, name) of the newest private chat that wrote to the bot.

        Reads the pending updates without confirming them, so neither the
        saved cursor nor Telegram's own queue moves.
        """
        try:
            ok, result, error = self._call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
        except Exception as exc:
            ok, result, error = False, None, _describe(exc)
        if not ok:
            self.last_error = self._scrub(error)
            return "", ""
        self.last_error = ""
        for update in reversed(result or []):
            message = update.get("message") if isinstance(update, dict) else None
            chat = (message or {}).get("chat") or {}
            if chat.get("type") == "private" and chat.get("id") is not None:
                return str(chat["id"]), _telegram_name(chat, (message or {}).get("from"))
        return "", ""


# -- ntfy -------------------------------------------------------------------

_NTFY_THREAD_RE = re.compile(r"^\s*\[?#?([0-9a-f]{16})\]?(?![0-9a-f])[\s:,-]*", re.I)


class NtfyChannel(Channel):
    type = "ntfy"
    can_receive = True

    def __init__(self, cfg, **kw):
        super().__init__(cfg, **kw)
        self.server = self._opt("server", "https://ntfy.sh").rstrip("/")
        self.topic = self._opt("topic")
        self.reply_topic = self._opt("reply_topic", f"{self.topic}-reply")

    @classmethod
    def missing(cls, cfg):
        out = super().missing(cfg)
        topic = str(cfg.get("topic") or "").strip()
        if topic and str(cfg.get("reply_topic") or "").strip() == topic:
            # Reading replies from the alert topic would feed alerts back as answers.
            out.append("reply_topic (it must differ from topic)")
        return out

    def _headers(self, extra=None):
        headers = dict(extra or {})
        token = self._secret("token_vault")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _reply_url(self):
        return f"{self.server}/{urllib.parse.quote(self.reply_topic, safe='')}"

    def _fetch(self, since):
        url = f"{self._reply_url()}/json?poll=1&since={urllib.parse.quote(str(since), safe='')}"
        status, body = self.http("GET", url, headers=self._headers(), timeout=20)
        if status != 200:
            raise OSError("ntfy: " + str(_json(body).get("error") or f"HTTP {status}"))
        events = []
        for line in body.decode("utf-8", "replace").splitlines():
            event = _json(line)
            if event.get("event") == "message" and event.get("id"):
                events.append(event)
        return events

    def _check(self):
        self._fetch(int(self.clock()))
        return True, "Connected."

    def _send(self, msg):
        subject = " ".join(str(msg.subject or "").split())
        text = str(msg.text or "")
        if subject and text.startswith(subject):
            text = text[len(subject):].lstrip("\n")  # the title already says it
        payload = {"topic": self.topic, "message": _clip_bytes(text, NTFY_MESSAGE_BYTES)}
        if subject:
            payload["title"] = _clip_bytes(subject, 250)
        thread = str(msg.thread or "").strip()
        actions = []
        pairs = [(str(c[0]).strip(), str(c[1]).strip()) if isinstance(c, (tuple, list))
                 else (str(c).strip(),) * 2 for c in (msg.choices or ())]
        for choice, reply in [p for p in pairs if p[0] and p[1]][:3]:
            # The access token is deliberately left out: it would be readable by
            # every subscriber of the topic.
            actions.append({"action": "http", "label": choice, "url": self._reply_url(),
                            "method": "POST", "body": f"{thread} {reply}" if thread else reply,
                            "clear": True})
        if actions:
            payload["actions"] = actions
        status, body = self.http(
            "POST", self.server + "/", headers=self._headers({"Content-Type": "application/json"}),
            data=json.dumps(payload).encode("utf-8"), timeout=20)
        data = _json(body)
        if status != 200:
            return Receipt(ok=False, error="ntfy: " + str(data.get("error") or f"HTTP {status}"))
        return Receipt(ok=True, external_id=str(data.get("id") or ""))

    def _poll(self):
        cursor = self._get("cursor")
        if not isinstance(cursor, dict):
            # First poll: ask only for what arrives from now on and keep nothing.
            now = int(self.clock())
            events = self._fetch(now)
            newest = events[-1] if events else {}
            self._set("baseline", now)
            self._set("cursor", {"id": str(newest.get("id") or ""),
                                 "time": _int(newest.get("time"), now)})
            self._set("seen", [str(e["id"]) for e in events][-50:])
            return []
        last_id, last_time = str(cursor.get("id") or ""), _int(cursor.get("time"))
        baseline = self._get("baseline", last_time)
        self._set("baseline", baseline)
        events = self._fetch(last_id or last_time)
        seen = list(self._get("seen", []) or [])
        out = []
        for event in events:
            event_id, when = str(event["id"]), _int(event.get("time"))
            # A server that no longer knows our cursor id replays its whole cache.
            if event_id in seen or when < baseline:
                continue
            seen.append(event_id)
            last_id, last_time = event_id, max(last_time, when)
            text = str(event.get("message") or "").strip()
            found = _NTFY_THREAD_RE.match(text)
            thread = ""
            if found:
                thread, text = found.group(1).lower(), text[found.end():].strip()
            if not text:
                continue
            out.append(Inbound(
                channel=self.id, external_id=event_id, sender="ntfy", text=text,
                subject=str(event.get("title") or ""), thread=thread,
                received_at=float(when or self.clock())))
        self._set("cursor", {"id": last_id, "time": last_time})
        self._set("seen", seen[-50:])
        return out


# -- imessage ---------------------------------------------------------------

_IMESSAGE_SCRIPTS = (
    # macOS 11 and later
    """on run argv
    set theText to item 1 of argv
    set theBuddy to item 2 of argv
    tell application "Messages"
        set theService to 1st account whose service type = iMessage
        send theText to participant theBuddy of theService
    end tell
end run
""",
    # macOS 10.15 and earlier
    """on run argv
    set theText to item 1 of argv
    set theBuddy to item 2 of argv
    tell application "Messages"
        set theService to 1st service whose service type = iMessage
        send theText to buddy theBuddy of theService
    end tell
end run
""",
)


def decode_attributed_body(blob):
    """The text inside a Messages ``attributedBody`` typedstream blob, or ''."""
    try:
        data = bytes(blob or b"")
        at = data.find(b"NSString")
        if at < 0:
            return ""
        rest = data[at + len(b"NSString"):]
        plus = rest.find(b"+", 0, 12)       # the "+" type tag ends the class marker
        rest = rest[plus + 1:] if plus >= 0 else rest[5:]
        if not rest:
            return ""
        if rest[0] == 0x81:
            length, rest = int.from_bytes(rest[1:3], "little"), rest[3:]
        elif rest[0] == 0x82:
            length, rest = int.from_bytes(rest[1:5], "little"), rest[5:]
        else:
            length, rest = rest[0], rest[1:]
        return rest[:length].decode("utf-8", "replace")
    except Exception:
        return ""


def _system():
    return platform.system()


class IMessageChannel(Channel):
    type = "imessage"
    can_receive = True

    def _db_path(self):
        return os.path.expanduser(self._opt("db_path", "~/Library/Messages/chat.db"))

    def _open(self):
        """A read-only handle on chat.db, or None when it cannot be read."""
        path = self._db_path()
        if not os.path.isfile(path):     # sqlite would create an empty file otherwise
            return None
        con = connect_readonly(path, timeout=2)
        if con is None:
            return None
        try:
            con.execute("SELECT MAX(ROWID) FROM message").fetchone()
        except Exception:
            _quiet(con.close)
            return None
        return con

    def _check(self):
        if _system() != "Darwin":
            return False, "iMessage only works on a Mac."
        if not shutil.which("osascript"):
            return False, "osascript was not found on this Mac."
        con = self._open()
        if con is None:
            return False, ("Cannot read the Messages database. Give CCC Full Disk Access in "
                           "System Settings, then try again.")
        con.close()
        return True, "Messages is readable."

    def _send(self, msg):
        buddy = str(msg.to or "").strip() or str(self.cfg.get("owner") or "").strip()
        if not buddy:
            return Receipt(ok=False, error="No recipient.")
        text = str(msg.text or "")
        error = ""
        for script in _IMESSAGE_SCRIPTS:
            # The text and the recipient travel as arguments, never as script source.
            proc = self.run(["osascript", "-", text, buddy], input=script, text=True,
                            capture_output=True, timeout=30)
            if proc.returncode == 0:
                self._remember("sent", _text_hash(text), 50)
                return Receipt(ok=True, external_id="")
            error = error or str(proc.stderr or "").strip() or f"osascript exited {proc.returncode}."
        return Receipt(ok=False, error=error)

    def _poll(self):
        con = self._open()
        if con is None:
            raise OSError("Cannot read the Messages database. Give CCC Full Disk Access.")
        try:
            cursor = self._get("rowid")
            if cursor is None:
                row = con.execute("SELECT MAX(ROWID) FROM message").fetchone()
                self._set("rowid", int(row[0] or 0))
                return []
            columns = {r[1] for r in con.execute("PRAGMA table_info(message)")}
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            extra = ""
            for name in ("guid", "attributedBody", "associated_message_type"):
                extra += f", m.{name} AS {name}" if name in columns else f", NULL AS {name}"
            if {"chat", "chat_message_join"} <= tables:
                extra += (", (SELECT MAX(c.style) FROM chat_message_join j JOIN chat c"
                          " ON c.ROWID = j.chat_id WHERE j.message_id = m.ROWID) AS chat_style")
            else:
                extra += ", NULL AS chat_style"
            rows = con.execute(
                "SELECT m.ROWID AS rid, m.text AS text, m.is_from_me AS is_from_me, m.date AS date,"
                f" h.id AS handle{extra} FROM message m JOIN handle h ON m.handle_id = h.ROWID"
                " WHERE m.ROWID > ? ORDER BY m.ROWID ASC LIMIT 500", (int(cursor),)).fetchall()
        finally:
            _quiet(con.close)
        owner = self.owner
        sent = set(self._get("sent", []) or [])
        recent = list(self._get("recent", []) or [])
        out, last = [], int(cursor)
        for row in rows:
            last = int(row["rid"])
            if normalize_address(row["handle"]) != owner:
                continue
            if row["associated_message_type"]:      # a tapback, not a message
                continue
            if row["chat_style"] == 43:             # a group chat, not the direct line
                continue
            text = row["text"]
            if text is None:
                text = decode_attributed_body(row["attributedBody"])
            text = str(text or "").replace("￼", "").strip()
            if not text:
                continue
            digest = _text_hash(text)
            if digest in sent:
                # Ours. A chat with yourself stores a sent copy and a received copy.
                continue
            from_me = 1 if row["is_from_me"] else 0
            raw_date = float(row["date"] or 0)
            when = (raw_date / 1e9 if raw_date > 1e11 else raw_date) + APPLE_EPOCH_OFFSET
            if not raw_date:
                when = self.clock()
            echo = next((r for r in recent if r[0] == digest and r[1] != from_me
                         and abs(r[2] - when) <= 60), None)
            if echo is not None:
                recent.remove(echo)                 # the other copy of a note to self
                continue
            recent = (recent + [[digest, from_me, when]])[-20:]
            out.append(Inbound(
                channel=self.id, external_id=str(row["guid"] or f"rowid:{last}"), sender=owner,
                text=text, received_at=when, raw={"rowid": last, "is_from_me": bool(from_me)}))
        if last != int(cursor):
            self._set("rowid", last)
        self._set("recent", recent)
        return out


# -- twilio (sms and whatsapp) ----------------------------------------------

def _twilio_time(value, default):
    try:
        return email.utils.parsedate_to_datetime(str(value)).timestamp()
    except Exception:
        return default


class TwilioChannel(Channel):
    """SMS and WhatsApp through Twilio's Messages resource."""

    type = "sms"
    can_receive = True

    def __init__(self, cfg, **kw):
        super().__init__(cfg, **kw)
        if self.cfg.get("type") == "whatsapp":
            self.type = "whatsapp"
        self.whatsapp = self.type == "whatsapp"
        self.account_sid = self._opt("account_sid")
        self.template_sid = self._opt("template_sid") if self.whatsapp else ""

    def _address(self, value):
        number = normalize_address(value)
        return f"whatsapp:{number}" if self.whatsapp and number else number

    def _call(self, method, path, form=None, query=None):
        token = self._secret("auth_token_vault")
        if not token:
            raise OSError("No auth token is saved.")
        base = self._opt("api_base", "https://api.twilio.com").rstrip("/")
        url = f"{base}/2010-04-01/Accounts/{urllib.parse.quote(self.account_sid, safe='')}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        basic = base64.b64encode(f"{self.account_sid}:{token}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {basic}"}
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urllib.parse.urlencode(form).encode("utf-8")
        status, body = self.http(method, url, headers=headers, data=data, timeout=20)
        return status, _json(body)

    def _check(self):
        status, data = self._call("GET", ".json")
        if status == 200:
            name = str(data.get("friendly_name") or "")
            return True, f"Connected to {name}." if name else "Connected."
        if status in (401, 403):
            return False, "Twilio rejected the account SID or auth token."
        return False, "Twilio: " + str(data.get("message") or f"HTTP {status}")

    def _create(self, form):
        """(receipt, error code) for one create-message call."""
        status, data = self._call("POST", "/Messages.json", form=form)
        code = _int(data.get("code") if status >= 400 else data.get("error_code"))
        if 200 <= status < 300 and data.get("sid") and not code:
            return Receipt(ok=True, external_id=str(data["sid"])), 0
        reason = str(data.get("message") or data.get("error_message") or f"HTTP {status}")
        return Receipt(ok=False, error="Twilio: " + reason), code

    def _template(self, to, text):
        # A template variable may not hold line breaks, so the text is flattened.
        flat = _clip(" ".join(str(text or "").split()), 900)
        receipt, code = self._create([
            ("To", to), ("From", self._address(self.cfg.get("from"))),
            ("ContentSid", self.template_sid), ("ContentVariables", json.dumps({"1": flat}))])
        return receipt

    def _send(self, msg):
        to = self._address(msg.to) or self._address(self.cfg.get("owner"))
        if not to:
            return Receipt(ok=False, error="No recipient number.")
        text = _clip(msg.text, TWILIO_BODY_LIMIT)
        receipt, code = self._create(
            [("To", to), ("From", self._address(self.cfg.get("from"))), ("Body", text)])
        if self.whatsapp and code == _TWILIO_WINDOW_CODE:
            if self.template_sid:
                return self._template(to, text)
            return Receipt(ok=False, error=WHATSAPP_WINDOW_ERROR)
        if self.whatsapp and receipt.ok:
            # Twilio usually reports the 24 hour rule after accepting the message,
            # so the next polls look at how this one ended.
            pending = list(self._get("pending", []) or [])
            pending.append({"sid": receipt.external_id, "to": to, "text": text, "at": self.clock()})
            self._set("pending", pending[-10:])
        return receipt

    def _template_problem(self, item, error):
        self._note = error
        if self.kv is not None and hasattr(self.kv, "alert_upsert"):
            self.kv.alert_upsert(
                f"whatsapp-template-problem:{self.id}:{item['sid']}", kind="delivery_problem",
                title="WhatsApp delivery needs review", needs_reply=False,
                body=f"Fallback for message {item['sid']}: {error}. No automatic retry."
                     f"\n\nOriginal message:\n{item['text']}", now=self.clock())

    def _follow_up(self):
        """Retry accepted WhatsApp messages that later failed the 24 hour rule."""
        pending = list(self._get("pending", []) or [])
        if not pending:
            return
        keep = []
        for item in pending:
            if self.clock() - float(item.get("at") or 0) > 900:
                continue
            try:
                sid = urllib.parse.quote(str(item.get("sid") or ""), safe="")
                status, data = self._call("GET", f"/Messages/{sid}.json")
                if _int(data.get("error_code")) == _TWILIO_WINDOW_CODE:
                    if self.template_sid:
                        key = f"whatsapp-template:{self.id}:{item['sid']}"
                        if self.kv is not None and hasattr(self.kv, "claim_once"):
                            claimed = self.kv.claim_once(key)
                            previous = self.kv.kv_get(key)
                        else:
                            claimed = key not in self._mem
                            previous = self._mem.setdefault(key, True)
                        if not claimed:
                            if previous != "sent":
                                self._template_problem(item, "The fallback outcome is unknown")
                            continue
                        try:
                            receipt = self._template(item["to"], item["text"])
                        except Exception:
                            self._template_problem(item, "The fallback outcome is unknown")
                            continue
                        if receipt.ok:
                            # Commit outside the polling cursor buffer: this is an
                            # outbound side effect, not part of receiving a page.
                            if self.kv is not None:
                                self.kv.kv_set(key, "sent")
                            else:
                                self._mem[key] = "sent"
                        else:
                            self._template_problem(item, receipt.error)
                    else:
                        self._note = WHATSAPP_WINDOW_ERROR
                elif status == 200 and data.get("status") in ("accepted", "scheduled", "queued", "sending"):
                    keep.append(item)
            except Exception:
                keep.append(item)
        self._set("pending", keep)

    def _poll(self):
        if self.whatsapp:
            self._follow_up()
        cursor = self._get("cursor")
        first = cursor is None
        since = float(self.clock() if first else cursor)
        day = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%d")
        status, data = self._call("GET", "/Messages.json", query=[
            ("To", self._address(self.cfg.get("from"))), ("DateSent>", day), ("PageSize", "50")])
        if status != 200:
            raise OSError("Twilio: " + str(data.get("message") or f"HTTP {status}"))
        seen = list(self._get("seen", []) or [])
        known = set(seen)
        out, newest = [], since
        messages = [m for m in data.get("messages") or [] if isinstance(m, dict)]
        for message in reversed(messages):          # the list is newest first
            sid = str(message.get("sid") or "")
            if not sid or message.get("direction") != "inbound" or sid in known:
                continue
            known.add(sid)
            seen.append(sid)
            when = _twilio_time(message.get("date_sent") or message.get("date_created"), self.clock())
            newest = max(newest, when)
            body = str(message.get("body") or "").strip()
            if first or not body:
                continue
            out.append(Inbound(
                channel=self.id, external_id=sid, sender=normalize_address(message.get("from")),
                text=body, received_at=when))
        self._set("seen", seen[-300:])
        self._set("cursor", newest)
        return out


# -- command ----------------------------------------------------------------

class CommandChannel(Channel):
    type = "command"
    can_receive = False

    def _argv(self):
        argv = shlex.split(self._opt("send"))
        if argv:
            argv[0] = os.path.expanduser(argv[0])
        return argv

    def _check(self):
        argv = self._argv()
        if not argv:
            return False, "No command is set."
        if not shutil.which(argv[0]):
            return False, f"{os.path.basename(argv[0])} was not found."
        return True, "Command found."

    def _send(self, msg):
        argv = self._argv()
        if not argv:
            return Receipt(ok=False, error="No command is set.")
        env = dict(os.environ)
        env.update({"CCC_ONCALL_TO": str(msg.to or "") or self.owner,
                    "CCC_ONCALL_SUBJECT": str(msg.subject or ""),
                    "CCC_ONCALL_THREAD": str(msg.thread or "")})
        proc = self.run(argv, input=str(msg.text or ""), text=True, capture_output=True,
                        timeout=60, env=env)
        if proc.returncode == 0:
            return Receipt(ok=True, external_id="")
        detail = str(proc.stderr or "").strip().splitlines()
        return Receipt(ok=False, uncertain=True, error=f"Command exited {proc.returncode}."
                       + (f" {detail[-1]}" if detail else ""))


# -- factory ----------------------------------------------------------------

_CLASSES = {
    "email": EmailChannel,
    "telegram": TelegramChannel,
    "ntfy": NtfyChannel,
    "imessage": IMessageChannel,
    "sms": TwilioChannel,
    "whatsapp": TwilioChannel,
    "command": CommandChannel,
}


def build(cfg, **kw):
    """The channel object for one config entry. ValueError names what is wrong."""
    cfg = cfg if isinstance(cfg, dict) else {}
    ctype = str(cfg.get("type") or "")
    if ctype in ('slack', 'discord', 'matrix', 'mattermost'):
        from importlib import import_module
        module = import_module('.' + ctype, __package__)
        cls = getattr(module, ctype.capitalize() + 'Channel')
    else:
        cls = _CLASSES.get(ctype)
    if cls is None:
        raise ValueError(f"Unknown channel type {ctype!r}.")
    missing = cls.missing(cfg)
    if missing:
        label = str(cfg.get("id") or ctype)
        raise ValueError(f"Channel {label!r} is missing {', '.join(missing)}.")
    return cls(cfg, **kw)
