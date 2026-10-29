"""Vault (ccc_server/vault.py), its shared storage primitives
(ccc_server/secret_store.py), the write-only /api/vault endpoints, the BYOK
listing/import bridge, and the `ccc vault` CLI (incl. exec redaction).

Never touches the real Keychain: `secret_store.keychain_*` are replaced by an
in-memory fake for the Keychain-path tests, `subprocess.run` inside
secret_store is booby-trapped so any un-faked `security` call fails the test,
and all state lives in a per-test tempdir.
"""

import argparse
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- adopts byok/vault and wires the _core proxy
from ccc_server import byok, secret_store, vault

FAKE_SECRET = "sk-test-vault-XXXX-0123456789abcdef"


class FakeKeychain:
    def __init__(self):
        self.items = {}

    def set(self, service, account, secret):
        self.items[(service, account)] = secret
        return True

    def get(self, service, account):
        return self.items.get((service, account))

    def delete(self, service, account):
        self.items.pop((service, account), None)


def _no_real_security(*args, **kwargs):
    raise AssertionError(f"test tried to run a real `security` subprocess: {args!r}")


def _fake_subprocess(run):
    """Stand-in for secret_store's `subprocess` reference only (the server
    and CLI keep the real module)."""
    return types.SimpleNamespace(
        run=run, SubprocessError=subprocess.SubprocessError,
        CompletedProcess=subprocess.CompletedProcess,
    )


