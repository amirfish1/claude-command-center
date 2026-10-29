"""Repo auto-guess for new sessions: ccc_server/repo_guess.py."""
import email
import io
import json
import sys
import time
import urllib.error
import urllib.request

import pytest


def _server():
    sys.argv = ["server.py"]
    import server
    return server


@pytest.fixture
def rg(tmp_path, monkeypatch):
    server = _server()
    from ccc_server import repo_guess
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path / "state")
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    repo_guess._RG_DESC_CACHE.clear()
    repo_guess._RG_DESC_LOADED_FROM[0] = None
    monkeypatch.setattr(repo_guess, "_rg_jev_key", lambda: "")
    # tmp_path lives under /private/var on macOS, which the path rule ignores.
    monkeypatch.setattr(repo_guess, "_RG_IGNORED_ROOTS", ())
    monkeypatch.setattr(repo_guess, "_rg_scores", lambda paths: {})
    # A fresh, empty recent-work index: no background rebuild during a test.
    monkeypatch.setattr(repo_guess, "_RG_RECENT", {"at": time.time(), "by_slug": {}, "building": False})
    return repo_guess


def _repos(tmp_path, monkeypatch, *names):
    server = _server()
    paths = []
    for n in names:
        d = tmp_path / "work" / n
        d.mkdir(parents=True, exist_ok=True)
        paths.append(str(d.resolve()))
    monkeypatch.setattr(server, "_load_recent_repos", lambda: list(paths))
    monkeypatch.setattr(server, "_known_repo_paths", lambda: list(paths))
    return paths


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call made")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


# ---- scrubbing -------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "Bearer abcDEF1234567890token",
    "sk-ant-test-XXXXXXXXXXXX",
    "ghp_" + "a" * 36,
    "gho_" + "B" * 30,
    "github_pat_" + "c1" * 15,
    "xoxb-1234567890-abcdefghij",
    "AKIA" + "A1B2C3D4E5F6G7H8",
    "abc_XXXXXXXXXXXXXXXXXXXX",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4",
    "0123456789abcdef0123456789abcdef0123",
    "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==",
])
def test_scrub_redacts_secrets(rg, secret):
    out = rg.repo_guess_scrub(f"please use {secret} for this")
    assert "[REDACTED]" in out
    assert secret not in out
    assert out.startswith("please use") and out.endswith("for this")


@pytest.mark.parametrize("text", ["password=hunter2", "api_key: abc123xyz", "token=\"a b\""])
def test_scrub_redacts_key_value_assignments(rg, text):
    out = rg.repo_guess_scrub(text)
    assert out.endswith("[REDACTED]")
    assert "hunter2" not in out and "abc123xyz" not in out


def test_scrub_leaves_ordinary_text_alone(rg):
    text = "fix the login bug in ~/Apps/shop-api/src/auth/session_handler.py on branch feat/repo-guess-wt-long-name"
    assert rg.repo_guess_scrub(text) == text


# ---- local pass ------------------------------------------------------------

def test_path_match_beats_everything_and_longest_wins(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": f"look at {b}/src/x.py and alpha"})
    assert (out["repo_path"], out["confidence"], out["source"]) == (b, 0.9, "path")
    nested = tmp_path / "work" / "alpha" / "inner"
    nested.mkdir()
    server = _server()
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [a, b, str(nested.resolve())])
    out = rg.repo_guess_request({"prompt": f"edit {nested}/file"})
    assert out["repo_path"] == str(nested.resolve())


def test_tilde_path_match(rg, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    server = _server()
    monkeypatch.setattr(rg.Path, "home", classmethod(lambda cls: home))
    proj = str((home / "proj").resolve())
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [proj])
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [proj])
    out = rg.repo_guess_request({"prompt": "see ~/proj/README.md please"})
    assert out["repo_path"] == proj and out["source"] == "path"


def test_name_match_single(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "shopfront", "billing")
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": "The Billing page crashes on refunds"})
    assert (out["repo_path"], out["confidence"], out["source"]) == (b, 0.9, "name")


