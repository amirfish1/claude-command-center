#!/usr/bin/env python3
"""Build a realistic synthetic $HOME for the CCC training film.

local-build-home.py proves the live-server capture path with a thin fleet
(7 sessions, one engine, "Step N" transcripts). The film needs footage that
looks like real daily use, so this builder materialises one made-up product,
"Harbor" (a booking app), with:

  * ~25 sessions across three repos, written as believable transcripts
    (prompts, prose replies, Bash/Read/Edit tool calls with output)
  * several engines and models, parent/child spawns, a group chat
  * WatchTower-style queues, usage history, a peer machine

Every visible string is synthetic. Repos live under the fake HOME, so CCC
renders them as ~/code/<name> and nothing real can leak into the UI. Run, then boot server.py against it:

    HOME=<out> PORT=8097 CCC_EPHEMERAL=1 CCC_TELEMETRY_DISABLED=1 \
        CCC_SKIP_SKILL_INSTALL=1 python3 server.py --port 8097

Usage:
    python3 scripts/story-capture/seeds/film-build-home.py [--out DIR]

Idempotent: wipes and rebuilds --out (default /private/tmp/ccc-film-home).
"""
import argparse
import importlib.util
import json
import os
import shutil
import uuid as uuidlib
from datetime import timedelta
from pathlib import Path

# Reuse the thin builder's event/usage/queue helpers.
_spec = importlib.util.spec_from_file_location(
    "local_build_home", Path(__file__).with_name("local-build-home.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
NOW, iso, iso_s, slug = base.NOW, base.iso, base.iso_s, base.slug

# Real git repos under the fake HOME (set in main), so every engine's cwd
# resolves to a repo and CCC renders them as ~/code/<name>.
REPOS = {}

CLAUDE_MODELS = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}


def sid_for(n):
    # Stable, random-looking ids (not 1111…): uuid5 of a fixed namespace.
    return str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, f"harbor-film/{n}"))


# --- transcript DSL ---------------------------------------------------------
# A script is a list of steps:
#   ("user", text)
#   ("say", text)                         assistant prose
#   ("tool", name, input_dict, output)    assistant tool call + result
# Token usage ramps toward `ctx_pct` of a 200k window by the last turn.

def claude_transcript(sid, repo, title, model_key, start, steps, ctx_pct=30,
                      parent_sid=None):
    cwd = REPOS[repo]
    model = CLAUDE_MODELS[model_key]
    lines = [{"type": "custom-title", "customTitle": title, "sessionId": sid}]
    parent = None
    ts = start
    n_asst = max(1, sum(1 for s in steps if s[0] in ("say", "tool")))
    target = int(200_000 * ctx_pct / 100)
    k = 0
    for i, step in enumerate(steps):
        ts = ts + timedelta(seconds=35 + (i * 17) % 90)
        uid = f"{sid[:8]}-{i:03d}"
        if step[0] == "user":
            lines.append(base.user_ev(sid, cwd, uid, parent, ts, text=step[1]))
            parent = uid
            continue
        k += 1
        ctx = int(target * k / n_asst)
        usage = base.usage_block(tin=6 + k, tout=180 + 40 * k,
                                 cache_read=max(0, ctx - 2400), cache_creation=2400)
        if step[0] == "say":
            lines.append(base.assistant_ev(sid, cwd, model, uid, parent, ts, step[1], usage))
            parent = uid
        elif step[0] == "tool":
            _, name, inp, out = step
            tid = f"toolu_{uid.replace('-', '')}"
            ev = base.assistant_ev(sid, cwd, model, uid, parent, ts, "", usage,
                                   tool={"id": tid, "name": name, "input": inp})
            ev["message"]["content"] = [c for c in ev["message"]["content"]
                                        if c.get("type") != "text" or c.get("text")]
            lines.append(ev)
            ts = ts + timedelta(seconds=4)
            rid = uid + "r"
            lines.append(base.user_ev(sid, cwd, rid, uid, ts,
                                      tool_result={"id": tid, "content": out}))
            parent = rid
    return slug(cwd), lines, ts


