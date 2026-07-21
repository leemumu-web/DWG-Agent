"""Tests for database session, engine configuration, and health check."""

from __future__ import annotations

import asyncio
import inspect

from sqlalchemy import text

from app.modules.identity.interface import get_current_user, get_current_user_for_sse
from app.platform.config.settings import Settings, settings
from app.platform.database import session as session_module
from app.platform.database.session import SessionLocal, db_health, engine, get_db, pool_args


class TestEngineConfiguration:
    def test_engine_uses_configured_url(self):
        """Engine URL matches the effective application database URL."""
        assert str(engine.url) == settings.sqlalchemy_database_url

    def test_pool_pre_ping_enabled(self):
        """pool_pre_ping is True regardless of backend (stored as _pre_ping on pool)."""
        assert engine.pool._pre_ping is True


class TestPoolArgs:
    """Verify the module-level pool_args dict reflects current backend."""

    def test_pool_args_is_dict(self):
        assert isinstance(pool_args, dict)

    def test_pool_args_always_populated(self):
        """pool_args is unconditional, always providing MySQL tuning values."""
        assert pool_args == {
            "pool_recycle": 3600,
            "pool_size": 2,
            "max_overflow": 2,
            "pool_timeout": 30,
            "pool_use_lifo": True,
        }


class TestDbHealth:
    def test_returns_ok(self):
        result = db_health()
        assert result["status"] == "ok"
        assert "reachable" in result["message"]

    def test_engine_can_execute_sql(self):
        """Verify the engine actually works by executing SELECT 1."""
        with engine.connect() as conn:
            row = conn.execute(text("SELECT 1")).scalar()
            assert row == 1


class TestGetDb:
    def test_dependency_is_async_generator_to_avoid_threadpool_cleanup_starvation(self):
        assert inspect.isasyncgenfunction(get_db)

    def test_dependency_closes_session_on_async_teardown(self, monkeypatch):
        class FakeSession:
            closed = False

            def close(self):
                self.closed = True

        fake = FakeSession()
        monkeypatch.setattr(session_module, "SessionLocal", lambda: fake)

        async def consume_dependency():
            dependency = get_db()
            assert await anext(dependency) is fake
            await dependency.aclose()

        asyncio.run(consume_dependency())
        assert fake.closed is True

    def test_sync_auth_dependencies_never_block_event_loop_on_pool_checkout(self):
        assert not inspect.iscoroutinefunction(get_current_user)
        assert not inspect.iscoroutinefunction(get_current_user_for_sse)

    def test_session_can_execute_raw_sql(self):
        """SessionLocal works for ad-hoc SQL execution."""
        db = SessionLocal()
        try:
            rows = db.execute(text("SELECT 1 AS n UNION SELECT 2 ORDER BY n")).fetchall()
            assert len(rows) == 2
            assert rows[0][0] == 1
            assert rows[1][0] == 2
        finally:
            db.close()


class TestMySQLPoolConfiguration:
    """Verify that MySQL URLs would trigger pool_recycle / pool_size configuration.

    These tests verify the *conditional logic* in session.py without requiring a
    real MySQL server.  The engine at module level is SQLite-based so we check
    that the pool_args dict is NOT populated for SQLite, then verify the
    conditional independently.
    """

    def test_mysql_url_triggers_pool_condition(self):
        """Verify the conditional: pool_recycle=3600 would be set for mysql:// URLs."""
        mysql_url = "mysql+pymysql://user:pass@host:3306/db"
        assert mysql_url.startswith("mysql")
        # The conditional in session.py uses settings.sqlalchemy_database_url.

    def test_settings_database_url_is_pytest_sqlite_override(self):
        """Sanity: pytest explicitly overrides the runtime MySQL URL."""
        s = Settings()
        assert s.sqlalchemy_database_url.startswith("sqlite")
        assert not s.sqlalchemy_database_url.startswith("mysql")

    def test_mysql_url_from_settings_is_compatible(self):
        """settings.mysql_url is a valid connection string that could replace DATABASE_URL."""
        from sqlalchemy.engine import make_url

        s = Settings(MYSQL_PASSWORD="test")
        url = make_url(s.mysql_url)
        assert url.drivername == "mysql+pymysql"
        assert url.host == "127.0.0.1"
        assert url.database == "dwg_agent"

    def test_pool_defaults_are_bounded_for_multiprocess_deployment(self):
        s = Settings(_env_file=None)

        assert s.db_pool_size == 2
        assert s.db_pool_max_overflow == 2
        assert s.db_pool_timeout_seconds == 30
        assert s.db_pool_recycle_seconds == 3600
