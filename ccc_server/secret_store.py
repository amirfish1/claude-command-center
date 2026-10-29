# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Shared local secret-storage primitives for BYOK (ccc_server/byok.py) and
the Vault (ccc_server/vault.py).

Two backends:

- macOS Keychain via the ``security`` CLI (generic passwords, one service per
  feature, one account per secret). Writes go through ``security -i`` on
  stdin with the value hex-encoded (``-X``), so secret material never shows
  up in a process's argv (visible to every local user via ``ps``). Reads use
  ``-g`` and parse its ``password:`` line, which is unambiguous for any
  value (``-w`` prints non-printable values as bare hex, indistinguishable
  from a printable hex-looking value).
- Off Darwin, or when ``security`` is missing: a locally-encrypted JSON
  file. The cipher is a from-stdlib HMAC-SHA256 counter-mode stream with a
  PBKDF2-derived key from a per-feature random seed file (0600) — sturdy
  against casual disclosure, but a fallback, not an audited primitive.

Stdlib-only and import-side-effect free. Deliberately does NOT import
``server``/``_core`` so the ``ccc`` CLI can use it without a running server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import shutil
import subprocess

_SECURITY_TIMEOUT_S = 5


# ---------------------------------------------------------------------------
# Atomic, private file writes
# ---------------------------------------------------------------------------

def ensure_private_dir(path):
    """mkdir -p with 0700 on the leaf (best effort)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def write_private_text(path, text):
    """Atomically write ``text`` to ``path`` with mode 0600.

    The temp file is created 0600 from the start (os.open with O_EXCL), so
    there is no window where the content is world-readable."""
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        os.unlink(tmp)
    except OSError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Encrypted-file fallback
# ---------------------------------------------------------------------------

def load_or_create_seed(seed_path):
    """Random 32-byte machine seed for the file cipher, created 0600 once."""
    try:
        return seed_path.read_bytes()
    except OSError:
        pass
    secret = os.urandom(32)
    try:
        ensure_private_dir(seed_path.parent)
        fd = os.open(str(seed_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(secret)
    except FileExistsError:
        # Lost a create race; use whatever won.
        try:
            return seed_path.read_bytes()
        except OSError:
            pass
    except OSError:
        pass
    return secret


def derive_key(seed, context):
    return hashlib.pbkdf2_hmac("sha256", seed, context, 100_000, dklen=32)


def _keystream(key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def encrypt(key, plaintext: bytes) -> dict:
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext))))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).hexdigest()
    return {"nonce": nonce.hex(), "cipher": cipher.hex(), "mac": mac}


def decrypt(key, blob):
    """Plaintext bytes, or None when the blob is malformed or tampered."""
    try:
        nonce = bytes.fromhex(blob["nonce"])
        cipher = bytes.fromhex(blob["cipher"])
        expect_mac = hmac.new(key, nonce + cipher, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect_mac, blob.get("mac", "")):
            return None
        return bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher))))
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def load_encrypted_json(path, key):
    """Decrypt a JSON-object file written by save_encrypted_json; {} on any
    failure (missing, corrupt, wrong key)."""
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    plain = decrypt(key, blob)
    if plain is None:
        return {}
    try:
        data = json.loads(plain.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_encrypted_json(path, key, data):
    ensure_private_dir(path.parent)
    blob = encrypt(key, json.dumps(data).encode("utf-8"))
    write_private_text(path, json.dumps(blob))


# ---------------------------------------------------------------------------
# macOS Keychain (generic passwords via the `security` CLI)
# ---------------------------------------------------------------------------

def keychain_available():
    return platform.system() == "Darwin" and shutil.which("security") is not None


def _security_quote(arg):
    """Quote one token for `security -i`'s line parser. Service/account names
    are validated slugs, so this only has to survive spaces/colons."""
    return '"' + str(arg).replace("\\", "\\\\").replace('"', '\\"') + '"'


def keychain_delete(service, account):
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", service],
            capture_output=True, timeout=_SECURITY_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def parse_security_password_line(stderr_text):
    """Value from `security find-generic-password -g` stderr, else None.

    Formats: ``password: "plain"`` for printable values without quotes or
    backslashes; ``password: 0x<HEX>  "<escaped>"`` otherwise; and a bare
    ``password: `` for an empty value."""
    for line in (stderr_text or "").splitlines():
        if not line.startswith("password:"):
            continue
        rest = line[len("password:"):].strip()
        if not rest:
            return ""
        if rest.startswith("0x"):
            hex_part = rest[2:].split()[0] if rest[2:].strip() else ""
            try:
                return bytes.fromhex(hex_part).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None
        if len(rest) >= 2 and rest.startswith('"') and rest.endswith('"'):
            return rest[1:-1]
        return None
    return None


def keychain_get(service, account):
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-g"],
            capture_output=True, text=True, timeout=_SECURITY_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return parse_security_password_line(proc.stderr)


def keychain_set(service, account, secret):
    """Store ``secret`` (str); True only when a read-back matches.

    ``security -i`` exits 0 even when a command inside it fails, so success
    is confirmed by reading the item back rather than trusting the rc."""
    keychain_delete(service, account)
    line = "add-generic-password -a {} -s {} -X {} -U\n".format(
        _security_quote(account), _security_quote(service), secret.encode("utf-8").hex(),
    )
    try:
        proc = subprocess.run(
            ["security", "-i"], input=line, capture_output=True, text=True,
            timeout=_SECURITY_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    return keychain_get(service, account) == secret
