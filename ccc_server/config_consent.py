# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Consent gate for every change CCC makes to agent config it does not own.

CCC used to write its hooks into ~/.claude/settings.json and ~/.codex/hooks.json
and copy its skills into ~/.claude/skills and ~/.codex/skills on every start,
without asking. This module is now the only path to those writes:

  * Inventory: ITEMS lists every file CCC touches outside its own
    ~/.claude/command-center dir. Each item can plan (diff), apply, and revoke.
  * Nothing is written until the user approves an item (dashboard modal or
    `ccc consent`). The decision is stored in config-consent.json with a hash
    of the content CCC proposed; if that content later changes, the item goes
    back to "changed" and is not re-applied until approved again.
  * Declining an item that is already present removes it.
  * Installs that predate this gate are recorded as approved on the first run
    (so nothing breaks) and a one-time notice lists what is installed.

Every write keeps the user's formatting (indent, trailing newline, key order),
writes the real file behind a symlink instead of replacing the link, and saves
the previous bytes under <state>/config-backups/ first.

Stdlib-only. Paths are resolved at call time from a Ctx so tests can point
everything at a temp dir.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ccc_server import core as _core

STATE_FILE_NAME = "config-consent.json"
BACKUP_DIR_NAME = "config-backups"
BACKUPS_KEPT = 30

HOOK_MARKER = "command-center/hooks/"
HOOK_MARKER_LEGACY = "log-viewer/hooks/"
PRETOOLUSE_HOOK_TIMEOUT = 1800

# (event, script, timeout). Order is the order entries are appended.
CLAUDE_HOOK_SPECS = (
    ("PreToolUse", "pre-tool-use.py", PRETOOLUSE_HOOK_TIMEOUT),
    ("PostToolUse", "post-tool-use.py", None),
    ("Notification", "notification.py", None),
    ("Stop", "stop.py", None),
    ("SessionStart", "session-start.py", 5),
    ("PreCompact", "pre-compact.py", None),
    ("PostCompact", "post-compact.py", None),
)
CODEX_HOOK_SPECS = (
    ("PostCompact", "post-compact-codex.py", 5),
)
HOOK_SCRIPT_NAMES = (
    "pre-tool-use.py", "post-tool-use.py", "notification.py", "stop.py",
    "session-start.py", "pre-compact.py", "post-compact.py", "_reorient_shared.py",
    "post-compact-codex.py",
)

BUNDLED_SKILLS = (
    ("ccc-orchestration",
     "Lets an agent spawn, message, and ask other CCC sessions."),
    ("group-chat-checkin",
     "Lets a session check in with a CCC group chat."),
    ("superpowers-to-watchtower",
     "Turns a superpowers plan into WatchTower queue tickets."),
    ("fleet-verify",
     "Spawns a browser-driven lane that verifies a UI change."),
)

_LOCK = threading.RLock()


def _env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def skill_install_skipped_by_env():
    return _env_flag("CCC_SKIP_SKILL_INSTALL")


# ── Context ────────────────────────────────────────────────────────────────

def hook_python_executable():
    """Absolute Python for hook commands (hook envs can have a minimal PATH)."""
    system_python = "/usr/bin/python3"
    if sys.platform == "darwin" and os.access(system_python, os.X_OK):
        return system_python
    exe = sys.executable or ""
    if exe and not os.path.isabs(exe):
        exe = shutil.which(exe) or exe
    if not exe:
        exe = shutil.which("python3") or shutil.which("python") or "python3"
    return exe


@dataclass
class Ctx:
    home: Path
    state_dir: Path
    ccc_root: Path
    hook_scripts_dir: Path
    codex_present: bool = False
    claude_present: bool = True
    wt_bin: str = ""
    hook_command: Optional[Callable[[str], str]] = None

    def command_for(self, script_name):
        if self.hook_command is not None:
            return self.hook_command(script_name)
        return (f"{shlex.quote(hook_python_executable())} "
                f"{shlex.quote(str(self.hook_scripts_dir / script_name))}")

    @property
    def claude_settings(self):
        return self.home / ".claude" / "settings.json"

    @property
    def codex_home(self):
        env = os.environ.get("CODEX_HOME")
        return Path(env).expanduser() if env else self.home / ".codex"

    @property
    def codex_hooks(self):
        return self.home / ".codex" / "hooks.json"

    @property
    def state_file(self):
        return self.state_dir / STATE_FILE_NAME

    def skill_roots(self):
        roots = [("claude", self.home / ".claude" / "skills")]
        if self.codex_present:
            roots.append(("codex", self.codex_home / "skills"))
        return roots


