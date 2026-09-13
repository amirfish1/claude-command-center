"""_codex_cwd_matches_repo runs once per candidate session for every engine
scan (codex, gemini, kimi, cursor, grok, copilot, devin): ~4,000 calls per
sessions-snapshot refresh, each resolving cwd and repo root through realpath.
Resolve each distinct path once."""
from pathlib import Path

from ccc_server import codex


def test_cwd_matcher_resolves_each_distinct_path_once(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    codex._CODEX_PATH_RESOLVE_MEMO.clear()
    resolved = []
    real = codex._codex_resolve_path_uncached

    def counting(raw):
        resolved.append(raw)
        return real(raw)

    monkeypatch.setattr(codex, "_codex_resolve_path_uncached", counting)

    for _ in range(3):
        assert codex._codex_cwd_matches_repo(str(repo / "sub"), repo, {}) is True
        assert codex._codex_cwd_matches_repo(str(tmp_path / "elsewhere"), repo, {}) is False
    assert sorted(set(resolved)) == sorted({str(repo / "sub"), str(repo), str(tmp_path / "elsewhere")})
    assert len(resolved) == 3
    codex._CODEX_PATH_RESOLVE_MEMO.clear()


def test_cwd_matcher_memo_is_bounded(monkeypatch, tmp_path):
    codex._CODEX_PATH_RESOLVE_MEMO.clear()
    monkeypatch.setattr(codex, "_CODEX_PATH_RESOLVE_MEMO_MAX", 4)
    for i in range(10):
        codex._codex_cwd_matches_repo(str(tmp_path / f"cwd{i}"), tmp_path, {})
    assert len(codex._CODEX_PATH_RESOLVE_MEMO) <= 4
    codex._CODEX_PATH_RESOLVE_MEMO.clear()
