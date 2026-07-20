from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.platform.database.base import Base
from app.platform.database.session import get_db as original_get_db

# Module-level vars set by _isolate_test_db so the db fixture can use them.
_test_session_factory: sessionmaker | None = None


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
    global _test_session_factory

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        """Enable FK enforcement on SQLite connections (disabled by default).

        We intentionally do NOT set journal_mode=WAL or busy_timeout because
        this is an in-memory database behind StaticPool -- there is only one
        connection, so WAL concurrency and lock-timeout tuning are meaningless.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    _test_session_factory = TestSessionLocal

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # FastAPI dep override — key is the same function object that Depends(get_db) captured
    app.dependency_overrides[original_get_db] = _override_get_db

    # These modules import SessionLocal at module level — their local
    # names must point to our test objects.
    # init_db no longer imports engine (table creation is Alembic-owned);
    # only SessionLocal is monkeypatched for seed-data writes.
    monkeypatch.setattr("app.bootstrap.seed.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.jobs.dispatch.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.jobs.recovery.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.jobs.stub_execution.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.cad_processing.dwg_to_dxf.execution.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.cad_processing.dxf_to_dwg.execution.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.cad_processing.dxf_to_excel.execution.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.modules.dxf_classification.execution.SessionLocal", TestSessionLocal)
    monkeypatch.setattr(
        "app.modules.excel_processing.execution.SessionLocal",
        TestSessionLocal,
    )
    monkeypatch.setattr("app.platform.messaging.celery_app.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.workers.tasks_maintenance.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.workers.tasks_report.SessionLocal", TestSessionLocal)

    # db_health() uses the module-level engine directly
    monkeypatch.setattr("app.platform.database.session.engine", engine)

    yield

    app.dependency_overrides.clear()


@pytest.fixture
def db() -> Session:
    """Provide a fresh SQLAlchemy session for direct service-layer tests."""
    assert _test_session_factory is not None, "_isolate_test_db must run first (autouse)"
    session = _test_session_factory()
    try:
        yield session
    finally:
        session.close()
