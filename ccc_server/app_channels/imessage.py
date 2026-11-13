"""iMessage channel for app routine alerts.

macOS: osascript talks to Messages directly. Linux: the same AppleScript runs
on a configured ssh host. Only the single owner handle in cfg["to"] is ever
messaged; any other recipient is refused.
"""
import platform
import re
import shlex
import subprocess

NAME = "imessage"

_SCRIPT = (
    'on run argv\n'
    '  set theTo to item 1 of argv\n'
    '  set theText to item 2 of argv\n'
    '  tell application "Messages"\n'
    '    set svc to 1st account whose service type = iMessage\n'
    '    send theText to participant theTo of svc\n'
    '  end tell\n'
    'end run\n'
)
_HANDLE = re.compile(r"^(\+?[0-9][0-9 ()\-]{5,24}|[^@\s]+@[^@\s]+\.[^@\s]+)$")
_HOST = re.compile(r"^[A-Za-z0-9._@\-]+$")


def _system():
    return platform.system()


def _enabled(cfg):
    return bool(cfg) and bool(cfg.get("enabled"))


def _handle_ok(h):
    return isinstance(h, str) and bool(_HANDLE.match(h.strip()))


def _norm(h):
    return re.sub(r"[\s()\-]", "", h or "").lower()


def configured(cfg):
    cfg = cfg or {}
    if not _enabled(cfg) or not _handle_ok(cfg.get("to")):
        return False
    if _system() == "Darwin":
        return True
    host = cfg.get("ssh_host")
    return isinstance(host, str) and bool(_HOST.match(host)) and not host.startswith("-")


def _message(title, body, url):
    parts = [p for p in ((title or "").strip(), (body or "").strip(), (url or "").strip()) if p]
    return "\n".join(parts)[:1500]


def _command(cfg, to, text):
    if _system() == "Darwin":
        return ["osascript", "-e", _SCRIPT, to, text]
    remote = "osascript -e %s %s %s" % (shlex.quote(_SCRIPT), shlex.quote(to), shlex.quote(text))
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            cfg["ssh_host"], remote]


def send(cfg, title, body, url="", to=None, dry_run=False):
    cfg = cfg or {}
    if not _enabled(cfg):
        return False, "imessage channel disabled"
    owner = cfg.get("to")
    if not _handle_ok(owner):
        return False, "imessage: no valid owner handle configured"
    if to is not None and _norm(to) != _norm(owner):
        return False, "imessage: recipient is not the configured owner"
    if not configured(cfg):
        return False, "imessage: ssh_host required when not on macOS"
    text = _message(title, body, url)
    if not text:
        return False, "imessage: empty message"
    cmd = _command(cfg, owner.strip(), text)
    if dry_run or cfg.get("dry_run"):
        return True, ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except Exception as e:  # noqa: BLE001
        return False, "imessage: %s" % e
    if r.returncode != 0:
        return False, ("imessage: " + (r.stderr or r.stdout or "send failed").strip())[:300]
    return True, ""
