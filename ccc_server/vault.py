# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Vault: local storage for any kind of secret (API keys, tokens, logins).

BYOK (ccc_server/byok.py) is scoped to LLM providers and spawn-time env
injection. The Vault holds everything else — a Meta app token, a Stripe key,
a website login — under a name the user picks.

Storage:
- Values: macOS Keychain, service ``ccc-vault``, account = entry name. Off
  Darwin (or without ``security``), an encrypted file
  ``~/.claude/command-center/vault/secrets.enc.json`` using the same
  stdlib cipher as BYOK (ccc_server/secret_store.py).
- Metadata: ``~/.claude/command-center/vault/index.json`` (0600) — name,
  kind, service, username, env var, website, notes, timestamps, and for long
  api_key/token values a last-4 hint. Never the value itself.

Read access to values is local-process only: the HTTP API is write-only
(server.py never returns a value), and the ``ccc vault`` CLI reads the store
directly. This module imports neither ``server`` nor ``_core`` at import
time so the CLI can use it without a running server.

Every module-level name is ``vault_``/``_vault_``-prefixed because
server.py adopts this module's globals (``_adopt_ccc_module``) next to
byok's; unprefixed helpers like ``_load_index`` would shadow BYOK's.
"""

from __future__ import annotations

import json
import re
import threading
import time

from ccc_server import paths as _vault_paths
from ccc_server import secret_store as _vault_secret_store

VAULT_KEYCHAIN_SERVICE = "ccc-vault"
VAULT_KINDS = {
    "api_key": "API key",
    "token": "Token",
    "login": "Login",
    "other": "Other",
}
VAULT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VAULT_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
VAULT_MAX_VALUE_BYTES = 64 * 1024
# Free-text metadata caps (chars). Generous, but bounded so the index file
# and the Settings list stay sane.
_VAULT_TEXT_LIMITS = {
    "service": 120,
    "username": 200,
    "website": 500,
    "notes": 2000,
}
# The last-4 hint is only kept for long machine-generated secrets; for a
# short password or PIN four characters is a real chunk of the secret.
_VAULT_HINT_KINDS = ("api_key", "token")
_VAULT_HINT_MIN_LEN = 16

_VAULT_LOCK = threading.RLock()


class VaultError(ValueError):
    """User-facing validation/storage error (message is safe to show)."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _vault_state_dir():
    return _vault_paths.COMMAND_CENTER_STATE_DIR / "vault"


def _vault_index_path():
    return _vault_state_dir() / "index.json"


def _vault_file_store_path():
    return _vault_state_dir() / "secrets.enc.json"


def _vault_seed_path():
    return _vault_state_dir() / ".secret"


# ---------------------------------------------------------------------------
# Secret backend
# ---------------------------------------------------------------------------

def _vault_keychain_available():
    return _vault_secret_store.keychain_available()


def vault_storage_backend():
    return "keychain" if _vault_keychain_available() else "encrypted-file"


def _vault_file_key():
    seed = _vault_secret_store.load_or_create_seed(_vault_seed_path())
    return _vault_secret_store.derive_key(seed, b"ccc-vault-v1")


def _vault_secret_put(name, value):
    if _vault_keychain_available():
        return _vault_secret_store.keychain_set(VAULT_KEYCHAIN_SERVICE, name, value)
    key = _vault_file_key()
    data = _vault_secret_store.load_encrypted_json(_vault_file_store_path(), key)
    data[name] = value
    _vault_secret_store.save_encrypted_json(_vault_file_store_path(), key, data)
    return True


def _vault_secret_get(name):
    if _vault_keychain_available():
        return _vault_secret_store.keychain_get(VAULT_KEYCHAIN_SERVICE, name)
    data = _vault_secret_store.load_encrypted_json(_vault_file_store_path(), _vault_file_key())
    value = data.get(name)
    return value if isinstance(value, str) else None


