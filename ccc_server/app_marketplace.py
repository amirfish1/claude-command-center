"""Opt-in, opaque-origin apps. No package scripts or arbitrary API forwarding."""
import argparse
import contextlib
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

PERMISSIONS = {
    "sessions.read": "Read live session IDs, titles, engines and states (no transcripts or paths)",
    "queues.read": "Read queue item IDs, titles and statuses (no bodies or paths)",
}
RESERVED = {"sessions", "queues", "morning", "allowances", "decision-inbox",
            "decision-guide", "spawn-ledger", "savings", "pipeline-canvas"}
MARKER = ".ccc-marketplace"
MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 500
MAX_DEPTH = 16
MAX_PATH_BYTES = 1024
PREVIEW_TTL = 900
MAX_MANIFEST_BYTES = 16 * 1024
MAX_GIT_FILE_BYTES = 16 * 1024 * 1024
MAX_GIT_TREE_BYTES = 1024 * 1024
VCS_METADATA = {".git", ".hg", ".svn"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css",
         ".js": "application/javascript", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".ico": "image/x-icon", ".webp": "image/webp"}


def app_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", value):
        raise ValueError("Invalid app id (use 1–40 lowercase letters, digits, _ or -)")
    if value in RESERVED:
        raise ValueError("App id is reserved")
    return value


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlinks are not allowed")
    return path


