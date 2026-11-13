# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""The Concierge: turn one sentence into a new app with a builder agent.

POST /api/apps/concierge {wish} makes a blank app folder (app.json plus a
page that loads the app kit), starts the app's builder agent and sends it a
setup brief. The agent asks at most three short questions, builds the page,
proposes routines and tests each once.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from ccc_server import app_routines

WISH_LIMIT = 600
_STOP = {"a", "an", "the", "to", "for", "my", "me", "i", "want", "need", "app",
         "that", "and", "of", "with", "of", "keep", "track", "help", "make",
         "build", "create", "please", "can", "you", "on", "in", "about", "so"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{label}</title>
<style>
body{{margin:0;font:16px/1.5 system-ui,sans-serif;background:var(--k-bg,#0f1115);color:var(--k-text,#edf0f5)}}
main{{max-width:760px;margin:0 auto;padding:40px 24px}}
.hero{{font-size:56px}}
p{{color:var(--k-muted,#a8b2c2)}}
</style></head><body>
<main data-ccc-content>
<div class="hero">{icon}</div>
<h1>{label}</h1>
<p>Your agent is setting this app up. Answer its questions on the right.</p>
</main>
<script src="/static/app-kit.js" data-app="{app_id}" defer></script>
</body></html>
"""


def slugify(wish, existing=lambda s: False):
    words = [w for w in re.findall(r"[a-z0-9]+", wish.lower()) if w not in _STOP]
    base = "-".join(words[:3])[:30].strip("-") or "app"
    if not base[0].isalnum():
        base = "app"
    slug, n = base, 2
    while existing(slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _label(wish, slug):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", wish) if w.lower() not in _STOP][:3]
    return " ".join(w.capitalize() for w in words) or slug


def _icon(wish):
    w = wish.lower()
    for keys, icon in ((("ski", "snow"), "⛷"), (("trip", "travel", "flight"), "🧳"),
                       (("money", "budget", "bill", "expense"), "💰"),
                       (("workout", "gym", "run", "fitness"), "🏃"),
                       (("food", "meal", "recipe"), "🍲"), (("read", "book"), "📚"),
                       (("plant", "garden"), "🌱"), (("job", "career"), "💼")):
        if any(k in w for k in keys):
            return icon
    return "✨"


def create_app(wish, apps_dir=None):
    """Make a blank app folder. Returns (app_id, label)."""
    base = Path(apps_dir or app_routines.APPS_DIR)
    base.mkdir(parents=True, exist_ok=True)
    slug = slugify(wish, lambda s: (base / s).exists())
    label, icon = _label(wish, slug), _icon(wish)
    d = base / slug
    d.mkdir()
    (d / "app.json").write_text(
        json.dumps({"label": label, "icon": icon, "wish": wish, "routines": []}, indent=2),
        encoding="utf-8")
    (d / "index.html").write_text(
        PAGE.format(label=label, icon=icon, app_id=slug), encoding="utf-8")
    return slug, label


def brief(wish, label, docs_path=""):
    docs = f" Read {docs_path}, section \"An app with an agent and routines\", first." if docs_path else ""
    return (
        f'The owner just asked for a new app: "{wish}".{docs}\n'
        "You are setting it up from scratch. Do this in order:\n"
        "1. Ask the owner at most 3 short questions, one message, only what you truly need "
        "(what to track, how often, what should alert them). Then wait for the answers.\n"
        "2. Pick the closest starting point and build the page in index.html "
        "(keep the app-kit script tag and data-ccc-content). Make it visual and friendly; "
        "it must look right in light and dark themes.\n"
        "3. Propose routines in app.json, each with a friendly persona name and an icon "
        "(for example \"Price Watch\" 🔎). Use a script routine for mechanical checks, an agent "
        "routine when it needs judgement. Add them once the owner agrees.\n"
        "4. Run each routine once to test it, fix what breaks.\n"
        f'5. Finish with two lines telling the owner what "{label}" does and when it next runs.\n'
        "Never put personal data in the CCC repo; everything stays in this folder.")


def _inject(port, sid, text):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/inject-input",
        data=json.dumps({"session_id": sid, "text": text}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def concierge(payload, port, apps_dir=None, docs_path="", inject=_inject, sleep=time.sleep):
    wish = str((payload or {}).get("wish") or "").strip()
    if not wish:
        return {"ok": False, "error": "Tell me what you want an app for."}, 400
    wish = wish[:WISH_LIMIT]
    app_id, label = create_app(wish, apps_dir)
    res, status = app_routines.ensure_agent(app_id, port, apps_dir=apps_dir, docs_path=docs_path)
    if not res.get("ok"):
        return {"ok": False, "app": app_id, "error": res.get("error", "could not start the agent")}, status
    sid, text, sent = res["session_id"], brief(wish, label, docs_path), {}
    for _ in range(5):
        sent = inject(port, sid, text)
        if sent.get("ok") is not False:
            break
        sleep(2)
    return {"ok": True, "app": app_id, "label": label, "session_id": sid,
            "url": f"/view/{app_id}", "briefed": sent.get("ok") is not False}, 200
