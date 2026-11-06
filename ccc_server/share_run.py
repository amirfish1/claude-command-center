# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Local "share this run" export and the weekly stats card.

Turns one session into a redacted, self-contained replay page and a card
image. Everything is built on this machine and handed back to the caller;
this module never opens a socket and uploads nothing.

Only what a viewer needs survives, and the rest is never read into the
output: prompts and agent replies go through the redactor; tool calls are
reduced to the tool name and an allow-listed command verb; tool output,
tool arguments, thinking and absolute timestamps are dropped.

HTTP surface (wired in server.py):

    GET /api/conversations/<id>/share?hide=a,b&keep=c&title=...
    GET /api/share/stats?days=7
"""

from __future__ import annotations

import getpass
import html
import os
import re
import socket
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from ccc_server import paths as _paths
from ccc_server.share_redact import RedactionError, Redactor

REPO_URL = "https://github.com/amirfish1/claude-command-center"
HIDE_FILE_NAME = "share-hide.txt"

MAX_STEPS = 240
MAX_PROMPT_CHARS = 700
MAX_REPLY_CHARS = 900
MAX_TITLE_CHARS = 90
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 80

_IDENTITY_TTL = 300
_STATS_TTL = 300

_TOOL_NAMES = frozenset("""
    Agent Bash Edit Glob Grep MultiEdit NotebookEdit Read Skill Task TodoWrite
    WebFetch WebSearch Write apply_patch exec_command read_file shell
    update_plan write_stdin
""".split())
_SHELL_TOOLS = frozenset(("Bash", "exec_command", "shell"))
_EDIT_TOOLS = frozenset(("Edit", "MultiEdit", "NotebookEdit", "Write", "apply_patch"))
_COMMAND_VERBS = frozenset("""
    bun bundle cargo cat cmake composer curl deno docker dotnet eslint flake8
    gcc gh git go gradle grep head jest kubectl ls make mix mvn mypy next node
    npm npx pip pip3 pnpm poetry prettier pytest python python3 rg ruff ruby
    rustc sed swift tail terraform tsc uv vite vitest wc yarn
""".split())
_GIT_VERBS = frozenset("""
    add blame branch checkout cherry-pick clone commit diff fetch log merge
    pull push rebase restore show stash status switch tag worktree
