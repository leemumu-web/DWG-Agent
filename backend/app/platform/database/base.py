"""SQLAlchemy declarative base and portable primary-key type."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, event, inspect
from sqlalchemy.orm import DeclarativeBase, attributes

from app.platform.time import as_business_time


class Base(DeclarativeBase):
    pass


def _localize_loaded_datetimes(target: object) -> None:
    """Attach the business timezone to MySQL/SQLite DATETIME values on ORM load."""
    mapper = inspect(target).mapper
    for prop in mapper.column_attrs:
        value = getattr(target, prop.key, None)
        if isinstance(value, datetime) and value.tzinfo is None:
            attributes.set_committed_value(target, prop.key, as_business_time(value))


@event.listens_for(Base, "load", propagate=True)
def _on_load(target: object, _context: object) -> None:
    _localize_loaded_datetimes(target)


@event.listens_for(Base, "refresh", propagate=True)
def _on_refresh(target: object, _context: object, _attrs: object) -> None:
    _localize_loaded_datetimes(target)


# Primary-key type: BIGINT on MySQL, INTEGER on SQLite (which is already 64-bit).
PKType = BigInteger().with_variant(Integer(), "sqlite")
