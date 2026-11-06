"""ByteBudgetCache holds both of its bounds."""
from ccc_server.bounded_cache import ByteBudgetCache


def test_byte_budget_evicts_least_recently_used():
    cache = ByteBudgetCache(max_bytes=100, max_entries=10)
    cache.put("a", 1, 40)
    cache.put("b", 2, 40)
    assert cache.get("a") == 1  # "b" is now the oldest
    cache.put("c", 3, 40)
    assert "b" not in cache and "a" in cache and "c" in cache
    assert cache.bytes == 80


def test_entry_cap_applies_even_when_entries_are_tiny():
    cache = ByteBudgetCache(max_bytes=10_000, max_entries=3)
    for i in range(50):
        cache.put(i, i, 1)
    assert len(cache) == 3 and cache.bytes == 3


def test_value_over_the_whole_budget_is_refused_and_replaces_nothing_stale():
    cache = ByteBudgetCache(max_bytes=100, max_entries=10)
    cache.put("a", "old", 10)
    assert cache.put("a", "new", 101) is False
    assert "a" not in cache and cache.bytes == 0


def test_replacing_a_key_does_not_double_count():
    cache = ByteBudgetCache(max_bytes=100, max_entries=10)
    cache.put("a", 1, 60)
    cache.put("a", 2, 70)
    assert cache.bytes == 70 and cache.get("a") == 2
    assert cache.pop("a") == 2 and cache.bytes == 0
    cache.put("b", 1, 5)
    cache.clear()
    assert len(cache) == 0 and cache.bytes == 0
