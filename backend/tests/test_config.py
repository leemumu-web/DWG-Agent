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


class TestCeleryUrls:
    """Celery broker/result backend URLs computed from Redis component fields."""

    def test_no_password_default(self):
        s = Settings()
        assert s.celery_broker_url == "redis://localhost:6379/0"
        assert s.celery_result_backend == "redis://localhost:6379/1"

    def test_with_password(self, monkeypatch):
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        s = Settings()
        assert s.celery_broker_url == "redis://:s3cret@localhost:6379/0"
        assert s.celery_result_backend == "redis://:s3cret@localhost:6379/1"

    def test_password_encoded_for_url_safety(self, monkeypatch):
        monkeypatch.setenv("REDIS_PASSWORD", "p@ss!")
        s = Settings()
        assert "%40" in s.celery_broker_url
        assert "%21" in s.celery_broker_url

    def test_consistent_with_redis_url(self, monkeypatch):
        """Celery URLs share the same password+host+port as redis_url, only DB differs."""
        monkeypatch.setenv("REDIS_PASSWORD", "shared-secret")
        monkeypatch.setenv("REDIS_HOST", "redis.internal")
        monkeypatch.setenv("REDIS_PORT", "6380")
        s = Settings()
        assert "shared-secret" in s.celery_broker_url
        assert "redis.internal:6380" in s.celery_broker_url
        assert s.celery_broker_url.endswith("/0")
        assert s.celery_result_backend.endswith("/1")


# ---------------------------------------------------------------------------
# MySQL configuration (spec §18 component fields)
# ---------------------------------------------------------------------------


class TestMysqlDefaults:
    def test_host_default(self):
        assert Settings().mysql_host == "mysql"

    def test_port_default(self):
        assert Settings().mysql_port == 3306

    def test_database_default(self):
        assert Settings().mysql_database == "dwg_agent"

    def test_user_default(self):
        assert Settings().mysql_user == "dwg_user"

    def test_password_default(self):
        assert Settings().mysql_password == ""


class TestMysqlUrl:
    def test_url_no_password(self):
        s = Settings()
        assert s.mysql_url == "mysql+pymysql://dwg_user@mysql:3306/dwg_agent"

    def test_url_with_password(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "s3cret")
        s = Settings()
        assert s.mysql_url == "mysql+pymysql://dwg_user:s3cret@mysql:3306/dwg_agent"

    def test_url_with_special_chars_in_password(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "p@ss:word!")
        s = Settings()
        assert "%40" in s.mysql_url  # @ encoded
        assert "%3A" in s.mysql_url  # : encoded
        assert "%21" in s.mysql_url  # ! encoded

    def test_url_with_custom_host_and_port(self, monkeypatch):
        monkeypatch.setenv("MYSQL_HOST", "db.internal")
        monkeypatch.setenv("MYSQL_PORT", "3307")
        monkeypatch.setenv("MYSQL_DATABASE", "test_db")
        s = Settings()
        assert "db.internal:3307" in s.mysql_url
        assert s.mysql_url.endswith("/test_db")

    def test_url_with_all_components(self, monkeypatch):
        monkeypatch.setenv("MYSQL_HOST", "10.0.0.50")
        monkeypatch.setenv("MYSQL_PORT", "3308")
        monkeypatch.setenv("MYSQL_DATABASE", "dwg_prod")
        monkeypatch.setenv("MYSQL_USER", "app_user")
        monkeypatch.setenv("MYSQL_PASSWORD", "prod-secret")
        s = Settings()
        assert s.mysql_url == "mysql+pymysql://app_user:prod-secret@10.0.0.50:3308/dwg_prod"


class TestMysqlEnvMapping:
    def test_env_vars_are_picked_up(self, monkeypatch):
        monkeypatch.setenv("MYSQL_HOST", "db-prod")
        monkeypatch.setenv("MYSQL_PORT", "4000")
        monkeypatch.setenv("MYSQL_USER", "prod_user")
        monkeypatch.setenv("MYSQL_PASSWORD", "secure")
        s = Settings()
        assert s.mysql_host == "db-prod"
        assert s.mysql_port == 4000
        assert s.mysql_user == "prod_user"
        assert s.mysql_password == "secure"
        assert "db-prod" in s.mysql_url
        assert "4000" in s.mysql_url
        assert "prod_user" in s.mysql_url

    def test_database_url_still_defaults_to_sqlite(self):
        """MySQL component fields must not affect the existing database_url default."""
        s = Settings()
        assert s.database_url == "sqlite:///./var/app.db"

    def test_mysql_fields_dont_interfere_with_redis(self):
        """Redis URL must be unaffected by MySQL component fields."""
        s = Settings()
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.redis_host == "localhost"

    def test_mysql_url_can_serve_as_database_url(self, monkeypatch):
        """mysql_url is a valid pymysql connection string suitable for DATABASE_URL."""
        monkeypatch.setenv("MYSQL_PASSWORD", "test123")
        s = Settings()
        # mysql_url should be parseable as a valid SQLAlchemy URL
        from sqlalchemy.engine import make_url

        url = make_url(s.mysql_url)
        assert url.drivername == "mysql+pymysql"
        assert url.host == "mysql"
        assert url.port == 3306
        assert url.database == "dwg_agent"
        assert url.username == "dwg_user"
        assert url.password == "test123"


class TestMysqlPortIsInt:
    """MYSQL_PORT from env is a string; pydantic must coerce to int."""

    def test_port_is_int(self):
        assert isinstance(Settings().mysql_port, int)

    def test_env_var_coerced_to_int(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PORT", "13306")
        s = Settings()
        assert s.mysql_port == 13306
        assert isinstance(s.mysql_port, int)
