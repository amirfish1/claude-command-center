"""Phone access: open this CCC on a phone over Tailscale, no manual setup.

CCC binds 127.0.0.1 and rejects any POST whose Origin is not loopback (see
SECURITY.md). Reaching it from a phone therefore needs three things to line
up, and getting any one wrong leaves reads working while every write 403s:

1. A tailnet-only HTTPS front door: ``tailscale serve --bg --https=<N>
   http://127.0.0.1:<PORT>``. Port 443 is often taken by something else, so
   we pick a free HTTPS port and never touch entries we did not create.
2. The resulting ``https://<node>.<tailnet>.ts.net[:N]`` origin trusted by the
   same-origin guard -- WITHOUT a restart. ``live_extra_origins`` is read by
   ``_check_same_origin`` on every POST and reloads phone-access.json and
   network.json when their mtime changes.
3. A test that does a real POST round trip through that URL, so "it works" is
   proven rather than assumed.
4. A second tailnet-only entry for the conversation-reader port
   (``https://<node>:<R>`` -> ``http://127.0.0.1:<R>``, R = PORT + 10000,
   ccc_server/conv_reader.py), so conversation opens skip the dashboard from
   the phone too. Best effort: when it cannot be added, the UI keeps opening
   conversations through the dashboard entry.

Everything is recorded in ``phone-access.json`` (what we created, so Turn off
removes exactly that) together with an optional PIN gate: when a PIN is set,
requests that arrive from off-machine (via tailscale serve, a tunnel, or a
non-loopback bind) must present a session cookie obtained by entering the
PIN. Loopback stays open, as it always has.

Federated peers run the same functions on their own loopback via the
federation route table (phone_access_* actions), so the Fleet page can show
and set up each node's phone URL.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import federation
from ccc_server import core as _core
from ccc_server import qrcode as _qrcode

PHONE_ACCESS_FLAG = "phone_access"


def state_file() -> Path:
    """phone-access.json, resolved per call so tests can point the state dir
    (or CCC_PHONE_ACCESS_FILE) somewhere private."""
    override = os.environ.get("CCC_PHONE_ACCESS_FILE")
    if override:
        return Path(override)
    return Path(_core.COMMAND_CENTER_STATE_DIR) / "phone-access.json"

PHONE_PIN_COOKIE = "ccc_phone_session"
# Tried in order when CCC has no serve entry yet. 443 gives the cleanest URL;
# the rest are the HTTPS ports Tailscale has always accepted, then a few more.
_CANDIDATE_HTTPS_PORTS = (443, 8443, 10000, 4443, 9443, 10443)
_PIN_ITERATIONS = 200_000
_PIN_SESSION_TTL_S = 30 * 24 * 3600
_PIN_MAX_SESSIONS = 20
_PIN_MAX_FAILS_PER_MIN = 5
_TAILSCALE_CANDIDATES = (
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)
# Forwarding headers a reverse proxy in front of CCC adds. Tailscale serve
# always sets X-Forwarded-For; cloudflared sets Cf-Connecting-Ip.
_FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded",
                    "Tailscale-User-Login", "Cf-Connecting-Ip", "X-Real-Ip")

_lock = threading.Lock()
_state_cache = {"key": None, "data": None}
_network_cache = {"key": None, "origins": [], "trust_tailnet": False}
_pin_fail_times: list[float] = []
_ts_status_cache = {"ts": 0.0, "data": None}
_tailnet_refresh = {"ts": 0.0, "hostname": "", "ips": []}


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def _file_key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def load_state() -> dict:
    """phone-access.json, cached by (mtime, size). Missing/corrupt -> {}."""
    path = state_file()
    key = (str(path), _file_key(path))
    with _lock:
        if key[1] is not None and _state_cache["key"] == key:
            return dict(_state_cache["data"])
    data = {}
    if key[1] is not None:
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
    with _lock:
        _state_cache["key"] = key
        _state_cache["data"] = data
    return dict(data)


def save_state(data: dict) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)  # holds the PIN hash and session hashes
    except OSError:
        pass
    tmp.replace(path)
    with _lock:
        _state_cache["key"] = None


def _update_state(mutator) -> dict:
    data = load_state()
    mutator(data)
    save_state(data)
    return data


# ---------------------------------------------------------------------------
# Hot-reloadable origin allowlist
# ---------------------------------------------------------------------------

def phone_origin(hostname: str, https_port: int) -> str:
    host = (hostname or "").rstrip(".").lower()
    if not host:
        return ""
    return f"https://{host}" if int(https_port) == 443 else f"https://{host}:{int(https_port)}"


def _network_file_live():
    """(allowed_origins, trust_tailnet) from network.json, re-read on change."""
    path = Path(_core.NETWORK_CONFIG_FILE)
    key = (str(path), _file_key(path))
    with _lock:
        if _network_cache["key"] == key:
            return list(_network_cache["origins"]), _network_cache["trust_tailnet"]
    origins, trust = [], False
    if key[1] is not None:
        try:
            cfg = _core._load_network_config()
            origins = list(cfg.get("allowed_origins") or [])
            trust = bool(cfg.get("trust_tailnet"))
        except Exception:
            origins, trust = [], False
    with _lock:
        _network_cache.update({"key": key, "origins": origins, "trust_tailnet": trust})
    return origins, trust


def live_extra_origins() -> list[str]:
    """Origins trusted on top of the startup allowlist, reloaded without a
    restart: network.json ``allowed_origins`` plus the phone-access origin
    recorded when the user turned phone access on. Cheap: two stat() calls
    on the hot path, a JSON parse only when a file changed."""
    origins, _ = _network_file_live()
    serve = (load_state().get("serve") or {})
    origin = serve.get("origin") or ""
    if origin:
        origins.append(origin)
    return origins


def live_trust_tailnet() -> bool:
    return _network_file_live()[1]


def refreshed_tailnet_identity(max_age_s: float = 30.0) -> dict:
    """This node's current MagicDNS name + Tailscale IPs, re-detected at most
    every ``max_age_s``. Used only on a same-origin MISS for a Tailscale-looking
    origin, so a node whose hostname changed after startup (a cloned disk, a
    renamed machine) is recognised without a restart."""
    now = time.time()
    with _lock:
        if now - _tailnet_refresh["ts"] < max_age_s:
            return {"hostname": _tailnet_refresh["hostname"], "ips": list(_tailnet_refresh["ips"])}
        _tailnet_refresh["ts"] = now
    st = tailscale_status(max_age_s=0)
    with _lock:
        _tailnet_refresh["hostname"] = st.get("hostname") or ""
        _tailnet_refresh["ips"] = list(st.get("ips") or [])
    return {"hostname": st.get("hostname") or "", "ips": list(st.get("ips") or [])}


# ---------------------------------------------------------------------------
# Tailscale CLI
# ---------------------------------------------------------------------------

def find_tailscale() -> str | None:
    """Absolute path to the tailscale CLI. The launchd PATH is minimal, so
    check the usual install locations (Homebrew, the Mac app bundle, distro
    packages) rather than trusting PATH alone."""
    override = os.environ.get("CCC_TAILSCALE_BIN")
    if override:
        return override if os.path.exists(override) else None
    found = shutil.which("tailscale")
    if found:
        return found
    for cand in _TAILSCALE_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def _run_tailscale(args, timeout=10.0):
    """Run the tailscale CLI. Returns (rc, stdout, stderr); rc is None when the
    binary is missing and -1 on timeout (partial output preserved)."""
    binary = find_tailscale()
    if not binary:
        return None, "", "tailscale CLI not found"
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        def _s(v):
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return v or ""
        return -1, _s(e.stdout), _s(e.stderr)
    except OSError as e:
        return None, "", str(e)


def parse_tailscale_status(data) -> dict:
    """Pure: ``tailscale status --json`` payload -> the fields the wizard
    needs. Tolerates missing keys (older CLIs, logged-out nodes)."""
    if not isinstance(data, dict):
        data = {}
    self_node = data.get("Self") or {}
    backend = str(data.get("BackendState") or "")
    hostname = str(self_node.get("DNSName") or "").rstrip(".").lower()
    ips = [ip for ip in (self_node.get("TailscaleIPs") or data.get("TailscaleIPs") or [])
           if isinstance(ip, str)]
    users = data.get("User") or {}
    user = users.get(str(self_node.get("UserID"))) if isinstance(users, dict) else None
    tailnet = data.get("CurrentTailnet") or {}
    cert_domains = [d for d in (data.get("CertDomains") or []) if isinstance(d, str)]
    running = backend == "Running"
    return {
        "backend_state": backend,
        "running": running,
        "logged_in": backend not in ("NeedsLogin", "NoState", ""),
        "needs_login": backend == "NeedsLogin",
        "needs_machine_auth": backend == "NeedsMachineAuth",
        "auth_url": str(data.get("AuthURL") or ""),
        "hostname": hostname,
        "ips": ips,
        "login_name": str((user or {}).get("LoginName") or ""),
        "tailnet": str(tailnet.get("Name") or ""),
        "magicdns": bool(tailnet.get("MagicDNSEnabled")) if tailnet else bool(hostname),
        "https_certs": bool(cert_domains),
        "version": str(data.get("Version") or ""),
    }


def tailscale_status(max_age_s: float = 3.0) -> dict:
    """Installed/running/logged-in snapshot of the local Tailscale node."""
    now = time.time()
    with _lock:
        cached = _ts_status_cache["data"]
        if cached is not None and now - _ts_status_cache["ts"] < max_age_s:
            return dict(cached)
    rc, out, err = _run_tailscale(["status", "--json"], timeout=6.0)
    if rc is None:
        result = {"installed": False, "running": False, "logged_in": False,
                  "backend_state": "", "hostname": "", "ips": [], "error": err}
    else:
        try:
            data = json.loads(out) if out.strip() else {}
        except ValueError:
            data = {}
        result = {"installed": True, **parse_tailscale_status(data)}
        if not data:
            # `tailscale status --json` exits non-zero with no JSON when the
            # daemon isn't running at all (Mac app quit, tailscaled stopped).
            result["backend_state"] = result["backend_state"] or "NotRunning"
            result["error"] = (err or out).strip()[:300]
    with _lock:
        _ts_status_cache.update({"ts": now, "data": result})
    return dict(result)


def _normalize_target(url: str) -> str:
    """http://localhost:8090/ -> http://127.0.0.1:8090 for comparisons."""
    u = (url or "").strip().rstrip("/")
    u = re.sub(r"^http://(?:localhost|\[::1\])(?=:|$)", "http://127.0.0.1", u)
    if re.fullmatch(r"\d+", u):
        u = "http://127.0.0.1:" + u
    return u


