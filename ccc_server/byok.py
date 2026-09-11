# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""BYOK (bring-your-own-key) profile storage, provider catalog, spawn-time
env injection, and a lightweight usage/cost ledger.

Secrets live in the macOS Keychain (`security` CLI, service "ccc-byok",
one account per "<profile>:<provider>") when available. Off Darwin, or when
the `security` binary is missing, keys fall back to a locally-encrypted
JSON file under ``~/.claude/command-center/byok/``. That fallback cipher is
a from-stdlib HMAC-SHA256 counter-mode stream (PBKDF2-derived key, no
plaintext ever hits disk) — sturdy against casual disclosure, but it is a
fallback, not an audited primitive; the Keychain is the real security
boundary on the platform CCC ships for. A small unencrypted index file
(profile -> [providers]) is kept alongside so profile/provider names can be
listed without touching either secret store.

Names still living in server.py are reached via ``_core`` at call time,
same convention as every other ccc_server module."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import shutil
import subprocess
import threading
import time

from ccc_server import core as _core

_KEYCHAIN_SERVICE = "ccc-byok"
_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Provider catalog
# ---------------------------------------------------------------------------

BYOK_PROVIDERS = {
    "anthropic": {"label": "Anthropic", "env_vars": ["ANTHROPIC_API_KEY"], "key_hint": "sk-ant-..."},
    "openai": {"label": "OpenAI", "env_vars": ["OPENAI_API_KEY"], "key_hint": "sk-..."},
    "openrouter": {"label": "OpenRouter", "env_vars": ["OPENROUTER_API_KEY"], "key_hint": "sk-or-..."},
    "tokenrouter": {"label": "TokenRouter", "env_vars": ["TOKENROUTER_API_KEY"], "key_hint": "tr-..."},
    "xai": {"label": "xAI", "env_vars": ["XAI_API_KEY"], "key_hint": "xai-..."},
    "moonshot": {"label": "Moonshot", "env_vars": ["MOONSHOT_API_KEY"], "key_hint": "sk-..."},
    "google": {"label": "Google", "env_vars": ["GOOGLE_API_KEY", "GEMINI_API_KEY"], "key_hint": "AIza..."},
}

# Engines whose CLIs read provider API keys straight from the environment
# (per each CLI's own documented auth env vars). CCC injects a profile's
# keys into the spawned subprocess's env for these; other engines keep
# using whatever local CLI auth they already have (unchanged behavior).
BYOK_DIRECT_ENV_ENGINES = ("opencode", "droid", "kilo", "hermes", "aider", "pi")

# model-id prefix -> provider id. OpenCode (and Droid, once wired) accept
# "<provider>/<vendor>/<model>" ids natively — e.g.
# "openrouter/anthropic/claude-sonnet-5" — so routing a BYOK model through
# them needs no engine override, only the right API key in the environment.
_VIRTUAL_MODEL_PREFIXES = {
    "openrouter/": "openrouter",
    "tokenrouter/": "tokenrouter",
}

