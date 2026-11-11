# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""``ccc oncall``: set On-call up and drive it from a terminal.

Every command talks to the running CCC over its local HTTP API, so a person
and an agent use the same path. ``setup`` asks a few questions; every answer
can also be given as a flag, so an agent can run it with no prompts.

A secret is typed at a hidden prompt, read from stdin, or named as an existing
Vault entry. It is never an argument, so it never lands in shell history or a
transcript.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets as _secrets
import sys

CHANNEL_NAMES = {
    "email": "Email", "telegram": "Telegram", "ntfy": "ntfy (phone push, no account)",
    "imessage": "iMessage (this Mac)", "sms": "SMS (your Twilio number)",
    "whatsapp": "WhatsApp (your Twilio sender)", "command": "Your own command",
}
SOURCE_NAMES = {
    "ci": "Failed CI runs",
    "github": "GitHub issues", "sentry": "Sentry", "posthog": "PostHog",
    "http": "A URL that must stay up",
    "command": "A command that must keep passing",
}
PREVIEW_OFF = "On-call is a preview and it is off. Turn it on with: ccc oncall setup"
NOT_RUNNING = "CCC is not running. Start it with `ccc`, then run this again."


class Stop(Exception):
    """End the command with this message and exit code 1."""


class Client:
    """The local API, with the two failures a person can act on spelled out."""

    def __init__(self, base, get, post):
        self.base, self._get, self._post = base, get, post

    def _need_server(self):
        if not self.base:
            raise Stop(NOT_RUNNING)

    def get(self, name):
        self._need_server()
        try:
            return 200, self._get(self.base, f"/api/oncall/{name}", timeout=60)
        except OSError as exc:
            code = getattr(exc, "code", None)
            if code == 404:
                return 404, {"ok": False, "error": PREVIEW_OFF}
            if code:
                return code, {"ok": False, "error": f"CCC answered HTTP {code}."}
            raise Stop(NOT_RUNNING)

    def post(self, name, body=None, path=None):
        self._need_server()
        try:
            return self._post(self.base, path or f"/api/oncall/{name}", body or {}, timeout=120)
        except OSError:
            raise Stop(NOT_RUNNING)

    def must(self, name, body=None):
        code, res = self.post(name, body)
        if code == 404 and "preview" in str(res.get("error", "")).lower():
            raise Stop(PREVIEW_OFF)
        if code != 200 or res.get("ok") is False and "results" not in res:
            raise Stop(str(res.get("error") or f"CCC answered HTTP {code}."))
        return res

    def status(self):
        code, res = self.get("status")
        if code == 404:
            raise Stop(PREVIEW_OFF)
        if code != 200:
            raise Stop(str(res.get("error") or f"CCC answered HTTP {code}."))
        return res


class IO:
    def __init__(self, ask=None, secret=None, out=None, stdin=None, interactive=None):
        self.ask = ask or input
        self.secret = secret or getpass.getpass
        self.out = out or print
        self.stdin = stdin or sys.stdin
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def prompt(self, label, default=""):
        if not self.interactive:
            return default
        try:
            answer = self.ask(f"{label}{f' [{default}]' if default else ''}: ").strip()
        except EOFError:
            answer = ""
        return answer or default

    def yes(self, label, default=True):
        if not self.interactive:
            return default
        answer = self.prompt(f"{label} ({'Y/n' if default else 'y/N'})").lower()
        return default if not answer else answer.startswith("y")


# -- parsing ----------------------------------------------------------------

def _decision_state(row):
    """WatchTower's follow-through word for a decision, in plain words."""
    state, reason = str(row.get("state") or ""), str(row.get("reason") or "")
    if state.endswith(" reversed"):
        return "Undone."
    if state == "pending":
        return "Waiting for an agent to pick it up."
    if state == "consumed":
        return "An agent is applying it."
    if state == "applied":
        return "Applied."
    if state == "verified":
        return f"Done: {reason}" if reason else "Done."
    if state == "failed":
        return f"Did not finish: {reason}" if reason else "Did not finish."
    return f"{state}  {reason}".strip()