def _vault_secret_delete(name):
    if _vault_keychain_available():
        _vault_secret_store.keychain_delete(VAULT_KEYCHAIN_SERVICE, name)
        return
    path = _vault_file_store_path()
    if not path.exists():
        return
    key = _vault_file_key()
    data = _vault_secret_store.load_encrypted_json(path, key)
    if name in data:
        data.pop(name)
        _vault_secret_store.save_encrypted_json(path, key, data)


# ---------------------------------------------------------------------------
# Metadata index
# ---------------------------------------------------------------------------

def _vault_load_index():
    try:
        data = json.loads(_vault_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return data


def _vault_save_index(idx):
    _vault_secret_store.ensure_private_dir(_vault_state_dir())
    _vault_secret_store.write_private_text(
        _vault_index_path(), json.dumps(idx, indent=2, sort_keys=True),
    )


def _vault_public(entry):
    """Copy of an index entry safe for the API/CLI (index never holds values,
    this is defence in depth against a future field slipping in)."""
    allowed = (
        "name", "kind", "service", "username", "env_var", "website", "notes",
        "created_at", "updated_at", "value_updated_at", "hint", "source",
    )
    out = {k: entry.get(k) for k in allowed if k in entry}
    out["saved"] = True
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def vault_normalize_name(name):
    return (name or "").strip().lower()


def _vault_validate_name(name):
    name = vault_normalize_name(name)
    if not VAULT_NAME_RE.match(name):
        raise VaultError(
            "name must be 1-64 chars of a-z, 0-9, '.', '_' or '-', starting with a letter or digit"
        )
    return name


def _vault_clean_meta(fields, *, partial):
    """Validated metadata subset of ``fields``. With partial=True only keys
    present in ``fields`` are returned (an update), else defaults fill in."""
    out = {}
    if "kind" in fields or not partial:
        kind = (fields.get("kind") or "other").strip().lower()
        if kind not in VAULT_KINDS:
            raise VaultError("kind must be one of: " + ", ".join(VAULT_KINDS))
        out["kind"] = kind
    for key, limit in _VAULT_TEXT_LIMITS.items():
        if key in fields or not partial:
            text = fields.get(key)
            text = "" if text is None else str(text).strip()
            if len(text) > limit:
                raise VaultError(f"{key} is too long (max {limit} characters)")
            out[key] = text
    if "env_var" in fields or not partial:
        env_var = (fields.get("env_var") or "").strip()
        if env_var and not VAULT_ENV_VAR_RE.match(env_var):
            raise VaultError("env var must look like MY_API_KEY (letters, digits, underscore)")
        out["env_var"] = env_var
    return out


def _vault_clean_value(value):
    if not isinstance(value, str):
        raise VaultError("value is required")
    # Trim surrounding whitespace/newlines (paste artefacts); a secret whose
    # meaning depends on edge whitespace is not a case worth the footgun.
    value = value.strip()
    if not value:
        raise VaultError("value is required")
    if len(value.encode("utf-8")) > VAULT_MAX_VALUE_BYTES:
        raise VaultError("value is too large (max 64 KB)")
    return value


def _vault_hint(kind, value):
    if kind in _VAULT_HINT_KINDS and len(value) >= _VAULT_HINT_MIN_LEN:
        return value[-4:]
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vault_list_entries():
    """Metadata for every entry, sorted by name. Never values."""
    idx = _vault_load_index()
    return [_vault_public(idx["entries"][name]) for name in sorted(idx["entries"])]


def vault_get_entry(name):
    entry = _vault_load_index()["entries"].get(vault_normalize_name(name))
    return _vault_public(entry) if entry else None


def vault_create_entry(name, value, *, source=None, **fields):
    """Create a new entry. Raises VaultError on bad input, a duplicate name,
    or a storage failure. Returns the public metadata."""
    name = _vault_validate_name(name)
    meta = _vault_clean_meta(fields, partial=False)
    value = _vault_clean_value(value)
    with _VAULT_LOCK:
        idx = _vault_load_index()
        if name in idx["entries"]:
            raise VaultError(f"an entry named '{name}' already exists")
        if not _vault_secret_put(name, value):
            raise VaultError("could not write the secret to " + vault_storage_backend())
        now = time.time()
        entry = {"name": name, **meta, "created_at": now, "updated_at": now,
                 "value_updated_at": now, "hint": _vault_hint(meta["kind"], value)}
        if isinstance(source, dict) and source:
            entry["source"] = {str(k): str(v) for k, v in source.items()}
        idx["entries"][name] = entry
        _vault_save_index(idx)
    return _vault_public(entry)


def vault_update_entry(name, *, value=None, **fields):
    """Update metadata and/or replace the value of an existing entry."""
    name = vault_normalize_name(name)
    meta = _vault_clean_meta(fields, partial=True)
    if value is not None and (not isinstance(value, str) or value.strip() == ""):
        value = None  # blank "replace value" box == keep the current value
    if value is not None:
        value = _vault_clean_value(value)
    with _VAULT_LOCK:
        idx = _vault_load_index()
        entry = idx["entries"].get(name)
        if entry is None:
            raise VaultError(f"no entry named '{name}'")
        now = time.time()
        if value is not None:
            if not _vault_secret_put(name, value):
                raise VaultError("could not write the secret to " + vault_storage_backend())
            entry["value_updated_at"] = now
            entry["hint"] = _vault_hint(meta.get("kind", entry.get("kind")), value)
        elif "kind" in meta and meta["kind"] not in _VAULT_HINT_KINDS:
            # Switching to login/other drops a hint we'd no longer keep.
            entry["hint"] = ""
        entry.update(meta)
        entry["updated_at"] = now
        _vault_save_index(idx)
    return _vault_public(entry)


def vault_delete_entry(name):
    name = vault_normalize_name(name)
    with _VAULT_LOCK:
        idx = _vault_load_index()
        existed = idx["entries"].pop(name, None) is not None
        _vault_secret_delete(name)
        if existed:
            _vault_save_index(idx)
    return existed


def vault_get_value(name):
    """The secret value, or None. Local callers only (CLI exec/get --reveal,
    BYOK import); server.py must never put this in an HTTP response."""
    name = vault_normalize_name(name)
    if name not in _vault_load_index()["entries"]:
        return None
    return _vault_secret_get(name)


# ---------------------------------------------------------------------------
# BYOK bridge: list existing BYOK keys, copy one into the Vault
# ---------------------------------------------------------------------------

def _vault_byok():
    from ccc_server import byok  # lazy: byok needs a server/_core context
    return byok


def vault_list_byok():
    """Every BYOK key as a read-only row, plus which vault entry (if any) it
    was imported into. Names only — never key material."""
    byok = _vault_byok()
    imported = {}
    for entry in _vault_load_index()["entries"].values():
        src = entry.get("source") or {}
        if src.get("type") == "byok":
            imported.setdefault((src.get("profile"), src.get("provider")), []).append(entry["name"])
    rows = []
    for profile in byok.byok_list_profiles():
        for provider in profile.get("providers") or []:
            info = byok.BYOK_PROVIDERS.get(provider, {})
            rows.append({
                "profile": profile["name"],
                "provider": provider,
                "provider_label": info.get("label") or provider,
                "env_vars": list(info.get("env_vars") or []),
                "imported_as": sorted(imported.get((profile["name"], provider), [])),
            })
    return rows


def vault_import_byok(profile, provider, name, **fields):
    """Copy one BYOK key into a new vault entry. The BYOK entry is left in
    place (BYOK still injects it into engine spawns)."""
    byok = _vault_byok()
    profile = (profile or "").strip()
    provider = (provider or "").strip().lower()
    if not profile or provider not in byok.BYOK_PROVIDERS:
        raise VaultError("profile and a known BYOK provider are required")
    if provider not in {p for row in byok.byok_list_profiles() if row["name"] == profile
                        for p in row.get("providers") or []}:
        raise VaultError(f"no BYOK key for {profile}/{provider}")
    value = byok.byok_get_key(profile, provider)
    if not value:
        raise VaultError(f"could not read the BYOK key for {profile}/{provider}")
    return vault_create_entry(
        name, value, source={"type": "byok", "profile": profile, "provider": provider}, **fields,
    )
