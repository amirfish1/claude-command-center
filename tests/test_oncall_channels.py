# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""On-call channels: every transport against a fake, never the network."""

from __future__ import annotations

import base64
import imaplib
import json
import re
import shlex
import smtplib
import socket
import sqlite3
import sys
import threading
import types
import urllib.parse
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ccc_server.oncall import channels, store
from ccc_server.oncall.store import Outbound

NOW = 1700000000.0          # 2023-11-14 22:13:20 UTC
TOKEN = "0123456789abcdef"
SECRETS = {
    "mail-pw": "pw-s3cret-value",
    "tg-token": "111222:TESTTOKEN-not-real",
    "ntfy-token": "tk_testtoken_not_real",
    "twilio-token": "twilio-test-token-not-real",
}


def secret(name):
    return SECRETS.get(name, "")


def clock():
    return NOW


@pytest.fixture
def kv(tmp_path):
    s = store.Store(tmp_path / "oncall")
    yield s
    s.close()


def make(cfg, kv=None, **kw):
    kw.setdefault("secret", secret)
    kw.setdefault("clock", clock)
    return channels.build(cfg, kv=kv, **kw)


# -- a tiny local web server ------------------------------------------------

class Web:
    def __init__(self):
        self.requests = []
        self.replies = []          # queued (status, payload); the last one repeats
        self.base = ""

    def reply(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.replies.append((status, body))

    def next_reply(self):
        if not self.replies:
            return 200, b"{}"
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def json(self, index=-1):
        return json.loads(self.requests[index]["body"])

    def form(self, index=-1):
        return urllib.parse.parse_qs(self.requests[index]["body"].decode(), keep_blank_values=True)


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    state = Web()

    class Handler(BaseHTTPRequestHandler):
        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            state.requests.append({
                "method": self.command, "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": self.rfile.read(length) if length else b""})
            status, body = state.next_reply()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _handle

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    state.base = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


# -- strip_reply ------------------------------------------------------------

def test_strip_reply_gmail():
    body = (
        "Yes, roll it back.\n"
        "\n"
        "On Mon, Jan 5, 2026 at 10:00 AM CCC On-call <oncall@example.com> wrote:\n"
        "\n"
        "> The deploy failed. Roll back?\n"
        "> Reply yes or no.\n")
    assert channels.strip_reply(body) == "Yes, roll it back."


def test_strip_reply_gmail_header_wrapped_over_two_lines():
    body = (
        "Go ahead\n\n"
        "On Mon, Jan 5, 2026 at 10:00 AM CCC On-call <oncall@example.com>\n"
        "wrote:\n"
        "> Ship it?\n")
    assert channels.strip_reply(body) == "Go ahead"


def test_strip_reply_apple_mail_with_phone_signature():
    body = (
        "No, wait for me.\r\n"
        "\r\n"
        "Sent from my iPhone\r\n"
        "\r\n"
        "> On Jan 5, 2026, at 10:00 AM, CCC On-call <oncall@example.com> wrote:\r\n"
        "> \r\n"
        "> Ship it?\r\n")
    assert channels.strip_reply(body) == "No, wait for me."
    plain = "Fine by me\n\nOn Jan 5, 2026, at 10:00 AM, CCC On-call <oncall@example.com> wrote:\n\n> Ship it?"
    assert channels.strip_reply(plain) == "Fine by me"


def test_strip_reply_outlook_blocks():
    body = (
        "Approved.\n"
        "\n"
        "________________________________\n"
        "From: CCC On-call <oncall@example.com>\n"
        "Sent: Monday, January 5, 2026 10:00 AM\n"
        "To: Sam Example <sam@example.com>\n"
        "Subject: Deploy failed\n"
        "\n"
        "The deploy failed. Roll back?\n")
    assert channels.strip_reply(body) == "Approved."
    original = "Option 2 please\n\n-----Original Message-----\nFrom: oncall@example.com\nShip it?"
    assert channels.strip_reply(original) == "Option 2 please"
    flat = "Do it\n\nFrom: CCC On-call <oncall@example.com> Sent: Monday To: Sam Subject: Deploy\nold text"
    assert channels.strip_reply(flat) == "Do it"


@pytest.mark.parametrize("header", [
    "Am 05.01.2026 um 10:00 schrieb CCC On-call <oncall@example.com>:",
    "Am Mo., 5. Jan. 2026 um 10:00 Uhr schrieb CCC On-call <oncall@example.com>:",
    "Le lun. 5 janv. 2026 à 10:00, CCC On-call <oncall@example.com> a écrit :",
    "El lun, 5 ene 2026 a las 10:00, CCC On-call (<oncall@example.com>) escribió:",
])
def test_strip_reply_other_languages(header):
    body = f"Ja, bitte.\n\n{header}\n> Ship it?\n> Second line\n"
    assert channels.strip_reply(body) == "Ja, bitte."


def test_strip_reply_signature_and_inline_answers():
    assert channels.strip_reply("Yes\n-- \nSam Example\nExample Corp") == "Yes"
    inline = (
        "On Mon, Jan 5, 2026 at 10:00 AM CCC On-call <oncall@example.com> wrote:\n"
        "> Roll back?\n"
        "Yes\n"
        "> Tell the customer?\n"
        "No\n")
    assert channels.strip_reply(inline) == "Yes\nNo"


def test_strip_reply_never_returns_empty_and_leaves_plain_text_alone():
    quoted_only = "> Ship it?\n> Reply yes or no."
    assert channels.strip_reply(quoted_only) == quoted_only
    assert channels.strip_reply("") == ""
    assert channels.strip_reply(None) == ""
    plain = "On Monday I fixed 3 bugs.\nThe customer wrote back and is happy.\nFrom: here it is easy."
    assert channels.strip_reply(plain) == plain
    assert channels.strip_reply("  yes  \n\n") == "yes"


def test_thread_re():
    assert channels.THREAD_RE.search(f"Re: Deploy [#{TOKEN}]").group(1) == TOKEN
    assert channels.THREAD_RE.search("Re: Deploy [#123]") is None
    assert channels.THREAD_RE.pattern == r"\[#([0-9a-f]{16})\]"


# -- presets, fields, build -------------------------------------------------

@pytest.mark.parametrize("address,smtp_host,smtp_port,imap_host", [
    ("me@gmail.com", "smtp.gmail.com", 465, "imap.gmail.com"),
    ("Me <me@GoogleMail.com>", "smtp.gmail.com", 465, "imap.gmail.com"),
    ("me@outlook.com", "smtp-mail.outlook.com", 587, "outlook.office365.com"),
    ("me@hotmail.com", "smtp-mail.outlook.com", 587, "outlook.office365.com"),
    ("me@live.com", "smtp-mail.outlook.com", 587, "outlook.office365.com"),
    ("me@office365.com", "smtp.office365.com", 587, "outlook.office365.com"),
    ("me@icloud.com", "smtp.mail.me.com", 587, "imap.mail.me.com"),
    ("me@me.com", "smtp.mail.me.com", 587, "imap.mail.me.com"),
    ("me@mac.com", "smtp.mail.me.com", 587, "imap.mail.me.com"),
    ("me@yahoo.com", "smtp.mail.yahoo.com", 465, "imap.mail.yahoo.com"),
    ("me@fastmail.com", "smtp.fastmail.com", 465, "imap.fastmail.com"),
    ("me@fastmail.fm", "smtp.fastmail.com", 465, "imap.fastmail.com"),
    ("me@zoho.com", "smtp.zoho.com", 465, "imap.zoho.com"),
    ("me@aol.com", "smtp.aol.com", 465, "imap.aol.com"),
    ("me@gmx.com", "mail.gmx.com", 587, "imap.gmx.com"),
    ("me@gmx.net", "mail.gmx.net", 587, "imap.gmx.net"),
])
def test_email_preset_known(address, smtp_host, smtp_port, imap_host):
    assert channels.email_preset(address) == {
        "smtp_host": smtp_host, "smtp_port": smtp_port, "imap_host": imap_host, "imap_port": 993}


@pytest.mark.parametrize("address", ["me@proton.me", "me@protonmail.com", "me@example.com", "", None])
def test_email_preset_unknown(address):
    assert channels.email_preset(address) == {}


def test_email_preset_returns_a_copy():
    channels.email_preset("me@gmail.com")["smtp_host"] = "changed"
    assert channels.email_preset("me@gmail.com")["smtp_host"] == "smtp.gmail.com"


def test_required_fields_shape():
    for ctype in store.CHANNEL_TYPES:
        fields = channels.required_fields(ctype)
        assert fields, ctype
        for f in fields:
            assert set(f) == {"name", "label", "secret", "optional", "placeholder"}
            assert 1 <= len(f["label"].split()) <= 3
            assert "—" not in f["label"] + f["placeholder"]
            assert f["secret"] is (f["name"].endswith("_vault"))
    assert [f["name"] for f in channels.required_fields("email")][:3] == [
        "address", "password_vault", "owner"]
    assert channels.required_fields("nope") == []
    channels.required_fields("email")[0]["label"] = "changed"
    assert channels.required_fields("email")[0]["label"] != "changed"


MINIMAL = {
    "email": {"address": "oncall@gmail.com", "password_vault": "mail-pw", "owner": "sam@example.com"},
    "telegram": {"token_vault": "tg-token"},
    "ntfy": {"topic": "alerts"},
    "imessage": {"owner": "+15550100"},
    "sms": {"account_sid": "ACtest", "auth_token_vault": "twilio-token", "from": "+15550100",
            "owner": "+15550101"},
    "whatsapp": {"account_sid": "ACtest", "auth_token_vault": "twilio-token", "from": "+15550100",
                 "owner": "+15550101"},
    "command": {"send": "true"},
}


def test_build_every_type():
    assert set(MINIMAL) == set(store.CHANNEL_TYPES)
    for ctype, cfg in MINIMAL.items():
        ch = make(dict(cfg, id=f"my-{ctype}", type=ctype))
        assert isinstance(ch, channels.Channel)
        assert ch.type == ctype
        assert ch.id == f"my-{ctype}"
        assert ch.can_receive is (ctype != "command")
        assert ch.last_error == ""


def test_build_errors_name_the_problem():
    with pytest.raises(ValueError, match="carrier-pigeon"):
        channels.build({"id": "x", "type": "carrier-pigeon"})
    with pytest.raises(ValueError):
        channels.build(None)
    for ctype, cfg in MINIMAL.items():
        for name in cfg:
            broken = dict(cfg, id="x", type=ctype)
            broken[name] = "  "
            with pytest.raises(ValueError, match=re.escape(name)):
                channels.build(broken)
    # An unknown mail domain has no preset, so the servers must be given.
    custom = {"id": "m", "type": "email", "address": "oncall@example.com",
              "password_vault": "mail-pw", "owner": "sam@example.com"}
    with pytest.raises(ValueError, match="smtp_host"):
        channels.build(custom)
    with pytest.raises(ValueError, match="imap_host"):
        channels.build(dict(custom, smtp_host="smtp.example.com"))
    assert channels.build(dict(custom, smtp_host="smtp.example.com", imap_host="imap.example.com"))
    with pytest.raises(ValueError, match="reply_topic"):
        channels.build({"id": "n", "type": "ntfy", "topic": "alerts", "reply_topic": "alerts"})


def test_owner_is_normalized_and_cursors_are_namespaced(kv):
    ch = make({"id": "mail", "type": "command", "send": "true", "owner": "Sam <SAM@Example.com>"}, kv)
    assert ch.owner == "sam@example.com"
    ch._set("cursor", {"a": 1})
    assert kv.kv_get("ch:mail:cursor") == {"a": 1}
    memory = make({"id": "mail", "type": "command", "send": "true"})
    memory._set("cursor", 5)
    assert memory._get("cursor") == 5


def test_default_http_reports_status_and_raises_only_for_network(web):
    web.reply(404, {"error": "nope"})
    status, body = channels.http_request("GET", web.base + "/missing")
    assert status == 404 and json.loads(body) == {"error": "nope"}
    with pytest.raises(OSError):
        channels.http_request("GET", "file:///etc/hostname")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(OSError):
        channels.http_request("GET", f"http://127.0.0.1:{port}/", timeout=2)


def test_clip_helpers():
    assert channels._clip("short", 10) == "short"
    clipped = channels._clip("a" * 5000, 4096)
    assert len(clipped) == 4096 and clipped.endswith(channels.ELLIPSIS)
    faces = channels._clip("\U0001F600" * 3000, 4096)
    assert len(faces.encode("utf-16-le")) // 2 <= 4096
    assert faces.endswith(channels.ELLIPSIS)
    faces.encode("utf-8")       # no lone surrogate
    by_bytes = channels._clip_bytes("é" * 3000, 4000)
    assert len(by_bytes.encode("utf-8")) <= 4000 and by_bytes.endswith(channels.ELLIPSIS)


# -- email ------------------------------------------------------------------

class Mailbox:
    def __init__(self):
        self.smtp_password = self.imap_password = SECRETS["mail-pw"]
        self.uidvalidity = 7
        self.next_uid = 1
        self.messages = {}
        self.smtp = []          # (class name, host, port)
        self.starttls = 0
        self.sent = []          # (message, from_addr, to_addrs)
        self.imap = []          # (host, port)
        self.selects = []
        self.fetches = []
        self.fail_fetch_uid = None
        self.uidnext_known = True

    def add(self, raw):
        uid = self.next_uid
        self.next_uid += 1
        self.messages[uid] = raw
        return uid


_mail_counter = iter(range(1, 100000))


def mail_bytes(body="hello", html=None, **fields):
    msg = EmailMessage()
    headers = {name.replace("_", "-"): value for name, value in fields.items()}
    headers.setdefault("From", "Sam Example <sam@example.com>")
    headers.setdefault("To", "oncall@gmail.com")
    headers.setdefault("Subject", "Re: Deploy failed")
    headers.setdefault("Date", "Tue, 14 Nov 2023 22:00:00 +0000")
    headers.setdefault("Message-ID", f"<auto-{next(_mail_counter)}@example.com>")
    for name, value in headers.items():
        if value is not None:
            msg[name] = value
    if html is not None and body is None:
        msg.set_content(html, subtype="html")
    else:
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


@pytest.fixture
def box(monkeypatch):
    state = Mailbox()

    class FakeSMTP:
        def __init__(self, host, port, **kw):
            state.smtp.append((type(self).__name__, host, port))

        def ehlo(self):
            pass

        def starttls(self, **kw):
            state.starttls += 1

        def login(self, user, password):
            if password != state.smtp_password:
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 rejected " + password.encode())

        def send_message(self, msg, from_addr=None, to_addrs=None):
            state.sent.append((msg, from_addr, to_addrs))

        def quit(self):
            pass

        close = quit

    class FakeSMTPSSL(FakeSMTP):
        pass

    class FakeIMAP:
        def __init__(self, host, port=993, **kw):
            state.imap.append((host, port))

        def login(self, user, password):
            if password != state.imap_password:
                raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials " + password.encode())

        def select(self, mailbox="INBOX", readonly=False):
            state.selects.append((mailbox, readonly))
            return "OK", [str(len(state.messages)).encode()]

        def response(self, code):
            if code == "UIDVALIDITY":
                return code, [str(state.uidvalidity).encode()]
            if code == "UIDNEXT" and state.uidnext_known:
                return code, [str(state.next_uid).encode()]
            return code, [None]

        def uid(self, command, *args):
            if command == "SEARCH":
                if args[-1] == "ALL":
                    found = sorted(state.messages)
                else:
                    low = int(args[-1].split(":")[0])
                    found = [u for u in sorted(state.messages) if u >= low]
                    if not found and state.messages:      # "n:*" always names the newest
                        found = [max(state.messages)]
                return "OK", [" ".join(str(u) for u in found).encode()]
            assert command == "FETCH"
            uid = int(args[0])
            state.fetches.append((uid, args[1]))
            if uid == state.fail_fetch_uid:
                raise imaplib.IMAP4.abort("connection lost")
            raw = state.messages.get(uid)
            if raw is None:
                return "OK", [None]
            return "OK", [(b"1 (UID %d BODY[] {%d}" % (uid, len(raw)), raw), b")"]

        def close(self):
            pass

        def logout(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return state


EMAIL_CFG = {"id": "mail", "type": "email", "address": "oncall@gmail.com",
             "password_vault": "mail-pw", "owner": "Sam Example <sam@example.com>"}


def test_email_send_headers(box, kv):
    ch = make(EMAIL_CFG, kv)
    receipt = ch.send(Outbound(text="The deploy failed.\nRoll back?", subject="Deploy failed",
                               thread=TOKEN, reply_to="<earlier@example.com>"))
    assert receipt.ok and ch.last_error == ""
    assert box.smtp == [("FakeSMTPSSL", "smtp.gmail.com", 465)] and box.starttls == 0
    mail, from_addr, to_addrs = box.sent[0]
    assert from_addr == "oncall@gmail.com" and to_addrs == ["sam@example.com"]
    assert mail["From"] == "CCC On-call <oncall@gmail.com>"
    assert mail["To"] == "sam@example.com"
    assert mail["Subject"] == f"Deploy failed [#{TOKEN}]"
    assert mail["In-Reply-To"] == "<earlier@example.com>"
    assert mail["References"] == "<earlier@example.com>"
    assert mail["X-CCC-Oncall"] == "out"
    assert mail["Auto-Submitted"] is None
    assert re.fullmatch(rf"<ccc-oncall\.{TOKEN}\.[0-9a-f]{{16}}@gmail\.com>", mail["Message-ID"])
    assert receipt.external_id == mail["Message-ID"]
    assert mail.get_content().strip() == "The deploy failed.\nRoll back?"


def test_email_send_defaults(box):
    ch = make(dict(EMAIL_CFG, smtp_host="smtp.example.com", smtp_port="587", from_name="Storefront bot"))
    long_line = "x" * 200
    receipt = ch.send(Outbound(text=f"\n{long_line}\nmore", to="Kim <kim@example.com>"))
    assert receipt.ok
    assert box.smtp == [("FakeSMTP", "smtp.example.com", 587)] and box.starttls == 1
    mail, _, to_addrs = box.sent[0]
    assert to_addrs == ["kim@example.com"]
    assert mail["Subject"] == "x" * 80
    assert mail["From"] == "Storefront bot <oncall@gmail.com>"
    assert mail["In-Reply-To"] is None
    assert re.fullmatch(r"<ccc-oncall\.0{16}\.[0-9a-f]{16}@gmail\.com>", mail["Message-ID"])
    # A subject that already carries the token is left alone.
    ch.send(Outbound(text="t", subject=f"Re: Deploy [#{TOKEN}]", thread=TOKEN))
    assert box.sent[1][0]["Subject"] == f"Re: Deploy [#{TOKEN}]"
    # A line break in a subject cannot add a header.
    ch.send(Outbound(text="t", subject="Hi\r\nBcc: other@example.com"))
    assert box.sent[2][0]["Bcc"] is None and box.sent[2][0]["Subject"] == "Hi Bcc: other@example.com"


def test_email_first_poll_never_reads_history(box, kv):
    box.add(mail_bytes("old one"))
    box.add(mail_bytes("old two"))
    ch = make(EMAIL_CFG, kv)
    assert ch.poll() == []
    assert ch.last_error == ""
    assert box.fetches == []
    assert box.selects == [("INBOX", True)]
    assert kv.kv_get("ch:mail:cursor") == {"uidvalidity": 7, "last_uid": 2}
    assert ch.poll() == [] and box.fetches == []


def test_email_first_poll_without_uidnext_falls_back_to_search(box, kv):
    box.uidnext_known = False
    box.add(mail_bytes("old one"))
    box.add(mail_bytes("old two"))
    box.add(mail_bytes("old three"))
    ch = make(EMAIL_CFG, kv)
    assert ch.poll() == []
    assert kv.kv_get("ch:mail:cursor") == {"uidvalidity": 7, "last_uid": 3}


def test_email_reply_returned_once_with_thread_from_subject(box, kv):
    box.add(mail_bytes("old"))
    ch = make(EMAIL_CFG, kv)
    assert ch.poll() == []
    body = (
        "Yes, roll it back.\n\n"
        "On Tue, Nov 14, 2023 at 9:50 PM CCC On-call <oncall@gmail.com> wrote:\n"
        "> The deploy failed.\n> Roll back?\n")
    box.add(mail_bytes(body, Subject=f"Re: Deploy failed [#{TOKEN}]",
                       Message_ID="<reply-1@example.com>",
                       In_Reply_To=f"<ccc-oncall.{TOKEN}.aaaaaaaaaaaaaaaa@gmail.com>"))
    got = ch.poll()
    assert len(got) == 1
    msg = got[0]
    assert msg.channel == "mail"
    assert msg.external_id == "<reply-1@example.com>"
    assert msg.sender == "sam@example.com" and msg.sender_name == "Sam Example"
    assert msg.text == "Yes, roll it back."
    assert msg.subject == f"Re: Deploy failed [#{TOKEN}]"
    assert msg.thread == TOKEN
    assert msg.reply_to == f"<ccc-oncall.{TOKEN}.aaaaaaaaaaaaaaaa@gmail.com>"
    assert msg.received_at == 1699999200.0
    assert box.fetches == [(2, "(BODY.PEEK[])")]        # never marks the mail as read
    assert all(readonly for _, readonly in box.selects)
    assert ch.poll() == []
    assert len(box.fetches) == 1


def test_email_thread_recovered_from_references(box, kv):
    ch = make(EMAIL_CFG, kv)
    ch.poll()
    other = "fedcba9876543210"
    box.add(mail_bytes("first", Subject="Re: Deploy failed", Message_ID="<r1@example.com>",
                       In_Reply_To="<unrelated@example.com>",
                       References=f"<a@example.com> <ccc-oncall.{TOKEN}.bbbbbbbbbbbbbbbb@gmail.com>"))
    box.add(mail_bytes("second", Subject="Re: hello", Message_ID="<r2@example.com>",
                       In_Reply_To=f"<ccc-oncall.{other}.cccccccccccccccc@gmail.com>"))
    box.add(mail_bytes("third", Subject="Re: hello", Message_ID="<r3@example.com>",
                       In_Reply_To=f"<ccc-oncall.{'0' * 16}.dddddddddddddddd@gmail.com>"))
    box.add(mail_bytes("fourth", Subject="New question", Message_ID=None, Date="not a date"))
    got = ch.poll()
    assert [m.thread for m in got] == [TOKEN, other, "", ""]
    assert got[0].reply_to == "<unrelated@example.com>"
    assert got[3].external_id == "uid:7:4" and got[3].received_at == NOW and got[3].reply_to == ""


def test_email_skips_own_outbound_and_automatic_mail(box, kv):
    ch = make(EMAIL_CFG, kv)
    ch.poll()
    ours = f"<ccc-oncall.{TOKEN}.aaaaaaaaaaaaaaaa@gmail.com>"
    skipped = [
        mail_bytes("our alert", From="CCC On-call <oncall@gmail.com>", Message_ID=ours, X_CCC_Oncall="out"),
        # A provider that rewrote our Message-ID: still ours, even though it threads.
        mail_bytes("our follow up", From="CCC On-call <oncall@gmail.com>",
                   Message_ID="<rewritten@mail.example.net>", X_CCC_Oncall="out", In_Reply_To=ours),
        mail_bytes("header but no thread", Message_ID="<x1@example.com>", X_CCC_Oncall="out"),
        mail_bytes("I am away", Message_ID="<a1@example.com>", Auto_Submitted="auto-replied"),
        mail_bytes("newsletter", Message_ID="<a2@example.com>", Precedence="bulk"),
        mail_bytes("away", Message_ID="<a3@example.com>", X_Autoreply="yes"),
        mail_bytes("away", Message_ID="<a4@example.com>", X_Autorespond="vacation"),
        mail_bytes("bounce", Message_ID="<a5@example.com>", From="MAILER-DAEMON@example.com"),
        mail_bytes("bounce", Message_ID="<a6@example.com>", From="Postmaster <postmaster@example.com>"),
        mail_bytes("receipt", Message_ID="<a7@example.com>", From="Shop <no-reply@example.com>"),
        mail_bytes("receipt", Message_ID="<a8@example.com>", From="noreply@example.com"),
        mail_bytes("receipt", Message_ID="<a9@example.com>", From="DoNotReply@example.com"),
        mail_bytes("digest", Message_ID="<a10@example.com>", List_Unsubscribe="<mailto:leave@example.com>"),
        mail_bytes("   \n", Message_ID="<a11@example.com>"),
    ]
    for raw in skipped:
        box.add(raw)
    assert ch.poll() == []
    assert ch.last_error == ""
    assert kv.kv_get("ch:mail:cursor")["last_uid"] == len(skipped)     # skipped, but not re-read
    assert len(box.fetches) == len(skipped)
    assert ch.poll() == [] and len(box.fetches) == len(skipped)

    box.add(mail_bytes("a person", Message_ID="<p1@example.com>", Auto_Submitted="no"))
    # A reply whose mail program copied our marker header is still a reply.
    box.add(mail_bytes("kept the header", Message_ID="<p2@example.com>", X_CCC_Oncall="out", In_Reply_To=ours))
    # The owner answering from the on-call mailbox itself.
    box.add(mail_bytes("from the same mailbox", Message_ID="<p3@example.com>",
                       From="Sam Example <oncall@gmail.com>", In_Reply_To=ours))
    assert [m.text for m in ch.poll()] == ["a person", "kept the header", "from the same mailbox"]


def test_email_html_only_body(box, kv):
    ch = make(EMAIL_CFG, kv)
    ch.poll()
    html = ("<html><head><style>p{color:red}</style></head><body><div>Yes &amp; thanks</div>"
            "<div class=\"gmail_quote\">On Tue, Nov 14, 2023 at 9:50 PM CCC On-call "
            "&lt;oncall@gmail.com&gt; wrote:<br><blockquote>The deploy failed.<blockquote>older"
            "</blockquote></blockquote></div></body></html>")
    box.add(mail_bytes(None, html=html, Message_ID="<h1@example.com>"))
    got = ch.poll()
    assert [m.text for m in got] == ["Yes & thanks"]


def test_email_uidvalidity_reset_starts_over(box, kv):
    ch = make(EMAIL_CFG, kv)
    ch.poll()
    box.add(mail_bytes("before the reset", Message_ID="<b1@example.com>"))
    assert len(ch.poll()) == 1
    # The server renumbered the folder: everything in it is history again.
    box.uidvalidity = 8
    box.messages = {}
    box.next_uid = 1
    for n in range(5):
        box.add(mail_bytes(f"renumbered {n}", Message_ID=f"<n{n}@example.com>"))
    fetched = len(box.fetches)
    assert ch.poll() == []
    assert len(box.fetches) == fetched
    assert kv.kv_get("ch:mail:cursor") == {"uidvalidity": 8, "last_uid": 5}
    box.add(mail_bytes("after the reset", Message_ID="<b2@example.com>"))
    assert [m.text for m in ch.poll()] == ["after the reset"]


def test_email_poll_cap_and_partial_failure(box, kv):
    ch = make(EMAIL_CFG, kv)
    ch.poll()
    for n in range(60):
        box.add(mail_bytes(f"message {n}", Message_ID=f"<c{n}@example.com>"))
    assert len(ch.poll()) == 50
    assert [m.text for m in ch.poll()] == [f"message {n}" for n in range(50, 60)]
    # A connection that drops mid-way keeps what it has and resumes after it.
    first = box.add(mail_bytes("kept", Message_ID="<k1@example.com>"))
    lost = box.add(mail_bytes("retried", Message_ID="<k2@example.com>"))
    box.fail_fetch_uid = lost
    assert [m.text for m in ch.poll()] == ["kept"]
    assert "IMAP" in ch.last_error
    assert kv.kv_get("ch:mail:cursor")["last_uid"] == first
    box.fail_fetch_uid = None
    assert [m.text for m in ch.poll()] == ["retried"]
    assert ch.last_error == ""


def test_email_cursor_survives_a_new_channel_object(box, kv):
    make(EMAIL_CFG, kv).poll()
    box.add(mail_bytes("after restart", Message_ID="<s1@example.com>"))
    assert [m.text for m in make(EMAIL_CFG, kv).poll()] == ["after restart"]
    assert make(EMAIL_CFG, kv).poll() == []


def test_email_check_says_which_side_failed(box):
    ch = make(EMAIL_CFG)
    assert ch.check() == (True, "Sending and receiving both work.")
    assert box.sent == []                               # a check never sends
    box.smtp_password = "something else"
    ok, detail = ch.check()
    assert not ok and "SMTP" in detail and "IMAP" not in detail
    assert ch.last_error == detail
    box.smtp_password, box.imap_password = SECRETS["mail-pw"], "something else"
    ok, detail = ch.check()
    assert not ok and "IMAP" in detail and "SMTP" not in detail
    assert SECRETS["mail-pw"] not in detail
    assert make(EMAIL_CFG, secret=lambda name: "").check()[0] is False


def test_email_failures_never_raise_or_leak(box, kv):
    ch = make(EMAIL_CFG, kv)
    box.smtp_password = box.imap_password = "something else"
    receipt = ch.send(Outbound(text="hello"))
    assert not receipt.ok and receipt.error and ch.last_error == receipt.error
    assert ch.poll() == []
    assert ch.last_error
    for text in (receipt.error, ch.last_error, ch.check()[1]):
        assert SECRETS["mail-pw"] not in text
    assert kv.kv_get("ch:mail:cursor") is None


# -- telegram ---------------------------------------------------------------

def telegram(web, kv=None, **extra):
    cfg = dict({"id": "tg", "type": "telegram", "token_vault": "tg-token", "owner": "4242",
                "api_base": web.base}, **extra)
    return make(cfg, kv)


def test_telegram_send_request_shape(web, kv):
    web.reply(200, {"ok": True, "result": {"message_id": 77}})
    ch = telegram(web, kv)
    receipt = ch.send(Outbound(text="Roll back? https://example.com/run/1", reply_to="55"))
    assert receipt == store.Receipt(ok=True, external_id="77")
    req = web.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == f"/bot{SECRETS['tg-token']}/sendMessage"
    assert req["headers"]["content-type"] == "application/json"
    assert web.json() == {
        "chat_id": 4242, "text": "Roll back? https://example.com/run/1",
        "link_preview_options": {"is_disabled": True},
        "reply_parameters": {"message_id": 55, "allow_sending_without_reply": True}}
    ch.send(Outbound(text="hi", to="@example_channel", reply_to="<not-a-number@example.com>"))
    assert web.json() == {"chat_id": "@example_channel", "text": "hi",
                          "link_preview_options": {"is_disabled": True}}
    ch.send(Outbound(text="b" * 5000, to="-100123"))
    sent = web.json()
    assert sent["chat_id"] == -100123
    assert len(sent["text"]) == 4096 and sent["text"].endswith(channels.ELLIPSIS)


def test_telegram_send_errors(web):
    web.reply(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    ch = telegram(web)
    receipt = ch.send(Outbound(text="hi"))
    assert receipt == store.Receipt(ok=False, error="Telegram: Unauthorized")
    assert ch.last_error == "Telegram: Unauthorized"
    assert ch.check() == (False, "Telegram: Unauthorized")
    no_chat = telegram(web, owner="")
    count = len(web.requests)
    assert not no_chat.send(Outbound(text="hi")).ok
    assert len(web.requests) == count                 # nothing went out
    no_token = make({"id": "tg", "type": "telegram", "token_vault": "missing", "api_base": web.base})
    assert not no_token.send(Outbound(text="hi", to="1")).ok
    assert len(web.requests) == count


def test_telegram_check(web):
    web.reply(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "example_bot"}})
    ch = telegram(web)
    assert ch.check() == (True, "Connected as @example_bot.")
    assert web.requests[0]["path"].endswith("/getMe")
    assert len(web.requests) == 1


def update(update_id, text="hello", chat_id=4242, chat_type="private", **message):
    body = dict({"message_id": update_id + 1000, "date": 1699999000,
                 "chat": {"id": chat_id, "type": chat_type, "first_name": "Sam", "last_name": "Example"},
                 "from": {"id": chat_id, "first_name": "Sam", "last_name": "Example"}}, **message)
    if text is not None:
        body["text"] = text
    return {"update_id": update_id, "message": body}


def test_telegram_poll_no_history_then_new_messages(web, kv):
    ch = telegram(web, kv)
    web.reply(200, {"ok": True, "result": [update(41, "old message")]})
    assert ch.poll() == []
    assert web.requests[0]["path"].endswith("/getUpdates")
    assert web.json(0) == {"offset": -1, "timeout": 0, "allowed_updates": ["message"]}
    assert kv.kv_get("ch:tg:update_id") == 41

    web.replies[:] = []
    web.reply(200, {"ok": True, "result": [
        update(42, "yes", reply_to_message={"message_id": 77}),
        update(43, None, photo=[{"file_id": "x"}]),
        {"update_id": 44, "edited_message": {"text": "edit"}},
        update(45, "from a group", chat_id=-100123, chat_type="group"),
    ]})
    got = ch.poll()
    assert web.json() == {"offset": 42, "timeout": 0, "allowed_updates": ["message"]}
    assert [(m.external_id, m.text, m.sender, m.reply_to) for m in got] == [
        ("42", "yes", "4242", "77"), ("45", "from a group", "-100123", "")]
    assert got[0].channel == "tg" and got[0].sender_name == "Sam Example"
    assert got[0].received_at == 1699999000.0
    assert kv.kv_get("ch:tg:update_id") == 45         # non-text updates still move the cursor

    web.replies[:] = []
    web.reply(200, {"ok": True, "result": []})
    assert ch.poll() == []
    assert web.json()["offset"] == 46
    assert ch.last_error == ""


def test_telegram_first_poll_on_an_empty_queue(web, kv):
    web.reply(200, {"ok": True, "result": []})
    ch = telegram(web, kv)
    assert ch.poll() == []
    assert kv.kv_get("ch:tg:update_id") == 0
    ch.poll()
    assert web.json()["offset"] == 1


def test_telegram_poll_failure_keeps_cursor(web, kv):
    ch = telegram(web, kv)
    web.reply(409, {"ok": False, "error_code": 409, "description": "Conflict: webhook is active"})
    assert ch.poll() == []
    assert ch.last_error == "Telegram: Conflict: webhook is active"
    assert kv.kv_get("ch:tg:update_id") is None


def test_telegram_discover_chat_does_not_move_the_cursor(web, kv):
    ch = telegram(web, kv, owner="")
    web.reply(200, {"ok": True, "result": [
        update(10, "hi", chat_id=111),
        update(11, "/start", chat_id=222),
        update(12, "group noise", chat_id=-100123, chat_type="supergroup"),
    ]})
    assert ch.discover_chat() == ("222", "Sam Example")
    assert web.json() == {"timeout": 0, "allowed_updates": ["message"]}     # no offset: confirms nothing
    assert kv.kv_get("ch:tg:update_id") is None
    web.replies[:] = []
    web.reply(200, {"ok": True, "result": []})
    assert ch.discover_chat() == ("", "")
    web.replies[:] = []
    web.reply(401, {"ok": False, "description": "Unauthorized"})
    assert ch.discover_chat() == ("", "")
    assert ch.last_error == "Telegram: Unauthorized"


def test_telegram_token_never_leaks():
    token = SECRETS["tg-token"]

    def broken(method, url, **kw):
        raise OSError(f"cannot reach {url}")

    ch = make({"id": "tg", "type": "telegram", "token_vault": "tg-token", "owner": "1"}, http=broken)
    receipt = ch.send(Outbound(text="hi"))
    assert not receipt.ok and "***" in receipt.error
    assert ch.poll() == []
    assert ch.discover_chat() == ("", "")
    ok, detail = ch.check()
    assert not ok
    for text in (receipt.error, ch.last_error, detail):
        assert text and token not in text and urllib.parse.quote(token, safe="") not in text


# -- ntfy -------------------------------------------------------------------

def ntfy(web, kv=None, **extra):
    cfg = dict({"id": "push", "type": "ntfy", "topic": "alerts", "server": web.base + "/",
                "token_vault": "ntfy-token"}, **extra)
    return make(cfg, kv)


def test_ntfy_send_with_choices(web):
    web.reply(200, {"id": "msg1", "time": 1700000000, "event": "message", "topic": "alerts"})
    ch = ntfy(web)
    receipt = ch.send(Outbound(text="Roll back?", subject="Deploy failed", thread=TOKEN,
                               to="ignored@example.com", choices=("Yes", "No", "Later", "Never")))
    assert receipt == store.Receipt(ok=True, external_id="msg1")
    req = web.requests[0]
    assert (req["method"], req["path"]) == ("POST", "/")
    assert req["headers"]["authorization"] == f"Bearer {SECRETS['ntfy-token']}"
    assert req["headers"]["content-type"] == "application/json"
    reply_url = f"{web.base}/alerts-reply"
    assert web.json() == {
        "topic": "alerts", "title": "Deploy failed", "message": "Roll back?",
        "actions": [
            {"action": "http", "label": label, "url": reply_url, "method": "POST",
             "body": f"{TOKEN} {label}", "clear": True}
            for label in ("Yes", "No", "Later")]}
    assert SECRETS["ntfy-token"] not in req["body"].decode()


def test_ntfy_send_plain_and_errors(web):
    ch = make({"id": "push", "type": "ntfy", "topic": "alerts", "server": web.base,
               "reply_topic": "answers"})
    web.reply(200, {"id": "msg2"})
    assert ch.send(Outbound(text="FYI", choices=("OK",))).ok
    assert "authorization" not in web.requests[0]["headers"]
    assert web.json() == {"topic": "alerts", "message": "FYI", "actions": [
        {"action": "http", "label": "OK", "url": f"{web.base}/answers", "method": "POST",
         "body": "OK", "clear": True}]}
    ch.send(Outbound(text="é" * 5000))
    assert len(web.json()["message"].encode("utf-8")) <= 4000
    assert "actions" not in web.json() and "title" not in web.json()
    web.replies[:] = []
    web.reply(403, {"code": 40301, "http": 403, "error": "forbidden"})
    receipt = ch.send(Outbound(text="FYI"))
    assert receipt == store.Receipt(ok=False, error="ntfy: forbidden")
    assert ch.check() == (False, "ntfy: forbidden")


def ndjson(*events):
    return "\n".join(json.dumps(e) for e in events).encode() + b"\n"


def test_ntfy_poll(web, kv):
    ch = ntfy(web, kv)
    web.reply(200, b"")
    assert ch.poll() == []
    first = web.requests[0]
    assert (first["method"], first["path"]) == ("GET", "/alerts-reply/json?poll=1&since=1700000000")
    assert first["headers"]["authorization"] == f"Bearer {SECRETS['ntfy-token']}"
    assert kv.kv_get("ch:push:cursor") == {"id": "", "time": 1700000000}

    web.replies[:] = []
    web.reply(200, ndjson(
        {"id": "open1", "time": 1700000050, "event": "open", "topic": "alerts-reply"},
        {"id": "aaa111", "time": 1700000060, "event": "message", "topic": "alerts-reply",
         "message": f"{TOKEN} Yes"},
        {"id": "bbb222", "time": 1700000070, "event": "message", "topic": "alerts-reply",
         "message": "just text", "title": "From my phone"},
        {"id": "ccc333", "time": 1700000080, "event": "message", "topic": "alerts-reply",
         "message": f"[#{TOKEN}]"},
        {"id": "kkk", "time": 1700000090, "event": "keepalive", "topic": "alerts-reply"}))
    got = ch.poll()
    assert web.requests[-1]["path"] == "/alerts-reply/json?poll=1&since=1700000000"
    assert [(m.external_id, m.text, m.thread, m.sender, m.received_at) for m in got] == [
        ("aaa111", "Yes", TOKEN, "ntfy", 1700000060.0), ("bbb222", "just text", "", "ntfy", 1700000070.0)]
    assert got[1].subject == "From my phone" and got[0].channel == "push"
    assert kv.kv_get("ch:push:cursor") == {"id": "ccc333", "time": 1700000080}

    # A server that forgot our cursor id replays its whole cache: nothing is returned twice.
    got = ch.poll()
    assert web.requests[-1]["path"] == "/alerts-reply/json?poll=1&since=ccc333"
    assert got == []
    web.replies[:] = []
    web.reply(200, ndjson(
        {"id": "old000", "time": 1699990000, "event": "message", "message": "ancient"},
        {"id": "ddd444", "time": 1700000100, "event": "message", "message": "new one"}))
    assert [m.text for m in ch.poll()] == ["new one"]
    assert ch.last_error == ""


def test_ntfy_first_poll_ignores_what_is_already_there(web, kv):
    ch = ntfy(web, kv)
    web.reply(200, ndjson({"id": "zzz999", "time": 1700000000, "event": "message", "message": "early"}))
    assert ch.poll() == []
    assert kv.kv_get("ch:push:cursor") == {"id": "zzz999", "time": 1700000000}
    assert ch.poll() == []
    assert web.requests[-1]["path"].endswith("since=zzz999")


def test_ntfy_poll_and_check_failures(web, kv):
    ch = ntfy(web, kv)
    web.reply(401, {"code": 40101, "http": 401, "error": "unauthorized"})
    assert ch.poll() == []
    assert ch.last_error == "ntfy: unauthorized"
    assert kv.kv_get("ch:push:cursor") is None
    web.replies[:] = []
    web.reply(200, b"")
    count = len(web.requests)
    assert ch.check() == (True, "Connected.")
    assert [r["method"] for r in web.requests[count:]] == ["GET"]      # a check never publishes


# -- twilio -----------------------------------------------------------------

def twilio(web, kv=None, ctype="sms", **extra):
    cfg = dict({"id": "tw", "type": ctype, "account_sid": "ACtest", "auth_token_vault": "twilio-token",
                "from": "+1 (555) 010-0", "owner": "+15550101", "api_base": web.base}, **extra)
    return make(cfg, kv)


BASIC = "Basic " + base64.b64encode(f"ACtest:{SECRETS['twilio-token']}".encode()).decode()


def test_sms_send_request_shape(web):
    web.reply(201, {"sid": "SM1", "status": "queued", "error_code": None})
    ch = twilio(web)
    receipt = ch.send(Outbound(text="Roll back?"))
    assert receipt == store.Receipt(ok=True, external_id="SM1")
    req = web.requests[0]
    assert (req["method"], req["path"]) == ("POST", "/2010-04-01/Accounts/ACtest/Messages.json")
    assert req["headers"]["authorization"] == BASIC
    assert req["headers"]["content-type"] == "application/x-www-form-urlencoded"
    assert web.form() == {"To": ["+15550101"], "From": ["+15550100"], "Body": ["Roll back?"]}
    ch.send(Outbound(text="x" * 2000, to="+1 555 0102"))
    form = web.form()
    assert form["To"] == ["+15550102"]
    assert len(form["Body"][0]) == 1600 and form["Body"][0].endswith(channels.ELLIPSIS)


def test_sms_send_error_and_check(web):
    web.reply(400, {"code": 21211, "message": "Invalid 'To' Phone Number", "status": 400})
    ch = twilio(web)
    assert ch.send(Outbound(text="hi")) == store.Receipt(
        ok=False, error="Twilio: Invalid 'To' Phone Number")
    web.replies[:] = []
    web.reply(401, {"code": 20003, "message": "Authenticate", "status": 401})
    assert ch.check() == (False, "Twilio rejected the account SID or auth token.")
    assert (web.requests[-1]["method"], web.requests[-1]["path"]) == ("GET", "/2010-04-01/Accounts/ACtest.json")
    assert web.requests[-1]["headers"]["authorization"] == BASIC
    web.replies[:] = []
    web.reply(200, {"friendly_name": "Example account"})
    assert ch.check() == (True, "Connected to Example account.")


def test_whatsapp_prefix_and_window_error(web, kv):
    ch = twilio(web, kv, ctype="whatsapp")
    assert ch.type == "whatsapp"
    web.reply(201, {"sid": "SM2", "status": "queued"})
    assert ch.send(Outbound(text="hi")).ok
    assert web.form() == {"To": ["whatsapp:+15550101"], "From": ["whatsapp:+15550100"], "Body": ["hi"]}
    web.replies[:] = []
    web.reply(400, {"code": 63016, "message": "Outside the allowed window", "status": 400})
    receipt = ch.send(Outbound(text="hi", to="whatsapp:+15550102"))
    assert receipt == store.Receipt(ok=False, error=channels.WHATSAPP_WINDOW_ERROR)
    assert web.form()["To"] == ["whatsapp:+15550102"]
    assert "—" not in channels.WHATSAPP_WINDOW_ERROR


def test_whatsapp_template_retry(web, kv):
    ch = twilio(web, kv, ctype="whatsapp", template_sid="HXtemplate")
    web.reply(400, {"code": 63016, "message": "Outside the allowed window", "status": 400})
    web.reply(201, {"sid": "SM3", "status": "queued"})
    receipt = ch.send(Outbound(text="Deploy failed.\nRoll back?"))
    assert receipt == store.Receipt(ok=True, external_id="SM3")
    assert len(web.requests) == 2
    assert "Body" in web.form(0)
    retry = web.form(1)
    assert set(retry) == {"To", "From", "ContentSid", "ContentVariables"}
    assert retry["ContentSid"] == ["HXtemplate"]
    assert json.loads(retry["ContentVariables"][0]) == {"1": "Deploy failed. Roll back?"}
    assert retry["To"] == ["whatsapp:+15550101"] and retry["From"] == ["whatsapp:+15550100"]
    # SMS never takes the template path.
    sms = twilio(web, ctype="sms", template_sid="HXtemplate")
    web.replies[:] = []
    web.reply(400, {"code": 63016, "message": "Outside the allowed window", "status": 400})
    assert sms.send(Outbound(text="hi")).error == "Twilio: Outside the allowed window"


def test_whatsapp_window_failure_reported_after_acceptance(web, kv):
    """Twilio accepts the message, then fails it: the next poll picks that up."""
    ch = twilio(web, kv, ctype="whatsapp", template_sid="HXtemplate")
    web.reply(201, {"sid": "SM4", "status": "queued"})
    assert ch.send(Outbound(text="Roll back?")).ok
    web.replies[:] = []
    web.reply(200, {"sid": "SM4", "status": "failed", "error_code": 63016})
    web.reply(201, {"sid": "SM5", "status": "queued"})
    web.reply(200, {"messages": []})
    assert ch.poll() == []
    methods = [(r["method"], r["path"].split("?")[0]) for r in web.requests[1:]]
    assert methods == [
        ("GET", "/2010-04-01/Accounts/ACtest/Messages/SM4.json"),
        ("POST", "/2010-04-01/Accounts/ACtest/Messages.json"),
        ("GET", "/2010-04-01/Accounts/ACtest/Messages.json")]
    assert json.loads(web.form(2)["ContentVariables"][0]) == {"1": "Roll back?"}
    assert ch.last_error == ""
    count = len(web.requests)
    ch.poll()
    assert len(web.requests) == count + 1             # retried once, never again

    plain = twilio(web, None, ctype="whatsapp")
    web.replies[:] = []
    web.reply(201, {"sid": "SM6", "status": "queued"})
    assert plain.send(Outbound(text="Roll back?")).ok
    web.replies[:] = []
    web.reply(200, {"sid": "SM6", "status": "undelivered", "error_code": 63016})
    web.reply(200, {"messages": []})
    assert plain.poll() == []
    assert plain.last_error == channels.WHATSAPP_WINDOW_ERROR
    plain.poll()
    assert plain.last_error == ""


def twilio_message(sid, body="hello", direction="inbound", sender="+15550101",
                   sent="Tue, 14 Nov 2023 22:20:00 +0000"):
    return {"sid": sid, "body": body, "direction": direction, "from": sender, "to": "+15550100",
            "date_sent": sent, "status": "received"}


def test_sms_poll_baseline_then_new_messages(web, kv):
    ch = twilio(web, kv)
    web.reply(200, {"messages": [twilio_message("SMold2", "earlier today"), twilio_message("SMold1")]})
    assert ch.poll() == []
    req = web.requests[0]
    assert req["method"] == "GET" and req["headers"]["authorization"] == BASIC
    assert req["path"] == ("/2010-04-01/Accounts/ACtest/Messages.json"
                           "?To=%2B15550100&DateSent%3E=2023-11-14&PageSize=50")

    web.replies[:] = []
    web.reply(200, {"messages": [
        twilio_message("SMnew2", "second", sent="Wed, 15 Nov 2023 00:05:00 +0000"),
        twilio_message("SMout", "our alert", direction="outbound-api"),
        twilio_message("SMnew1", "first", sender="+1 555 0103"),
        twilio_message("SMmedia", ""),
        twilio_message("SMold2", "earlier today"),
        twilio_message("SMold1")]})
    got = ch.poll()
    assert [(m.external_id, m.text, m.sender) for m in got] == [
        ("SMnew1", "first", "+15550103"), ("SMnew2", "second", "+15550101")]
    assert got[0].channel == "tw" and got[1].received_at == 1700006700.0
    assert ch.poll() == []
    # The cursor day follows the newest message that was seen.
    assert "DateSent%3E=2023-11-15" in web.requests[-1]["path"]
    assert ch.last_error == ""


def test_whatsapp_poll_addresses(web, kv):
    ch = twilio(web, kv, ctype="whatsapp")
    web.reply(200, {"messages": []})
    assert ch.poll() == []
    assert "To=whatsapp%3A%2B15550100" in web.requests[0]["path"]
    web.replies[:] = []
    web.reply(200, {"messages": [twilio_message("SMw1", "yes", sender="whatsapp:+15550101")]})
    got = ch.poll()
    assert [(m.sender, m.text) for m in got] == [("+15550101", "yes")]


def test_twilio_failures_never_raise_or_leak(web, kv):
    token = SECRETS["twilio-token"]

    def broken(method, url, headers=None, **kw):
        raise OSError(f"proxy refused header {headers['Authorization']} for {token}")

    ch = make({"id": "tw", "type": "sms", "account_sid": "ACtest", "auth_token_vault": "twilio-token",
               "from": "+15550100", "owner": "+15550101"}, kv, http=broken)
    receipt = ch.send(Outbound(text="hi"))
    assert not receipt.ok
    assert ch.poll() == []
    ok, detail = ch.check()
    assert not ok
    for text in (receipt.error, ch.last_error, detail):
        assert text and token not in text
    web.reply(500, b"<html>oops</html>")
    real = twilio(web, kv)
    assert real.poll() == [] and real.last_error == "Twilio: HTTP 500"
    assert not make({"id": "tw", "type": "sms", "account_sid": "ACtest", "auth_token_vault": "missing",
                     "from": "+15550100", "owner": "+15550101"}).send(Outbound(text="hi")).ok


# -- imessage ---------------------------------------------------------------

def attributed(text, mutable=False):
    """A hand-built typedstream blob shaped like the ones Messages stores."""
    data = text.encode("utf-8")
    length = bytes([len(data)]) if len(data) < 0x80 else b"\x81" + len(data).to_bytes(2, "little")
    klass = (b"\x84\x84\x84\x0fNSMutableString\x01\x84\x84\x08NSString\x01\x95\x84\x01+" if mutable
             else b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00\x85\x92" + klass + length + data
            + b"\x86\x84\x02iI\x01" + bytes([min(len(data), 0x7f)])
            + b"\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x86\x86")


def test_decode_attributed_body():
    assert channels.decode_attributed_body(attributed("Yes, ship it")) == "Yes, ship it"
    assert channels.decode_attributed_body(attributed("mutable", mutable=True)) == "mutable"
    long_text = "long é\U0001F600 " * 60
    assert len(long_text.encode()) > 255
    assert channels.decode_attributed_body(attributed(long_text)) == long_text
    for junk in (None, b"", b"no marker here", b"NSString", b"NSString\x01\x94\x84\x01+", 12, "text"):
        assert channels.decode_attributed_body(junk) == ""
    # A length that runs past the end returns what is there instead of failing.
    assert channels.decode_attributed_body(b"NSString\x01\x94\x84\x01+\x40abc") == "abc"


def apple_ns(unix):
    return int((unix - 978307200) * 1e9)


class ChatDb:
    def __init__(self, path):
        self.path = path
        con = sqlite3.connect(str(path))
        con.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, style INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT, text TEXT,
                attributedBody BLOB, handle_id INTEGER, is_from_me INTEGER, date INTEGER,
                associated_message_type INTEGER DEFAULT 0);
            INSERT INTO handle(id) VALUES ('+15550100'), ('+15550199'), ('Sam@Example.com');
            INSERT INTO chat(style) VALUES (45), (43);
        """)
        con.commit()
        con.close()
        self.count = 0

    def add(self, text, handle=1, from_me=0, body=None, tapback=0, chat=1, when=NOW):
        self.count += 1
        con = sqlite3.connect(str(self.path))
        cur = con.execute(
            "INSERT INTO message(guid,text,attributedBody,handle_id,is_from_me,date,associated_message_type)"
            " VALUES(?,?,?,?,?,?,?)",
            (f"GUID-{self.count}", text, body, handle, from_me, apple_ns(when + self.count), tapback))
        con.execute("INSERT INTO chat_message_join VALUES(?,?)", (chat, cur.lastrowid))
        con.commit()
        con.close()
        return cur.lastrowid


class Runner:
    def __init__(self, codes=(0,)):
        self.calls = []
        self.codes = list(codes)

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        return types.SimpleNamespace(returncode=code, stdout="", stderr="" if code == 0 else "execution error: nope (-1728)")


@pytest.fixture
def chatdb(tmp_path):
    return ChatDb(tmp_path / "chat.db")


def imessage(chatdb, kv=None, run=None, **extra):
    cfg = dict({"id": "im", "type": "imessage", "owner": "+1 (555) 010-0", "db_path": str(chatdb.path)},
               **extra)
    return make(cfg, kv, run=run or Runner())


def test_imessage_send_passes_text_as_an_argument(chatdb, kv):
    run = Runner()
    ch = imessage(chatdb, kv, run)
    hostile = 'done" & (do shell script "id") & "'
    receipt = ch.send(Outbound(text=hostile))
    assert receipt == store.Receipt(ok=True, external_id="")
    argv, kw = run.calls[0]
    assert argv == ["osascript", "-", hostile, "+1 (555) 010-0"]
    assert "on run argv" in kw["input"] and "iMessage" in kw["input"]
    assert hostile not in kw["input"] and "do shell script" not in kw["input"]
    assert kw.get("shell") is not True
    ch.send(Outbound(text="hi", to="kim@example.com"))
    assert run.calls[1][0][3] == "kim@example.com"
    assert len(kv.kv_get("ch:im:sent")) == 2
    for n in range(60):
        ch.send(Outbound(text=f"alert {n}"))
    assert len(kv.kv_get("ch:im:sent")) == 50


def test_imessage_send_falls_back_to_the_older_script_and_reports_failure(chatdb):
    run = Runner(codes=(1, 0))
    assert imessage(chatdb, None, run).send(Outbound(text="hi")).ok
    assert len(run.calls) == 2 and run.calls[0][1]["input"] != run.calls[1][1]["input"]
    failing = Runner(codes=(1,))
    ch = imessage(chatdb, None, failing)
    receipt = ch.send(Outbound(text="hi"))
    assert not receipt.ok and "nope" in receipt.error and ch.last_error == receipt.error

    def missing(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory", "osascript")

    assert not imessage(chatdb, None, missing).send(Outbound(text="hi")).ok


def test_imessage_poll_rules(chatdb, kv):
    chatdb.add("old history")
    ch = imessage(chatdb, kv)
    assert ch.poll() == []
    assert kv.kv_get("ch:im:rowid") == 1
    assert ch.owner == "+15550100"

    chatdb.add("Yes do it")                                         # the owner, plain text
    chatdb.add("someone else", handle=2)                            # another person
    chatdb.add(None, body=attributed("from the blob"))              # text only in attributedBody
    chatdb.add("Loved an alert", tapback=2000)                      # a tapback
    chatdb.add("family chatter", chat=2)                            # the owner, but in a group
    chatdb.add(None)                                                # nothing readable
    got = ch.poll()
    assert [m.text for m in got] == ["Yes do it", "from the blob"]
    assert got[0].channel == "im" and got[0].sender == "+15550100"
    assert got[0].external_id == "GUID-2"
    assert got[0].received_at == pytest.approx(NOW + 2, abs=0.01)
    assert kv.kv_get("ch:im:rowid") == 7
    assert ch.poll() == []
    assert ch.last_error == ""


def test_imessage_chat_with_yourself(chatdb, kv):
    """One Apple ID on the Mac and the phone: everything looks like it is from me."""
    ch = imessage(chatdb, kv)
    ch.poll()
    alert = "Deploy failed. Roll back?"
    assert ch.send(Outbound(text=alert)).ok
    chatdb.add(None, from_me=1, body=attributed(alert))             # our alert, as sent
    chatdb.add(alert, from_me=0)                                    # and its received copy
    assert ch.poll() == []

    chatdb.add("Yes, roll back", from_me=1)                         # typed on the phone
    got = ch.poll()
    assert [m.text for m in got] == ["Yes, roll back"]
    assert got[0].raw["is_from_me"] is True
    chatdb.add("Yes, roll back", from_me=0)                         # the same note, received copy
    assert ch.poll() == []

    chatdb.add("ok", from_me=0)                                     # copies in the other order
    chatdb.add("ok", from_me=1)
    assert [m.text for m in ch.poll()] == ["ok"]
    chatdb.add("ok", from_me=1)                                     # a genuine repeat later on
    assert [m.text for m in ch.poll()] == ["ok"]


def test_imessage_same_answer_twice_from_another_person(chatdb, kv):
    ch = imessage(chatdb, kv, owner="sam@example.com")
    ch.poll()
    chatdb.add("yes", handle=3)
    chatdb.add("yes", handle=3)
    assert [m.text for m in ch.poll()] == ["yes", "yes"]


def test_imessage_minimal_schema_and_unreadable_db(tmp_path, kv):
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, handle_id INTEGER,
            is_from_me INTEGER, date INTEGER);
        INSERT INTO handle VALUES (1, '+15550100');
    """)
    con.commit()
    ch = make({"id": "im", "type": "imessage", "owner": "+15550100", "db_path": str(path)}, kv)
    assert ch.poll() == []
    con.execute("INSERT INTO message(text,handle_id,is_from_me,date) VALUES('hi',1,0,?)",
                (int(NOW - 978307200),))                            # older macOS stored seconds
    con.commit()
    con.close()
    got = ch.poll()
    assert [(m.text, m.external_id, m.received_at) for m in got] == [("hi", "rowid:1", NOW)]

    gone = tmp_path / "missing" / "chat.db"
    broken = make({"id": "im2", "type": "imessage", "owner": "+15550100", "db_path": str(gone)}, kv)
    assert broken.poll() == []
    assert "Full Disk Access" in broken.last_error
    assert not gone.exists() and not gone.parent.exists()           # never creates the file


