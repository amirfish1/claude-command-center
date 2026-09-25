# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Instinct: a proactive daily brief across your repos (stdlib-only).

Instead of waiting to be asked, Instinct looks at what happened since the last
brief and writes one self-contained HTML page answering three questions:

* **What changed** -- commits per repo (local branches, since the last run),
  grouped by Conventional Commit type, with the Hunch "why" (recorded
  decisions and invariants) for the files those commits touched.
* **What's stuck** -- CCC sessions waiting on a human (the same live attention
  feed as "Needs Your Attention"), WatchTower tickets blocked on a human
  answer or product gate, stalled queues, and unpushed commits.
* **What to do next** -- a ranked action list, plus *proposed* WatchTower
  tickets. Proposals are dry-run only: Instinct prints the ``wt add`` command
  and never files anything itself.

Sources, all read-only and bounded (no per-session work, no model calls):

* git: two subprocesses per repo (``log`` + ``status``), capped repo count.
* CCC: one ``GET /api/attention?scope=live`` against the local dashboard.
* WatchTower: ``wt status|blocked|gated --json`` -- three subprocesses total.
* Hunch: the committed ``.hunch/{decisions,constraints}/*.json`` graph in each
  repo, read directly (no ``npx`` spawn per file).

A source that fails is reported under "Blind spots" instead of aborting the
brief -- a partial brief that says what it could not see beats no brief.

Config lives outside the repo (``~/.claude/command-center/instinct.json`` by
default; see ``example_config()``) because repo lists and queue names are
personal. Output goes to ``~/.claude/command-center/instinct/`` with private
file permissions; publishing a brief anywhere is an explicit opt-in hook.

CLI::

    python3 -m ccc_server.instinct brief            # write today's brief
    python3 -m ccc_server.instinct brief --json     # also print the JSON
    python3 -m ccc_server.instinct init-config      # write an example config
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import html
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "command-center"
DEFAULT_CONFIG_PATH = STATE_DIR / "instinct.json"
DEFAULT_OUT_DIR = STATE_DIR / "instinct"
WT_HOME = Path.home() / ".watchtower"

SCHEMA_VERSION = 1
SUBPROCESS_TIMEOUT_S = 20
HTTP_TIMEOUT_S = 30
# A proposal already shown within this window is marked "seen" instead of new,
# so a daily brief does not nag about the same ticket every morning.
PROPOSAL_MEMORY_DAYS = 7

DEFAULTS = {
    "repos": [],                 # explicit repo paths (always included)
    "auto_discover_repos": True,  # add repos CCC saw sessions in this week
    "max_repos": 12,
    "exclude_repo_globs": ["/tmp/*", "*/pytest-of-*/*"],
    "ccc_url": None,             # default: $CCC_URL or http://127.0.0.1:$PORT
    "wt_bin": "wt",
    "window_hours": 24,          # first run / --since; later runs use state
    "max_window_hours": 24 * 7,
    "max_commits_per_repo": 40,
    "repo_queues": {},           # {"/abs/repo/path": "QUEUE"} for proposals
    "stuck_queue_days": 3,       # an idle, non-empty queue older than this
    "unpushed_hours": 12,        # commits ahead of upstream older than this
    "hotspot_fix_count": 3,      # N fix commits on one file in-window
    "stale_blocked_days": 14,    # blocked longer than this -> one sweep item
    "ignore_author_suffixes": ["[bot]"],
    "publish_command": None,     # argv list; "{html}" / "{title}" substituted
}

_CC_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<desc>.+)$")
_AHEAD_RE = re.compile(r"ahead (\d+)")
_BEHIND_RE = re.compile(r"behind (\d+)")

TYPE_LABELS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "refactor": "Refactors",
    "docs": "Docs",
    "test": "Tests",
    "chore": "Chores",
    "revert": "Reverts",
}

# CCC attention kinds -> (human label, severity). Unknown kinds fall back to
# the generic label so a new server-side kind still shows up.
ATTENTION_KINDS = {
    "question_blocked": ("Asked you a question", "high"),
    "pending_tool": ("Blocked on tool approval", "high"),
    "soft_block": ("Ended its turn waiting on you", "medium"),
    "sidecar_waiting": ("Side question waiting", "medium"),
    "stale_tool_call": ("Tool call looks hung", "medium"),
    "inject_stuck": ("Queued message never landed", "high"),
    "error": ("Errored", "high"),
}


# ---------------------------------------------------------------- config ---

def example_config() -> dict:
    """Public-safe example; real repo paths belong in the private copy."""
    cfg = dict(DEFAULTS)
    cfg["repos"] = ["~/code/my-app"]
    cfg["repo_queues"] = {"~/code/my-app": "MYAPP"}
    cfg["publish_command"] = None
    return cfg


def load_config(path: Path | None = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if path.is_file():
        try:
            user = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise SystemExit(f"instinct: cannot read config {path}: {e}")
        if not isinstance(user, dict):
            raise SystemExit(f"instinct: config {path} must be a JSON object")
        cfg.update(user)
    cfg["repos"] = [_expand(p) for p in cfg.get("repos") or []]
    cfg["repo_queues"] = {_expand(k): v for k, v in (cfg.get("repo_queues") or {}).items()}
    if not cfg.get("ccc_url"):
        cfg["ccc_url"] = os.environ.get("CCC_URL") or (
            "http://127.0.0.1:%s" % (os.environ.get("PORT") or "8090"))
    return cfg


def _expand(p: str) -> str:
    return os.path.normpath(os.path.expanduser(str(p)))


# --------------------------------------------------------------- helpers ---

def _run(argv, cwd=None, timeout=SUBPROCESS_TIMEOUT_S):
    """Run argv; return (stdout, error_string_or_None). Never raises."""
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return "", f"{' '.join(argv[:3])}: timed out after {timeout}s"
    except OSError as e:
        return "", f"{argv[0]}: {e}"
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip().splitlines()
        return p.stdout, f"{' '.join(argv[:3])}: exit {p.returncode}: {err[-1] if err else ''}"
    return p.stdout, None


def _http_json(url, timeout=HTTP_TIMEOUT_S):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"GET {url}: {getattr(e, 'reason', e)}"


def _age(seconds) -> str:
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return "?"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def _iso_to_ts(s):
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_conventional(subject: str) -> dict:
    m = _CC_RE.match(subject or "")
    if not m:
        return {"type": "other", "scope": "", "breaking": False, "desc": subject or ""}
    t = m.group("type").lower()
    return {"type": t if t in TYPE_LABELS else "other", "scope": m.group("scope") or "",
            "breaking": bool(m.group("bang")), "desc": m.group("desc")}


# ------------------------------------------------------------ collectors ---

def discover_repos(cfg, repo_list_payload=None) -> list[dict]:
    """Configured repos first, then CCC-known repos with sessions this week.

    ``repo_list_payload`` is the ``/api/repo/list`` JSON (fetched by the
    caller so tests can inject it)."""
    seen, out = set(), []

    def add(path, label=None, source="config"):
        path = _expand(path)
        if path in seen or len(out) >= int(cfg["max_repos"]):
            return
        if any(fnmatch.fnmatch(path, g) for g in cfg.get("exclude_repo_globs") or []):
            return
        if not (Path(path) / ".git").exists():
            return
        seen.add(path)
        out.append({"path": path, "label": label or Path(path).name, "source": source})

    for p in cfg.get("repos") or []:
        add(p)
    if cfg.get("auto_discover_repos") and isinstance(repo_list_payload, dict):
        repos = [r for r in repo_list_payload.get("repos") or [] if isinstance(r, dict)]
        active = [r for r in repos
                  if ((r.get("signals") or {}).get("d7") or {}).get("sessions", 0) > 0]
        active.sort(key=lambda r: -float(r.get("score") or 0))
        for r in active:
            add(r.get("path") or "", r.get("label"), "ccc")
    return out


def collect_git(repo: dict, since_ts: float, cfg) -> dict:
    path = repo["path"]
    info = dict(repo, commits=[], branch="", upstream="", ahead=0, behind=0,
                dirty=0, oldest_unpushed_ts=None, error=None)
    fmt = "%x1e%H%x1f%an%x1f%at%x1f%s"
    out, err = _run(["git", "-C", path, "log", "--branches", "--no-merges",
                     f"--since=@{int(since_ts)}", f"-n{int(cfg['max_commits_per_repo'])}",
                     f"--format={fmt}", "--name-only"])
    if err:
        info["error"] = err
        return info
    seen = set()
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, ts, subject = parts
        files = [f for f in rest.splitlines() if f.strip()]
        if any(author.endswith(sfx) for sfx in cfg.get("ignore_author_suffixes") or []):
            continue
        # --branches sees a rebased commit on both the old and new branch;
        # log is newest-first, so keep the first copy of each (author, subject).
        if (author, subject) in seen:
            continue
        seen.add((author, subject))
        c = {"sha": sha, "short": sha[:8], "author": author, "ts": int(ts),
             "subject": subject, "files": files}
        c.update(parse_conventional(subject))
        info["commits"].append(c)

    status, err = _run(["git", "-C", path, "status", "--porcelain=v1", "-b"])
    if err:
        info["error"] = err
        return info
    lines = status.splitlines()
    if lines and lines[0].startswith("## "):
        head = lines[0][3:]
        branch, _, tail = head.partition("...")
        info["branch"] = branch.strip()
        if tail:
            info["upstream"] = tail.split(" ", 1)[0]
            a, b = _AHEAD_RE.search(tail), _BEHIND_RE.search(tail)
            info["ahead"] = int(a.group(1)) if a else 0
            info["behind"] = int(b.group(1)) if b else 0
        lines = lines[1:]
    info["dirty"] = sum(1 for ln in lines if ln.strip())
    if info["ahead"] and info["upstream"]:
        ts_out, err = _run(["git", "-C", path, "log", "--format=%at",
                            f"{info['upstream']}..HEAD"])
        stamps = [int(x) for x in ts_out.split() if x.isdigit()]
        if stamps:
            info["oldest_unpushed_ts"] = min(stamps)
    return info


def load_hunch(repo_path: str) -> dict:
    """Read a repo's committed Hunch graph. Empty when the repo has none."""
    root = Path(repo_path) / ".hunch"
    graph = {"decisions": [], "constraints": [], "present": root.is_dir()}
    if not graph["present"]:
        return graph
    for kind in ("decisions", "constraints"):
        for f in sorted((root / kind).glob("*.json")):
            try:
                rec = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("valid_to") or rec.get("superseded_by"):
                continue
            if kind == "decisions" and rec.get("status") not in ("accepted", "proposed"):
                continue
            if kind == "constraints" and rec.get("status") not in (None, "active"):
                continue
            graph[kind].append(rec)
    return graph


def hunch_why(graph: dict, files: list[str]) -> dict:
    """Decisions anchored to ``files`` and file-scoped invariants over them.

    Repo-wide (``**``) constraints are skipped: they apply to every change and
    would repeat on every repo every day."""
    fileset = set(files)
    decisions, constraints = [], []
    for d in graph.get("decisions") or []:
        hit = sorted(fileset.intersection(d.get("related_files") or []))
        if hit:
            prov = d.get("provenance") if isinstance(d.get("provenance"), dict) else {}
            decisions.append({
                "id": d.get("id"), "title": d.get("title") or d.get("topic") or "",
                "decision": d.get("decision") or "", "files": hit,
                "rejected": list(d.get("alternatives_rejected") or [])[:2],
                "date": (d.get("date") or d.get("valid_from") or "")[:10],
                "verified_ts": _iso_to_ts(d.get("valid_from") or d.get("date")),
                "weight": _decision_weight(d, prov),
            })
    for c in graph.get("constraints") or []:
        scopes = c.get("scope") or []
        if isinstance(scopes, str):
            scopes = [scopes]
        scopes = [s for s in scopes if s and s not in ("**", "*")]
        hit = sorted(f for f in fileset if any(fnmatch.fnmatch(f, s) for s in scopes))
        if hit:
            constraints.append({"id": c.get("id"), "statement": c.get("statement") or "",
                                "severity": c.get("severity") or "advisory", "files": hit})
    decisions.sort(key=lambda d: (-d["weight"], d["id"] or ""))
    return {"decisions": decisions, "constraints": constraints}


_BOILERPLATE_RE = re.compile(r"^(changed code in|changed \S+:)", re.I)


def _decision_weight(d: dict, prov: dict) -> float:
    """Rank decisions so a recorded trade-off beats an auto-captured diff note."""
    text = str(d.get("decision") or "")
    w = float(prov.get("confidence") or 0.5)
    w += 1.0 if d.get("alternatives_rejected") else 0.0
    w += min(len(text), 600) / 600.0
    if _BOILERPLATE_RE.match(text.strip()):
        w -= 2.0
    return round(w, 3)


def collect_sessions(cfg) -> tuple[list, str | None]:
    data, err = _http_json(cfg["ccc_url"].rstrip("/") + "/api/attention?scope=live")
    if err:
        return [], err
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items or [] if isinstance(i, dict)], None


def collect_wt(cfg) -> dict:
    wt = cfg.get("wt_bin") or "wt"
    res = {"status": [], "blocked": [], "gated": [], "errors": []}
    for key in ("status", "blocked", "gated"):
        out, err = _run([wt, key, "--json"])
        if err:
            res["errors"].append(err)
            continue
        try:
            val = json.loads(out or "[]")
        except ValueError as e:
            res["errors"].append(f"wt {key} --json: bad JSON ({e})")
            continue
        res[key] = val if isinstance(val, list) else []
    return res


def load_wt_queue_repos() -> dict:
    """``{"owner/repo": [QUEUE, ...]}`` from WatchTower's queue config."""
    try:
        cfg = json.loads((WT_HOME / "queue-config.json").read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for q, v in (cfg.items() if isinstance(cfg, dict) else []):
        gh = isinstance(v, dict) and v.get("github_repo")
        if gh:
            out.setdefault(gh.lower(), []).append(q)
    return out


def _origin_slug(repo_path: str) -> str:
    out, err = _run(["git", "-C", repo_path, "remote", "get-url", "origin"])
    if err:
        return ""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", out.strip())
    return m.group(1).lower() if m else ""


def collect(cfg, since_ts: float, *, use_ccc=True, use_wt=True) -> dict:
    """Gather one raw snapshot. Pure I/O; ``analyze`` does the thinking."""
    now = time.time()
    snap = {"schema": SCHEMA_VERSION, "generated_ts": now, "since_ts": since_ts,
            "repos": [], "sessions": [], "wt": {"status": [], "blocked": [], "gated": []},
            "blind_spots": [], "queue_for_repo": {}}
    repo_list = None
    if use_ccc and cfg.get("auto_discover_repos"):
        repo_list, err = _http_json(cfg["ccc_url"].rstrip("/") + "/api/repo/list")
        if err:
            snap["blind_spots"].append(f"CCC repo list: {err}")
    repos = discover_repos(cfg, repo_list)
    if not repos:
        snap["blind_spots"].append(
            "No repos to watch: add \"repos\" to the Instinct config or start CCC.")
    wt_repo_queues = load_wt_queue_repos() if use_wt else {}
    for r in repos:
        g = collect_git(r, since_ts, cfg)
        if g.get("error"):
            snap["blind_spots"].append(f"git {r['label']}: {g['error']}")
        touched = sorted({f for c in g["commits"] for f in c["files"]})
        g["hunch"] = hunch_why(load_hunch(r["path"]), touched)
        g["hunch_present"] = (Path(r["path"]) / ".hunch").is_dir()
        snap["repos"].append(g)
        q = cfg["repo_queues"].get(r["path"])
        if not q and wt_repo_queues:
            qs = wt_repo_queues.get(_origin_slug(r["path"])) or []
            q = sorted(qs, key=len)[0] if qs else None
        snap["queue_for_repo"][r["path"]] = q
    if use_ccc:
        snap["sessions"], err = collect_sessions(cfg)
        if err:
            snap["blind_spots"].append(f"CCC sessions: {err}")
    else:
        snap["blind_spots"].append("CCC sessions: skipped (--no-ccc)")
    if use_wt:
        wt = collect_wt(cfg)
        snap["blind_spots"].extend(f"WatchTower: {e}" for e in wt.pop("errors"))
        snap["wt"] = wt
    else:
        snap["blind_spots"].append("WatchTower: skipped (--no-wt)")
    return snap


# --------------------------------------------------------------- analyze ---

_SEV_RANK = {"high": 0, "medium": 1, "low": 2}
MAX_HOTSPOTS_PER_REPO = 3
MAX_NEXT_ITEMS = 10  # per-item actions; roll-up actions are appended after
_TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|[._-](test|spec)\.[a-z0-9]+$")


def _is_test_path(path: str) -> bool:
    """Tests change alongside fixes by design; they are not the hotspot."""
    return bool(_TEST_PATH_RE.search(path))


def _proposal(queue, title, text, rationale, evidence, key, priority="normal"):
    argv = ["wt", "add", "-q", queue or "<QUEUE>", "--title", title, "--text", text]
    if priority and priority != "normal":
        argv += ["--priority", priority]
    return {"queue": queue, "title": title, "text": text, "priority": priority,
            "rationale": rationale, "evidence": evidence, "key": key,
            "wt_command": " ".join(shlex.quote(a) for a in argv)}


def analyze(snap: dict, cfg: dict, memory: dict | None = None) -> dict:
    """Turn a raw snapshot into the brief: changed / stuck / next / proposals.

    Deterministic: same snapshot + config + memory -> same brief."""
    now = snap["generated_ts"]
    memory = memory or {}
    changed, stuck, nxt, proposals = [], [], [], []

    # -- what changed ----------------------------------------------------
    for r in snap["repos"]:
        commits = r.get("commits") or []
        if not commits:
            continue
        groups = {}
        for c in commits:
            groups.setdefault(c["type"], []).append(c)
        order = [t for t in list(TYPE_LABELS) + ["other"] if t in groups]
        file_counts = {}
        for c in commits:
            for f in c["files"]:
                file_counts[f] = file_counts.get(f, 0) + 1
        hot = sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        changed.append({
            "repo": r["label"], "path": r["path"], "count": len(commits),
            "authors": sorted({c["author"] for c in commits}),
            "groups": [{"type": t, "label": TYPE_LABELS.get(t, "Other"),
                        "commits": groups[t]} for t in order],
            "hot_files": [{"file": f, "commits": n} for f, n in hot],
            "hunch": r.get("hunch") or {"decisions": [], "constraints": []},
            "hunch_present": bool(r.get("hunch_present")),
        })

    # -- what's stuck: sessions -----------------------------------------
    for it in snap.get("sessions") or []:
        kind = it.get("kind") or "attention"
        label, sev = ATTENTION_KINDS.get(kind, ("Needs attention", "medium"))
        age = now - float(it.get("mtime") or now)
        stuck.append({
            "source": "session", "severity": sev, "kind": kind,
            "title": f"{it.get('name') or it.get('session_id', '')[:8]}: {label}",
            "repo": it.get("repo") or it.get("folder_label") or "",
            "detail": it.get("question_text") or it.get("next_step") or it.get("where") or "",
            "age": _age(age), "age_s": age, "ref": it.get("session_id") or "",
        })

    # -- what's stuck: WatchTower ----------------------------------------
    wt = snap.get("wt") or {}
    stale_blocked_s = float(cfg["stale_blocked_days"]) * 86400
    for t in wt.get("blocked") or []:
        ts = _iso_to_ts(t.get("blocked_at") or t.get("claimed_at") or t.get("created_at"))
        age = now - ts if ts else 0
        stale = age >= stale_blocked_s
        stuck.append({
            "source": "watchtower", "severity": "low" if stale else "high",
            "kind": "blocked_stale" if stale else "blocked",
            "title": f"{t.get('ref') or t.get('id') or '?'}: {t.get('title') or 'blocked ticket'}",
            "repo": t.get("queue") or t.get("project") or "",
            "detail": t.get("block_question") or "Waiting on a human answer.",
            "age": _age(age), "age_s": age, "ref": t.get("ref") or "",
        })
    for t in wt.get("gated") or []:
        ts = _iso_to_ts(t.get("created_at"))
        age = now - ts if ts else 0
        stuck.append({
            "source": "watchtower", "severity": "medium", "kind": "gated",
            "title": f"{t.get('ref') or '?'}: {t.get('title') or 'product-gate pitch'}",
            "repo": t.get("queue") or t.get("project") or "",
            "detail": "Product-gate pitch awaiting Ack/Nack.",
            "age": _age(age), "age_s": age, "ref": t.get("ref") or "",
        })
    stuck_days = float(cfg["stuck_queue_days"])
    for q in wt.get("status") or []:
        idle = float(q.get("since_progress_s") or 0)
        if not q.get("depth") or idle < stuck_days * 86400:
            continue
        name = q.get("queue") or "?"
        why = ("auto-drain is off" if not q.get("auto_drain")
               else "auto-drain is on but nothing is closing")
        stuck.append({
            "source": "watchtower", "severity": "medium", "kind": "queue_stalled",
            "title": f"Queue {name}: {q.get('depth')} open, no progress for {_age(idle)}",
            "repo": name,
            "detail": f"Oldest open ticket {q.get('oldest_open_age') or _age(q.get('oldest_open_age_s'))}; {why}.",
            "age": _age(idle), "age_s": idle, "ref": name,
        })
        key = f"queue-triage:{name}"
        proposals.append(_proposal(
            name, f"Triage stalled {name} backlog ({q.get('depth')} open, idle {_age(idle)})",
            (f"Queue {name} has {q.get('depth')} open tickets and no progress for "
             f"{_age(idle)} ({why}). Close what is obsolete, merge duplicates "
             "(`wt dedup`), and mark the rest ready or icebox it."),
            "A stalled backlog hides real work and makes the queue look healthy.",
            [f"wt status: depth={q.get('depth')} since_progress={_age(idle)} "
             f"auto_drain={bool(q.get('auto_drain'))}"], key, "low"))

    # -- what's stuck: git -----------------------------------------------
    unpushed_s = float(cfg["unpushed_hours"]) * 3600
    for r in snap["repos"]:
        ts = r.get("oldest_unpushed_ts")
        if r.get("ahead") and ts and now - ts >= unpushed_s:
            age = now - ts
            stuck.append({
                "source": "git", "severity": "medium", "kind": "unpushed",
                "title": f"{r['label']}: {r['ahead']} unpushed commit(s) on {r.get('branch') or '?'}",
                "repo": r["label"],
                "detail": f"Oldest is {_age(age)} old; other machines and deploys cannot see it.",
                "age": _age(age), "age_s": age, "ref": r["path"],
            })
        if r.get("behind"):
            stuck.append({
                "source": "git", "severity": "low", "kind": "behind",
                "title": f"{r['label']}: {r['behind']} commit(s) behind {r.get('upstream')}",
                "repo": r["label"], "detail": "Pull before starting new work there.",
                "age": "", "age_s": 0, "ref": r["path"],
            })

    stuck.sort(key=lambda s: (_SEV_RANK.get(s["severity"], 3), -float(s.get("age_s") or 0)))

    # -- proposals from code signals --------------------------------------
    hot_n = int(cfg["hotspot_fix_count"])
    for r in snap["repos"]:
        queue = (snap.get("queue_for_repo") or {}).get(r["path"])
        fixes = {}
        for c in r.get("commits") or []:
            if c["type"] == "fix":
                for f in c["files"]:
                    if f.startswith("changelog.d/") or f.endswith(".md") or _is_test_path(f):
                        continue
                    fixes.setdefault(f, []).append(c)
        hotspots = [(f, cs) for f, cs in sorted(fixes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
                    if len(cs) >= hot_n]
        for f, cs in hotspots[:MAX_HOTSPOTS_PER_REPO]:
            hunch = r.get("hunch") or {}
            inv = [k["statement"] for k in hunch.get("constraints") or [] if f in k["files"]]
            decs = [d["title"] for d in hunch.get("decisions") or [] if f in d["files"]]
            text = (f"`{f}` in {r['label']} needed {len(cs)} fix commits since the last brief:\n"
                    + "\n".join(f"- {c['short']} {c['subject']}" for c in cs[:6])
                    + "\n\nFind the shared root cause and add a regression test that would "
                      "have caught all of them, instead of another point fix.")
            if decs or inv:
                text += "\n\nHunch context: " + "; ".join(
                    [f"decision '{d}'" for d in decs[:2]] + [f"invariant '{i}'" for i in inv[:2]])
            proposals.append(_proposal(
                queue, f"Fix hotspot: {f} ({len(cs)} fixes)", text,
                "Repeated fixes to one file usually mean a missing invariant or test.",
                [c["short"] for c in cs], f"hotspot:{r['path']}:{f}"))
        for c in r.get("commits") or []:
            if c["type"] == "revert" or c["subject"].lower().startswith("revert "):
                proposals.append(_proposal(
                    queue, f"Re-land or close out reverted change ({c['short']})",
                    (f"{r['label']} reverted a change: {c['subject']} ({c['short']}).\n"
                     "Decide whether it should be re-landed with a fix, or record why it "
                     "was abandoned (Hunch decision) so nobody re-tries it blind."),
                    "Reverts without a recorded reason tend to get re-attempted.",
                    [c["short"]], f"revert:{r['path']}:{c['sha']}"))
        # A decision whose anchored file changed after it was recorded may
        # have drifted -- the "why" quoted above might no longer be true. One
        # proposal per repo (not per decision) keeps a busy file from
        # flooding the brief.
        drifted = []
        for d in (r.get("hunch") or {}).get("decisions") or []:
            vts = d.get("verified_ts") or 0
            touching = [c for c in r.get("commits") or []
                        if c["ts"] > vts and set(c["files"]) & set(d["files"])]
            if touching:
                drifted.append((d, touching))
        if drifted:
            lines = [f"- {d['id']} '{d['title'][:90]}' (recorded {d['date']}; "
                     f"{', '.join(d['files'][:2])} changed in "
                     f"{', '.join(c['short'] for c in t[:3])})" for d, t in drifted[:8]]
            if len(drifted) > 8:
                lines.append(f"- ...and {len(drifted) - 8} more")
            shas = sorted({c["short"] for _, t in drifted for c in t})
            proposals.append(_proposal(
                queue, f"Re-verify {len(drifted)} Hunch decision(s) in {r['label']}",
                ("These recorded decisions are anchored to files that changed since they "
                 "were recorded. Confirm each still holds, or supersede it:\n"
                 + "\n".join(lines)),
                "Stale decisions quietly turn into wrong advice for the next agent.",
                shas[:8], f"hunch-drift:{r['path']}", "low"))

    # Mark proposals already shown recently so the brief can say "still open".
    cutoff = now - PROPOSAL_MEMORY_DAYS * 86400
    for p in proposals:
        first = (memory.get("proposals") or {}).get(p["key"])
        p["first_seen_ts"] = first if first and first >= cutoff else now
        p["seen_before"] = bool(first and first >= cutoff and first < now)
    proposals.sort(key=lambda p: (p["seen_before"], {"high": 0, "normal": 1, "low": 2}.get(p["priority"], 1)))

    # -- what to do next ---------------------------------------------------
    for s in stuck:
        if s["severity"] == "low":
            continue
        cmd = {
            "blocked": f"wt answer {s['ref']}",
            "gated": f"wt ack {s['ref']}",
            "queue_stalled": f"wt ls -q {s['ref']}",
        }.get(s["kind"], "")
        verb = {
            "blocked": f"Answer {s['ref']}",
            "gated": f"Ack or Nack {s['ref']}",
            "pending_tool": "Approve or deny the paused tool call",
            "question_blocked": "Answer the question",
            "soft_block": "Reply so it can continue",
            "sidecar_waiting": "Answer the side question",
            "stale_tool_call": "Check whether the tool call is hung",
            "inject_stuck": "Check the undelivered message (inject-receipt)",
            "queue_stalled": f"Triage queue {s['ref']}",
            "unpushed": "Push (or discard) the local commits",
        }.get(s["kind"], "Take a look")
        nxt.append({"action": verb, "why": s["title"], "severity": s["severity"],
                    "source": s["source"], "command": cmd})
    nxt = nxt[:MAX_NEXT_ITEMS]
    old_blocked = [s for s in stuck if s["kind"] == "blocked_stale"]
    if old_blocked:
        oldest = max(float(s["age_s"]) for s in old_blocked)
        nxt.append({"action": f"Sweep {len(old_blocked)} long-blocked ticket(s): answer or close",
                    "why": f"Blocked over {cfg['stale_blocked_days']} days (oldest {_age(oldest)}).",
                    "severity": "low", "source": "watchtower", "command": "wt blocked"})
    fresh = [p for p in proposals if not p["seen_before"]]
    if fresh:
        nxt.append({"action": f"Review {len(fresh)} proposed ticket(s) below and file the ones worth doing",
                    "why": "Dry-run proposals; nothing was filed.", "severity": "low",
                    "source": "instinct", "command": ""})

    totals = {
        "repos_watched": len(snap["repos"]),
        "repos_changed": len(changed),
        "commits": sum(c["count"] for c in changed),
        "stuck": len(stuck),
        "stuck_high": sum(1 for s in stuck if s["severity"] == "high"),
        "proposals": len(proposals),
        "proposals_new": len(fresh),
    }
    return {
        "schema": SCHEMA_VERSION,
        "generated_ts": now,
        "since_ts": snap["since_ts"],
        "headline": _headline(totals),
        "totals": totals,
        "changed": changed,
        "stuck": stuck,
        "next": nxt,
        "proposals": proposals,
        "blind_spots": list(snap.get("blind_spots") or []),
    }


def _headline(t: dict) -> str:
    parts = [f"{t['commits']} commit(s) across {t['repos_changed']} repo(s)"]
    if t["stuck_high"]:
        parts.append(f"{t['stuck_high']} thing(s) blocked on you")
    elif t["stuck"]:
        parts.append(f"{t['stuck']} thing(s) worth a look")
    else:
        parts.append("nothing stuck")
    if t["proposals_new"]:
        parts.append(f"{t['proposals_new']} new ticket idea(s)")
    return ", ".join(parts) + "."


# ---------------------------------------------------------------- render ---

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d9;--card:#fff;
--high:#b42318;--medium:#b54708;--low:#475467;--accent:#3538cd}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;
--line:#2e2d2a;--card:#1f1e1c;--high:#f97066;--medium:#fdb022;--low:#98a2b3;--accent:#a4bcfd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;
padding-bottom:6px;border-bottom:1px solid var(--line)}h3{font-size:15px;margin:18px 0 6px}
.meta,.muted{color:var(--muted);font-size:13px}.headline{font-size:17px;margin:14px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.tile b{display:block;font-size:22px}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px 16px;margin:10px 0}
.sev{display:inline-block;font-size:11px;font-weight:600;text-transform:uppercase;
letter-spacing:.04em;padding:1px 7px;border-radius:99px;border:1px solid currentColor}
.sev-high{color:var(--high)}.sev-medium{color:var(--medium)}.sev-low{color:var(--low)}
ul{padding-left:20px;margin:6px 0}li{margin:3px 0}code,pre{font:12.5px/1.45 ui-monospace,
SFMono-Regular,Menlo,monospace}pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);
border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:8px 0 0}
.why{border-left:3px solid var(--accent);padding:2px 0 2px 10px;margin:8px 0;font-size:14px}
.sha{color:var(--muted)}ol.next li{margin:6px 0}.tag{font-size:12px;color:var(--muted)}
details summary{cursor:pointer;color:var(--muted);font-size:13px}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _fmt_ts(ts) -> str:
    return _dt.datetime.fromtimestamp(float(ts)).strftime("%a %d %b %Y, %H:%M")


def render_html(brief: dict) -> str:
    t = brief["totals"]
    out = []
    w = out.append
    w("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    w("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    w("<meta name=\"robots\" content=\"noindex,nofollow\">")
    w(f"<title>Instinct brief · {_e(_dt.datetime.fromtimestamp(brief['generated_ts']).strftime('%Y-%m-%d'))}</title>")
    w(f"<style>{_CSS}</style></head><body><main>")
    w("<h1>What changed, what's stuck, what's next</h1>")
    w(f"<div class=\"meta\">Instinct brief · {_e(_fmt_ts(brief['since_ts']))} → "
      f"{_e(_fmt_ts(brief['generated_ts']))}</div>")
    w(f"<p class=\"headline\">{_e(brief['headline'])}</p>")
    w("<div class=\"tiles\">")
    for label, val in (("Commits", t["commits"]), ("Repos changed", f"{t['repos_changed']}/{t['repos_watched']}"),
                       ("Blocked on you", t["stuck_high"]), ("Worth a look", t["stuck"] - t["stuck_high"]),
                       ("Ticket ideas", t["proposals"])):
        w(f"<div class=\"tile\"><span class=\"muted\">{_e(label)}</span><b>{_e(val)}</b></div>")
    w("</div>")

    w("<h2>What to do next</h2>")
    if brief["next"]:
        w("<ol class=\"next\">")
        for n in brief["next"]:
            cmd = f" <code>{_e(n['command'])}</code>" if n.get("command") else ""
            w(f"<li><span class=\"sev sev-{_e(n['severity'])}\">{_e(n['severity'])}</span> "
              f"<b>{_e(n['action'])}</b>{cmd} <span class=\"muted\">— {_e(n['why'])}</span></li>")
        w("</ol>")
    else:
        w("<p class=\"muted\">Nothing needs you right now.</p>")

    w("<h2>What's stuck</h2>")
    if not brief["stuck"]:
        w("<p class=\"muted\">Nothing stuck.</p>")
    for s in brief["stuck"]:
        w("<div class=\"card\">")
        w(f"<span class=\"sev sev-{_e(s['severity'])}\">{_e(s['severity'])}</span> "
          f"<b>{_e(s['title'])}</b>")
        meta = " · ".join(x for x in (s.get("source"), s.get("repo"),
                                       f"{s['age']} old" if s.get("age") else "") if x)
        w(f"<div class=\"tag\">{_e(meta)}</div>")
        if s.get("detail"):
            w(f"<div>{_e(s['detail'])}</div>")
        w("</div>")

    w("<h2>What changed</h2>")
    if not brief["changed"]:
        w("<p class=\"muted\">No commits in the window.</p>")
    for c in brief["changed"]:
        w("<div class=\"card\">")
        w(f"<h3>{_e(c['repo'])} <span class=\"muted\">· {c['count']} commit(s) by "
          f"{_e(', '.join(c['authors']))}</span></h3>")
        for g in c["groups"]:
            w(f"<div class=\"tag\">{_e(g['label'])}</div><ul>")
            for cm in g["commits"][:12]:
                w(f"<li><code class=\"sha\">{_e(cm['short'])}</code> {_e(cm['subject'])}</li>")
            if len(g["commits"]) > 12:
                w(f"<li class=\"muted\">…and {len(g['commits']) - 12} more</li>")
            w("</ul>")
        if c["hot_files"]:
            w("<div class=\"tag\">Most-touched: " + ", ".join(
                f"<code>{_e(h['file'])}</code>×{h['commits']}" for h in c["hot_files"]) + "</div>")
        hunch = c.get("hunch") or {}
        if any(d.get("weight", 0) >= 0 for d in hunch.get("decisions") or []) or hunch.get("constraints"):
            w("<details open><summary>Why it's built this way (Hunch)</summary>")
            # Negative weight = an auto-captured diff note, not a recorded why.
            for d in [d for d in hunch.get("decisions", []) if d.get("weight", 0) >= 0][:4]:
                w(f"<div class=\"why\"><b>{_e(d['title'])}</b> <span class=\"muted\">"
                  f"({_e(d['date'])} · {_e(', '.join(d['files'][:3]))})</span><br>{_e(d['decision'][:400])}")
                if d.get("rejected"):
                    w("<br><span class=\"muted\">Rejected: " + _e("; ".join(str(x)[:140] for x in d["rejected"])) + "</span>")
                w("</div>")
            for k in hunch.get("constraints", [])[:4]:
                w(f"<div class=\"why\"><span class=\"sev sev-{'high' if k['severity'] in ('error', 'block') else 'medium'}\">"
                  f"invariant</span> {_e(k['statement'][:300])} <span class=\"muted\">"
                  f"({_e(', '.join(k['files'][:3]))})</span></div>")
            w("</details>")
        elif not c.get("hunch_present"):
            w("<div class=\"tag\">No Hunch graph in this repo, so no recorded \"why\".</div>")
        w("</div>")

    w("<h2>Proposed tickets <span class=\"muted\">(dry run, nothing filed)</span></h2>")
    if not brief["proposals"]:
        w("<p class=\"muted\">No ticket ideas today.</p>")
    for p in brief["proposals"]:
        w("<div class=\"card\">")
        status = "still open from an earlier brief" if p["seen_before"] else "new"
        w(f"<b>{_e(p['title'])}</b> <span class=\"tag\">· {_e(p['queue'] or 'no queue mapped')} "
          f"· {_e(p['priority'])} · {_e(status)}</span>")
        w(f"<div class=\"muted\">{_e(p['rationale'])}</div>")
        w(f"<pre>{_e(p['text'])}</pre>")
        w(f"<details><summary>File it</summary><pre>{_e(p['wt_command'])}</pre></details>")
        w("</div>")

    if brief["blind_spots"]:
        w("<h2>Blind spots</h2><ul>")
        for b in brief["blind_spots"]:
            w(f"<li class=\"muted\">{_e(b)}</li>")
        w("</ul>")
    w("<p class=\"meta\" style=\"margin-top:40px\">Generated by CCC Instinct. Read-only: "
      "it filed no tickets and messaged no one.</p>")
    w("</main></body></html>")
    return "\n".join(out)


# ----------------------------------------------------------------- state ---

def _write_private(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(out_dir: Path) -> dict:
    try:
        s = json.loads((out_dir / "state.json").read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def window_start(state: dict, cfg: dict, now: float, since_hours=None) -> float:
    """Since the last successful brief, bounded to ``max_window_hours``."""
    if since_hours is not None:
        return now - float(since_hours) * 3600
    last = state.get("last_run_ts")
    floor = now - float(cfg["max_window_hours"]) * 3600
    if isinstance(last, (int, float)) and floor <= last < now:
        return float(last)
    return now - float(cfg["window_hours"]) * 3600


def next_state(state: dict, brief: dict) -> dict:
    now = brief["generated_ts"]
    cutoff = now - PROPOSAL_MEMORY_DAYS * 86400
    props = {k: v for k, v in (state.get("proposals") or {}).items()
             if isinstance(v, (int, float)) and v >= cutoff}
    for p in brief["proposals"]:
        props.setdefault(p["key"], p["first_seen_ts"])
    return {"last_run_ts": now, "proposals": props}


def publish(cfg: dict, html_path: Path, title: str) -> tuple[str | None, str | None]:
    """Run the opt-in ``publish_command``; its last stdout line is the URL."""
    argv = cfg.get("publish_command")
    if not argv:
        return None, None
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return None, "publish_command must be a list of strings"
    argv = [a.replace("{html}", str(html_path)).replace("{title}", title) for a in argv]
    out, err = _run([os.path.expanduser(argv[0])] + argv[1:], timeout=120)
    if err:
        return None, err
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return (lines[-1] if lines else None), None


# ------------------------------------------------------------------- cli ---

def run_brief(cfg: dict, out_dir: Path, *, since_hours=None, use_ccc=True, use_wt=True,
              snapshot=None, do_publish=False, now=None) -> dict:
    state = load_state(out_dir)
    if snapshot is None:
        now = now or time.time()
        snapshot = collect(cfg, window_start(state, cfg, now, since_hours),
                           use_ccc=use_ccc, use_wt=use_wt)
    brief = analyze(snapshot, cfg, state)
    day = _dt.datetime.fromtimestamp(brief["generated_ts"]).strftime("%Y-%m-%d")
    html_path = out_dir / f"brief-{day}.html"
    _write_private(html_path, render_html(brief))
    _write_private(out_dir / f"brief-{day}.json", json.dumps(brief, indent=2, default=str))
    _write_private(out_dir / f"proposals-{day}.json",
                   json.dumps(brief["proposals"], indent=2, default=str))
    _write_private(out_dir / "latest.html", render_html(brief))
    brief["html_path"] = str(html_path)
    if do_publish:
        url, err = publish(cfg, html_path, f"Instinct brief {day}")
        brief["published_url"], brief["publish_error"] = url, err
    _write_private(out_dir / "state.json", json.dumps(next_state(state, brief), indent=2))
    return brief


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ccc_server.instinct",
                                 description="Proactive daily brief across your repos.")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("brief", help="collect, analyze, and write today's brief")
    b.add_argument("--config", type=Path, default=None)
    b.add_argument("--out-dir", type=Path, default=None)
    b.add_argument("--since", type=float, default=None, metavar="HOURS",
                   help="override the window (default: since the last brief)")
    b.add_argument("--no-ccc", action="store_true", help="skip the CCC dashboard API")
    b.add_argument("--no-wt", action="store_true", help="skip WatchTower")
    b.add_argument("--snapshot", type=Path, default=None,
                   help="render from a saved snapshot JSON instead of collecting")
    b.add_argument("--save-snapshot", type=Path, default=None)
    b.add_argument("--publish", action="store_true",
                   help="run the configured publish_command on the HTML")
    b.add_argument("--json", action="store_true", help="print the brief JSON to stdout")
    c = sub.add_parser("init-config", help="write an example config if none exists")
    c.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "init-config":
        path = args.config or DEFAULT_CONFIG_PATH
        if path.exists():
            print(f"instinct: {path} already exists; not overwriting", file=sys.stderr)
            return 1
        _write_private(path, json.dumps(example_config(), indent=2) + "\n")
        print(path)
        return 0
    if args.cmd != "brief":
        ap.print_help()
        return 2

    cfg = load_config(args.config)
    out_dir = args.out_dir or DEFAULT_OUT_DIR
    snapshot = None
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
    elif args.save_snapshot:
        state = load_state(out_dir)
        now = time.time()
        snapshot = collect(cfg, window_start(state, cfg, now, args.since),
                           use_ccc=not args.no_ccc, use_wt=not args.no_wt)
        _write_private(args.save_snapshot, json.dumps(snapshot, indent=2, default=str))
    brief = run_brief(cfg, out_dir, since_hours=args.since, use_ccc=not args.no_ccc,
                      use_wt=not args.no_wt, snapshot=snapshot, do_publish=args.publish)
    if args.json:
        print(json.dumps(brief, indent=2, default=str))
    else:
        print(brief["headline"])
        print(brief["html_path"])
        if brief.get("published_url"):
            print(brief["published_url"])
        if brief.get("publish_error"):
            print(f"publish failed: {brief['publish_error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
