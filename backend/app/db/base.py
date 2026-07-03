from __future__ import annotations

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Primary-key type: BIGINT on MySQL, INTEGER on SQLite (which is already 64-bit).
PKType = BigInteger().with_variant(Integer(), "sqlite")
