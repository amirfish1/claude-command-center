#!/usr/bin/env python3
"""Opt-in PreToolUse guard for accidental shared-worktree Git damage.

Standalone, stdlib-only; never executes the command being inspected. See
docs/shared-clone-guard.md for installation, detection, and parser limits.
"""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

MAX_INPUT = 131072
MAX_ROWS = 256
MAX_ROW_BYTES = 16384
DETECTION_SECONDS = 0.4
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _options(args, valued_short="", valued_long=()):
    """Separate options from paths/messages, including short-option bundles."""
    flags, paths = set(), []
    it = iter(args)
    for arg in it:
        if arg == "--":
            paths.extend(it)
            break
        if arg.startswith("--"):
            name, sep, _ = arg.partition("=")
            flags.add(name)
            if name in valued_long and not sep:
                next(it, None)
        elif arg.startswith("-") and arg != "-":
            for index, char in enumerate(arg[1:], 1):
                flags.add("-" + char)
                if char in valued_short:
                    if index == len(arg) - 1:
                        next(it, None)
                    break
        else:
            paths.append(arg)
    return flags, paths


def unsafe_git(subcommand, args):
    """Return a fixed label, never an arbitrary command or commit message."""
    valued = {
        "commit": ("mFCctU", ("--message", "--file", "--reuse-message",
                              "--reedit-message", "--template", "--author",
                              "--date", "--fixup", "--squash", "--trailer",
                              "--cleanup", "--pathspec-from-file")),
        "restore": ("sU", ("--source", "--pathspec-from-file")),
        "checkout": ("bBU", ("--conflict", "--pathspec-from-file")),
        "clean": ("e", ("--exclude",)),
        "add": ("", ("--chmod", "--pathspec-from-file")),
    }
    flags, paths = _options(args, *valued.get(subcommand, ("", ())))
    if "--help" in flags or "-h" in flags:
        return None
    if subcommand == "reset" and "--hard" in flags:
        return "git reset --hard"
    if subcommand == "checkout" and "--" in args and args.index("--") < len(args) - 1:
        return "git checkout -- <path>"
    if subcommand == "restore":
        staged = bool(flags & {"--staged", "-S"})
        worktree = bool(flags & {"--worktree", "-W"})
        if not staged or worktree:
            return "git restore <path>"
    if subcommand == "clean" and flags & {"--force", "-f"}:
        if not flags & {"--dry-run", "-n"}:
            return "git clean -f"
    if subcommand == "stash" and paths and paths[0] in {"drop", "pop"}:
        return "git stash " + paths[0]
    if subcommand == "add" and not flags & {"--dry-run", "-n"}:
        if flags & {"-A", "--all"} or any(p in {".", "./", ":/"} for p in paths):
            return "git add -A / ."
    if subcommand == "commit" and flags & {"-a", "--all"}:
        if not flags & {"--dry-run"}:
            return "git commit -a"
    return None


def _literal_directory(value, cwd):
    if any(char in value for char in "$`*"):
        return cwd  # unresolved shell expressions retain the conservative context
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(value)))


def _unwrap(words):
    """Common transparent wrappers; arguments to echo/printf are never commands."""
    words = list(words)
    while words:
        first = os.path.basename(words[0])
        if ASSIGNMENT.match(words[0]) or first in {"!", "then", "do", "else", "if", "elif"}:
            words.pop(0)
        elif first in {"command", "builtin", "exec"}:
            words.pop(0)
            while words and words[0] in {"--", "-p"}:
                words.pop(0)
        elif first == "env":
            words.pop(0)
            while words:
                if words[0] in {"-u", "--unset"}:
                    del words[:2]
                elif words[0] in {"-i", "--ignore-environment", "--"} or words[0].startswith("--unset="):
                    words.pop(0)
                elif ASSIGNMENT.match(words[0]):
                    words.pop(0)
                else:
                    break
        elif first == "nice":
            words.pop(0)
            if words and words[0] in {"-n", "--adjustment"}:
                del words[:2]
            elif words and re.fullmatch(r"-\d+|--adjustment=.*", words[0]):
                words.pop(0)
        else:
            break
    return words


def _git_invocation(words, cwd):
    it = iter(words[1:])
    for word in it:
        if word == "-C":
            cwd = _literal_directory(next(it, ""), cwd)
        elif word.startswith("-C"):
            cwd = _literal_directory(word[2:], cwd)
        elif word == "--work-tree":
            cwd = _literal_directory(next(it, ""), cwd)
        elif word.startswith("--work-tree="):
            cwd = _literal_directory(word.split("=", 1)[1], cwd)
        elif word in {"-c", "--git-dir", "--namespace", "--config-env"}:
            next(it, None)
        elif word in {"--help", "--version"}:
            return None
        elif not word.startswith("-"):
            return word, list(it), cwd
    return None


def _shell_parts(command):
    """Keep quoted separators as arguments, and ignore shell comments."""
    start, quote, escaped, comment = 0, "", False, False
    for index, char in enumerate(command):
        if comment:
            if char == "\n":
                comment = False
                start = index + 1
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == start or command[index - 1].isspace()):
            yield command[start:index], ""
            comment = True
        elif char in ";&|()\n":
            yield command[start:index], char
            start = index + 1
    if not comment:
        yield command[start:], ""


