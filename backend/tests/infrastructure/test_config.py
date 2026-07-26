"""Unit tests for pydantic-settings configuration.

Each test creates a fresh ``Settings()`` instance so environment overrides are
isolated. The module-level ``settings`` singleton (cached by ``@lru_cache``) is
**not** used here.
"""

from __future__ import annotations

import pytest

from app.platform.config.settings import Settings


# ---------------------------------------------------------------------------
# Agent memory configuration
# ---------------------------------------------------------------------------
class TestAgentMemoryConfig:
    def test_agent_memory_ttl_default(self):
        assert Settings().agent_memory_ttl == 7200

    def test_agent_memory_ttl_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY_TTL", "3600")
        assert Settings().agent_memory_ttl == 3600

    def test_agent_max_messages_default(self):
        assert Settings().agent_max_messages == 20

    def test_agent_max_messages_override(self, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_MESSAGES", "50")
        assert Settings().agent_max_messages == 50


# ---------------------------------------------------------------------------
# Celery broker/result backend URLs (computed from MySQL component fields)
# ---------------------------------------------------------------------------
class TestCeleryUrls:
    """Celery broker/result backend URLs computed from MySQL component fields."""

    def test_no_password_default(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "")
        s = Settings()
        assert s.celery_broker_url == "sqla+mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent"
        assert s.celery_result_backend == "db+mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent"

    def test_with_password(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "s3cret")
        s = Settings()
        assert "sqla+mysql+pymysql://dwg_user:s3cret@127.0.0.1:3306/dwg_agent" == s.celery_broker_url
        assert "db+mysql+pymysql://dwg_user:s3cret@127.0.0.1:3306/dwg_agent" == s.celery_result_backend

    def test_password_encoded_for_url_safety(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "p@ss!")
        s = Settings()
        assert "%40" in s.celery_broker_url  # @ encoded
        assert "%21" in s.celery_broker_url  # ! encoded

    def test_consistent_with_mysql_url(self, monkeypatch):
        """Celery URLs share the same user+password+host+port as mysql_url."""
        monkeypatch.setenv("MYSQL_PASSWORD", "shared-secret")
        monkeypatch.setenv("MYSQL_HOST", "db.internal")
        monkeypatch.setenv("MYSQL_PORT", "6380")
        s = Settings()
        assert "shared-secret" in s.celery_broker_url
        assert "db.internal:6380" in s.celery_broker_url
        assert s.celery_broker_url.startswith("sqla+mysql+pymysql://")
        assert s.celery_result_backend.startswith("db+mysql+pymysql://")

    def test_broker_and_backend_same_host(self, monkeypatch):
        """Both broker and backend point to the same MySQL host."""
        monkeypatch.setenv("MYSQL_HOST", "mysql-prod")
        s = Settings()
        assert "mysql-prod" in s.celery_broker_url
        assert "mysql-prod" in s.celery_result_backend

    def test_database_url_override_is_the_single_runtime_source(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL",
            "mysql+pymysql://override_user:override_pass@db-primary:3307/override_db",
        )
        monkeypatch.setenv("MYSQL_HOST", "must-not-be-used")

        s = Settings()

        assert s.sqlalchemy_database_url == (
            "mysql+pymysql://override_user:override_pass@db-primary:3307/override_db"
        )
        assert s.celery_broker_url == (
            "sqla+mysql+pymysql://override_user:override_pass@db-primary:3307/override_db"
        )
        assert s.celery_result_backend == (
            "db+mysql+pymysql://override_user:override_pass@db-primary:3307/override_db"
        )


# ---------------------------------------------------------------------------
# MySQL configuration (spec §18 component fields)
# ---------------------------------------------------------------------------


class TestMysqlDefaults:
    def test_host_default(self):
        assert Settings().mysql_host == "127.0.0.1"

    def test_port_default(self):
        assert Settings().mysql_port == 3306

    def test_database_default(self):
        assert Settings().mysql_database == "dwg_agent"

    def test_user_default(self):
        assert Settings().mysql_user == "dwg_user"

    def test_password_default(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "")
        assert Settings().mysql_password == ""


class TestMysqlUrl:
    def test_url_no_password(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "")
        s = Settings()
        assert s.mysql_url == "mysql+pymysql://dwg_user@127.0.0.1:3306/dwg_agent"

    def test_url_with_password(self, monkeypatch):
        monkeypatch.setenv("MYSQL_PASSWORD", "s3cret")
        s = Settings()
        assert s.mysql_url == "mysql+pymysql://dwg_user:s3cret@127.0.0.1:3306/dwg_agent"

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

    def test_database_url_uses_pytest_override(self):
        """Pytest explicitly uses an isolated DB URL; runtime env files use MySQL."""
        s = Settings()
        assert s.sqlalchemy_database_url == "sqlite://"

    def test_mysql_components_are_used_when_database_url_is_not_set(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        s = Settings(
            _env_file=None,
            mysql_host="db.internal",
            mysql_port=3308,
            mysql_database="dwg_test",
            mysql_user="dwg_test_user",
            mysql_password="p@ssword",
        )

        assert s.database_url is None
        assert s.sqlalchemy_database_url == s.mysql_url
        assert s.celery_broker_url == f"sqla+{s.mysql_url}"
        assert s.celery_result_backend == f"db+{s.mysql_url}"

    def test_mysql_url_can_serve_as_database_url(self, monkeypatch):
        """mysql_url is a valid pymysql connection string suitable for DATABASE_URL."""
        monkeypatch.setenv("MYSQL_PASSWORD", "test123")
        s = Settings()
        # mysql_url should be parseable as a valid SQLAlchemy URL
        from sqlalchemy.engine import make_url

        url = make_url(s.mysql_url)
        assert url.drivername == "mysql+pymysql"
        assert url.host == "127.0.0.1"
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


def test_excel_final_pipeline_is_disabled_by_default():
    assert Settings(_env_file=None).excel_final_pipeline_enabled is False


def test_dxf_split_pipeline_is_disabled_by_default():
    configured = Settings(_env_file=None)

    assert configured.dxf_split_pipeline_enabled is False
    assert configured.dxf_split_timeout_seconds == 3600


def test_handbook_database_defaults_to_platform_mysql_connection():
    configured = Settings(
        _env_file=None,
        mysql_host="mysql.internal",
        mysql_port=13306,
        mysql_user="dwg_reader",
        mysql_password="secret",
    )

    assert configured.handbook_database_config == {
        "host": "mysql.internal",
        "port": 13306,
        "database": "hardware_handbook",
        "user": "dwg_reader",
        "password": "secret",
        "charset": "utf8mb4",
        "connect_timeout": 5,
    }


def test_handbook_database_supports_independent_read_only_credentials():
    configured = Settings(
        _env_file=None,
        handbook_mysql_host="handbook.internal",
        handbook_mysql_port=3307,
        handbook_mysql_database="steel_reference",
        handbook_mysql_user="readonly",
        handbook_mysql_password="read-secret",
    )

    assert configured.handbook_database_config["host"] == "handbook.internal"
    assert configured.handbook_database_config["port"] == 3307
    assert configured.handbook_database_config["database"] == "steel_reference"
    assert configured.handbook_database_config["user"] == "readonly"
    assert configured.handbook_database_config["password"] == "read-secret"
def test_minio_metrics_url_defaults_to_configured_endpoint():
    configured = Settings(_env_file=None, minio_endpoint="http://objects:9000/")

    assert (
        configured.effective_minio_metrics_url
        == "http://objects:9000/minio/v2/metrics/cluster"
    )


def test_minio_metrics_url_accepts_explicit_proxy_path():
    configured = Settings(
        _env_file=None,
        minio_endpoint="http://objects:9000",
        minio_metrics_url="http://metrics-proxy/minio-capacity",
    )

    assert configured.effective_minio_metrics_url == "http://metrics-proxy/minio-capacity"


def test_storage_capacity_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="WARNING_PERCENT"):
        Settings(
            _env_file=None,
            storage_capacity_warning_percent=90,
            storage_capacity_critical_percent=90,
        )