""".split())
_ENGINES = {
    "aider": "Aider", "antigravity": "Antigravity", "claude": "Claude Code",
    "codex": "Codex", "copilot": "Copilot", "cursor": "Cursor", "devin": "Devin",
    "droid": "Droid", "gemini": "Gemini", "grok": "Grok", "hermes": "Hermes",
    "kilo": "Kilo", "kimi": "Kimi", "opencode": "OpenCode", "pi": "Pi",
}
_MODEL_RE = re.compile(
    r"^(?:claude|gpt|o[134]|gemini|grok|kimi|deepseek|qwen|llama|mistral|glm|devin)"
    r"[a-z0-9.\-]{0,40}$"
)
_TEST_COMMAND_RE = re.compile(
    r"^(?:(?:python3?\s+-m\s+)?(?:pytest|unittest)|vitest|jest|rspec|"
    r"npx\s+(?:jest|vitest)|(?:npm|pnpm|yarn)\s+(?:run\s+)?test|"
    r"(?:go|cargo|mvn|make)\s+test)\b"
)
_CWD_RE = re.compile(rb'"cwd"\s*:\s*"([^"\n]{1,1024})"')
_PLACEHOLDER_RE = re.compile(r"\[(?:[a-z]+(?:-\d+)?|code, \d+ lines?)\]")

_identity_lock = threading.Lock()
_identity_cache = {}
_stats_lock = threading.Lock()
_stats_cache = {}


def parse_list(value) -> list:
    """Split a comma/newline list from a query string, bounded."""
    items = [p.strip() for p in re.split(r"[,\n]", str(value or ""))]
    return [p[:MAX_LIST_ITEM_CHARS] for p in items if p][:MAX_LIST_ITEMS]


def _hide_file() -> Path:
    return _paths.COMMAND_CENTER_STATE_DIR / HIDE_FILE_NAME


def _git_identity(cwd: str) -> tuple:
    """(people, projects) from git config: one subprocess, no network."""
    people, projects = [], []
    try:
        out = subprocess.run(
            ["git", "-C", cwd or ".", "config", "--get-regexp",
             r"^(user\.(name|email)|remote\.origin\.url)$"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return people, projects
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "user.name":
            people.append(value)
        elif key == "user.email":
            people.append(value.partition("@")[0])
        elif key == "remote.origin.url":
            parts = [p for p in re.split(r"[/:]", value.removesuffix(".git")) if p]
            if len(parts) >= 2:
                people.append(parts[-2])
                projects.append(parts[-1])
    return people, projects


def machine_identity(cwds=()) -> dict:
    """Names this machine knows are private, cached so an export forks once.

    ``people`` are masked word by word; ``projects`` only as whole phrases, so
    a repo called "command-center" does not ban the word "command".
    """
    key = tuple(sorted(c for c in cwds if c))
    now = time.monotonic()
    with _identity_lock:
        hit = _identity_cache.get(key)
        if hit and now - hit[0] < _IDENTITY_TTL:
            return hit[1]
    people = []
    projects = []
    home = Path.home()
    people.append(home.name)
    try:
        people.append(getpass.getuser())
    except (OSError, KeyError, ImportError):
        pass
    for var in ("USER", "LOGNAME", "USERNAME"):
        people.append(os.environ.get(var) or "")
    try:
        import pwd

        people.append(pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0])
    except (ImportError, KeyError, OSError):
        pass
    hostname = socket.gethostname()
    people.append(hostname.split(".")[0])
    for cwd in key or ("",):
        git_people, git_projects = _git_identity(cwd)
        people += git_people
        projects += git_projects
        if cwd:
            path = Path(cwd)
            try:
                relative = path.relative_to(home)
                projects += list(relative.parts)
            except ValueError:
                projects.append(path.name)
    identity = {
        "people": sorted({p for p in people if p}),
        "projects": sorted({p for p in projects if p}),
    }
    with _identity_lock:
        _identity_cache[key] = (now, identity)
    return identity


def _user_hide_list() -> list:
    try:
        lines = _hide_file().read_text("utf-8").splitlines()
    except OSError:
        return []
    items = [line.strip() for line in lines]
    return [i[:MAX_LIST_ITEM_CHARS] for i in items if i and not i.startswith("#")]


def transcript_cwds(transcript_path) -> list:
    """Working directories named near the start of a transcript."""
    try:
        with open(transcript_path, "rb") as handle:
            head = handle.read(256 * 1024)
    except (OSError, TypeError):
        return []
    found = {m.decode("utf-8", "ignore").replace("\\\\", "\\") for m in _CWD_RE.findall(head)}
    return sorted(found)[:8]


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _command_segments(command: str):
    """Each pipeline stage of a shell command, without wrappers or env vars."""
    for segment in re.split(r"&&|\|\||[;|\n]", str(command or "")):
        words = segment.split()
        while words and (
            "=" in words[0] or words[0] in ("sudo", "env", "nice", "time")
            or words[0].startswith("-") or words[0].isdigit()
        ):
            words.pop(0)
        if words:
            words[0] = words[0].rsplit("/", 1)[-1]
            yield words


def _command_verb(command: str) -> str:
    """The allow-listed program a shell command runs, or ''."""
    for words in _command_segments(command):
        verb = words[0]
        if verb not in _COMMAND_VERBS:
            continue
        if verb == "git":
            sub = next((w for w in words[1:] if w in _GIT_VERBS), "")
            return f"git {sub}".strip()
        return verb
    return ""


def _is_commit(command: str) -> bool:
    return any(
        words[0] == "git" and next((w for w in words[1:] if w in _GIT_VERBS), "") == "commit"
        for words in _command_segments(command)
    )


def _is_test_run(command: str) -> bool:
    return any(_TEST_COMMAND_RE.match(" ".join(words)) for words in _command_segments(command))


def _shell_command(block: dict) -> str:
    """Short commands are only in ``detail``; long ones have ``command`` too."""
    if str(block.get("name") or "") not in _SHELL_TOOLS:
        return ""
    return str(block.get("command") or block.get("detail") or "")


def _tool_label(block: dict) -> tuple:
    name = str(block.get("name") or "")
    known = name if name in _TOOL_NAMES else "Tool"
    return known, _command_verb(_shell_command(block))


def _safe_model(value) -> str:
    model = str(value or "").strip().lower()
    return model if _MODEL_RE.match(model) else ""


def build_replay(events, *, engine="", identity=None, hide=(), keep=(), title="") -> tuple:
    """Return (replay, redactor). Raises RedactionError on a verify failure."""
    identity = identity or {}
    redactor = Redactor(
        identity=list(identity.get("people") or ()),
        phrases=list(identity.get("projects") or ()),
        hide=list(hide) + _user_hide_list(),
        keep=keep,
    )
    steps = []
    stats = Counter()
    edited = set()
    models = Counter()
    first_ts = last_ts = None
    first_prompt = ""

    def add_tool(at, label, verb):
        if steps and steps[-1]["kind"] == "tools":
            tools = steps[-1]["tools"]
        else:
            tools = {}
            steps.append({"kind": "tools", "at": at, "tools": tools, "errors": 0})
        key = f"{label} {verb}".strip()
        tools[key] = tools.get(key, 0) + 1

    for event in events or ():
        ts = _parse_ts(event.get("ts"))
        if ts is not None:
            first_ts = ts if first_ts is None else first_ts
            last_ts = ts
        at = int(ts - first_ts) if ts is not None and first_ts is not None else None
        kind = event.get("type")
        if kind == "user_text":
            if event.get("bookkeeping"):
                continue
            text = redactor.text(event.get("text"), MAX_PROMPT_CHARS)
            if not text:
                continue
            first_prompt = first_prompt or text
            stats["prompts"] += 1
            steps.append({"kind": "user", "at": at, "text": text})
        elif kind == "assistant":
            if event.get("is_sidechain"):
                continue
            model = _safe_model(event.get("model"))
            if model:
                models[model] += 1
            for block in event.get("blocks") or ():
                block_kind = block.get("kind")
                if block_kind == "text":
                    text = redactor.text(block.get("text"), MAX_REPLY_CHARS)
                    if text:
                        stats["replies"] += 1
                        steps.append({"kind": "agent", "at": at, "text": text})
                elif block_kind == "tool_use":
                    label, verb = _tool_label(block)
                    add_tool(at, label, verb)
                    stats["tool_calls"] += 1
                    command = _shell_command(block)
                    stats["commits"] += _is_commit(command)
                    stats["test_runs"] += _is_test_run(command)
                    if label in _EDIT_TOOLS:
                        edited.add(str(block.get("detail") or block.get("id") or ""))
        elif kind == "tool_result" and event.get("is_error"):
            stats["tool_errors"] += 1
            for step in reversed(steps):
                if step["kind"] == "tools":
                    step["errors"] += 1
                    break

    for step in steps:
        if step["kind"] == "tools":
            step["tools"] = [{"label": k, "count": v} for k, v in step["tools"].items()]
    if len(steps) > MAX_STEPS:
        half = MAX_STEPS // 2
        skipped = len(steps) - MAX_STEPS
        steps = steps[:half] + [{"kind": "gap", "at": None, "count": skipped}] + steps[-half:]

    chosen_title = redactor.text(title, MAX_TITLE_CHARS) if title else ""
    if not chosen_title:
        chosen_title = _title_from(first_prompt)
    duration = int(last_ts - first_ts) if first_ts is not None and last_ts is not None else 0
    replay = {
        "title": chosen_title,
        "engine": _ENGINES.get(str(engine or "").lower(), "Coding agent"),
        "model": models.most_common(1)[0][0] if models else "",
        "stats": {
            "duration_sec": duration,
            "prompts": stats["prompts"],
            "replies": stats["replies"],
            "tool_calls": stats["tool_calls"],
            "tool_errors": stats["tool_errors"],
            "files_edited": len(edited),
            "commits": stats["commits"],
            "test_runs": stats["test_runs"],
        },
        "steps": steps,
        "hidden": {k: v for k, v in sorted(redactor.counts.items())},
    }
    # Engine and model come from fixed allow-lists, so a repo named after one
    # must not fail the check; every free-text field is verified.
    redactor.verify([replay["title"]] + [s.get("text", "") for s in steps])
    return replay, redactor


def _title_from(prompt: str) -> str:
    """First sentence of the prompt, cut on a word boundary."""
    line = prompt.split("\n")[0].strip()
    sentence = re.match(r".*?[.!?](?=\s|$)", line)
    title = sentence.group(0) if sentence else line
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + " …"
    return title or "An agent run"


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _mark(text: str) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: f"<mark>{m.group(0)}</mark>", _esc(text))


def format_duration(seconds) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def _stat_tiles(replay: dict) -> list:
    stats = replay["stats"]
    tiles = [
        (format_duration(stats["duration_sec"]), "elapsed"),
        (stats["tool_calls"], "tool calls"),
        (stats["files_edited"], "files edited"),
    ]
    if stats["test_runs"]:
        tiles.append((stats["test_runs"], "test runs"))
    if stats["commits"]:
        tiles.append((stats["commits"], "commits"))
    return tiles


def _hidden_sentence(hidden: dict) -> str:
    labels = (
        ("secret", "secret", "secrets"), ("path", "path", "paths"), ("name", "name", "names"),
        ("word", "word", "words"), ("email", "email", "emails"), ("url", "link", "links"),
        ("host", "host", "hosts"), ("ip", "IP", "IPs"), ("code", "code block", "code blocks"),
    )
    parts = [
        f"{hidden[key]} {one if hidden[key] == 1 else many}"
        for key, one, many in labels
        if hidden.get(key)
    ]
    named = {key for key, _, _ in labels}
    other = sum(count for key, count in hidden.items() if key not in named)
    if other:
        parts.append(f"{other} other detail" + ("" if other == 1 else "s"))
    return "Redacted on the author's machine: " + (", ".join(parts) or "nothing matched") + "."


_PAGE_STYLE = """
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;
--dim:#8b949e;--accent:#39d353;--user:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
main{max-width:760px;margin:0 auto;padding:32px 20px 96px}
h1{font-size:24px;line-height:1.3;margin:0 0 6px}
.meta{color:var(--dim);font-size:13px;margin:0 0 20px}
.tiles{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 24px;padding:0;list-style:none}
.tiles li{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:8px 14px;min-width:96px}.tiles b{display:block;font-size:20px}
.tiles span{color:var(--dim);font-size:12px}
.step{margin:0 0 12px;padding:10px 14px;border-radius:8px;background:var(--panel);
border:1px solid var(--line);white-space:pre-wrap;overflow-wrap:anywhere}
.step.user{border-left:3px solid var(--user)}.step.agent{border-left:3px solid var(--accent)}
.step.tools,.step.gap{background:none;border:0;padding:2px 14px;color:var(--dim);
font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace}
.who{display:block;color:var(--dim);font-size:11.5px;margin-bottom:2px}
.err{color:#f85149}mark{background:#30363d;color:#c9d1d9;border-radius:3px;padding:0 3px;
font-size:.92em}
.player{position:fixed;left:0;right:0;bottom:0;background:var(--panel);
border-top:1px solid var(--line);padding:10px 16px;display:none;gap:12px;align-items:center;
justify-content:center}.player.on{display:flex}
button{background:#21262d;color:var(--text);border:1px solid var(--line);border-radius:6px;
padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
input[type=range]{flex:1;max-width:420px}
footer{margin-top:28px;color:var(--dim);font-size:12.5px}a{color:var(--user)}
.hide{display:none}
"""

_PAGE_SCRIPT = """
(function(){var s=[].slice.call(document.querySelectorAll('.step'));if(!s.length)return;
var bar=document.querySelector('.player'),range=bar.querySelector('input'),
play=bar.querySelector('[data-a=play]'),speed=bar.querySelector('[data-a=speed]');
var at=s.length,timer=null,rate=1;bar.classList.add('on');range.max=s.length;
function show(n){at=n;s.forEach(function(e,i){e.classList.toggle('hide',i>=n)});range.value=n;
if(timer&&n>0)s[n-1].scrollIntoView({block:'end',behavior:'smooth'})}
function wait(e){var n=e?e.textContent.length:0;return Math.min(2400,350+n*9)/rate}
function tick(){if(at>=s.length){stop();return}show(at+1);timer=setTimeout(tick,wait(s[at]))}
function stop(){clearTimeout(timer);timer=null;play.textContent='Play'}
function start(){if(at>=s.length)show(0);play.textContent='Pause';timer=setTimeout(tick,1)}
play.onclick=function(){timer?stop():start()};
speed.onclick=function(){rate=rate>=4?1:rate*2;speed.textContent=rate+'x'};
range.oninput=function(){stop();show(+range.value)};
if(!matchMedia('(prefers-reduced-motion: reduce)').matches){show(0);start()}})();
"""

_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)


def _step_html(step: dict) -> str:
    kind = step["kind"]
    if kind == "gap":
        return f'<div class="step gap">… {step["count"]} steps skipped …</div>'
    if kind == "tools":
        items = " · ".join(
            _esc(t["label"]) + (f' ×{t["count"]}' if t["count"] > 1 else "")
            for t in step["tools"]
        )
        errors = step.get("errors") or 0
        failed = f' <span class="err">{errors} failed</span>' if errors else ""
        return f'<div class="step tools">{items}{failed}</div>'
    who = "Prompt" if kind == "user" else "Agent"
    return f'<div class="step {kind}"><span class="who">{who}</span>{_mark(step["text"])}</div>'


def render_html(replay: dict) -> str:
    tiles = "".join(
        f"<li><b>{_esc(value)}</b><span>{_esc(label)}</span></li>"
        for value, label in _stat_tiles(replay)
    )
    meta = " · ".join(x for x in (replay["engine"], replay["model"]) if x)
    steps = "\n".join(_step_html(s) for s in replay["steps"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(replay["title"])} · agent run replay</title>
<style>{_PAGE_STYLE}</style></head><body><main>
<h1>{_mark(replay["title"])}</h1>
<p class="meta">{_esc(meta)}</p>
<ul class="tiles">{tiles}</ul>
{steps}
<footer><p>{_esc(_hidden_sentence(replay["hidden"]))} Tool output, tool arguments and timestamps were left out.</p>
<p>Replay made with <a href="{REPO_URL}" rel="noreferrer">CCC</a>, a free local command center for coding agents.</p></footer>
</main>
<div class="player"><button type="button" data-a="play">Play</button>
<input type="range" min="0" max="1" value="1" aria-label="Replay position">
<button type="button" data-a="speed">1x</button></div>
<script>{_PAGE_SCRIPT}</script></body></html>
"""


def _wrap(text: str, width: int, lines: int) -> list:
    words = str(text).split()
    out, line = [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(" .,") + " …"
    return out


_SVG_FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, sans-serif"


def _svg_frame(body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" '
        f'viewBox="0 0 1200 630" font-family="{_SVG_FONT}">'
        '<rect width="1200" height="630" fill="#0d1117"/>'
        f"{body}"
        '<text x="64" y="582" fill="#8b949e" font-size="24">'
        "Made with CCC, a free local command center for coding agents</text></svg>"
    )


def _svg_tiles(tiles: list, y: int) -> str:
    out = []
    width = (1200 - 128 - 24 * (len(tiles) - 1)) // max(len(tiles), 1)
    for index, (value, label) in enumerate(tiles):
        x = 64 + index * (width + 24)
        out.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="150" rx="14" '
            'fill="#161b22" stroke="#30363d"/>'
            f'<text x="{x + 24}" y="{y + 82}" fill="#e6edf3" font-size="56" '
            f'font-weight="700">{_esc(value)}</text>'
            f'<text x="{x + 24}" y="{y + 122}" fill="#8b949e" font-size="24">{_esc(label)}</text>'
        )
    return "".join(out)


def render_svg(replay: dict) -> str:
    """Social-card image of a run. The browser rasterizes it to PNG."""
    title = "".join(
        f'<text x="64" y="{150 + index * 62}" fill="#e6edf3" font-size="50" '
        f'font-weight="700">{_esc(line)}</text>'
        for index, line in enumerate(_wrap(replay["title"], 38, 2))
    )
    meta = " · ".join(x for x in (replay["engine"], replay["model"]) if x)
    colors = {"user": "#58a6ff", "agent": "#39d353", "tools": "#30363d", "gap": "#161b22"}
    steps = replay["steps"]
    bar_width = 1072 / max(len(steps), 1)
    bars = "".join(
        f'<rect x="{64 + index * bar_width:.1f}" y="500" width="{max(bar_width - 1, 1):.1f}" '
        f'height="26" fill="{colors.get(step["kind"], "#30363d")}"/>'
        for index, step in enumerate(steps)
    )
    return _svg_frame(
        f'<text x="64" y="76" fill="#39d353" font-size="26">{_esc(meta)} · agent run</text>'
        + title
        + _svg_tiles(_stat_tiles(replay)[:4], 300)
        + bars
    )


def export_conversation(conversation_id, *, parse, transcript_path, engine="",
                        hide=(), keep=(), title="") -> tuple:
    """(payload, status) for GET /api/conversations/<id>/share.

    Reads this one session. Fails closed: if the finished text does not pass
    verification the export is refused, and nothing is returned.
    """
    parsed = parse(conversation_id) or {}
    events = parsed.get("events") or []
    if not events:
        return {"ok": False, "error": "This session has nothing to share yet."}, 404
    identity = machine_identity(transcript_cwds(transcript_path))
    try:
        replay, _redactor = build_replay(
            events, engine=engine or parsed.get("engine") or "", identity=identity,
            hide=hide, keep=keep, title=title,
        )
    except RedactionError as exc:
        return {
            "ok": False,
            "error": "Redaction check failed, so nothing was exported.",
            "detail": str(exc),
        }, 422
    return {
        "ok": True,
        "replay": replay,
        "html": render_html(replay),
        "svg": render_svg(replay),
        "hidden_summary": _hidden_sentence(replay["hidden"]),
    }, 200


def _commits_in_days(productivity: dict, days: int, now: float):
    """Pushed commits over the last ``days`` days, or None when unknown."""
    rows = productivity.get("daily")
    if not isinstance(rows, list):
        return None
    start = (datetime.fromtimestamp(now) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return sum(int(r.get("commits") or 0) for r in rows if str(r.get("date") or "") >= start)


def _stats_tiles(stats: dict) -> list:
    tiles = [(stats["agents"], "agent" if stats["agents"] == 1 else "agents")]
    if stats.get("commits") is not None:
        tiles.append((stats["commits"], "commit" if stats["commits"] == 1 else "commits"))
    tiles.append((stats["turns"], "agent turns"))
    return tiles


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}" + ("" if value == 1 else "s")


def render_stats_svg(stats: dict) -> str:
    period = "this week" if stats["days"] == 7 else f'the last {stats["days"]} days'
    parts = [_count(stats["agents"], "agent")]
    if stats.get("commits"):
        parts.append(_count(stats["commits"], "commit"))
    headline = ", ".join(parts) + f" {period}"
    return _svg_frame(
        '<text x="64" y="84" fill="#39d353" font-size="28">My agents, from my own machine</text>'
        f'<text x="64" y="200" fill="#e6edf3" font-size="62" font-weight="700">{_esc(headline)}</text>'
        + _svg_tiles(_stats_tiles(stats), 280)
    )


def stats_card(*, throughput, productivity, days=7, now=None) -> tuple:
    """(payload, status) for GET /api/share/stats. Numbers only.

    Both sources are already cached and built off the request path, so this
    never walks sessions itself; the result is held for ``_STATS_TTL`` too.
    """
    days = 30 if days == 30 else 7
    now = time.time() if now is None else now
    with _stats_lock:
        hit = _stats_cache.get(days)
        if hit and time.monotonic() - hit[0] < _STATS_TTL:
            return hit[1], 200
    usage, _status = throughput()
    if usage.get("pending"):
        return {"ok": True, "pending": True}, 200
    start = (datetime.fromtimestamp(now) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    recent = [r for r in usage.get("daily") or [] if str(r.get("day") or "") >= start]
    delivery, _status = productivity()
    stats = {
        "days": days,
        "agents": int((usage.get("agents") or {}).get(str(days)) or 0),
        "commits": _commits_in_days(delivery or {}, days, now),
        "turns": sum(int(r.get("turns") or 0) for r in recent),
        "tokens": sum(int(r.get("tokens") or 0) for r in recent),
    }
    payload = {"ok": True, "pending": False, **stats, "svg": render_stats_svg(stats)}
    with _stats_lock:
        _stats_cache[days] = (time.monotonic(), payload)
    return payload, 200