def test_name_match_needs_whole_word_and_min_length(rg, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, "billing", "ab")
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": "billings and billing-service and ab"})
    assert out["repo_path"] is None and out["source"] == "none"


def test_name_match_ambiguous_is_not_decided_locally(rg, tmp_path, monkeypatch):
    a, b, _ = _repos(tmp_path, monkeypatch, "shopfront", "billing", "docs")
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": "move the billing code out of shopfront"})
    assert out["repo_path"] is None and out["source"] == "none"
    assert {c["repo_path"] for c in out["candidates"]} == {a, b}


def test_worktree_collapses_into_main_repo(rg, tmp_path, monkeypatch):
    main, = _repos(tmp_path, monkeypatch, "widgets")
    wt = tmp_path / "work" / "widgets-wt-feature"
    wt.mkdir()
    server = _server()
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [str(wt.resolve()), main])
    cands, _ = rg._rg_candidates("")
    assert cands == [main]
    out = rg.repo_guess_request({"prompt": f"fix {wt}/a.py"})
    assert out["repo_path"] == main


def test_candidates_capped_and_current_repo_included(rg, tmp_path, monkeypatch):
    paths = _repos(tmp_path, monkeypatch, *[f"repo{i:02d}" for i in range(30)])
    cands, _ = rg._rg_candidates(paths[29])
    assert len(cands) == 15 and paths[29] in cands


# ---- Jev -------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_no_key_makes_zero_network_calls(rg, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, "alpha", "beta")
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": "something vague about pricing"})
    assert out["source"] == "none" and out["repo_path"] is None


