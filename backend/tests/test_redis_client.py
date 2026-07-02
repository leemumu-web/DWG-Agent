from __future__ import annotations

import app.core.redis_client as m


class TestLazyInit:
    """Verify the singleton does NOT connect at import time."""

    def test_client_is_none_before_first_call(self, monkeypatch):
        monkeypatch.setattr(m, "_redis_client", None)
        monkeypatch.setattr(m, "_redis_available", None)
        # Simulate fresh import state
        assert m._redis_client is None
        assert m._redis_available is None

    def test_get_redis_initialises_eagerly(self):
        client = m.get_redis()
        assert client is not None
        assert m._redis_available is True


class TestGetRedis:
    def test_returns_client_when_available(self):
        assert m.get_redis() is not None

    def test_second_call_returns_same_instance(self):
        a = m.get_redis()
        b = m.get_redis()
        assert a is b

    def test_returns_none_when_marked_unavailable(self, monkeypatch):
        monkeypatch.setattr(m, "_redis_client", None)
        monkeypatch.setattr(m, "_redis_available", False)
        assert m.get_redis() is None

    def test_connection_failure_marks_unavailable(self, monkeypatch):
        """When redis.Redis.from_url raises, the singleton records unavailable."""
        monkeypatch.setattr(m, "_redis_client", None)
        monkeypatch.setattr(m, "_redis_available", None)

        def _raise(*args, **kwargs):
            raise m.RedisConnectionError("Connection refused")

        monkeypatch.setattr(m.redis.Redis, "from_url", staticmethod(_raise))
        result = m.get_redis()
        assert result is None
        assert m._redis_available is False

    def test_decode_responses_round_trip(self):
        """With decode_responses=True, bytes round-trip as native str."""
        r = m.get_redis()
        assert r is not None
        r.set("__test_str", "hello")
        val = r.get("__test_str")
        assert val == "hello"
        assert isinstance(val, str)


class TestRedisHealth:
    def test_ok_when_connected(self):
        assert m.redis_health()["status"] == "ok"

    def test_unavailable_when_not_connected(self, monkeypatch):
        monkeypatch.setattr(m, "_redis_client", None)
        monkeypatch.setattr(m, "_redis_available", False)
        assert m.redis_health()["status"] == "unavailable"

    def test_error_on_ping_failure(self, monkeypatch):
        """If client exists but ping raises, health reports error."""

        class BrokenRedis:
            def ping(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(m, "_redis_client", BrokenRedis())
        monkeypatch.setattr(m, "_redis_available", True)
        result = m.redis_health()
        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestCloseRedis:
    def test_clears_state(self):
        assert m._redis_client is not None
        m.close_redis()
        assert m._redis_client is None
        assert m._redis_available is None

    def test_idempotent(self):
        m.close_redis()
        m.close_redis()  # second call must not raise

    def test_state_cleared_after_close(self):
        m.close_redis()
        assert m._redis_client is None
        assert m._redis_available is None
