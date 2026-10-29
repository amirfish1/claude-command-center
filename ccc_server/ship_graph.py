# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Commit and ticket graph linking sessions, git commits, and WatchTower tickets.

Exposes:
  - is_shipped(topic: str) -> dict:
      Answers 'did we ship X?' with:
      {'shipped': bool, 'confidence': float, 'evidence': [{'repo', 'commit', 'subject', 'session_id'}], 'tickets': [...]}
      Also adds 'verdict' (SHIPPED / PUSHED, NOT MERGED / COMMITTED ON <node>, NOT PUSHED /
      NOT FOUND on reachable nodes ...), 'origin_freshness', and evidence[0]['state']
      (on_default / on_remote_branch / local_only / unknown) -- multi-machine S2.
  - search_sessions(query: str, limit: int = 20) -> list[dict]:
      Re-ranks/augments ccc_server/session_fts.search_sessions results using
      commit matches and ticket expansion over the graph.

Persists index on disk under the CCC state directory (ship_graph.sqlite).
Refreshes incrementally:
  - Per-repo HEAD (only runs git log when HEAD changes)
  - Per-transcript (mtime, size)
  - WatchTower tickets (from ~/.local/share/watchtower/queues.db)
Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import federation
from ccc_server import github_quota as _github_quota

_base_search_sessions = None


def _get_base_search_sessions():
    global _base_search_sessions
    if _base_search_sessions is not None:
        return _base_search_sessions
    sfts_path = Path(__file__).resolve().parent / "session_fts.py"
    if sfts_path.exists():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("ccc_server_session_fts", sfts_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["ccc_server_session_fts"] = mod
                spec.loader.exec_module(mod)
                if hasattr(mod, "search_sessions"):
                    _base_search_sessions = mod.search_sessions
                    return _base_search_sessions
        except Exception:
            pass
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from ccc_server.session_fts import search_sessions
        _base_search_sessions = search_sessions
    except Exception:
        _base_search_sessions = lambda q, limit=20: []
    return _base_search_sessions

COMMIT_RE = re.compile(r"\[([\w./-]+)(?: \(root-commit\))? ([0-9a-f]{7,12})\] ([^\n\\]{3,160})")
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11}-\d{1,5})\b")
TICKET_STOP = re.compile(r"^(UTF|SHA|ISO|RFC|CVE|HTTP|TLS|GPT|MD|X|UUID|AES|RSA|P|H|E|W|U|A|B|C)-", re.I)
SCRATCH_RE = re.compile(r"(command-center-scratch|/private/var/|/tmp/|/var/folders/|scratch-|ccc-claude-midstream)", re.I)
CODEX_SID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", re.I)
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+#-]*")
# MEMO-FIX-lineage: "Continue in a new session" / auto-resume stamps this line
# into the successor's first user turn. Mirrors engines.py's
# _CONTINUATION_ORIGIN_RE -- duplicated (not imported) because ship_graph.py
# stays stdlib-only and independent of the live server's engines module.
CONTINUATION_ORIGIN_RE = re.compile(r"Origin session id: ([A-Za-z0-9][A-Za-z0-9_.-]{7,127})")

STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing done down during each few for from further had has have
having he her here hers him his how i if in into is it its itself just let lets me more most my myself no
nor not now of off on once only or other our ours out over own same she should so some such than that the
their them then there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours find
where remember recall worked working work done did do we i me my the that which built build already
have has is it there any way something thing things one ones ship shipped shipping feature task
ticket pr pull request commit commits repo repository support implemented implement
ever actually someone onto doesn don didn need needs try trying want wants sure make makes know knows yet
good get still used use uses using somewhere anywhere anybody somebody anyone
live merged merge complete completed finish finished exist exists existing currently today
able
""".split())

SYNONYMS = {
    "rid": ["remove", "drop", "delete"],
    "remove": ["rid", "drop", "delete", "strip", "clean"],
    "delete": ["remove", "drop", "rid"],
    "drop": ["remove", "delete", "rid"],
    "fix": ["resolve", "repair", "patch"],
    "fixed": ["resolve", "resolved", "repair", "repaired"],
    "resolve": ["fix", "repair"],
    "add": ["implement", "support", "introduce", "create"],
    "support": ["implement", "add"],
    "implement": ["support", "add"],
    "stop": ["prevent", "block", "avoid"],
    "prevent": ["stop", "block", "avoid"],
    "block": ["prevent", "stop", "avoid"],
    "hide": ["conceal", "suppress", "mask"],
    "show": ["display", "reveal", "render", "expose"],
    "display": ["show", "render"],
    "autoupdate": ["auto", "update"],
    "fastforward": ["fast", "forward"],
    "reinstall": ["re", "install"],
}

GENERIC_VERBS = frozenset({
    "add", "fix", "support", "implement", "get", "make", "do", "work",
    "create", "update", "try", "want", "need", "ship", "creat", "updat", "tri"
})

COMMON_PRODUCT_WORDS = frozenset({
    "flow", "flows", "queue", "queues", "session", "sessions", "page", "pages",
    "button", "buttons", "view", "views", "tab", "tabs", "panel", "panels",
    "modal", "modals", "dialog", "dialogs", "list", "lists", "menu", "menus",
    "icon", "icons", "input", "inputs", "window", "windows", "bar", "bars",
    "card", "cards", "screen", "screens", "header", "headers", "footer", "footers",
    "nav", "navigation", "sidebar", "sidebars", "banner", "banners", "toast", "toasts",
    "row", "rows", "column", "columns", "table", "tables", "item", "items",
    "link", "links", "board", "boards", "layout", "layouts", "strip", "strips",
    "worker", "workers", "client", "clients", "user", "users", "app", "apps",
    "mode", "modes",
})

CLAUSE_BREAKERS = frozenset({
    "so", "because", "while", "but", "or", "if", "when", "where",
    "whether", "instead", "rather",
})

LOCATIVE_PREPS = frozenset({
    "in", "on", "inside", "within", "into", "onto", "under", "for",
    "to", "across", "at",
})

VERBISH = GENERIC_VERBS | set(SYNONYMS) | {v for vs in SYNONYMS.values() for v in vs}
CHUNK_DELIMS = STOPWORDS | VERBISH


def _question_structure(topic: str, repo_words: set[str]) -> dict:
    """Extract structural hints from the question: locative chunks (noun runs
    introduced by a preposition like 'in the X'), first clause only."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", topic.lower()) if len(t) >= 2 and t not in repo_words]
    for i, tok in enumerate(tokens):
        if tok in CLAUSE_BREAKERS:
            tokens = tokens[:i]
            break

    chunks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    cur_start = 0
    for i, tok in enumerate(tokens):
        if tok in CHUNK_DELIMS:
            if cur:
                chunks.append((cur_start, cur))
                cur = []
        else:
            if not cur:
                cur_start = i
            cur.append(tok)
    if cur:
        chunks.append((cur_start, cur))

    locative_chunks: list[set[str]] = []
    for start_idx, chunk_tokens in chunks:
        j = start_idx - 1
        if j >= 0 and tokens[j] in ("the", "a", "an", "this", "that", "our", "its", "their"):
            j -= 1
        if j >= 0 and tokens[j] in LOCATIVE_PREPS:
            locative_chunks.append({_stem(tok) for tok in chunk_tokens})

    return {"locative_chunks": locative_chunks}

IRREGULAR_VERBS = {
    "came": "come", "went": "go", "gone": "go", "ran": "run",
    "wrote": "write", "written": "write", "broke": "break", "broken": "break",
    "hid": "hide", "hidden": "hide", "chose": "choose", "chosen": "choose",
    "sent": "send", "spoke": "speak", "spoken": "speak", "gave": "give",
    "given": "give", "took": "take", "taken": "take", "made": "make",
    "built": "build", "bought": "buy", "brought": "bring", "caught": "catch",
    "found": "find", "held": "hold", "kept": "keep", "lost": "lose",
    "met": "meet", "paid": "pay", "saw": "see", "seen": "see",
    "sold": "sell", "told": "tell", "won": "win", "left": "leave",
    "felt": "feel", "began": "begin", "begun": "begin", "split": "split",
}

_stem_cache: dict[str, str] = {}
_stem_lock = threading.Lock()
_tls = threading.local()
_sync_lock = threading.Lock()
_last_sync_ts = 0.0
_SYNC_TTL = 5.0  # seconds between freshness checks

# A cold-start (or any catch-up this large) re-parses too many transcripts to
# do inline on a request thread -- see _start_background_sync().
_BG_SYNC_THRESHOLD = int(os.environ.get("CCC_SHIP_GRAPH_SYNC_INLINE_MAX", "50"))
_bg_sync_state_lock = threading.Lock()
_bg_sync_running = False


def _ccc_dir() -> Path:
    cc_name = "command-center"
    return Path.home() / ".claude" / cc_name


def _get_db_path() -> Path:
    env = os.environ.get("CCC_SHIP_GRAPH_DB")
    if env:
        return Path(env)
    return _ccc_dir() / "ship_graph.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with WAL journaling so a long writer (the cold-start
    catch-up sync) never blocks a concurrent reader (MEMO-FIX-14: the default
    rollback-journal mode serializes readers behind writers for the whole
    write transaction, which is exactly the tens-of-seconds-long first recall
    seen after a real restart)."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _get_wt_db_path() -> Path:
    env = os.environ.get("WATCHTOWER_DB")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "watchtower" / "queues.db"


