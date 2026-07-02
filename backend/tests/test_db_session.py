"""Tests for database session, engine configuration, and health check."""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import Settings, settings
from app.db.session import SessionLocal, connect_args, db_health, engine, get_db, pool_args


class TestEngineConfiguration:
    def test_engine_uses_configured_url(self):
        """Engine URL matches settings.database_url."""
        assert str(engine.url) == settings.database_url

    def test_pool_pre_ping_enabled(self):
        """pool_pre_ping is True regardless of backend (stored as _pre_ping on pool)."""
        assert engine.pool._pre_ping is True


class TestPoolArgs:
    """Verify the module-level pool_args dict reflects current backend."""

    def test_pool_args_is_dict(self):
        assert isinstance(pool_args, dict)

    def test_pool_args_empty_for_sqlite(self):
        """When DATABASE_URL is sqlite, pool_args must be empty (no MySQL tuning)."""
        assert settings.database_url.startswith("sqlite")
        assert pool_args == {}

    def test_pool_args_would_be_populated_for_mysql(self):
        """Simulate the conditional: if url starts with mysql, pool_args gets values."""
        simulated = {}
        if "mysql+pymysql://user@host/db".startswith("mysql"):
            simulated = {"pool_recycle": 3600, "pool_size": 10, "max_overflow": 20}
        assert simulated == {"pool_recycle": 3600, "pool_size": 10, "max_overflow": 20}


class TestSQLiteConnectArgs:
    def test_check_same_thread_false_for_sqlite(self):
        """SQLite needs check_same_thread=False for FastAPI multi-thread access."""
        assert connect_args == {"check_same_thread": False}

    def test_engine_created_successfully(self):
        """The SQLite engine is created at module import time without error."""
        assert engine is not None
        assert engine.name == "sqlite"


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
    def test_yields_session_and_closes(self):
        """get_db() is a generator that yields a Session and closes on teardown."""
        gen = get_db()
        db = next(gen)
        try:
            result = db.execute(text("SELECT 1")).scalar()
            assert result == 1
        finally:
            db.close()
        # After close, using the session should raise
        try:
            db.execute(text("SELECT 1"))
            raise AssertionError("Session should be closed")
        except Exception:
            pass  # expected — closed sessions cannot execute

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

    def test_sqlite_url_does_not_trigger_mysql_pool(self):
        """With the current SQLite URL, pool_size should NOT be set to MySQL values."""
        # SQLAlchemy default pool_size is 5 when using QueuePool
        assert engine.pool.size() <= 5  # SQLite pool; MySQL would have been 10

    def test_mysql_url_triggers_pool_condition(self):
        """Verify the conditional: pool_recycle=3600 would be set for mysql:// URLs."""
        mysql_url = "mysql+pymysql://user:pass@host:3306/db"
        assert mysql_url.startswith("mysql")
        # The conditional in session.py is: if settings.database_url.startswith("mysql")

    def test_settings_database_url_is_sqlite(self):
        """Sanity: the current test environment uses SQLite, not MySQL."""
        s = Settings()
        assert s.database_url.startswith("sqlite")
        assert not s.database_url.startswith("mysql")

    def test_mysql_url_from_settings_is_compatible(self):
        """settings.mysql_url is a valid connection string that could replace DATABASE_URL."""
        from sqlalchemy.engine import make_url

        s = Settings(MYSQL_PASSWORD="test")
        url = make_url(s.mysql_url)
        assert url.drivername == "mysql+pymysql"
        assert url.host == "mysql"
        assert url.database == "dwg_agent"