def parse_serve_status(data) -> dict:
    """Pure: ``tailscale serve status --json`` -> {entries, occupied_ports}.
    Each entry is one (host:port, path) handler."""
    if not isinstance(data, dict):
        data = {}
    occupied = set()
    for port in (data.get("TCP") or {}):
        try:
            occupied.add(int(port))
        except (TypeError, ValueError):
            pass
    entries = []
    for hostport, web in (data.get("Web") or {}).items():
        host, _, port_s = str(hostport).rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        occupied.add(port)
        for path, handler in ((web or {}).get("Handlers") or {}).items():
            handler = handler or {}
            target = handler.get("Proxy") or handler.get("Path") or handler.get("Text") or ""
            entries.append({
                "host": host.lower(),
                "https_port": port,
                "path": path,
                "proxy": _normalize_target(handler.get("Proxy") or ""),
                "target": target,
            })
    funnel = sorted(str(k) for k, v in (data.get("AllowFunnel") or {}).items() if v)
    return {"entries": entries, "occupied_ports": sorted(occupied), "funnel": funnel}


def plan_serve(serve: dict, local_port: int, candidates=_CANDIDATE_HTTPS_PORTS) -> dict:
    """Pure: decide how to expose http://127.0.0.1:<local_port>.

    - ``reuse`` when a root handler already proxies to CCC (set up earlier,
      by us or by hand) -- nothing to create, nothing to delete later.
    - ``create`` on the first candidate HTTPS port nobody is using.
    - ``conflicts`` lists entries on candidate ports that point elsewhere,
      so the UI can say "443 is used by http://127.0.0.1:18765 (left alone)".
    """
    target = f"http://127.0.0.1:{int(local_port)}"
    for e in serve.get("entries") or []:
        if e["path"] == "/" and e["proxy"] == target:
            return {"action": "reuse", "https_port": e["https_port"], "target": target,
                    "conflicts": []}
    occupied = set(serve.get("occupied_ports") or [])
    conflicts = [
        {"https_port": e["https_port"], "target": e["target"] or e["proxy"]}
        for e in serve.get("entries") or []
        if e["https_port"] in candidates and e["proxy"] != target
    ]
    for port in candidates:
        if port not in occupied:
            return {"action": "create", "https_port": port, "target": target,
                    "conflicts": conflicts}
    return {"action": "none", "https_port": None, "target": target, "conflicts": conflicts}