def test_jev_response_mapped_back_to_paths(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    (tmp_path / "work" / "beta" / "README.md").write_text("# Beta\n\nBilling and invoices service.\n")
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    seen = {}

    def fake(req, timeout=None):
        seen["timeout"] = timeout
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode())
        return _Resp({"answers": {"repo": {"type": "choice", "choice": "R2",
                                           "probabilities": {"R1": 0.07, "R2": 0.93},
                                           "confidence": 0.93}}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    secret = "sk-ant-test-XXXXXXXXXXXX"
    out = rg.repo_guess_request({"prompt": f"send the invoices {secret}", "current_repo": a})
    assert (out["repo_path"], out["source"], out["confidence"]) == (b, "jev", 0.93)
    assert out["candidates"][0] == {"repo_path": b, "label": "beta", "probability": 0.93}
    assert seen["timeout"] == 3 and seen["auth"] == "Bearer jev-test-key"
    state = seen["body"]["state"]
    assert secret not in json.dumps(seen["body"]) and "[REDACTED]" in state["first_message"]
    assert state["last_used_workspace"] == "R1"
    crit = seen["body"]["questions"]["repo"]["criteria"]
    assert set(crit) == {"R1", "R2"} and "Billing and invoices" in crit["R2"]
    assert a not in json.dumps(seen["body"]) and b not in json.dumps(seen["body"])


def test_jev_error_falls_back_to_local(rg, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, "alpha", "beta")
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    out = rg.repo_guess_request({"prompt": "something vague about pricing"})
    assert out["ok"] and out["repo_path"] is None and out["source"] == "none"


def test_jev_auth_failure_is_logged_once(rg, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, "alpha", "beta")
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    monkeypatch.setattr(rg, "_RG_JEV_FAIL_LAST", {})
    logged = []
    monkeypatch.setattr(rg._core, "_log_activity", lambda *a: logged.append(a))

    def unauthorized(*a, **k):
        raise urllib.error.HTTPError(rg._RG_JEV_URL, 401, "Unauthorized", None, io.BytesIO(b""))
    monkeypatch.setattr(urllib.request, "urlopen", unauthorized)
    rg.repo_guess_request({"prompt": "something vague about pricing"})
    rg.repo_guess_request({"prompt": "something else vague about pricing"})
    assert logged == [("repoguess", "JEV_FAIL", "http=401 (bad or expired key)")]


def test_jev_key_from_byok_profile(tmp_path, monkeypatch):
    _server()
    from ccc_server import byok, repo_guess, vault
    monkeypatch.setattr(vault, "vault_list_entries", lambda: [])
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setattr(byok, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(byok, "_keychain_available", lambda: False)
    assert repo_guess._rg_jev_key() == ""
    byok.byok_set_key("work", "jev", "jev-test-key")
    assert repo_guess._rg_jev_key() == "jev-test-key"
    monkeypatch.setenv("JEV_API_KEY", "from-env")
    assert repo_guess._rg_jev_key() == "from-env"


@pytest.mark.parametrize("entry", [
    {"name": "jev-api-key.password"},
    {"name": "jev-api-key.token"},
    {"name": "jev-api-key"},
    {"name": "custom-jev", "env_var": "JEV_API_KEY"},
])
def test_jev_key_from_vault_and_status(monkeypatch, entry):
    _server()
    from ccc_server import byok, repo_guess, vault
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setattr(byok, "byok_list_profiles", lambda: [])
    monkeypatch.setattr(vault, "vault_list_entries", lambda: [entry])
    monkeypatch.setattr(vault, "vault_get_value", lambda name: "  jev-test-key  ")
    assert repo_guess._rg_jev_key() == "jev-test-key"
    assert repo_guess.repo_guess_status() == {"ok": True, "jev_configured": True}
    monkeypatch.setattr(vault, "vault_get_value", lambda name: None)
    assert repo_guess.repo_guess_status() == {"ok": True, "jev_configured": False}


def test_jev_ignores_unrelated_vault_entries(monkeypatch):
    _server()
    from ccc_server import byok, repo_guess, vault
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.setattr(byok, "byok_list_profiles", lambda: [])
    monkeypatch.setattr(vault, "vault_list_entries", lambda: [{"name": "another-service"}])
    def unexpected_read(name):
        pytest.fail("Unrelated secret must not be read")
    monkeypatch.setattr(vault, "vault_get_value", unexpected_read)
    assert repo_guess.repo_guess_status() == {"ok": True, "jev_configured": False}


def test_jev_key_not_injected_into_agent_env(tmp_path, monkeypatch):
    _server()
    from ccc_server import byok
    monkeypatch.setattr(byok, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(byok, "_keychain_available", lambda: False)
    byok.byok_set_key("default", "jev", "jev-test-key")
    byok.byok_set_key("default", "openrouter", "sk-or-test-XXXX")
    implicit = byok.byok_spawn_env("opencode", "openrouter/anthropic/claude-sonnet-5", None)
    assert "JEV_API_KEY" not in implicit and implicit["OPENROUTER_API_KEY"] == "sk-or-test-XXXX"
    explicit = byok.byok_spawn_env("opencode", "x", "default")
    assert explicit["JEV_API_KEY"] == "jev-test-key"


# ---- descriptions ----------------------------------------------------------

def test_description_from_readme_and_fallback(rg, tmp_path):
    d = tmp_path / "r1"
    d.mkdir()
    (d / "README.md").write_text(
        "# Shop API\n\n[![ci](x.svg)](y)\n<p align=center>hi</p>\n\n"
        "Backend for   the\nonline shop.\n\nSecond paragraph.\n")
    # A short first paragraph keeps reading into the next one.
    assert rg.repo_guess_describe(str(d)) == "Shop API: Backend for the online shop. Second paragraph."
    e = tmp_path / "empty"
    e.mkdir()
    assert rg.repo_guess_describe(str(e)) == "Folder empty"


def test_description_override_and_length_caps(rg, tmp_path):
    d = tmp_path / "r2"
    (d / ".claude").mkdir(parents=True)
    (d / "README.md").write_text("# Ignored\n\nignored\n")
    (d / ".claude" / "ccc-repo-description.md").write_text("x " * 400)
    assert len(rg.repo_guess_describe(str(d))) <= 500
    d2 = tmp_path / "r3"
    d2.mkdir()
    (d2 / "CLAUDE.md").write_text("# T\n\n" + "word " * 200)
    assert len(rg.repo_guess_describe(str(d2))) <= 300


def test_description_skips_nav_links_and_managed_blocks(rg, tmp_path):
    d = tmp_path / "r5"
    d.mkdir()
    (d / "README.md").write_text(
        "# CCC\n\n**English** · [日本語](README.ja.md) · [简体中文](README.zh-CN.md)\n\n"
        "A dashboard for coding agents.\n")
    assert rg.repo_guess_describe(str(d)) == "CCC: A dashboard for coding agents."
    e = tmp_path / "r6"
    e.mkdir()
    (e / "CLAUDE.md").write_text(
        "<!-- memplug:start -->\n## Project memory\n\nAuto-managed notes.\n<!-- memplug:end -->\n\n"
        "# Studio app\n\nBooking for yoga studios.\n")
    assert rg.repo_guess_describe(str(e)) == "Studio app: Booking for yoga studios."


def test_description_reads_readme_and_claude_md(rg, tmp_path):
    d = tmp_path / "r7"
    d.mkdir()
    (d / "README.md").write_text("# Old Name\n\nA stale summary.\n")
    (d / "CLAUDE.md").write_text("# New Name\n\nWhat the repo is today.\n")
    desc = rg.repo_guess_describe(str(d))
    assert "Old Name: A stale summary." in desc and "New Name: What the repo is today." in desc


def test_recent_session_titles_sent_as_recent_work(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    server = _server()
    slug = server._encode_project_slug(b)
    fake_cache = {
        f"/p/{slug}/1.jsonl": {"mtime": 1, "last_meaningful_ts": 10, "custom_title": "Send owner digest emails"},
        f"/p/{slug}/2.jsonl": {"mtime": 1, "last_meaningful_ts": 20, "custom_title": "fix-booking-page"},
        f"/p/{slug}/3.jsonl": {"mtime": 1, "last_meaningful_ts": 5, "custom_title": "token sk-ant-test-XXXXXXXXXXXX"},
        "/p/other/4.jsonl": {"mtime": 1, "custom_title": "Unrelated"},
    }
    monkeypatch.setattr(server, "_conv_meta_cache", fake_cache)
    rg._rg_build_recent_titles()
    assert rg._rg_recent_titles([b]) == ["fix booking page", "Send owner digest emails",
                                         "token sk-ant-test-XXXXXXXXXXXX"]
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    seen = {}

    def fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _Resp({"answers": {"repo": {"choice": "R2", "confidence": 0.9}}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rg.repo_guess_request({"prompt": "start sending the digest to owners"})
    crit = seen["body"]["questions"]["repo"]["criteria"]
    # A title sharing a word with the prompt ("digest") leads, then the newest.
    assert "Recent work: Send owner digest emails; fix booking page" in crit["R2"]
    assert "Recent work" not in crit["R1"] and "sk-ant-test" not in crit["R2"]


def test_recent_titles_never_scan_on_request_path(rg, monkeypatch):
    started = []

    class _T:
        def __init__(self, **k):
            self.k = k

        def start(self):
            started.append(self.k)
    monkeypatch.setattr(rg.threading, "Thread", _T)
    monkeypatch.setattr(rg, "_RG_RECENT", {"at": 0.0, "by_slug": None, "building": False})
    assert rg._rg_recent_titles(["/x"]) == [] and rg._rg_recent_titles(["/x"]) == []
    assert len(started) == 1 and started[0]["target"] is rg._rg_build_recent_titles


def test_description_cache_does_not_reread_unchanged_file(rg, tmp_path, monkeypatch):
    d = tmp_path / "r4"
    d.mkdir()
    f = d / "README.md"
    f.write_text("# One\n\nfirst\n")
    reads = []
    real = rg._rg_read_head
    monkeypatch.setattr(rg, "_rg_read_head", lambda p: (reads.append(p), real(p))[1])
    assert rg.repo_guess_describe(str(d)) == "One: first"
    assert rg.repo_guess_describe(str(d)) == "One: first"
    assert len(reads) == 1
    # persisted: a fresh process-level cache still does not re-read
    rg._RG_DESC_CACHE.clear()
    rg._RG_DESC_LOADED_FROM[0] = None
    assert rg.repo_guess_describe(str(d)) == "One: first"
    assert len(reads) == 1
    f.write_text("# Two\n\nsecond, longer\n")
    assert rg.repo_guess_describe(str(d)) == "Two: second, longer"
    assert len(reads) == 2


# ---- handler ---------------------------------------------------------------

def _post_json(server, path, payload):
    raw = json.dumps(payload).encode()
    handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
    handler.path = path
    handler.command = "POST"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"POST {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.headers = email.message_from_string(f"Content-Length: {len(raw)}\r\n\r\n")
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.do_POST()
    _, _, body = handler.wfile.getvalue().partition(b"\r\n\r\n")
    return json.loads(body)


def test_handler_returns_documented_shape(rg, tmp_path, monkeypatch):
    server = _server()
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    _no_network(monkeypatch)
    out = _post_json(server, "/api/repo/guess", {"prompt": "work on beta now", "current_repo": a})
    assert set(out) == {"ok", "repo_path", "confidence", "source", "candidates", "latency_ms"}
    assert out["ok"] is True and out["repo_path"] == b and out["source"] == "name"
    assert isinstance(out["latency_ms"], int)
    assert out["candidates"] == [{"repo_path": b, "label": "beta", "probability": 0.9}]


def test_handler_survives_garbage_body(rg, tmp_path, monkeypatch):
    server = _server()
    _repos(tmp_path, monkeypatch, "alpha")
    out = _post_json(server, "/api/repo/guess", {"prompt": 5})
    assert out["ok"] is True and out["repo_path"] is None and out["source"] == "none"


# ---- ranking, containers, path/name rules, hints ---------------------------

def test_higher_usage_score_ranks_first_and_unscored_dropped(rg, tmp_path, monkeypatch):
    names = [f"proj{i}" for i in range(8)]
    paths = _repos(tmp_path, monkeypatch, *names)
    server = _server()
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [paths[7]])  # recent but unscored
    scores = {paths[i]: float(i + 1) for i in range(6)}  # proj5 highest of the six scored
    monkeypatch.setattr(rg, "_rg_scores", lambda ps: {p: scores.get(p, 0.0) for p in ps})
    cands, _, sc = rg._rg_candidates_scored("")
    assert cands[:6] == [paths[i] for i in (5, 4, 3, 2, 1, 0)]
    assert cands[6] == paths[7]          # recents follow the scored ones
    assert paths[6] not in cands         # score-0, non-recent leftovers dropped
    assert sc[paths[5]] == 6.0


def test_cold_signal_cache_falls_back_without_scanning(tmp_path, monkeypatch):
    server = _server()
    from ccc_server import repo_guess
    started = []
    monkeypatch.setitem(server._REPO_SIGNALS_CACHE, "data", None)
    monkeypatch.setattr(repo_guess.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(server, "_compute_repo_usage_signals",
                        lambda ps: (_ for _ in ()).throw(AssertionError("scan on request path")))
    assert repo_guess._rg_scores(["/x"]) == {}
    assert started == [1]


def test_container_dirs_are_excluded_everywhere(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    container = str((tmp_path / "work").resolve())
    server = _server()
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [container, a, b])
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [container, a, b])
    cands, collapse = rg._rg_candidates(container)
    assert container not in cands and container not in collapse
    _no_network(monkeypatch)
    out = rg.repo_guess_request({"prompt": f"check {container}/somefile.txt for me", "current_repo": container})
    assert out["repo_path"] is None and out["source"] == "none"


def test_hidden_and_attachment_paths_are_ignored(rg, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    server = _server()
    monkeypatch.setattr(rg.Path, "home", classmethod(lambda cls: home))
    proj = str((home / "proj").resolve())
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [proj])
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [proj])
    # A scratch root beside (not above) proj: on Linux pytest's tmp_path itself
    # lives under /tmp, so ignoring a literal "/tmp" would swallow proj too.
    scratch = str(tmp_path / "scratch")
    monkeypatch.setattr(rg, "_RG_IGNORED_ROOTS", ("~/Desktop", "~/Downloads", scratch))
    for text in (f"see {proj}/.claude/plans/x.md", "see ~/.claude/plans/x.md", f"see {home}/Desktop/shot.png",
                 "see ~/Downloads/a.png", f"see {scratch}/x.log"):
        assert rg._rg_match_path(text, {proj: proj}) is None, text
    assert rg._rg_match_path(f"see {proj}/src/x.py", {proj: proj}) == proj


def test_name_rule_skips_paths_stoplist_and_filenames(rg, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, "bookyourmat", "test", "apps")
    cands, _ = rg._rg_candidates("")
    for text in ("read apps/bookyourmat/notes", "run the test suite", "go to the apps",
                 "open bookyourmat.py", "see bookyourmat/src"):
        assert rg._rg_match_names(text, cands) == [], text
    assert [p.rsplit("/", 1)[1] for p in rg._rg_match_names("fix bookyourmat checkout", cands)] == ["bookyourmat"]


def test_acronym_alias_only_when_unique(rg, tmp_path, monkeypatch):
    ccc, other = _repos(tmp_path, monkeypatch, "claude-command-center", "billing")
    cands, _ = rg._rg_candidates("")
    assert rg._rg_match_names("fix the CCC sidebar", cands) == [ccc]
    _repos(tmp_path, monkeypatch, "claude-command-center", "ccc")
    cands, _ = rg._rg_candidates("")
    assert [p.rsplit("/", 1)[1] for p in rg._rg_match_names("fix the ccc sidebar", cands)] == ["ccc"]


def test_with_key_path_hit_is_a_hint_and_jev_decides(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    seen = {}

    def fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _Resp({"answers": {"repo": {"choice": "R1", "probabilities": {"R1": 0.9, "R2": 0.1},
                                           "confidence": 0.9}}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = rg.repo_guess_request({"prompt": f"read {b}/runbook.md then fix the alpha parser"})
    assert out["source"] == "jev"
    body = seen["body"]
    labels = {v: k for k, v in zip(("R1", "R2"), rg._rg_candidates("")[0])}
    assert set(body["state"]["workspaces_mentioned"]) == {labels[a], labels[b]}
    assert "workspaces_mentioned" in body["questions"]["repo"]["instructions"]


def test_with_key_jev_failure_falls_back_to_local_decision(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    out = rg.repo_guess_request({"prompt": f"read {b}/runbook.md"})
    assert (out["repo_path"], out["source"], out["confidence"]) == (b, "path", 0.9)


def test_container_current_repo_not_sent_as_hint(rg, tmp_path, monkeypatch):
    a, b = _repos(tmp_path, monkeypatch, "alpha", "beta")
    container = str((tmp_path / "work").resolve())
    monkeypatch.setattr(rg, "_rg_jev_key", lambda: "jev-test-key")
    seen = {}

    def fake(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _Resp({"answers": {"repo": {"choice": "R1", "probabilities": {"R1": 0.9}, "confidence": 0.9}}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rg.repo_guess_request({"prompt": "something vague about pricing", "current_repo": container})
    assert seen["body"]["state"]["last_used_workspace"] is None


def test_leading_segment_alias_only_when_unique(rg, tmp_path, monkeypatch):
    ergo, _ = _repos(tmp_path, monkeypatch, "ERGO-NEXT", "billing")
    cands, _c = rg._rg_candidates("")
    assert rg._rg_match_names("About the latest ERGO meeting", cands) == [ergo]
    assert rg._rg_match_names("ask claude about it", cands) == []
    _repos(tmp_path, monkeypatch, "ERGO-NEXT", "ergo-tools")
    cands, _c = rg._rg_candidates("")
    assert rg._rg_match_names("About the latest ERGO meeting", cands) == []
