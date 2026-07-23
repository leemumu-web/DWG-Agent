from __future__ import annotations

from app.modules.remnant_inventory.execution import run_conversion_batch, run_parse_item
from app.platform.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.tasks_remnant_convert.convert_batch")
def convert_batch_task(batch_id: int, expected_attempts: dict[str, int]) -> None:
    run_conversion_batch(batch_id, expected_attempts)


@celery_app.task(name="app.workers.tasks_remnant_parse.parse_item")
def parse_item_task(item_id: int, expected_attempt: int) -> None:
    run_parse_item(item_id, expected_attempt)