def serve_status() -> dict:
    rc, out, err = _run_tailscale(["serve", "status", "--json"], timeout=6.0)
    if rc is None:
        return {"ok": False, "entries": [], "occupied_ports": [], "funnel": [], "error": err}
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError:
        data = {}
    parsed = parse_serve_status(data)
    parsed["ok"] = rc == 0
    if rc != 0:
        parsed["error"] = (err or out).strip()[:300]
    return parsed


def classify_serve_error(text: str) -> dict:
    """Pure: map `tailscale serve` failure output to an actionable hint."""
    t = text or ""
    url_m = re.search(r"https://login\.tailscale\.com/\S+", t)
    url = url_m.group(0).rstrip(".,)") if url_m else ""
    low = t.lower()
    if "serve is not enabled" in low or "not enabled on your tailnet" in low or url_m and "enable" in low:
        return {"error": "serve_not_enabled", "action_url": url,
                "detail": "Tailscale Serve (HTTPS certificates) is not enabled for your "
                          "tailnet yet. Open the link, approve it once, then retry."}
    if "access denied" in low or "operator" in low or "permission denied" in low:
        return {"error": "needs_operator",
                "detail": "This user may not configure Tailscale Serve. Run once: "
                          "sudo tailscale set --operator=$USER  (Linux), then retry."}
    if "https" in low and "cert" in low:
        return {"error": "https_disabled", "action_url": url or "https://login.tailscale.com/admin/dns",
                "detail": "Enable MagicDNS and HTTPS Certificates in the Tailscale admin "
                          "console (DNS page), then retry."}
    return {"error": "serve_failed", "action_url": url, "detail": t.strip()[:400]}


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------

