import argparse
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INHERITED_SESSION_ENV = (
    "CCC_WORKER_PROCESS", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_CHILD_SESSION",
)


def _version():
    try:
        return importlib.metadata.version("claude-command-center")
    except importlib.metadata.PackageNotFoundError:
        text = (ROOT / "server.py").read_text(encoding="utf-8")
        match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
        return match.group(1) if match else "unknown"


def _port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("choose a port from 1 to 65535") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("choose a port from 1 to 65535")
    return port


def _watchtower_available():
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import watchtower.queue"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ensure_watchtower():
    if _watchtower_available():
        return True
    bash = shutil.which("bash")
    installer = ROOT / "scripts" / "install-watchtower.sh"
    if not bash:
        print("CCC needs Bash to set up WatchTower, its queue engine. Install Bash or use WSL, then retry.", file=sys.stderr)
        return False
    if not installer.is_file():
        print("The WatchTower setup file is missing. Get a fresh CCC wheel and retry.", file=sys.stderr)
        return False
    print("Setting up WatchTower, CCC's queue engine. This only happens once.", flush=True)
    env = dict(os.environ, CCC_PYTHON=sys.executable, CCC_SKIP_WATCHTOWER_DAEMON="1")
    try:
        result = subprocess.run([bash, str(installer)], env=env, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        print("WatchTower setup could not finish. Check your connection and retry the same command with CCC_WATCHTOWER_FORCE=1.", file=sys.stderr)
        return False
    if result.returncode != 0 or not _watchtower_available():
        print("CCC could not load WatchTower. Check your connection and retry the same command with CCC_WATCHTOWER_FORCE=1.", file=sys.stderr)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="claude-command-center",
        description="Claude Command Center: your local AI dashboard.",
        epilog="Open the printed dashboard link. Leave this terminal open; press Ctrl+C to stop.",
    )
    parser.add_argument("--version", action="version", version=f"Claude Command Center {_version()}")
    parser.add_argument("--port", type=_port, help="dashboard port (default: PORT or 8090)")
    parser.add_argument("--host", help="bind address (default: 127.0.0.1; keep it local)")
    args = parser.parse_args(argv)
    os.umask(0o077)
    for key in INHERITED_SESSION_ENV:
        os.environ.pop(key, None)
    if not _ensure_watchtower():
        return 1
    command = [sys.executable, str(ROOT / "server.py")]
    if args.port is not None:
        command.extend(["--port", str(args.port)])
    if args.host is not None:
        command.extend(["--host", args.host])
    os.execv(sys.executable, command)