def _find_wt():
    found = shutil.which("wt")
    # Windows Terminal's app alias (...\WindowsApps\wt.exe) shadows
    # WatchTower's `wt` on PATH; running it opens a terminal tab instead.
    if found and os.name == "nt" and "\\windowsapps\\" in found.lower():
        found = None
    if found:
        return found
    if os.name == "nt":
        for scheme in ("nt_user", None):
            try:
                scripts = (sysconfig.get_path("scripts", scheme=scheme) if scheme
                           else sysconfig.get_path("scripts"))
            except Exception:
                continue
            candidate = Path(scripts) / "wt.exe"
            if os.access(candidate, os.X_OK):
                return str(candidate)
        return ""
    fallback = Path.home() / ".local" / "bin" / "wt"
    return str(fallback) if os.access(fallback, os.X_OK) else ""


def default_ctx():
    """Context for the running server (reads server globals at call time)."""
    home = Path.home()
    try:
        ccc_root = Path(_core.CCC_ROOT)
    except Exception:
        ccc_root = Path(__file__).resolve().parent.parent
    try:
        state_dir = Path(_core.COMMAND_CENTER_STATE_DIR)
    except Exception:
        state_dir = home / ".claude" / "command-center"
    try:
        scripts_dir = Path(_core.HOOK_SCRIPTS_DIR)
    except Exception:
        scripts_dir = home / ".claude" / "command-center" / "hooks"
    codex_home = Path(os.environ.get("CODEX_HOME") or (home / ".codex"))
    try:
        codex_present = codex_home.is_dir() or bool(_core._resolve_codex_bin().get("available"))
    except Exception:
        codex_present = codex_home.is_dir()
    try:
        claude_present = (bool(_core._resolve_claude_bin().get("available"))
                          or (home / ".claude" / "projects").is_dir()
                          or (home / ".claude" / "settings.json").is_file())
    except Exception:
        claude_present = (home / ".claude" / "projects").is_dir()
    try:
        hook_command = _core._ccc_hook_command
    except Exception:
        hook_command = None
    return Ctx(home=home, state_dir=state_dir, ccc_root=ccc_root,
               hook_scripts_dir=scripts_dir, codex_present=codex_present,
               claude_present=claude_present,
               wt_bin=_find_wt(), hook_command=hook_command)


# ── File helpers ───────────────────────────────────────────────────────────

def display_path(path, home=None):
    text = str(path)
    home_text = str(home or Path.home())
    if text == home_text or text.startswith(home_text + os.sep):
        return "~" + text[len(home_text):]
    return text


def _read_text(path):
    """(text, error). Missing file -> (None, None)."""
    try:
        return Path(path).read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"could not read {display_path(path)}: {e}"


def read_json_config(path):
    """(data, original_text, error). Missing or blank file -> ({}, text|None, None)."""
    text, err = _read_text(path)
    if err:
        return None, None, err
    if text is None or not text.strip():
        return {}, text, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, text, f"{display_path(path)} is not valid JSON ({e}); CCC will not edit it"
    if not isinstance(data, dict):
        return None, text, f"{display_path(path)} is not a JSON object; CCC will not edit it"
    return data, text, None


def _detect_indent(text, default):
    """The indent unit the file already uses, else `default`."""
    if text:
        for line in text.splitlines()[1:]:
            stripped = line.lstrip(" \t")
            if not stripped or len(stripped) == len(line):
                continue
            lead = line[: len(line) - len(stripped)]
            return "\t" if lead.startswith("\t") else len(lead)
    return default


def render_json(data, original_text, default_indent):
    body = (original_text or "").strip()
    if body and "\n" not in body and len(body) > 2:
        # One-line file: stay on one line, with its own separator spacing.
        spaced = '": ' in body or '", ' in body
        out = json.dumps(data, ensure_ascii=False,
                         separators=(", ", ": ") if spaced else (",", ":"))
    else:
        indent = _detect_indent(original_text, default_indent)
        out = json.dumps(data, indent=indent, ensure_ascii=False)
    if original_text is None or original_text.endswith("\n"):
        out += "\n"
    return out


