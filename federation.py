"""Federation layer for Claude Command Center.

Gives every CCC installation a stable node identity, a paired-peer registry,
and a transport for calling a peer CCC's HTTP API on *its own* loopback
interface (over SSH for remote machines, or direct loopback for a second CCC
on this machine). Also defines the stable cross-machine identities: canonical
repository identity and global session references.

Design rules:
- stdlib only, like server.py.
- Loopback trust model is preserved: we never listen on non-loopback here;
  the SSH transport executes a tiny HTTP client on the remote machine so the
  request originates from the peer's own loopback.
- Secrets and machine-local paths live under ~/.claude/command-center/,
  never in a repository.
"""

from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

FEDERATION_PROTO_VERSION = 1

# ---------------------------------------------------------------------------
# State locations (lazy — respect $HOME so isolated test homes work)
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    return Path.home() / ".claude" / "command-center"


def federation_dir() -> Path:
    return state_dir() / "federation"


def _node_file() -> Path:
    return state_dir() / "node.json"


def _peers_file() -> Path:
    return state_dir() / "peers.json"


def _repo_map_file() -> Path:
    return federation_dir() / "repo-map.json"


def _dedupe_file() -> Path:
    return federation_dir() / "route-dedupe.json"


_STATE_LOCK = threading.Lock()


def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Node identity
# ---------------------------------------------------------------------------


