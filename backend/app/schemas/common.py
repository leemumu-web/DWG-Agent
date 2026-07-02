from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    request_id: str
    timestamp: datetime


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int


def meta(request_id: str) -> dict[str, Any]:
    return {"request_id": request_id, "timestamp": datetime.now(UTC).isoformat()}


def ok(data: Any, request_id: str) -> dict[str, Any]:
    return {"data": data, "meta": meta(request_id)}


def page(data: list[Any], page_no: int, page_size: int, total: int, request_id: str) -> dict[str, Any]:
    return {
        "data": data,
        "pagination": {"page": page_no, "page_size": page_size, "total": total},
        "meta": meta(request_id),
    }
