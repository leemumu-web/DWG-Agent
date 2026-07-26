"""Prepare the durable Celery SQL transport before workers are admitted."""

from __future__ import annotations

from app.platform.messaging.celery_app import prepare_sql_broker_schema


def main() -> None:
    created_index = prepare_sql_broker_schema()
    state = "created" if created_index else "ready"
    print(f"SQL broker schema: {state}")


if __name__ == "__main__":
    main()
