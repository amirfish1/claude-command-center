"""Model pricing lookups are memoized (CCC-142).

A cold /api/savings priced ~13k transcript rows and spent most of its time
re-running the canonicalizing regexes for a handful of distinct model ids.
"""
import server  # noqa: F401  (binds the _core proxy)
from ccc_server import morning_launch as ml


def test_memo_matches_uncached_and_returns_copies(monkeypatch):
    monkeypatch.setattr(ml, "_CANONICAL_MODEL_MEMO", {})
    monkeypatch.setattr(ml, "_RATES_KNOWN_MEMO", {})
    for model in ("claude-opus-4-7[1m]", "anthropic/claude-sonnet-4-6", "claude-haiku-3-5-20241022",
                  "gpt-5.6-terra", "unknown-model", "", None):
        first = ml._rates_for_model_known(model)
        assert first == ml._rates_for_canonical_model(ml._canonical_model_name_uncached(model))
        first[0][0] = -1  # a caller mutating its copy must not poison the memo
        assert ml._rates_for_model_known(model)[0][0] != -1


def test_repeat_lookups_skip_the_regexes(monkeypatch):
    monkeypatch.setattr(ml, "_CANONICAL_MODEL_MEMO", {})
    monkeypatch.setattr(ml, "_RATES_KNOWN_MEMO", {})
    ml._rates_for_model_known("claude-opus-4-7")
    calls = []
    real = ml._canonical_model_name_uncached
    monkeypatch.setattr(ml, "_canonical_model_name_uncached", lambda m: calls.append(m) or real(m))
    for _ in range(1000):
        ml._rates_for_model_known("claude-opus-4-7")
    assert calls == []


def test_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(ml, "_CANONICAL_MODEL_MEMO", {})
    monkeypatch.setattr(ml, "_MODEL_MEMO_MAX", 8)
    for i in range(50):
        ml._canonical_model_name(f"model-{i}")
    assert len(ml._CANONICAL_MODEL_MEMO) <= 8