def _pairs(items, what):
    out = {}
    for item in items or []:
        key, sep, value = str(item).partition("=")
        if not sep or not key.strip():
            raise Stop(f"{what} expects name=value, got {item!r}.")
        out[key.strip()] = value.strip()
    return out


def _entry_flags(p):
    p.add_argument("--set", action="append", metavar="FIELD=VALUE", default=[],
                   help="a setting, for example --set owner=you@example.com (repeatable)")
    p.add_argument("--vault", action="append", metavar="FIELD=ENTRY", default=[],
                   help="use a secret already saved in the CCC Vault, for example --vault token=my-bot-token")
    p.add_argument("--secret-stdin", metavar="FIELD",
                   help="read this one secret from stdin, for example: "
                        "ccc secret request NAME --reason ... --return-key | ccc oncall channel add telegram --secret-stdin token")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ccc oncall",
        description="Agents fix reported bugs and message you only for real decisions. Preview.")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("setup", help="ask a few questions, send a test message, turn it on")
    p.add_argument("--queue", help="name of the queue bug tickets go to, for example SHOP")
    p.add_argument("--repo", help="folder of the code agents should fix")
    p.add_argument("--channel", choices=sorted(CHANNEL_NAMES), help="how CCC reaches you")
    p.add_argument("--yes", action="store_true", help="answer yes to every confirmation")
    _entry_flags(p)

    p = sub.add_parser("status", help="what is on and what is waiting for you")
    p.add_argument("--json", action="store_true", help="raw JSON")

    p = sub.add_parser("test", help="send a test message to every channel, or to one")
    p.add_argument("channel", nargs="?", default="")

    sub.add_parser("on", help="turn On-call on")
    sub.add_parser("off", help="turn On-call off")

    p = sub.add_parser("channel", help="add, list or remove a message channel")
    csub = p.add_subparsers(dest="what", required=True)
    c = csub.add_parser("add")
    c.add_argument("type", choices=sorted(CHANNEL_NAMES))
    c.add_argument("--intake", action="store_true",
                   help="also read reports from customers on this channel (email, sms, whatsapp)")
    _entry_flags(c)
    csub.add_parser("list")
    c = csub.add_parser("rm")
    c.add_argument("id")

    p = sub.add_parser("source", help="add, list or remove a monitoring source")
    ssub = p.add_subparsers(dest="what", required=True)
    s = ssub.add_parser("add")
    s.add_argument("type", choices=sorted(SOURCE_NAMES))
    _entry_flags(s)
    ssub.add_parser("list")
    s = ssub.add_parser("rm")
    s.add_argument("id")

    p = sub.add_parser("customer", help="people whose reports go straight to an agent")
    usub = p.add_subparsers(dest="what", required=True)
    u = usub.add_parser("add")
    u.add_argument("address", help="an email address, a phone number, or @example.com for a whole domain")
    u.add_argument("--name", default="")
    u = usub.add_parser("rm")
    u.add_argument("address")
    usub.add_parser("list")

    p = sub.add_parser("report", help="report a bug yourself; an agent picks it up")
    p.add_argument("text")
    p.add_argument("--title", default="")

    for name, text in (("ask", "ask the person who reported a ticket a question"),
                       ("tell", "tell the person who reported a ticket something")):
        p = sub.add_parser(name, help=text)
        p.add_argument("ref", help="the ticket, for example SHOP-12")
        p.add_argument("text")

    p = sub.add_parser("minor", help="let agents decide small reversible things without stopping")
    p.add_argument("state", choices=("on", "off"))

    p = sub.add_parser("week", help="counts from stored On-call activity")
    p.add_argument("--json", action="store_true")
    p.add_argument("--since")
    p.add_argument("--weeks", type=int, default=2)
    p.add_argument("--detail", action="store_true")
    p.add_argument("--post", nargs="?", const="text", choices=("text", "markdown"))
    sub.add_parser("decisions", help="what agents decided without you, each with its undo")
    return parser


# -- building an entry ------------------------------------------------------

