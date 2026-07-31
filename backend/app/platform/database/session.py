from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.config.settings import settings
from app.platform.time import MYSQL_TIME_ZONE


def _configure_mysql_timezone(dbapi_connection, _connection_record) -> None:
    """Force every pooled MySQL session onto the business wall-clock timezone."""
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET time_zone = '{MYSQL_TIME_ZONE}'")

# This budget applies per process. Defaults account for the API and all
# queue-specific Celery parent/child processes in compose.yaml.
pool_args = {
    "pool_recycle": settings.db_pool_recycle_seconds,
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_pool_max_overflow,
    "pool_timeout": settings.db_pool_timeout_seconds,
    "pool_use_lifo": True,
}

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.sqlalchemy_database_url.startswith("mysql"):
    engine_kwargs.update(pool_args)
engine = create_engine(settings.sqlalchemy_database_url, **engine_kwargs)
if settings.sqlalchemy_database_url.startswith("mysql"):
    event.listen(engine, "connect", _configure_mysql_timezone)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


async def get_db() -> AsyncGenerator[Session, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def db_health() -> dict[str, str]:
    """Return database connection health status."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if conn.dialect.name == "mysql":
                timezone = conn.execute(text("SELECT @@session.time_zone")).scalar_one()
                if timezone != MYSQL_TIME_ZONE:
                    raise RuntimeError(
                        f"MySQL session timezone is {timezone!r}; expected {MYSQL_TIME_ZONE!r}."
                    )
        return {"status": "ok", "message": "Database is reachable."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
