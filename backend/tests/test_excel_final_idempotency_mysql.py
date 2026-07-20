from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.models.job import Job
from app.modules.identity.interface import User
from app.platform.config.constants import TASK_EXCEL_FINAL
from app.schemas.job_schema import JobCreate
from app.services.job_service import create_or_reuse_job

MYSQL_URL = os.getenv("MYSQL_INTEGRATION_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not MYSQL_URL,
    reason="Set MYSQL_INTEGRATION_DATABASE_URL to run live MySQL idempotency tests.",
)


def test_excel_final_request_key_is_unique_under_mysql() -> None:
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    request_key = f"process:mysql-race-{uuid4().hex}"
    payload = JobCreate(task_type=TASK_EXCEL_FINAL, params={"file_id": 91})
    gate = Barrier(2)

    with factory() as lookup:
        actor_id = lookup.scalar(select(User.id).where(User.username == "admin"))
    assert actor_id is not None

    def submit() -> tuple[int, bool]:
        with factory() as session:
            gate.wait()
            job, reused = create_or_reuse_job(
                session,
                payload,
                created_by=actor_id,
                request_key=request_key,
            )
            session.commit()
            return job.id, reused

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: submit(), range(2)))
        with factory() as verify:
            count = verify.scalar(
                select(func.count()).select_from(Job).where(
                    Job.created_by == actor_id,
                    Job.task_type == TASK_EXCEL_FINAL,
                    Job.request_key == request_key,
                )
            )
        assert len({job_id for job_id, _reused in results}) == 1
        assert sorted(reused for _job_id, reused in results) == [False, True]
        assert count == 1
    finally:
        with engine.begin() as cleanup:
            cleanup.execute(delete(Job).where(Job.request_key == request_key))
        engine.dispose()