def _collect(kind, etype, fields, args, io):
    """(entry, secrets) for one channel or source, from flags then prompts."""
    names = {f["name"] for f in fields}
    entry, typed = {"type": etype}, {}
    for key, value in _pairs(args.set, "--set").items():
        if key not in names or key.endswith("_vault"):
            raise Stop(f"{etype} has no setting called {key}. It has: "
                       + ", ".join(sorted(n for n in names if not n.endswith("_vault"))) + ".")
        entry[key] = value
    for key, value in _pairs(args.vault, "--vault").items():
        field = key if key.endswith("_vault") else key + "_vault"
        if field not in names:
            raise Stop(f"{etype} has no secret called {key}.")
        entry[field] = value
    if args.secret_stdin:
        field = args.secret_stdin if args.secret_stdin.endswith("_vault") else args.secret_stdin + "_vault"
        if field not in names:
            raise Stop(f"{etype} has no secret called {args.secret_stdin}.")
        value = io.stdin.readline().strip()
        if not value:
            raise Stop("Nothing arrived on stdin.")
        typed[field] = value
    if kind == "source" and etype == "ci" and "repo" not in entry:
        from .ci_source import detect_repository
        entry.update(detect_repository())
    for field in fields:
        name = field["name"]
        if name in entry or name in typed:
            continue
        if field["optional"]:
            continue
        default = ""
        if kind == "channel" and etype == "ntfy" and name == "topic":
            entry[name] = "ccc-oncall-" + _secrets.token_hex(12)
            continue
        if field["secret"]:
            if not io.interactive:
                if field["optional"]:
                    continue
                raise Stop(f"{etype} needs {field['label'].lower()}. Give it with "
                           f"--vault {name[:-6]}=ENTRY or --secret-stdin {name[:-6]}.")
            value = io.secret(f"{field['label']} (hidden): ").strip()
            if value:
                typed[name] = value
            continue
        if not io.interactive:
            if default:
                entry[name] = default
            elif not field["optional"]:
                raise Stop(f"{etype} needs {field['label'].lower()}. Give it with --set {name}=...")
            continue
        hint = f" (like {field['placeholder']})" if field["placeholder"] and not default else ""
        value = io.prompt(field["label"] + hint, default)
        if value:
            entry[name] = value
    return entry, typed


def _add_channel(client, etype, args, io, types):
    fields = types.get(etype) or []
    labels = {f["name"]: f["label"] for f in fields}
    entry, typed = _collect("channel", etype, fields, args, io)
    if getattr(args, "intake", False):
        entry["intake"] = True
    asked_servers = False
    for _attempt in range(6):
        code, res = client.post("channel", {"channel": entry, "secrets": typed})
        if code == 200 and res.get("ok"):
            saved = res.get("channel") or {}
            if etype == "ntfy":
                io.out(f"Subscribe in the ntfy app: {saved.get('server') or 'https://ntfy.sh'}/{saved.get('topic')}")
            io.out(f"Added {CHANNEL_NAMES.get(etype, etype)}"
                   + (f" ({saved.get('owner')})" if saved.get("owner") else "") + "."
                   + (f" {res['note']}" if res.get("note") else ""))
            return saved
        error = str(res.get("error") or f"CCC answered HTTP {code}.")
        if code == 404 and "preview" in error.lower():
            raise Stop(PREVIEW_OFF)
        if code == 409 and io.interactive:
            # Telegram: the bot has to hear from the person once before it can write to them.
            io.out(error.replace(", then add it again", ""))
            io.ask("Press Enter when you have done that. ")
            continue
        if "mail servers" in error and io.interactive and not asked_servers:
            asked_servers = True
            io.out(error)
            for name in ("smtp_host", "imap_host"):
                value = io.prompt(labels.get(name, name))
                if value:
                    entry[name] = value
            continue
        raise Stop(" ".join(error.split()) + f" Check the settings, then run ccc oncall channel add {etype} again.")
    raise Stop("That did not work. Run ccc oncall channel add again when you are ready.")