def test_imessage_check(chatdb, monkeypatch):
    ch = imessage(chatdb)
    monkeypatch.setattr(channels, "_system", lambda: "Linux")
    ok, detail = ch.check()
    assert not ok and "Mac" in detail and ch.last_error == detail
    monkeypatch.setattr(channels, "_system", lambda: "Darwin")
    monkeypatch.setattr(channels.shutil, "which", lambda name: None)
    ok, detail = ch.check()
    assert not ok and "osascript" in detail
    monkeypatch.setattr(channels.shutil, "which", lambda name: "/usr/bin/" + name)
    assert ch.check() == (True, "Messages is readable.")
    assert ch.last_error == ""
    locked = make({"id": "im", "type": "imessage", "owner": "+15550100",
                   "db_path": str(chatdb.path) + ".nope"})
    ok, detail = locked.check()
    assert not ok and "Full Disk Access" in detail


# -- command ----------------------------------------------------------------

SCRIPT = """\
import json, os, sys
text = sys.stdin.read()
if text.startswith("fail"):
    sys.stderr.write("first line\\nboom\\n")
    sys.exit(3)
with open(sys.argv[1], "w") as handle:
    json.dump({"text": text, "extra": sys.argv[2:],
               "to": os.environ.get("CCC_ONCALL_TO"),
               "subject": os.environ.get("CCC_ONCALL_SUBJECT"),
               "thread": os.environ.get("CCC_ONCALL_THREAD")}, handle)
"""