def _backup(ctx, real_path, data):
    """Copy the previous bytes into <state>/config-backups/<stamp>/..."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = ctx.state_dir / BACKUP_DIR_NAME
    rel = display_path(real_path, ctx.home).lstrip("~/").lstrip("/")
    dest = root / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    try:
        stamps = sorted(p for p in root.iterdir() if p.is_dir())
        for old in stamps[:-BACKUPS_KEPT]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass
    return dest


CREATED_FILE_NAME = "config-consent-created.json"


def _created_paths(ctx):
    try:
        data = json.loads((ctx.state_dir / CREATED_FILE_NAME).read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _save_created_paths(ctx, paths):
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    (ctx.state_dir / CREATED_FILE_NAME).write_text(json.dumps(sorted(paths), indent=2) + "\n")


def _note_created(ctx, real):
    """Remember files/dirs CCC created, so revoke can remove them again
    (only while empty) instead of leaving a stray {} or empty folder."""
    created = []
    p = real
    while not p.exists():
        created.append(str(p))
        if p.parent == p:
            break
        p = p.parent
    if created:
        with _LOCK:
            _save_created_paths(ctx, _created_paths(ctx) | set(created))


def cleanup_created(ctx, path):
    """After a revoke: remove `path` and its parents if CCC created them and
    they are now empty (a file that is only `{}` counts as empty)."""
    with _LOCK:
        created = _created_paths(ctx)
        if not created:
            return
        p = Path(os.path.realpath(path))
        changed = False
        while str(p) in created:
            try:
                if p.is_file():
                    text = p.read_text(encoding="utf-8").strip()
                    if text not in ("", "{}"):
                        break
                    p.unlink()
                elif p.is_dir():
                    if any(p.iterdir()):
                        break
                    p.rmdir()
            except (OSError, UnicodeDecodeError):
                break
            created.discard(str(p))
            changed = True
            p = p.parent
        if changed:
            _save_created_paths(ctx, created)


def write_config_text(ctx, path, new_text):
    """Atomically replace the real file behind `path` with `new_text`.

    Follows a symlink to its target (the link is left alone), keeps the
    file's mode, and backs up the previous bytes. Returns None or an error.
    """
    real = Path(os.path.realpath(path))
    try:
        old = real.read_bytes() if real.exists() else None
    except OSError as e:
        return f"could not read {display_path(real)}: {e}"
    new_bytes = new_text.encode("utf-8")
    if old == new_bytes:
        return None
    tmp = None
    try:
        if old is not None:
            _backup(ctx, real, old)
            mode = real.stat().st_mode & 0o777
        else:
            mode = 0o644
            _note_created(ctx, real)
        real.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.", suffix=".ccc-tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
        tmp = None
    except OSError as e:
        return f"could not write {display_path(real)}: {e}"
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


def _diff(path, before, after, home):
    name = display_path(path, home)
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=name if before is not None else "/dev/null",
        tofile=name if after is not None else "/dev/null",
    )
    out = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return out


def _hash(obj):
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ── Hook entries (Claude settings.json / Codex hooks.json) ─────────────────

def _is_ccc_command(cmd):
    return isinstance(cmd, str) and (HOOK_MARKER in cmd or HOOK_MARKER_LEGACY in cmd)


def _normalize_command(ctx, cmd, script_names):
    if not isinstance(cmd, str):
        return cmd
    rewritten = cmd.replace(HOOK_MARKER_LEGACY, HOOK_MARKER)
    for name in script_names:
        if name in rewritten and HOOK_MARKER in rewritten:
            return ctx.command_for(name)
    return rewritten


def _has_ccc_hook(entries, script, any_ccc=False):
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks", []) or []:
            cmd = h.get("command", "") if isinstance(h, dict) else ""
            if not isinstance(cmd, str) or HOOK_MARKER not in cmd:
                continue
            if any_ccc or script in cmd:
                return True
    return False


def apply_hook_specs(ctx, config, specs, *, matcher=True):
    """Add CCC's hook entries to a hooks config dict in place. Returns True
    when anything changed. Other tools' entries are never touched."""
    changed = False
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        config["hooks"] = hooks
        changed = True
    names = [s for _, s, _ in specs] + [n for n in HOOK_SCRIPT_NAMES if n not in [s for _, s, _ in specs]]
    # Rewrite existing CCC entries to the current absolute interpreter/path.
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command", "")
                if not _is_ccc_command(cmd):
                    continue
                norm = _normalize_command(ctx, cmd, names)
                if norm != cmd:
                    h["command"] = norm
                    changed = True
    for event, script, timeout in specs:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
            changed = True
        # Stop has always matched on the marker alone (older installs named
        # the script differently).
        if _has_ccc_hook(entries, script, any_ccc=(event == "Stop")):
            if timeout is not None:
                for entry in entries:
                    for h in (entry.get("hooks", []) if isinstance(entry, dict) else []) or []:
                        cmd = h.get("command", "") if isinstance(h, dict) else ""
                        if (isinstance(cmd, str) and script in cmd and HOOK_MARKER in cmd
                                and h.get("timeout") != timeout):
                            h["timeout"] = timeout
                            changed = True
            continue
        hook = {"type": "command", "command": ctx.command_for(script)}
        if timeout is not None:
            hook["timeout"] = timeout
        entry = {"matcher": "", "hooks": [hook]} if matcher else {"hooks": [hook]}
        entries.append(entry)
        changed = True
    return changed