class VaultTestBase(unittest.TestCase):
    """Fake Keychain on (use_keychain=True) or the encrypted-file fallback."""

    use_keychain = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.vault_dir = root / "vault"
        self.byok_dir = root / "byok"
        self.keychain = FakeKeychain()
        patches = [
            mock.patch.object(vault, "_vault_state_dir", return_value=self.vault_dir),
            mock.patch.object(byok, "_state_dir", return_value=self.byok_dir),
            mock.patch.object(secret_store, "keychain_available", return_value=self.use_keychain),
            mock.patch.object(secret_store, "keychain_set", side_effect=self.keychain.set),
            mock.patch.object(secret_store, "keychain_get", side_effect=self.keychain.get),
            mock.patch.object(secret_store, "keychain_delete", side_effect=self.keychain.delete),
            mock.patch.object(secret_store, "subprocess", _fake_subprocess(_no_real_security)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def index_text(self):
        return (self.vault_dir / "index.json").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class TestVaultStorageKeychain(VaultTestBase):
    def test_create_stores_value_in_keychain_service_ccc_vault(self):
        entry = vault.vault_create_entry(
            "meta-app-token", FAKE_SECRET, kind="token", service="Meta", env_var="META_TOKEN",
        )
        self.assertEqual(self.keychain.items[("ccc-vault", "meta-app-token")], FAKE_SECRET)
        self.assertEqual(entry["name"], "meta-app-token")
        self.assertEqual(entry["kind"], "token")
        self.assertTrue(entry["saved"])
        self.assertEqual(vault.vault_get_value("meta-app-token"), FAKE_SECRET)

    def test_index_never_contains_value_and_is_0600(self):
        vault.vault_create_entry("stripe", FAKE_SECRET, kind="api_key", service="Stripe")
        self.assertNotIn(FAKE_SECRET, self.index_text())
        mode = stat.S_IMODE(os.stat(self.vault_dir / "index.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_hint_last4_only_for_long_api_keys_and_tokens(self):
        e1 = vault.vault_create_entry("k1", FAKE_SECRET, kind="api_key")
        e2 = vault.vault_create_entry("pw", "hunter2-password-long", kind="login", username="me")
        e3 = vault.vault_create_entry("short", "abcd1234", kind="token")
        self.assertEqual(e1["hint"], FAKE_SECRET[-4:])
        self.assertEqual(e2["hint"], "")
        self.assertEqual(e3["hint"], "")

    def test_listing_is_metadata_only(self):
        vault.vault_create_entry("b-entry", FAKE_SECRET, kind="other", service="B")
        vault.vault_create_entry("a-entry", FAKE_SECRET + "2", kind="other", service="A")
        rows = vault.vault_list_entries()
        self.assertEqual([r["name"] for r in rows], ["a-entry", "b-entry"])
        self.assertNotIn(FAKE_SECRET, json.dumps(rows))

    def test_duplicate_name_rejected(self):
        vault.vault_create_entry("dup", FAKE_SECRET)
        with self.assertRaises(vault.VaultError):
            vault.vault_create_entry("dup", "other-value")
        self.assertEqual(vault.vault_get_value("dup"), FAKE_SECRET)

    def test_validation(self):
        for bad_name in ("", "Has Space", "-leading", "x" * 65, "slash/name"):
            with self.assertRaises(vault.VaultError, msg=bad_name):
                vault.vault_create_entry(bad_name, FAKE_SECRET)
        with self.assertRaises(vault.VaultError):
            vault.vault_create_entry("ok", FAKE_SECRET, kind="password")
        with self.assertRaises(vault.VaultError):
            vault.vault_create_entry("ok", FAKE_SECRET, env_var="1BAD")
        with self.assertRaises(vault.VaultError):
            vault.vault_create_entry("ok", "   ")
        self.assertEqual(vault.vault_list_entries(), [])

    def test_name_is_lowercased(self):
        vault.vault_create_entry("Google_Places_API", FAKE_SECRET)
        self.assertIsNotNone(vault.vault_get_entry("google_places_api"))

    def test_update_metadata_keeps_value(self):
        vault.vault_create_entry("e", FAKE_SECRET, kind="api_key", service="Old")
        entry = vault.vault_update_entry("e", service="New", notes="rotated quarterly", value="")
        self.assertEqual(entry["service"], "New")
        self.assertEqual(entry["notes"], "rotated quarterly")
        self.assertEqual(entry["kind"], "api_key")
        self.assertEqual(vault.vault_get_value("e"), FAKE_SECRET)

    def test_replace_value_bumps_value_updated_at(self):
        first = vault.vault_create_entry("e", FAKE_SECRET, kind="api_key")
        with mock.patch.object(vault.time, "time", return_value=first["value_updated_at"] + 100):
            entry = vault.vault_update_entry("e", value="sk-test-new-value-ZZZZ-1111")
        self.assertEqual(vault.vault_get_value("e"), "sk-test-new-value-ZZZZ-1111")
        self.assertGreater(entry["value_updated_at"], first["value_updated_at"])
        self.assertEqual(entry["hint"], "1111")

    def test_update_missing_entry_errors(self):
        with self.assertRaises(vault.VaultError):
            vault.vault_update_entry("ghost", service="x")

    def test_delete_removes_value_and_metadata(self):
        vault.vault_create_entry("gone", FAKE_SECRET)
        self.assertTrue(vault.vault_delete_entry("gone"))
        self.assertIsNone(vault.vault_get_entry("gone"))
        self.assertIsNone(vault.vault_get_value("gone"))
        self.assertNotIn(("ccc-vault", "gone"), self.keychain.items)
        self.assertFalse(vault.vault_delete_entry("gone"))

    def test_failed_keychain_write_leaves_no_index_entry(self):
        with mock.patch.object(secret_store, "keychain_set", return_value=False):
            with self.assertRaises(vault.VaultError):
                vault.vault_create_entry("e", FAKE_SECRET)
        self.assertEqual(vault.vault_list_entries(), [])

    def test_backend_reports_keychain(self):
        self.assertEqual(vault.vault_storage_backend(), "keychain")


class TestVaultStorageEncryptedFile(VaultTestBase):
    use_keychain = False

    def test_round_trip_without_plaintext_on_disk(self):
        vault.vault_create_entry("e", FAKE_SECRET, kind="token", service="S")
        self.assertEqual(vault.vault_get_value("e"), FAKE_SECRET)
        self.assertEqual(vault.vault_storage_backend(), "encrypted-file")
        self.assertEqual(self.keychain.items, {})
        for path in self.vault_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(FAKE_SECRET.encode(), path.read_bytes(), str(path))
        mode = stat.S_IMODE(os.stat(self.vault_dir / "secrets.enc.json").st_mode)
        self.assertEqual(mode, 0o600)

    def test_delete_from_file_store(self):
        vault.vault_create_entry("a", FAKE_SECRET)
        vault.vault_create_entry("b", FAKE_SECRET + "b")
        vault.vault_delete_entry("a")
        self.assertIsNone(vault.vault_get_value("a"))
        self.assertEqual(vault.vault_get_value("b"), FAKE_SECRET + "b")

    def test_byok_file_store_still_round_trips_after_refactor(self):
        self.assertTrue(byok.byok_set_key("work", "openrouter", "sk-or-test-XXXX"))
        self.assertEqual(byok.byok_get_key("work", "openrouter"), "sk-or-test-XXXX")


# ---------------------------------------------------------------------------
# Shared Keychain primitives (security CLI invocation shape)
# ---------------------------------------------------------------------------

class TestSecretStoreKeychainCalls(unittest.TestCase):
    def test_set_passes_value_hex_on_stdin_never_in_argv(self):
        calls = []
        secret = 'sk-test "quote" \\slash XXXX'

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            if "find-generic-password" in argv:
                hexval = secret.encode().hex()
                return subprocess.CompletedProcess(argv, 0, "", f'password: 0x{hexval.upper()}  "..."\n')
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(secret_store, "subprocess", _fake_subprocess(fake_run)):
            self.assertTrue(secret_store.keychain_set("ccc-vault", "acct", secret))
        for argv, kwargs in calls:
            self.assertNotIn(secret, " ".join(argv))
        add = [kw for argv, kw in calls if argv == ["security", "-i"]]
        self.assertEqual(len(add), 1)
        self.assertIn(secret.encode().hex(), add[0]["input"])
        self.assertIn('-s "ccc-vault"', add[0]["input"])

    def test_set_reports_failure_when_read_back_mismatches(self):
        def fake_run(argv, **kwargs):
            if "find-generic-password" in argv:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(secret_store, "subprocess", _fake_subprocess(fake_run)):
            self.assertFalse(secret_store.keychain_set("svc", "acct", "v"))

    def test_parse_password_line_formats(self):
        p = secret_store.parse_security_password_line
        self.assertEqual(p('keychain: "x"\npassword: "plain-value"\n'), "plain-value")
        self.assertEqual(p('password: 0x6162  "ab"\n'), "ab")
        self.assertEqual(p('password: 0x70C3A4  "p\\303\\244"\n'), "p\u00e4")
        self.assertEqual(p("password: \n"), "")
        self.assertIsNone(p("nothing here"))


# ---------------------------------------------------------------------------
# BYOK listing + import
# ---------------------------------------------------------------------------

class TestByokBridge(VaultTestBase):
    def setUp(self):
        super().setUp()
        byok.byok_set_key("default", "jev", "tsk-test-XXXX-meta-0000")
        byok.byok_set_key("default", "google", "AIza-test-XXXX-resend")

    def test_byok_listed_with_names_only(self):
        rows = vault.vault_list_byok()
        pairs = {(r["profile"], r["provider"]) for r in rows}
        self.assertEqual(pairs, {("default", "jev"), ("default", "google")})
        self.assertNotIn("tsk-test", json.dumps(rows))
        self.assertTrue(all(r["imported_as"] == [] for r in rows))

    def test_byok_keys_read_from_ccc_byok_service(self):
        self.assertEqual(self.keychain.items[("ccc-byok", "default:jev")], "tsk-test-XXXX-meta-0000")

    def test_import_copies_value_and_keeps_byok_entry(self):
        entry = vault.vault_import_byok(
            "default", "jev", "meta-app-token", kind="token", service="Meta", env_var="META_TOKEN",
        )
        self.assertEqual(entry["source"], {"type": "byok", "profile": "default", "provider": "jev"})
        self.assertEqual(vault.vault_get_value("meta-app-token"), "tsk-test-XXXX-meta-0000")
        # BYOK untouched: still listed, still injectable.
        self.assertEqual(byok.byok_get_key("default", "jev"), "tsk-test-XXXX-meta-0000")
        self.assertIn({"name": "default", "providers": ["google", "jev"]}, byok.byok_list_profiles())
        rows = {(r["profile"], r["provider"]): r for r in vault.vault_list_byok()}
        self.assertEqual(rows[("default", "jev")]["imported_as"], ["meta-app-token"])

    def test_import_unknown_or_missing_key_errors(self):
        with self.assertRaises(vault.VaultError):
            vault.vault_import_byok("default", "openai", "x")
        with self.assertRaises(vault.VaultError):
            vault.vault_import_byok("nope", "jev", "x")
        with self.assertRaises(vault.VaultError):
            vault.vault_import_byok("default", "not-a-provider", "x")
        self.assertEqual(vault.vault_list_entries(), [])

    def test_import_name_collision_errors(self):
        vault.vault_create_entry("taken", FAKE_SECRET)
        with self.assertRaises(vault.VaultError):
            vault.vault_import_byok("default", "jev", "taken")
        self.assertEqual(vault.vault_get_value("taken"), FAKE_SECRET)


# ---------------------------------------------------------------------------
# HTTP API (real requests through CommandCenterHandler)
# ---------------------------------------------------------------------------

class TestVaultApi(VaultTestBase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    def setUp(self):
        super().setUp()
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, timeout=5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"
        self.raw_bodies = []

    def _post(self, path, payload, headers=None):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode()
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            status = e.code
        self.raw_bodies.append(raw)
        return status, json.loads(raw)

    def _get(self):
        with urllib.request.urlopen(self.base + "/api/vault", timeout=10) as resp:
            raw = resp.read().decode()
        self.raw_bodies.append(raw)
        return json.loads(raw)

    def test_create_list_update_delete_never_returns_value(self):
        status, data = self._post("/api/vault/entries", {
            "name": "stripe-live", "kind": "api_key", "service": "Stripe",
            "env_var": "STRIPE_API_KEY", "value": FAKE_SECRET,
        })
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["entry"]["hint"], FAKE_SECRET[-4:])
        listing = self._get()
        self.assertEqual([e["name"] for e in listing["entries"]], ["stripe-live"])
        self.assertEqual(listing["backend"], "keychain")
        self.assertIn("login", listing["kinds"])

        status, data = self._post("/api/vault/entries/update", {
            "name": "stripe-live", "service": "Stripe (live)", "value": "sk-test-rotated-XXXX-9999",
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(data["entry"]["service"], "Stripe (live)")
        self.assertEqual(vault.vault_get_value("stripe-live"), "sk-test-rotated-XXXX-9999")
        self._get()

        status, data = self._post("/api/vault/entries/delete", {"name": "stripe-live"})
        self.assertEqual((status, data), (200, {"ok": True, "deleted": True}))
        self.assertEqual(self._get()["entries"], [])
        for raw in self.raw_bodies:
            self.assertNotIn(FAKE_SECRET, raw)
            self.assertNotIn("sk-test-rotated-XXXX-9999", raw)

    def test_validation_errors_are_400(self):
        status, data = self._post("/api/vault/entries", {"name": "x", "value": ""})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        status, _ = self._post("/api/vault/entries", {"name": "Bad Name", "value": FAKE_SECRET})
        self.assertEqual(status, 400)
        status, _ = self._post("/api/vault/entries/update", {"name": "ghost", "service": "x"})
        self.assertEqual(status, 400)

    def test_cross_origin_post_rejected(self):
        status, _ = self._post(
            "/api/vault/entries", {"name": "x", "value": FAKE_SECRET},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(vault.vault_list_entries(), [])

    def test_byok_rows_and_import_via_api(self):
        byok.byok_set_key("default", "anthropic", "sk-ant-test-XXXX-places")
        listing = self._get()
        self.assertEqual(
            [(r["profile"], r["provider"]) for r in listing["byok"]], [("default", "anthropic")],
        )
        status, data = self._post("/api/vault/import-byok", {
            "profile": "default", "provider": "anthropic", "name": "google-places-api",
            "kind": "api_key", "service": "Google Places",
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(vault.vault_get_value("google-places-api"), "sk-ant-test-XXXX-places")
        self.assertEqual(byok.byok_get_key("default", "anthropic"), "sk-ant-test-XXXX-places")
        self.assertEqual(self._get()["byok"][0]["imported_as"], ["google-places-api"])
        for raw in self.raw_bodies:
            self.assertNotIn("sk-ant-test-XXXX-places", raw)


# ---------------------------------------------------------------------------
# `ccc vault` CLI
# ---------------------------------------------------------------------------

def _load_ccc_cli():
    ccc_path = Path(__file__).resolve().parent.parent / "ccc"
    loader = importlib.machinery.SourceFileLoader("ccc_cli_vault", str(ccc_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _BytesStream:
    """Stand-in for sys.stdout/sys.stderr with a .buffer the pumps write to."""

    def __init__(self):
        self.buffer = io.BytesIO()
        self._text = io.StringIO()

    def write(self, s):
        return self._text.write(s)

    def flush(self):
        pass

    def isatty(self):
        return False

    def getvalue(self):
        return self._text.getvalue() + self.buffer.getvalue().decode("utf-8", "replace")


class TestVaultCli(VaultTestBase):
    @classmethod
    def setUpClass(cls):
        cls.ccc = _load_ccc_cli()

    def setUp(self):
        super().setUp()
        p = mock.patch.object(self.ccc, "_vault_module", return_value=vault)
        p.start()
        self.addCleanup(p.stop)
        vault.vault_create_entry(
            "meta-app-token", FAKE_SECRET, kind="token", service="Meta", env_var="META_TOKEN",
        )

    def _run(self, fn, **kw):
        out, err = _BytesStream(), _BytesStream()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            rc = fn(argparse.Namespace(**kw))
        return rc, out.getvalue(), err.getvalue()

    def test_list_never_prints_values(self):
        rc, out, _ = self._run(self.ccc.cmd_vault_list, json=False)
        self.assertEqual(rc, 0)
        self.assertIn("meta-app-token", out)
        self.assertIn("META_TOKEN", out)
        self.assertNotIn(FAKE_SECRET, out)
        rc, out, _ = self._run(self.ccc.cmd_vault_list, json=True)
        data = json.loads(out)
        self.assertEqual(data["entries"][0]["env_var"], "META_TOKEN")
        self.assertNotIn(FAKE_SECRET, out)
        self.assertNotIn(FAKE_SECRET[-4:], out)

    def test_get_exit_codes_and_reveal(self):
        rc, out, _ = self._run(self.ccc.cmd_vault_get, name="meta-app-token", reveal=False)
        self.assertEqual(rc, 0)
        self.assertNotIn(FAKE_SECRET, out)
        rc, out, _ = self._run(self.ccc.cmd_vault_get, name="meta-app-token", reveal=True)
        self.assertEqual((rc, out), (0, FAKE_SECRET))
        rc, out, _ = self._run(self.ccc.cmd_vault_get, name="missing", reveal=True)
        self.assertEqual((rc, out), (1, ""))

    def test_exec_injects_env_and_redacts_stdout_and_stderr(self):
        code = (
            "import os, sys\n"
            "v = os.environ['META_TOKEN']\n"
            "print('out:' + v)\n"
            "sys.stderr.write('err:' + v + '\\n')\n"
            "print('len', len(v))\n"
        )
        rc, out, err = self._run(
            self.ccc.cmd_vault_exec, env=["META_TOKEN=meta-app-token"],
            cmd=["--", sys.executable, "-c", code],
        )
        self.assertEqual(rc, 0)
        self.assertNotIn(FAKE_SECRET, out + err)
        self.assertIn("out:[REDACTED:meta-app-token]", out)
        self.assertIn("err:[REDACTED:meta-app-token]", err)
        self.assertIn(f"len {len(FAKE_SECRET)}", out)  # the child really got the value

    def test_exec_bare_name_uses_stored_env_var_and_propagates_exit_code(self):
        rc, out, _ = self._run(
            self.ccc.cmd_vault_exec, env=["meta-app-token"],
            cmd=[sys.executable, "-c", "import os,sys; print(os.environ['META_TOKEN']); sys.exit(7)"],
        )
        self.assertEqual(rc, 7)
        self.assertEqual(out.strip(), "[REDACTED:meta-app-token]")

    def test_exec_missing_entry_does_not_run(self):
        rc, out, err = self._run(
            self.ccc.cmd_vault_exec, env=["X=nope"], cmd=[sys.executable, "-c", "print('ran')"],
        )
        self.assertEqual(rc, 2)
        self.assertNotIn("ran", out)
        self.assertIn("nope", err)

    def test_exec_bad_env_var_name_rejected(self):
        rc, _, err = self._run(
            self.ccc.cmd_vault_exec, env=["1X=meta-app-token"], cmd=["true"],
        )
        self.assertEqual(rc, 2)
        self.assertIn("not a valid", err)

    def test_pump_redacts_secret_split_across_reads(self):
        pairs = self.ccc._vault_redaction_pairs([("s", FAKE_SECRET)])
        r, w = os.pipe()
        sink = io.BytesIO()
        pump = self.ccc._VaultRedactingPump(r, sink, pairs)
        t = threading.Thread(target=pump.run)
        t.start()
        half = len(FAKE_SECRET) // 2
        os.write(w, b"prefix " + FAKE_SECRET[:half].encode())
        os.write(w, FAKE_SECRET[half:].encode() + b" suffix\n")
        os.close(w)
        t.join(timeout=5)
        self.assertEqual(sink.getvalue(), b"prefix [REDACTED:s] suffix\n")

    def test_multiline_secret_lines_redacted_individually(self):
        pem = "-----BEGIN KEY-----\nAAAABBBBCCCCDDDD\nEEEEFFFFGGGGHHHH\n-----END KEY-----"
        pairs = self.ccc._vault_redaction_pairs([("pem", pem)])
        out = self.ccc._vault_redact(b"line: EEEEFFFFGGGGHHHH\n", pairs)
        self.assertEqual(out, b"line: [REDACTED:pem]\n")


if __name__ == "__main__":
    unittest.main()
