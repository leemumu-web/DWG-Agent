from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


def paginate_scalars[T](
    db: Session,
    statement: Select[tuple[T]],
    *,
    page_no: int,
    page_size: int,
) -> tuple[list[T], int]:
    """Execute an exact SQL count and load only one page of scalar rows."""
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int(db.scalar(count_statement) or 0)
    offset = (page_no - 1) * page_size
    items = list(db.scalars(statement.offset(offset).limit(page_size)).all())
    return items, total
