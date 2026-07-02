from __future__ import annotations

import app.core.redis_client as redis_module
from app.services.cache_service import (
    cache_clear_namespace,
    cache_delete,
    cache_get,
    cache_get_or_set,
    cache_set,
)


# ---------------------------------------------------------------------------
# cache_get / cache_set
# ---------------------------------------------------------------------------
class TestCacheGetSet:
    def test_get_returns_none_for_unknown_key(self):
        assert cache_get("ns", "missing") is None

    def test_set_get_round_trip(self):
        cache_set("ns", "k", {"hello": "world"})
        assert cache_get("ns", "k") == {"hello": "world"}

    def test_cjk_round_trip(self):
        cache_set("ns", "zh", ["中文", "テスト"])
        assert cache_get("ns", "zh") == ["中文", "テスト"]

    def test_ttl_is_set(self):
        cache_set("ns", "ttl", 42, ttl=600)
        r = redis_module.get_redis()
        assert r is not None
        remaining = r.ttl("cache:ns:ttl")
        assert 590 <= remaining <= 600

    def test_large_nested_value(self):
        large = {"items": [{"id": i, "name": f"item-{i:04d}"} for i in range(200)]}
        cache_set("ns", "large", large)
        assert cache_get("ns", "large") == large

    def test_scalar_types(self):
        tests = [
            ("int", 42),
            ("float", 3.14),
            ("str", "hello"),
            ("bool", True),
            ("null", None),
            ("list", [1, "a", None]),
        ]
        for label, value in tests:
            cache_set("scalars", label, value)
            assert cache_get("scalars", label) == value, f"failed for {label}"

    def test_overwrite_updates_value_and_ttl(self):
        cache_set("ns", "ow", "v1", ttl=3600)
        cache_set("ns", "ow", "v2", ttl=60)
        assert cache_get("ns", "ow") == "v2"
        r = redis_module.get_redis()
        assert r is not None
        remaining = r.ttl("cache:ns:ow")
        assert remaining <= 60


# ---------------------------------------------------------------------------
# cache_delete
# ---------------------------------------------------------------------------
class TestCacheDelete:
    def test_removes_key(self):
        cache_set("ns", "del-me", 1)
        cache_delete("ns", "del-me")
        assert cache_get("ns", "del-me") is None

    def test_delete_non_existent(self):
        cache_delete("ns", "never-existed")  # must not raise


# ---------------------------------------------------------------------------
# cache_get_or_set
# ---------------------------------------------------------------------------
class TestCacheGetOrSet:
    def test_computes_on_miss(self):
        calls = []

        def factory():
            calls.append(1)
            return "computed"

        val = cache_get_or_set("ns", "compute", factory)
        assert val == "computed"
        assert len(calls) == 1

    def test_returns_cached_on_hit(self):
        cache_set("ns", "hit", "cached")
        calls = []

        def factory():
            calls.append(1)
            return "never"

        val = cache_get_or_set("ns", "hit", factory)
        assert val == "cached"
        assert len(calls) == 0

    def test_factory_exception_propagates(self):
        def factory():
            raise ValueError("factory failed")

        try:
            cache_get_or_set("ns", "err", factory)
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "factory failed" in str(exc)

    def test_factory_returns_none_is_cached(self):
        # json.loads("null") == None, which is indistinguishable from a cache
        # miss.  This is a known limitation — do not store None as a sentinel.
        cache_get_or_set("ns", "none-val", lambda: None)
        factory_called = []

        def factory():
            factory_called.append(1)
            return "fallback"

        val = cache_get_or_set("ns", "none-val", factory)
        assert val == "fallback"
        assert len(factory_called) == 1  # None ≠ cache-hit, factory runs


# ---------------------------------------------------------------------------
# cache_clear_namespace
# ---------------------------------------------------------------------------
class TestCacheClearNamespace:
    def test_deletes_only_own_namespace(self):
        cache_set("a", "k1", 1)
        cache_set("a", "k2", 2)
        cache_set("b", "k3", 3)
        count = cache_clear_namespace("a")
        assert count == 2
        assert cache_get("a", "k1") is None
        assert cache_get("a", "k2") is None
        assert cache_get("b", "k3") == 3

    def test_empty_namespace_returns_zero(self):
        assert cache_clear_namespace("empty-ns") == 0

    def test_many_keys(self):
        for i in range(50):
            cache_set("bulk", str(i), i)
        count = cache_clear_namespace("bulk")
        assert count == 50
        for i in range(50):
            assert cache_get("bulk", str(i)) is None


# ---------------------------------------------------------------------------
# Redis unavailable — all calls safe
# ---------------------------------------------------------------------------
class TestRedisUnavailable:
    def test_all_safe_when_redis_down(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)

        assert cache_get("ns", "k") is None
        cache_set("ns", "k", 1)  # should not raise
        cache_delete("ns", "k")  # should not raise
        assert cache_clear_namespace("ns") == 0
        assert cache_get_or_set("ns", "k", lambda: 42) == 42

    def test_get_or_set_returns_factory_result_when_redis_unavailable(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        val = cache_get_or_set("ns", "fresh", lambda: [1, 2, 3])
        assert val == [1, 2, 3]