def test_command_channel_runs_a_real_script(tmp_path):
    script = tmp_path / "notify me.py"
    script.write_text(SCRIPT)
    out = tmp_path / "out.json"
    line = " ".join(shlex.quote(part) for part in (sys.executable, str(script), str(out), "two words", "$HOME"))
    ch = make({"id": "cmd", "type": "command", "send": line, "owner": "Sam <sam@example.com>"})
    assert ch.can_receive is False
    receipt = ch.send(Outbound(text="Roll back?\nYes or no", subject="Deploy failed", thread=TOKEN))
    assert receipt == store.Receipt(ok=True, external_id="")
    assert json.loads(out.read_text()) == {
        "text": "Roll back?\nYes or no", "extra": ["two words", "$HOME"],      # no shell expansion
        "to": "sam@example.com", "subject": "Deploy failed", "thread": TOKEN}
    ch.send(Outbound(text="hi", to="kim@example.com"))
    assert json.loads(out.read_text())["to"] == "kim@example.com"

    failed = ch.send(Outbound(text="fail please"))
    assert failed == store.Receipt(ok=False, error="Command exited 3. boom")
    assert ch.last_error == "Command exited 3. boom"
    assert ch.poll() == [] and ch.last_error == ""
    assert ch.check() == (True, "Command found.")