# --- the Harbor fleet -------------------------------------------------------
# Each entry: key, engine, repo, title, model, hours_ago, ctx_pct, parent_key,
# steps. Engines other than claude are written by their own writer below.

def T(cmd, out):
    return ("tool", "Bash", {"command": cmd}, out)


def R(path, out):
    return ("tool", "Read", {"file_path": path}, out)


def E(path, old, new):
    return ("tool", "Edit", {"file_path": path, "old_string": old, "new_string": new},
            f"The file {path} has been updated.")


# Paths inside tool calls read as the user would see them.
API, WEB, IOS = "~/code/harbor-api", "~/code/harbor-web", "~/code/harbor-ios"

FLEET = [
    # ---------------- harbor-api ----------------
    dict(key="refunds", engine="claude", repo="harbor-api", model="opus",
         title="Add partial refunds to the booking API", hours=0.4, ctx=46,
         steps=[
             ("user", "Guests keep asking for partial refunds when they cut a stay short. "
                      "Add a partial refund endpoint to harbor-api. Split the tests and the "
                      "review out to other sessions so this one stays focused."),
             ("say", "I'll start by reading how full refunds work today, then add a "
                     "`POST /bookings/{id}/refunds` that takes an amount."),
             R(f"{API}/app/payments/refunds.py",
               "def refund_booking(booking):\n    charge = booking.charge\n"
               "    stripe.Refund.create(charge=charge.id)\n    booking.status = 'refunded'\n"),
             ("say", "Full refunds call Stripe with no amount. I'll add an `amount_cents` "
                     "argument, cap it at what's left on the charge, and record each refund "
                     "row so we can show a history."),
             E(f"{API}/app/payments/refunds.py",
               "def refund_booking(booking):",
               "def refund_booking(booking, amount_cents=None):"),
             T("pytest tests/payments -q", "........................                 [100%]\n24 passed in 3.81s"),
             ("say", "The endpoint is in and the existing payment tests pass. I spawned two "
                     "helpers:\n\n- **Write partial-refund tests** (Codex) for edge cases: "
                     "over-refund, currency rounding, and a refund on a cancelled stay.\n"
                     "- **Review refunds PR** (Claude Sonnet) to read the diff cold.\n\n"
                     "I'll merge once both report back."),
         ]),
    dict(key="refund-tests", engine="codex", repo="harbor-api", model="gpt-5.5",
         title="Write partial-refund tests", hours=0.3, ctx=22, parent="refunds",
         steps=[
             ("user", "Write tests for the new partial refund endpoint: over-refund is "
                      "rejected, amounts round to the cent, cancelled stays refund cleanly."),
             ("say", "Adding `tests/payments/test_partial_refunds.py` with six cases."),
             T("pytest tests/payments/test_partial_refunds.py -q",
               "......                                                   [100%]\n6 passed in 0.94s"),
             ("say", "All six pass. One finding: refunding exactly the remaining balance "
                     "left the booking as `partially_refunded`; it should be `refunded`. "
                     "I sent that back to the parent session."),
         ]),
    dict(key="refund-review", engine="claude", repo="harbor-api", model="sonnet",
         title="Review refunds PR", hours=0.25, ctx=18, parent="refunds",
         steps=[
             ("user", "Review the partial refunds diff in harbor-api. Be picky about money."),
             T("git diff main --stat", " app/payments/refunds.py   | 41 +++++++++++++----\n"
               " app/api/bookings.py       | 18 ++++++++\n 2 files changed, 52 insertions(+), 7 deletions(-)"),
             ("say", "Two issues, both small:\n\n1. `amount_cents` is trusted from the "
                     "request body before the cap is applied. Validate first.\n2. The refund "
                     "row is written before Stripe confirms. If Stripe fails we show a refund "
                     "that never happened.\n\nOtherwise it looks right."),
         ]),
    dict(key="availability", engine="claude", repo="harbor-api", model="opus",
         title="Investigate slow /availability query", hours=2.5, ctx=84,
         steps=[
             ("user", "/availability takes 4 seconds for big properties. Find out why."),
             T("python manage.py explain_availability --property 812",
               "Seq Scan on nights  (cost=0.00..48211.90 rows=1204 width=32)\n"
               "  Filter: ((property_id = 812) AND (night >= '2026-11-01'))\n"
               "Execution Time: 3912.448 ms"),
             ("say", "It's a sequential scan over every night in the table. There's no "
                     "index on `(property_id, night)`."),
             E(f"{API}/migrations/0042_nights_property_night_idx.py", "", 
               "CREATE INDEX CONCURRENTLY nights_property_night_idx ON nights (property_id, night);"),
             T("python manage.py explain_availability --property 812",
               "Index Scan using nights_property_night_idx on nights\nExecution Time: 41.207 ms"),
             ("say", "From 3.9 s to 41 ms. The migration builds the index concurrently, so "
                     "it's safe to run on production without locking bookings."),
         ]),
    dict(key="tz", engine="claude", repo="harbor-api", model="sonnet",
         title="Fix reminder emails sent in the wrong time zone", hours=1.1, ctx=31,
         steps=[
             ("user", "Guests in Lisbon got their check-in reminder at 3am. Fix it."),
             R(f"{API}/app/reminders/schedule.py",
               "send_at = booking.check_in - timedelta(hours=24)\n"),
             ("say", "Reminders are scheduled in server time (UTC), not the property's "
                     "time zone."),
             ("say", "Before I change it: should reminders follow the **property's** time "
                     "zone or the **guest's** phone time zone? They differ for guests who "
                     "travel. I'd pick the property's.\n\n<session-state>\nDID: Found the "
                     "reminder bug.\nNEXT_STEP_USER: Pick property or guest time zone.\n"
                     "</session-state>"),
         ]),
    dict(key="pg17", engine="kimi", repo="harbor-api", model="kimi-k2.5",
         title="Upgrade staging to Postgres 17", hours=5, ctx=38,
         steps=[
             ("user", "Upgrade the staging database to Postgres 17 and check nothing broke."),
             T("make staging-db-upgrade VERSION=17", "pg_upgrade: Upgrade Complete\n"),
             T("pytest -q", "412 passed, 3 skipped in 58.20s"),
             ("say", "Staging is on Postgres 17.2 and the full suite passes."),
         ]),
    dict(key="ratelimit", engine="codex", repo="harbor-api", model="gpt-5.5",
         title="Rate-limit the public search API", hours=7, ctx=27,
         steps=[
             ("user", "Partners are hammering /search. Add a per-key rate limit."),
             T("rg -n 'def search' app/api", "app/api/search.py:14:def search(request):"),
             ("say", "Added a token bucket per API key: 60 requests a minute, with "
                     "`Retry-After` on a 429."),
         ]),
    dict(key="webhooks", engine="claude", repo="harbor-api", model="haiku",
         title="Retry failed Stripe webhooks", hours=26, ctx=12,
         steps=[
             ("user", "Stripe webhooks fail when we deploy. Add retries."),
             ("say", "Done: failed webhooks go to a retry table and a worker replays them "
                     "with backoff."),
         ]),
    # ---------------- harbor-web ----------------
    dict(key="checkout", engine="claude", repo="harbor-web", model="opus",
         title="Redesign the checkout page", hours=0.8, ctx=57,
         steps=[
             ("user", "Checkout converts badly on phones. Redesign it: one column, price "
                      "summary pinned to the bottom, Apple Pay first."),
             R(f"{WEB}/src/pages/checkout.tsx", "export default function Checkout() {\n  return <TwoColumn ...\n"),
             ("say", "I'll build it as one column and split the mobile polish into its own "
                     "session so we can work in parallel."),
             E(f"{WEB}/src/pages/checkout.tsx", "<TwoColumn", "<CheckoutStack"),
             T("npm run test -- checkout", "PASS  src/pages/checkout.test.tsx (12 tests)"),
             ("say", "Desktop layout is done. Mobile polish is running in **Checkout: phone "
                     "layout** (Gemini)."),
         ]),
    dict(key="checkout-mobile", engine="gemini", repo="harbor-web", model="gemini-3-pro",
         title="Checkout: phone layout", hours=0.6, ctx=24, parent="checkout",
         steps=[
             ("user", "Make the new checkout work at 390px wide. Pin the price summary."),
             ("say", "Pinned the summary with `position: sticky` and moved Apple Pay above "
                     "the card form. Screenshot attached for review."),
         ]),
    dict(key="darkmode", engine="codex", repo="harbor-web", model="gpt-5.5",
         title="Dark mode for the host dashboard", hours=3, ctx=41,
         steps=[
             ("user", "Hosts work at night. Add dark mode to the dashboard."),
             T("npm run lint", "✔ No problems"),
             ("say", "Dark mode follows the system setting, with a toggle in the header."),
         ]),
    dict(key="flaky", engine="claude", repo="harbor-web", model="sonnet",
         title="Fix the flaky login end-to-end test", hours=9, ctx=19,
         steps=[
             ("user", "The login e2e test fails about 1 run in 5. Fix it."),
             ("say", "It clicked before the form hydrated. It now waits for the submit "
                     "button to be enabled. 50 runs in a row pass."),
         ]),
    dict(key="seo", engine="kimi", repo="harbor-web", model="kimi-k2.5",
         title="Add structured data to listing pages", hours=30, ctx=15,
         steps=[
             ("user", "Add schema.org markup to listing pages so Google shows prices."),
             ("say", "Added `LodgingBusiness` JSON-LD with price range and rating."),
         ]),
    # ---------------- harbor-ios ----------------
    dict(key="deeplinks", engine="claude", repo="harbor-ios", model="opus",
         title="Open bookings from push notifications", hours=1.6, ctx=35,
         steps=[
             ("user", "Tapping a booking notification opens the home screen. It should open "
                      "the booking."),
             T("xcodebuild test -scheme Harbor -only-testing:HarborTests/DeepLinkTests",
               "Test Suite 'DeepLinkTests' passed.\n\t Executed 9 tests, with 0 failures"),
             ("say", "Notifications now carry `harbor://booking/<id>` and the app routes it "
                     "on cold start too."),
         ]),
    dict(key="crash", engine="codex", repo="harbor-ios", model="gpt-5.5",
         title="Crash when the photo picker is cancelled", hours=20, ctx=16,
         steps=[
             ("user", "Crash report: app dies when you cancel the photo picker."),
             ("say", "Force-unwrapped nil result. Fixed and added a test."),
         ]),
    dict(key="widgets", engine="claude", repo="harbor-ios", model="sonnet",
         title="Home screen widget for upcoming stays", hours=48, ctx=22,
         steps=[
             ("user", "Add a home screen widget that shows the next stay."),
             ("say", "Small and medium widgets are in, refreshed by a timeline every hour."),
         ]),
]


