"""Authoritative business-wall-clock helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE_NAME = "Asia/Shanghai"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
MYSQL_TIME_ZONE = "+08:00"


def business_now() -> datetime:
    """Return the current business time with an explicit UTC+08:00 offset."""
    return datetime.now(BUSINESS_TIMEZONE)


def as_business_time(value: datetime) -> datetime:
    """Interpret naive persisted values as business wall time and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=BUSINESS_TIMEZONE)
    return value.astimezone(BUSINESS_TIMEZONE)


__all__ = [
    "BUSINESS_TIMEZONE",
    "BUSINESS_TIMEZONE_NAME",
    "MYSQL_TIME_ZONE",
    "as_business_time",
    "business_now",
]