# Indicative per-1M-token USD pricing for models only reachable via a BYOK
# provider (no vendor CLI subscription backs these through CCC). These are
# ballpark figures for the cost estimator and the model picker's cost
# column — treat them as directional, not billing-accurate; refresh from
# the provider's own pricing page before trusting them for real spend
# tracking.
BYOK_MODEL_CATALOG = (
    {"id": "openrouter/anthropic/claude-sonnet-5", "label": "Claude Sonnet 5 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 3.0, "cost_out_per_1m": 15.0},
    {"id": "openrouter/anthropic/claude-opus-5", "label": "Claude Opus 5 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 15.0, "cost_out_per_1m": 75.0},
    {"id": "openrouter/anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 1.0, "cost_out_per_1m": 5.0},
    {"id": "openrouter/openai/gpt-5.4", "label": "GPT-5.4 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 2.5, "cost_out_per_1m": 10.0},
    {"id": "openrouter/x-ai/grok-4.6", "label": "Grok 4.6 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 3.0, "cost_out_per_1m": 15.0},
    {"id": "openrouter/moonshotai/kimi-k3", "label": "Kimi K3 (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 0.6, "cost_out_per_1m": 2.5},
    {"id": "openrouter/google/gemini-3-flash-preview", "label": "Gemini 3 Flash (OpenRouter)", "provider": "openrouter", "cost_in_per_1m": 0.3, "cost_out_per_1m": 1.2},
    {"id": "tokenrouter/anthropic/claude-sonnet-5", "label": "Claude Sonnet 5 (TokenRouter)", "provider": "tokenrouter", "cost_in_per_1m": 3.0, "cost_out_per_1m": 15.0},
    {"id": "tokenrouter/openai/gpt-5.4", "label": "GPT-5.4 (TokenRouter)", "provider": "tokenrouter", "cost_in_per_1m": 2.5, "cost_out_per_1m": 10.0},
)
_BYOK_MODEL_INDEX = {row["id"].lower(): row for row in BYOK_MODEL_CATALOG}


def byok_resolve_virtual_provider(model):
    """Provider id for a "<provider>/..." model string, else None."""
    model = (model or "").strip().lower()
    for prefix, provider in _VIRTUAL_MODEL_PREFIXES.items():
        if model.startswith(prefix):
            return provider
    return None


# ---------------------------------------------------------------------------
# State paths
# ---------------------------------------------------------------------------

def _state_dir():
    return _core.COMMAND_CENTER_STATE_DIR / "byok"


def _index_path():
    return _state_dir() / "index.json"


def _file_store_path():
    return _state_dir() / "profiles.enc.json"


def _secret_seed_path():
    return _state_dir() / ".secret"


def _usage_log_path():
    return _state_dir() / "usage.jsonl"


# ---------------------------------------------------------------------------
# Encrypted-file fallback (used only when the macOS Keychain isn't available)
# ---------------------------------------------------------------------------

def _machine_secret():
    try:
        return _secret_seed_path().read_bytes()
    except OSError:
        pass
    secret = os.urandom(32)
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        _secret_seed_path().write_bytes(secret)
        os.chmod(_secret_seed_path(), 0o600)
    except OSError:
        pass
    return secret


def _derive_key(nonce_context=b"ccc-byok-v1"):
    return hashlib.pbkdf2_hmac("sha256", _machine_secret(), nonce_context, 100_000, dklen=32)


def _keystream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _encrypt(plaintext: bytes) -> dict:
    key = _derive_key()
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext))))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).hexdigest()
    return {"nonce": nonce.hex(), "cipher": cipher.hex(), "mac": mac}


def _decrypt(blob: dict):
    try:
        key = _derive_key()
        nonce = bytes.fromhex(blob["nonce"])
        cipher = bytes.fromhex(blob["cipher"])
        expect_mac = hmac.new(key, nonce + cipher, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect_mac, blob.get("mac", "")):
            return None
        return bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher))))
    except (KeyError, ValueError, TypeError):
        return None


