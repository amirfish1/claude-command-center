# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Browser sidekick — the right-rail Browser tab's server half.

Two jobs:

1. Detect the local dev-server URLs a session is serving, for
   GET /api/session/<sid>/dev-urls. Signals, strongest first:
     - a TCP port LISTENing in the session's own process tree,
     - a port LISTENing in a process whose cwd sits inside the session's
       working directory (``npm run dev &`` reparents to launchd, so the
       tree alone misses the common case),
     - a loopback URL printed in the transcript (Vite's "Local:" line,
       "Server running at http://localhost:3000", curl commands).
   Transcript URLs are kept even when nothing listens any more; the UI shows
   them as stale so the user can see what the session last served.

2. A frame-friendly loopback proxy for POST /api/browser/proxy. Dev servers
   such as Django (X-Frame-Options: DENY) or Rails (SAMEORIGIN) refuse to be
   framed by the dashboard. The proxy runs one listener per target on an
   ephemeral 127.0.0.1 port and forwards everything, WebSocket upgrades
   included (HMR keeps working), while replacing frame-blocking headers with
   a loopback-only ``frame-ancestors`` policy.

Perf: this is single-session work, done only for the conversation the user
has open. Transcript scans resume from the last byte read, keyed by
(path, size, mtime); the lsof/ps snapshots are shared across sessions and
cached for a few seconds, so a poll never forks per row.

