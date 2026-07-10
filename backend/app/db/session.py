from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# MySQL connection pool — recycle connections before MySQL wait_timeout (default 28800s)
pool_args = {"pool_recycle": 3600, "pool_size": 10, "max_overflow": 20}

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.sqlalchemy_database_url.startswith("mysql"):
    engine_kwargs.update(pool_args)
engine = create_engine(settings.sqlalchemy_database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_health() -> dict[str, str]:
    """Return database connection health status."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "message": "Database is reachable."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
