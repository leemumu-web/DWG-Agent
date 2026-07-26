from __future__ import annotations

import sys
import time
from collections.abc import Callable

from sqlalchemy import text

from app.platform.database.session import engine

DEFAULT_MAX_ATTEMPTS = 120
DEFAULT_DELAY_SECONDS = 2.0


def wait_for_database(
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for the configured database listener without exposing its DSN."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must not be negative")

    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            error_type = type(exc).__name__
            if attempt == max_attempts:
                print(
                    f"Database connection did not become ready after {max_attempts} attempts. "
                    f"Last error type: {error_type}.",
                    file=sys.stderr,
                    flush=True,
                )
                return False
            print(
                f"Database connection is not ready "
                f"(attempt {attempt}/{max_attempts}, error type: {error_type}); retrying.",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay_seconds)
            continue

        print("Database connection is ready.", flush=True)
        return True

    return False


def main() -> int:
    return 0 if wait_for_database() else 1


if __name__ == "__main__":
    raise SystemExit(main())