def _add_source(client, etype, args, io, types):
    entry, typed = _collect("source", etype, types.get(etype) or [], args, io)
    code, res = client.post("source", {"source": entry, "secrets": typed})
    if code != 200 or not res.get("ok"):
        raise Stop(str(res.get("error") or f"CCC answered HTTP {code}."))
    io.out(f"Added {SOURCE_NAMES.get(etype, etype)}." + (f" {res['note']}" if res.get("note") else ""))
    return res.get("source") or {}


# -- commands ---------------------------------------------------------------

def _print_status(status, io):
    state = "on" if status.get("enabled") else "off"
    if status.get("enabled") and status.get("paused"):
        state = "paused"
    io.out(f"On-call is {state}." + (f" Queue: {status['queue']}." if status.get("queue") else ""))
    if not status.get("wt"):
        io.out("WatchTower is not installed, so no agent will pick tickets up.")
    channels = status.get("channels") or []
    if channels:
        io.out("Channels:")
        for c in channels:
            flags = [] if c.get("enabled") else ["off"]
            if c.get("intake"):
                flags.append("reads customer reports")
            if c.get("last_ok") is False:
                flags.append("last send failed")
            elif c.get("last_ok") is None:
                flags.append("not tested")
            if not c.get("can_receive"):
                flags.append("send only")
            if c.get("error"):
                flags.append("error: " + c["error"])
            io.out(f"  {c['id']}  {c.get('owner') or ''}".rstrip()
                   + (f"  ({'; '.join(flags)})" if flags else ""))
    else:
        io.out("No message channel yet. Add one: ccc oncall channel add ntfy")
    sources = status.get("sources") or []
    if sources:
        io.out("Monitoring:")
        for s in sources:
            flags = [] if s.get("enabled") else ["off"]
            if s.get("error"):
                flags.append("error: " + s["error"])
            io.out(f"  {s['id']}  -> {s.get('queue') or '?'}" + (f"  ({'; '.join(flags)})" if flags else ""))
    else:
        io.out("No monitoring source configured. Add one: ccc oncall source add http")
    waiting = [w for w in status.get("waiting") or [] if w.get("needs_reply")]
    if waiting:
        io.out(f"{len(waiting)} waiting on you:")
        for w in waiting:
            io.out(f"  #{w['id']}  {w.get('line') or ''}")
    else:
        io.out("Nothing is waiting on you.")
    if status.get("held_reports"):
        io.out(f"{status['held_reports']} report(s) from people not on your customer list are held.")
    if status.get("minor_decisions"):
        io.out("Agents decide small reversible things themselves. See them: ccc oncall decisions")