def strip_ccc_hooks(config):
    """Remove every CCC hook command from a hooks config dict in place.

    Only CCC's commands go; an entry (matcher group) is dropped only when CCC
    emptied it, an event only when CCC emptied it, and the "hooks" key only
    when it ends up empty. Returns True when anything changed."""
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept_entries = []
        event_changed = False
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                kept_entries.append(entry)
                continue
            kept = [h for h in entry["hooks"]
                    if not (isinstance(h, dict) and _is_ccc_command(h.get("command")))]
            if len(kept) == len(entry["hooks"]):
                kept_entries.append(entry)
                continue
            event_changed = True
            if kept:
                entry["hooks"] = kept
                kept_entries.append(entry)
        if event_changed:
            changed = True
            if kept_entries:
                hooks[event] = kept_entries
            else:
                del hooks[event]
    if changed and not hooks:
        del config["hooks"]
    return changed


def has_any_ccc_hook(config):
    hooks = (config or {}).get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("hooks", []) or []:
                if isinstance(h, dict) and _is_ccc_command(h.get("command")):
                    return True
    return False


# ── Items ──────────────────────────────────────────────────────────────────
#
# Each item: id, title, summary, depends (what stops working without it),
# applicable(ctx), plan(ctx) -> Plan, apply(ctx) -> error|None,
# revoke(ctx) -> error|None, proposal(ctx) -> hashable description.

@dataclass
class Plan:
    installed: bool = False        # CCC's content is present (any version)
    up_to_date: bool = False       # applying would change nothing
    files: list = field(default_factory=list)    # [{path, action, diff}]
    remove_files: list = field(default_factory=list)
    error: str = ""


def _json_hook_plan(ctx, path, specs, matcher, default_indent):
    plan = Plan()
    data, text, err = read_json_config(path)
    if err:
        plan.error = err
        return plan
    plan.installed = has_any_ccc_hook(data)
    after = copy.deepcopy(data)
    if apply_hook_specs(ctx, after, specs, matcher=matcher):
        new_text = render_json(after, text, default_indent)
        plan.files.append({"path": display_path(path, ctx.home),
                           "action": "modify" if text is not None else "create",
                           "diff": _diff(path, text, new_text, ctx.home)})
    else:
        plan.up_to_date = True
    stripped = copy.deepcopy(data)
    if strip_ccc_hooks(stripped):
        new_text = render_json(stripped, text, default_indent)
        plan.remove_files.append({"path": display_path(path, ctx.home), "action": "modify",
                                  "diff": _diff(path, text, new_text, ctx.home)})
    return plan


def _json_hook_write(ctx, path, mutate, default_indent):
    data, text, err = read_json_config(path)
    if err:
        return err
    if not mutate(data):
        return None
    return write_config_text(ctx, path, render_json(data, text, default_indent))


