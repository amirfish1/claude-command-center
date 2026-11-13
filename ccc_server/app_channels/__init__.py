"""Outside-the-dashboard delivery for app routine alerts.

Config: ~/.claude/command-center/app-channels.json
    {"whatsapp": {"enabled": true, ...}, "imessage": {"enabled": true, ...}}
Nothing is enabled by default. Each channel module in this package exposes
NAME, configured(cfg) -> bool and send(cfg, title, body, url) -> (ok, err).
A failing channel never raises out of deliver().
"""
import importlib
import json
import os
from pathlib import Path

CHANNEL_MODULES = ("whatsapp", "imessage")


def config_path():
    return Path(os.environ.get("CCC_APP_CHANNELS_FILE")
                or Path.home() / ".claude" / "command-center" / "app-channels.json")


def load_config():
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _modules():
    for name in CHANNEL_MODULES:
        try:
            yield importlib.import_module(f"{__name__}.{name}")
        except ImportError:
            continue  # channel not installed on this machine


def deliver(title, body="", url=""):
    """Send to every enabled, configured channel. Returns {channel: (ok, err)}."""
    cfg_all = load_config()
    results = {}
    for mod in _modules():
        cfg = cfg_all.get(mod.NAME)
        if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
            continue
        try:
            if not mod.configured(cfg):
                results[mod.NAME] = (False, "not configured")
                continue
            results[mod.NAME] = tuple(mod.send(cfg, str(title or ""), str(body or ""), str(url or "")))
        except Exception as e:  # a channel must never break a routine
            results[mod.NAME] = (False, f"{type(e).__name__}: {e}"[:200])
    return results