def node_identity() -> dict[str, Any]:
    """Load (or lazily create) this installation's stable node identity."""
    with _STATE_LOCK:
        data = _read_json(_node_file(), None)
        if isinstance(data, dict) and data.get("node_id"):
            return data
        data = {
            "node_id": str(uuid.uuid4()),
            "display_name": socket.gethostname().split(".")[0] or "ccc-node",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        _write_json(_node_file(), data)
        return data


def node_id() -> str:
    return node_identity()["node_id"]


def set_node_display_name(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("display name must not be empty")
    with _STATE_LOCK:
        data = _read_json(_node_file(), None)
        if not isinstance(data, dict) or not data.get("node_id"):
            data = None
    if data is None:
        data = node_identity()
    data["display_name"] = name[:80]
    with _STATE_LOCK:
        _write_json(_node_file(), data)
    return data


def capability_manifest(version: str, engines: list[str] | None = None) -> dict[str, Any]:
    """This node's capability card, computed fresh (never persisted)."""
    engines = engines or ["claude"]
    return {
        "proto": FEDERATION_PROTO_VERSION,
        "version": version,
        "engines": sorted(set(engines)),
        "features": [
            "federation",
            "fleet",
            "group_chat_host",
            "handoff",
            "orchestration",
        ],
        # Only engines whose native session store we can migrate safely.
        "handoff_engines": ["claude"],
    }


# ---------------------------------------------------------------------------
# Repository identity
# ---------------------------------------------------------------------------

_SSH_URL_RE = re.compile(r"^(?:ssh://)?(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9._-]+)(?::(\d+))?[:/](.+)$")


def parse_remote_url(url: str) -> str | None:
    """Normalize a Git remote URL to canonical ``host/owner/repo`` identity.

    Handles https://, ssh://, scp-like (git@host:owner/repo.git) and
    git:// forms. Returns None when the URL cannot be parsed.
    """
    url = (url or "").strip()
    if not url:
        return None
    m = re.match(r"^(?:https?|git)://([^/]+)/(.+)$", url)
    if m:
        host, rest = m.group(1), m.group(2)
    else:
        if "://" in url and not url.startswith("ssh://"):
            return None  # file://, ftp://, other non-Git schemes
        m = _SSH_URL_RE.match(url)
        if not m:
            return None
        host, rest = m.group(1), m.group(3)
    host = host.split("@")[-1].split(":")[0].lower()
    rest = rest.strip("/")
    if rest.endswith(".git"):
        rest = rest[: -len(".git")]
    if not host or not rest or "/" not in rest:
        return None
    # owner/repo (some hosts nest deeper, e.g. GitLab groups — keep the path)
    return f"{host}/{rest}"


def _run_git(repo_path: str, args: list[str], timeout: int = 10) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def repo_identity(repo_path: str) -> dict[str, Any] | None:
    """Stable cross-machine identity for the repository at ``repo_path``.

    Prefers canonical remote identity (host/owner/repo). Falls back to a
    local-only identity derived from the root commit so two clones of the
    same history still match even without a shared remote.
    """
    repo_path = str(Path(repo_path).expanduser())
    if not os.path.isdir(repo_path):
        return None
    url = _run_git(repo_path, ["remote", "get-url", "origin"])
    if url:
        ident = parse_remote_url(url)
        if ident:
            return {"identity": ident, "kind": "remote", "remote_url": url}
    root = _run_git(repo_path, ["rev-list", "--max-parents=0", "--max-count=1", "HEAD"])
    if root:
        return {
            "identity": f"local:{Path(repo_path).name}:{root[:12]}",
            "kind": "local",
            "remote_url": None,
        }
    return None


# Per-node mapping: stable repo identity -> this node's local clone path.


def load_repo_map() -> dict[str, str]:
    data = _read_json(_repo_map_file(), {})
    return data if isinstance(data, dict) else {}


def map_repo(identity: str, local_path: str) -> dict[str, str]:
    identity = (identity or "").strip()
    local_path = str(Path(local_path).expanduser().resolve())
    if not identity:
        raise ValueError("repo identity required")
    with _STATE_LOCK:
        data = load_repo_map()
        if data.get(identity) == local_path:
            return data  # unchanged — skip the disk write
        data[identity] = local_path
        _write_json(_repo_map_file(), data)
    return data


def unmap_repo(identity: str) -> dict[str, str]:
    with _STATE_LOCK:
        data = load_repo_map()
        data.pop(identity, None)
        _write_json(_repo_map_file(), data)
    return data


def resolve_repo_path(identity: str) -> str | None:
    """Local clone path for a stable repo identity on THIS node."""
    return load_repo_map().get(identity)


# ---------------------------------------------------------------------------
# Global session references
# ---------------------------------------------------------------------------


def format_session_ref(owner_node_id: str, session_id: str) -> str:
    return f"{owner_node_id}:{session_id}"


def parse_session_ref(ref: str) -> tuple[str | None, str]:
    """Split ``node_id:session_id`` → (node_id, session_id).

    A bare session ID (no node prefix) returns (None, ref) and means
    "owned by this node" — existing local callers stay compatible.
    """
    ref = (ref or "").strip()
    if ":" not in ref:
        return None, ref
    head, tail = ref.split(":", 1)
    # Node ids are UUIDs; anything else with a colon is treated as a native
    # session id (defensive — no engine we support uses colons today).
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", head):
        return head, tail
    return None, ref


# ---------------------------------------------------------------------------
# Peer registry
# ---------------------------------------------------------------------------


def load_peers() -> list[dict[str, Any]]:
    data = _read_json(_peers_file(), [])
    return [p for p in data if isinstance(p, dict) and p.get("node_id")] if isinstance(data, list) else []


def _save_peers(peers: list[dict[str, Any]]) -> None:
    _write_json(_peers_file(), peers)


def get_peer(peer_node_id: str) -> dict[str, Any] | None:
    for p in load_peers():
        if p.get("node_id") == peer_node_id:
            return p
    return None


def upsert_peer(entry: dict[str, Any]) -> dict[str, Any]:
    """Add or update a paired peer. ``entry`` must carry node_id + transport."""
    if not entry.get("node_id"):
        raise ValueError("peer entry requires node_id")
    transport = entry.get("transport") or {}
    # "unconfigured" = we know the peer (it paired with us) but have no route
    # back to it yet; calls raise unsupported_capability until the user
    # configures a transport.
    if transport.get("type") not in ("ssh", "loopback", "unconfigured"):
        raise ValueError("peer transport must be ssh, loopback, or unconfigured")
    with _STATE_LOCK:
        peers = load_peers()
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        for i, p in enumerate(peers):
            if p.get("node_id") == entry["node_id"]:
                merged = {**p, **entry}
                merged.setdefault("added_at", p.get("added_at") or now)
                peers[i] = merged
                _save_peers(peers)
                return merged
        entry.setdefault("added_at", now)
        peers.append(entry)
        _save_peers(peers)
        return entry


def remove_peer(peer_node_id: str) -> bool:
    with _STATE_LOCK:
        peers = load_peers()
        remaining = [p for p in peers if p.get("node_id") != peer_node_id]
        if len(remaining) == len(peers):
            return False
        _save_peers(remaining)
        return True


def update_peer(peer_node_id: str, **fields) -> dict[str, Any] | None:
    with _STATE_LOCK:
        peers = load_peers()
        for i, p in enumerate(peers):
            if p.get("node_id") == peer_node_id:
                p.update(fields)
                peers[i] = p
                _save_peers(peers)
                return p
    return None


def generate_pairing_secret() -> str:
    return _secrets.token_urlsafe(32)


def validate_peer_auth(peer_node_id: str, token: str) -> dict[str, Any] | None:
    """Return the peer entry when its pairing token matches, else None."""
    if not peer_node_id or not token:
        return None
    peer = get_peer(peer_node_id)
    if not peer:
        return None
    expected = peer.get("secret") or ""
    if expected and _secrets.compare_digest(str(expected), str(token)):
        return peer
    return None


# ---------------------------------------------------------------------------
# Route envelopes + idempotency
# ---------------------------------------------------------------------------

MAX_ROUTE_HOPS = 2
_DEDUPE_CAP = 500


def make_route_envelope(action: str, args: dict[str, Any], hops: int = MAX_ROUTE_HOPS) -> dict[str, Any]:
    return {
        "proto": FEDERATION_PROTO_VERSION,
        "req_id": str(uuid.uuid4()),
        "hops": int(hops),
        "action": action,
        "args": args or {},
    }


def check_and_record_request(req_id: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Idempotency ring: returns the recorded result when ``req_id`` was
    already processed; otherwise records it (with ``result`` if given) and
    returns None. Persisted so retries after a restart stay idempotent."""
    if not req_id:
        return None
    with _STATE_LOCK:
        data = _read_json(_dedupe_file(), {})
        if not isinstance(data, dict):
            data = {}
        if req_id in data:
            return data[req_id] or {"duplicate": True}
        entry = {"completed_at": time.time()}
        if result is not None:
            entry["result"] = result
        data[req_id] = entry
        if len(data) > _DEDUPE_CAP:
            oldest = sorted(data.items(), key=lambda kv: kv[1].get("completed_at", 0))
            data = dict(oldest[-_DEDUPE_CAP:])
        _write_json(_dedupe_file(), data)
    return None


def record_request_result(req_id: str, result: dict[str, Any]) -> None:
    if not req_id:
        return
    with _STATE_LOCK:
        data = _read_json(_dedupe_file(), {})
        if not isinstance(data, dict):
            data = {}
        entry = data.get(req_id) or {"completed_at": time.time()}
        entry["result"] = result
        data[req_id] = entry
        _write_json(_dedupe_file(), data)


# ---------------------------------------------------------------------------
# Peer transport
# ---------------------------------------------------------------------------


class PeerError(Exception):
    """Transport/protocol failure talking to a peer.

    ``kind`` is machine-readable and part of the API contract:
    peer_offline | timeout | unpaired_peer | http_error | bad_response |
    unsupported_capability | stale_mapping | unknown_session
    """

    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


# Small HTTP client executed on the REMOTE machine (over SSH) so the request
# reaches the peer CCC on its own loopback. Reads one JSON envelope on stdin:
# {"method","path","headers",{...},"body":...,"port":int|null}
# and prints {"status":int,"body":<parsed-or-raw>} on stdout.
_REMOTE_HTTP_CLIENT = r"""
import json, os, sys, urllib.request, urllib.error
env = json.load(sys.stdin)
port = env.get("port")
if not port:
    try:
        pf = os.path.expanduser("~/.claude/command-center/port.txt")
        port = int(open(pf).read().strip().splitlines()[0])
    except Exception:
        print(json.dumps({"status": 0, "error": "no port.txt on peer"}))
        sys.exit(0)
url = "http://127.0.0.1:%d%s" % (port, env["path"])
data = None
if env.get("body") is not None:
    data = json.dumps(env["body"]).encode()
req = urllib.request.Request(url, data=data, method=env.get("method", "GET"))
req.add_header("Content-Type", "application/json")
for k, v in (env.get("headers") or {}).items():
    req.add_header(k, v)
try:
    with urllib.request.urlopen(req, timeout=float(env.get("timeout") or 30)) as resp:
        raw = resp.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = raw
        print(json.dumps({"status": resp.status, "body": body}))
except urllib.error.HTTPError as e:
    raw = e.read().decode("utf-8", "replace")
    try:
        body = json.loads(raw)
    except Exception:
        body = raw
    print(json.dumps({"status": e.code, "body": body}))
except Exception as e:
    print(json.dumps({"status": 0, "error": str(e)}))
"""


class PeerClient:
    """Calls a paired peer CCC's /api/federation/v1/* endpoints."""

    def __init__(self, peer: dict[str, Any], self_node_id: str | None = None):
        self.peer = peer
        self.transport = peer.get("transport") or {}
        self.self_node_id = self_node_id or node_id()

    # -- public ------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict[str, Any]:
        headers = {
            "X-CCC-Peer": self.self_node_id,
            "X-CCC-Peer-Token": self.peer.get("secret") or "",
        }
        ttype = self.transport.get("type")
        if ttype == "loopback":
            result = self._request_loopback(method, path, body, headers, timeout)
        elif ttype == "ssh":
            result = self._request_ssh(method, path, body, headers, timeout)
        elif ttype == "unconfigured":
            raise PeerError(
                "unsupported_capability",
                "no transport configured back to this peer — set one up in peer settings",
            )
        else:
            raise PeerError("unsupported_capability", f"unknown transport {ttype!r}")
        status = result.get("status", 0)
        if status == 0:
            raise PeerError("peer_offline", str(result.get("error") or "peer unreachable"))
        payload = result.get("body")
        if status == 403 and isinstance(payload, dict) and payload.get("error") == "unpaired_peer":
            raise PeerError("unpaired_peer", "peer does not recognize this node — re-pair")
        if status >= 400:
            detail = payload.get("error") if isinstance(payload, dict) else str(payload)[:200]
            raise PeerError("http_error", f"HTTP {status}: {detail}")
        if not isinstance(payload, dict):
            raise PeerError("bad_response", "peer returned non-JSON payload")
        return payload

    # -- transports ----------------------------------------------------------

    def _request_loopback(self, method, path, body, headers, timeout) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        port = self.transport.get("port")
        if not port:
            raise PeerError("stale_mapping", "loopback peer has no port configured")
        url = f"http://127.0.0.1:{int(port)}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw
                return {"status": resp.status, "body": parsed}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            return {"status": e.code, "body": parsed}
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
                raise PeerError("timeout", f"peer timed out after {timeout}s")
            return {"status": 0, "error": str(reason)}
        except socket.timeout:
            raise PeerError("timeout", f"peer timed out after {timeout}s")

    def _ssh_mux(self):
        import ssh_multiplexer

        t = self.transport
        host = (t.get("host") or "").strip()
        if not host:
            raise PeerError("stale_mapping", "ssh peer has no host configured")
        user = t.get("user") or os.environ.get("USER") or "root"
        if "@" in host:
            user, host = host.split("@", 1)
        ssh_port = int(t.get("ssh_port") or 22)
        key_path = t.get("key_path") or ssh_multiplexer._default_key_path()
        config = ssh_multiplexer.BridgeConfig(
            host=host,
            user=user,
            ssh_port=ssh_port,
            key_path=str(Path(key_path).expanduser()),
            control_path=ssh_multiplexer._safe_control_path(user, host, ssh_port),
            control_persist="1h",
            connect_timeout=int(t.get("connect_timeout") or 10),
            remote_tmp="/tmp",
        )
        return ssh_multiplexer.SSHMultiplexer(config)

    def _request_ssh(self, method, path, body, headers, timeout) -> dict[str, Any]:
        result = self._request_ssh_once(method, path, body, headers, timeout,
                                        self.transport.get("port"))
        if result.get("status") == 0 and self.transport.get("port"):
            # Pinned port may be stale after a peer restart — retry once via
            # the peer's own port.txt discovery.
            result = self._request_ssh_once(method, path, body, headers, timeout, None)
        return result

    def _request_ssh_once(self, method, path, body, headers, timeout, port) -> dict[str, Any]:
        envelope = json.dumps({
            "method": method,
            "path": path,
            "headers": headers,
            "body": body,
            "port": port,
            "timeout": timeout,
        })
        try:
            mux = self._ssh_mux()
            proc = mux.run_capture(
                ["python3", "-c", _REMOTE_HTTP_CLIENT],
                timeout=int(timeout) + 15,
                input=envelope,
            )
        except subprocess.TimeoutExpired:
            raise PeerError("timeout", f"ssh transport timed out after {timeout}s")
        except (OSError, RuntimeError) as e:
            return {"status": 0, "error": str(e)}
        if proc.returncode != 0:
            return {"status": 0, "error": (proc.stderr or proc.stdout or "ssh failed").strip()[:300]}
        try:
            return json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"status": 0, "error": "peer returned unparseable transport payload"}


def peer_client(peer_node_id: str) -> PeerClient:
    peer = get_peer(peer_node_id)
    if not peer:
        raise PeerError("unpaired_peer", f"no paired peer {peer_node_id}")
    return PeerClient(peer)


# ---------------------------------------------------------------------------
# Session handoff — leases, bundle build, staged import
# ---------------------------------------------------------------------------

MAX_BUNDLE_BYTES = 256 * 1024 * 1024
_SESSION_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,127}$")


def encode_project_slug(path: str) -> str:
    """Claude Code's project-directory encoding for a cwd.

    Mirror of server._encode_project_slug — every non-alphanumeric char
    becomes '-'. Duplicated here (one regex) so the pure handoff layer has
    no server.py dependency; keep the two in sync.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def leases_dir() -> Path:
    return federation_dir() / "leases"


def read_lease(session_id: str) -> dict[str, Any] | None:
    if not _SESSION_ID_SAFE_RE.match(session_id or ""):
        return None
    data = _read_json(leases_dir() / f"{session_id}.json", None)
    return data if isinstance(data, dict) and data.get("owner_node") else None


def lease_owner(session_id: str) -> str | None:
    lease = read_lease(session_id)
    return lease.get("owner_node") if lease else None


def write_lease(session_id: str, owner_node: str, *, transfer_id: str = "",
                transcript_sha: str = "", note: str = "") -> dict[str, Any]:
    """Record who owns a session after a handoff. Keeps an audit history so
    force-takeovers and return handoffs are reconstructable."""
    if not _SESSION_ID_SAFE_RE.match(session_id or ""):
        raise ValueError(f"unsafe session id {session_id!r}")
    with _STATE_LOCK:
        existing = _read_json(leases_dir() / f"{session_id}.json", None)
        history = existing.get("history", []) if isinstance(existing, dict) else []
        history.append({
            "owner_node": owner_node,
            "transfer_id": transfer_id,
            "transcript_sha": transcript_sha,
            "note": note,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        lease = {
            "session_id": session_id,
            "owner_node": owner_node,
            "transfer_id": transfer_id,
            "transcript_sha": transcript_sha,
            "acquired_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "history": history[-20:],
        }
        _write_json(leases_dir() / f"{session_id}.json", lease)
        return lease


def rewrite_transcript(raw: bytes, source_cwd: str, dest_cwd: str) -> tuple[bytes, dict[str, Any]]:
    """Rewrite path-bearing transcript METADATA (per-line top-level "cwd")
    from the source repo root to the destination's. Message content is
    history and is left untouched. Returns (new_bytes, audit)."""
    source_cwd = source_cwd.rstrip("/")
    dest_cwd = dest_cwd.rstrip("/")
    rewrites = 0
    foreign_cwds: set[str] = set()
    out_lines: list[bytes] = []
    for line in raw.split(b"\n"):
        if not line.strip():
            out_lines.append(line)
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            out_lines.append(line)
            continue
        changed = False
        if isinstance(obj, dict):
            cwd = obj.get("cwd")
            if isinstance(cwd, str) and cwd:
                if cwd == source_cwd or cwd.startswith(source_cwd + "/"):
                    obj["cwd"] = dest_cwd + cwd[len(source_cwd):]
                    rewrites += 1
                    changed = True
                else:
                    foreign_cwds.add(cwd)
        if changed:
            out_lines.append(json.dumps(obj, ensure_ascii=False).encode())
        else:
            out_lines.append(line)
    audit = {
        "cwd_rewrites": rewrites,
        "source_cwd": source_cwd,
        "dest_cwd": dest_cwd,
        "warnings": sorted(
            f"transcript references a cwd outside the handed-off repo: {c}"
            for c in foreign_cwds
        )[:20],
    }
    return b"\n".join(out_lines), audit


def build_transfer_bundle(*, engine: str, session_id: str, transcript_path: str,
                          source_cwd: str, dest_cwd: str, repo_identity: str,
                          source_node: str, dest_node: str, branch: str | None,
                          commit: str | None,
                          sidecars: dict[str, Any] | None = None,
                          rewrite_from: str | None = None,
                          rewrite_to: str | None = None) -> dict[str, Any]:
    """Build a session-transfer bundle: versioned manifest + file bytes.

    Only the native transcript travels as a file; CCC sidecar values (title,
    model override, parent link) ride in the manifest and are applied to the
    destination's own state files on import. Never repository contents.

    ``rewrite_from``/``rewrite_to`` are the path prefixes swapped inside the
    transcript (default: the session cwds). Pass the repo roots when the
    session lived in a subdirectory or worktree so every in-repo cwd maps."""
    if engine != "claude":
        raise PeerError(
            "unsupported_capability",
            f"engine {engine!r} has no safe native-store migration yet — "
            "orchestrate it through its owning CCC instead",
        )
    if not _SESSION_ID_SAFE_RE.match(session_id or ""):
        raise ValueError(f"unsafe session id {session_id!r}")
    raw = Path(transcript_path).read_bytes()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError(f"transcript exceeds bundle cap ({len(raw)} bytes)")
    rewritten, audit = rewrite_transcript(
        raw, rewrite_from or source_cwd, rewrite_to or dest_cwd)
    files = {"transcript.jsonl": rewritten}
    manifest = {
        "manifest_version": TRANSFER_MANIFEST_VERSION,
        "transfer_id": str(uuid.uuid4()),
        "engine": engine,
        "session_id": session_id,
        "source_node": source_node,
        "dest_node": dest_node,
        "repo_identity": repo_identity,
        "source_cwd": source_cwd,
        "dest_cwd": dest_cwd,
        "branch": branch,
        "commit": commit,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rewrites": audit,
        "sidecars": sidecars or {},
        "files": [
            {
                "name": name,
                "role": "transcript" if name == "transcript.jsonl" else "sidecar",
                "bytes": len(data),
                "sha256": _sha256_bytes(data),
            }
            for name, data in files.items()
        ],
    }
    return {"manifest": manifest, "files": files}


def stage_and_import_bundle(manifest: dict[str, Any], files: dict[str, bytes],
                            *, projects_root: Path,
                            allow_overwrite: bool = False) -> dict[str, Any]:
    """Import a transfer bundle on the destination node.

    Everything lands in a staging directory first and is validated there
    (manifest schema, hashes, size, name safety); only then is the transcript
    renamed atomically into ~/.claude/projects/<dest-slug>/. A failed import
    leaves any existing destination session untouched.
    """
    problems = validate_transfer_manifest(manifest)
    if problems:
        return {"ok": False, "error": "invalid_manifest", "problems": problems}
    session_id = manifest["session_id"]
    if not _SESSION_ID_SAFE_RE.match(session_id):
        return {"ok": False, "error": "invalid_manifest",
                "problems": [f"unsafe session id {session_id!r}"]}
    declared = {f["name"]: f for f in manifest["files"]}
    if set(declared) != set(files):
        return {"ok": False, "error": "invalid_manifest",
                "problems": ["file set does not match manifest"]}
    total = 0
    for name, data in files.items():
        total += len(data)
        if len(data) != declared[name]["bytes"]:
            return {"ok": False, "error": "hash_mismatch",
                    "problems": [f"{name}: size mismatch"]}
        if _sha256_bytes(data) != declared[name]["sha256"]:
            return {"ok": False, "error": "hash_mismatch",
                    "problems": [f"{name}: sha256 mismatch"]}
    if total > MAX_BUNDLE_BYTES:
        return {"ok": False, "error": "invalid_manifest",
                "problems": [f"bundle too large ({total} bytes)"]}

    # The destination slug is derived by encoding dest_cwd — the encoding
    # strips every path separator, so the target can never escape
    # projects_root regardless of what the manifest claims.
    dest_slug = encode_project_slug(manifest["dest_cwd"])
    dest_dir = projects_root / dest_slug
    dest_transcript = dest_dir / f"{session_id}.jsonl"
    existing_sha = None
    if dest_transcript.exists():
        existing_sha = _sha256_bytes(dest_transcript.read_bytes())
        incoming_sha = declared["transcript.jsonl"]["sha256"]
        if existing_sha == incoming_sha:
            # Retried import of identical content — idempotent success.
            return {
                "ok": True,
                "already_present": True,
                "session_id": session_id,
                "transfer_id": manifest["transfer_id"],
                "transcript_path": str(dest_transcript),
                "transcript_sha256": existing_sha,
                "dest_slug": dest_slug,
            }
        if not allow_overwrite:
            return {
                "ok": False,
                "error": "session_exists",
                "existing_sha256": existing_sha,
                "detail": "destination already has this session with different "
                          "content; divergence review required",
            }

    staging = federation_dir() / "staging" / manifest["transfer_id"]
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for name, data in files.items():
            target = staging / name
            # names were validated (no abs, no ..) but belt-and-braces:
            if not str(target.resolve()).startswith(str(staging.resolve())):
                return {"ok": False, "error": "invalid_manifest",
                        "problems": [f"{name}: escapes staging"]}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        # Re-verify from disk, then activate atomically.
        staged_transcript = staging / "transcript.jsonl"
        if _sha256_bytes(staged_transcript.read_bytes()) != declared["transcript.jsonl"]["sha256"]:
            return {"ok": False, "error": "hash_mismatch",
                    "problems": ["staged transcript re-verify failed"]}
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staged_transcript, dest_transcript)
        imported_sha = declared["transcript.jsonl"]["sha256"]
        return {
            "ok": True,
            "session_id": session_id,
            "transfer_id": manifest["transfer_id"],
            "transcript_path": str(dest_transcript),
            "transcript_sha256": imported_sha,
            "replaced_existing_sha256": existing_sha,
            "dest_slug": dest_slug,
        }
    finally:
        try:
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Session-transfer manifest (handoff contract)
# ---------------------------------------------------------------------------

TRANSFER_MANIFEST_VERSION = 1

_MANIFEST_REQUIRED = (
    "manifest_version",
    "transfer_id",
    "engine",
    "session_id",
    "source_node",
    "dest_node",
    "repo_identity",
    "source_cwd",
    "dest_cwd",
    "files",
)


def validate_transfer_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty = valid)."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    for key in _MANIFEST_REQUIRED:
        if key not in manifest:
            problems.append(f"missing field: {key}")
    if manifest.get("manifest_version") not in (TRANSFER_MANIFEST_VERSION,):
        problems.append(f"unsupported manifest_version: {manifest.get('manifest_version')!r}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        problems.append("files must be a non-empty list")
    else:
        for i, f in enumerate(files):
            if not isinstance(f, dict):
                problems.append(f"files[{i}] is not an object")
                continue
            for k in ("name", "role", "bytes", "sha256"):
                if k not in f:
                    problems.append(f"files[{i}] missing {k}")
            name = str(f.get("name") or "")
            if name.startswith("/") or ".." in name.split("/"):
                problems.append(f"files[{i}].name escapes the bundle: {name!r}")
    return problems