Security: the proxy and the probe only ever target loopback hosts, and the
proxy binds 127.0.0.1 regardless of CCC_BIND_HOST.
"""

from __future__ import annotations

import http.client
import os
import re
import select
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")

# Loopback URL with an explicit port. The path stops at whitespace, quotes,
# JSON escapes (a backslash starts `\n`, `\"`), brackets and ANSI escapes.
_URL_RE = re.compile(
    r"\bhttps?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):(\d{2,5})"
    r"(/[^\s\"'`\\<>(){}\[\]\x1b|^]*)?",
    re.IGNORECASE,
)

# Tail budget for the first scan of a transcript; later scans read only the
# bytes appended since.
_FIRST_SCAN_BYTES = 4 * 1024 * 1024
_URL_CAP = 12
_SNAPSHOT_TTL_S = 8.0
# A session counts as a live-process candidate only if its transcript moved
# this recently; older sessions still get transcript + port-open detection.
_LIVE_CANDIDATE_WINDOW_S = 30 * 60
_PID_CACHE_TTL_S = 30.0
_PROBE_TIMEOUT_S = 2.0

_LSOF = next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof", "/bin/lsof") if os.path.exists(p)), "lsof")
_PS = next((p for p in ("/bin/ps", "/usr/bin/ps") if os.path.exists(p)), "ps")
_NETSTAT = next((p for p in ("/usr/sbin/netstat",) if os.path.exists(p)), "")


# ── URL extraction ─────────────────────────────────────────────────────


def normalize_url(url):
    """Canonical form: lowercase loopback host (0.0.0.0 -> localhost), no
    trailing punctuation, no fragment. Returns "" when not loopback+port."""
    m = _URL_RE.match(str(url or "").strip())
    if not m:
        return ""
    parts = urlsplit(m.group(0).rstrip(".,;:!?'\""))
    host = (parts.hostname or "").lower()
    if host in ("0.0.0.0",):
        host = "localhost"
    if host == "::1":
        host = "[::1]"
    try:
        port = int(parts.port or 0)
    except ValueError:
        return ""
    if not (1 <= port <= 65535):
        return ""
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), f"{host}:{port}", path, parts.query, ""))


def extract_urls(text):
    """Loopback URLs in `text`, most recent (last) occurrence first, deduped."""
    seen = {}
    for idx, m in enumerate(_URL_RE.finditer(text or "")):
        url = normalize_url(m.group(0))
        if url:
            seen[url] = idx
    return [u for u, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


# Transcript scan cache: path -> {"size", "mtime", "order": {url: seq}, "seq"}
_scan_cache = {}
_scan_lock = threading.Lock()


def scan_transcript(path):
    """Loopback URLs mentioned in a transcript, most recent first.

    Incremental: an appended-to file is read from the previous end (minus a
    small overlap so a URL split across the boundary is not lost)."""
    try:
        p = Path(path)
        st = p.stat()
    except (OSError, TypeError):
        return []
    if not p.is_file() or p.suffix.lower() not in (".jsonl", ".json", ".log", ".txt", ".md"):
        return []
    key = str(p)
    with _scan_lock:
        entry = _scan_cache.get(key)
        if entry and entry["size"] == st.st_size and entry["mtime"] == st.st_mtime:
            return _ordered(entry)
        if entry and st.st_size > entry["size"]:
            start = max(0, entry["size"] - 512)
        else:
            entry = {"size": 0, "mtime": 0, "order": {}, "seq": 0}
            start = max(0, st.st_size - _FIRST_SCAN_BYTES)
        try:
            with open(p, "rb") as fh:
                fh.seek(start)
                chunk = fh.read(st.st_size - start)
        except OSError:
            return _ordered(entry) if entry["order"] else []
        text = chunk.decode("utf-8", "replace")
        for url in reversed(extract_urls(text)):
            entry["seq"] += 1
            entry["order"][url] = entry["seq"]
        entry["size"] = st.st_size
        entry["mtime"] = st.st_mtime
        _scan_cache[key] = entry
        if len(_scan_cache) > 64:
            _scan_cache.pop(next(iter(_scan_cache)))
        return _ordered(entry)


def _ordered(entry):
    return [u for u, _ in sorted(entry["order"].items(), key=lambda kv: -kv[1])]


# ── Process / socket snapshots (shared, short-TTL) ─────────────────────

_snap_lock = threading.Lock()
_snap = {"ts": 0.0, "listen": {}, "ppid": {}, "cwds": {}}


def _run(argv, timeout=4.0):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return out.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_lsof_listen(output):
    """`lsof -F pn` output -> {pid: set(port)}."""
    result = {}
    pid = None
    for line in (output or "").splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            m = re.search(r":(\d+)$", line[1:])
            if m:
                result.setdefault(pid, set()).add(int(m.group(1)))
    return result


def parse_netstat_listen(output):
    """macOS `netstat -anv -p tcp` -> {pid: set(port)}.

    ~15x cheaper than lsof on a busy Mac. The local address ends in
    `.<port>`; the process column is `<name>:<pid>` (names may contain
    spaces), followed by the hex `state` column."""
    result = {}
    for line in (output or "").splitlines():
        if " LISTEN " not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        m_port = re.search(r"\.(\d+)$", parts[3])
        m_pid = re.search(r":(\d+)\s+[0-9a-fA-F]{5}\s", line)
        if m_port and m_pid:
            result.setdefault(int(m_pid.group(1)), set()).add(int(m_port.group(1)))
    return result


def _listen_map():
    if _NETSTAT and os.uname().sysname == "Darwin":
        out = _run([_NETSTAT, "-anv", "-p", "tcp"])
        if "process:pid" in out:
            return parse_netstat_listen(out)
    return parse_lsof_listen(_run([_LSOF, "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pn"]))


def parse_ps_ppid(output):
    """`ps -A -o pid=,ppid=` output -> {pid: ppid}."""
    result = {}
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                result[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
    return result


def _snapshots(now=None):
    """(listen map, ppid map) — ONE netstat/lsof + ONE ps, cached for a few
    seconds and shared by every session asking."""
    now = time.monotonic() if now is None else now
    with _snap_lock:
        if now - _snap["ts"] < _SNAPSHOT_TTL_S:
            return _snap["listen"], _snap["ppid"]
    listen = _listen_map()
    ppid = parse_ps_ppid(_run([_PS, "-A", "-o", "pid=,ppid="]))
    with _snap_lock:
        # A new snapshot drops memoised cwds: pids are reused over time.
        _snap.update(ts=now, listen=listen, ppid=ppid, cwds={})
    return listen, ppid


def descendants(root_pid, ppid_map):
    """root_pid plus every descendant, from a {pid: ppid} map."""
    children = {}
    for pid, parent in ppid_map.items():
        children.setdefault(parent, []).append(pid)
    out, stack = set(), [int(root_pid)]
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(children.get(pid, ()))
    return out


def _pid_cwds(pids):
    """{pid: cwd} for listening pids. Memoised inside the snapshot window, so
    a poll only forks lsof for pids it has not resolved yet."""
    pids = sorted(int(p) for p in pids)[:64]
    with _snap_lock:
        known = _snap.setdefault("cwds", {})
        missing = [p for p in pids if p not in known]
    if missing:
        fetched = _lsof_cwds(missing)
        with _snap_lock:
            known = _snap.setdefault("cwds", {})
            for p in missing:
                known[p] = fetched.get(p, "")
    with _snap_lock:
        known = _snap.get("cwds", {})
        return {p: known[p] for p in pids if known.get(p)}


def _lsof_cwds(pids):
    out = _run([_LSOF, "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-F", "pn"])
    result, pid = {}, None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            result[pid] = line[1:]
    return result


def _within(child, parent):
    try:
        child = os.path.realpath(child)
        parent = os.path.realpath(parent)
    except (OSError, ValueError):
        return False
    home = os.path.realpath(str(Path.home()))
    # A cwd of $HOME or / would claim every dev server on the machine.
    if parent in ("/", home):
        return False
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def port_open(port, host="127.0.0.1", timeout=0.25):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        if host == "127.0.0.1":
            try:
                with socket.create_connection(("::1", int(port)), timeout=timeout):
                    return True
            except OSError:
                return False
        return False


# ── Detection ───────────────────────────────────────────────────────────


def detect_dev_urls(*, transcript_path=None, pid=None, cwd=None, exclude_ports=(),
                    snapshots=None, pid_cwds=None, is_open=None):
    """Merge the three signals into a ranked list of candidate URLs.

    Each item: {url, port, source, live}. `source` is "process" (listening in
    the session's process tree), "cwd" (listening in a process running inside
    the session's directory) or "transcript". Injectable collaborators keep
    this testable without real processes.
    """
    exclude = {int(p) for p in exclude_ports if p}
    exclude.update(proxy_ports())
    is_open = is_open or port_open
    items, by_port = [], {}

    def add(url, port, source, live):
        if port in exclude or port in by_port:
            return
        item = {"url": url, "port": port, "source": source, "live": bool(live)}
        by_port[port] = item
        items.append(item)

    transcript_urls = scan_transcript(transcript_path) if transcript_path else []
    url_for_port = {}
    for url in transcript_urls:
        port = urlsplit(url).port
        url_for_port.setdefault(port, url)

    if pid or cwd:
        listen, ppid = (snapshots or _snapshots)()
        tree = descendants(pid, ppid) if pid else set()
        if pid:
            for p in sorted(tree):
                for port in sorted(listen.get(p, ())):
                    add(url_for_port.get(port) or f"http://localhost:{port}/", port, "process", True)
        if cwd:
            others = [p for p in listen if p not in tree and listen[p] - set(by_port)]
            cwds = (pid_cwds or _pid_cwds)(others) if others else {}
            for p in sorted(others):
                if cwds.get(p) and _within(cwds[p], cwd):
                    for port in sorted(listen[p]):
                        add(url_for_port.get(port) or f"http://localhost:{port}/", port, "cwd", True)

    for url in transcript_urls:
        port = urlsplit(url).port
        if port in by_port or port in exclude:
            continue
        if len(items) >= _URL_CAP:
            break
        add(url, port, "transcript", is_open(port))

    # Live first, then by signal strength, keeping transcript recency order.
    rank = {"process": 0, "cwd": 1, "transcript": 2}
    items.sort(key=lambda it: (not it["live"], rank[it["source"]]))
    return items[:_URL_CAP]


_pid_cache = {}  # sid -> (monotonic ts, pid or None)


def _session_pid(core, sid, cwd, transcript, now=None):
    """The session's live pid, or None. Gated by candidacy: a transcript
    idle past the window is not looked up at all (session_live_status can
    fork ps), and answers are cached per session for a short TTL."""
    now = time.monotonic() if now is None else now
    try:
        idle = time.time() - os.stat(transcript).st_mtime if transcript else None
    except OSError:
        idle = None
    if idle is not None and idle > _LIVE_CANDIDATE_WINDOW_S:
        return None
    hit = _pid_cache.get(sid)
    if hit and now - hit[0] < _PID_CACHE_TTL_S:
        return hit[1]
    pid = None
    try:
        status = core.session_live_status(sid, cwd) or {}
        if status.get("live") and status.get("pid"):
            pid = int(status["pid"])
    except Exception:
        pid = None
    _pid_cache[sid] = (now, pid)
    if len(_pid_cache) > 256:
        _pid_cache.pop(next(iter(_pid_cache)))
    return pid


def session_dev_urls(session_id, exclude_ports=()):
    """GET /api/session/<sid>/dev-urls body."""
    from ccc_server import core as _core

    sid = str(session_id or "").strip()
    transcript, cwd = "", None
    try:
        transcript = _core.conversation_transcript_path(sid)
    except Exception:
        transcript = ""
    try:
        cwd = _core.find_session_cwd(sid)
    except Exception:
        cwd = None
    pid = _session_pid(_core, sid, cwd, transcript)
    urls = detect_dev_urls(transcript_path=transcript, pid=pid, cwd=cwd,
                           exclude_ports=exclude_ports)
    return {"ok": True, "session_id": sid, "live": bool(pid), "urls": urls}


# ── Frameability probe ─────────────────────────────────────────────────


def is_loopback_url(url):
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def frame_blocked(headers):
    """Why a response would refuse to render in a cross-origin iframe, or ""."""
    xfo = (headers.get("X-Frame-Options") or "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return f"X-Frame-Options: {xfo.upper()}"
    for value in headers.get_all("Content-Security-Policy") or []:
        for directive in value.split(";"):
            parts = directive.strip().split()
            if parts and parts[0].lower() == "frame-ancestors":
                sources = [s.lower() for s in parts[1:]]
                if "*" in sources or any(s.startswith(("http://localhost", "http://127.0.0.1")) for s in sources):
                    continue
                return "CSP frame-ancestors " + " ".join(parts[1:])
    return ""


def probe(url):
    """GET /api/browser/probe — reachability + frameability of a loopback URL."""
    if not is_loopback_url(url):
        return {"ok": True, "url": url, "checked": False, "reason": "not_loopback"}
    parts = urlsplit(url)
    host = "127.0.0.1" if (parts.hostname or "") in ("localhost", "0.0.0.0") else parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": _PROBE_TIMEOUT_S}
    if parts.scheme == "https":
        import ssl
        kwargs["context"] = ssl._create_unverified_context()  # local dev certs
    conn = conn_cls(host, port, **kwargs)
    try:
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        conn.request("GET", path, headers={"Host": parts.netloc, "Accept": "text/html,*/*"})
        resp = conn.getresponse()
        reason = frame_blocked(resp.headers)
        return {"ok": True, "url": url, "checked": True, "reachable": True,
                "status": resp.status, "frame_blocked": bool(reason), "reason": reason}
    except (OSError, http.client.HTTPException) as exc:
        return {"ok": True, "url": url, "checked": True, "reachable": False,
                "frame_blocked": False, "reason": type(exc).__name__}
    finally:
        conn.close()


# ── Frame-friendly loopback proxy ──────────────────────────────────────

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_FRAME_ANCESTORS = "frame-ancestors 'self' http://localhost:* http://127.0.0.1:* http://[::1]:*"
_PROXY_CAP = 8

_proxies = {}  # (scheme, host, port) -> {"server", "port", "ts"}
_proxy_lock = threading.Lock()


def proxy_ports():
    with _proxy_lock:
        return {entry["port"] for entry in _proxies.values()}


def rewrite_csp(value):
    """Replace a CSP's frame-ancestors directive with the loopback policy."""
    kept = [d.strip() for d in (value or "").split(";")
            if d.strip() and d.strip().split()[0].lower() != "frame-ancestors"]
    kept.append(_FRAME_ANCESTORS)
    return "; ".join(kept)


def rewrite_response_headers(headers, target_origins, proxy_origin):
    """[(name, value)] safe to hand the dashboard's iframe."""
    if isinstance(target_origins, str):
        target_origins = (target_origins,)
    out, saw_csp = [], False
    for name, value in headers:
        low = name.lower()
        if low in _HOP_BY_HOP or low == "x-frame-options":
            continue
        if low == "content-security-policy":
            value, saw_csp = rewrite_csp(value), True
        elif low == "location":
            for origin in target_origins:
                if value.startswith(origin):
                    value = proxy_origin + value[len(origin):]
                    break
        out.append((name, value))
    if not saw_csp:
        out.append(("Content-Security-Policy", _FRAME_ANCESTORS))
    return out


def _make_handler(scheme, host, port):
    target_netloc = f"{host}:{port}" if ":" not in host else f"[{host}]:{port}"
    display_host = "localhost" if host in ("127.0.0.1", "localhost") else target_netloc.rsplit(":", 1)[0]
    target_origin = f"{scheme}://{display_host}:{port}"
    connect_host = "127.0.0.1" if host in ("localhost", "0.0.0.0") else host
    target_origins = (target_origin,) + tuple(
        f"{scheme}://{alias}:{port}" for alias in ("localhost", "127.0.0.1", "0.0.0.0")
        if f"{scheme}://{alias}:{port}" != target_origin
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "CCC-browser-proxy"

        def log_message(self, *_args):
            pass

        def _proxy_origin(self):
            return f"http://127.0.0.1:{self.server.server_address[1]}"

        def _request_headers(self):
            proxy_origin = self._proxy_origin()
            headers = {}
            for name, value in self.headers.items():
                low = name.lower()
                if low in ("host", "proxy-connection") or (low in _HOP_BY_HOP and low != "upgrade"):
                    continue
                if low in ("origin", "referer") and value.startswith(proxy_origin):
                    value = target_origin + value[len(proxy_origin):]
                headers[name] = value
            headers["Host"] = f"{display_host}:{port}"
            return headers

        def _tunnel(self):
            """WebSocket (or any Upgrade): forward the head, then pipe bytes."""
            try:
                upstream = socket.create_connection((connect_host, port), timeout=5)
                if scheme == "https":
                    import ssl
                    upstream = ssl._create_unverified_context().wrap_socket(upstream, server_hostname=host)
            except OSError:
                self.send_error(502, "upstream unreachable")
                return
            head = [f"{self.command} {self.path} HTTP/1.1"]
            for name, value in self._request_headers().items():
                head.append(f"{name}: {value}")
            head.append("Connection: Upgrade")
            upstream.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            client = self.connection
            upstream.settimeout(None)
            client.settimeout(None)
            socks = [client, upstream]
            try:
                while True:
                    ready, _, errored = select.select(socks, [], socks, 300)
                    if errored or not ready:
                        break
                    done = False
                    for s in ready:
                        data = s.recv(65536)
                        if not data:
                            done = True
                            break
                        (upstream if s is client else client).sendall(data)
                    if done:
                        break
            except OSError:
                pass
            finally:
                try:
                    upstream.close()
                except OSError:
                    pass
                self.close_connection = True

        def _forward(self):
            if (self.headers.get("Upgrade") or "").strip():
                self._tunnel()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else None
            if scheme == "https":
                import ssl
                conn = http.client.HTTPSConnection(connect_host, port, timeout=60,
                                                   context=ssl._create_unverified_context())
            else:
                conn = http.client.HTTPConnection(connect_host, port, timeout=60)
            try:
                conn.request(self.command, self.path, body=body, headers=self._request_headers())
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                self.send_response(502)
                msg = f"CCC browser proxy: {target_origin} is not answering ({type(exc).__name__}).".encode()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(msg)
                self.close_connection = True
                return
            try:
                self.send_response_only(resp.status, resp.reason)
                for name, value in rewrite_response_headers(resp.getheaders(), target_origins, self._proxy_origin()):
                    self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    while True:
                        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except OSError:
                pass
            finally:
                conn.close()
                self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    return Handler


def ensure_proxy(url):
    """Start (or reuse) a proxy for a loopback URL's origin.

    Returns {"ok", "proxy_url"} where proxy_url keeps the path/query, or
    {"ok": False, "error"} for a non-loopback target."""
    if not is_loopback_url(url):
        return {"ok": False, "error": "only loopback URLs can be proxied"}
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "0.0.0.0":
        host = "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (parts.scheme, host, port)
    with _proxy_lock:
        entry = _proxies.get(key)
        if entry is None:
            if len(_proxies) >= _PROXY_CAP:
                oldest = min(_proxies, key=lambda k: _proxies[k]["ts"])
                old = _proxies.pop(oldest)
                threading.Thread(target=old["server"].shutdown, daemon=True).start()
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(parts.scheme, host, port))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name=f"ccc-browser-proxy-{port}",
                             daemon=True).start()
            entry = {"server": server, "port": server.server_address[1], "ts": 0.0}
            _proxies[key] = entry
        entry["ts"] = time.monotonic()
        proxy_port = entry["port"]
    path = parts.path or "/"
    proxy_url = urlunsplit(("http", f"127.0.0.1:{proxy_port}", path, parts.query, parts.fragment))
    return {"ok": True, "proxy_url": proxy_url, "target": url}


def shutdown_proxies():
    """Tests / shutdown: stop every proxy listener."""
    with _proxy_lock:
        entries = list(_proxies.values())
        _proxies.clear()
    for entry in entries:
        try:
            entry["server"].shutdown()
            entry["server"].server_close()
        except OSError:
            pass
