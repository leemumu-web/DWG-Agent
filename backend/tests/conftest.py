from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

import pytest
from fakeredis import FakeRedis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.redis_client as redis_module
from app.db.base import Base
from app.db.session import get_db as original_get_db
from app.main import app


@pytest.fixture(autouse=True)
def _isolate_redis_client(monkeypatch):
    """Replace the real Redis module-level singleton with a FakeRedis for every test.

    This ensures tests never touch a real Redis server and provide full isolation
    between test cases (keys are flushed on teardown).
    """
    fake = FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_module, "_redis_client", fake)
    monkeypatch.setattr(redis_module, "_redis_available", True)
    yield
    fake.flushall()
    fake.close()


@pytest.fixture(autouse=True)
def _isolate_test_db(monkeypatch):
    """Use one in-memory SQLite connection per test for isolated unit tests.

    Creates a fresh :memory: engine, builds all tables, then overrides:
    - FastAPI dependency injection (``Depends(get_db)`` in route handlers)
    - ``init_db()`` local references to ``SessionLocal`` + ``engine``
    - ``db_health()`` reference to the module-level engine

    Runtime and deployment use MySQL; this fixture is only a process-local
    test double. StaticPool is required because SQLite in-memory databases are
    scoped to one DB-API connection.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # FastAPI dep override — key is the same function object that Depends(get_db) captured
    app.dependency_overrides[original_get_db] = _override_get_db

    # These modules import SessionLocal / engine at module level — their local
    # names must point to our test objects:
    monkeypatch.setattr("app.db.init_db.engine", engine)
    monkeypatch.setattr("app.db.init_db.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.services.job_service.SessionLocal", TestSessionLocal)

    # db_health() uses the module-level engine directly
    monkeypatch.setattr("app.db.session.engine", engine)

    yield

    app.dependency_overrides.clear()
