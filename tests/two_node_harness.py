"""Two-node CCC integration harness.

Boots two fully isolated CCC servers on this machine: each gets its own
temporary HOME (so ~/.claude/command-center, ~/.claude/projects, and every
engine store are separate), its own loopback port, and ephemeral mode so
neither claims the machine-wide port.txt. Peer transport between them is
`loopback` — the same versioned peer protocol production uses over SSH,
minus the tunnel — so cross-node behavior is exercised for real without
touching user data or the network.

Also provides a temporary bare Git "origin" plus per-node clones so
Git-state scenarios (unpublished commits, dirty trees, worktrees, merges)
run against real repositories.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CCC_ROOT = Path(__file__).resolve().parents[1]

GIT_ENV = {
    "GIT_AUTHOR_NAME": "ccc-test",
    "GIT_AUTHOR_EMAIL": "ccc-test@example.test",
    "GIT_COMMITTER_NAME": "ccc-test",
    "GIT_COMMITTER_EMAIL": "ccc-test@example.test",
}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def git(cwd, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, **GIT_ENV},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


class CCCNode:
    """One isolated CCC server process."""

    def __init__(self, name: str, base_dir: Path):
        self.name = name
        self.home = base_dir / f"home-{name}"
        self.home.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.log_path = base_dir / f"server-{name}.log"

    # -- lifecycle ----------------------------------------------------------

    def start(self, extra_env=None):
        # Fleet isolation: never auto-map the host machine's repos (the
        # server's own install dir counts as a known repo) — harness nodes
        # only see repos explicitly mapped by the test.
        state_dir = self.home / ".claude" / "command-center"
        state_dir.mkdir(parents=True, exist_ok=True)
        fleet_cfg = state_dir / "fleet.json"
        if not fleet_cfg.exists():
            fleet_cfg.write_text('{"automap": false}\n')
        env = {
            **os.environ,
            "HOME": str(self.home),
            "PORT": str(self.port),
            "CCC_EPHEMERAL": "1",
            "CCC_SKIP_SKILL_INSTALL": "1",
            "CCC_TELEMETRY_DISABLED": "1",
            "CCC_CHAT_ORCHESTRATOR": "builtin",
        }
        env.pop("CCC_SSH_HOST", None)  # never let dev env leak a remote redirect
        env.update(extra_env or {})
        self._log_fh = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, str(CCC_ROOT / "server.py")],
            cwd=str(CCC_ROOT),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
        )

    def wait_ready(self, timeout=40.0):
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(
                    f"node {self.name} exited rc={self.proc.returncode}: "
                    f"{self.log_tail()}")
            try:
                out = self.get("/api/federation/v1/hello")
                if out.get("node_id"):
                    self.node_id = out["node_id"]
                    return out
            except (urllib.error.URLError, OSError, ValueError) as e:
                last_err = e
            time.sleep(0.25)
        raise RuntimeError(f"node {self.name} not ready after {timeout}s: {last_err}; "
                           f"log: {self.log_tail()}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        try:
            self._log_fh.close()
        except Exception:
            pass

    def log_tail(self, lines=15):
        try:
            return "\n".join(self.log_path.read_text().splitlines()[-lines:])
        except OSError:
            return "<no log>"

    # -- HTTP ---------------------------------------------------------------

    def request(self, method, path, body=None, headers=None, timeout=30.0):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw}

    def get(self, path, **kw):
        status, payload = self.request("GET", path, **kw)
        if status >= 400:
            raise urllib.error.URLError(f"GET {path} -> {status}: {payload}")
        return payload

    def post(self, path, body=None, expect_error=False, **kw):
        status, payload = self.request("POST", path, body=body, **kw)
        if status >= 400 and not expect_error:
            raise AssertionError(f"POST {path} -> {status}: {payload}")
        return status, payload

    # -- state helpers --------------------------------------------------------

    @property
    def state_dir(self) -> Path:
        return self.home / ".claude" / "command-center"

    @property
    def projects_root(self) -> Path:
        return self.home / ".claude" / "projects"

    @property
    def group_chats_dir(self) -> Path:
        return self.home / ".claude" / "group-chats"


class TwoNodeFleet:
    """Two paired CCC nodes plus a temp bare origin and per-node clones."""

    def __init__(self):
        # resolve(): macOS tempdirs live under /var -> /private/var; the
        # server canonicalizes request paths, so the harness must too or
        # every path comparison fails on the symlink.
        self.base = Path(tempfile.mkdtemp(prefix="ccc-two-node-")).resolve()
        self.node_a = CCCNode("a", self.base)
        self.node_b = CCCNode("b", self.base)
        self.origin = self.base / "origin.git"
        self.repo_a = self.node_a.home / "repos" / "demo-app"
        self.repo_b = self.node_b.home / "repos" / "demo-app"

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        self.node_a.start()
        self.node_b.start()
        self.node_a.wait_ready()
        self.node_b.wait_ready()

    def stop(self):
        self.node_a.stop()
        self.node_b.stop()

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    # -- pairing ---------------------------------------------------------------

    def pair(self):
        """Pair A -> B over loopback (reciprocal loopback transport back)."""
        status, payload = self.node_a.post("/api/federation/peers/pair", {
            "transport": {"type": "loopback", "port": self.node_b.port},
        })
        assert payload.get("ok"), f"pairing failed: {payload}"
        return payload["peer"]

    # -- git fixtures -----------------------------------------------------------

    def make_origin_and_clones(self):
        """Bare origin + one clone per node, with one initial pushed commit."""
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)],
                       check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
        # Point the bare repo's HEAD at main so clones get a born HEAD.
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = self.base / "seed"
        seed.mkdir()
        git(self.base, "clone", "-q", str(self.origin), str(seed))
        (seed / "README.md").write_text("# demo-app\n")
        git(seed, "add", "README.md")
        git(seed, "commit", "-q", "-m", "init: seed")
        git(seed, "branch", "-M", "main")
        git(seed, "push", "-q", "origin", "main")
        shutil.rmtree(seed)
        for repo in (self.repo_a, self.repo_b):
            repo.parent.mkdir(parents=True, exist_ok=True)
            git(self.base, "clone", "-q", str(self.origin), str(repo))
        return self.origin

    def commit_on(self, repo: Path, filename: str, content: str, message: str,
                  push: bool = False):
        (repo / filename).write_text(content)
        git(repo, "add", filename)
        git(repo, "commit", "-q", "-m", message)
        if push:
            git(repo, "push", "-q", "origin", "HEAD")
        return git(repo, "rev-parse", "HEAD").stdout.strip()