def strict_json(raw):
    """Ambiguous duplicate fields must never hide the permissions being reviewed."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Invalid JSON constant")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc


def metadata_text(value, limit, field):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError(f"{field} must be a nonempty string (maximum {limit})")
    # Consent text must not hide/reorder permission lines or break UTF-8 output.
    if re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]", value):
        raise ValueError("Metadata cannot contain control or surrogate characters")
    return value


def manifest_data(data):
    if not isinstance(data, dict):
        raise ValueError("app.json must be an object")
    if set(data) - {"id", "label", "description", "permissions", "icon"}:
        raise ValueError("Unknown manifest field")
    app_id(data.get("id"))
    if "icon" in data:
        metadata_text(data["icon"], 8, "icon")
    for key, limit in (("label", 80), ("description", 1000)):
        metadata_text(data.get(key), limit, key)
    perms = data.get("permissions", [])
    if (not isinstance(perms, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in perms)
            or len(set(perms)) != len(perms)):
        raise ValueError("Unknown or duplicate permissions")
    return {"id": data["id"], "label": data["label"],
            "description": data["description"], "permissions": perms,
            "icon": data.get("icon", "▧")}


def manifest(folder):
    with (folder / "app.json").open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("app.json exceeds 16 KiB")
    data = manifest_data(strict_json(raw))
    if not (folder / "index.html").is_file():
        raise ValueError("index.html is required")
    return data


def tree_digest(folder, ignore_vcs=False):
    """Bounded validation; reject links, devices, sockets and escaped paths."""
    no_links(folder)
    digest = hashlib.sha256()
    count = total = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        if ignore_vcs:
            dirs[:] = [name for name in dirs if name not in VCS_METADATA]
            files = [name for name in files if name not in VCS_METADATA]
        for name in sorted(dirs + files):
            target = Path(root) / name
            relative = target.relative_to(folder)
            if len(relative.parts) > MAX_DEPTH or len(relative.as_posix().encode()) > MAX_PATH_BYTES:
                raise ValueError("App path is too deep or too long")
            mode = target.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError("Only regular files and directories are allowed")
            count += 1
            if count > MAX_FILES:
                raise ValueError("App has too many files")
            if stat.S_ISREG(mode):
                total += target.stat().st_size
                if total > MAX_BYTES:
                    raise ValueError("App exceeds 10 MiB")
                rel = target.relative_to(folder).as_posix().encode()
                data = target.read_bytes()
                digest.update(len(rel).to_bytes(4, "big") + rel)
                digest.update(len(data).to_bytes(8, "big") + data)
        dirs.sort()
    return digest.hexdigest()


def git_source(source):
    if not isinstance(source, str) or not 1 <= len(source) <= 2048 or any(ord(c) < 33 for c in source):
        raise ValueError("Invalid HTTPS Git URL")
    url = urllib.parse.urlsplit(source)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.fragment or "\\" in source):
        raise ValueError("Use an HTTPS Git URL without credentials or fragment")
    # Force validation of malformed ports, even before invoking Git.
    _ = url.port
    return source


def git_environment():
    # A package's .gitattributes must not invoke filters from the user's Git
    # configuration. Public HTTPS installs do not inherit helpers or rewrites.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_LFS_SKIP_SMUDGE="1")
    return env


# Apply process limits in a separate interpreter, never preexec_fn in the
# threaded HTTP server. Git helpers inherit both this limit and process group.
_GIT_RUNNER = """import os, resource, signal, sys
limit = int(sys.argv[1])
hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]
if hard != resource.RLIM_INFINITY:
    limit = min(limit, hard)
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
os.execvpe('git', ['git', *sys.argv[2:]], os.environ)
"""


def run_git(args, output=None, file_limit=MAX_GIT_FILE_BYTES, timeout=60):
    command = [sys.executable, "-c", _GIT_RUNNER, str(file_limit),
               "-c", "core.hooksPath=/dev/null", "-c", "http.followRedirects=false",
               "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "transfer.unpackLimit=0", "-c", "fetch.unpackLimit=0",
               "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=output if output is not None else subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=git_environment(), start_new_session=True)
    try:
        result = process.wait(timeout=timeout)
    except BaseException as exc:
        # A timed-out Git transport/index-pack must not keep writing after its
        # parent exits. Kill the entire private group, then reap the parent.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(["git", *args], timeout) from None
        raise
    if result:
        raise subprocess.CalledProcessError(result, ["git", *args])


def inspect_git_tree(folder):
    """Reject oversized trees and links before Git materializes working files."""
    with tempfile.TemporaryFile(dir=folder.parent) as output:
        run_git(["-C", str(folder), "ls-tree", "-r", "--full-tree", "-l", "-z", "HEAD"],
                output=output, file_limit=MAX_GIT_TREE_BYTES)
        output.seek(0)
        raw = output.read(MAX_GIT_TREE_BYTES + 1)
    if len(raw) > MAX_GIT_TREE_BYTES or (raw and not raw.endswith(b"\0")):
        raise ValueError("Git tree metadata exceeds its limit")
    nodes = set()
    total = 0
    files = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, _object_id, size = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError("Only regular files are allowed in Git packages")
        name = raw_name.decode("utf-8")
        parts = name.split("/")
        if ("\\" in name or ":" in name or "\x00" in name
                or any(part in ("", ".", "..", ".git") for part in parts)):
            raise ValueError("Invalid Git package path")
        if len(parts) > MAX_DEPTH or len(raw_name) > MAX_PATH_BYTES:
            raise ValueError("App path is too deep or too long")
        nodes.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
        if len(nodes) > MAX_FILES:
            raise ValueError("App has too many files")
        total += int(size)
        if total > MAX_BYTES:
            raise ValueError("App exceeds 10 MiB")
        files.add(name)
    if not {"app.json", "index.html"}.issubset(files):
        raise ValueError("Git package must contain app.json and index.html at its root")
    return {"entries": len(nodes), "bytes": total}


def checkout_git_tree(folder):
    inspect_git_tree(folder)
    # Built-in Git attributes can expand bytes even with external filters off.
    # Export first, then enforce expanded ZIP limits before writing app files.
    exported = folder.with_name("exported")
    with tempfile.NamedTemporaryFile(dir=folder.parent, suffix=".zip") as archive:
        run_git(["-C", str(folder), "archive", "--format=zip", "HEAD"], output=archive)
        unpack_zip(Path(archive.name), exported, archive_limit=MAX_GIT_FILE_BYTES)
    shutil.rmtree(folder)
    exported.rename(folder)


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        git_source(newurl)  # Same HTTPS/no-credentials/no-fragment URL policy.
        return super().redirect_request(request, response, code, message, headers, newurl)


def validate_index(data, remote=False):
    """The runtime and contributor validator share the same catalog contract."""
    if (not isinstance(data, dict) or set(data) != {"apps"}
            or not isinstance(data["apps"], list) or len(data["apps"]) > 100):
        raise ValueError("Invalid app index")
    rows = []
    seen = set()
    for row in data["apps"]:
        if not isinstance(row, dict) or set(row) != {"id", "name", "description", "permissions", "source"}:
            raise ValueError("Invalid index entry fields")
        ident = app_id(row["id"])
        if ident in seen:
            raise ValueError("Duplicate app id in index")
        seen.add(ident)
        for key, limit in (("name", 80), ("description", 1000), ("source", 2048)):
            metadata_text(row[key], limit, key)
        perms = row["permissions"]
        if (not isinstance(perms, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in perms)
                or len(set(perms)) != len(perms)):
            raise ValueError("Unknown or duplicate index permission")
        source = row["source"]
        if source.startswith("https://"):
            git_source(source)
        elif remote or not re.fullmatch(r"examples/apps/[a-z0-9_-]{1,40}", source):
            raise ValueError("Use an HTTPS Git URL or a bundled examples/apps/<id> folder")
        rows.append(dict(row))
    return rows


def unpack_zip(archive, folder, archive_limit=None):
    """Validate every member before writing, then stream with an actual byte cap."""
    if archive.stat().st_size > (MAX_BYTES if archive_limit is None else archive_limit):
        raise ValueError("Archive exceeds its size limit")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > MAX_FILES:
            raise ValueError("App has too many files")
        seen = set()
        nodes = set()
        total = 0
        for entry in entries:
            name = entry.orig_filename
            parts = name.rstrip("/").split("/")
            if (not name or "\\" in name or "\x00" in name or ":" in name
                    or any(part in ("", ".", "..") for part in parts)):
                raise ValueError("Invalid archive path")
            if len(parts) > MAX_DEPTH or len(name.encode()) > MAX_PATH_BYTES:
                raise ValueError("App path is too deep or too long")
            # ZIPs can omit directory records; those still consume the entry budget.
            nodes.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
            if len(nodes) > MAX_FILES:
                raise ValueError("App has too many files")
            key = "/".join(parts)
            if key in seen:
                raise ValueError("Duplicate archive path")
            seen.add(key)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("Only regular files and directories are allowed")
            if kind == stat.S_IFDIR and not entry.is_dir():
                raise ValueError("Invalid archive directory")
            if entry.flag_bits & 1:
                raise ValueError("Encrypted archives are unsupported")
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError("Only stored or deflated ZIP entries are supported")
            total += entry.file_size
            if total > MAX_BYTES:
                raise ValueError("App exceeds 10 MiB")
        folder.mkdir()
        total = 0
        for entry in entries:
            target = folder.joinpath(*entry.filename.rstrip("/").split("/"))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(entry) as source, target.open("xb") as output:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("App exceeds 10 MiB")
                    output.write(chunk)


class Marketplace:
    def __init__(self, state, repo):
        self.state = Path(state)
        self.repo = Path(repo)
        self.apps = self.state / "apps"
        self.store = self.state / "app-marketplace"

    def _setup(self):
        for directory in (self.apps, self.store, self.store / "staged", self.store / "grants"):
            no_links(directory)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def locked(self):
        self._setup()
        lock = no_links(self.store / "lock")
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _grant_path(self, ident):
        return no_links(self.store / "grants" / (app_id(ident) + ".json"))

    def isolated(self, ident):
        # Tombstones and the in-package marker keep missing/corrupt grants
        # from silently promoting downloaded code into a trusted local app.
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", ident):
            return False
        return ((self.store / "grants" / (ident + ".json")).exists()
                or (self.apps / ident / MARKER).exists())

    def grant(self, ident):
        data = strict_json(self._grant_path(ident).read_text())
        if not isinstance(data, dict) or data.get("installed") is not True:
            raise PermissionError("App is not installed")
        generation = data.get("generation")
        if (data.get("id") != ident or not isinstance(generation, str)
                or not re.fullmatch(r"[0-9a-f]{32}", generation)):
            raise PermissionError("Invalid app grant")
        meta = manifest_data({key: value for key, value in data.items()
                              if key not in ("installed", "generation")})
        return {**meta, "installed": True, "generation": generation}

    def _write_grant(self, ident, data):
        dest = self._grant_path(ident)
        fd, filename = tempfile.mkstemp(dir=dest.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
            os.replace(filename, dest)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def prepare(self, source):
        if not isinstance(source, str) or not source or len(source) > 2048:
            raise ValueError("Supply a Git HTTPS URL or local folder or ZIP")
        with self.locked():
            self._prune_staging()
            token = secrets.token_hex(24)
            staging = self.store / "staged" / token
            staging.mkdir(mode=0o700)
        folder = staging / "app"
        try:
            if source.startswith("https://"):
                git_source(source)
                run_git(["clone", "--depth", "1", "--single-branch", "--no-tags",
                         "--no-checkout", "--no-recurse-submodules", "--", source, str(folder)])
                checkout_git_tree(folder)
            else:
                original = no_links(Path(source).expanduser())
                if original.is_file() and original.suffix.lower() == ".zip":
                    unpack_zip(original, folder)
                elif original.is_dir():
                    # Validate before copying, then validate the actual staged bytes.
                    tree_digest(original, ignore_vcs=True)
                    shutil.copytree(original, folder, symlinks=True,
                                    ignore=shutil.ignore_patterns(*VCS_METADATA))
                else:
                    raise ValueError("Source must be a local folder, ZIP or HTTPS Git URL")
            tree_digest(folder)
            meta = manifest(folder)
            if (self.apps / meta["id"]).exists():
                raise ValueError("App id already exists; remove it before reinstalling")
            (folder / MARKER).write_text("opaque-origin-v1\n")
            digest = tree_digest(folder)
            receipt = {"manifest": meta, "digest": digest, "created": time.time()}
            (staging / "receipt.json").write_text(json.dumps(receipt))
            return {"ok": True, "token": token, **meta, "permission_descriptions": PERMISSIONS}
        except Exception:
            shutil.rmtree(staging)
            raise

    def _prune_staging(self):
        """Bound cleanup work; active prepares are younger than the clone timeout."""
        cutoff = time.time() - PREVIEW_TTL
        with os.scandir(self.store / "staged") as entries:
            for index, entry in enumerate(entries):
                if index >= 100:
                    break
                if (re.fullmatch(r"[0-9a-f]{48}", entry.name)
                        and entry.is_dir(follow_symlinks=False)
                        and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                    shutil.rmtree(entry.path)

    def staged(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
            raise ValueError("Invalid installation token")
        return no_links(self.store / "staged" / token)

    def cancel(self, token):
        with self.locked():
            folder = self.staged(token)
            if folder.exists():
                shutil.rmtree(folder)
        return {"ok": True}

    def install(self, token, consent):
        if consent is not True:
            raise PermissionError("Explicit permission consent is required")
        with self.locked():
            staging = self.staged(token)
            receipt = json.loads((staging / "receipt.json").read_text())
            folder = staging / "app"
            if time.time() - receipt["created"] > PREVIEW_TTL:
                raise ValueError("Installation preview expired; preview again")
            if tree_digest(folder) != receipt["digest"] or manifest(folder) != receipt["manifest"]:
                raise ValueError("App changed after permission preview")
            meta = receipt["manifest"]
            dest = no_links(self.apps / meta["id"])
            if dest.exists():
                raise ValueError("App id already exists")
            # Record isolation BEFORE the folder becomes visible to HTTP.
            self._write_grant(meta["id"], {**meta, "installed": True,
                                         "generation": secrets.token_hex(16)})
            folder.rename(dest)
            shutil.rmtree(staging)
            return {"ok": True, "id": meta["id"]}

    def remove(self, ident):
        ident = app_id(ident)
        with self.locked():
            if not self.isolated(ident):
                raise ValueError("App is not a marketplace installation")
            try:
                grant = self.grant(ident)
            except (ValueError, OSError, PermissionError):
                # Corrupt/missing grants and a previous failed archive move
                # must still be removable. The tombstone always fails closed.
                grant = {"id": ident}
            grant["installed"] = False
            self._write_grant(ident, grant)
            folder = no_links(self.apps / ident)
            if folder.exists():
                graveyard = no_links(self.apps / ".removed")
                graveyard.mkdir(exist_ok=True)
                folder.rename(graveyard / (ident + "-" + secrets.token_hex(8)))
        return {"ok": True}

    def installed(self):
        result = []
        if (self.store / "grants").is_dir():
            for record in sorted((self.store / "grants").glob("*.json")):
                try:
                    grant = self.grant(record.stem)
                    if (self.apps / record.stem / "index.html").is_file():
                        result.append(grant)
                except (ValueError, OSError, PermissionError):
                    continue
        return result

    def bridge(self, ident, generation, method, callbacks):
        grant = self.grant(ident)
        if (generation != grant["generation"] or method not in PERMISSIONS
                or method not in grant["permissions"]):
            raise PermissionError("Permission denied")
        # Closed dispatch table: no URLs, HTTP verbs, paths or user arguments.
        return callbacks[method]()

    def index(self):
        configured = os.environ.get("CCC_APPS_INDEX_URL")
        if configured:
            git_source(configured)
            opener = urllib.request.build_opener(HTTPSRedirects())
            with opener.open(configured, timeout=10) as response:
                raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError("App index is too large")
            data = strict_json(raw)
        else:
            data = strict_json((self.repo / "apps-index.json").read_text())
        rows = validate_index(data, remote=bool(configured))
        installed = {row["id"] for row in self.installed()}
        for row in rows:
            row["installed"] = row["id"] in installed
        return {"ok": True, "apps": rows, "permission_descriptions": PERMISSIONS}

    def index_source(self, ident):
        row = next((r for r in self.index()["apps"] if r["id"] == app_id(ident)), None)
        if row is None:
            raise ValueError("Unknown index app")
        source = row["source"]
        if not source.startswith("https://"):
            target = no_links(self.repo / source)
            target.resolve().relative_to((self.repo / "examples" / "apps").resolve())
            source = str(target)
        return source


def sandbox_csp(ident, host):
    # Paths, not 'self': even script/image loads cannot address arbitrary APIs.
    if not re.fullmatch(r"[a-zA-Z0-9.\-:\[\]]+", host):
        raise ValueError("Invalid host")
    route = f"{host}/marketplace-content/{app_id(ident)}/"
    asset = f"http://{route} https://{route}"
    return ("sandbox allow-scripts; default-src 'none'; "
            f"script-src 'unsafe-inline' {asset}; style-src 'unsafe-inline' {asset}; "
            f"img-src data: {asset}; font-src {asset}; connect-src 'none'; "
            "frame-src 'none'; worker-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'")


def send_bytes(handler, body, content_type, csp=None):
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    if csp:
        handler.send_header("Content-Security-Policy", csp)
    handler.end_headers()
    handler.wfile.write(body)


def host_page(market, ident):
    grant = market.grant(ident)
    config = json.dumps({"id": ident, "permissions": grant["permissions"],
                         "generation": grant["generation"]}).replace("<", "\\u003c")
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Sandboxed CCC app</title><style>body{margin:0;background:#0f1115;color:#eee;"
            "font:14px system-ui;display:flex;flex-direction:column;height:100vh;height:100dvh}"
            "header{padding:12px;overflow-wrap:anywhere;flex:none}"
            "iframe{width:100%;min-height:0;flex:1;border:0}</style>"
            f"<header>{html.escape(grant['label'])} · Sandboxed app · "
            "<a href='/applications' style='color:#5ac8fa'>Applications</a></header>"
            f"<script id=app-config type=application/json>{config}</script>"
            "<iframe id=app-frame sandbox='allow-scripts' title='Sandboxed app'></iframe>"
            "<script src='/static/app-marketplace-host.js'></script>")


def handle_get(handler, path, market):
    """Called before legacy app routes; return whether the request was handled."""
    if path == "/api/app-marketplace/index":
        try:
            handler.send_json(market.index())
        except (ValueError, OSError):
            handler.send_json({"error": "Cannot load app index"}, 400)
        return True
    if path.startswith("/view/"):
        ident = path[len("/view/"):]
        if not market.isolated(ident):
            return False
        try:
            send_bytes(handler, host_page(market, ident).encode(), "text/html; charset=utf-8")
        except (ValueError, OSError, PermissionError):
            handler.send_json({"error": "App is unavailable"}, 403)
        return True
    if not path.startswith(("/marketplace-content/", "/app-static/")):
        return False
    legacy = path.startswith("/app-static/")
    rel = urllib.parse.unquote(path.split("/", 2)[2])
    parts = rel.split("/")
    # Legacy assets must be clamped to a SINGLE app. This also closes an
    # alias through another app/../installed-app/file.svg or .removed/.
    if len(parts) < 2 or any(p in ("", ".", "..") for p in parts) or "\\" in rel:
        handler.send_json({"error": "Invalid app asset path"}, 400)
        return True
    ident = parts[0]
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", ident):
        handler.send_json({"error": "Invalid app id"}, 400)
        return True
    if not market.isolated(ident):
        if legacy:
            try:
                no_links(market.apps.joinpath(*parts))
            except ValueError:
                handler.send_json({"error": "Symlink asset refused"}, 400)
                return True
            return False
        handler.send_json({"error": "Unknown marketplace app"}, 404)
        return True
    try:
        market.grant(ident)
        folder = no_links(market.apps / app_id(ident))
        target = no_links(folder.joinpath(*parts[1:]))
        target.resolve().relative_to(folder.resolve())
        content_type = TYPES.get(target.suffix)
        if content_type is None or not target.is_file():
            raise ValueError("Unknown asset")
        send_bytes(handler, target.read_bytes(), content_type, sandbox_csp(ident, handler.headers.get("Host", "")))
    except (ValueError, OSError, PermissionError):
        handler.send_json({"error": "App asset unavailable"}, 404)
    return True


def handle_post(handler, path, market, callbacks):
    if not path.startswith("/api/app-marketplace/"):
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 8192:
            raise ValueError("Invalid body size")
        payload = strict_json(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Expected object")
        action = path.rsplit("/", 1)[-1]
        if action == "prepare":
            ident = payload.get("id")
            result = market.prepare(market.index_source(ident))
            if result["id"] != ident:
                market.cancel(result["token"])
                raise ValueError("Catalog id does not match package")
        elif action == "install":
            result = market.install(payload.get("token"), payload.get("consent"))
        elif action == "cancel":
            result = market.cancel(payload.get("token"))
        elif action == "remove":
            result = market.remove(payload.get("id"))
        elif action == "bridge":
            result = {"ok": True, "result": market.bridge(payload.get("id"), payload.get("generation"),
                                                          payload.get("method"), callbacks)}
        else:
            raise ValueError("Unknown marketplace action")
        handler.send_json(result)
    except PermissionError:
        handler.send_json({"ok": False, "error": "Permission denied"}, 403)
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError):
        handler.send_json({"ok": False, "error": "Invalid or unavailable app; preview again before installing"}, 400)
    return True


def cli(argv, state=None, repo=None):
    parser = argparse.ArgumentParser(prog="ccc app")
    sub = parser.add_subparsers(dest="action", required=True)
    install = sub.add_parser("install", help="Install a sandboxed app from a folder, ZIP or HTTPS Git URL")
    install.add_argument("source")
    install.add_argument("--yes", action="store_true", help="Consent to the displayed permissions without prompting")
    sub.add_parser("list")
    sub.add_parser("remove").add_argument("id")
    args = parser.parse_args(argv)
    market = Marketplace(state or Path.home() / ".claude/command-center",
                         repo or Path(__file__).resolve().parents[1])
    try:
        if args.action == "list":
            for row in market.installed():
                print(f"{row['id']}\t{row['label']}\t{', '.join(row['permissions']) or 'no permissions'}")
        elif args.action == "remove":
            market.remove(args.id)
            print("Removed " + args.id)
        else:
            preview = market.prepare(args.source)
            try:
                print(f"Install {preview['label']} ({preview['id']}) in an opaque-origin sandbox.")
                print("Requested permissions:")
                for permission in preview["permissions"]:
                    print(f"  {permission}: {PERMISSIONS[permission]}")
                if not preview["permissions"]:
                    print("  None")
                if not args.yes and input("Grant these permissions and install? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Cancelled")
                    return 1
                market.install(preview["token"], True)
                print("Installed " + preview["id"])
            finally:
                market.cancel(preview["token"])
        return 0
    except (ValueError, OSError, EOFError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print("App operation failed: " + str(exc))
        return 1
