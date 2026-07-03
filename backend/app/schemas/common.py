from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def meta(request_id: str) -> dict[str, Any]:
    return {"request_id": request_id, "timestamp": datetime.now(UTC).isoformat()}


def ok(data: Any, request_id: str) -> dict[str, Any]:
    return {"data": data, "meta": meta(request_id)}


def page(
    data: list[Any], page_no: int, page_size: int, total: int, request_id: str
) -> dict[str, Any]:
    return {
        "data": data,
        "pagination": {"page": page_no, "page_size": page_size, "total": total},
        "meta": meta(request_id),
    }


def page_from_list(
    data: list[Any], page_no: int, page_size: int, request_id: str
) -> dict[str, Any]:
    total = len(data)
    start = (page_no - 1) * page_size
    end = start + page_size
    return page(data[start:end], page_no, page_size, total, request_id)