def _setup(client, args, io):
    code, status = client.get("status")
    if code == 404:
        if not (args.yes or io.yes("On-call is a preview. Turn it on for this machine?")):
            raise Stop("Left off.")
        fcode, fres = client.post("", {"name": "oncall", "on": True}, path="/api/features/flag")
        if fcode != 200 or not fres.get("ok"):
            raise Stop(str(fres.get("error") or "Could not turn the preview on."))
        io.out("Preview turned on. Reload any open CCC page to see Settings, On-call.")
        code, status = client.get("status")
    if code != 200:
        raise Stop(str(status.get("error") or f"CCC answered HTTP {code}."))

    repo = args.repo or status["config"].get("repo") or ""
    if not repo:
        repo = io.prompt("Folder of the code agents should fix", os.getcwd())
    repo = os.path.abspath(os.path.expanduser(repo)) if repo else ""
    if repo and not os.path.isdir(repo):
        raise Stop(f"{repo} is not a folder. Run ccc oncall setup --repo PATH with an existing folder.")
    queue = args.queue or status.get("queue") or ""
    if not queue:
        guess = re.sub(r"[^A-Z0-9]+", "-", os.path.basename(repo).upper()).strip("-")[:16] or "BUGS"
        queue = guess
    res = client.must("config", {"queue": queue, "repo": repo})
    queue = res["config"]["queue"]
    io.out(f"Bug tickets go to the queue {queue}" + (f", worked on in {repo}." if repo else "."))
    for note in res.get("notes") or []:
        io.out(note)

    status = client.status()
    if not status.get("channels") or args.channel:
        etype = args.channel
        if not etype:
            if not io.interactive:
                raise Stop("Say how CCC should reach you: --channel ntfy (or telegram, email, imessage, sms, whatsapp, command).")
            order = ["ntfy", "telegram", "email", "imessage", "sms", "whatsapp", "command"]
            io.out("How should CCC reach you?")
            for i, name in enumerate(order, 1):
                io.out(f"  {i}. {CHANNEL_NAMES[name]}")
            pick = io.prompt("Pick a number", "1")
            etype = order[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(order) else pick
            if etype not in CHANNEL_NAMES:
                raise Stop(f"{pick} is not one of the choices.")
        _add_channel(client, etype, args, io, status.get("channel_types") or {})

    code, res = client.post("test")
    for row in res.get("results") or []:
        io.out(f"Test message to {row['channel']}: " + ("sent." if row["ok"] else f"failed. {row['error']}"))
    if not res.get("results"):
        raise Stop(str(res.get("error") or "No channel to test."))
    if not any(r["ok"] for r in res["results"]):
        client.must("config", {"enabled": False})
        raise Stop("No test message got through. On-call is off. Fix the channel, then run ccc oncall setup again.")

    if not any(s.get("type") == "ci" for s in status.get("config", {}).get("sources", [])):
        from .ci_source import setup_candidate
        detected = setup_candidate(repo)
        if detected and io.interactive and io.yes("Watch failed CI runs for this repository?"):
            source_code, source_result = client.post("source", {"source": {"type": "ci", **detected}})
            if source_code != 200:
                io.out(str(source_result.get("error") or "CI source could not connect."))
            else:
                io.out("CI monitoring added.")

    client.must("config", {"enabled": True})
    io.out("")
    io.out("On-call is on.")
    io.out('Report a bug:            ccc oncall report "Checkout button does nothing on Safari"')
    io.out("Add monitoring:          ccc oncall source add github   (or sentry, posthog, http, command)")
    io.out("Let agents decide small reversible things:  ccc oncall minor on")
    io.out("See what is waiting:     ccc oncall status")
    receives = any(c.get("enabled") and c.get("can_receive") for c in client.status().get("channels", []))
    io.out("On-call is on: " + ("alerts and replies are enabled." if receives else "alerts are enabled; channels are send only.")
           + " Turn it off: ccc oncall off")
    return 0


def run(argv, *, base, get, post, io=None):
    """Run one ``ccc oncall ...`` command. Returns the exit code."""
    if argv[:1] == ["demo"]: return __import__("ccc_server.oncall.demo", fromlist=["main"]).main(argv[1:])
    io = io or IO()
    args = build_parser().parse_args(argv)
    if args.action == "setup" and not args.yes and not args.secret_stdin:
        io.interactive = True
    client = Client(base, get, post)
    try:
        return _run(args, client, io) or 0
    except Stop as stop:
        print(f"ccc oncall: {stop}", file=sys.stderr)
        return 1


def _run(args, client, io):
    action = args.action
    if action == "week":
        from urllib.parse import urlencode
        from .week import render
        query = {"weeks": args.weeks, "detail": "1" if args.detail and not args.post else "0"}
        if args.since:
            query["since"] = args.since
        code, data = client.get("week?" + urlencode(query))
        if code != 200 or not data.get("ok"):
            raise Stop(data.get("error", "Could not read the week."))
        if data.get("limits") and args.post:
            raise Stop("Export unavailable: " + " ".join(data["limits"]))
        if args.post:
            io.out(render(data, args.post == "markdown"))
        elif args.json:
            io.out(json.dumps(data, indent=2))
        else:
            for period in data["periods"]:
                io.out(render({"periods": [period]}))
                for label, counts in (("Sources", period["reports"]["by_source"]),
                                      ("Channels", period["reports"]["by_channel"]),
                                      ("Owner message kinds", period["owner_messages"])):
                    io.out(label + ": " + (", ".join(f"{k}: {v}" for k, v in counts.items()) or "none"))
                if args.detail:
                    io.out("Reporters: " + ", ".join(period.get("reporters", [])))
            for limit in data.get("limits", []):
                io.out("Incomplete: " + limit)
        return 0
    if action == "setup":
        return _setup(client, args, io)
    if action == "status":
        status = client.status()
        if args.json:
            io.out(json.dumps(status, indent=2))
        else:
            _print_status(status, io)
        return 0
    if action == "test":
        code, res = client.post("test", {"channel": args.channel})
        if not res.get("results"):
            raise Stop(PREVIEW_OFF if code == 404 else str(res.get("error") or f"CCC answered HTTP {code}."))
        for row in res["results"]:
            io.out(f"{row['channel']}: " + ("sent." if row["ok"] else f"failed. {row['error']}"))
        return 0 if res.get("ok") else 1
    if action in ("on", "off"):
        res = client.must("config", {"enabled": action == "on"})
        io.out(f"On-call is {action}.")
        for note in res.get("notes") or []:
            io.out(note)
        return 0
    if action in ("channel", "source"):
        status = client.status()
        rows = status.get(action + "s") or []
        if args.what == "list":
            for row in rows:
                io.out(f"{row['id']}  {row['type']}  {'on' if row.get('enabled') else 'off'}"
                       + (f"  {row.get('owner')}" if row.get("owner") else ""))
            if not rows:
                io.out(f"No {action} yet.")
            return 0
        if args.what == "rm":
            if args.id not in [r["id"] for r in rows]:
                raise Stop(f"No {action} called {args.id}.")
            client.must(action, {"action": "remove", action: {"id": args.id}})
            io.out(f"Removed {args.id}.")
            return 0
        if action == "channel":
            _add_channel(client, args.type, args, io, status.get("channel_types") or {})
        else:
            _add_source(client, args.type, args, io, status.get("source_types") or {})
        return 0
    if action == "customer":
        if args.what == "list":
            known = (client.status()["config"].get("customers") or {}).get("known") or []
            for k in known:
                io.out(f"{k.get('address')}" + (f"  {k['name']}" if k.get("name") else ""))
            if not known:
                io.out("No customers listed. Reports from anyone wait for your go-ahead.")
            return 0
        body = {"action": "add" if args.what == "add" else "remove", "address": args.address}
        if args.what == "add":
            body["name"] = args.name
        client.must("customer", body)
        io.out(f"Reports from {args.address} go straight to an agent." if args.what == "add"
               else f"Removed {args.address}.")
        return 0
    if action == "report":
        res = client.must("report", {"text": args.text, "title": args.title})
        io.out(f"Filed as {res.get('ref')}. An agent will pick it up.")
        return 0
    if action in ("ask", "tell"):
        res = client.must("customer", {"action": action, "ref": args.ref, "kind": action, "text": args.text})
        if res.get("state") == "sent":
            io.out(f"Sent to {res.get('to') or 'the reporter'}.")
        else:
            io.out("Waiting for the owner to approve the message. The reply will land on the ticket.")
        return 0
    if action == "minor":
        res = client.must("config", {"minor_decisions": {"enabled": args.state == "on"}})
        io.out("Agents now decide small reversible things and keep going. Money, messages to customers, "
               "public posts and anything that cannot be undone still come to you."
               if args.state == "on" else "Agents ask you about every decision again.")
        for note in res.get("notes") or []:
            io.out(note)
        return 0
    if action == "decisions":
        code, res = client.get("decisions")
        if code != 200:
            raise Stop(str(res.get("error") or f"CCC answered HTTP {code}."))
        rows = res.get("decisions") or []
        for row in rows:
            if not row.get("recommend"):
                io.out(f"{row['ref']}  {row['state']}  {row.get('reason') or ''}".rstrip())
                continue
            io.out(f"{row['ref']}  {row.get('question') or 'A small decision'}")
            io.out(f"  Decided: {row['recommend']}")
            if row.get("undo"):
                io.out(f"  Undo: {row['undo']}")
            io.out(f"  {_decision_state(row)}")
        if not rows:
            io.out("Agents have not decided anything without you.")
        return 0
    raise Stop(f"Unknown command {action}.")
