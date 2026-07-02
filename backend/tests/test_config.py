"""Unit tests for pydantic-settings configuration.

Each test creates a fresh ``Settings()`` instance so environment overrides are
isolated. The module-level ``settings`` singleton (cached by ``@lru_cache``) is
**not** used here.
"""

from __future__ import annotations

from app.core.config import Settings


class TestRedisUrl:
    def test_default_url(self):
        s = Settings()
        assert s.redis_url == "redis://localhost:6379/0"

    def test_url_with_password(self, monkeypatch):
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        s = Settings()
        assert s.redis_url == "redis://:s3cret@localhost:6379/0"

    def test_url_with_custom_host(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "redis.internal")
        monkeypatch.setenv("REDIS_PORT", "6380")
        monkeypatch.setenv("REDIS_DB", "2")
        s = Settings()
        assert s.redis_url == "redis://redis.internal:6380/2"

    def test_url_with_all_components(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "10.0.0.1")
        monkeypatch.setenv("REDIS_PORT", "16379")
        monkeypatch.setenv("REDIS_DB", "5")
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        s = Settings()
        assert s.redis_url == "redis://:s3cret@10.0.0.1:16379/5"


class TestRedisDefaults:
    def test_memory_ttl_default(self):
        assert Settings().redis_memory_ttl == 7200

    def test_memory_ttl_override(self, monkeypatch):
        monkeypatch.setenv("REDIS_MEMORY_TTL", "3600")
        assert Settings().redis_memory_ttl == 3600

    def test_max_messages_default(self):
        assert Settings().redis_max_messages == 20

    def test_max_messages_override(self, monkeypatch):
        monkeypatch.setenv("REDIS_MAX_MESSAGES", "50")
        assert Settings().redis_max_messages == 50

    def test_host_default(self):
        assert Settings().redis_host == "localhost"

    def test_host_override(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "cache.example.com")
        assert Settings().redis_host == "cache.example.com"

    def test_port_default(self):
        assert Settings().redis_port == 6379

    def test_db_default(self):
        assert Settings().redis_db == 0


class TestRedisEnvMapping:
    """Verify the env-var → field-name mapping works as expected."""

    def test_env_vars_are_picked_up(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "redis-prod")
        monkeypatch.setenv("REDIS_PORT", "7000")
        monkeypatch.setenv("REDIS_PASSWORD", "prod-pass")
        s = Settings()
        assert s.redis_host == "redis-prod"
        assert s.redis_port == 7000
        assert s.redis_password == "prod-pass"
        assert "redis-prod" in s.redis_url
        assert "7000" in s.redis_url