def _get_projects_dir() -> Path:
    env = os.environ.get("CCC_PROJECTS_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "projects"


def _get_codex_dir() -> Path:
    env = os.environ.get("CCC_CODEX_SESSIONS_ROOT")
    if env:
        return Path(env)
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "sessions"
    return Path.home() / ".codex" / "sessions"


def _get_days() -> float:
    try:
        # MEMORY-19: was "45" -- see session_fts._candidate_files()'s longer
        # note; same 68-day-old real transcript was also invisible to this
        # module's own session_meta (repo/date) sync at the old default.
        return float(os.environ.get("CCC_SHIP_GRAPH_DAYS", os.environ.get("BENCH_DAYS", "365")))
    except ValueError:
        return 365.0


def _stem(w: str) -> str:
    """Normalize English word suffixes using Porter stemmer and irregular verbs."""
    w = (w or "").lower()
    w = IRREGULAR_VERBS.get(w, w)
    if len(w) <= 2:
        return w
    cached = _stem_cache.get(w)
    if cached is not None:
        return cached

    w_clean = re.sub(r"[^a-z0-9]", "", w)
    if len(w_clean) <= 2:
        return w_clean

    with _stem_lock:
        cached = _stem_cache.get(w)
        if cached is not None:
            return cached
        if not hasattr(_tls, "_stem_conn") or _tls._stem_conn is None:
            _tls._stem_conn = sqlite3.connect(":memory:")
            _tls._stem_conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _st USING fts5(x, tokenize='porter unicode61')")
            _tls._stem_conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _sv USING fts5vocab(_st, col)")
        _tls._stem_conn.execute("INSERT INTO _st VALUES (?)", (w_clean,))
        row = _tls._stem_conn.execute("SELECT term FROM _sv").fetchone()
        res = row[0] if row else w_clean
        _tls._stem_conn.execute("DELETE FROM _st")
        _stem_cache[w] = res
        return res


def extract_terms(q: str) -> list[str]:
    """Extract informative lowercase terms, splitting punctuation and filtering stopwords."""
    out = []
    seen = set()
    words = re.findall(r"[A-Za-z0-9]+", q)
    for w in words:
        w_lower = w.lower()
        if len(w_lower) < 2 or w_lower in STOPWORDS or w_lower in seen:
            continue
        seen.add(w_lower)
        out.append(w_lower)
    return out


def fts_query(terms: list[str]) -> str:
    """Format sanitized terms as an OR FTS5 MATCH string."""
    parts = []
    for t in terms:
        t_clean = t.lower()
        if t_clean not in STOPWORDS and len(t_clean) > 1:
            parts.append(f'"{t_clean}"')
            for syn in SYNONYMS.get(t_clean, []):
                parts.append(f'"{syn}"')
    for i in range(len(terms) - 1):
        combined = terms[i].lower() + terms[i+1].lower()
        if len(combined) <= 24 and combined not in STOPWORDS:
            parts.append(f'"{combined}"')
    return " OR ".join(dict.fromkeys(parts))


def _repo_of(cwd: str) -> str:
    if not cwd:
        return ""
    p = cwd.rstrip("/")
    m = re.search(r"/([^/]+?)(?:-wt-[^/]+)?/\.(?:worktrees|claude/worktrees)/[^/]+", p)
    if m:
        return m.group(1)
    name = Path(p).name
    name = re.sub(r"-wt-.*$", "", name)
    return name


def discover_repo_roots() -> dict[str, str]:
    """Discover all known repo paths mapped by repo name."""
    roots: dict[str, str] = {}

    def add_root(p_raw: str | Path) -> None:
        try:
            p = Path(p_raw).expanduser().resolve()
        except Exception:
            return
        if any(part.startswith(".") for part in p.parts[1:]):
            return
        if not (p / ".git").exists():
            return
        if re.search(r"-wt-", p.name):
            return
        if p.name not in roots:
            roots[p.name] = str(p)

    # 0. Environment override
    env_repos = os.environ.get("CCC_SHIP_GRAPH_REPOS", os.environ.get("CCC_KNOWN_REPOS"))
    if env_repos:
        for r_path in env_repos.split(os.pathsep):
            if r_path.strip():
                add_root(r_path.strip())
        return roots

    # 1. Config files from command-center
    for fn in ["custom-repos.txt", "recent-repos.txt"]:
        p = _ccc_dir() / fn
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        add_root(line)
            except Exception:
                pass

    # 2. Registry
    reg = _ccc_dir() / "registry.json"
    if reg.exists():
        try:
            for item in json.loads(reg.read_text(encoding="utf-8")):
                if isinstance(item, dict) and "install_path" in item:
                    add_root(item["install_path"])
        except Exception:
            pass

    # 3. Conventional directories under HOME, Apps, dev
    for parent in [Path.home() / "Apps", Path.home(), Path.home() / "dev", Path.home() / "dev" / "tools"]:
        if parent.exists():
            try:
                for child in parent.iterdir():
                    if child.is_dir() and (child / ".git").exists():
                        add_root(child)
            except Exception:
                pass

    return roots


KNOWN_REPO_ALIASES: dict[str, list[str]] = {
    "claude-command-center": [
        r"\bccc\b",
        r"\bclaude[- ]command[- ]center\b",
        r"\bcommand[- ]center\b",
        r"\bcommand center\b",
        r"\bclaude command center\b",
        r"\bccc-\d+\b",
    ],
    "BYM": [
        r"\bbym\b",
        r"\bbook[- ]?your[- ]?mat\b",
        r"\bbook your mat\b",
        r"\bbecky\b",
        r"\bbecky[- ]pro\b",
        r"\bbym-\d+\b",
        r"\bbecky-\d+\b",
        r"\bbymops-\d+\b",
    ],
    "watchtower": [
        r"\bwatchtower\b",
        r"\bwatch[- ]?tower\b",
        r"\bwt\b",
        r"\bwt-\d+\b",
        r"\bwatchtower-\d+\b",
    ],
    "chuck-realtor-web": [
        r"\bchuck\b",
        r"\bchuck[- ]realtor\b",
        r"\bchuck[- ]realtor[- ]web\b",
        r"\bchuckrealtor\b",
    ],
}


def detect_named_repo(query: str, known_roots: dict[str, str] | None = None) -> tuple[str | None, set[str]]:
    """Detect if a question names a specific repository or alias."""
    q_lower = query.lower()
    for repo, patterns in KNOWN_REPO_ALIASES.items():
        for pat in patterns:
            m = re.search(pat, q_lower)
            if m:
                words = set(re.findall(r"[a-z0-9]+", m.group(0)))
                return repo, words

    if known_roots:
        for r_name in known_roots:
            if r_name in KNOWN_REPO_ALIASES:
                continue
            if len(r_name) <= 2 or r_name.startswith((".", "_")):
                continue
            clean = re.sub(r"[-_]", "[-_ ]", r_name.lower())
            pat = r"\b" + clean + r"\b"
            m = re.search(pat, q_lower)
            if m:
                words = set(re.findall(r"[a-z0-9]+", m.group(0)))
                return r_name, words
            m_repo = re.search(r"\b" + re.escape(r_name.lower()) + r"\s+repo\b", q_lower)
            if m_repo:
                words = set(re.findall(r"[a-z0-9]+", m_repo.group(0)))
                return r_name, words

    return None, set()


def _get_repo_head_fast(path_str: str) -> str:
    """Quickly read repo's HEAD commit SHA without spawning subprocess if possible."""
    git_entry = Path(path_str) / ".git"
    if git_entry.is_file():
        try:
            txt = git_entry.read_text(encoding="utf-8").strip()
            if txt.startswith("gitdir:"):
                p = Path(txt.split(":", 1)[1].strip())
                if not p.is_absolute():
                    p = (git_entry.parent / p).resolve()
            else:
                p = git_entry
        except Exception:
            p = git_entry
    else:
        p = git_entry

    head_file = p / "HEAD"
    if not head_file.exists():
        return ""
    try:
        content = head_file.read_text(encoding="utf-8").strip()
        if not content.startswith("ref:"):
            if re.match(r"^[0-9a-f]{40}$", content):
                return content
        else:
            ref_path = content.split(":", 1)[1].strip()
            target = p / ref_path
            if target.exists():
                return target.read_text(encoding="utf-8").strip()
            # Check commondir if worktree
            commondir_file = p / "commondir"
            common_p = p
            if commondir_file.exists():
                cd_txt = commondir_file.read_text(encoding="utf-8").strip()
                common_p = (p / cd_txt).resolve()
                target_c = common_p / ref_path
                if target_c.exists():
                    return target_c.read_text(encoding="utf-8").strip()
            # Check packed-refs in p or common_p
            for base in (p, common_p):
                packed = base / "packed-refs"
                if packed.exists():
                    for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if line.startswith("#") or line.startswith("^"):
                            continue
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == ref_path:
                            return parts[0]
    except Exception:
        pass
    return ""


def _resolve_git_dir(path_str: str) -> Path:
    """Resolve a checkout's `.git` entry to the real git dir, following
    `gitdir:` indirection (linked worktrees) -- shared by the HEAD and
    origin-ref fast-readers below."""
    git_entry = Path(path_str) / ".git"
    if git_entry.is_file():
        try:
            txt = git_entry.read_text(encoding="utf-8").strip()
            if txt.startswith("gitdir:"):
                p = Path(txt.split(":", 1)[1].strip())
                return p if p.is_absolute() else (git_entry.parent / p).resolve()
        except Exception:
            pass
    return git_entry


def _read_ref_sha_fast(git_dir: Path, ref_path: str) -> str:
    """Read one ref's SHA (loose, worktree commondir, or packed-refs) --
    no subprocess. `ref_path` is relative to `git_dir`, e.g.
    'refs/remotes/origin/main'."""
    target = git_dir / ref_path
    if target.exists():
        try:
            return target.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    commondir_file = git_dir / "commondir"
    common_p = git_dir
    if commondir_file.exists():
        try:
            cd_txt = commondir_file.read_text(encoding="utf-8").strip()
            common_p = (git_dir / cd_txt).resolve()
            target_c = common_p / ref_path
            if target_c.exists():
                return target_c.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    for base in (git_dir, common_p):
        packed = base / "packed-refs"
        if packed.exists():
            try:
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == ref_path:
                        return parts[0]
            except Exception:
                pass
    return ""


def _default_branch_name_fast(path_str: str) -> str:
    """Best-guess default branch, no subprocess: whichever of main/master has
    a local origin-tracking ref, else 'main'."""
    git_dir = _resolve_git_dir(path_str)
    for name in ("main", "master"):
        if _read_ref_sha_fast(git_dir, f"refs/remotes/origin/{name}"):
            return name
    return "main"


def _get_origin_head_fast(path_str: str, default_branch: str) -> str:
    """Local-only read of `refs/remotes/origin/<default_branch>` -- the SHA
    as of the last fetch, not a live network check. No subprocess."""
    if not default_branch:
        return ""
    return _read_ref_sha_fast(_resolve_git_dir(path_str), f"refs/remotes/origin/{default_branch}")


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            val TEXT
        );

        CREATE TABLE IF NOT EXISTS repos (
            path TEXT PRIMARY KEY,
            name TEXT,
            head_sha TEXT,
            indexed_at REAL,
            origin_ref TEXT DEFAULT '',
            origin_head_sha TEXT DEFAULT '',
            fetched_at REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS commits (
            commit_id TEXT PRIMARY KEY,
            repo TEXT,
            hash TEXT,
            short_hash TEXT,
            ts REAL,
            subject TEXT,
            body TEXT,
            files TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_commits_hash ON commits(hash);
        CREATE INDEX IF NOT EXISTS idx_commits_short ON commits(short_hash);
        CREATE INDEX IF NOT EXISTS idx_commits_repo ON commits(repo);

        CREATE VIRTUAL TABLE IF NOT EXISTS commits_fts USING fts5(
            commit_id UNINDEXED,
            repo UNINDEXED,
            hash UNINDEXED,
            short_hash UNINDEXED,
            subject,
            body,
            files,
            tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS tickets (
            ref TEXT PRIMARY KEY,
            project TEXT,
            number INTEGER,
            status TEXT,
            title TEXT,
            text TEXT,
            commit_sha TEXT,
            repo_path TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_commit ON tickets(commit_sha);
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

        CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(
            ref UNINDEXED,
            project UNINDEXED,
            status UNINDEXED,
            commit_sha UNINDEXED,
            title,
            text,
            tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS edges (
            src TEXT,
            dst TEXT,
            kind TEXT,
            w REAL
        );
        CREATE INDEX IF NOT EXISTS idx_edge_src ON edges(src);
        CREATE INDEX IF NOT EXISTS idx_edge_dst ON edges(dst);

        CREATE TABLE IF NOT EXISTS transcripts (
            path TEXT PRIMARY KEY,
            sid TEXT,
            mtime REAL,
            size INTEGER,
            indexed INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_transcripts_sid ON transcripts(sid);

        CREATE VIRTUAL TABLE IF NOT EXISTS commits_vocab USING fts5vocab(commits_fts, 'row');
        CREATE VIRTUAL TABLE IF NOT EXISTS tickets_vocab USING fts5vocab(tickets_fts, 'row');

        CREATE TABLE IF NOT EXISTS session_meta (
            sid TEXT PRIMARY KEY,
            repo TEXT,
            cwd TEXT,
            start_ts REAL,
            end_ts REAL,
            tickets TEXT,
            commits TEXT,
            files TEXT,
            continuation_origin TEXT
        );

        -- MEMO-FIX-21: every file a session wrote or read, as a normalized
        -- absolute path (repo or not), so `ccc history <path>` is an exact
        -- indexed lookup instead of a LIKE over session_meta.files JSON.
        CREATE TABLE IF NOT EXISTS session_files (
            path TEXT,
            sid TEXT,
            op TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_session_files_path ON session_files(path);
        CREATE INDEX IF NOT EXISTS idx_session_files_sid ON session_files(sid);
    """)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
    if "on_main" not in cols:
        conn.execute("ALTER TABLE commits ADD COLUMN on_main INTEGER DEFAULT 0")

    repo_cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if "origin_ref" not in repo_cols:
        conn.execute("ALTER TABLE repos ADD COLUMN origin_ref TEXT DEFAULT ''")
    if "origin_head_sha" not in repo_cols:
        conn.execute("ALTER TABLE repos ADD COLUMN origin_head_sha TEXT DEFAULT ''")
    if "fetched_at" not in repo_cols:
        conn.execute("ALTER TABLE repos ADD COLUMN fetched_at REAL DEFAULT 0")

    sm_cols = {r[1] for r in conn.execute("PRAGMA table_info(session_meta)")}
    if "continuation_origin" not in sm_cols:
        conn.execute("ALTER TABLE session_meta ADD COLUMN continuation_origin TEXT DEFAULT ''")
    # Column is guaranteed to exist above this line (fresh DB: created in the
    # executescript above; existing DB: just ALTERed in) -- only now is it
    # safe to index it. Creating this index inside the executescript above
    # would break on a pre-existing session_meta table that predates the
    # column, since CREATE INDEX IF NOT EXISTS still requires the column to
    # exist to parse.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_meta_continuation_origin "
        "ON session_meta(continuation_origin)"
    )

    row_v = conn.execute("SELECT val FROM meta WHERE key = 'schema_v'").fetchone()
    # v3: the commit scan now walks origin/<default> too; force one rescan so
    # already-gated repos pick up commits their behind clones never indexed.
    if not row_v or row_v[0] != "3":
        conn.execute("UPDATE repos SET head_sha = ''")
        conn.execute("INSERT OR REPLACE INTO meta (key, val) VALUES ('schema_v', '3')")
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    if not hasattr(_tls, "conn") or _tls.conn is None:
        db_path = _get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _tls.conn = _connect(db_path)
    return _tls.conn


def _sync_git_repos(conn: sqlite3.Connection, roots: dict[str, str], days: float) -> None:
    cur = conn.execute("SELECT path, head_sha, origin_head_sha FROM repos")
    have = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    for repo_name, repo_path in roots.items():
        current_head = _get_repo_head_fast(repo_path)
        if not current_head:
            continue
        default_branch = _default_branch_name_fast(repo_path)
        current_origin_head = _get_origin_head_fast(repo_path, default_branch)
        if have.get(repo_path) == (current_head, current_origin_head):
            continue

        # HEAD changed or new repo: batch git log once
        main_hashes: set[str] = set()
        main_ref = None
        for ref in ("origin/main", "main", "origin/master", "master"):
            try:
                vr = subprocess.run(
                    ["git", "-C", repo_path, "rev-parse", "--verify", "--quiet", ref],
                    capture_output=True, text=True, timeout=10,
                )
                if vr.returncode == 0 and vr.stdout.strip():
                    main_ref = ref
                    break
            except Exception:
                continue
        if main_ref:
            rl_args = ["git", "-C", repo_path, "rev-list", main_ref]
            if days > 0:
                rl_args.append(f"--since={int(days)}.days")
            try:
                rl = subprocess.run(rl_args, capture_output=True, text=True, timeout=30)
                if rl.returncode == 0:
                    main_hashes = {x.strip() for x in rl.stdout.splitlines() if x.strip()}
            except Exception:
                pass

        # Walk origin/<b> too, not just the local branch: a clone that is
        # behind origin (never pulled) would otherwise never index commits
        # that already shipped, and `ccc shipped` says NOT FOUND for them.
        # One for-each-ref call also sees packed refs, unlike a loose-file stat.
        branches = ["HEAD"]
        try:
            fr = subprocess.run(
                ["git", "-C", repo_path, "for-each-ref", "--format=%(refname:short)",
                 "refs/heads/next", "refs/heads/main", "refs/heads/master",
                 "refs/remotes/origin/next", "refs/remotes/origin/main", "refs/remotes/origin/master"],
                capture_output=True, text=True, timeout=10,
            )
            if fr.returncode == 0:
                branches += [x.strip() for x in fr.stdout.splitlines() if x.strip()]
        except Exception:
            pass

        args = ["git", "-C", repo_path, "log"] + list(dict.fromkeys(branches))
        if days > 0:
            args.append(f"--since={int(days)}.days")
        args += ["--name-only", "--format=\x1e%H\x1f%h\x1f%ct\x1f%s\x1f%b\x1f"]

        try:
            res = subprocess.run(args, capture_output=True, text=True, timeout=30)
            stdout = res.stdout
        except Exception:
            continue

        records = stdout.split("\x1e")[1:]
        commits_to_insert = []
        fts_to_insert = []
        ticket_edges = []

        for rec in records:
            parts = rec.split("\x1f")
            if len(parts) < 5:
                continue
            h, sh, ct, subj, body = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()
            files_str = parts[5].strip() if len(parts) > 5 else ""
            files = [f.strip() for f in files_str.splitlines() if f.strip()][:100]
            cid = f"{repo_name}:{h}"
            ts = float(ct) if ct else 0.0
            files_blob = " ".join(files)

            commits_to_insert.append((cid, repo_name, h, sh, ts, subj, body, files_blob, 1 if h in main_hashes else 0))
            fts_to_insert.append((cid, repo_name, h, sh, subj, body, files_blob))

            # Extract tickets mentioned in commit
            mentioned_tickets = {t for t in TICKET_RE.findall(f"{subj}\n{body}") if not TICKET_STOP.match(t)}
            for t in mentioned_tickets:
                ticket_edges.append((f"ticket:{t}", f"commit:{h}", "mentioned", 1.0))
                ticket_edges.append((f"ticket:{t}", f"commit:{sh}", "mentioned", 1.0))

        with conn:
            # Delete old commits for this repo
            conn.execute("DELETE FROM commits WHERE repo = ?", (repo_name,))
            conn.execute("DELETE FROM commits_fts WHERE repo = ?", (repo_name,))
            conn.executemany(
                "INSERT OR REPLACE INTO commits (commit_id, repo, hash, short_hash, ts, subject, body, files, on_main) VALUES (?,?,?,?,?,?,?,?,?)",
                commits_to_insert,
            )
            conn.executemany(
                "INSERT INTO commits_fts VALUES (?,?,?,?,?,?,?)",
                fts_to_insert,
            )
            conn.executemany(
                "INSERT INTO edges VALUES (?,?,?,?)",
                ticket_edges,
            )
            conn.execute(
                """
                INSERT INTO repos (path, name, head_sha, indexed_at, origin_ref, origin_head_sha)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    head_sha=excluded.head_sha,
                    indexed_at=excluded.indexed_at,
                    origin_ref=excluded.origin_ref,
                    origin_head_sha=excluded.origin_head_sha
                """,
                (repo_path, repo_name, current_head, time.time(),
                 f"origin/{default_branch}", current_origin_head),
            )

    # Prune repos that disappeared from discovery (e.g. hidden-path clones)
    root_paths = set(roots.values())
    stale = [r for r in conn.execute("SELECT path, name FROM repos").fetchall() if r[0] not in root_paths]
    if stale:
        with conn:
            for p_row, n_row in stale:
                if n_row not in roots:
                    conn.execute("DELETE FROM commits WHERE repo = ?", (n_row,))
                    conn.execute("DELETE FROM commits_fts WHERE repo = ?", (n_row,))
                conn.execute("DELETE FROM repos WHERE path = ?", (p_row,))


def _clean_ticket_title(title: str, text: str) -> str:
    t = (title or "").strip()
    if not t or t.startswith("reporter=") or "Command Center for Claude" in t or "BookYourMat — Studio Scheduling" in t or t == "Flow — CCC":
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        for line in lines:
            if not line.startswith("WAVE ") and not line.startswith("DEPENDS:") and not line.startswith("ENGINE:") and not line.startswith("REVIEW:"):
                return line[:140]
        return ""
    return t


def _sync_watchtower(conn: sqlite3.Connection) -> None:
    wt_db = _get_wt_db_path()
    if not wt_db.exists():
        return
    try:
        st = wt_db.stat()
        current_mtime = str(st.st_mtime)
    except OSError:
        return

    cur = conn.execute("SELECT val FROM meta WHERE key = 'wt_db_mtime'")
    row = cur.fetchone()
    cur_v = conn.execute("SELECT val FROM meta WHERE key = 'wt_clean_v2'")
    row_v = cur_v.fetchone()
    if row and row[0] == current_mtime and row_v and row_v[0] == "1":
        return

    try:
        wt_conn = sqlite3.connect(f"file:{wt_db}?mode=ro", uri=True)
        items = wt_conn.execute("SELECT ref, project, number, status, updated_at, item_json FROM items").fetchall()
        wt_conn.close()
    except Exception:
        return

    tickets_rows = []
    fts_rows = []
    edges_rows = []

    for ref, project, number, status, updated_at, item_json in items:
        try:
            d = json.loads(item_json)
        except Exception:
            continue
        raw_title = d.get("title") or ""
        text = d.get("text") or d.get("note") or ""
        title = _clean_ticket_title(raw_title, text)
        repo_path = d.get("repo_path") or ""

        # Extract commit sha if closed
        commit_sha = ""
        res = d.get("resolution")
        if isinstance(res, dict):
            commit_sha = res.get("commit") or ""
        elif isinstance(res, str):
            m = re.search(r"\b([0-9a-f]{7,40})\b", res)
            if m:
                commit_sha = m.group(1)

        if not commit_sha:
            for ev in d.get("history") or []:
                if isinstance(ev, dict) and ev.get("event") == "close":
                    r2 = ev.get("resolution")
                    if isinstance(r2, dict) and r2.get("commit"):
                        commit_sha = r2["commit"]
                        break
                    elif isinstance(r2, str):
                        m = re.search(r"\b([0-9a-f]{7,40})\b", r2)
                        if m:
                            commit_sha = m.group(1)
                            break

        tickets_rows.append((ref, project, number, status, title, text, commit_sha, repo_path, updated_at))
        fts_rows.append((ref, project, status, commit_sha, title, text))
        if commit_sha:
            edges_rows.append((f"ticket:{ref}", f"commit:{commit_sha}", "resolves", 1.0))

    with conn:
        conn.execute("DELETE FROM tickets")
        conn.execute("DELETE FROM tickets_fts")
        conn.execute("DELETE FROM edges WHERE kind = 'resolves'")
        conn.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)", tickets_rows)
        conn.executemany("INSERT INTO tickets_fts VALUES (?,?,?,?,?,?)", fts_rows)
        conn.executemany("INSERT INTO edges VALUES (?,?,?,?)", edges_rows)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('wt_db_mtime', ?)", (current_mtime,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('wt_clean_v2', '1')")


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                if c.get("type") in ("text", "input_text", "output_text"):
                    out.append(c.get("text") or "")
    return "\n".join(out)


def _tool_blob(content: Any) -> str:
    out = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                inner = c.get("content")
                out.append(inner if isinstance(inner, str) else _text_of(inner))
    return "\n".join(out)


def _ts(s: Any) -> float | None:
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _parse_transcript(args: tuple[str, str]) -> dict | None:
    engine, path = args
    sid = Path(path).stem
    cwd = ""
    tools = []
    alltext = []
    files = set()
    reads = set()
    ts0 = ts1 = None
    continuation_origin = ""
    first_user_seen = False

    if engine == "claude":
        try:
            with open(path, "rb") as f:
                for raw in f:
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    t = d.get("type")
                    if t not in ("user", "assistant"):
                        continue
                    if d.get("isSidechain"):
                        continue
                    cwd = cwd or d.get("cwd") or ""
                    ts = _ts(d.get("timestamp"))
                    if ts:
                        ts0 = ts if ts0 is None else min(ts0, ts)
                        ts1 = ts if ts1 is None else max(ts1, ts)
                    msg = d.get("message") or {}
                    content = msg.get("content")
                    txt = _text_of(content)
                    if txt:
                        alltext.append(txt[:1000])
                    if t == "user":
                        if not first_user_seen and not d.get("isMeta"):
                            first_user_seen = True
                            m_origin = CONTINUATION_ORIGIN_RE.search(txt or "")
                            if m_origin:
                                continuation_origin = m_origin.group(1).strip()
                        tb = _tool_blob(content)
                        if tb:
                            tools.append(tb[:2000])
                    else:
                        if isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict) and c.get("type") == "tool_use":
                                    inp = c.get("input") or {}
                                    fp = inp.get("file_path") or inp.get("notebook_path")
                                    if fp and c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                                        files.add(fp)
                                    elif fp and c.get("name") == "Read":
                                        reads.add(fp)
                                    cmd = inp.get("command")
                                    if isinstance(cmd, str) and "git commit" in cmd:
                                        tools.append(cmd[:1000])
        except Exception:
            return None
    else:  # codex
        m = CODEX_SID_RE.search(path)
        if m:
            sid = m.group(1)
        try:
            with open(path, "rb") as f:
                for raw in f:
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    t = d.get("type")
                    p = d.get("payload") or {}
                    ts = _ts(d.get("timestamp"))
                    if ts:
                        ts0 = ts if ts0 is None else min(ts0, ts)
                        ts1 = ts if ts1 is None else max(ts1, ts)
                    if t == "session_meta":
                        sid = p.get("id") or p.get("session_id") or sid
                        cwd = p.get("cwd") or cwd
                        continue
                    if t == "turn_context" and not cwd:
                        cwd = p.get("cwd") or cwd
                    pt = p.get("type")
                    if t == "response_item" and pt == "message":
                        txt = _text_of(p.get("content"))
                        if txt:
                            alltext.append(txt[:1000])
                        if not first_user_seen and p.get("role") == "user":
                            first_user_seen = True
                            m_origin = CONTINUATION_ORIGIN_RE.search(txt or "")
                            if m_origin:
                                continuation_origin = m_origin.group(1).strip()
                    elif t == "response_item" and pt in ("function_call_output", "custom_tool_call_output"):
                        out = p.get("output")
                        tb = out if isinstance(out, str) else _text_of(out)
                        if tb and ("git" in tb or "] " in tb):
                            tools.append(tb[:2000])
                    elif t == "response_item" and pt in ("function_call", "custom_tool_call"):
                        arg = p.get("arguments") or p.get("input") or ""
                        if isinstance(arg, str) and "git commit" in arg:
                            tools.append(arg[:1000])
                        for fm in re.finditer(r"\*\*\* (?:Update|Add) File: ([^\n\\]+)", arg if isinstance(arg, str) else ""):
                            files.add(fm.group(1).strip())
        except Exception:
            return None

    if not sid:
        return None

    blob = "\n".join(tools)
    commits = {}
    for m_com in COMMIT_RE.finditer(blob):
        commits[m_com.group(2)] = {"branch": m_com.group(1), "subject": m_com.group(3).strip()}

    full_text = "\n".join(alltext)
    tickets = sorted({t for t in TICKET_RE.findall(full_text) if not TICKET_STOP.match(t)})

    return {
        "sid": sid,
        "repo": _repo_of(cwd),
        "cwd": cwd,
        "start_ts": ts0,
        "end_ts": ts1,
        "commits": commits,
        "tickets": tickets,
        "files": sorted(files)[:200],
        "file_ops": _file_ops(cwd, files, reads),
        "continuation_origin": continuation_origin,
    }


_MAX_FILE_OPS = 2000


def _abs_path(cwd: str, fp: str) -> str:
    """Normalized absolute form of a transcript file path. Codex apply_patch
    paths are often cwd-relative; Claude's tool paths are already absolute."""
    fp = os.path.expanduser(str(fp).strip())
    if not os.path.isabs(fp):
        if not cwd:
            return ""
        fp = os.path.join(cwd, fp)
    return os.path.normpath(fp)


def _file_ops(cwd: str, wrote: set, read: set) -> list[tuple[str, str]]:
    """(abs_path, op) pairs, op 'wrote' or 'read'; a path both written and
    read is recorded once as 'wrote'. Bounded per session."""
    out: dict[str, str] = {}
    for fp in wrote:
        ap = _abs_path(cwd, fp)
        if ap:
            out[ap] = "wrote"
    for fp in read:
        ap = _abs_path(cwd, fp)
        if ap and ap not in out:
            out[ap] = "read"
    return sorted(out.items())[:_MAX_FILE_OPS]


def _candidate_transcript_files(days: float) -> list[tuple[str, str, float, int]]:
    cutoff = (time.time() - days * 86400) if days > 0 else 0.0
    out = []
    p_dir = _get_projects_dir()
    if p_dir.exists():
        for p in p_dir.glob("*/*.jsonl"):
            try:
                st = p.stat()
                if st.st_size > 0 and st.st_mtime >= cutoff:
                    out.append(("claude", str(p), st.st_mtime, st.st_size))
            except OSError:
                continue

    c_dir = _get_codex_dir()
    if c_dir.exists():
        for p in c_dir.rglob("*.jsonl"):
            try:
                st = p.stat()
                if st.st_size > 0 and st.st_mtime >= cutoff:
                    out.append(("codex", str(p), st.st_mtime, st.st_size))
            except OSError:
                continue
    return out


def _sync_transcripts(conn: sqlite3.Connection, days: float) -> None:
    cur = conn.execute("SELECT path, mtime, size FROM transcripts")
    have = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    candidates = _candidate_transcript_files(days)
    todo = [(eng, p, mt, sz) for eng, p, mt, sz in candidates if have.get(p) != (mt, sz)]
    if not todo:
        return

    items_to_parse = [(eng, p) for eng, p, _, _ in todo]
    if len(items_to_parse) > 16:
        with ThreadPoolExecutor(max_workers=8) as ex:
            parsed = list(ex.map(_parse_transcript, items_to_parse, chunksize=16))
    else:
        parsed = [_parse_transcript(item) for item in items_to_parse]

    # Pre-index commits by short hash and by repo for fast window matching
    cur_cm = conn.execute("SELECT hash, short_hash, repo, ts, files FROM commits")
    by_short = defaultdict(list)
    by_repo = defaultdict(list)
    for h, sh, rep, ts, fl in cur_cm.fetchall():
        f_set = {Path(f).name for f in fl.split()} if fl else set()
        row_cm = {"hash": h, "short": sh, "repo": rep, "ts": ts, "files": f_set}
        by_short[sh[:7]].append(row_cm)
        by_repo[rep].append(row_cm)

    session_rows = []
    edge_rows = []
    trans_rows = []
    file_rows = []
    reparsed_sids = []

    for (eng, path, mt, sz), r in zip(todo, parsed):
        if not r or not r.get("sid"):
            trans_rows.append((path, "", mt, sz, 0))
            continue
        sid = r["sid"]
        session_rows.append((
            sid,
            r["repo"],
            r["cwd"],
            r["start_ts"],
            r["end_ts"],
            json.dumps(r["tickets"]),
            json.dumps(r["commits"]),
            json.dumps(r["files"]),
            r.get("continuation_origin") or "",
        ))
        trans_rows.append((path, sid, mt, sz, 1))
        reparsed_sids.append(sid)
        file_rows.extend((fp, sid, op) for fp, op in r.get("file_ops") or [])

        # Commit edges
        for h in r["commits"]:
            matches = by_short.get(h[:7], [])
            if matches:
                for cm in matches:
                    edge_rows.append((sid, f"commit:{cm['hash']}", "made", 1.0))
            else:
                edge_rows.append((sid, f"commit:{h}", "made", 1.0))

        # Time window edges
        files_names = {Path(f).name for f in r["files"]}
        if files_names and r["start_ts"] and r["end_ts"] and r["repo"]:
            for cm in by_repo.get(r["repo"], []):
                if (r["start_ts"] - 60 <= cm["ts"] <= r["end_ts"] + 600) and (files_names & cm["files"]):
                    edge_rows.append((sid, f"commit:{cm['hash']}", "window", 0.6))

        # Ticket edges
        for t in r["tickets"]:
            edge_rows.append((sid, f"ticket:{t}", "ref", 1.0))

    with conn:
        # A re-parsed session replaces its own rows. Session edges (made/
        # window/ref) are keyed src=sid; without this delete every re-parse
        # of a still-growing transcript appended duplicate edges.
        for sid in reparsed_sids:
            conn.execute("DELETE FROM edges WHERE src = ? AND kind IN ('made', 'window', 'ref')", (sid,))
            conn.execute("DELETE FROM session_files WHERE sid = ?", (sid,))
        conn.executemany("INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?)", trans_rows)
        conn.executemany("INSERT OR REPLACE INTO session_meta VALUES (?,?,?,?,?,?,?,?,?)", session_rows)
        conn.executemany("INSERT INTO edges VALUES (?,?,?,?)", edge_rows)
        conn.executemany("INSERT INTO session_files VALUES (?,?,?)", file_rows)


SESSION_FILES_SCHEMA = "1"


def _session_files_migration_pending(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT val FROM meta WHERE key = 'session_files_v'").fetchone()
    if row and row[0] == SESSION_FILES_SCHEMA:
        return False
    if not conn.execute("SELECT 1 FROM transcripts LIMIT 1").fetchone():
        # Fresh DB: every transcript will be parsed with file_ops anyway.
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, val) VALUES ('session_files_v', ?)", (SESSION_FILES_SCHEMA,))
        return False
    return True


def _migrate_session_files(conn: sqlite3.Connection) -> None:
    """Invalidate the transcripts (mtime, size) gate so the sync that follows
    re-parses every session and fills session_files."""
    with conn:
        conn.execute("UPDATE transcripts SET mtime = -1")
        conn.execute("INSERT OR REPLACE INTO meta (key, val) VALUES ('session_files_v', ?)", (SESSION_FILES_SCHEMA,))


CONTINUATION_ORIGIN_SCHEMA = "1"


def _continuation_origin_migration_pending(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT val FROM meta WHERE key = 'continuation_origin_v'").fetchone()
    if row and row[0] == CONTINUATION_ORIGIN_SCHEMA:
        return False
    if not conn.execute("SELECT 1 FROM transcripts LIMIT 1").fetchone():
        # Fresh DB: every transcript will be parsed with continuation_origin anyway.
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, val) VALUES ('continuation_origin_v', ?)",
                         (CONTINUATION_ORIGIN_SCHEMA,))
        return False
    return True


def _migrate_continuation_origin(conn: sqlite3.Connection) -> None:
    """Invalidate the transcripts (mtime, size) gate so the sync that follows
    re-parses every session and fills session_meta.continuation_origin."""
    with conn:
        conn.execute("UPDATE transcripts SET mtime = -1")
        conn.execute("INSERT OR REPLACE INTO meta (key, val) VALUES ('continuation_origin_v', ?)",
                     (CONTINUATION_ORIGIN_SCHEMA,))


def continuation_origin_of(conn: sqlite3.Connection, sid: str) -> str:
    """The session id `sid`'s transcript named as "Origin session id: X" in
    its first user turn -- i.e. the ancestor it auto-resumed/continued from.
    Empty string if `sid` is unknown or wasn't a continuation."""
    row = conn.execute("SELECT continuation_origin FROM session_meta WHERE sid = ?", (sid,)).fetchone()
    return (row[0] if row else "") or ""


def continuation_children_of(conn: sqlite3.Connection, sid: str) -> list[str]:
    """Sessions that named `sid` as their continuation origin, newest first."""
    rows = conn.execute(
        "SELECT sid FROM session_meta WHERE continuation_origin = ? ORDER BY start_ts DESC",
        (sid,),
    ).fetchall()
    return [r[0] for r in rows]


def _count_pending_transcripts(conn: sqlite3.Connection, days: float) -> int:
    """Cheap (stat-only, no parsing) count of transcripts _sync_transcripts
    would need to (re)parse -- used to decide inline vs. background sync."""
    cur = conn.execute("SELECT path, mtime, size FROM transcripts")
    have = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    candidates = _candidate_transcript_files(days)
    return sum(1 for _eng, p, mt, sz in candidates if have.get(p) != (mt, sz))


def _sync_all(conn: sqlite3.Connection, force: bool = False) -> None:
    global _last_sync_ts
    now = time.time()
    if not force and (now - _last_sync_ts < _SYNC_TTL):
        return
    if not force and os.environ.get("CCC_CONV_READER_CHILD") == "1":
        # MEMORY-20: the conv-reader child only answers recall queries; the
        # dashboard owns the sync.
        _last_sync_ts = now
        return

    # Non-blocking acquire for regular (non-forced) callers -- see the
    # matching comment in session_fts._sync_index. force=True (explicit
    # force_refresh, and the background worker's own call) still blocks.
    got = _sync_lock.acquire(blocking=force)
    if not got:
        return
    try:
        if not force and (time.time() - _last_sync_ts < _SYNC_TTL):
            return

        _init_db(conn)
        days = _get_days()

        # MEMO-FIX-21: one-time re-parse so session_files covers transcripts
        # indexed before it existed. Background-only, like any cold catch-up.
        # MEMO-FIX-lineage: same shape for continuation_origin -- both flags
        # share the one re-parse pass rather than triggering two.
        files_pending = _session_files_migration_pending(conn)
        origin_pending = _continuation_origin_migration_pending(conn)
        if files_pending or origin_pending:
            if not force:
                _start_background_sync()
                return
            if files_pending:
                _migrate_session_files(conn)
            if origin_pending:
                _migrate_continuation_origin(conn)

        if not force:
            # Counting changed transcripts still walks the entire corpus.
            # Defer every large-corpus refresh, including warm processes
            # whose TTL expired, so recall never waits for that scan.
            existing = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
            if existing > _BG_SYNC_THRESHOLD:
                _start_background_sync()
                return

            pending = _count_pending_transcripts(conn, days)
            if pending > _BG_SYNC_THRESHOLD:
                # Cold start (or a big catch-up): re-parsing this many
                # transcripts (plus session_fts's own re-parse of the same
                # files) can take a minute or more. Don't block the request;
                # warm in the background and answer with what's indexed so far.
                _last_sync_ts = time.time()
                _start_background_sync()
                return

        roots = discover_repo_roots()
        _sync_git_repos(conn, roots, days)
        _sync_watchtower(conn)
        _sync_transcripts(conn, days)
        _last_sync_ts = time.time()
    finally:
        _sync_lock.release()


_helper_indexing = {"value": False, "ts": 0.0}


def is_indexing() -> bool:
    """True while a background cold-start/catch-up sync is in flight."""
    if _bg_sync_running:
        return True
    # CCC-142: a sync delegated to the background helper reports back on each
    # kick; trust that answer for a short while.
    return bool(_helper_indexing["value"]) and time.time() - _helper_indexing["ts"] < 30


def _delegate_background_sync() -> bool:
    """CCC-142: run the full sync in the background-helper process, off the
    dashboard's GIL (the index is shared on-disk SQLite). True when handled:
    delegated, or deferred while the helper is still booting (it syncs on
    start). False runs it in-process as before."""
    try:
        from ccc_server import bg_helper
    except Exception:
        return False
    if bg_helper.is_child():
        return False
    if bg_helper.ready():
        reply = bg_helper.call("ship_sync", timeout=5)
        if reply is not None:
            _helper_indexing.update(value=bool(reply.get("indexing")), ts=time.time())
            return True
        return False
    return bg_helper.starting()


def _start_background_sync() -> None:
    """Kick a full (blocking, force=True) sync on a background thread against
    its own connection. Idempotent while already running."""
    global _bg_sync_running
    if _delegate_background_sync():
        return
    with _bg_sync_state_lock:
        if _bg_sync_running:
            return
        _bg_sync_running = True

    def _worker() -> None:
        global _bg_sync_running
        try:
            conn2 = _connect(_get_db_path())
            try:
                _sync_all(conn2, force=True)
            finally:
                conn2.close()
        except Exception:
            pass
        finally:
            with _bg_sync_state_lock:
                _bg_sync_running = False

    threading.Thread(target=_worker, daemon=True, name="ship-graph-warm").start()


_ORIGIN_FRESHNESS_LOCK = threading.Lock()
_ORIGIN_FRESHNESS_STARTED = False
_ORIGIN_FRESHNESS_TICK_INTERVAL_S = 60
_ORIGIN_FRESHNESS_ACTIVE_WINDOW_S = 15 * 60
_ORIGIN_FRESHNESS_IDLE_WINDOW_S = 24 * 60 * 60
_ORIGIN_FRESHNESS_ACTIVITY_DAYS = 30
_ORIGIN_FRESHNESS_MAX_WORKERS = 4


def _repo_is_active(conn: sqlite3.Connection, repo_name: str, now: float) -> bool:
    """True if repo had a commit in the last 30 days, per the already-
    indexed commits table -- no git subprocess."""
    cutoff = now - _ORIGIN_FRESHNESS_ACTIVITY_DAYS * 86400
    row = conn.execute(
        "SELECT 1 FROM commits WHERE repo = ? AND ts >= ? LIMIT 1", (repo_name, cutoff)
    ).fetchone()
    return row is not None


def _repo_due_for_freshness_check(conn: sqlite3.Connection, repo_name: str, repo_path: str, now: float) -> bool:
    row = conn.execute("SELECT fetched_at FROM repos WHERE path = ?", (repo_path,)).fetchone()
    fetched_at = row[0] if row and row[0] else 0.0
    window = _ORIGIN_FRESHNESS_ACTIVE_WINDOW_S if _repo_is_active(conn, repo_name, now) else _ORIGIN_FRESHNESS_IDLE_WINDOW_S
    return (now - fetched_at) >= window


def _check_and_fetch_origin(repo_path: str) -> tuple[str, str, float] | None:
    """`ls-remote` first so an unchanged remote costs one small round trip
    and no fetch; only `git fetch` when origin actually moved. Runs on a
    worker thread -- no sqlite access here, the caller persists the result.
    Returns (origin_ref, origin_head_sha, fetched_at), or None on failure."""
    default_branch = _default_branch_name_fast(repo_path)
    if not default_branch:
        return None
    try:
        lr = subprocess.run(
            ["git", "-C", repo_path, "ls-remote", "origin", f"refs/heads/{default_branch}"],
            capture_output=True, text=True, timeout=10,
        )
        remote_sha = lr.stdout.split()[0] if lr.returncode == 0 and lr.stdout.strip() else ""
    except Exception:
        remote_sha = ""

    now = time.time()
    local_origin_sha = _get_origin_head_fast(repo_path, default_branch)
    if remote_sha and remote_sha == local_origin_sha:
        return (f"origin/{default_branch}", local_origin_sha, now)

    try:
        subprocess.run(
            ["git", "-C", repo_path, "fetch", "--quiet", "--no-tags",
             "--no-write-fetch-head", "origin", default_branch],
            capture_output=True, timeout=30,
        )
    except Exception:
        return None

    new_origin_sha = _get_origin_head_fast(repo_path, default_branch)
    return (f"origin/{default_branch}", new_origin_sha, now)


def _origin_freshness_tick(conn: sqlite3.Connection) -> int:
    """One pass over discovered repos: check+fetch every repo that's due
    (bounded concurrency), persist results. Returns how many repos were
    checked, for tests."""
    roots = discover_repo_roots()
    now = time.time()
    due = [(name, path) for name, path in roots.items()
           if _repo_due_for_freshness_check(conn, name, path, now)]
    if not due:
        return 0
    with ThreadPoolExecutor(max_workers=_ORIGIN_FRESHNESS_MAX_WORKERS) as pool:
        results = list(pool.map(lambda nr: _check_and_fetch_origin(nr[1]), due))
    with conn:
        for (_name, path), result in zip(due, results):
            if result is None:
                continue
            origin_ref, origin_head_sha, fetched_at = result
            conn.execute(
                "UPDATE repos SET origin_ref = ?, origin_head_sha = ?, fetched_at = ? WHERE path = ?",
                (origin_ref, origin_head_sha, fetched_at, path),
            )
    return len(due)


def _run_origin_freshness_loop() -> None:
    """Background daemon modeled on _start_usage_limit_watcher (usage_limit.py):
    keeps each active repo's knowledge of origin/<default> fresh so the
    request path (is_shipped) never has to spawn ls-remote/fetch itself.
    Started once from warm_start(); idempotent while already running."""
    global _ORIGIN_FRESHNESS_STARTED
    with _ORIGIN_FRESHNESS_LOCK:
        if _ORIGIN_FRESHNESS_STARTED:
            return
        _ORIGIN_FRESHNESS_STARTED = True

    def _worker() -> None:
        try:
            conn = _connect(_get_db_path())
            try:
                _init_db(conn)
                while True:
                    try:
                        _origin_freshness_tick(conn)
                    except Exception:
                        pass
                    time.sleep(_ORIGIN_FRESHNESS_TICK_INTERVAL_S)
            finally:
                conn.close()
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="ship-graph-origin-freshness").start()


def warm_start() -> None:
    """Call once at process/server start to begin warming the graph in the
    background before the first real request arrives."""
    _start_background_sync()
    _run_origin_freshness_loop()


def graph_health() -> dict:
    """Cheap read-only snapshot of ship-graph freshness for `ccc doctor`
    (MEMO-FIX-24). COUNT(*) queries only -- never triggers a sync."""
    conn = _get_connection()
    _init_db(conn)
    transcripts_rows = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    commits_rows = conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    return {
        "transcripts_rows": transcripts_rows,
        "commits_rows": commits_rows,
        "last_sync_ts": _last_sync_ts or None,
        "indexing": is_indexing(),
    }


def rrf(lists: list[list[str]], k: int = 60, weights: list[float] | None = None) -> list[str]:
    """Reciprocal Rank Fusion."""
    sc: dict[str, float] = defaultdict(float)
    for i, lst in enumerate(lists):
        w = weights[i] if weights and i < len(weights) else 1.0
        for r, sid in enumerate(lst, 1):
            sc[sid] += w / (k + r)
    return [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1])]


def search_sessions(query: str, limit: int = 20, force_refresh: bool = False) -> list[dict]:
    """Search sessions re-ranking session_fts results with commit/ticket graph.

    Must NOT lower retrieval vs session_fts alone.
    Returns: [{'session_id': ...}, ...]
    """
    q = (query or "").strip()
    if not q:
        return []

    conn = _get_connection()
    _sync_all(conn, force=force_refresh)

    # 1. Base results from session_fts
    try:
        base_search = _get_base_search_sessions()
        base_hits = base_search(q, limit=max(limit * 2, 50))
        base_sids = [r["session_id"] for r in base_hits if isinstance(r, dict) and "session_id" in r]
    except Exception:
        base_sids = []

    terms = extract_terms(q)
    if not terms:
        return [{"session_id": sid} for sid in base_sids[:limit]]

    match_str = fts_query(terms)
    commit_sids: list[str] = []
    if match_str:
        try:
            cur = conn.execute(
                """SELECT hash, short_hash FROM commits_fts
                   WHERE commits_fts MATCH ?
                   ORDER BY bm25(commits_fts, 0, 0, 0, 0, 5.0, 1.5, 1.0)
                   LIMIT 25""",
                (match_str,),
            )
            commit_hits = cur.fetchall()
            sc_commit: dict[str, float] = defaultdict(float)
            for rank, (h, sh) in enumerate(commit_hits, 1):
                cur_edges = conn.execute(
                    "SELECT src, w FROM edges WHERE dst IN (?, ?, ?, ?) AND kind IN ('made', 'window')",
                    (f"commit:{h}", f"commit:{sh}", h, sh),
                )
                for sid, w in cur_edges.fetchall():
                    sc_commit[sid] += w / (10 + rank)
            commit_sids = [s for s, _ in sorted(sc_commit.items(), key=lambda kv: -kv[1])]
        except sqlite3.OperationalError:
            pass

    # Ticket expansion from base seed sessions
    ticket_sids: list[str] = []
    if base_sids:
        sc_ticket: dict[str, float] = defaultdict(float)
        for rank, sid in enumerate(base_sids[:5], 1):
            cur_t = conn.execute("SELECT dst FROM edges WHERE src = ? AND dst LIKE 'ticket:%'", (sid,))
            for (dst,) in cur_t.fetchall():
                cur_sibs = conn.execute("SELECT src FROM edges WHERE dst = ? AND src != ?", (dst, sid)).fetchall()
                if 0 < len(cur_sibs) <= 12:
                    for (s2,) in cur_sibs:
                        sc_ticket[s2] += 1.0 / (rank * len(cur_sibs))
        ticket_sids = [s for s, _ in sorted(sc_ticket.items(), key=lambda kv: -kv[1])]

    if not commit_sids and not ticket_sids:
        return [{"session_id": sid} for sid in base_sids[:limit]]

    # Fuse with RRF: strong base weight guarantees no retrieval drop
    fused = rrf([base_sids, commit_sids, ticket_sids], k=60, weights=[1.0, 0.35, 0.2])
    return [{"session_id": sid} for sid in fused[:limit]]


_STRONG_JOINERS = [
    ("that", "also"), ("and", "also"), ("as", "well", "as"),
    ("alongside",), ("along", "with"), ("together", "with"),
    ("plus",), ("which", "also"), ("also",),
]

_POLARITY_MARKERS = frozenset({_stem(w) for w in ("instead", "rather", "over", "not")})

_deep_history_cache: dict[tuple[str, str, tuple[str, ...]], list[tuple]] = {}
_deep_history_lock = threading.Lock()


def _split_clauses(first_clause_tokens: list[str]) -> list[list[str]]:
    """Split a question's first clause into coordinated sub-clauses.

    Strong joiners ('that also', 'as well as', 'plus', ...) always split and the
    joiner tokens are dropped. A bare 'and' splits only when both sides contain
    a verb-ish token, so noun-phrase 'and' ('bot and crawler visits') stays whole.
    """
    clauses: list[list[str]] = [[]]
    i = 0
    n = len(first_clause_tokens)
    while i < n:
        matched = 0
        for seq in _STRONG_JOINERS:
            m = len(seq)
            if tuple(first_clause_tokens[i:i + m]) == seq:
                matched = m
                break
        if matched:
            clauses.append([])
            i += matched
            continue
        tok = first_clause_tokens[i]
        if tok == "and":
            left = clauses[-1]
            right = first_clause_tokens[i + 1:]
            if any(w in VERBISH for w in left) and any(w in VERBISH for w in right):
                clauses.append([])
                i += 1
                continue
        clauses[-1].append(tok)
        i += 1
    return [c for c in clauses if c]


def _corpus_df(conn: sqlite3.Connection, stems: set[str]) -> dict[str, int]:
    """Document frequency per stem across the commits and tickets corpora.

    Stems match fts5vocab terms directly (both FTS tables use porter). On any
    schema problem (older DBs without the vocab tables) returns all zeros so
    callers treat 'no DF info' as 'don't require anything'.
    """
    out = {s: 0 for s in stems}
    if not stems:
        return out
    ph = ",".join("?" * len(stems))
    params = tuple(stems)
    try:
        for term, doc in conn.execute(
            f"SELECT term, doc FROM commits_vocab WHERE term IN ({ph})", params
        ):
            out[term] = out.get(term, 0) + doc
        for term, doc in conn.execute(
            f"SELECT term, doc FROM tickets_vocab WHERE term IN ({ph})", params
        ):
            out[term] = out.get(term, 0) + doc
    except sqlite3.OperationalError:
        return {s: 0 for s in stems}
    return out


def _deep_history(repo_path: str, head: str, terms: tuple[str, ...],
                  branches: list[str], cutoff_ts: float) -> list[tuple]:
    """git-log grep of commits older than the indexed window, for one repo.

    Cached per (repo_path, head, terms) so a repeated question spawns no
    subprocess; a new HEAD invalidates automatically.
    """
    key = (repo_path, head, tuple(terms))
    with _deep_history_lock:
        if key in _deep_history_cache:
            return _deep_history_cache[key]

    args = ["git", "-C", repo_path, "log"] + list(dict.fromkeys(branches))
    args += ["--max-count=200", "-i", "--extended-regexp"]
    args += [f"--grep={t}" for t in terms]
    if cutoff_ts:
        args.append(f"--before=@{int(cutoff_ts)}")
    args.append("--format=\x1e%H\x1f%h\x1f%ct\x1f%s\x1f%b\x1f")

    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=20)
        stdout = res.stdout if res.returncode == 0 else ""
    except Exception:
        stdout = ""

    records = []
    for rec in stdout.split("\x1e")[1:]:
        parts = rec.split("\x1f")
        if len(parts) < 5:
            continue
        h, sh, ct, subj, body = (parts[0].strip(), parts[1].strip(),
                                 parts[2].strip(), parts[3].strip(), parts[4].strip())
        records.append((h, sh, ct, subj, body))

    with _deep_history_lock:
        if len(_deep_history_cache) > 256:
            _deep_history_cache.clear()
        _deep_history_cache[key] = records
    return records


def _ticket_matches_repo(t_ref: str, repo: str) -> bool:
    pfx = t_ref.split("-")[0].upper()
    if pfx == "CCC" and repo != "claude-command-center":
        return False
    if pfx in ("WT", "WATCHTOWER") and repo != "watchtower":
        return False
    if pfx in ("BYM", "BECKY", "BYMOPS") and repo not in ("BYM", "becky-pro", "amirfish1__BYM-Finie"):
        return False
    return True


_staleness_cache: dict[str, tuple[float, int | None]] = {}
_staleness_lock = threading.Lock()
_STALENESS_TTL = 300.0  # 5 min: bounds how often a NOT SHIPPED verdict pays for a fetch


def _clone_behind_count(repo_path: str) -> int | None:
    """How many commits `origin/<default-branch>` has that our local clone
    lacks, or None if this can't be determined. `git fetch -q` first so a
    stale local clone (no push received in a while) doesn't read as
    'not shipped' when it actually shipped upstream. Cached per repo_path
    with a short TTL so a NOT SHIPPED answer doesn't pay for a fetch every
    call."""
    now = time.time()
    with _staleness_lock:
        cached = _staleness_cache.get(repo_path)
        if cached and now - cached[0] < _STALENESS_TTL:
            return cached[1]

    behind: int | None = None
    try:
        subprocess.run(
            ["git", "-C", repo_path, "fetch", "-q", "origin"],
            capture_output=True, timeout=8,
        )
        main_ref = None
        for ref in ("origin/main", "origin/master"):
            vr = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "--verify", "--quiet", ref],
                capture_output=True, text=True, timeout=5,
            )
            if vr.returncode == 0 and vr.stdout.strip():
                main_ref = ref
                break
        if main_ref:
            cr = subprocess.run(
                ["git", "-C", repo_path, "rev-list", "--count", f"HEAD..{main_ref}"],
                capture_output=True, text=True, timeout=5,
            )
            if cr.returncode == 0 and cr.stdout.strip().isdigit():
                behind = int(cr.stdout.strip())
    except Exception:
        behind = None

    with _staleness_lock:
        _staleness_cache[repo_path] = (now, behind)
    return behind


