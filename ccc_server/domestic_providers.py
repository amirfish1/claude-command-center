import copy
import json
import re
from pathlib import Path

from ccc_server import byok
from ccc_server.domestic_presets import (
    CREDENTIAL_ENV_VARS, DOMESTIC_PRESETS, KEY_PROFILE, KEY_REGEX,
    MODEL_ENV_VARS, MODEL_PREFIX, ROUTING_MODE_ENV_VARS, VERIFIED_AT,
)

_PRESETS = {row["id"]: row for row in DOMESTIC_PRESETS}
_KEY_PATTERN = re.compile(KEY_REGEX)


class DomesticProviderError(ValueError):
    def __init__(self, message, code="byok_preset_unavailable"):
        super().__init__(message)
        self.code = code

    def as_payload(self):
        return {"ok": False, "error": str(self), "code": self.code}


def configured_presets():
    return next((set(p.get("providers", [])) for p in byok.byok_list_profiles()
                 if p.get("name") == KEY_PROFILE), set())


def catalog():
    configured = configured_presets()
    rows = []
    for preset in DOMESTIC_PRESETS:
        row = copy.deepcopy(preset)
        row.update({
            "key_regex": KEY_REGEX,
            "paid": True,
            "configured": row["id"] in configured,
            "verified_at": VERIFIED_AT,
            "model_ids": [f"{MODEL_PREFIX}{row['id']}/{model}" for model in row["models"]],
        })
        rows.append(row)
    return {"presets": rows, "backend": byok.byok_storage_backend()}


def model_records():
    configured = configured_presets()
    return [{
        "id": f"{MODEL_PREFIX}{p['id']}/{model}",
        "label": f"{p['name']} · {p['region']} · {model} (paid)",
        "provider": p["id"], "byok": True, "source": "byok-preset",
        "available": p["id"] in configured,
        "availability_reason": ("" if p["id"] in configured else
                                "Add your key in Settings > Free models > Use your own paid key."),
        "cost_summary": p["price_note"],
    } for p in DOMESTIC_PRESETS for model in p["models"]]


def add_to_model_catalog(catalog_data):
    from ccc_server import core
    for row in model_records():
        attrs = {key: value for key, value in row.items() if key != "id"}
        core._model_catalog_add(catalog_data, "claude", row["id"], **attrs)


def _invalidate_catalog():
    from ccc_server import core
    core._MODEL_CATALOG_CACHE.update({"ts": 0.0, "data": None})


def _preset(preset_id):
    if not isinstance(preset_id, str) or preset_id not in _PRESETS:
        raise DomesticProviderError("Choose one of the listed providers and regions.", "unknown_preset")
    return _PRESETS[preset_id]


def key_error(preset_id, key):
    preset = _preset(preset_id)
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key.strip()):
        return {"ok": False, "error": "Paste the full API key, without quotes or spaces.", "code": "key_format"}
    key = key.strip()
    if key.lower().startswith(("sk-ant-", "oauth-")):
        return {"ok": False, "error": "Use this provider's API key, not a Claude key or login token.",
                "code": "wrong_key_type"}
    if any(key.startswith(prefix) for prefix in preset.get("reject_key_prefixes", ())):
        return {"ok": False, "error": "This preset needs a pay-as-you-go Model Studio key. Coding Plan keys use a different endpoint.",
                "code": "wrong_key_type"}
    return None


def save_key(preset_id, key):
    try:
        preset = _preset(preset_id)
        invalid = key_error(preset_id, key)
    except DomesticProviderError as error:
        return error.as_payload()
    if invalid:
        return invalid
    try:
        saved = byok.byok_set_key(KEY_PROFILE, preset["id"], key.strip())
    except Exception:
        saved = False
    if not saved:
        return {"ok": False, "error": "The key could not be saved. Check access to your local key store and try again.",
                "code": "storage_error"}
    _invalidate_catalog()
    return {"ok": True, "validated": False, "message": "Key saved. Your first run will test it."}


def remove_key(preset_id):
    try:
        preset = _preset(preset_id)
    except DomesticProviderError as error:
        return error.as_payload()
    try:
        removed = byok.byok_delete_key(KEY_PROFILE, preset["id"])
    except Exception:
        removed = False
    if not removed:
        return {"ok": False, "error": "The key could not be removed. Try again.", "code": "storage_error"}
    _invalidate_catalog()
    return {"ok": True}