def test_command_channel_missing_program_and_timeout(tmp_path):
    ch = make({"id": "cmd", "type": "command", "send": str(tmp_path / "no-such-program") + " --flag"})
    ok, detail = ch.check()
    assert not ok and "no-such-program" in detail
    receipt = ch.send(Outbound(text="hi"))
    assert not receipt.ok and receipt.error

    seen = {}

    def slow(argv, **kw):
        seen.update(kw, argv=argv)
        raise channels.subprocess.TimeoutExpired(argv, kw["timeout"])

    timed = make({"id": "cmd", "type": "command", "send": "notify --to 'a b'"}, run=slow)
    assert timed.send(Outbound(text="the secret plan")) == store.Receipt(ok=False, error="Timed out.")
    assert seen["argv"] == ["notify", "--to", "a b"] and seen["timeout"] == 60
    assert seen["input"] == "the secret plan" and seen.get("shell") is not True


# -- module hygiene ---------------------------------------------------------

def test_module_header_and_plain_strings():
    source = open(channels.__file__, encoding="utf-8").read()
    lines = source.splitlines()
    assert lines[0] == "# Copyright (c) 2026 Amir Fish. All rights reserved."
    assert lines[1] == "# SPDX-License-Identifier: LicenseRef-CCC-Software-License"
    assert "—" not in source and "–" not in source
    assert "from __future__ import annotations" in source


