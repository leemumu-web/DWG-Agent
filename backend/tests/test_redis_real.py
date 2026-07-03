"""Integration tests against a real Redis (Valkey) server.

All tests are **skipped automatically** when no reachable Redis is found.
They bypass the ``conftest.py`` autouse FakeRedis fixture by using
``redis.Redis.from_url()`` directly, so these tests verify real wire behaviour.

Run::

    cd backend
    uv run pytest tests/test_redis_real.py -v
"""

from __future__ import annotations

import json
import time
from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.redis_client import get_redis

# ---------------------------------------------------------------------------
# Skip-check — runs at *import* time so all tests in this module are gated
# ---------------------------------------------------------------------------


def _real_redis_available() -> bool:
    """Return True if a real Redis server is reachable at the configured URL."""
    try:
        r = Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True
        )
        r.ping()
        r.close()
        return True
    except (RedisConnectionError, OSError):
        return False


_real_redis_skip = not _real_redis_available()
pytestmark = pytest.mark.skipif(
    _real_redis_skip, reason="Real Redis not available — skipping integration tests"
)

# Unique prefix so concurrent test runs across sessions don't collide
PREFIX = f"__test_redis_real_{uuid4().hex[:8]}__"


def _key(name: str) -> str:
    return f"{PREFIX}:{name}"


@pytest.fixture(autouse=True)
def _cleanup():
    """Remove all keys matching our prefix after each test."""
    yield
    try:
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        keys = list(r.scan_iter(match=f"{PREFIX}:*"))
        if keys:
            r.delete(*keys)
        r.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Client-level tests
# ---------------------------------------------------------------------------


class TestRealRedisClient:
    def test_ping(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            assert r.ping() is True
        finally:
            r.close()

    def test_get_set_round_trip(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            r.set(_key("str"), "hello 世界")
            val = r.get(_key("str"))
            assert val == "hello 世界"
            assert isinstance(val, str)
        finally:
            r.close()

    def test_decode_responses_bytes_vs_str(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            r.set(_key("bytes"), b"raw-bytes")
            val = r.get(_key("bytes"))
            # With decode_responses=True, even bytes values come back as str
            assert isinstance(val, str)
        finally:
            r.close()

    def test_health_returns_ok(self):
        """Ping real Redis directly (bypasses module singleton to avoid FakeRedis)."""
        r = Redis.from_url(settings.redis_url, socket_connect_timeout=2, decode_responses=True)
        try:
            assert r.ping() is True
        finally:
            r.close()

    def test_module_singleton_connected(self):
        """get_redis() returns a client (may be real or FakeRedis depending on fixture).

        This is a smoke test — it does NOT guarantee the singleton is real Redis,
        because conftest.py may inject FakeRedis.  A separate "no-fakeredis" test
        directory would be needed for that.
        """
        client = get_redis()
        assert client is not None
        assert client.ping() is True


# ---------------------------------------------------------------------------
# Memory tests (spec §11.5) — full end-to-end against real Redis
# ---------------------------------------------------------------------------


class TestRealRedisMemory:
    def test_full_lifecycle(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        sid = _key("session-1")
        memory_key = f"agent:memory:{sid}"

        try:
            # Write
            msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi!"}]
            r.set(memory_key, json.dumps(msgs, ensure_ascii=False), ex=settings.redis_memory_ttl)

            # Read
            raw = r.get(memory_key)
            loaded = json.loads(raw)
            assert loaded == msgs

            # TTL set
            ttl = r.ttl(memory_key)
            assert ttl >= settings.redis_memory_ttl - 100  # allow small clock skew

            # Delete
            r.delete(memory_key)
            assert r.get(memory_key) is None
        finally:
            r.close()

    def test_truncation_to_max_messages(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        sid = _key("trunc-session")
        memory_key = f"agent:memory:{sid}"

        try:
            limit = settings.redis_max_messages
            msgs = [{"n": i} for i in range(limit + 5)]
            truncated = msgs[-limit:]
            r.set(memory_key, json.dumps(msgs, ensure_ascii=False))
            # Simulate truncation (read → truncate → write back)
            stored = json.loads(r.get(memory_key))
            stored = stored[-limit:]
            r.set(memory_key, json.dumps(stored, ensure_ascii=False), ex=settings.redis_memory_ttl)

            result = json.loads(r.get(memory_key))
            assert len(result) == limit
            assert result == truncated
        finally:
            r.close()

    def test_multiple_sessions_independent(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        a = _key("session-a")
        b = _key("session-b")

        try:
            r.set(f"agent:memory:{a}", json.dumps([{"id": "a"}]), ex=7200)
            r.set(f"agent:memory:{b}", json.dumps([{"id": "b"}]), ex=7200)

            assert json.loads(r.get(f"agent:memory:{a}")) == [{"id": "a"}]
            assert json.loads(r.get(f"agent:memory:{b}")) == [{"id": "b"}]
        finally:
            r.close()

    def test_ttl_refreshed_on_update(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        key = f"agent:memory:{_key('ttl-refresh')}"

        try:
            r.set(key, json.dumps([{"v": 1}]), ex=settings.redis_memory_ttl)
            ttl1 = r.ttl(key)
            time.sleep(0.1)
            r.set(key, json.dumps([{"v": 2}]), ex=settings.redis_memory_ttl)
            ttl2 = r.ttl(key)
            assert ttl1 is not None and ttl2 is not None
            # Second TTL should be close to the original (not reduced by ~0.1 s)
            assert ttl2 >= ttl1 - 2  # allow ±1 s clock skew

        finally:
            r.close()


# ---------------------------------------------------------------------------
# Cache tests — namespace-based key isolation against real Redis
# ---------------------------------------------------------------------------


class TestRealRedisCache:
    def test_set_get_delete(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        key = f"cache:{_key('ns')}:item1"

        try:
            r.set(key, json.dumps({"x": 42}), ex=3600)
            assert json.loads(r.get(key)) == {"x": 42}
            r.delete(key)
            assert r.get(key) is None
        finally:
            r.close()

    def test_namespace_isolation(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        a_key = f"cache:{_key('ns-a')}:item"
        b_key = f"cache:{_key('ns-b')}:item"

        try:
            r.set(a_key, json.dumps("a-data"), ex=3600)
            r.set(b_key, json.dumps("b-data"), ex=3600)
            assert json.loads(r.get(a_key)) == "a-data"
            assert json.loads(r.get(b_key)) == "b-data"

            # Clear namespace A only
            deleted = 0
            for k in r.scan_iter(match=f"cache:{_key('ns-a')}:*"):
                r.delete(k)
                deleted += 1
            assert deleted >= 1
            assert r.get(a_key) is None
            assert r.get(b_key) is not None  # namespace B untouched
        finally:
            r.close()

    def test_ttl_expiry(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        key = f"cache:{_key('ttl-ns')}:expire-me"

        try:
            r.set(key, json.dumps("ephemeral"), ex=1)
            assert r.get(key) is not None
            time.sleep(1.5)
            assert r.get(key) is None  # expired
        finally:
            r.close()

    def test_large_value_round_trip(self):
        r = Redis.from_url(settings.redis_url, decode_responses=True)
        key = f"cache:{_key('big')}:payload"

        try:
            big = {"data": [{"i": i, "text": "hello" * 20} for i in range(200)]}
            r.set(key, json.dumps(big, ensure_ascii=False), ex=3600)
            result = json.loads(r.get(key))
            assert result == big
        finally:
            r.close()