def _origin_freshness_info(repo_path: str) -> dict | None:
    """No-subprocess read of ship_graph.sqlite's cached view of how fresh our
    knowledge of origin/<default> is -- what `ccc shipped` prints. Populated
    by the background loop (_run_origin_freshness_loop), never by this call."""
    conn = _get_connection()
    row = conn.execute(
        "SELECT origin_ref, fetched_at FROM repos WHERE path = ?", (repo_path,)
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    origin_ref, fetched_at = row
    return {"origin_ref": origin_ref, "fetched_at": fetched_at, "age_s": max(0.0, time.time() - fetched_at)}


def _remote_branch_contains(repo_path: str, sha: str) -> bool | None:
    """Whether any remote-tracking branch already known to this clone
    contains `sha` -- a `git branch -r --contains` subprocess, but no
    network I/O (it reads existing remote-tracking refs). Only called from
    `_classify_non_main_commit`, which is itself cached, so this never
    re-runs for a warm (repo_path, sha) pair. Returns None if this can't be
    determined."""
    try:
        r = subprocess.run(
            ["git", "-C", repo_path, "branch", "-r", "--contains", sha],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return any(line.strip() and "->" not in line for line in r.stdout.splitlines())
    except Exception:
        pass
    return None


def _commit_on_main(conn: sqlite3.Connection, repo_name: str, sha: str) -> bool:
    row = conn.execute(
        "SELECT on_main FROM commits WHERE repo = ? AND hash = ?", (repo_name, sha)
    ).fetchone()
    return bool(row and row[0])


_verdict_state_cache: dict[tuple[str, str], tuple[float, str]] = {}
_verdict_state_lock = threading.Lock()
_VERDICT_STATE_TTL = 300.0  # matches _STALENESS_TTL: bounds repeat classification subprocesses


def _classify_non_main_commit(repo_path: str, sha: str, freshness: dict | None) -> str:
    """The non-trivial half of `_commit_verdict_state`: `sha` is not on the
    locally known default branch. Cached per (repo_path, sha) so a warm,
    repeated `ccc shipped` call spawns zero subprocesses."""
    key = (repo_path, sha)
    now = time.time()
    with _verdict_state_lock:
        cached = _verdict_state_cache.get(key)
        if cached and now - cached[0] < _VERDICT_STATE_TTL:
            return cached[1]

    state = "unknown"
    on_remote = _remote_branch_contains(repo_path, sha)
    if on_remote:
        state = "on_remote_branch"
    else:
        stale = (
            freshness is None
            or not isinstance(freshness.get("age_s"), (int, float))
            or freshness["age_s"] > 3600
        )
        if not stale:
            state = "local_only"
        else:
            ident = federation.repo_identity(repo_path)
            if ident and ident.get("kind") == "remote" and ident["identity"].startswith("github.com/"):
                owner_repo = ident["identity"][len("github.com/"):]
                default_branch = _default_branch_name_fast(repo_path)
                gh_state = _github_quota.github_compare_state(owner_repo, default_branch, sha)
                if gh_state == "on_default":
                    state = "on_default"
                elif gh_state == "ahead":
                    state = "local_only"

    with _verdict_state_lock:
        _verdict_state_cache[key] = (now, state)
    return state


def _commit_verdict_state(
    conn: sqlite3.Connection, repo_name: str, repo_path: str, sha: str, freshness: dict | None
) -> str:
    """Classify one commit's relationship to origin/<default> (MEMORY-7,
    multi-machine S2, design spec section 6): 'on_default', 'on_remote_branch',
    'local_only', or 'unknown'. Only reaches out to GitHub (quota-gated,
    cached) when the local fetched view of origin is stale or missing --
    a fresh local view is trusted outright."""
    if _commit_on_main(conn, repo_name, sha):
        return "on_default"
    return _classify_non_main_commit(repo_path, sha, freshness)


def is_shipped(topic: str) -> dict:
    """Determine whether a topic has been shipped. Wraps `_is_shipped_impl`
    to annotate the verdict with clone staleness, the top evidence commit's
    relationship to origin/<default> (on_default / on_remote_branch /
    local_only / unknown, per multi-machine S2), and how fresh our knowledge
    of origin is."""
    result = _is_shipped_impl(topic)
    if not (topic or "").strip():
        return result
    roots = discover_repo_roots()
    detected_repo, _ = detect_named_repo((topic or "").strip(), roots)
    evidence = result.get("evidence") or []
    top_repo_name = evidence[0]["repo"] if evidence else detected_repo
    repo_path = roots.get(top_repo_name) if top_repo_name else None

    freshness = _origin_freshness_info(repo_path) if repo_path else None
    if freshness:
        result["origin_freshness"] = freshness

    node = federation.node_identity().get("display_name") or "this machine"

    if evidence and repo_path:
        conn = _get_connection()
        top_state = _commit_verdict_state(conn, top_repo_name, repo_path, evidence[0]["commit"], freshness)
        evidence[0]["state"] = top_state
        if top_state == "on_remote_branch":
            result["verdict"] = "PUSHED, NOT MERGED"
        elif top_state == "local_only":
            result["verdict"] = f"COMMITTED ON {node}, NOT PUSHED"
        else:
            # "on_default" and "unknown" both keep the plain SHIPPED verdict:
            # a qualifying commit was found, and 'unknown' only means we
            # could not further verify its reachability within budget.
            result["verdict"] = "SHIPPED"
    elif not result.get("shipped"):
        behind = _clone_behind_count(repo_path) if repo_path else None
        if behind:
            result["stale_clone"] = {"repo": detected_repo, "behind": behind}
        age_note = ""
        if freshness and isinstance(freshness.get("age_s"), (int, float)):
            mins = max(0, int(freshness["age_s"] // 60))
            age_note = f"; {freshness.get('origin_ref') or 'origin'} fetched {mins} min ago"
        result["verdict"] = f"NOT FOUND on reachable nodes ({node}{age_note})"
    return result


def _is_shipped_impl(topic: str) -> dict:
    """Determine whether a topic has been shipped.

    Contract:
      {'shipped': bool, 'confidence': float, 'evidence': [{'repo','commit','subject','session_id'}], 'tickets': [...]}
    """
    t = (topic or "").strip()
    if not t:
        return {"shipped": False, "confidence": 0.0, "evidence": [], "tickets": []}

    conn = _get_connection()
    _sync_all(conn, force=False)

    roots = discover_repo_roots()
    detected_repo, repo_words = detect_named_repo(t, roots)

    # Ticket-project identifiers in the question ("PROJ", "PROJ-12") are
    # routing hints, not content words — strip them like repo alias words.
    ticket_tokens = re.findall(r"\b([A-Z][A-Z0-9]{1,11}(?:-[A-Z0-9]{1,10})*)\b", t)
    identifier_words: set[str] = set()
    for tok in ticket_tokens:
        if TICKET_STOP.match(tok):
            continue
        project = re.sub(r"-\d+$", "", tok)
        try:
            row_id = conn.execute(
                "SELECT 1 FROM tickets WHERE project = ? OR ref = ? LIMIT 1",
                (project, tok),
            ).fetchone()
        except sqlite3.OperationalError:
            row_id = None
        if row_id:
            identifier_words.update(re.findall(r"[a-z0-9]+", tok.lower()))
            # "the PROJ queue" refers to the ticket queue itself, not a
            # product surface to match — treat it as part of the identifier
            if re.search(re.escape(tok.lower()) + r"\s+queues?\b", t.lower()):
                identifier_words.update(("queue", "queues"))
    repo_words = repo_words | identifier_words

    all_terms = extract_terms(t)
    if not all_terms:
        return {"shipped": False, "confidence": 0.0, "evidence": [], "tickets": []}

    content_terms = [w for w in all_terms if w not in repo_words]
    if not content_terms:
        content_terms = all_terms

    match_str = fts_query(content_terms)
    if not match_str:
        return {"shipped": False, "confidence": 0.0, "evidence": [], "tickets": []}

    stemmed_content_terms = {_stem(w) for w in content_terms}
    query_asks_docs = any(w in content_terms for w in ("doc", "docs", "document", "documentation", "spec", "specs", "readme", "runbook"))

    common_product_stems = {_stem(w) for w in COMMON_PRODUCT_WORDS}
    generic_verb_stems = {_stem(w) for w in GENERIC_VERBS}
    distinguishing_stems = stemmed_content_terms - common_product_stems - generic_verb_stems
    n_dist = len(distinguishing_stems)
    locative_chunks = _question_structure(t, repo_words)["locative_chunks"]
    locative_stems = set().union(*locative_chunks) if locative_chunks else set()
    non_locative_dist = distinguishing_stems - locative_stems

    # First-clause tokens, truncated at the first clause breaker (same cut as
    # _question_structure). The breaker token itself is kept for polarity.
    q_tokens_full = [tok for tok in re.findall(r"[a-z0-9]+", t.lower())
                     if len(tok) >= 2 and tok not in repo_words]
    break_idx = None
    for i, tok in enumerate(q_tokens_full):
        if tok in CLAUSE_BREAKERS:
            break_idx = i
            break
    first_clause_tokens = q_tokens_full[:break_idx] if break_idx is not None else q_tokens_full
    break_token = q_tokens_full[break_idx] if break_idx is not None else None

    # Class A: distinctive-term coverage. Corpus DF picks the rarest
    # first-clause distinctive stems (plus anything nearly as rare) as required.
    fc_dist = {_stem(w) for w in first_clause_tokens if w not in STOPWORDS} & distinguishing_stems
    df = _corpus_df(conn, fc_dist)
    # df=0 stems can never be covered and cannot discriminate a lookalike
    pos = {s: v for s, v in df.items() if v > 0}
    if not fc_dist or not pos:
        required_stems: set[str] = set()
    else:
        min_df = min(pos.values())
        required_stems = {s for s, v in pos.items() if v <= 2 * min_df + 1}
    # Locative stems are gated by rule (c); Class A must not re-block them
    required_stems -= locative_stems

    # Class E: corpus-absent terms. A first-clause distinctive stem with df=0
    # whose synonyms are also unknown to the corpus is the strongest signal the
    # thing was never built, so it is required like a rare word (the paraphrase
    # hatch may still forgive it, capped below the spawn-warning bar).
    absent_stems: set[str] = set()
    for s, v in df.items():
        if v > 0 or s in locative_stems:
            continue
        syn_stems = {_stem(x) for w in content_terms if _stem(w) == s
                     for x in SYNONYMS.get(w, [])}
        if syn_stems and any(v2 > 0 for v2 in _corpus_df(conn, syn_stems).values()):
            continue
        absent_stems.add(s)
    required_stems |= absent_stems

    # Class B: multi-clause questions — every clause needs coverage.
    clause_reqs: list[set[str]] = []
    clauses = _split_clauses(first_clause_tokens)
    if len(clauses) >= 2:
        for clause in clauses:
            clause_dist = {_stem(w) for w in clause if w not in STOPWORDS} & distinguishing_stems
            if clause_dist:
                clause_reqs.append(clause_dist)

    # Class D: polarity — 'X instead/rather (of|than) Y' records the Y stems so
    # commits that did 'Y instead of X' can be dropped.
    x_stems = fc_dist
    y_stems: set[str] = set()
    if break_token in ("instead", "rather"):
        y_tokens = []
        for tok in q_tokens_full[break_idx + 1:]:
            if tok in CLAUSE_BREAKERS:
                break
            y_tokens.append(tok)
        y_stems = {_stem(w) for w in y_tokens if w not in STOPWORDS} - x_stems

    def _coverage_ok(c: dict) -> bool:
        boosted = c.get("ticket_boost", 0) > 0
        cov = c.get("coverage_stems", c.get("evidence_stems", set()))
        if boosted:
            # The linking ticket may spell a rare word the commit does not
            cov = cov | c.get("ticket_stems", set())
        missing = required_stems - cov
        if missing:
            # Escape hatch: exactly one rare word missing from a commit that
            # already covers 3+ distinctive terms with an adjacent phrase is a
            # paraphrase, not a lookalike.
            n_dist_all = len(c.get("matched_dist_all", set()))
            if (len(missing) == 1
                    and ((n_dist_all >= 3 and c.get("has_phrase_subj"))
                         or n_dist_all >= 4)):
                c["coverage_hatch"] = True
                if missing & absent_stems:
                    c["absent_hatch"] = True
            else:
                return False
        if boosted:
            return True
        for clause_dist in clause_reqs:
            if not (clause_dist & cov):
                return False
        if y_stems:
            toks = c.get("clean_subj_stem_tokens") or []
            k = None
            for i, tk in enumerate(toks):
                if tk in _POLARITY_MARKERS:
                    k = i
                    break
            if k is not None and (y_stems & set(toks[:k])) and (x_stems & set(toks[k + 1:])):
                return False
        return True

    # 1. Search WatchTower tickets
    candidate_tickets: list[dict] = []
    open_tickets: list[dict] = []
    closed_tickets: list[dict] = []
    seen_ticket_refs = set()

    def process_ticket(ref, proj, status, commit_sha, title, text, rank):
        if ref in seen_ticket_refs:
            return
        seen_ticket_refs.add(ref)
        if '/Users/' in (title or '') or '.png' in (title or ''):
            return
        title_tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
        title_stems = {_stem(w) for w in title_tokens if w not in STOPWORDS}
        text_tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        text_stems = {_stem(w) for w in text_tokens if w not in STOPWORDS}

        # Check compound pairs in title and text
        for i in range(len(content_terms) - 1):
            pair = content_terms[i].lower() + content_terms[i+1].lower()
            pair_stem = _stem(pair)
            if pair in title_tokens or pair_stem in title_stems:
                title_stems.add(_stem(content_terms[i]))
                title_stems.add(_stem(content_terms[i+1]))
            if pair in text_tokens or pair_stem in text_stems:
                text_stems.add(_stem(content_terms[i]))
                text_stems.add(_stem(content_terms[i+1]))

        # Check synonyms in title and text
        for orig_term, syn_list in SYNONYMS.items():
            orig_stem = _stem(orig_term)
            if orig_stem in stemmed_content_terms:
                for syn in syn_list:
                    syn_stem = _stem(syn)
                    if syn_stem in title_stems:
                        title_stems.add(orig_stem)
                    if syn_stem in text_stems:
                        text_stems.add(orig_stem)

        m_title = stemmed_content_terms & title_stems
        title_ratio = len(m_title) / len(stemmed_content_terms) if stemmed_content_terms else 0
        m_text = stemmed_content_terms & text_stems
        text_ratio = len(m_text) / len(stemmed_content_terms) if stemmed_content_terms else 0

        title_spaced = " " + " ".join(title_tokens) + " "
        has_phrase_title = False
        if len(content_terms) >= 2:
            for i in range(len(content_terms) - 1):
                t1, t2 = content_terms[i], content_terms[i+1]
                s1_list = [_stem(t1)] + [_stem(s) for s in SYNONYMS.get(t1, [])]
                s2_list = [_stem(t2)] + [_stem(s) for s in SYNONYMS.get(t2, [])]
                for s1 in s1_list:
                    for s2 in s2_list:
                        if f" {s1} {s2} " in title_spaced:
                            has_phrase_title = True
                            break
                    if has_phrase_title:
                        break
                if has_phrase_title:
                    break

        is_relevant = (
            title_ratio >= 0.40
            or (len(m_title) >= 2 and (has_phrase_title or len(stemmed_content_terms) <= 4))
            or (text_ratio >= 0.60 and len(m_title) >= 1)
        )

        if is_relevant:
            t_info = {
                "ref": ref, "status": status, "commit_sha": commit_sha or "",
                "title": title, "title_ratio": title_ratio,
                "m_title": m_title, "has_phrase_title": has_phrase_title,
                "title_stems": title_stems, "text_stems": text_stems,
            }
            candidate_tickets.append(t_info)
            if status in ("open", "in_progress", "blocked", "todo"):
                open_tickets.append(t_info)
            elif status == "closed" and commit_sha:
                closed_tickets.append(t_info)

    try:
        cur_t = conn.execute(
            """SELECT ref, project, status, commit_sha, title, text,
                      bm25(tickets_fts, 0, 0, 0, 0, 6.0, 1.0) as rank
               FROM tickets_fts
               WHERE tickets_fts MATCH ?
               ORDER BY rank LIMIT 150""",
            (match_str,),
        )
        for row in cur_t.fetchall():
            process_ticket(*row)

        for tok in ticket_tokens:
            if TICKET_STOP.match(tok):
                continue
            cur_direct = conn.execute(
                """SELECT ref, project, status, commit_sha, title, text, 0.0 as rank
                   FROM tickets
                   WHERE project = ? OR ref = ? LIMIT 50""",
                (tok, tok),
            )
            for row in cur_direct.fetchall():
                process_ticket(*row)
    except sqlite3.OperationalError:
        pass

    # 2. Search commits via FTS5
    candidate_commits: list[dict] = []
    seen_commits = set()

    def process_commit(cid, repo, h, sh, subj, body, files, rank):
        if h in seen_commits:
            return
        seen_commits.add(h)
        subj_lower = (subj or "").lower()
        body_lower = (body or "").lower()

        if subj_lower.startswith("merge "):
            return

        is_doc_commit = (
            subj_lower.startswith("docs:")
            or subj_lower.startswith("docs(")
            or subj_lower.startswith("doc:")
            or "document " in subj_lower
        )
        if is_doc_commit and not query_asks_docs:
            return

        subj_tokens = re.findall(r"[a-z0-9]+", subj_lower)
        subj_stems = {_stem(w) for w in subj_tokens if w not in STOPWORDS}
        body_tokens = re.findall(r"[a-z0-9]+", body_lower)
        body_stems = {_stem(w) for w in body_tokens if w not in STOPWORDS}

        m_scope = re.match(r"^(?:feat|fix|chore|docs|refactor|test|ci|perf|build)(?:\(([^)]*)\))?:", subj_lower)
        scope_stems = set()
        if m_scope and m_scope.group(1):
            scope_stems = {_stem(w) for w in re.findall(r"[a-z0-9]+", m_scope.group(1)) if w not in STOPWORDS}

        # Check compound pairs
        for i in range(len(content_terms) - 1):
            pair = content_terms[i].lower() + content_terms[i+1].lower()
            pair_stem = _stem(pair)
            if pair in subj_tokens or pair_stem in subj_stems:
                subj_stems.add(_stem(content_terms[i]))
                subj_stems.add(_stem(content_terms[i+1]))
            if pair in body_tokens or pair_stem in body_stems:
                body_stems.add(_stem(content_terms[i]))
                body_stems.add(_stem(content_terms[i+1]))

        # Check synonyms
        for orig_term, syn_list in SYNONYMS.items():
            orig_stem = _stem(orig_term)
            if orig_stem in stemmed_content_terms:
                for syn in syn_list:
                    syn_stem = _stem(syn)
                    if syn_stem in subj_stems:
                        subj_stems.add(orig_stem)
                    if syn_stem in body_stems:
                        body_stems.add(orig_stem)

        matched_subj = stemmed_content_terms & subj_stems
        matched_all_stems = stemmed_content_terms & (subj_stems | body_stems)
        matched_all = matched_all_stems

        subj_ratio = len(matched_subj) / len(stemmed_content_terms) if stemmed_content_terms else 0
        all_ratio = len(matched_all) / len(stemmed_content_terms) if stemmed_content_terms else 0

        clean_subj = re.sub(r"^(?:feat|fix|chore|docs|refactor|test|ci|perf|build)(?:\([^)]*\))?:\s*", "", subj_lower)
        clean_subj_tokens = re.findall(r"[a-z0-9]+", clean_subj)
        clean_subj_stemmed_spaced = " " + " ".join([_stem(w) for w in clean_subj_tokens]) + " "
        body_stemmed_spaced = " " + " ".join([_stem(w) for w in body_tokens]) + " "

        has_phrase_subj = False
        has_phrase_body = False
        if len(content_terms) >= 2:
            for i in range(len(content_terms) - 1):
                t1, t2 = content_terms[i], content_terms[i+1]
                s1_list = [_stem(t1)] + [_stem(s) for s in SYNONYMS.get(t1, [])]
                s2_list = [_stem(t2)] + [_stem(s) for s in SYNONYMS.get(t2, [])]
                for s1 in s1_list:
                    for s2 in s2_list:
                        target = f" {s1} {s2} "
                        if target in clean_subj_stemmed_spaced:
                            has_phrase_subj = True
                        if target in body_stemmed_spaced:
                            has_phrase_body = True

        candidate_commits.append({
            "commit_id": cid, "repo": repo, "hash": h, "short_hash": sh,
            "subject": subj, "subj_ratio": subj_ratio, "all_ratio": all_ratio,
            "matched_subj": matched_subj,
            "matched_all_stems": matched_all_stems,
            "matched_dist_subj": matched_subj & distinguishing_stems,
            "matched_dist_all": matched_all_stems & distinguishing_stems,
            "subj_stems": subj_stems,
            "scope_stems": scope_stems,
            "evidence_stems": subj_stems | scope_stems | body_stems,
            # File-path stems count for coverage gates only — not scoring/rules
            "coverage_stems": subj_stems | scope_stems | body_stems | {
                _stem(w) for w in re.findall(r"[a-z0-9]+", (files or "").lower())
                if w not in STOPWORDS
            },
            "n_matched_subj": len(matched_subj),
            "n_matched_all": len(matched_all),
            "has_phrase": has_phrase_subj or has_phrase_body,
            "has_phrase_subj": has_phrase_subj,
            "has_phrase_body": has_phrase_body,
            "clean_subj_stem_tokens": [_stem(w) for w in clean_subj_tokens],
            "rank": rank,
        })

    try:
        if detected_repo:
            cur_c = conn.execute(
                """SELECT commit_id, repo, hash, short_hash, subject, body, files,
                          bm25(commits_fts, 0, 0, 0, 0, 8.0, 2.0, 0.5) as rank
                   FROM commits_fts
                   WHERE commits_fts MATCH ? AND repo = ?
                   ORDER BY rank LIMIT 80""",
                (match_str, detected_repo),
            )
        else:
            cur_c = conn.execute(
                """SELECT commit_id, repo, hash, short_hash, subject, body, files,
                          bm25(commits_fts, 0, 0, 0, 0, 8.0, 2.0, 0.5) as rank
                   FROM commits_fts
                   WHERE commits_fts MATCH ?
                   ORDER BY rank LIMIT 80""",
                (match_str,),
            )
        for row in cur_c.fetchall():
            process_commit(*row)
    except sqlite3.OperationalError:
        pass

    # Closed tickets linking directly to commits
    for ct in closed_tickets:
        t_sha = ct.get("commit_sha", "")
        t_ref = ct.get("ref", "")
        if not t_sha:
            continue
        found_c = None
        for c in candidate_commits:
            if c["hash"].startswith(t_sha) or t_sha.startswith(c["hash"]):
                found_c = c
                break
        if found_c and not _ticket_matches_repo(t_ref, found_c["repo"]):
            found_c = None
        if not found_c:
            try:
                cur_direct_c = conn.execute(
                    "SELECT commit_id, repo, hash, short_hash, subject, body, files, 0.0 FROM commits WHERE hash LIKE ?",
                    (f"{t_sha}%",),
                )
                row_c = cur_direct_c.fetchone()
                if row_c:
                    if (not detected_repo or row_c[1] == detected_repo) and _ticket_matches_repo(t_ref, row_c[1]):
                        process_commit(*row_c)
                        for c in candidate_commits:
                            if c["hash"].startswith(t_sha) or t_sha.startswith(c["hash"]):
                                found_c = c
                                break
            except Exception:
                pass

        if found_c:
            if (ct.get("has_phrase_title") or found_c.get("has_phrase_subj") or ct["title_ratio"] >= 0.60 or found_c["subj_ratio"] >= 0.50):
                found_c["ticket_boost"] = 15.0
                found_c["ticket_ref"] = t_ref
                found_c["ticket_stems"] = ct["title_stems"] | ct["text_stems"]

    # 3. Evaluate qualifying commits
    n_stems = len(stemmed_content_terms)

    def qualify(cands: list[dict]) -> list[dict]:
        out: list[dict] = []
        for c in cands:
            ticket_boost = c.get("ticket_boost", 0.0)

            matched_dist_subj = c.get("matched_dist_subj", set())
            matched_dist_all = c.get("matched_dist_all", set())
            evidence_stems = c.get("evidence_stems", set())
            c["locative_in_subject"] = (not locative_chunks) or any(
                chunk & (c["subj_stems"] | c["scope_stems"]) for chunk in locative_chunks
            )

            # (a) keyword lookalike: subject shares no distinguishing term
            if n_dist > 0 and len(matched_dist_subj) == 0:
                continue
            if ticket_boost <= 0:
                # (b) short question: every distinguishing term must appear in subject or body
                if 1 <= n_dist <= 2 and not distinguishing_stems <= matched_dist_all:
                    continue
                # (c) question names a place/scope the commit never mentions —
                # unless the subject alone covers 3+ non-locative distinguishing
                # terms (the place may be named by path or alias)
                if locative_chunks and not any(chunk & evidence_stems for chunk in locative_chunks):
                    if not (len(non_locative_dist) >= 3 and non_locative_dist <= matched_dist_subj):
                        continue

            is_strong = False
            n_m_subj = c["n_matched_subj"]
            n_m_all = c["n_matched_all"]
            subj_ratio = c["subj_ratio"]
            has_phrase_subj = c.get("has_phrase_subj", False)

            matched_substantive = {w for w in c.get("matched_subj", set()) if w not in GENERIC_VERBS and _stem(w) not in GENERIC_VERBS}

            if n_stems <= 1 or len(matched_substantive) < 2:
                is_strong = False
            elif n_stems == 2:
                if n_m_subj >= 2:
                    is_strong = True
                elif has_phrase_subj:
                    is_strong = True
                elif ticket_boost > 0:
                    is_strong = True
            elif n_stems == 3:
                if has_phrase_subj and n_m_subj >= 2:
                    is_strong = True
                elif n_m_subj >= 3:
                    is_strong = True
                elif n_m_subj >= 2 and subj_ratio >= 0.65:
                    is_strong = True
                elif ticket_boost > 0 and (n_m_subj >= 2 or has_phrase_subj):
                    is_strong = True
            else:  # n_stems >= 4
                if has_phrase_subj and n_m_subj >= 2:
                    is_strong = True
                elif n_m_subj >= 3 and subj_ratio >= 0.50:
                    is_strong = True
                elif subj_ratio >= 0.65 and n_m_subj >= 3:
                    is_strong = True
                elif ticket_boost > 0 and (n_m_subj >= 2 or has_phrase_subj):
                    is_strong = True

            if is_strong:
                cur_s = conn.execute(
                    "SELECT src FROM edges WHERE dst IN (?, ?) AND kind IN ('made', 'window') LIMIT 1",
                    (f"commit:{c['hash']}", f"commit:{c['short_hash']}"),
                )
                s_row = cur_s.fetchone()
                c["session_id"] = s_row[0] if s_row else ""
                row_tom = conn.execute(
                    "SELECT ts, on_main FROM commits WHERE hash = ?", (c["hash"],)
                ).fetchone()
                if row_tom:
                    c["ts"] = row_tom[0]
                    c["on_main"] = row_tom[1]
                else:
                    # Deep-history commits are not in the commits table
                    c["ts"] = c.get("deep_ts", 0.0)
                    c["on_main"] = 0
                score = (
                    ticket_boost
                    + subj_ratio * 25.0
                    + n_m_subj * 10.0
                    + (12.0 if has_phrase_subj else 0.0)
                    + n_m_all * 3.0
                    + len(matched_dist_subj) * 6.0
                    + (4.0 if c["on_main"] else 0.0)
                    - (c["rank"] * 0.1)
                )
                c["final_score"] = score
                out.append(c)
        return out

    def sort_qualifying(q: list[dict]) -> list[dict]:
        q.sort(key=lambda x: (-x["final_score"], -x.get("on_main", 0), -x.get("ts", 0.0)))
        return q

    qualifying = sort_qualifying(qualify(candidate_commits))

    # Coverage gates (classes A/B/D): a qualifying commit that fails distinctive,
    # per-clause, or polarity coverage is dropped unless a ticket boosts it.
    n_qualifying_pre = len(qualifying)
    qualifying = [c for c in qualifying if _coverage_ok(c)]
    coverage_filtered = n_qualifying_pre > len(qualifying)

    # Class C: named repo but nothing qualified — grep pre-window git history.
    if not qualifying and detected_repo and not open_tickets:
        repo_path = roots.get(detected_repo)
        head = _get_repo_head_fast(repo_path) if repo_path else ""
        terms = sorted(s for s in distinguishing_stems if len(s) >= 3)[:8]
        if repo_path and head and terms:
            branches = ["HEAD"]
            for b in ["next", "main", "master"]:
                p_ref = Path(repo_path) / ".git" / "refs" / "heads" / b
                p_rem = Path(repo_path) / ".git" / "refs" / "remotes" / "origin" / b
                if p_ref.exists() or p_rem.exists():
                    branches.append(b)
            days = _get_days()
            cutoff_ts = (time.time() - days * 86400) if days > 0 else 0.0
            records = _deep_history(repo_path, head, tuple(terms), branches, cutoff_ts)
            deep_cands: list[dict] = []
            for rank_i, (h, sh, ct, subj, body) in enumerate(records):
                before = len(candidate_commits)
                process_commit(f"{detected_repo}:{h}", detected_repo, h, sh, subj, body, "", float(rank_i))
                if len(candidate_commits) > before:
                    c_new = candidate_commits[-1]
                    c_new["deep_history"] = True
                    c_new["deep_ts"] = float(ct) if ct else 0.0
                    deep_cands.append(c_new)
            deep_q = [c for c in qualify(deep_cands) if _coverage_ok(c)]
            if deep_cands and not deep_q:
                coverage_filtered = True
            qualifying = sort_qualifying(deep_q)

    # 4. Decision logic
    all_tickets = list(dict.fromkeys(
        [t["ref"] for t in candidate_tickets] + [c.get("ticket_ref") for c in qualifying if c.get("ticket_ref")]
    ))

    if qualifying:
        top_commit = qualifying[0]
        # Check if an open ticket overrides the commit
        if open_tickets:
            best_ot = max(open_tickets, key=lambda x: x["title_ratio"])
            if (
                best_ot["title_ratio"] > top_commit["subj_ratio"]
                or (best_ot["title_ratio"] >= top_commit["subj_ratio"] and not top_commit.get("has_phrase_subj"))
                or (best_ot["title_ratio"] >= 0.50 and top_commit["subj_ratio"] < 0.60 and not top_commit.get("has_phrase_subj"))
            ):
                return {
                    "shipped": False,
                    "confidence": 0.85,
                    "evidence": [],
                    "tickets": all_tickets,
                }

        evidence = [
            {
                "repo": c["repo"],
                "commit": c["hash"],
                "subject": c["subject"],
                **({"session_id": c["session_id"]} if c.get("session_id") else {}),
            }
            for c in qualifying[:5]
        ]

        top_phrase = top_commit.get("has_phrase_subj", False)
        top_n_subj = top_commit.get("n_matched_subj", 0)
        top_subj_ratio = top_commit.get("subj_ratio", 0.0)
        top_ticket_boost = top_commit.get("ticket_boost", 0.0)
        repo_matched = (detected_repo is not None and top_commit.get("repo") == detected_repo)

        if top_ticket_boost > 0 and top_n_subj >= 2 and (top_phrase or top_subj_ratio >= 0.60):
            conf = 0.95
        elif repo_matched and top_phrase and top_n_subj >= 3 and top_subj_ratio >= 0.70:
            conf = 0.95
        elif repo_matched and n_stems == 2 and top_n_subj == 2 and top_phrase:
            conf = 0.92
        elif repo_matched and top_n_subj >= 4 and top_subj_ratio >= 0.80:
            conf = 0.95
        elif not detected_repo and top_phrase and top_n_subj >= 4 and top_subj_ratio >= 0.80:
            conf = 0.92
        else:
            conf = 0.85

        dist_cov = len(top_commit.get("matched_dist_subj", set())) / n_dist if n_dist else 1.0
        if dist_cov < 0.75:
            conf = min(conf, 0.85)
        if not top_commit.get("locative_in_subject", True):
            conf = min(conf, 0.85)
        if top_commit.get("deep_history") or top_commit.get("coverage_hatch"):
            conf = min(conf, 0.85)
        if top_commit.get("absent_hatch"):
            conf = min(conf, 0.70)

        return {
            "shipped": True,
            "confidence": conf,
            "evidence": evidence,
            "tickets": all_tickets,
        }

    # Not shipped: check if there were planned/open tickets
    if open_tickets:
        return {
            "shipped": False,
            "confidence": 0.85,
            "evidence": [],
            "tickets": all_tickets,
        }

    if coverage_filtered:
        return {
            "shipped": False,
            "confidence": 0.60,
            "evidence": [],
            "tickets": all_tickets,
        }

    has_partial_match = False
    for c in candidate_commits:
        if c.get("n_matched_subj", 0) >= 1 or c.get("n_matched_all", 0) >= 1:
            has_partial_match = True
            break

    if has_partial_match:
        return {
            "shipped": False,
            "confidence": 0.65,
            "evidence": [],
            "tickets": all_tickets,
        }

    return {
        "shipped": False,
        "confidence": 0.50,
        "evidence": [],
        "tickets": all_tickets,
    }
