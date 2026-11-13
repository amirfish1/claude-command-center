# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Default app templates and one-call app creation.

Each template is a folder under app_templates/ (app.json, index.html, sample
data, at least one routine). create() copies one into the user's apps folder,
fills in the label, icon and id, and returns the new app's id.
"""
import json
import re
import shutil
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app_templates"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TEXT_SUFFIXES = {".html", ".json", ".py", ".md", ".txt"}
_RESERVED = {"sessions", "queues", "morning"}


def _read_manifest(folder):
    try:
        data = json.loads((folder / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_templates(templates_dir=None):
    base = Path(templates_dir or TEMPLATES_DIR)
    out = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        m = _read_manifest(d) if d.is_dir() else None
        if m is None:
            continue
        out.append({
            "id": d.name,
            "label": str(m.get("label") or d.name),
            "icon": str(m.get("icon") or ""),
            "description": str(m.get("description") or ""),
            "routines": [str(r.get("name") or r.get("id")) for r in m.get("routines", [])
                         if isinstance(r, dict)],
        })
    return out


def _slug(label, taken):
    base = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")[:32] or "app"
    cand, n = base, 2
    while cand in taken or cand in _RESERVED:
        cand = f"{base}-{n}"[:40]
        n += 1
    return cand


def create(payload, apps_dir, taken=(), templates_dir=None):
    """Copy a template into apps_dir. Returns (result dict, http status)."""
    base = Path(templates_dir or TEMPLATES_DIR)
    tid = str(payload.get("template") or "blank")
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", tid) or not (base / tid / "app.json").is_file():
        return {"ok": False, "error": "Unknown template."}, 404
    src = base / tid
    manifest = _read_manifest(src)
    if manifest is None:
        return {"ok": False, "error": "Template is broken."}, 500
    label = str(payload.get("label") or "").strip()[:24] or str(manifest.get("label") or tid)
    icon = str(payload.get("icon") or "").strip()[:8] or str(manifest.get("icon") or "")
    apps_dir = Path(apps_dir)
    taken = set(taken)
    wanted = payload.get("id")
    if wanted:
        app_id = str(wanted)
        if not _ID_RE.match(app_id) or app_id in _RESERVED:
            return {"ok": False, "error": "App id: lowercase letters, digits, - and _."}, 400
        if app_id in taken or (apps_dir / app_id).exists():
            return {"ok": False, "error": "That app id is taken."}, 409
    else:
        app_id = _slug(label, taken | {p.name for p in apps_dir.iterdir()} if apps_dir.is_dir() else taken)
    dest = apps_dir / app_id
    apps_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "ccc-state.json"))
        manifest["label"], manifest["icon"] = label, icon
        manifest.pop("description", None)
        (dest / "app.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        for f in dest.rglob("*"):
            if f.is_file() and f.suffix == ".html":
                f.write_text(f.read_text(encoding="utf-8").replace("{{label}}", esc(label))
                             .replace("{{icon}}", esc(icon)).replace("{{app_id}}", app_id),
                             encoding="utf-8")
    except OSError as e:
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": f"Could not create the app: {e}"}, 500
    return {"ok": True, "id": app_id, "template": tid}, 200