def test_idle_question_round_trip_over_local_ntfy(web, tmp_path):
    """Real HTTP and SQLite, with a synthetic idle transcript and inject sink."""
    from ccc_server.oncall import engine, session_signals
    from tests.test_oncall_engine import FakeWT, Deps
    transcript = tmp_path / 'idle.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'uuid': 'turn-local',
        'message': {'content': [{'type': 'text', 'text': 'Should I keep the old client?'}]}}) + '\n')
    cfg = store.default_config()
    cfg.update(enabled=True, channels=[{'id': 'push', 'type': 'ntfy',
                                       'server': web.base, 'topic': 'alerts'}])
    store.save_config(tmp_path / 'state', cfg)
    deps = Deps()
    deps.question = lambda sid, db: session_signals.question(db, sid, transcript)
    eng = engine.Engine(tmp_path / 'state', wt=FakeWT(), deps=deps, flag_on=lambda: True)
    web.reply(200, b'')
    eng.tick(force=True)
    session_signals.observe(eng, {'local-session': {'state': 'idle'}})
    web.replies[:] = [(200, b''), (200, b'{"id":"sent-1"}')]
    eng.tick(force=True)
    sent = web.json()
    assert 'Should I keep the old client?' in sent['message']
    token = eng.store.alert(1)['token']
    web.replies[:] = [(200, ndjson({'id': 'reply-1', 'time': 2_000_000_000,
        'event': 'message', 'message': token + ' keep it'})), (200, b'{"id":"ack-1"}')]
    eng.tick(force=True)
    assert deps.injected == [('local-session', 'keep it')]
    assert web.json()['message'] == 'Sent to the agent.'
    assert eng.store.alert(1)['state'] == 'answered'
    eng.store.close()