def risky_commands(command, cwd, depth=0):
    """Inspect literal commands, shell lists, cd/-C, and sh/bash/zsh -c."""
    if depth > 3 or len(command) > MAX_INPUT:
        return []
    results, stack = [], []

    def inspect(words, directory):
        words = _unwrap(words)
        if not words:
            return directory
        name = os.path.basename(words[0])
        if name == "cd":
            args = [w for w in words[1:] if w not in {"--", "-P", "-L"}]
            return _literal_directory(args[0], directory) if args else directory
        if name in {"sh", "bash", "zsh"}:
            for index, word in enumerate(words[1:], 1):
                if word.startswith("-") and not word.startswith("--") and "c" in word:
                    if index + 1 < len(words):
                        results.extend(risky_commands(words[index + 1], directory, depth + 1))
                    break
        if name == "git":
            invocation = _git_invocation(words, directory)
            if invocation:
                subcommand, args, target = invocation
                label = unsafe_git(subcommand, args)
                if label:
                    results.append((label, target))
        return directory

    for segment, operator in _shell_parts(command.replace("\\\n", "")):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        cwd = inspect(words, cwd)
        if operator == "(":
            stack.append(cwd)
        elif operator == ")" and stack:
            cwd = stack.pop()
    return results


def checkout_root(cwd):
    """Read HEAD directly: no git subprocess, even for linked worktrees."""
    current = Path(os.path.realpath(cwd))
    for root in (current, *current.parents):
        marker = root / ".git"
        try:
            gitdir = marker
            if marker.is_file():
                with marker.open(encoding="utf-8") as handle:
                    text = handle.read(4096).strip()
                if not text.startswith("gitdir: "):
                    continue
                gitdir = root / text[8:]
            with (gitdir / "HEAD").open(encoding="utf-8") as handle:
                head = handle.read(1024).strip()
            return str(root), head == "ref: refs/heads/main"
        except (OSError, ValueError):
            continue
    return None, False


def _is_claude_process(value):
    name = os.path.basename(value)
    if name == "claude":
        return True
    versions = str(Path.home() / ".local" / "share" / "claude" / "versions") + os.sep
    return value.startswith(versions) and bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", name))


def _live_claude_pids():
    """One bounded process snapshot; never a subprocess per registry row."""
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm=,args="], capture_output=True,
            text=True, timeout=0.2, check=False,
        )
        if result.returncode:
            return []
        pids = []
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) < 2 or not fields[0].isdigit():
                continue
            args = fields[2] if len(fields) > 2 else ""
            first = args.split(None, 1)[0] if args else ""
            if _is_claude_process(fields[1]) or _is_claude_process(first):
                pids.append(int(fields[0]))
        return pids[:MAX_ROWS]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _read_json(file, limit):
    try:
        with file.open(encoding="utf-8") as handle:
            data = json.loads(handle.read(limit + 1))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def peer_roots(session_id, config_dir, deadline):
    """Only read registry entries for live candidates; cache parsed metadata."""
    pids = _live_claude_pids()
    if not pids:
        return set()
    cache_path = config_dir / "command-center" / "git-guard-cache.json"
    cache = _read_json(cache_path, MAX_ROWS * 1024)
    previous = cache.get("rows", {})
    if not isinstance(previous, dict):
        previous = {}
    rows, roots = {}, set()
    for pid in pids:
        if time.monotonic() >= deadline:
            break
        file = config_dir / "sessions" / (str(pid) + ".json")
        try:
            stat = file.stat()
        except OSError:
            continue
        if stat.st_size > MAX_ROW_BYTES:
            continue
        stamp = [stat.st_mtime_ns, stat.st_size]
        entry = previous.get(str(pid), {})
        if not isinstance(entry, dict) or entry.get("stamp") != stamp:
            data = _read_json(file, MAX_ROW_BYTES)
            entry = {"stamp": stamp, "sid": data.get("sessionId"),
                     "cwd": data.get("cwd"), "pid": data.get("pid")}
        rows[str(pid)] = entry
        sid, cwd = entry.get("sid"), entry.get("cwd")
        if (not isinstance(sid, str) or not sid or sid == session_id
                or entry.get("pid") != pid or not isinstance(cwd, str) or not cwd):
            continue
        root, _ = checkout_root(cwd)
        if root:
            roots.add(root)
    if rows != previous:
        temporary = cache_path.with_name(cache_path.name + ".%d.tmp" % os.getpid())
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                json.dump({"rows": rows}, handle)
            os.replace(temporary, cache_path)
        except OSError:
            pass  # cache availability must not change the decision
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return roots


def check(data):
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        return None
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command, cwd = tool_input.get("command"), data.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str) or not cwd:
        return None
    candidates = risky_commands(command, cwd)
    if not candidates:
        return None
    if os.environ.get("CCC_SHARED_CLONE", "").lower() in {"1", "true", "yes"}:
        return candidates[0][0], "CCC_SHARED_CLONE is enabled"
    # Do not enumerate sessions for private branches or non-Git directories.
    main_candidates = []
    for label, target in candidates:
        root, on_main = checkout_root(target)
        if root and on_main:
            main_candidates.append((label, root))
    sid = data.get("session_id")
    if not main_candidates or not isinstance(sid, str) or not sid:
        return None
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    peers = peer_roots(sid, config_dir, time.monotonic() + DETECTION_SECONDS)
    for label, root in main_candidates:
        if root in peers:
            return label, "main is shared with another live Claude session"
    return None


def main():
    try:
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            return
        blocked = check(json.loads(raw))
    except (OSError, ValueError, TypeError):
        return
    if blocked:
        label, reason = blocked
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Shared-clone Git guard blocked %s: %s. This can discard or "
                "include another session's work. Use an isolated git worktree; "
                "stage explicit paths and commit with git commit --only <paths>. "
                "To unstage without changing files, use git restore --staged <paths>."
            ) % (label, reason),
        }}))


if __name__ == "__main__":
    main()