def _load_file_store():
    try:
        blob = json.loads(_file_store_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    plain = _decrypt(blob)
    if plain is None:
        return {}
    try:
        data = json.loads(plain.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_file_store(data):
    _state_dir().mkdir(parents=True, exist_ok=True)
    blob = _encrypt(json.dumps(data).encode("utf-8"))
    tmp = _file_store_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_file_store_path())


# ---------------------------------------------------------------------------
# macOS Keychain backend
# ---------------------------------------------------------------------------

def _keychain_available():
    return platform.system() == "Darwin" and shutil.which("security") is not None


def _keychain_account(profile, provider):
    return f"{profile}:{provider}"


def _keychain_set(profile, provider, secret):
    account = _keychain_account(profile, provider)
    subprocess.run(
        ["security", "delete-generic-password", "-a", account, "-s", _KEYCHAIN_SERVICE],
        capture_output=True, timeout=5,
    )
    try:
        proc = subprocess.run(
            ["security", "add-generic-password", "-a", account, "-s", _KEYCHAIN_SERVICE, "-w", secret, "-U"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _keychain_get(profile, provider):
    account = _keychain_account(profile, provider)
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _keychain_delete(profile, provider):
    account = _keychain_account(profile, provider)
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", _KEYCHAIN_SERVICE],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def byok_storage_backend():
    return "keychain" if _keychain_available() else "encrypted-file"


# ---------------------------------------------------------------------------
# Index (profile -> [providers]; names only, never secret material)
# ---------------------------------------------------------------------------

def _load_index():
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(idx):
    _state_dir().mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_index_path())


# ---------------------------------------------------------------------------
# Public profile API
# ---------------------------------------------------------------------------

def byok_list_profiles():
    """[{"name", "providers": [...]}] — names and providers only, no keys."""
    idx = _load_index()
    return [
        {"name": name, "providers": sorted(providers)}
        for name, providers in sorted(idx.items())
    ]


def byok_set_key(profile, provider, secret):
    profile = (profile or "").strip()
    provider = (provider or "").strip().lower()
    secret = (secret or "").strip()
    if not profile or provider not in BYOK_PROVIDERS or not secret:
        return False
    with _LOCK:
        if _keychain_available():
            ok = _keychain_set(profile, provider, secret)
        else:
            data = _load_file_store()
            data.setdefault(profile, {})[provider] = secret
            _save_file_store(data)
            ok = True
        if ok:
            idx = _load_index()
            providers = idx.setdefault(profile, [])
            if provider not in providers:
                providers.append(provider)
            _save_index(idx)
    return ok


def byok_get_key(profile, provider):
    profile = (profile or "").strip()
    provider = (provider or "").strip().lower()
    if not profile or provider not in BYOK_PROVIDERS:
        return None
    with _LOCK:
        if _keychain_available():
            return _keychain_get(profile, provider)
        return _load_file_store().get(profile, {}).get(provider)


def byok_delete_key(profile, provider):
    profile = (profile or "").strip()
    provider = (provider or "").strip().lower()
    if not profile:
        return False
    with _LOCK:
        if _keychain_available():
            _keychain_delete(profile, provider)
        else:
            data = _load_file_store()
            if profile in data:
                data[profile].pop(provider, None)
                if not data[profile]:
                    data.pop(profile)
                _save_file_store(data)
        idx = _load_index()
        if provider in idx.get(profile, []):
            idx[profile].remove(provider)
            if not idx[profile]:
                idx.pop(profile)
            _save_index(idx)
    return True


def byok_delete_profile(profile):
    profile = (profile or "").strip()
    if not profile:
        return False
    idx = _load_index()
    for provider in list(idx.get(profile, [])):
        byok_delete_key(profile, provider)
    return True


def byok_env_for_profile(profile):
    """{"ANTHROPIC_API_KEY": "...", ...} for every provider configured on profile."""
    profile = (profile or "").strip()
    if not profile:
        return {}
    env = {}
    for provider in _load_index().get(profile, []):
        key = byok_get_key(profile, provider)
        if not key:
            continue
        for var in BYOK_PROVIDERS.get(provider, {}).get("env_vars", []):
            env[var] = key
    return env


# ---------------------------------------------------------------------------
# Spawn-time env injection
# ---------------------------------------------------------------------------

def byok_spawn_env(engine, model, key_profile):
    """Extra env vars to merge into a spawned engine subprocess for BYOK.

    Returns {} when BYOK doesn't apply (no profile picked, and the model
    isn't a "<provider>/..." virtual id) so callers can pass this straight
    through without a branch: ``env={**os.environ, **byok_spawn_env(...)}``.
    """
    engine = (engine or "").strip().lower()
    profile = (key_profile or "").strip()
    provider = byok_resolve_virtual_provider(model)
    if not profile and provider:
        profile = "default"
    if not profile or engine not in BYOK_DIRECT_ENV_ENGINES:
        return {}
    return byok_env_for_profile(profile)


# ---------------------------------------------------------------------------
# Usage / cost ledger
# ---------------------------------------------------------------------------

def byok_estimate_cost(model, tokens_in, tokens_out):
    row = _BYOK_MODEL_INDEX.get((model or "").strip().lower())
    if not row:
        return None
    return round(
        (tokens_in or 0) / 1_000_000 * row["cost_in_per_1m"]
        + (tokens_out or 0) / 1_000_000 * row["cost_out_per_1m"],
        6,
    )


def byok_record_usage(*, session_id=None, engine, model, key_profile=None, tokens_in=0, tokens_out=0, cost_usd=None):
    provider = byok_resolve_virtual_provider(model)
    if cost_usd is None:
        cost_usd = byok_estimate_cost(model, tokens_in, tokens_out)
    entry = {
        "ts": time.time(),
        "session_id": session_id,
        "engine": engine,
        "model": model,
        "provider": provider,
        "key_profile": key_profile,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost_usd": cost_usd,
    }
    try:
        _state_dir().mkdir(parents=True, exist_ok=True)
        with open(_usage_log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return entry


def byok_usage_summary(days=30):
    cutoff = time.time() - max(days, 0) * 86400
    try:
        lines = _usage_log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    total_cost = 0.0
    total_tokens = 0
    by_profile = {}
    spawns = 0
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("ts", 0) < cutoff:
            continue
        cost = entry.get("cost_usd") or 0.0
        tokens = int(entry.get("tokens_in") or 0) + int(entry.get("tokens_out") or 0)
        total_cost += cost
        total_tokens += tokens
        spawns += 1
        key = entry.get("key_profile") or "(none)"
        bucket = by_profile.setdefault(key, {"cost_usd": 0.0, "tokens": 0, "spawns": 0})
        bucket["cost_usd"] += cost
        bucket["tokens"] += tokens
        bucket["spawns"] += 1
    for bucket in by_profile.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return {
        "days": days,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "spawns": spawns,
        "by_profile": by_profile,
    }