def _url_for(state: dict) -> str:
    serve = state.get("serve") or {}
    return (serve.get("origin") or "") + "/" if serve.get("origin") else ""


def enable(local_port: int | None = None) -> dict:
    port = int(local_port or _core.PORT)
    ts = tailscale_status(max_age_s=0)
    if not ts.get("installed"):
        return {"ok": False, "error": "tailscale_missing",
                "detail": "Install Tailscale on this computer first.",
                "action_url": "https://tailscale.com/download"}
    if not ts.get("running"):
        return {"ok": False, "error": "tailscale_down",
                "detail": "Tailscale is installed but not connected "
                          f"(state: {ts.get('backend_state') or 'unknown'}). "
                          "Open Tailscale and log in, then retry.",
                "action_url": ts.get("auth_url") or ""}
    if not ts.get("hostname"):
        return {"ok": False, "error": "no_magicdns",
                "detail": "This node has no MagicDNS name. Enable MagicDNS in the "
                          "Tailscale admin console (DNS page), then retry.",
                "action_url": "https://login.tailscale.com/admin/dns"}
    before = serve_status()
    plan = plan_serve(before, port)
    if plan["action"] == "none":
        return {"ok": False, "error": "no_free_port", "plan": plan,
                "detail": "Every candidate HTTPS port already serves something else. "
                          "Free one with `tailscale serve status`."}
    funnel_keys = {str(k).lower() for k in before.get("funnel") or []}
    if plan["action"] == "reuse" and any(
            k.endswith(f":{plan['https_port']}") for k in funnel_keys):
        return {"ok": False, "error": "funnel_on", "plan": plan,
                "detail": "Tailscale Funnel is ON for this CCC's serve entry, which would "
                          "expose it to the public internet. Run `tailscale funnel "
                          f"--https={plan['https_port']} off` first, then retry."}
    created_by = "existing"
    if plan["action"] == "create":
        rc, out, err = _run_tailscale(
            ["serve", "--bg", f"--https={plan['https_port']}", plan["target"]], timeout=25.0)
        if rc != 0:
            hint = classify_serve_error((out or "") + "\n" + (err or ""))
            if rc == -1:
                hint.setdefault("detail", "")
                hint["timed_out"] = True
            return {"ok": False, "plan": plan, **hint}
        after = parse_serve_status(_json_or_empty(_run_tailscale(["serve", "status", "--json"], 6.0)[1]))
        if plan_serve(after, port)["action"] != "reuse":
            return {"ok": False, "error": "serve_not_applied", "plan": plan,
                    "detail": "tailscale serve reported success but the entry is not "
                              "in `tailscale serve status`."}
        created_by = "ccc"
    origin = phone_origin(ts["hostname"], plan["https_port"])
    previous = (load_state().get("serve") or {})

    def _mut(d):
        d["serve"] = {
            "https_port": plan["https_port"],
            "hostname": ts["hostname"],
            "origin": origin,
            "target": plan["target"],
            # Keep "ccc" if we created it earlier and are merely re-confirming.
            "created_by": "ccc" if previous.get("created_by") == "ccc"
                          and previous.get("https_port") == plan["https_port"] else created_by,
            "enabled_at": time.time(),
        }
    state = _update_state(_mut)
    reader = ensure_reader_serve(port)
    return {"ok": True, "url": _url_for(state), "origin": origin, "plan": plan,
            "created_by": state["serve"]["created_by"], "reader": reader}


def plan_reader_serve(serve: dict, reader_port) -> dict:
    """Pure: expose the conversation-reader port on the same HTTPS port
    number (the UI derives https://<node>:<reader_port> from the page). Never
    takes over a port something else serves."""
    if not reader_port:
        return {"action": "none", "https_port": None, "target": None}
    port = int(reader_port)
    target = f"http://127.0.0.1:{port}"
    for e in serve.get("entries") or []:
        if e["https_port"] == port:
            ours = e["path"] == "/" and e["proxy"] == target
            return {"action": "reuse" if ours else "taken", "https_port": port,
                    "target": target}
    if port in set(serve.get("occupied_ports") or []):
        return {"action": "taken", "https_port": port, "target": target}
    return {"action": "create", "https_port": port, "target": target}


