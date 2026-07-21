"""Test-session access shared by HTTP and direct-service integration tests."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

_session_factory: sessionmaker[Session] | None = None


def install_test_session_factory(factory: sessionmaker[Session]) -> None:
    """Install the per-test factory created by the root isolation fixture."""
    global _session_factory
    _session_factory = factory


def get_test_session_factory() -> sessionmaker[Session]:
    """Return the active per-test factory or fail with a useful setup error."""
    assert _session_factory is not None, "the autouse database fixture must run first"
    return _session_factory


@contextmanager
def open_test_session() -> Iterator[Session]:
    """Open a session against the database used by the current test client."""
    with get_test_session_factory()() as session:
        yield session
