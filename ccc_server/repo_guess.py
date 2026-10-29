# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Guess which repo a new session belongs in from its first prompt.

``POST /api/repo/guess`` -> ``repo_guess_request(body)``. Two passes:

1. Local, no network: an absolute / ~ path in the prompt that lies inside a
   known repo, else a single repo whose folder name appears as a whole word.
2. Optional TypeSafe Jev call, only when a key is configured (env
   ``JEV_API_KEY``, a BYOK ``jev`` key, or a Jev Vault entry). The prompt is
   scrubbed of likely secrets first; repos are sent as opaque labels
   (R1, R2...) with short descriptions, never as paths. Short timeout, no
   retries, any failure falls back to the local result.

Names still living in server.py are reached via ``_core`` at call time, same
convention as every other ccc_server module. Top-level names are prefixed so
adoption into server globals cannot shadow anything."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ccc_server import core as _core

_RG_JEV_URL = "https://api.typesafe.ai/v1/systemone"
_RG_JEV_TIMEOUT_S = 3
_RG_MAX_CANDIDATES = 15
_RG_ENOUGH_SCORED = 5
# Folders pasted images, transcripts and attachments live in: a path there is
# not where the work happens.
_RG_IGNORED_ROOTS = ("~/Desktop", "~/Downloads", "~/Documents", "~/Movies", "~/Pictures",
                     "/tmp", "/private", "/var")
_RG_NAME_STOPLIST = frozenset((
    "test", "tests", "apps", "app", "dev", "src", "docs", "tools", "web", "api", "work",
    "scratch", "tmp", "data", "notes", "projects", "code", "repo", "repos",
))
# Words too common in prompts to stand for a repo by their first segment.
_RG_LEAD_STOPLIST = frozenset(("claude", "codex", "chat", "next", "the", "new", "old", "my"))
_RG_MAX_PROMPT_CHARS = 3000
_RG_DESC_MAX = 300
_RG_OVERRIDE_MAX = 500
_RG_OVERRIDE_REL = Path(".claude") / "ccc-repo-description.md"
_RG_DESC_SOURCES = ("README.md", "CLAUDE.md", "AGENTS.md")
_RG_READ_BYTES = 16384
_RG_LOCAL_CONF = 0.9

_RG_INSTRUCTIONS = (
    "A developer starts a new AI coding-agent session with first_message. "
    "Which workspace should it launch in? Pick the project the work is about, "
    "even if the message never names it. last_used_workspace is where their "
    "previous session ran: a weak hint, prefer it only when the message fits "
    "it or is ambiguous."
)
_RG_MENTION_NOTE = (
    " workspaces_mentioned lists workspaces whose name or a file path appears in "
    "the message; a mention is evidence, but the message may only be referencing "
    "a file there while the work belongs elsewhere."
)

# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------

_RG_SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b[A-Za-z]{2,12}_[A-Za-z0-9]{20,}\b"),
]
_RG_KV_PATTERN = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|token|secret|api[_-]?key|access[_-]?key|auth)"
    r"(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|\S+)"
)
# 32+ base64 / hex chars. The lookarounds keep path segments and hyphenated
# slugs (branch names, folder names) from being mistaken for key material.
_RG_LONG_RUN = re.compile(r"(?<![\w/.\-])[A-Za-z0-9+/]{32,}={0,2}(?![\w/\-])")


def repo_guess_scrub(text):
    """Replace likely secrets in ``text`` with ``[REDACTED]``."""
    out = str(text or "")
    for pat in _RG_SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    out = _RG_KV_PATTERN.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", out)
    out = _RG_LONG_RUN.sub("[REDACTED]", out)
    return out


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _rg_main_repo_of(path):
    """Main-checkout path for a worktree dir, else None."""
    marker = os.sep + ".claude" + os.sep + "worktrees" + os.sep
    if marker in path:
        return path.split(marker, 1)[0]
    head, base = os.path.split(path)
    if "-wt-" in base:
        main = base.split("-wt-", 1)[0]
        if main:
            return os.path.join(head, main)
    return None


