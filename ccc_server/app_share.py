# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Sharing an app: install one from a folder or a git repository, and
optionally lock CCC's navigation to it ("focus").

Focus is two keys in custom-links.json:
  focus_app   the app the rail is locked to right now ("" or absent = unlocked)
  focus_home  the app to offer locking back to after an unlock

Nothing is hidden behind a permission: focus only decides what the rail shows
and where a bare `/` lands. Every page still answers at its own URL.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

APPS_DIR = COMMAND_CENTER_STATE_DIR / "apps"
CONFIG_PATH = COMMAND_CENTER_STATE_DIR / "custom-links.json"
FOCUS_KEY = "focus_app"
HOME_KEY = "focus_home"
CLONE_TIMEOUT_SECONDS = 120

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_RESERVED = {"sessions", "queues", "morning", "apps"}
_SHORTHAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")
# Never carried into an install: another machine's state, caches, history.
_SKIP = shutil.ignore_patterns(
    ".git", ".venv", ".tools", "__pycache__", "node_modules", "ccc-state.json", ".claude", ".DS_Store")


def focus_state(cfg, apps):
    """What the rail needs: the locked app (if any) and the one to offer."""
    cfg = cfg if isinstance(cfg, dict) else {}
    by_id = {a["id"]: a for a in apps if isinstance(a, dict) and a.get("id")}

    def known(key):
        value = cfg.get(key)
        return value if isinstance(value, str) and value in by_id else ""

    app, home = known(FOCUS_KEY), known(HOME_KEY)
    home = home or app
    out = {"app": app, "home": home}
    target = by_id.get(app or home)
    if target:
        out["url"] = target.get("nav") or target.get("url") or ""
        out["label"] = target.get("label") or target["id"]
        out["icon"] = target.get("icon") or ""
    return out


def set_focus(cfg, app_id, apps, forget=False):
    """Mutate cfg. An empty app_id unlocks; forget also drops the offer."""
    app_id = str(app_id or "").strip()
    if not app_id:
        cfg.pop(FOCUS_KEY, None)
        if forget:
            cfg.pop(HOME_KEY, None)
        return {"ok": True, "focus": focus_state(cfg, apps)}, 200
    if app_id not in {a.get("id") for a in apps}:
        return {"ok": False, "error": "unknown app"}, 404
    cfg[FOCUS_KEY] = cfg[HOME_KEY] = app_id
    return {"ok": True, "focus": focus_state(cfg, apps)}, 200


def _read_manifest(folder):
    try:
        data = json.loads((Path(folder) / "app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _find_app(root):
    """The app folder inside a source: the root itself, or its only child app."""
    root = Path(root)
    if _read_manifest(root) is not None:
        return root, None
    found = [d for d in sorted(root.iterdir())
             if d.is_dir() and not d.name.startswith(".") and _read_manifest(d) is not None]
    if len(found) == 1:
        return found[0], found[0].name
    if not found:
        return None, "No app.json found there."
    return None, "More than one app found there: " + ", ".join(d.name for d in found)


def _default_id(name):
    base = re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-")
    base = re.sub(r"^ccc-", "", base)
    return base[:40]


def source_url(source):
    """A git URL for `owner/repo` shorthand or a URL; None for a local path."""
    source = str(source or "").strip()
    if re.match(r"^(https?://|git@|ssh://)", source):
        return source
    if _SHORTHAND_RE.match(source) and not Path(source).expanduser().exists():
        return f"https://github.com/{source}.git"
    return None


def install(source, apps_dir=None, app_id=None):
    """Copy an app from a folder or git repository into the apps folder.

    Returns (result, http-style status). An installed app belongs to its
    owner: the agent reshapes it, so an existing app is never overwritten.
    """
    apps_dir = Path(apps_dir or APPS_DIR)
    source = str(source or "").strip()
    if not source:
        return {"ok": False, "error": "Say where the app is: a folder, a git URL or owner/repo."}, 400
    url = source_url(source)
    tmp = None
    try:
        if url:
            tmp = tempfile.mkdtemp(prefix="ccc-app-")
            try:
                proc = subprocess.run(
                    ["git", "clone", "--depth", "1", "--quiet", url, tmp + "/src"],
                    capture_output=True, text=True, timeout=CLONE_TIMEOUT_SECONDS,
                    stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired) as e:
                return {"ok": False, "error": f"Could not download the app: {e}"}, 502
            if proc.returncode != 0:
                tail = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
                return {"ok": False,
                        "error": "Could not download the app: " + (tail[-1] if tail else "git failed")}, 502
            root = Path(tmp) / "src"
            fallback_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        else:
            root = Path(source).expanduser().resolve()
            if not root.is_dir():
                return {"ok": False, "error": f"No such folder: {source}"}, 404
            fallback_name = root.name
        folder, hint = _find_app(root)
        if folder is None:
            return {"ok": False, "error": hint}, 400
        app_id = str(app_id or "").strip() or _default_id(hint or fallback_name)
        if not _ID_RE.match(app_id) or app_id in _RESERVED:
            return {"ok": False,
                    "error": f"'{app_id}' cannot be an app id (lowercase letters, digits, - and _). "
                             "Pick one with --id."}, 400
        dest = apps_dir / app_id
        if dest.exists():
            return {"ok": False, "error": f"An app called '{app_id}' is already installed. "
                                          "Remove it first, or install under another name with --id."}, 409
        manifest = _read_manifest(folder) or {}
        apps_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(folder, dest, ignore=_SKIP)
        except OSError as e:
            shutil.rmtree(dest, ignore_errors=True)
            return {"ok": False, "error": f"Could not copy the app: {e}"}, 500
        routines = manifest.get("routines") if isinstance(manifest.get("routines"), list) else []
        return {"ok": True, "id": app_id, "label": str(manifest.get("label") or app_id),
                "url": f"/view/{app_id}", "dir": str(dest),
                "setup": [r.get("run") for r in routines
                          if isinstance(r, dict) and r.get("setup") and isinstance(r.get("run"), list)]}, 200
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def write_focus_offline(app_id, config_path=None, forget=False):
    """Set focus straight in the config file, for when no server is running."""
    path = Path(config_path or CONFIG_PATH)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {"links": cfg}
    except (OSError, ValueError):
        cfg = {}
    if app_id:
        cfg[FOCUS_KEY] = cfg[HOME_KEY] = app_id
    else:
        cfg.pop(FOCUS_KEY, None)
        if forget:
            cfg.pop(HOME_KEY, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