def ensure_reader_serve(local_port: int | None = None, before: dict | None = None) -> dict:
    """Add (or adopt) the reader's serve entry and record it. Never raises."""
    try:
        from ccc_server import conv_reader
        reader_port = conv_reader.http_port(int(local_port or _core.PORT))
        before = before if before is not None else serve_status()
        plan = plan_reader_serve(before, reader_port)
        if plan["action"] in ("none", "taken"):
            return {"ok": False, "plan": plan}
        if plan["action"] == "create":
            rc, out, err = _run_tailscale(
                ["serve", "--bg", f"--https={plan['https_port']}", plan["target"]], timeout=25.0)
            if rc != 0:
                return {"ok": False, "plan": plan,
                        "detail": ((err or out) or "tailscale serve failed").strip()[:300]}
        previous = load_state().get("reader") or {}
        created_by = "ccc" if plan["action"] == "create" or (
            previous.get("created_by") == "ccc"
            and previous.get("https_port") == plan["https_port"]) else "existing"

        def _mut(d):
            d["reader"] = {"https_port": plan["https_port"], "target": plan["target"],
                           "created_by": created_by}
        _update_state(_mut)
        return {"ok": True, "plan": plan, "created_by": created_by}
    except Exception as exc:  # best effort: opens fall back to the dashboard
        return {"ok": False, "detail": repr(exc)[:300]}


def ensure_reader_serve_if_enabled(local_port: int | None = None) -> dict | None:
    """Dashboard start: installs that turned Phone access on before the
    reader port existed get its entry without re-running setup."""
    if not (load_state().get("serve") or {}):
        return None
    return ensure_reader_serve(local_port)


def _json_or_empty(text):
    try:
        return json.loads(text) if (text or "").strip() else {}
    except ValueError:
        return {}


def disable() -> dict:
    """Remove ONLY the serve entry CCC created, and only if it still points at
    CCC. An entry the user made (or changed) is forgotten, never deleted."""
    state = load_state()
    serve = state.get("serve") or {}
    if not serve:
        return {"ok": True, "removed": False, "detail": "phone access was not on"}
    removed = False
    note = ""
    current = None
    reader = state.get("reader") or {}
    if reader.get("created_by") == "ccc":
        current = serve_status()
        if any(e["https_port"] == reader.get("https_port") and e["path"] == "/"
               and e["proxy"] == reader.get("target")
               for e in current.get("entries") or []):
            _run_tailscale(["serve", f"--https={reader['https_port']}", "off"], timeout=15.0)
    if serve.get("created_by") == "ccc":
        current = current or serve_status()
        still_ours = any(
            e["https_port"] == serve.get("https_port") and e["path"] == "/"
            and e["proxy"] == serve.get("target")
            for e in current.get("entries") or []
        )
        if still_ours:
            rc, out, err = _run_tailscale(
                ["serve", f"--https={serve['https_port']}", "off"], timeout=15.0)
            if rc != 0:
                return {"ok": False, "error": "serve_off_failed",
                        "detail": ((err or out) or "tailscale serve off failed").strip()[:300]}
            removed = True
        else:
            note = "the serve entry was changed or removed outside CCC; left as is"
    else:
        note = "the serve entry existed before CCC; left as is"

    def _mut(d):
        d.pop("serve", None)
        d.pop("reader", None)
    _update_state(_mut)
    return {"ok": True, "removed": removed, "detail": note}


# ---------------------------------------------------------------------------
# PIN gate
# ---------------------------------------------------------------------------

def _hash_pin(pin: str, salt: bytes, iterations: int = _PIN_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations).hex()


def pin_is_set() -> bool:
    return bool((load_state().get("pin") or {}).get("hash"))


def set_pin(pin: str) -> dict:
    pin = (pin or "").strip()
    if not re.fullmatch(r"\S{4,64}", pin):
        return {"ok": False, "error": "bad_pin",
                "detail": "Use 4-64 characters, no spaces. A 6+ digit PIN or a passphrase."}
    salt = secrets.token_bytes(16)

    def _mut(d):
        d["pin"] = {"salt": salt.hex(), "hash": _hash_pin(pin, salt),
                    "iterations": _PIN_ITERATIONS, "set_at": time.time()}
        d["pin_sessions"] = []  # a new PIN signs every phone out
    _update_state(_mut)
    return {"ok": True, "pin_set": True}


def clear_pin() -> dict:
    def _mut(d):
        d.pop("pin", None)
        d.pop("pin_sessions", None)
    _update_state(_mut)
    return {"ok": True, "pin_set": False}