class ClaudeHooks:
    id = "claude-hooks"
    kind = "hooks"
    title = "Claude Code hooks"
    summary = ("Adds 6 hook entries (PreToolUse, PostToolUse, Notification, Stop, "
               "PreCompact, PostCompact) to ~/.claude/settings.json. Each runs a "
               "small script from ~/.claude/command-center/hooks/ that writes live "
               "status for the dashboard. Your other hooks and settings are kept as-is.")
    depends = ("Live \"running X for Ns\" tool status, Needs-approval badges, "
               "answering AskUserQuestion from the dashboard, the Compacting badge, "
               "and precise turn-end detection for Claude sessions.")
    default_indent = 2
    engines = ("claude",)

    def hook_count(self):
        return len(CLAUDE_HOOK_SPECS)

    def applicable(self, ctx):
        return True

    def targets(self, ctx):
        return [ctx.claude_settings]

    def proposal(self, ctx):
        return [[e, ctx.command_for(s), t] for e, s, t in CLAUDE_HOOK_SPECS]

    def plan(self, ctx):
        return _json_hook_plan(ctx, ctx.claude_settings, CLAUDE_HOOK_SPECS, True, self.default_indent)

    def apply(self, ctx):
        return _json_hook_write(ctx, ctx.claude_settings,
                                lambda d: apply_hook_specs(ctx, d, CLAUDE_HOOK_SPECS, matcher=True),
                                self.default_indent)

    def revoke(self, ctx):
        err = _json_hook_write(ctx, ctx.claude_settings, strip_ccc_hooks, self.default_indent)
        if not err:
            cleanup_created(ctx, ctx.claude_settings)
        return err


class CodexHooks(ClaudeHooks):
    id = "codex-hooks"
    title = "Codex hook"
    summary = ("Adds one PostCompact hook entry to ~/.codex/hooks.json. Codex asks "
               "you to trust it once on the next `codex` launch. Other tools' "
               "entries are kept as-is.")
    depends = "Re-orienting Codex sessions on their task after /compact."
    engines = ("codex",)

    def hook_count(self):
        return len(CODEX_HOOK_SPECS)

    def applicable(self, ctx):
        return ctx.codex_present

    def targets(self, ctx):
        return [ctx.codex_hooks]

    def proposal(self, ctx):
        return [[e, ctx.command_for(s), t] for e, s, t in CODEX_HOOK_SPECS]

    def plan(self, ctx):
        return _json_hook_plan(ctx, ctx.codex_hooks, CODEX_HOOK_SPECS, False, self.default_indent)

    def apply(self, ctx):
        return _json_hook_write(ctx, ctx.codex_hooks,
                                lambda d: apply_hook_specs(ctx, d, CODEX_HOOK_SPECS, matcher=False),
                                self.default_indent)

    def revoke(self, ctx):
        err = _json_hook_write(ctx, ctx.codex_hooks, strip_ccc_hooks, self.default_indent)
        if not err:
            cleanup_created(ctx, ctx.codex_hooks)
        return err


