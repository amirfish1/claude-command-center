"""OpenSSH Multiplexer Bridge for Claude Command Center.

Manages persistent OpenSSH ControlMaster connections to remote machines,
enabling remote session discovery, process spawning, and log streaming over SSH.
Ported from Hermes bridge pattern and adapted for general CCC engine use.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 35 * 1024 * 1024
IMAGE_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
}


def _expand(path: str) -> str:
    return str(Path(path).expanduser())


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _safe_control_path(user: str, host: str, port: int) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{user}-{host}-{port}")
    return f"/tmp/ccc-ssh-{key}.sock"


def _default_key_path() -> str:
    for cand in ["~/.ssh/id_ed25519", "~/.ssh/id_rsa", "~/.ssh/id_ecdsa"]:
        p = Path(cand).expanduser()
        if p.exists():
            return str(p)
    return "~/.ssh/id_ed25519"


@dataclass(frozen=True)
class BridgeConfig:
    host: str
    user: str
    ssh_port: int
    key_path: str
    control_path: str
    control_persist: str
    connect_timeout: int
    remote_tmp: str

    @classmethod
    def from_env(cls) -> "BridgeConfig | None":
        raw_host = os.environ.get("CCC_SSH_HOST", "").strip()
        if not raw_host:
            return None
        if "@" in raw_host:
            user, host = raw_host.split("@", 1)
        else:
            user = os.environ.get("CCC_SSH_USER") or os.environ.get("USER") or "root"
            host = raw_host
        ssh_port = _env_int("CCC_SSH_PORT", 22)
        key_path = os.environ.get("CCC_SSH_KEY") or _default_key_path()
        control_path = os.environ.get("CCC_SSH_CONTROL_PATH") or _safe_control_path(user, host, ssh_port)
        return cls(
            host=host,
            user=user,
            ssh_port=ssh_port,
            key_path=_expand(key_path),
            control_path=control_path,
            control_persist=os.environ.get("CCC_SSH_CONTROL_PERSIST", "1h"),
            connect_timeout=_env_int("CCC_SSH_CONNECT_TIMEOUT", 10),
            remote_tmp=os.environ.get("CCC_SSH_REMOTE_TMP", "/tmp").rstrip("/") or "/tmp",
        )

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"


class SSHMultiplexer:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self._master_lock = threading.Lock()

    def _ssh_options(self, control_master: str = "auto") -> list[str]:
        opts = [
            "-p",
            str(self.config.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ControlPath={self.config.control_path}",
            "-o",
            f"ControlMaster={control_master}",
            "-o",
            f"ControlPersist={self.config.control_persist}",
        ]
        if os.environ.get("CCC_SSH_KEY") or Path(self.config.key_path).exists():
            opts = ["-i", self.config.key_path, "-o", "IdentitiesOnly=yes"] + opts
        return opts

    def check_master(self) -> tuple[bool, str]:
        cmd = [
            "ssh",
            *self._ssh_options(control_master="no"),
            "-O",
            "check",
            self.config.destination,
        ]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=8)
        text = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, text.strip()

    def ensure_master(self) -> dict[str, Any]:
        active, detail = self.check_master()
        if active:
            return {"ok": True, "already_running": True, "detail": detail}
        with self._master_lock:
            active, detail = self.check_master()
            if active:
                return {"ok": True, "already_running": True, "detail": detail}
            Path(self.config.control_path).parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ssh",
                *self._ssh_options(control_master="yes"),
                "-Nf",
                self.config.destination,
            ]
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=20)
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "already_running": False,
                    "error": ((proc.stderr or proc.stdout or "").strip() or "ssh master failed"),
                    "returncode": proc.returncode,
                }
            for _ in range(20):
                active, detail = self.check_master()
                if active:
                    return {"ok": True, "already_running": False, "detail": detail}
                time.sleep(0.1)
            return {"ok": False, "error": "ssh master started but did not answer check"}

    def close_master(self) -> dict[str, Any]:
        cmd = [
            "ssh",
            *self._ssh_options(control_master="no"),
            "-O",
            "exit",
            self.config.destination,
        ]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=10)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": ((proc.stdout or "") + (proc.stderr or "")).strip(),
        }

    def run_capture(self, remote_args: list[str] | str, timeout: int = 60, **kwargs) -> subprocess.CompletedProcess[str]:
        ensured = self.ensure_master()
        if not ensured.get("ok"):
            raise RuntimeError(ensured.get("error") or "could not start ssh master")
        if isinstance(remote_args, str):
            remote_cmd = remote_args
        else:
            remote_cmd = remote_shell(remote_args)
        cmd = ["ssh", *self._ssh_options(control_master="auto"), self.config.destination, remote_cmd]
        kwargs.setdefault("text", True)
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("timeout", timeout)
        return subprocess.run(cmd, **kwargs)

    def popen(self, remote_args: list[str] | str, **kwargs) -> subprocess.Popen:
        ensured = self.ensure_master()
        if not ensured.get("ok"):
            raise RuntimeError(ensured.get("error") or "could not start ssh master")
        if isinstance(remote_args, str):
            remote_cmd = remote_args
        else:
            remote_cmd = remote_shell(remote_args)
        cmd = ["ssh", *self._ssh_options(control_master="auto"), self.config.destination, remote_cmd]
        return subprocess.Popen(cmd, **kwargs)

    def copy_to_remote(self, local_path: Path, remote_path: str, timeout: int = 120) -> dict[str, Any]:
        ensured = self.ensure_master()
        if not ensured.get("ok"):
            return {"ok": False, "error": ensured.get("error") or "could not start ssh master"}
        cmd = [
            "scp",
            "-P",
            str(self.config.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ControlPath={self.config.control_path}",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPersist={self.config.control_persist}",
        ]
        if os.environ.get("CCC_SSH_KEY") or Path(self.config.key_path).exists():
            cmd = ["scp", "-i", self.config.key_path, "-o", "IdentitiesOnly=yes"] + cmd[1:]
        cmd.extend([str(local_path), f"{self.config.destination}:{remote_path}"])
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "error": (proc.stderr or proc.stdout or "").strip(),
            "remote_path": remote_path,
        }


def remote_quote(arg: str) -> str:
    text = str(arg)
    if re.match(r"^~/[A-Za-z0-9_./-]+$", text):
        return text
    return shlex.quote(text)


def remote_shell(args: list[str]) -> str:
    return " ".join(remote_quote(str(arg)) for arg in args)


_GLOBAL_MUX = None
_GLOBAL_MUX_LOCK = threading.Lock()


def get_global_multiplexer() -> SSHMultiplexer | None:
    global _GLOBAL_MUX
    with _GLOBAL_MUX_LOCK:
        if _GLOBAL_MUX is not None:
            return _GLOBAL_MUX
        config = BridgeConfig.from_env()
        if not config:
            return None
        _GLOBAL_MUX = SSHMultiplexer(config)
        return _GLOBAL_MUX


REMOTE_SCAN_SCRIPT = """
import sys, os, json, sqlite3, time
from pathlib import Path

limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "None" else 100
repo_path = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "None" else ""

results = []

try:
    for cand in [os.path.expanduser("~/Apps/claude-command-center"), os.path.expanduser("~/claude-command-center"), os.path.expanduser("~/.claude/command-center")]:
        if os.path.exists(os.path.join(cand, "server.py")):
            sys.path.insert(0, cand)
            import server as ccc_server
            out = ccc_server.find_all_sessions(repo_path=repo_path or None, limit=limit)
            for row in out:
                row["is_remote"] = True
                row["remote_host"] = os.environ.get("SSH_CONNECTION", "").split()[2] if "SSH_CONNECTION" in os.environ else "remote"
                row["source"] = f"remote-{row.get('source', 'claude')}"
            print(json.dumps({"ok": True, "sessions": out, "engine": "native"}))
            sys.exit(0)
except Exception:
    pass

try:
    claude_dir = Path(os.path.expanduser("~/.claude/projects"))
    if claude_dir.exists():
        for p in claude_dir.glob("*/*.jsonl"):
            try:
                st = p.stat()
                mtime = st.st_mtime
                sid = p.stem
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    first_line = f.readline()
                    if not first_line:
                        continue
                    data = json.loads(first_line)
                    first_msg = ""
                    cwd = ""
                    branch = ""
                    if isinstance(data, dict):
                        first_msg = str(data.get("prompt") or data.get("first_prompt") or "")
                        cwd = str(data.get("cwd") or data.get("workingDirectory") or "")
                        branch = str(data.get("gitBranch") or "")
                    if repo_path and cwd and not cwd.startswith(repo_path):
                        continue
                    results.append({
                        "session_id": sid,
                        "first_message": first_msg[:120] if first_msg else f"Claude session {sid[:8]}",
                        "last_prompt": first_msg[:120] if first_msg else "",
                        "cwd": cwd,
                        "gitBranch": branch,
                        "timestamp": int(mtime * 1000),
                        "source": "remote-claude",
                        "engine": "claude",
                        "is_remote": True,
                        "log_path": str(p),
                    })
            except Exception:
                continue
except Exception:
    pass

try:
    codex_dir = Path(os.path.expanduser("~/.codex"))
    for db in codex_dir.glob("state_*.sqlite"):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
            con.row_factory = sqlite3.Row
            rows = con.execute("SELECT id, rollout_path, created_at, updated_at, model_provider, model FROM threads ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
            for r in rows:
                sid = str(r["id"])
                ts = int(r["updated_at"] * 1000) if r["updated_at"] else int(time.time() * 1000)
                results.append({
                    "session_id": sid,
                    "first_message": f"Codex session {sid[:8]}",
                    "last_prompt": "",
                    "cwd": "",
                    "timestamp": ts,
                    "source": "remote-codex",
                    "engine": "codex",
                    "is_remote": True,
                    "log_path": str(r["rollout_path"] or ""),
                })
            con.close()
        except Exception:
            continue
except Exception:
    pass

results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
if limit:
    results = results[:limit]
print(json.dumps({"ok": True, "sessions": results, "engine": "fallback"}))
"""


def find_remote_sessions(
    mux: SSHMultiplexer, repo_path: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    try:
        proc = mux.run_capture(
            ["python3", "-c", REMOTE_SCAN_SCRIPT, str(limit or 100), str(repo_path or "")],
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout.strip())
            if isinstance(data, dict) and data.get("ok"):
                return data.get("sessions") or []
    except Exception:
        pass
    return []