def unlock(pin: str) -> dict:
    """Check a PIN; on success mint a session token (returned once, stored
    hashed). Globally rate-limited so a tailnet peer can't brute-force it."""
    now = time.time()
    with _lock:
        _pin_fail_times[:] = [t for t in _pin_fail_times if now - t < 60]
        if len(_pin_fail_times) >= _PIN_MAX_FAILS_PER_MIN:
            return {"ok": False, "error": "rate_limited",
                    "detail": "Too many wrong PINs. Wait a minute."}
        # Reserve the attempt BEFORE the slow PBKDF2 check: otherwise a burst of
        # concurrent guesses all pass the limit check before any failure is
        # recorded. The slot is released on success.
        _pin_fail_times.append(now)
    rec = load_state().get("pin") or {}
    if not rec.get("hash"):
        return {"ok": False, "error": "no_pin"}
    try:
        salt = bytes.fromhex(rec.get("salt") or "")
        iterations = int(rec.get("iterations") or _PIN_ITERATIONS)
    except (ValueError, TypeError):
        return {"ok": False, "error": "no_pin"}
    if not hmac.compare_digest(_hash_pin((pin or "").strip(), salt, iterations), rec["hash"]):
        return {"ok": False, "error": "wrong_pin"}
    with _lock:
        try:
            _pin_fail_times.remove(now)
        except ValueError:
            pass
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()

    def _mut(d):
        sessions = [s for s in (d.get("pin_sessions") or [])
                    if isinstance(s, dict) and s.get("exp", 0) > now]
        sessions.append({"h": digest, "exp": now + _PIN_SESSION_TTL_S})
        d["pin_sessions"] = sessions[-_PIN_MAX_SESSIONS:]
    _update_state(_mut)
    return {"ok": True, "token": token, "max_age": _PIN_SESSION_TTL_S}


def session_valid(token: str) -> bool:
    if not token:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    for s in load_state().get("pin_sessions") or []:
        if isinstance(s, dict) and s.get("exp", 0) > now and hmac.compare_digest(str(s.get("h") or ""), digest):
            return True
    return False


def is_remote_request(client_ip: str, headers) -> bool:
    """Pure: did this request come from off this machine? True for a
    non-loopback peer address, or for a loopback connection carrying a
    reverse proxy's forwarding headers (tailscale serve, cloudflared)."""
    try:
        if not ipaddress.ip_address((client_ip or "").split("%", 1)[0]).is_loopback:
            return True
    except ValueError:
        return True
    for name in _FORWARD_HEADERS:
        if headers.get(name):
            return True
    host = (headers.get("Host") or "").strip().lower()
    if not host:
        return False
    # A genuinely local caller addresses CCC by a loopback name. Anything else
    # arriving over loopback is a proxy or a DNS-rebinding page, not this user.
    hostname = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.split(":", 1)[0]
    return hostname not in ("localhost", "127.0.0.1", "::1")


# Paths a locked-out phone may reach: the unlock page/endpoint and the echo
# probe the Test button uses (it returns only the nonce it was sent).
PIN_EXEMPT_PATHS = ("/phone-unlock", "/api/phone-access/unlock", "/api/phone-access/echo")


def cookie_token(cookie_header: str) -> str:
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == PHONE_PIN_COOKIE:
            return value
    return ""


def pin_gate_blocks(path: str, client_ip: str, headers) -> bool:
    """True when the PIN gate must refuse this request."""
    if path in PIN_EXEMPT_PATHS:
        return False
    if not pin_is_set():
        return False
    if not is_remote_request(client_ip, headers):
        return False
    return not session_valid(cookie_token(headers.get("Cookie") or ""))