def _rg_norm(path):
    try:
        return str(Path(str(path)).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        return ""


def _rg_scores(paths):
    """{path: usage score} from the same memoised ranking /api/repo/list uses.

    Never triggers a scan on the request path: a cold cache (nothing computed
    yet) yields {} and starts one background computation for next time;
    a stale cache is used as-is."""
    try:
        cache = _core._REPO_SIGNALS_CACHE
        data = cache.get("data")
        if data is None:
            threading.Thread(target=_core._compute_repo_usage_signals,
                             args=(list(paths),), daemon=True).start()
            return {}
        return {p: float((data.get(p) or {}).get("score") or 0.0) for p in paths}
    except Exception:
        return {}


def _rg_candidates_scored(current_repo):
    """(candidates, collapse_map, scores).

    candidates: up to 15 existing repo dirs: usage-ranked first, then recents,
    then the rest (score-0 leftovers dropped once enough are scored);
    worktrees fold into their main repo when it is known; container folders
    (strict ancestors of other known repos) never appear. collapse_map: every
    known non-container path -> the path representing it, so a path typed
    inside a worktree still resolves to the main repo."""
    recents = []
    try:
        recents = [_rg_norm(p) for p in _core._load_recent_repos()]
    except Exception:
        pass
    others = []
    try:
        others = list(_core._known_repo_paths())
    except Exception:
        pass
    cur = _rg_norm(current_repo) if current_repo else ""
    seen = set()
    known = []
    for p in recents + [_rg_norm(x) for x in others] + ([cur] if cur else []):
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            known.append(p)
    pref = {p + os.sep for p in known}
    containers = {p for p in known
                  if any(q != p and q.startswith(p.rstrip(os.sep) + os.sep) for q in known)}
    known = [p for p in known if p not in containers]
    kset = set(known)
    collapse = {}
    for p in known:
        main = _rg_main_repo_of(p)
        collapse[p] = main if (main and main in kset) else p
    reps = []
    for p in known:
        if collapse[p] not in reps:
            reps.append(collapse[p])
    raw_scores = _rg_scores(known)
    scores = {}
    for p in known:
        rep = collapse[p]
        scores[rep] = max(scores.get(rep, 0.0), raw_scores.get(p, 0.0))
    scored = sorted((r for r in reps if scores.get(r, 0.0) > 0), key=lambda r: -scores[r])
    rec = [r for r in dict.fromkeys(collapse.get(p, p) for p in recents) if r in set(reps)]
    ordered = list(scored)
    ordered += [r for r in rec if r not in ordered]
    rest = [r for r in reps if r not in ordered]
    if len(scored) < _RG_ENOUGH_SCORED:
        ordered += rest
    cands = ordered[:_RG_MAX_CANDIDATES]
    cur_rep = collapse.get(cur) if cur else None
    if cur_rep and cur_rep not in cands:
        cands = cands[:_RG_MAX_CANDIDATES - 1] + [cur_rep]
    return cands, collapse, scores


def _rg_candidates(current_repo):
    cands, collapse, _ = _rg_candidates_scored(current_repo)
    return cands, collapse


# ---------------------------------------------------------------------------
# Local pass
# ---------------------------------------------------------------------------

_RG_PATH_TOKEN = re.compile(r"(?:(?<=[\s\"'`(\[=,:])|^)(~(?:/[^\s\"'`)\]>,;]*)?|/[^\s\"'`)\]>,;]+)")


def _rg_ignored_path(tok):
    """True for tokens that are attachments/config, not the work location."""
    home = str(Path.home())
    full = home + tok[1:] if tok.startswith("~") else tok
    if any(part.startswith(".") and part not in (".", "..") for part in full.split("/")):
        return True
    for root in _RG_IGNORED_ROOTS:
        r = home + root[1:] if root.startswith("~") else root
        if full == r or full.startswith(r + "/"):
            return True
    return False


def _rg_match_path(prompt, collapse):
    best = None
    best_len = -1
    home = str(Path.home())
    for m in _RG_PATH_TOKEN.finditer(prompt or ""):
        tok = m.group(1).rstrip(".:!?")
        if _rg_ignored_path(tok):
            continue
        if tok.startswith("~"):
            tok = home + tok[1:]
        tok = os.path.normpath(tok)
        for known, rep in collapse.items():
            if tok == known or tok.startswith(known.rstrip(os.sep) + os.sep):
                if len(known) > best_len:
                    best, best_len = rep, len(known)
    return best


def _rg_acronym(base):
    parts = [x for x in base.split("-") if x]
    if len(parts) >= 3:
        return "".join(x[0] for x in parts).lower()
    return ""


def _rg_word_in(prompt, word):
    # Not inside a path token (neighbouring "/"), a hyphenated slug, or a
    # filename ("billing.py").
    pat = r"(?<![\w/.\-])" + re.escape(word) + r"(?![\w/\-]|\.\w)"
    return re.search(pat, prompt, re.IGNORECASE) is not None


def _rg_match_names(prompt, cands):
    bases = {p: os.path.basename(p.rstrip(os.sep)) for p in cands}
    lower = [b.lower() for b in bases.values()]
    acr = {}
    for p, b in bases.items():
        a = _rg_acronym(b)
        if a and lower.count(a) == 0:
            acr.setdefault(a, []).append(p)
    hits = []
    for p, base in bases.items():
        if len(base) < 3 or base.lower() in _RG_NAME_STOPLIST:
            continue
        if _rg_word_in(prompt, base):
            hits.append(p)
    for a, owners in acr.items():
        if len(owners) == 1 and a not in _RG_NAME_STOPLIST and owners[0] not in hits \
                and _rg_word_in(prompt, a):
            hits.append(owners[0])
    # Leading segment of a hyphen/underscore name ("ERGO" for ERGO-NEXT), when
    # exactly one repo owns it and no repo is literally named that.
    lead = {}
    for p, b in bases.items():
        seg = re.split(r"[-_ ]", b, 1)[0].lower()
        if seg != b.lower() and len(seg) >= 3 and seg not in _RG_NAME_STOPLIST \
                and seg not in _RG_LEAD_STOPLIST and seg not in lower:
            lead.setdefault(seg, []).append(p)
    for seg, owners in lead.items():
        if len(owners) == 1 and owners[0] not in hits and _rg_word_in(prompt, seg):
            hits.append(owners[0])
    return hits


# ---------------------------------------------------------------------------
# Repo descriptions (cached by (mtime_ns, size); persisted)
# ---------------------------------------------------------------------------

_RG_DESC_LOCK = threading.Lock()
_RG_DESC_CACHE = {}
_RG_DESC_LOADED_FROM = [None]


def _rg_desc_file():
    return _core.COMMAND_CENTER_STATE_DIR / "repo-descriptions.json"


def _rg_desc_load():
    f = _rg_desc_file()
    if _RG_DESC_LOADED_FROM[0] == str(f):
        return
    _RG_DESC_CACHE.clear()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(v.get("desc"), str):
                    _RG_DESC_CACHE[k] = v
    except (OSError, ValueError):
        pass
    _RG_DESC_LOADED_FROM[0] = str(f)


def _rg_desc_save():
    f = _rg_desc_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(_RG_DESC_CACHE, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(f)
    except OSError:
        pass


def _rg_read_head(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(_RG_READ_BYTES)


def _rg_collapse_ws(text):
    return " ".join(str(text).split())


# Tool-managed blocks (`<!-- name:start -->...<!-- name:end -->`, e.g. a
# memory plugin's section at the top of CLAUDE.md) say nothing about the repo.
_RG_MANAGED_BLOCK = re.compile(r"<!--\s*([\w.-]+):start\s*-->.*?<!--\s*\1:end\s*-->", re.S)
_RG_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_RG_MD_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
# A first paragraph shorter than this keeps reading the next one.
_RG_DESC_MIN_BODY = 120
# Bump when the summarizer changes so cached descriptions are rebuilt.
_RG_DESC_VERSION = 2


def _rg_link_only(line):
    """True for nav lines like `**English** · [日本語](README.ja.md)`."""
    if not _RG_MD_LINK.search(line):
        return False
    rest = re.sub(r"[*_`·•|/,\-\s]+", " ", _RG_MD_LINK.sub(" ", line)).strip()
    return len(rest) < 12


def _rg_summarize_markdown(text, limit=_RG_DESC_MAX):
    text = _RG_HTML_COMMENT.sub("", _RG_MANAGED_BLOCK.sub("", text))
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
    heading = ""
    para = []
    in_fence = False
    for line in lines[i:]:
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not heading and s.startswith("#"):
            heading = s.lstrip("#").strip()
            continue
        long_enough = len(" ".join(para)) >= _RG_DESC_MIN_BODY
        if not s or s.startswith(("#", "![", "[![", "<", "|", "---", "===")) or _rg_link_only(s):
            if para and long_enough:
                break
            continue
        para.append(s.lstrip("-*+ ") if s[:2] in ("- ", "* ", "+ ") else s)
    body = _rg_collapse_ws(" ".join(para))
    head = _rg_collapse_ws(heading)
    if head and body:
        out = f"{head}: {body}"
    else:
        out = head or body
    return out[:limit].rstrip()


def _rg_desc_sources(repo):
    """[(kind, path, [mtime_ns, size])]: the override alone if present, else
    every doc that exists. README goes stale; CLAUDE.md / AGENTS.md are kept
    current for agents, so they are read too and listed first."""
    found = []
    for kind, name in (("override", _RG_OVERRIDE_REL),) + tuple(("doc", n) for n in _RG_DESC_SOURCES):
        path = Path(repo) / name
        try:
            st = path.stat()
        except OSError:
            continue
        if kind == "doc" and not os.path.isfile(path):
            continue
        if kind == "override":
            return [(kind, path, [st.st_mtime_ns, st.st_size])]
        found.append((kind, path, [st.st_mtime_ns, st.st_size]))
    return found


def repo_guess_describe(repo):
    """Short description of a repo for the Jev criteria."""
    base = os.path.basename(str(repo).rstrip(os.sep)) or str(repo)
    fallback = f"Folder {base}"
    srcs = _rg_desc_sources(repo)
    sig = [_RG_DESC_VERSION] + [[str(p.name), *st] for _, p, st in srcs]
    with _RG_DESC_LOCK:
        _rg_desc_load()
        hit = _RG_DESC_CACHE.get(repo)
        if hit and hit.get("sig") == sig:
            return hit["desc"] or fallback
    parts = []
    per_doc = max(_RG_DESC_MAX // max(len(srcs), 1), 120)
    for kind, path, _ in srcs:
        try:
            raw = _rg_read_head(path)
        except OSError:
            continue
        if kind == "override":
            parts.append(_rg_collapse_ws(raw)[:_RG_OVERRIDE_MAX])
        else:
            part = _rg_summarize_markdown(raw, per_doc)
            if part and part not in parts:
                parts.append(part)
    desc = " / ".join(parts)[:max(_RG_DESC_MAX, _RG_OVERRIDE_MAX if srcs and srcs[0][0] == "override" else 0)].rstrip()
    desc = desc or fallback
    with _RG_DESC_LOCK:
        _rg_desc_load()
        _RG_DESC_CACHE[repo] = {"sig": sig, "desc": desc}
        _rg_desc_save()
    return desc


# ---------------------------------------------------------------------------
# Recent work: the user's own session titles per repo
# ---------------------------------------------------------------------------

# What you actually do in a repo says more than its README. Titles come from
# the in-memory transcript-metadata cache, indexed off the request path in a
# background thread at most once per TTL; a request reads the last snapshot.
_RG_RECENT_PER_REPO = 6
# Older titles kept per repo so one that matches the prompt can still be sent.
_RG_RECENT_INDEX_PER_REPO = 300
_RG_RECENT_MATCHES = 6
_RG_RECENT_TTL_S = 600
_RG_WORD = re.compile(r"[a-z0-9]{4,}")
_RG_COMMON_WORDS = frozenset((
    "about", "after", "again", "also", "check", "could", "does", "doesn", "from", "have",
    "into", "just", "like", "make", "more", "need", "only", "should", "some", "start",
    "still", "than", "that", "their", "them", "then", "there", "they", "this", "what",
    "when", "where", "which", "while", "with", "would", "your", "issue", "issues", "work",
))
_RG_RECENT = {"at": 0.0, "by_slug": None, "building": False}
_RG_RECENT_LOCK = threading.Lock()


def _rg_build_recent_titles():
    by_slug = None
    try:
        with _core._conv_meta_cache_lock:
            items = list(_core._conv_meta_cache.items())
        rows = {}
        for key, meta in items:
            if not isinstance(meta, dict):
                continue
            title = str(meta.get("custom_title") or meta.get("ai_title") or "").strip()
            if not title:
                continue
            if " " not in title:  # launch slugs like `fix-booking-page`
                title = title.replace("-", " ")
            ts = float(meta.get("last_meaningful_ts") or meta.get("mtime") or 0)
            rows.setdefault(os.path.basename(os.path.dirname(str(key))), []).append((ts, title[:80]))
        by_slug = {}
        for slug, rs in rows.items():
            rs.sort(reverse=True)
            keep, seen = [], set()
            for ts, t in rs:
                if t.lower() not in seen:
                    seen.add(t.lower())
                    keep.append((ts, t))
                if len(keep) >= _RG_RECENT_INDEX_PER_REPO:
                    break
            by_slug[slug] = keep
    except Exception:
        by_slug = None
    with _RG_RECENT_LOCK:
        if by_slug is not None:
            _RG_RECENT["by_slug"] = by_slug
        _RG_RECENT["at"] = time.time()
        _RG_RECENT["building"] = False


def _rg_prompt_words(prompt):
    return {w for w in _RG_WORD.findall(str(prompt or "").lower()) if w not in _RG_COMMON_WORDS}


def _rg_recent_titles(paths, prompt=""):
    """Session titles across ``paths`` (a repo and its worktrees): older ones
    that share a word with ``prompt`` first, then the newest. Never scans on
    the request path: a stale or cold index starts one background rebuild and
    this call uses what it has (maybe nothing)."""
    with _RG_RECENT_LOCK:
        by_slug = _RG_RECENT["by_slug"]
        if time.time() - _RG_RECENT["at"] > _RG_RECENT_TTL_S and not _RG_RECENT["building"]:
            _RG_RECENT["building"] = True
            threading.Thread(target=_rg_build_recent_titles, daemon=True).start()
    if not by_slug:
        return []
    rows = []
    for p in dict.fromkeys(paths):
        try:
            rows.extend(by_slug.get(_core._encode_project_slug(p), ()))
        except Exception:
            continue
    rows.sort(reverse=True)
    titles = list(dict.fromkeys(t for _, t in rows))
    words = _rg_prompt_words(prompt)
    matches = [t for t in titles if words & set(_RG_WORD.findall(t.lower()))][:_RG_RECENT_MATCHES] if words else []
    return list(dict.fromkeys(matches + titles[:_RG_RECENT_PER_REPO]))


# ---------------------------------------------------------------------------
# Jev
# ---------------------------------------------------------------------------

# A failed Jev call falls back silently in the UI, so log it -- once per
# distinct error per window, since the composer re-guesses on every pause.
_RG_JEV_FAIL_LOG_EVERY_S = 600
_RG_JEV_FAIL_LAST = {}


def _rg_log_jev_failure(err):
    now = time.time()
    if now - _RG_JEV_FAIL_LAST.get(err, 0) < _RG_JEV_FAIL_LOG_EVERY_S:
        return
    _RG_JEV_FAIL_LAST[err] = now
    _core._log_activity("repoguess", "JEV_FAIL", err)


def _rg_jev_key():
    key = (os.environ.get("JEV_API_KEY") or "").strip()
    if key:
        return key
    try:
        from ccc_server import byok
        profiles = [p for p in byok.byok_list_profiles() if "jev" in p.get("providers", [])]
        profiles.sort(key=lambda p: p["name"] != "default")
        for p in profiles:
            k = (byok.byok_get_key(p["name"], "jev") or "").strip()
            if k:
                return k
    except Exception:
        pass
    try:
        from ccc_server import vault
        entries = vault.vault_list_entries()
        for entry in entries:
            if (entry.get("env_var") == "JEV_API_KEY" or entry.get("name") in
                    ("jev-api-key", "jev-api-key.password", "jev-api-key.token")):
                key = (vault.vault_get_value(entry["name"]) or "").strip()
                if key:
                    return key
    except Exception:
        pass
    return ""


def repo_guess_status():
    """Configuration only; never return a key or make a provider call."""
    return {"ok": True, "jev_configured": bool(_rg_jev_key())}


def _rg_ask_jev(key, prompt, cands, current_rep, mentioned=(), members=None):
    """-> (choice_path, confidence, [(path, prob)]) or None on any failure.

    ``members`` maps a candidate to every known path it stands for (its
    worktrees), whose session titles count as its recent work."""
    labels = {f"R{i + 1}": p for i, p in enumerate(cands)}
    by_path = {p: lab for lab, p in labels.items()}
    criteria = {}
    for lab, p in labels.items():
        base = os.path.basename(p.rstrip(os.sep))
        crit = f"{base}: {repo_guess_describe(p)}"
        recent = _rg_recent_titles([p, *((members or {}).get(p) or ())], prompt)
        if recent:
            crit += " Recent work: " + repo_guess_scrub("; ".join(recent))
        criteria[lab] = crit
    cur_label = by_path.get(current_rep) if current_rep else None
    ment = [by_path[p] for p in dict.fromkeys(mentioned) if p in by_path]
    body = {
        "model": "jev-latest",
        "state": {
            "first_message": repo_guess_scrub(prompt)[:_RG_MAX_PROMPT_CHARS],
            "last_used_workspace": cur_label,
            "workspaces_mentioned": ment,
        },
        "questions": {
            "repo": {"type": "choice", "instructions": _RG_INSTRUCTIONS + _RG_MENTION_NOTE, "criteria": criteria}
        },
    }
    try:
        req = urllib.request.Request(
            _RG_JEV_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "claude-command-center",
            },
        )
        with urllib.request.urlopen(req, timeout=_RG_JEV_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        answer = data["answers"]["repo"]
        choice = labels.get(str(answer["choice"]))
        if not choice:
            _rg_log_jev_failure(f"unknown choice={str(answer['choice'])[:40]!r}")
            return None
        conf = max(0.0, min(1.0, float(answer.get("confidence") or 0.0)))
        probs = []
        for lab, pr in (answer.get("probabilities") or {}).items():
            if lab in labels:
                probs.append((labels[lab], float(pr)))
        probs.sort(key=lambda t: -t[1])
        return choice, conf, probs
    except urllib.error.HTTPError as e:
        hint = " (bad or expired key)" if e.code in (401, 403) else ""
        _rg_log_jev_failure(f"http={e.code}{hint}")
        return None
    except Exception as e:
        _rg_log_jev_failure(f"{type(e).__name__}: {str(e)[:120]}")
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _rg_cand_row(path, prob):
    return {
        "repo_path": path,
        "label": os.path.basename(path.rstrip(os.sep)) or path,
        "probability": round(float(prob), 3),
    }


def repo_guess_request(body):
    """Payload for POST /api/repo/guess. Never raises."""
    started = time.time()

    def done(repo, conf, source, rows):
        return {
            "ok": True,
            "repo_path": repo,
            "confidence": round(float(conf), 3),
            "source": source,
            "candidates": rows,
            "latency_ms": int((time.time() - started) * 1000),
        }

    try:
        body = body if isinstance(body, dict) else {}
        prompt = str(body.get("prompt") or "")
        current = str(body.get("current_repo") or "").strip()
        cands, collapse = _rg_candidates(current)
        if not prompt.strip() or not cands:
            return done(None, 0.0, "none", [])
        path_hit = _rg_match_path(prompt, collapse)
        name_hits = _rg_match_names(prompt, cands)
        local = None
        # Local matches never auto-pick in the UI (only Jev does): on real
        # history a mentioned path or name was the work's repo only ~70% of
        # the time, so without Jev they surface as a suggestion, never a switch.
        if path_hit:
            local = done(path_hit, _RG_LOCAL_CONF, "path", [_rg_cand_row(path_hit, _RG_LOCAL_CONF)])
        elif len(name_hits) == 1:
            local = done(name_hits[0], _RG_LOCAL_CONF, "name", [_rg_cand_row(name_hits[0], _RG_LOCAL_CONF)])
        key = _rg_jev_key()
        if key:
            # Local matches are evidence, not decisions, once Jev can weigh
            # them: a prompt may only reference a file in another repo.
            jc = list(cands)
            if path_hit and path_hit not in jc:
                jc.append(path_hit)
            if len(jc) >= 2:
                cur_rep = collapse.get(_rg_norm(current)) if current else None
                members = {}
                for path, rep in collapse.items():
                    members.setdefault(rep, []).append(path)
                res = _rg_ask_jev(key, prompt, jc, cur_rep,
                                  ([path_hit] if path_hit else []) + name_hits, members)
                if res:
                    choice, conf, probs = res
                    return done(choice, conf, "jev", [_rg_cand_row(p, pr) for p, pr in probs[:5]])
        if local:
            return local
        return done(None, 0.0, "none", [_rg_cand_row(p, 0.0) for p in name_hits])
    except Exception:
        return done(None, 0.0, "none", [])