def resolve_model(model):
    if not isinstance(model, str) or not model.lower().startswith(MODEL_PREFIX):
        return None
    parts = model.split("/", 2)
    if len(parts) != 3:
        raise DomesticProviderError("Choose a listed paid model in the Claude model picker.", "unknown_preset_model")
    preset = _preset(parts[1])
    if parts[2] not in preset["models"]:
        raise DomesticProviderError("Choose a listed model for this provider and region.", "unknown_preset_model")
    return preset, parts[2]


def request_error(engine, model, runtime="", key_profile=None, remote=False):
    try:
        resolved = resolve_model(model)
        if not resolved:
            return None
        if engine != "claude":
            raise DomesticProviderError("These paid presets run with the Claude engine. Choose Claude first.")
        if runtime:
            raise DomesticProviderError("This model uses your paid API key. Turn off free $0 to run it.", "paid_preset_not_free")
        if key_profile not in (None, "", KEY_PROFILE):
            raise DomesticProviderError("These presets use the default key profile. Add the key in Settings > Free models.")
        if remote:
            raise DomesticProviderError("Paid model presets are available for local Claude sessions only.")
    except DomesticProviderError as error:
        return error.as_payload()
    return None


def _settings_paths(cwd):
    paths = [Path.home() / ".claude" / "settings.json",
             Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
             Path("/etc/claude-code/managed-settings.json")]
    if cwd:
        folder = Path(cwd).resolve()
        for parent in (folder, *folder.parents):
            paths.extend((parent / ".claude" / "settings.json", parent / ".claude" / "settings.local.json"))
            if (parent / ".git").exists():
                break
    return dict.fromkeys(paths)


def _check_settings(cwd, overlay):
    relevant = set(CREDENTIAL_ENV_VARS) | set(MODEL_ENV_VARS) | set(ROUTING_MODE_ENV_VARS) | {"ANTHROPIC_BASE_URL"}
    conflict = "Claude settings in this folder or account already override model routing. Remove the old routing entries, then try again."
    for settings_path in _settings_paths(cwd):
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            raise DomesticProviderError("Claude settings could not be read. Check their format and access before using a paid preset.", "routing_settings_conflict")
        if not isinstance(data, dict):
            raise DomesticProviderError(conflict, "routing_settings_conflict")
        env = data.get("env", {})
        if not isinstance(env, dict):
            raise DomesticProviderError(conflict, "routing_settings_conflict")
        if any(name in env and env[name] not in (None, "") and env[name] != overlay.get(name)
               for name in relevant):
            raise DomesticProviderError(conflict, "routing_settings_conflict")


def resolve_spawn(model, cwd=None):
    resolved = resolve_model(model)
    if not resolved:
        return None
    preset, raw_model = resolved
    try:
        key = byok.byok_get_key(KEY_PROFILE, preset["id"])
    except Exception:
        raise DomesticProviderError("Your saved key could not be read. Check access to your local key store.")
    if not key:
        raise DomesticProviderError("Add your key for this provider and region in Settings > Free models > Use your own paid key.", "preset_key_missing")
    invalid = key_error(preset["id"], key)
    if invalid:
        raise DomesticProviderError(invalid["error"], invalid["code"])
    overlay = {name: raw_model for name in MODEL_ENV_VARS}
    overlay.update({"ANTHROPIC_BASE_URL": preset["base_url"], "ANTHROPIC_AUTH_TOKEN": key,
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    _check_settings(cwd, overlay)
    return {"model": raw_model, "env": overlay, "qualified_model": model}


def apply_env(child_env, overlay):
    for name in (*CREDENTIAL_ENV_VARS, *ROUTING_MODE_ENV_VARS):
        child_env.pop(name, None)
    child_env.pop("CCC_SESSION_RUNTIME", None)
    child_env.update(overlay)
    return child_env


def handle(handler, method):
    if method == "GET":
        handler.send_json(catalog())
        return
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 16 * 1024:
            handler.send_json({"ok": False, "error": "Send a small JSON request."}, 400 if length <= 0 else 413)
            return
        payload = json.loads(handler.rfile.read(length))
    except (ValueError, OSError):
        handler.send_json({"ok": False, "error": "Send a valid JSON request."}, 400)
        return
    if not isinstance(payload, dict):
        handler.send_json({"ok": False, "error": "Send a JSON object."}, 400)
        return
    route = handler.path.split("?", 1)[0]
    if route == "/api/domestic-providers/keys":
        result = save_key(payload.get("preset"), payload.get("key"))
    elif route == "/api/domestic-providers/keys/remove":
        result = remove_key(payload.get("preset"))
    else:
        handler.send_json({"ok": False, "error": "Unknown preset action."}, 404)
        return
    handler.send_json(result, 200 if result.get("ok") else 400)