class BundledSkill:
    kind = "skill"
    engines = ("claude", "codex")

    def __init__(self, name, blurb):
        self.name = name
        self.id = f"skill:{name}"
        self.title = f"Skill: {name}"
        self.summary = (f"{blurb} Writes {name}/SKILL.md into your agent skills "
                        "folder(s). CCC skips a folder another tool manages (a symlink).")
        self.depends = f"Agents knowing the {name} skill exists."

    def _source(self, ctx):
        return ctx.ccc_root / "skills" / f"{self.name}.md"

    def applicable(self, ctx):
        # Not offered when every destination is another tool's symlink
        # (WatchTower ships its own group-chat-checkin): nothing to write.
        return self._source(ctx).is_file() and any(
            not dst_dir.is_symlink() for _, dst_dir, _ in self._dests(ctx))

    def _dests(self, ctx):
        return [(label, root / self.name, root / self.name / "SKILL.md")
                for label, root in ctx.skill_roots()]

    def targets(self, ctx):
        return [dst for _, _, dst in self._dests(ctx)]

    def proposal(self, ctx):
        try:
            digest = hashlib.sha256(self._source(ctx).read_bytes()).hexdigest()
        except OSError:
            digest = ""
        return [digest, [display_path(d, ctx.home) for d in self.targets(ctx)]]

    def plan(self, ctx):
        plan = Plan(up_to_date=True)
        src_text, err = _read_text(self._source(ctx))
        if err or src_text is None:
            plan.error = err or "bundled skill source missing"
            return plan
        for _, dst_dir, dst in self._dests(ctx):
            if dst_dir.is_symlink():
                # Another tool (e.g. WatchTower's skill sync) owns this path.
                continue
            cur, err = _read_text(dst)
            if err:
                plan.error = err
                continue
            if cur is not None:
                plan.installed = True
                plan.remove_files.append({"path": display_path(dst, ctx.home), "action": "remove",
                                          "diff": _diff(dst, cur, None, ctx.home)})
            if cur != src_text:
                plan.up_to_date = False
                plan.files.append({"path": display_path(dst, ctx.home),
                                   "action": "modify" if cur is not None else "create",
                                   "diff": _diff(dst, cur, src_text, ctx.home)})
        return plan

    def apply(self, ctx):
        src_text, err = _read_text(self._source(ctx))
        if err or src_text is None:
            return err or "bundled skill source missing"
        errors = []
        for _, dst_dir, dst in self._dests(ctx):
            if dst_dir.is_symlink():
                continue
            err = write_config_text(ctx, dst, src_text)
            if err:
                errors.append(err)
        return "; ".join(errors) or None

    def revoke(self, ctx):
        errors = []
        for _, dst_dir, dst in self._dests(ctx):
            if dst_dir.is_symlink() or not dst.is_file():
                continue
            try:
                _backup(ctx, dst, dst.read_bytes())
                dst.unlink()
                if dst_dir.is_dir() and not any(dst_dir.iterdir()):
                    dst_dir.rmdir()
                    cleanup_created(ctx, dst_dir.parent)
            except OSError as e:
                errors.append(f"could not remove {display_path(dst, ctx.home)}: {e}")
        return "; ".join(errors) or None


