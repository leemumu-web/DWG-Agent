from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import TASK_EXCEL_FINAL
from app.models.job import Job
from app.models.user import User


def _create_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        real_name=username,
        password_hash="test-only",
        password_algo="argon2id",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _job(*, user_id: int, request_key: str | None) -> Job:
    return Job(
        created_by=user_id,
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="queued",
        attempt=1,
        priority=0,
        progress=0,
        params_json={"file_id": 81},
        request_key=request_key,
    )


def test_job_request_key_is_unique_per_actor_and_task(db: Session):
    user = _create_user(db, "idempotency-owner")
    db.add(_job(user_id=user.id, request_key="process:key-1"))
    db.commit()

    db.add(_job(user_id=user.id, request_key="process:key-1"))

    with pytest.raises(IntegrityError):
        db.commit()
