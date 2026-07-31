"""Single-purpose durable Job outbox dispatcher process."""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session, sessionmaker

from app.modules.jobs.outbox import drain_once
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


def run_forever(
    factory: sessionmaker[Session] = SessionLocal,
    *,
    idle_seconds: float = 0.5,
    error_seconds: float = 1.0,
) -> None:
    """Drain continuously; database outages pause the loop without losing rows."""
    if idle_seconds < 0 or error_seconds < 0:
        raise ValueError("dispatcher sleep intervals cannot be negative")
    while True:
        try:
            worked = drain_once(factory)
        except Exception:
            logger.exception("Job dispatcher iteration failed")
            time.sleep(error_seconds)
            continue
        if not worked:
            time.sleep(idle_seconds)


if __name__ == "__main__":
    run_forever()