class WatchTowerSkills:
    id = "watchtower-skills"
    kind = "skill"
    engines = ("claude", "codex")
    title = "WatchTower skills"
    summary = ("Runs `wt skills sync`, which symlinks WatchTower's bundled skills "
               "(watchtower, critique, wt-triage-queue, ...) into each agent's skills "
               "folder (~/.claude/skills, ~/.codex/skills, ...). WatchTower keeps the "
               "list current; `wt skills remove` undoes it.")
    depends = "Agents knowing the `wt` queue commands and WatchTower skills."

    def applicable(self, ctx):
        return bool(ctx.wt_bin)

    def targets(self, ctx):
        return []

    def proposal(self, ctx):
        # WatchTower owns the list, so approval is for the sync itself.
        return "wt-skills-sync-v1"

    def _run(self, ctx, *args, timeout=20):
        try:
            proc = subprocess.run([ctx.wt_bin, "skills", *args], capture_output=True,
                                  text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            return None, str(e)
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return proc.stdout, None

    _status_cache = {"at": 0.0, "bin": "", "result": (None, None)}

    def _status_output(self, ctx):
        # One subprocess per minute at most: the dashboard reads this on load.
        cache = WatchTowerSkills._status_cache
        if cache["bin"] == ctx.wt_bin and time.monotonic() - cache["at"] < 60:
            return cache["result"]
        result = self._run(ctx, "status", timeout=5)
        cache.update(at=time.monotonic(), bin=ctx.wt_bin, result=result)
        return result

    def _invalidate(self):
        WatchTowerSkills._status_cache["at"] = 0.0

    def plan(self, ctx):
        plan = Plan()
        out, err = self._status_output(ctx)
        if err:
            plan.error = f"`wt skills status` failed: {err}"
            return plan
        missing, present = [], []
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            path, state = parts[1], " ".join(parts[2:])
            shown = display_path(path, ctx.home)
            if "up-to-date" in state:
                present.append(shown)
            else:
                missing.append(f"{shown}  {state}")
        plan.installed = bool(present)
        plan.up_to_date = not missing
        if missing:
            plan.files.append({"path": "symlinks", "action": "create",
                               "diff": "".join(f"+ {m}\n" for m in missing)})
        if present:
            plan.remove_files.append({"path": "symlinks", "action": "remove",
                                      "diff": "".join(f"- {p}\n" for p in present)})
        return plan

    def apply(self, ctx):
        self._invalidate()
        return self._run(ctx, "sync")[1]

    def revoke(self, ctx):
        self._invalidate()
        return self._run(ctx, "remove")[1]


def all_items():
    items = [ClaudeHooks(), CodexHooks()]
    items += [BundledSkill(n, b) for n, b in BUNDLED_SKILLS]
    items.append(WatchTowerSkills())
    return items


def _item_by_id(item_id):
    for item in all_items():
        if item.id == item_id:
            return item
    return None


# ── Decision store ─────────────────────────────────────────────────────────

def load_state(ctx):
    try:
        data = json.loads(ctx.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("items", {})
    data.setdefault("notice", {"pending": False, "items": []})
    return data


def save_state(ctx, state):
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = ctx.state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ctx.state_file)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(state, item_id, decision, digest, via):
    state["items"][item_id] = {"decision": decision, "hash": digest, "at": _now(), "via": via}


def _status(record, digest):
    if not record:
        return "pending"
    if record.get("decision") == "declined":
        return "declined"
    if record.get("decision") == "approved":
        return "enabled" if record.get("hash") == digest else "changed"
    return "pending"


def _skipped_by_env(item):
    return item.kind == "skill" and skill_install_skipped_by_env()


def _grandfather(ctx, state):
    """First run after the consent gate shipped: anything CCC already
    installed stays approved, and a one-time notice lists it."""
    carried = []
    for item in all_items():
        if not item.applicable(ctx):
            continue
        try:
            plan = item.plan(ctx)
        except Exception:
            continue
        if plan.installed and not plan.error:
            _record(state, item.id, "approved", _hash(item.proposal(ctx)), "existing-install")
            carried.append(item.id)
    state["notice"] = {"pending": bool(carried), "items": carried}


def _load_or_init(ctx):
    state = load_state(ctx)
    if state is None:
        state = {"version": 1, "items": {}, "notice": {"pending": False, "items": []}}
        _grandfather(ctx, state)
        save_state(ctx, state)
    return state


# ── Public operations ──────────────────────────────────────────────────────

def _engine_present(ctx, engine):
    return bool(ctx.claude_present if engine == "claude" else ctx.codex_present if engine == "codex" else False)


def overview(ctx=None):
    """Every applicable item with its decision, status, and diffs."""
    ctx = ctx or default_ctx()
    with _LOCK:
        state = _load_or_init(ctx)
    out = []
    for item in all_items():
        if not item.applicable(ctx):
            continue
        try:
            plan = item.plan(ctx)
        except Exception as e:  # never let one item break the list
            plan = Plan(error=str(e))
        digest = _hash(item.proposal(ctx))
        record = state["items"].get(item.id)
        status = _status(record, digest)
        engines = list(getattr(item, "engines", ()))
        relevant = any(_engine_present(ctx, e) for e in engines)
        out.append({
            "id": item.id,
            "engines": engines,
            "engine_installed": relevant,
            "hook_count": item.hook_count() if hasattr(item, "hook_count") else 0,
            "kind": item.kind,
            "title": item.title,
            "summary": item.summary,
            "depends": item.depends,
            "targets": [display_path(t, ctx.home) for t in item.targets(ctx)],
            "status": status,
            "decision": (record or {}).get("decision"),
            "decided_at": (record or {}).get("at"),
            "decided_via": (record or {}).get("via"),
            "installed": plan.installed,
            "up_to_date": plan.up_to_date,
            "needs_review": status in ("pending", "changed") and not _skipped_by_env(item),
            # needs_review, but only for an agent that is installed here; the
            # dashboard opens its prompt on this, not on needs_review.
            "auto_review": relevant and status in ("pending", "changed") and not _skipped_by_env(item),
            "skipped_by_env": _skipped_by_env(item),
            "error": plan.error,
            "changes": plan.files,
            "removal": plan.remove_files,
        })
    notice = state.get("notice") or {}
    return {
        "ok": True,
        "items": out,
        "needs_review": sum(1 for i in out if i["needs_review"]),
        "auto_review": sum(1 for i in out if i["auto_review"]),
        "engines_present": {"claude": bool(ctx.claude_present), "codex": bool(ctx.codex_present)},
        "notice": {
            "pending": bool(notice.get("pending")),
            "items": [i for i in out if i["id"] in (notice.get("items") or [])],
        },
        "backups_dir": display_path(ctx.state_dir / BACKUP_DIR_NAME, ctx.home),
    }


def decide(decisions, ctx=None, via="ui"):
    """Apply {item_id: "approve"|"decline"}. Approve writes now; decline
    removes CCC's content if present. Returns per-item results."""
    ctx = ctx or default_ctx()
    results = {}
    with _LOCK:
        state = _load_or_init(ctx)
        for item_id, choice in (decisions or {}).items():
            item = _item_by_id(item_id)
            if item is None or not item.applicable(ctx):
                results[item_id] = {"ok": False, "error": "unknown item"}
                continue
            if choice not in ("approve", "decline"):
                results[item_id] = {"ok": False, "error": "choice must be approve or decline"}
                continue
            digest = _hash(item.proposal(ctx))
            if choice == "approve":
                if _skipped_by_env(item):
                    results[item_id] = {"ok": False, "error": "CCC_SKIP_SKILL_INSTALL is set"}
                    continue
                err = item.apply(ctx)
                if err:
                    results[item_id] = {"ok": False, "error": err}
                    continue
                _record(state, item_id, "approved", digest, via)
            else:
                err = item.revoke(ctx)
                if err:
                    results[item_id] = {"ok": False, "error": err}
                    continue
                _record(state, item_id, "declined", digest, via)
            results[item_id] = {"ok": True}
        save_state(ctx, state)
    return {"ok": all(r.get("ok") for r in results.values()), "results": results}


def revoke(item_ids=None, ctx=None, via="ui"):
    """Remove CCC's content for the given items (default: all) and mark
    them declined."""
    ctx = ctx or default_ctx()
    if not item_ids:
        # "Everything CCC installed": WatchTower's own sync is only CCC's to
        # undo when it ran on the user's approval through CCC.
        state = load_state(ctx) or {"items": {}}
        item_ids = [
            i.id for i in all_items() if i.applicable(ctx) and (
                i.id != "watchtower-skills"
                or (state["items"].get(i.id) or {}).get("decision") == "approved")
        ]
    return decide({i: "decline" for i in item_ids}, ctx=ctx, via=via)


def ack_notice(ctx=None):
    ctx = ctx or default_ctx()
    with _LOCK:
        state = _load_or_init(ctx)
        state["notice"] = {"pending": False, "items": state.get("notice", {}).get("items", [])}
        save_state(ctx, state)
    return {"ok": True}


def is_enabled(item_id, ctx=None):
    """True when the item is approved at its current content."""
    ctx = ctx or default_ctx()
    item = _item_by_id(item_id)
    if item is None:
        return False
    state = load_state(ctx) or {"items": {}}
    return _status(state["items"].get(item_id), _hash(item.proposal(ctx))) == "enabled"


def startup(ctx=None, log=print):
    """Server start: apply only what the user approved at its current
    content. Nothing else is written outside CCC's own dir."""
    ctx = ctx or default_ctx()
    summary = {"applied": [], "pending": [], "changed": [], "declined": [], "errors": {}}
    with _LOCK:
        state = _load_or_init(ctx)
        for item in all_items():
            if not item.applicable(ctx):
                continue
            if _skipped_by_env(item):
                continue
            # WatchTower's sync runs from scripts/install-watchtower.sh.
            if item.id == "watchtower-skills":
                status = _status(state["items"].get(item.id), _hash(item.proposal(ctx)))
                if status == "pending":
                    summary["pending"].append(item.id)
                continue
            status = _status(state["items"].get(item.id), _hash(item.proposal(ctx)))
            if status == "enabled":
                try:
                    err = item.apply(ctx)
                except Exception as e:
                    err = str(e)
                if err:
                    summary["errors"][item.id] = err
                else:
                    summary["applied"].append(item.id)
            else:
                summary[status if status in summary else "pending"].append(item.id)
    if log:
        if skill_install_skipped_by_env():
            log("  [consent] skill install skipped (CCC_SKIP_SKILL_INSTALL=1)")
        if summary["pending"] or summary["changed"]:
            n = len(summary["pending"]) + len(summary["changed"])
            log(f"  [consent] {n} change(s) to your agent config await approval "
                f"({', '.join(summary['pending'] + summary['changed'])}); "
                "review them in the dashboard or with `ccc consent`")
        for item_id, err in summary["errors"].items():
            log(f"  [consent] {item_id}: {err}")
    return summary