UNLOCK_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CCC - enter PIN</title>
<style>body{font:16px -apple-system,system-ui,sans-serif;background:#111;color:#eee;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
form{display:flex;flex-direction:column;gap:12px;width:min(320px,90vw)}
input,button{font-size:20px;padding:12px;border-radius:8px;border:1px solid #444}
input{background:#1c1c1c;color:#eee;text-align:center;letter-spacing:4px}
button{background:#3b82f6;color:#fff;border:0}#e{color:#f87171;min-height:1.2em}</style>
</head><body><form id="f"><div>This Command Center is PIN-protected.</div>
<input id="p" type="password" inputmode="numeric" autocomplete="current-password" autofocus
 placeholder="PIN"><button>Unlock</button><div id="e"></div></form>
<script>
document.getElementById('f').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const e = document.getElementById('e'); e.textContent = '';
  try {
    const r = await fetch('/api/phone-access/unlock', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin: document.getElementById('p').value})});
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) { location.replace('/'); return; }
    e.textContent = d.error === 'rate_limited' ? 'Too many tries. Wait a minute.'
      : d.error === 'cross-origin POST rejected' || r.status === 403
        ? 'This address is not trusted by CCC yet. Re-run Phone access setup.'
        : 'Wrong PIN.';
  } catch (_) { e.textContent = 'Could not reach CCC.'; }
});
</script></body></html>"""


# ---------------------------------------------------------------------------
# Test: a real POST round trip through the tailnet URL
# ---------------------------------------------------------------------------

def classify_test_failure(status: int | None, body, exc_text: str = "") -> dict:
    """Pure: turn one failed round trip into {error, detail}."""
    if status is None:
        low = (exc_text or "").lower()
        if "nodename nor servname" in low or "name or service not known" in low \
                or "getaddrinfo" in low or "temporary failure in name resolution" in low:
            return {"error": "dns", "detail": "The tailnet name does not resolve on this "
                    "machine. Is MagicDNS on and Tailscale connected? (" + exc_text[:160] + ")"}
        if "timed out" in low:
            return {"error": "timeout", "detail": "No answer from the tailnet URL within the "
                    "timeout. Tailscale may be down or the serve entry is stale."}
        if "refused" in low:
            return {"error": "tailscale_down", "detail": "Connection refused on the tailnet "
                    "address: tailscale serve is not listening on that port."}
        return {"error": "unreachable", "detail": exc_text[:300] or "request failed"}
    err = body.get("error") if isinstance(body, dict) else ""
    if status == 403 and "cross-origin" in str(err):
        return {"error": "cross_origin", "detail": "CCC rejected the phone's Origin "
                f"({body.get('origin') if isinstance(body, dict) else ''}). "
                "Turn phone access off and on again to re-trust the current address."}
    if status in (502, 503, 504):
        return {"error": "serve_backend_down", "detail": f"HTTP {status}: tailscale serve "
                "answered but could not reach CCC behind it. The entry may point at an "
                "old port."}
    if status == 401:
        return {"error": "pin", "detail": "Blocked by the PIN gate."}
    if status == 200:
        return {"error": "serve_conflict", "detail": "Something answered on that URL, but "
                "it was not this CCC. Another app owns that serve entry."}
    return {"error": "http_error", "detail": f"HTTP {status}: {str(err or body)[:200]}"}


def run_roundtrip_test(url: str | None = None, timeout: float = 8.0) -> dict:
    state = load_state()
    serve = state.get("serve") or {}
    origin = serve.get("origin") or ""
    url = url or (origin + "/" if origin else "")
    if not url:
        return {"ok": False, "error": "not_enabled", "detail": "Phone access is off."}
    ts = tailscale_status(max_age_s=0)
    if not ts.get("running"):
        return {"ok": False, "error": "tailscale_down", "url": url,
                "detail": f"Tailscale is not connected (state: {ts.get('backend_state') or 'unknown'})."}
    pre = serve_status()
    ours = [e for e in pre.get("entries") or []
            if e["https_port"] == serve.get("https_port") and e["path"] == "/"]
    if serve and pre.get("ok") and not ours:
        return {"ok": False, "error": "serve_missing", "url": url,
                "detail": f"No tailscale serve entry on HTTPS port {serve.get('https_port')} "
                          "any more. Turn phone access on again."}
    if ours and ours[0]["proxy"] != serve.get("target"):
        return {"ok": False, "error": "serve_conflict", "url": url,
                "detail": f"HTTPS port {serve.get('https_port')} now serves "
                          f"{ours[0]['target']}, not this CCC."}
    nonce = secrets.token_hex(12)
    endpoint = url.rstrip("/") + "/api/phone-access/echo"
    body = json.dumps({"nonce": nonce}).encode()
    warning = ""
    started = time.time()
    for verify in (True, False):
        req = urllib.request.Request(endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", origin or url.rstrip("/"))
        ctx = None if verify else ssl._create_unverified_context()  # noqa: S323
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status, raw = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if verify and isinstance(reason, ssl.SSLCertVerificationError):
                # Python's own CA store (python.org builds on macOS) may lack
                # the root. The phone's browser verifies independently.
                warning = ("Python could not verify the certificate locally; retried "
                           "without verification. Your phone's browser checks it itself.")
                continue
            return {"ok": False, "url": url, **classify_test_failure(None, None, str(reason))}
        except (OSError, ValueError) as e:
            return {"ok": False, "url": url, **classify_test_failure(None, None, str(e))}
        break
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    elapsed_ms = int((time.time() - started) * 1000)
    if status == 200 and isinstance(parsed, dict) and parsed.get("nonce") == nonce:
        return {"ok": True, "url": url, "ms": elapsed_ms, "warning": warning,
                "detail": f"POST round trip through {url} succeeded in {elapsed_ms} ms."}
    return {"ok": False, "url": url, "ms": elapsed_ms, **classify_test_failure(status, parsed)}


# ---------------------------------------------------------------------------
# Overview + HTTP surface
# ---------------------------------------------------------------------------

def overview(include_qr: bool = True) -> dict:
    state = load_state()
    serve = state.get("serve") or {}
    ts = tailscale_status()
    sv = serve_status() if ts.get("installed") and ts.get("running") else {
        "ok": False, "entries": [], "occupied_ports": [], "funnel": []}
    plan = plan_serve(sv, _core.PORT)
    url = _url_for(state)
    hostname_changed = bool(serve and ts.get("hostname") and serve.get("hostname") != ts.get("hostname"))
    out = {
        "ok": True,
        "node_id": federation.node_id(),
        "node_name": (federation.node_identity().get("display_name") or ""),
        "flag": bool(_core._feature_flag(PHONE_ACCESS_FLAG)),
        "port": _core.PORT,
        "tailscale": ts,
        "serve": {"entries": sv.get("entries") or [], "funnel": sv.get("funnel") or [],
                  "plan": plan, "error": sv.get("error") or ""},
        "enabled": bool(serve),
        "url": url,
        "https_port": serve.get("https_port"),
        "created_by": serve.get("created_by") or "",
        "hostname_changed": hostname_changed,
        "pin_set": pin_is_set(),
    }
    if url and include_qr:
        try:
            out["qr_svg"] = _qrcode.to_svg(url)
        except ValueError:
            out["qr_svg"] = ""
    return out


def web_url_from_status(status) -> str:
    """Pure: the browser-reachable https URL of a node's CCC, from its
    phone-access status payload, or "" when it has none. Prefers the entry
    CCC created; otherwise reuses a `tailscale serve` root handler that
    already proxies to CCC (set up by hand)."""
    if not isinstance(status, dict) or not status.get("ok"):
        return ""
    if status.get("url"):
        return str(status["url"])
    plan = ((status.get("serve") or {}).get("plan")) or {}
    host = str((status.get("tailscale") or {}).get("hostname") or "").strip()
    port = plan.get("https_port")
    if plan.get("action") != "reuse" or not host or not port:
        return ""
    return "https://" + host + ("" if int(port) == 443 else ":" + str(int(port))) + "/"


# sub -> federation route action (see _FEDERATION_ROUTE_ACTIONS in fleet.py)
PHONE_ACCESS_ROUTE_ACTIONS = {
    "status": "phone_access_status",
    "enable": "phone_access_enable",
    "disable": "phone_access_disable",
    "test": "phone_access_test",
}


def phone_access_handle(sub: str, data: dict) -> tuple[dict, int]:
    """Dispatch one /api/phone-access/<sub> admin call (the caller has already
    checked this is a local, non-proxied request). ``node_id`` naming a paired
    peer runs the call on that peer via the federation route envelope."""
    if not isinstance(data, dict):
        data = {}
    node = "" if data.get("via_route") else str(data.get("node_id") or "").strip()
    if node and node != federation.node_id():
        if sub not in PHONE_ACCESS_ROUTE_ACTIONS:
            return {"ok": False, "error": "not_routable"}, 400
        args = {k: v for k, v in data.items() if k not in ("node_id", "via_route")}
        return _core._federation_proxy_session_action(
            node, PHONE_ACCESS_ROUTE_ACTIONS[sub], args, timeout=60.0), 200
    if sub == "status":
        return overview(include_qr=data.get("qr", True) is not False), 200
    if sub == "tailscale":
        # Light probe for the first-run step: cached Tailscale status only,
        # no `serve status` / QR work. Polled while that step is on screen.
        state = load_state()
        return {
            "ok": True,
            "tailscale": tailscale_status(max_age_s=8.0),
            "flag": bool(_core._feature_flag(PHONE_ACCESS_FLAG)),
            "enabled": bool(state.get("serve")),
            "url": _url_for(state),
        }, 200
    if sub == "enable":
        if not data.get("via_route") and not _core._feature_flag(PHONE_ACCESS_FLAG):
            return {"ok": False, "error": "feature_disabled",
                    "detail": "Turn on \"Phone access\" in Settings > Experimental"}, 403
        result = enable()
        if result.get("ok"):
            result["status"] = overview()
        return result, 200
    if sub == "disable":
        result = disable()
        result["status"] = overview(include_qr=False)
        return result, 200
    if sub == "test":
        return run_roundtrip_test(), 200
    if sub == "pin":
        if data.get("clear"):
            return clear_pin(), 200
        result = set_pin(str(data.get("pin") or ""))
        return result, (200 if result.get("ok") else 400)
    return {"ok": False, "error": "not_found"}, 404


def nodes_overview(timeout: float = 20.0) -> dict:
    """This node plus every paired peer's phone-access status, in parallel.
    A peer that can't be reached reports its transport error instead."""
    rows = [{"self": True, **overview(include_qr=False)}]
    peers = federation.load_peers()

    def _one(peer):
        nid = peer.get("node_id") or ""
        res = _core._federation_proxy_session_action(
            nid, "phone_access_status", {"qr": False}, timeout=timeout)
        name = peer.get("display_name") or peer.get("name") or ""
        if not isinstance(res, dict):
            res = {"ok": False, "error": "bad_response"}
        res.pop("routed_to", None)
        return {"self": False, **res, "node_id": nid, "node_name": res.get("node_name") or name}

    if peers:
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
            rows.extend(pool.map(_one, peers))
    return {"ok": True, "nodes": rows}