# --- engine writers ---------------------------------------------------------
TOUCH = []  # (path, datetime) pairs: files whose mtime should be the last turn

def _ts_series(start, steps):
    out, ts = [], start
    for i, _ in enumerate(steps):
        ts = ts + timedelta(seconds=35 + (i * 17) % 90)
        out.append(ts)
    return out


def write_claude(home, s, sid, start):
    pslug, lines, end = claude_transcript(sid, s["repo"], s["title"], s["model"],
                                          start, s["steps"], s["ctx"])
    d = home / ".claude" / "projects" / pslug
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{sid}.jsonl").open("w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    TOUCH.append((d / f"{sid}.jsonl", end))
    return end


def write_codex(home, s, sid, start, db_rows):
    cwd = REPOS[s["repo"]]
    day = start.strftime("%Y/%m/%d")
    d = home / ".codex" / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rollout-{start.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl"
    stamps = _ts_series(start, s["steps"])
    window = 272_000
    used = int(window * s["ctx"] / 100)
    ev = [{"type": "session_meta", "timestamp": iso(start),
           "payload": {"id": sid, "cwd": cwd, "model": s["model"]}},
          {"type": "turn_context", "timestamp": iso(start),
           "payload": {"cwd": cwd, "model": s["model"]}}]
    first_user = ""
    for i, (step, ts) in enumerate(zip(s["steps"], stamps)):
        t = iso(ts)
        if step[0] == "user":
            first_user = first_user or step[1]
            ev.append({"type": "event_msg", "timestamp": t,
                       "payload": {"type": "user_message", "message": step[1]}})
        elif step[0] == "say":
            ev.append({"type": "event_msg", "timestamp": t,
                       "payload": {"type": "agent_message", "message": step[1]}})
        else:
            cid = f"call_{sid[:6]}_{i}"
            cmd = step[2].get("command") or f"cat {step[2].get('file_path', '')}"
            ev.append({"type": "response_item", "timestamp": t, "payload": {
                "type": "function_call", "name": "exec_command", "call_id": cid,
                "arguments": json.dumps({"cmd": cmd, "workdir": cwd})}})
            ev.append({"type": "response_item", "timestamp": t, "payload": {
                "type": "function_call_output", "call_id": cid, "output": step[3]}})
    end = stamps[-1]
    ev.append({"type": "event_msg", "timestamp": iso(end), "payload": {
        "type": "token_count", "info": {
            "last_token_usage": {"input_tokens": used, "cached_input_tokens": int(used * .8),
                                 "output_tokens": 900, "total_tokens": used + 900},
            "total_token_usage": {"input_tokens": used * 3, "output_tokens": 5200,
                                  "total_tokens": used * 3 + 5200},
            "model_context_window": window}}})
    ev.append({"type": "event_msg", "timestamp": iso(end), "payload": {
        "type": "task_complete", "last_agent_message": s["steps"][-1][1]}})
    with path.open("w") as fh:
        for e in ev:
            fh.write(json.dumps(e) + "\n")
    TOUCH.append((path, end))
    db_rows.append((sid, str(path), cwd, s["title"], first_user, s["model"], "main",
                    int(start.timestamp()), int(end.timestamp())))
    return end


def write_codex_db(home, rows):
    import sqlite3
    p = home / ".codex" / "state_5.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT,
        title TEXT, first_user_message TEXT, model TEXT, git_branch TEXT,
        created_at INTEGER, updated_at INTEGER, archived INTEGER DEFAULT 0,
        source TEXT, agent_nickname TEXT, agent_path TEXT)""")
    c.executemany("INSERT INTO threads (id, rollout_path, cwd, title, first_user_message,"
                  " model, git_branch, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  rows)
    c.commit()
    c.close()


def write_kimi(home, s, sid, start, index_lines):
    cwd = REPOS[s["repo"]]
    ksid = f"session_{sid}"
    sdir = home / ".kimi-code" / "sessions" / ksid
    (sdir / "agents" / "main").mkdir(parents=True, exist_ok=True)
    stamps = _ts_series(start, s["steps"])
    first_user = next((st[1] for st in s["steps"] if st[0] == "user"), "")
    (sdir / "state.json").write_text(json.dumps({
        "title": s["title"], "lastPrompt": first_user, "createdAt": iso(start),
        "updatedAt": iso(stamps[-1]), "workDir": cwd}))
    with (sdir / "agents" / "main" / "wire.jsonl").open("w") as fh:
        fh.write(json.dumps({"type": "config.update", "modelAlias": s["model"]}) + "\n")
        fh.write(json.dumps({"type": "turn.prompt",
                             "input": [{"type": "text", "text": first_user}]}) + "\n")
        fh.write(json.dumps({"type": "context.update_token_count",
                             "tokenCount": int(256_000 * s["ctx"] / 100)}) + "\n")
    TOUCH.append((sdir / "state.json", stamps[-1]))
    TOUCH.append((sdir / "agents" / "main" / "wire.jsonl", stamps[-1]))
    TOUCH.append((tdir_path := home / ".claude" / "command-center" / "acp" / "kimi" / f"{ksid}.jsonl", stamps[-1]))
    index_lines.append({"sessionId": ksid, "sessionDir": str(sdir), "workDir": cwd})
    # CCC's own transcript for the conversation view.
    tdir = home / ".claude" / "command-center" / "acp" / "kimi"
    tdir.mkdir(parents=True, exist_ok=True)
    with (tdir / f"{ksid}.jsonl").open("w") as fh:
        for i, (step, ts) in enumerate(zip(s["steps"], stamps), 1):
            if step[0] == "user":
                rec = {"type": "user_text", "text": step[1], "images": []}
            elif step[0] == "say":
                rec = {"type": "assistant", "message_id": f"m{i}",
                       "blocks": [{"kind": "text", "text": step[1]}]}
            else:
                rec = {"type": "assistant", "message_id": f"m{i}", "blocks": [{
                    "kind": "tool_use", "name": "Shell",
                    "detail": step[2].get("command", ""), "id": f"t{i}",
                    "tool_status": "completed", "output_preview": step[3]}]}
            rec.update({"line": i, "ts": iso(ts)})
            fh.write(json.dumps(rec) + "\n")
    return ksid, stamps[-1]


def write_gemini(home, s, sid, start):
    cwd = REPOS[s["repo"]]
    d = home / ".gemini" / "tmp" / slug(cwd)[-12:]
    (d / "chats").mkdir(parents=True, exist_ok=True)
    (d / ".project_root").write_text(cwd)
    stamps = _ts_series(start, s["steps"])
    used = int(1_000_000 * s["ctx"] / 100)
    msgs = []
    for i, (step, ts) in enumerate(zip(s["steps"], stamps)):
        if step[0] == "user":
            msgs.append({"type": "user", "timestamp": iso(ts), "content": step[1]})
        else:
            m = {"type": "gemini", "id": f"g{i}", "timestamp": iso(ts), "model": s["model"],
                 "content": step[1] if step[0] == "say" else "",
                 "tokens": {"input": used, "cached": 0, "output": 600, "total": used + 600}}
            if step[0] == "tool":
                m["toolCalls"] = [{"id": f"c{i}", "name": "run_shell_command",
                                   "args": {"command": step[2].get("command", "")},
                                   "status": "success", "result": [{"functionResponse": {
                                       "response": {"output": step[3]}}}]}]
            msgs.append(m)
    (d / "chats" / f"session-{sid[:8]}.json").write_text(json.dumps(
        {"sessionId": sid, "lastUpdated": iso(stamps[-1]), "messages": msgs}))
    TOUCH.append((d / "chats" / f"session-{sid[:8]}.json", stamps[-1]))
    return stamps[-1]


def write_group_chat(home, node_id, members):
    """members: list of (sid, label). A closed chat so no orchestrator wakes."""
    gdir = home / ".claude" / "group-chats"
    gdir.mkdir(parents=True, exist_ok=True)
    start = NOW - timedelta(minutes=28)
    stem = f"ship-partial-refunds-{start.strftime('%Y%m%d-%H%M%S')}"
    topic = "Ship partial refunds"
    msgs = [
        (2, members[0], "Endpoint is in. Tests and review are running in my children. "
                        "Web, can you add the refund form to the host dashboard?"),
        (5, members[1], "On it. I'll use the existing money input so rounding matches the API."),
        (9, members[2], "Heads up: refunding the exact remaining balance leaves the booking "
                        "as partially_refunded. Should be refunded."),
        (12, members[0], "Good catch. Fixed in the API and added your case to the tests."),
        (18, None, "Ship it once the web form is merged. I'll do the release notes."),
        (21, members[1], "Form is merged. Screenshot is in the PR."),
    ]
    parts = ", ".join(f"`{lab}`" for _, lab in members)
    md = [f"# Group Chat — {topic}",
          f"**Started:** {start.strftime('%Y-%m-%d %A %H:%M:%S')} UTC",
          "**Mode:** topic", f"**Participants:** {parts}, `human`", "", "---", ""]
    for mins, who, text in msgs:
        ts = (start + timedelta(minutes=mins)).strftime("%Y-%m-%d %A %H:%M:%S")
        head = f"{who[0][:8]}: {who[1]} 💬" if who else "Human"
        md += [f"## {ts} UTC — {head}", "", text, ""]
    (gdir / f"{stem}.md").write_text("\n".join(md))
    last = start + timedelta(minutes=21)
    (gdir / f"{stem}.json").write_text(json.dumps({
        "uuid": str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "harbor-film/gc")),
        "host_node": node_id, "session_ids": [m[0] for m in members], "topic": topic,
        "mode": "topic", "name_map": {m[0]: m[1] for m in members}, "include_human": True,
        "started_at": int(start.timestamp()), "archived": False,
        "closed_at": None, "last_message_at": int(last.timestamp()),
        "lane": None, "keywords": [], "nudge_log": [], "read_state": {}, "nudged_at": {}}))


def write_vault(state_dir):
    t = int((NOW - timedelta(days=3)).timestamp())
    entries = {
        "stripe-test": dict(kind="api_key", service="Stripe", env_var="STRIPE_API_KEY",
                            website="https://dashboard.stripe.com", hint="4f2a"),
        "postmark-token": dict(kind="token", service="Postmark", env_var="POSTMARK_TOKEN",
                               website="https://postmarkapp.com", hint="91c0"),
        "apple-connect": dict(kind="api_key", service="App Store Connect",
                              env_var="ASC_KEY", hint="7d3e"),
        "staging-admin": dict(kind="login", service="Harbor staging admin",
                              username="ops@harbor.example", hint=""),
    }
    out = {}
    for name, e in entries.items():
        out[name] = {"name": name, "username": "", "env_var": "", "website": "", "notes": "",
                     "created_at": t, "updated_at": t, "value_updated_at": t, **e}
    v = state_dir / "vault"
    v.mkdir(parents=True, exist_ok=True)
    (v / "index.json").write_text(json.dumps({"version": 1, "entries": out}, indent=1))


def write_queue(state_dir):
    q = base.qitem
    a, w, i = REPOS["harbor-api"], REPOS["harbor-web"], REPOS["harbor-ios"]
    items = [
        q(1, "HARBOR", 41, "Booking calendar shows the wrong month after midnight",
          "open", minutes_ago_created=22, priority="p1", value="H", repo_path=w),
        q(2, "HARBOR", 42, "Guest can't remove a saved card on iOS", "open",
          minutes_ago_created=14, repo_path=i),
        q(3, "HARBOR", 40, "Host payout CSV is missing the fee column", "in_progress",
          claimed_by="harbor-drain", minutes_ago_created=70, minutes_ago_claimed=6,
          repo_path=a),
        q(4, "HARBOR", 39, "Refund email says 'undefined' for the amount", "closed",
          claimed_by="harbor-drain", minutes_ago_created=180, minutes_ago_closed=95,
          repo_path=a),
        q(5, "HARBOR", 38, "Search filters reset when you go back", "closed",
          claimed_by="harbor-drain", minutes_ago_created=300, minutes_ago_closed=200,
          repo_path=w),
        q(6, "INBOX", 7, "WhatsApp from a host: 'can guests pay a deposit only?'", "open",
          minutes_ago_created=9, readiness="needs-shaping", repo_path=a),
        q(7, "INBOX", 6, "iMessage: 'checkout button is grey on my phone'", "closed",
          claimed_by="inbox-triage", minutes_ago_created=240, minutes_ago_closed=200,
          repo_path=w),
    ]
    (state_dir / "ux-fixes-queue.json").write_text(
        json.dumps({"counter": len(items), "items": items}, indent=2))
    wt = state_dir.parent.parent / ".watchtower"
    wt.mkdir(parents=True, exist_ok=True)
    (wt / "queue-config.json").write_text(json.dumps({
        "HARBOR": {"auto_drain": True, "repo_path": a, "claim_types": []},
        "INBOX": {"auto_drain": False, "repo_path": a, "claim_types": []}}, indent=2))


def write_federation(state_dir):
    node = {"node_id": "4c8e1f20-9a7b-4d3c-8e21-5f0a6b7c8d9e", "display_name": "macbook",
            "created_at": iso_s(NOW - timedelta(days=60))}
    (state_dir / "node.json").write_text(json.dumps(node, indent=2))
    peers = [
        {"node_id": "a3d9c7e1-2b4f-4a6e-9c8d-1e2f3a4b5c6d", "name": "build-server",
         "transport": {"type": "ssh", "host": "build.demo.internal", "user": "demo"},
         "added_at": iso_s(NOW - timedelta(days=30)),
         "last_seen": iso_s(NOW - timedelta(minutes=2))},
        {"node_id": "e7f6a5b4-c3d2-4e1f-8a9b-0c1d2e3f4a5b", "name": "studio-mac",
         "transport": {"type": "ssh", "host": "studio.demo.internal", "user": "demo"},
         "added_at": iso_s(NOW - timedelta(days=12)),
         "last_seen": iso_s(NOW - timedelta(minutes=11))},
    ]
    (state_dir / "peers.json").write_text(json.dumps(peers, indent=2))
    return node["node_id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/private/tmp/ccc-film-home")
    args = ap.parse_args()
    home = Path(os.path.abspath(args.out))
    if home.exists():
        shutil.rmtree(home)
    state_dir = home / ".claude" / "command-center"
    state_dir.mkdir(parents=True, exist_ok=True)
    import subprocess
    for name in ("harbor-api", "harbor-web", "harbor-ios"):
        REPOS[name] = str(home / "code" / name)
        subprocess.run(["git", "init", "-q", "-b", "main", REPOS[name]], check=False)

    ids, codex_rows, kimi_index = {}, [], []
    for n, s in enumerate(FLEET):
        sid = sid_for(n)
        start = NOW - timedelta(hours=s["hours"]) - timedelta(minutes=12)
        eng = s["engine"]
        if eng == "claude":
            write_claude(home, s, sid, start)
        elif eng == "codex":
            write_codex(home, s, sid, start, codex_rows)
        elif eng == "kimi":
            sid, _ = write_kimi(home, s, sid, start, kimi_index)
        elif eng == "gemini":
            write_gemini(home, s, sid, start)
        ids[s["key"]] = (sid, eng)
    write_codex_db(home, codex_rows)
    kroot = home / ".kimi-code"
    kroot.mkdir(parents=True, exist_ok=True)
    (kroot / "session_index.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in kimi_index))

    edges = [{"parent": ids[s["parent"]][0], "child": ids[s["key"]][0],
              "source": "ccc-spawn", "engine": s["engine"], "resumable": True,
              "name": s["title"], "model": s["model"]}
             for s in FLEET if s.get("parent")]
    (state_dir / "session-graph.json").write_text(json.dumps(
        {"edges": edges, "claude_task_enriched": {}, "kimi_subagents_enriched": []}, indent=1))
    (state_dir / "session-names.json").write_text(json.dumps(
        {ids[s["key"]][0]: s["title"] for s in FLEET}, indent=1))

    base.write_usage(state_dir)
    write_queue(state_dir)
    node_id = write_federation(state_dir)
    write_vault(state_dir)
    write_group_chat(home, node_id, [(ids["refunds"][0], "API"),
                                     (ids["checkout"][0], "Web"),
                                     (ids["refund-tests"][0], "Tests")])
    # Onboarding done, repo list, empty git repos so grouping resolves.
    (state_dir / "onboarding.json").write_text(json.dumps(
        {"completed": True, "completed_at": iso_s(NOW - timedelta(days=30))}))
    (state_dir / "fleet.json").write_text(json.dumps({"automap": False, "pinned": [],
                                                      "hidden": []}))
    (state_dir / "custom-repos.txt").write_text("\n".join(REPOS.values()) + "\n")
    (state_dir / "recent-repos.txt").write_text("\n".join(REPOS.values()) + "\n")
    # Row ages come from file mtimes: stamp each file with its last turn.
    for path, t in TOUCH:
        os.utime(path, (t.timestamp(), t.timestamp()))
    print(f"[film-seed] HOME ready at {home}: {len(FLEET)} sessions, "
          f"{len(edges)} spawn edges, engines "
          f"{sorted({s['engine'] for s in FLEET})}")


if __name__ == "__main__":
    main()
