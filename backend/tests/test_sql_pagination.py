from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.pagination import paginate_scalars
from app.models.job import Job


def _add_jobs(db: Session, count: int) -> None:
    db.add_all(
        [
            Job(
                task_type="pagination_test",
                precision_level="normal",
                pipeline="stub",
                status="queued",
                progress=0,
            )
            for _ in range(count)
        ]
    )
    db.commit()


def test_paginate_scalars_returns_only_requested_page_and_full_total(db: Session):
    _add_jobs(db, 5)
    stmt = select(Job).where(Job.task_type == "pagination_test").order_by(Job.id)

    jobs, total = paginate_scalars(db, stmt, page_no=2, page_size=2)

    assert total == 5
    assert [job.id for job in jobs] == [3, 4]


def test_paginate_scalars_keeps_total_for_out_of_range_page(db: Session):
    _add_jobs(db, 3)
    stmt = select(Job).where(Job.task_type == "pagination_test").order_by(Job.id)

    jobs, total = paginate_scalars(db, stmt, page_no=99, page_size=20)

    assert jobs == []
    assert total == 3
