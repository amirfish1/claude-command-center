"""Local launch preferences; subscription details never belong in the checkout."""

from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
import threading

_LOCK = threading.Lock()
EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}


def empty_config():
    return {"version": 1, "enabled": False,
            "rankings": {"routine": [], "deep": []}, "resources": {}}


def validate(value, engines):
    if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
        raise ValueError("enabled must be true or false")
    rankings = value.get("rankings", {})
    resources = value.get("resources", {})
    if not isinstance(rankings, dict) or set(rankings) - {"routine", "deep"}:
        raise ValueError("rankings must contain routine and deep model lists")
    if not isinstance(resources, dict) or set(resources) - set(engines):
        raise ValueError("resources must use supported engine names")
    result = empty_config()
    result["enabled"] = value["enabled"]

    def string(item, key, limit=300):
        text = item.get(key, "")
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError(f"{key} must be text of at most {limit} characters")
        return text.strip()

    def number(item, key, maximum):
        n = item.get(key)
        if n is None or n == "":
            return None
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n <= maximum:
            raise ValueError(f"{key} must be between 0 and {maximum}")
        return n

    def stamp(item, key):
        text = string(item, key, 64)
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError(f"{key} must include a timezone") from None
        return parsed.isoformat()

    for depth in ("routine", "deep"):
        rows = rankings.get(depth, [])
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("Each ranking must contain at most 32 choices")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each choice must name an engine, model and effort")
            engine, model, effort = string(row, "engine", 40), string(row, "model"), string(row, "effort", 16)
            if engine not in engines or not model or effort not in EFFORTS:
                raise ValueError("Unsupported engine, empty model, or invalid effort")
            key = (engine, model, effort)
            if key in seen:
                raise ValueError("Duplicate choice in ranking")
            seen.add(key)
            result["rankings"][depth].append({"engine": engine, "model": model, "effort": effort,
                                               "reason": string(row, "reason")})
    for engine, item in resources.items():
        if not isinstance(item, dict):
            raise ValueError("Each resource must be an object")
        kind = string(item, "kind", 20) or "subscription"
        priority = string(item, "priority", 16) or "normal"
        if kind not in {"subscription", "credits", "metered"} or priority not in {"normal", "early", "soon"}:
            raise ValueError("Invalid funding kind or usage priority")
        result["resources"][engine] = {
            "label": string(item, "label", 120), "kind": kind, "priority": priority,
            "remaining_pct": number(item, "remaining_pct", 100),
            "balance_usd": number(item, "balance_usd", 1_000_000_000),
            "observed_at": stamp(item, "observed_at"), "resets_at": stamp(item, "resets_at"),
            "expires_at": stamp(item, "expires_at"), "reset_note": string(item, "reset_note"),
            "other_allowances": string(item, "other_allowances", 600),
        }
    return result


def load(state_dir, engines):
    target = Path(state_dir) / "spawn-advisor.json"
    try:
        return validate(json.loads(target.read_text(encoding="utf-8")), engines)
    except FileNotFoundError:
        return empty_config()


def save(state_dir, value, engines):
    config = validate(value, engines)
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd, temporary = tempfile.mkstemp(prefix=".spawn-advisor-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(config, stream, indent=2, allow_nan=False)
                stream.write("\n")
            os.replace(temporary, directory / "spawn-advisor.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return config
